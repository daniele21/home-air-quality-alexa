"""Offline tests for Alexa historical backfill (no Amazon credentials or I/O)."""
import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import sqlite3
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from home_air_quality_alexa.history_fetch import (
    QUERY, RESOLUTIONS, HistoryFetchError, build_request,
    choose_device_sensor, history_period, parse_history, store_history,
    run_history_fetch, safe_identifier, history_identity_matches,
    AQM_RANGE_INSTANCES, RANGE_HISTORY_INTERFACE,
)


def monitor(serial="ABC", endpoint="AAA_ENDPOINT"):
    return SimpleNamespace(serial_number=serial, endpoint_id=endpoint, account_name="Air Monitor")


def sensor():
    return SimpleNamespace(feature_name="range", instance="6", scale="MicroGramsPerCubicMeter")


def utc(day=1):
    return datetime(2026, 11, day, tzinfo=timezone.utc)


def response(rows, *, resolution="ONE_HOUR", instance="6", interface="range"):
    return {"data": {"airQualityHistory": {
        "resolution": resolution,
        "interfaceName": interface,
        "instance": instance,
        "averageValue": 13.5,
        "data": rows,
    }}}


class RequestTests(unittest.TestCase):
    def test_query_contract_and_real_sensor_identity(self):
        self.assertIn("airQualityHistory(airQualityHistoryInput: $input)", QUERY)
        self.assertIn("timestamp", QUERY)
        self.assertEqual(RESOLUTIONS, ("FIVE_MINS", "ONE_HOUR", "ONE_DAY"))
        item = build_request(monitor(), sensor(), utc(), utc(2), "ONE_HOUR")
        q = item["variables"]["input"]
        self.assertEqual(item["operationName"], "AirQualityLocalHistory")
        self.assertEqual(q["devices"], [{"endpointId":"AAA_ENDPOINT", "interfaceName":"range", "instance":"6"}])
        self.assertEqual(q["aggregationResolution"], "ONE_HOUR")
        self.assertTrue(q["startDate"].startswith("2026-11-01"))
        self.assertIn("data {", item["query"])

    def test_rolling_hourly_window_uses_stable_utc_hour_boundary(self):
        now = datetime(2026, 11, 1, 1, 47, 17, 226000, tzinfo=timezone.utc)
        start, end = history_period(7, now=now, resolution="ONE_HOUR")
        self.assertEqual(start.isoformat(), "2026-10-25T01:00:00+00:00")
        self.assertEqual(end.isoformat(), "2026-11-01T01:00:00+00:00")
        start_again, end_again = history_period(
            7, now=now + timedelta(minutes=5), resolution="ONE_HOUR")
        self.assertEqual((start, end), (start_again, end_again))

    def test_resolution_uses_correct_aggregation_boundary(self):
        now = datetime(2026, 11, 1, 11, 47, 17, 226000, tzinfo=timezone.utc)
        five_start, five_end = history_period(
            1, now=now, resolution="FIVE_MINS")
        day_start, day_end = history_period(
            1, now=now, resolution="ONE_DAY")
        self.assertEqual(five_end.isoformat(), "2026-11-01T11:45:00+00:00")
        self.assertEqual(day_end.isoformat(), "2026-11-01T00:00:00+00:00")
        self.assertEqual(five_end - five_start, timedelta(days=1))
        self.assertEqual(day_end - day_start, timedelta(days=1))

    def test_exact_date_ranges_are_utc_inclusive_start_exclusive_end(self):
        start, end = history_period(
            7, start_date="2026-09-01", end_date="2026-10-01")
        self.assertEqual(start.isoformat(), "2026-09-01T00:00:00+00:00")
        self.assertEqual(end.isoformat(), "2026-10-01T00:00:00+00:00")
        self.assertEqual(end-start, timedelta(days=30))
        start2, end2 = history_period(7, start_date="2026-10-31",
                                       end_date="2026-11-01")
        self.assertEqual(end2 - start2, timedelta(days=1))

    def test_invalid_explicit_ranges_fail_before_network(self):
        invalid = [
            ("2026-09-01", None),
            (None, "2026-10-01"),
            ("2026-09-01", "2026-09-01"),
            ("2026-10-02", "2026-10-01"),
            ("2026-09-01", "2026-11-01"),
            ("2026/09/01", "2026-09-02"),
            ("2026-10-30T00:00:00", "2026-11-01"),
            ("2026-02-30", "2026-03-02"),
        ]
        for start, end in invalid:
            with self.subTest(start=start, end=end):
                with self.assertRaises(HistoryFetchError):
                    history_period(7, start_date=start, end_date=end)
        with self.assertRaises(HistoryFetchError):
            history_period(7, resolution="INVALID")

    def test_select_monitor_and_sensor_no_guessing(self):
        devices = {"A":monitor("A","endpoint1"), "B":monitor("B","endpoint2")}
        sensors = {"A":{"PM25":sensor()}, "B":{"PM25":sensor()}}
        with self.assertRaisesRegex(HistoryFetchError, "Multiple"):
            choose_device_sensor(devices, sensors, serial=None, key="PM25")
        device, value = choose_device_sensor(devices, sensors, serial="B", key="PM25")
        self.assertEqual(device.endpoint_id, "endpoint2")
        self.assertEqual(value.instance, "6")
        with self.assertRaisesRegex(HistoryFetchError, "No monitor"):
            choose_device_sensor(devices, sensors, serial=None, key="VOC")
        with self.assertRaisesRegex(HistoryFetchError, "Unsupported"):
            choose_device_sensor(devices, sensors, serial=None, key="CUSTOM")

    def test_interval_bounds(self):
        start, end = history_period(7, now=utc())
        self.assertEqual(end - start, timedelta(days=7))
        with self.assertRaises(HistoryFetchError):
            history_period(32)
        with self.assertRaises(HistoryFetchError):
            history_period(0)
        with self.assertRaises(HistoryFetchError):
            build_request(monitor(), sensor(), utc(1), utc(1), "ONE_HOUR")
        with self.assertRaises(HistoryFetchError):
            build_request(monitor(), sensor(), utc(1), utc(2), "GUESS")


class HistoryTests(unittest.TestCase):
    def test_realistic_history_response_deduplicates_and_excludes_missing(self):
        rows = [
            {"endpointIds":["AAA_ENDPOINT"], "timestamp":"2026-11-01T12:00:00Z", "value":12.4},
            {"endpointIds":["AAA_ENDPOINT"], "timestamp":"2026-11-01T12:00:00Z", "value":12.4},
            {"endpointIds":[], "timestamp":"2026-11-01T13:00:00Z", "value":0},
            {"endpointIds":["other"], "timestamp":"2026-11-01T14:00:00Z", "value":30},
            {"endpointIds":["AAA_ENDPOINT"], "timestamp":"2026-11-01T15:00:00Z", "value":15.1},
            {"endpointIds":["AAA_ENDPOINT"], "timestamp":"2026-11-01T16:00:00", "value":16},
            {"endpointIds":["AAA_ENDPOINT"], "timestamp":"2026-11-01T17:00:00Z", "value":float("nan")},
        ]
        start=utc()
        end=utc(2)
        meta, accepted = parse_history(response(rows), endpoint_id="AAA_ENDPOINT",start=start,end=end,resolution="ONE_HOUR")
        self.assertEqual(len(accepted), 2)
        self.assertEqual(meta["missing_buckets"], 1)
        self.assertEqual(meta["invalid_buckets"], 3)
        self.assertEqual(meta["points_reported"], len(rows))
        self.assertEqual(meta["average_value"], 13.5)

    def test_graphql_errors_fail_closed(self):
        payload = response([])
        payload["errors"] = [{"message":"AUTH COOKIE SECRET"}]
        with self.assertRaisesRegex(HistoryFetchError, "details withheld"):
            parse_history(payload, endpoint_id="AAA_ENDPOINT", start=utc(),end=utc(2),resolution="ONE_HOUR")
        with self.assertRaises(HistoryFetchError):
            parse_history({"data":{"airQualityHistory":None}},endpoint_id="AAA_ENDPOINT",start=utc(),end=utc(2),resolution="ONE_HOUR")
        with self.assertRaises(HistoryFetchError):
            parse_history(response([],resolution="ONE_DAY"),endpoint_id="AAA_ENDPOINT",start=utc(),end=utc(2),resolution="ONE_HOUR")

    def test_idempotent_store_separate_from_live_readings(self):
        with TemporaryDirectory() as td:
            conn=sqlite3.connect(Path(td)/"history.sqlite3")
            conn.execute("CREATE TABLE readings (x INTEGER)")
            points=[("2026-11-01T12:00:00+00:00", 12.4,1)]
            args=dict(serial="ABC",sensor_key="PM25",resolution="ONE_HOUR",
                      points=points,fetched_at="2026-11-01T16:00:00+00:00",unit="µg/m³")
            self.assertEqual(store_history(conn,**args),1)
            self.assertEqual(store_history(conn,**args),0)
            self.assertEqual(conn.execute("SELECT count(*) FROM historical_readings").fetchone()[0],1)
            self.assertEqual(conn.execute("SELECT count(*) FROM readings").fetchone()[0],0)
            self.assertEqual(conn.execute("SELECT numeric_value FROM historical_readings").fetchone()[0],12.4)
            conn.close()




class IdentityValidationTests(unittest.TestCase):
    def test_safe_identifier_avoids_raw_secret_like_values(self):
        self.assertEqual(safe_identifier("range"), "range")
        self.assertEqual(safe_identifier("Alexa.RangeController"), "Alexa.RangeController")
        self.assertEqual(safe_identifier("6"), "6")
        self.assertIsNone(safe_identifier(None))
        self.assertEqual(safe_identifier("Bearer TOKEN=secret"), "<unrecognized>")
        self.assertEqual(safe_identifier("a" * 81), "<unrecognized>")

    def test_mismatch_returns_safe_metadata_without_writing_readings(self):
        api = SimpleNamespace(sensors={"ABC": {"PM25": sensor()}})
        arguments = SimpleNamespace(device=None, sensor="PM25", days=1, resolution="ONE_HOUR")
        date = datetime.now(timezone.utc) - timedelta(hours=1)
        rows = [{
            "endpointIds": ["AAA_ENDPOINT"],
            "timestamp": date.isoformat(),
            "value": 12.4,
        }]
        payload = response(rows, instance=None, interface="Alexa.AirQuality.ParticulateMatter")
        with sqlite3.connect(":memory:") as db:
            with patch("home_air_quality_alexa.history_fetch.fetch_history",
                       new_callable=AsyncMock, return_value=payload):
                result = asyncio.run(run_history_fetch(api, {"ABC": monitor()}, arguments, db))
            self.assertEqual(result["status"], "identity_mismatch")
            self.assertEqual(result["requested"], {"interface": "range", "instance": "6"})
            self.assertEqual(
                result["returned"],
                {"interface": "Alexa.AirQuality.ParticulateMatter", "instance": None},
            )
            self.assertEqual(result["points_reported"], 1)
            self.assertEqual(result["points_for_selected_endpoint"], 1)
            self.assertEqual(result["new_saved"], 0)
            self.assertNotIn("AAA_ENDPOINT", str(result))
            self.assertNotIn("12.4", str(result))
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' "
                "AND name='historical_readings'"
            ).fetchone()[0], 0)

    def test_real_alexa_range_controller_for_pm25_saves_168_historical_points(self):
        # Reproduce the successful Amazon response contract observed on 2026-11-01:
        # requested "range", instance "6"; returned "Alexa.RangeController", "6".
        api = SimpleNamespace(sensors={"ABC": {"PM25": sensor()}})
        arguments = SimpleNamespace(device=None, sensor="PM25", days=7, resolution="ONE_HOUR")
        now = datetime.now(timezone.utc)
        start_bucket = now - timedelta(hours=168)
        rows = [
            {
                "endpointIds": ["AAA_ENDPOINT"],
                "timestamp": (start_bucket + timedelta(hours=i)).isoformat(),
                "value": float(10 + i % 8),
            }
            for i in range(168)
        ]
        rows.append({"endpointIds": [], "timestamp": now.isoformat(), "value": 0.0})
        payload = response(
            rows, interface="Alexa.RangeController", instance="6"
        )
        with sqlite3.connect(":memory:") as db:
            with patch("home_air_quality_alexa.history_fetch.fetch_history",
                       new_callable=AsyncMock, return_value=payload):
                result = asyncio.run(
                    run_history_fetch(api, {"ABC": monitor()}, arguments, db)
                )
                result_again = asyncio.run(
                    run_history_fetch(api, {"ABC": monitor()}, arguments, db)
                )
            self.assertEqual(result["status"], "ok")
            self.assertTrue(result["interface_verified"])
            self.assertEqual(result["points_reported"], 169)
            self.assertEqual(result["valid_points"], 168)
            self.assertEqual(result["missing_buckets"], 1)
            self.assertEqual(result["invalid_buckets"], 0)
            self.assertEqual(result["new_saved"], 168)
            self.assertEqual(result_again["new_saved"], 0)
            self.assertEqual(
                db.execute("SELECT COUNT(*) FROM historical_readings").fetchone()[0],
                168,
            )
            self.assertEqual(
                db.execute("SELECT DISTINCT unit FROM historical_readings").fetchone()[0],
                "µg/m³",
            )

    def test_september_all_missing_is_no_data_not_zero_pm25(self):
        api = SimpleNamespace(sensors={"ABC": {"PM25": sensor()}})
        arguments = SimpleNamespace(
            device=None, sensor="PM25", days=7, resolution="ONE_HOUR",
            start="2026-09-01", end="2026-10-01", probe_only=False,
        )
        rows = [
            {
                "endpointIds": [],
                "timestamp": (datetime(2026, 9, 1, tzinfo=timezone.utc)
                              + timedelta(hours=i)).isoformat(),
                "value": 0.0,
            }
            for i in range(721)
        ]
        payload = response(rows, interface="Alexa.RangeController", instance="6")
        payload["data"]["airQualityHistory"]["averageValue"] = 0.0
        with sqlite3.connect(":memory:") as db:
            with patch("home_air_quality_alexa.history_fetch.fetch_history",
                       new_callable=AsyncMock, return_value=payload):
                result = asyncio.run(
                    run_history_fetch(api, {"ABC": monitor()}, arguments, db)
                )
            self.assertEqual(result["status"], "no_data")
            self.assertEqual(result["points_reported"], 721)
            self.assertEqual(result["missing_buckets"], 721)
            self.assertEqual(result["valid_points"], 0)
            self.assertEqual(result["new_saved"], 0)
            self.assertIsNone(result["average_value"])
            self.assertIsNone(result["oldest_bucket"])
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' "
                "AND name='historical_readings'"
            ).fetchone()[0], 0)

    def test_probe_only_reports_available_without_sqlite(self):
        api = SimpleNamespace(sensors={"ABC": {"PM25": sensor()}})
        arguments = SimpleNamespace(
            device=None, sensor="PM25", days=7, resolution="ONE_HOUR",
            start="2026-10-30", end="2026-10-31", probe_only=True,
        )
        payload = response([
            {"endpointIds": ["AAA_ENDPOINT"], "timestamp": "2026-10-30T08:00:00+00:00", "value": 8.5},
            {"endpointIds": ["AAA_ENDPOINT"], "timestamp": "2026-10-30T09:00:00+00:00", "value": 7.5},
            {"endpointIds": [], "timestamp": "2026-10-30T10:00:00+00:00", "value": 0.0},
        ], instance="6", interface="Alexa.RangeController")
        with patch("home_air_quality_alexa.history_fetch.fetch_history",
                   new_callable=AsyncMock, return_value=payload):
            result = asyncio.run(run_history_fetch(api, {"ABC": monitor()}, arguments, None))
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["probe_only"])
        self.assertEqual(result["valid_points"], 2)
        self.assertEqual(result["would_save"], 2)
        self.assertEqual(result["new_saved"], 0)
        self.assertEqual(result["average_value"], 13.5)

    def test_alias_does_not_allow_mismatched_instance_or_wrong_sensor(self):
        for key, instance in AQM_RANGE_INSTANCES.items():
            current = SimpleNamespace(feature_name="range", instance=instance)
            self.assertTrue(history_identity_matches(
                key, current, RANGE_HISTORY_INTERFACE, instance
            ))
            self.assertTrue(history_identity_matches(key, current, "range", instance))
            self.assertFalse(history_identity_matches(
                key, current, RANGE_HISTORY_INTERFACE, "99"
            ))
            self.assertFalse(history_identity_matches(
                key, current, "Alexa.RangeControllerExtra", instance
            ))
        self.assertFalse(history_identity_matches(
            "PM25", SimpleNamespace(feature_name="range", instance="7"),
            RANGE_HISTORY_INTERFACE, "7",
        ))
        self.assertFalse(history_identity_matches(
            "temperature", SimpleNamespace(feature_name="range", instance="6"),
            RANGE_HISTORY_INTERFACE, "6",
        ))
        self.assertFalse(history_identity_matches(
            "PM25", SimpleNamespace(feature_name="other", instance="6"),
            RANGE_HISTORY_INTERFACE, "6",
        ))

    def test_real_alias_wrong_instance_never_persists(self):
        api = SimpleNamespace(sensors={"ABC": {"PM25": sensor()}})
        arguments = SimpleNamespace(device=None, sensor="PM25", days=1, resolution="ONE_HOUR")
        date = datetime.now(timezone.utc) - timedelta(hours=1)
        payload = response(
            [{"endpointIds": ["AAA_ENDPOINT"], "timestamp": date.isoformat(), "value": 22.0}],
            interface="Alexa.RangeController",
            instance="7",
        )
        with sqlite3.connect(":memory:") as db:
            with patch("home_air_quality_alexa.history_fetch.fetch_history",
                       new_callable=AsyncMock, return_value=payload):
                result = asyncio.run(
                    run_history_fetch(api, {"ABC": monitor()}, arguments, db)
                )
            self.assertEqual(result["status"], "identity_mismatch")
            self.assertEqual(result["new_saved"], 0)
            self.assertEqual(
                db.execute(
                    "SELECT COUNT(*) FROM sqlite_master "
                    "WHERE type='table' AND name='historical_readings'"
                ).fetchone()[0],
                0,
            )

    def test_matching_identity_saves_history(self):
        api = SimpleNamespace(sensors={"ABC": {"PM25": sensor()}})
        arguments = SimpleNamespace(device=None, sensor="PM25", days=1, resolution="ONE_HOUR")
        date = datetime.now(timezone.utc) - timedelta(hours=1)
        payload = response([{
            "endpointIds": ["AAA_ENDPOINT"],
            "timestamp": date.isoformat(),
            "value": 12.4,
        }])
        with sqlite3.connect(":memory:") as db:
            with patch("home_air_quality_alexa.history_fetch.fetch_history",
                       new_callable=AsyncMock, return_value=payload):
                result = asyncio.run(run_history_fetch(api, {"ABC": monitor()}, arguments, db))
            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["new_saved"], 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM historical_readings")
                             .fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
