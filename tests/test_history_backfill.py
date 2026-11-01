"""Offline backfill tests: planning, resume, safety, and canonical hourly view."""

import asyncio
from datetime import date, datetime, timezone
from pathlib import Path
import sqlite3
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from home_air_quality_alexa.history_backfill import (
    HistoryTask, SENSOR_ORDER, available_sensors, completed_windows,
    ensure_backfill_schema, plan_tasks, record_window, resolve_bounds,
    run_backfill, safe_failure_code,
)
from home_air_quality_alexa.history_fetch import HistoryFetchError, store_history


def monitor():
    return SimpleNamespace(
        serial_number="S123", endpoint_id="SENSITIVE_ENDPOINT",
        account_name="First Air Quality Monitor",
    )


def api_and_devices():
    sensor_map = {
        "PM25": SimpleNamespace(feature_name="range", instance="6"),
        "PM10": SimpleNamespace(feature_name="range", instance="7"),
        "VOC": SimpleNamespace(feature_name="range", instance="5"),
        "CO": SimpleNamespace(feature_name="range", instance="8"),
        "Humidity": SimpleNamespace(feature_name="range", instance="4"),
        "Air Quality": SimpleNamespace(feature_name="range", instance="9"),
    }
    return SimpleNamespace(sensors={"S123": sensor_map}), {"S123": monitor()}


def args(**overrides):
    values = {
        "start": "2026-10-02", "end": "2026-10-04", "days": 30,
        "sensor": None, "device": None, "resolution": "ONE_HOUR",
        "chunk_days": 1, "delay": 3.0, "max_requests": 100,
        "apply": False, "force": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def result(status="ok", points=24, saved=24):
    return {
        "status": status,
        "valid_points": points,
        "new_saved": saved,
        "missing_buckets": 0,
        "outside_window_buckets": 1,
        "invalid_buckets": 0,
    }


class BackfillPlanTests(unittest.TestCase):
    def test_default_30_days_six_sensors_180_requests(self):
        start, end = resolve_bounds(
            None, None, days=30,
            now=datetime(2026, 11, 1, 1, 15, tzinfo=timezone.utc),
        )
        self.assertEqual(start, date(2026, 10, 2))
        self.assertEqual(end, date(2026, 11, 1))
        tasks = plan_tasks(start, end, SENSOR_ORDER, chunk_days=1)
        self.assertEqual(len(tasks), 180)
        self.assertEqual(tasks[0], HistoryTask("PM25", "2026-10-02", "2026-10-03"))
        self.assertEqual(tasks[5], HistoryTask("Air Quality", "2026-10-02", "2026-10-03"))
        self.assertEqual(tasks[-1], HistoryTask("Air Quality", "2026-10-31", "2026-11-01"))

    def test_seven_day_chunks_cover_dates_without_overlap(self):
        tasks = plan_tasks(
            date(2026, 10, 2), date(2026, 11, 1),
            ("PM25",), chunk_days=7,
        )
        self.assertEqual(len(tasks), 5)
        self.assertEqual(tasks[0], HistoryTask("PM25", "2026-10-02", "2026-10-09"))
        self.assertEqual(tasks[-1], HistoryTask("PM25", "2026-10-30", "2026-11-01"))
        self.assertEqual(tasks[0].end, tasks[1].start)

    def test_invalid_dates_and_chunk_sizes_fail(self):
        for start, end in [
            ("2026-10-01", None), ("2026-10-01", "2026-10-01"),
            ("2026/10/01", "2026-10-02"), ("2024-01-01", "2026-10-02"),
            ("2026-12-01", "2026-12-02"),
        ]:
            with self.subTest(start=start, end=end):
                with self.assertRaises(HistoryFetchError):
                    resolve_bounds(start, end, days=30)
        for days in (0, 8):
            with self.assertRaises(HistoryFetchError):
                plan_tasks(date(2026, 10, 1), date(2026, 10, 2),
                           ("PM25",), chunk_days=days)
        with self.assertRaises(HistoryFetchError):
            plan_tasks(date(2026, 10, 1), date(2026, 10, 2),
                       ("PM25", "PM25"), chunk_days=1)

    def test_unavailable_sensors_reported(self):
        current, missing = available_sensors({"PM25": object()}, None)
        self.assertEqual(current, ("PM25",))
        self.assertEqual(set(missing), set(SENSOR_ORDER) - {"PM25"})
        with self.assertRaises(HistoryFetchError):
            available_sensors({"PM25": object()}, ["PM25", "PM25"])

    def test_dry_run_never_calls_history_or_touches_database(self):
        api, devices = api_and_devices()
        with patch("home_air_quality_alexa.history_backfill.run_history_fetch",
                   new_callable=AsyncMock) as fetch:
            out = asyncio.run(run_backfill(api, devices, args(), None))
        self.assertEqual(out["status"], "plan")
        self.assertEqual(out["planned_requests"], 12)
        self.assertEqual(out["sensors"], list(SENSOR_ORDER))
        self.assertFalse(out["apply"])
        fetch.assert_not_called()
        self.assertNotIn("SENSITIVE_ENDPOINT", str(out))

    def test_safe_error_codes_do_not_leak_exception_text(self):
        cases = [
            (HistoryFetchError("Alexa GraphQL returned errors (details withheld for privacy)"), "graphql_rejected"),
            (HistoryFetchError("Alexa history request failed (TimeoutError); response details not logged"), "request_failed"),
            (HistoryFetchError("Alexa did not return an airQualityHistory object"), "no_history_object"),
            (HistoryFetchError("Alexa returned a different aggregation resolution"), "resolution_mismatch"),
            (RuntimeError("sensitive custom token"), "unexpected_exception"),
            (HistoryFetchError("untrusted response with SECRET=blah"), "other_history_error"),
        ]
        for exc, expected in cases:
            with self.subTest(expected=expected):
                code = safe_failure_code(exc)
                self.assertEqual(code, expected)
                self.assertNotIn("token", code)
                self.assertNotIn("SECRET", code)

    def test_enforces_rate_limit_before_apply(self):
        api, devices = api_and_devices()
        with self.assertRaises(HistoryFetchError):
            asyncio.run(run_backfill(api, devices, args(delay=0.0), None))
        with self.assertRaises(HistoryFetchError):
            asyncio.run(run_backfill(api, devices, args(max_requests=0), None))


class BackfillExecutionTests(unittest.TestCase):
    def test_successful_batches_are_checkpointed_and_resumable(self):
        api, devices = api_and_devices()
        with sqlite3.connect(":memory:") as conn:
            run = args(apply=True, sensor=["PM25"], max_requests=1)
            with patch("home_air_quality_alexa.history_backfill.run_history_fetch",
                       new_callable=AsyncMock, return_value=result()) as fetch:
                one = asyncio.run(run_backfill(api, devices, run, conn))
                two = asyncio.run(run_backfill(api, devices, run, conn))
                three = asyncio.run(run_backfill(api, devices, run, conn))
            self.assertEqual(one["status"], "partial_limit_reached")
            self.assertEqual(one["requests_sent"], 1)
            self.assertEqual(one["saved_new_rows"], 24)
            self.assertEqual(two["previously_completed"], 1)
            self.assertEqual(two["requests_sent"], 1)
            self.assertEqual(three["status"], "ok")
            self.assertEqual(three["requests_sent"], 0)
            self.assertEqual(fetch.await_count, 2)
            self.assertEqual(
                len(completed_windows(conn, "S123", "ONE_HOUR")), 2
            )
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM historical_backfill_windows").fetchone()[0], 2
            )

    def test_empty_windows_checkpointed_with_zero_saved(self):
        api, devices = api_and_devices()
        run = args(apply=True, sensor=["PM25"], max_requests=2)
        with sqlite3.connect(":memory:") as conn:
            with patch("home_air_quality_alexa.history_backfill.run_history_fetch",
                       new_callable=AsyncMock, return_value=result("no_data",0,0)):
                with patch("home_air_quality_alexa.history_backfill.asyncio.sleep",
                           new_callable=AsyncMock):
                    out=asyncio.run(run_backfill(api, devices, run, conn))
            self.assertEqual(out["status"], "ok")
            self.assertEqual(out["empty_windows"], 2)
            self.assertEqual(out["saved_new_rows"], 0)
            self.assertEqual(len(completed_windows(conn, "S123","ONE_HOUR")), 2)

    def test_mismatch_stops_before_calling_other_sensors(self):
        api, devices = api_and_devices()
        run = args(apply=True, max_requests=12)
        with sqlite3.connect(":memory:") as conn:
            with patch("home_air_quality_alexa.history_backfill.run_history_fetch",
                       new_callable=AsyncMock,
                       return_value={"status":"identity_mismatch","new_saved":0}) as fetch:
                out = asyncio.run(run_backfill(api, devices, run, conn))
            self.assertEqual(out["status"], "stopped_on_identity_mismatch")
            self.assertEqual(out["requests_sent"], 1)
            self.assertEqual(fetch.await_count, 1)
            self.assertEqual(out["failed_window"]["sensor"], "PM25")
            self.assertEqual(len(completed_windows(conn,"S123","ONE_HOUR")),0)

    def test_fail_fast_logs_only_safe_error_class(self):
        api, devices = api_and_devices()
        run = args(apply=True, max_requests=12)
        with sqlite3.connect(":memory:") as conn:
            with patch("home_air_quality_alexa.history_backfill.run_history_fetch",
                       new_callable=AsyncMock,
                       side_effect=RuntimeError("secret account token")) as fetch:
                out = asyncio.run(run_backfill(api, devices, run, conn))
            self.assertEqual(out["status"], "stopped_on_error")
            self.assertEqual(out["error_class"], "RuntimeError")
            self.assertEqual(out["error_code"], "unexpected_exception")
            self.assertNotIn("secret account token", str(out))
            self.assertEqual(fetch.await_count, 1)
            self.assertEqual(
                conn.execute("SELECT status,error_type FROM historical_backfill_windows")
                .fetchone(), ("failed","RuntimeError")
            )

    def test_hourly_utc_view_excludes_legacy_rolling_window_values(self):
        with sqlite3.connect(":memory:") as conn:
            store_history(conn, serial="S123", sensor_key="PM25",
                          resolution="ONE_HOUR", fetched_at="2026-11-01T01:00:00+00:00",
                          points=[
                              ("2026-10-30T12:47:17.226000+00:00", 12.0, 1),
                              ("2026-10-30T12:00:00.000000+00:00", 15.0, 1),
                              ("2026-10-30T13:00:00.000000+00:00", 18.0, 1),
                          ], unit="µg/m³")
            ensure_backfill_schema(conn)
            rows = conn.execute(
                "SELECT bucket_timestamp_utc, numeric_value FROM historical_hourly_utc"
                " WHERE sensor_key = 'PM25' ORDER BY bucket_timestamp_utc"
            ).fetchall()
            self.assertEqual(len(rows),2)
            self.assertEqual([r[1] for r in rows],[15.0,18.0])
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM historical_readings").fetchone()[0], 3
            )


if __name__ == "__main__":
    unittest.main()
