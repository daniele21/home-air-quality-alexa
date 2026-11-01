"""Offline tests for Alexa historical backfill (no Amazon credentials or I/O)."""
from datetime import datetime, timedelta, timezone
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


if __name__ == "__main__":
    unittest.main()
