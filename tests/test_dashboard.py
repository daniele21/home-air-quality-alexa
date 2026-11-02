"""Offline tests for the local Alexa dashboard (no Amazon calls)."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import math
import sqlite3
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from home_air_quality_alexa.dashboard import (
    ALLOWED_DAYS, CHANNELS, DashboardError, _as_utc_hour,
    build_dashboard, _make_handler, STATIC_FILE,
)
from home_air_quality_alexa.history_backfill import ensure_backfill_schema
from home_air_quality_alexa.history_fetch import store_history


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.db = Path(self.tmp.name) / "aqm.sqlite3"
        with sqlite3.connect(self.db) as conn:
            store_history(
                conn, serial="PRIVATE_SERIAL", sensor_key="PM25",
                resolution="ONE_HOUR", unit="µg/m³",
                fetched_at="2026-11-01T06:00:00+00:00",
                points=[
                    ("2026-10-31T00:00:00.000000+00:00", 12.0, 1),
                    ("2026-10-31T01:00:00.000000+00:00", 18.0, 1),
                    ("2026-10-24T00:00:00.000000+00:00", 10.0, 1),
                    # Legacy history stays stored but is never displayed.
                    ("2026-10-31T00:47:17.226000+00:00", 12000.0, 1),
                ],
            )
            store_history(
                conn, serial="PRIVATE_SERIAL", sensor_key="Humidity",
                resolution="ONE_HOUR", unit="%",
                fetched_at="2026-11-01T06:00:00+00:00",
                points=[("2026-10-31T12:00:00.000000+00:00", 55.0, 1)],
            )
            ensure_backfill_schema(conn)

    def tearDown(self):
        self.tmp.cleanup()

    def test_report_is_aligned_readonly_and_privacy_safe(self):
        before = self.db.read_bytes()
        report = build_dashboard(self.db, days=7)
        self.assertEqual(report["schema_version"], "1")
        self.assertEqual(report["timezone"], "UTC")
        self.assertEqual(report["start_date_utc"], "2026-10-25")
        self.assertEqual(report["end_date_utc_exclusive"], "2026-11-01")
        self.assertEqual(report["latest_available_hour_utc"], "2026-10-31T12:00:00+00:00")
        self.assertEqual(len(report["channels"]), len(CHANNELS))
        pm = next(x for x in report["channels"] if x["key"] == "PM25")
        self.assertEqual(pm["summary"]["observed_hours"], 2)
        self.assertEqual(pm["summary"]["expected_hours"], 168)
        self.assertEqual(pm["summary"]["mean"], 15.0)
        self.assertEqual(pm["summary"]["minimum"], 12.0)
        self.assertEqual(pm["summary"]["maximum"], 18.0)
        self.assertEqual(pm["previous"]["observed_hours"], 1)
        self.assertIsNone(pm["comparison_pct"])
        self.assertEqual(len(pm["daily"]), 7)
        self.assertEqual(len(pm["previous_daily"]), 7)
        self.assertEqual(len(pm["hourly"]), 2)
        self.assertEqual(pm["daily"][-1]["hours"], 2)
        self.assertEqual(pm["daily"][-1]["mean"], 15.0)
        text = str(report)
        self.assertNotIn("PRIVATE_SERIAL", text)
        self.assertNotIn("12000", text)
        self.assertNotIn("00:47:17", text)
        self.assertEqual(self.db.read_bytes(), before)

    def test_previous_period_and_navigation(self):
        latest = build_dashboard(self.db, days=7, offset=0)
        previous = build_dashboard(self.db, days=7, offset=7)
        self.assertEqual(previous["start_date_utc"], "2026-10-18")
        self.assertEqual(previous["end_date_utc_exclusive"], "2026-10-25")
        pm = next(x for x in previous["channels"] if x["key"] == "PM25")
        self.assertEqual(pm["summary"]["mean"], 10.0)
        self.assertEqual(pm["summary"]["observed_hours"], 1)
        self.assertNotEqual(latest["start_date_utc"], previous["start_date_utc"])

    def test_valid_periods_return_30_daily_entries(self):
        for days in ALLOWED_DAYS:
            report = build_dashboard(self.db, days=days)
            for channel in report["channels"]:
                self.assertEqual(len(channel["daily"]), days)
                self.assertEqual(channel["summary"]["expected_hours"], 24 * days)

    def test_missing_sensor_distinct_from_zero_value(self):
        with sqlite3.connect(self.db) as con:
            store_history(
                con, serial="PRIVATE_SERIAL", sensor_key="CO",
                resolution="ONE_HOUR", unit="ppm",
                fetched_at="2026-11-01T06:00:00+00:00",
                points=[("2026-10-30T01:00:00.000000+00:00", 0.0, 1)],
            )
        report = build_dashboard(self.db, days=7)
        co = next(x for x in report["channels"] if x["key"] == "CO")
        missing = next(x for x in report["channels"] if x["key"] == "VOC")
        self.assertEqual(co["summary"]["mean"], 0.0)
        self.assertEqual(co["summary"]["observed_hours"], 1)
        self.assertIsNone(missing["summary"]["mean"])
        self.assertEqual(missing["summary"]["observed_hours"], 0)

    def test_full_coverage_can_compare_periods(self):
        with sqlite3.connect(self.db) as con:
            start = datetime(2026, 10, 18, tzinfo=timezone.utc)
            readings = [
                ((start + timedelta(hours=i)).isoformat(timespec="microseconds"), 10.0 if i < 7*24 else 20.0, 1)
                for i in range(14*24)
            ]
            store_history(
                con, serial="PRIVATE_SERIAL", sensor_key="VOC",
                resolution="ONE_HOUR", unit="indice",
                fetched_at="2026-11-01T06:00:00+00:00", points=readings,
            )
        report = build_dashboard(self.db, days=7)
        voc = next(x for x in report["channels"] if x["key"] == "VOC")
        self.assertEqual(voc["summary"]["observed_hours"], 168)
        self.assertEqual(voc["previous"]["observed_hours"], 168)
        self.assertEqual(voc["comparison_pct"], 100.0)

    def test_fail_closed_for_nonexistent_database_and_invalid_params(self):
        with self.assertRaises(DashboardError):
            build_dashboard(Path(self.tmp.name) / "nonexistent.sqlite3")
        for days, offset in ((1,0),(8,0),(30,91),(7,-1),(True,0),(7,False)):
            with self.subTest(days=days,offset=offset):
                with self.assertRaises(DashboardError):
                    build_dashboard(self.db, days=days, offset=offset)
        self.assertFalse((Path(self.tmp.name) / "nonexistent.sqlite3").exists())

    def test_invalid_timestamps_refused(self):
        self.assertIsNone(_as_utc_hour("2026-10-30T12:47:00+00:00"))
        self.assertIsNone(_as_utc_hour("2026-10-30T12:00:01+00:00"))
        self.assertIsNone(_as_utc_hour("2026-10-30T12:00:00"))
        self.assertIsNone(_as_utc_hour("invalid"))
        self.assertIsNone(_as_utc_hour(None))
        self.assertEqual(_as_utc_hour("2026-10-30T12:00:00Z").hour, 12)

    def test_dashboard_source_static_exists_and_has_no_external_dependencies(self):
        self.assertTrue(STATIC_FILE.is_file())
        html = STATIC_FILE.read_text(encoding="utf-8")
        self.assertIn('id="hourlyChart"', html)
        self.assertIn('id="dailyChart"', html)
        self.assertIn('id="dayGrid"', html)
        self.assertIn("/api/dashboard", html)
        self.assertNotIn("https://", html)
        self.assertNotIn("http://", html)
        self.assertNotIn("localStorage", html)
        self.assertNotIn("<script src=", html)
        self.assertNotIn("<link rel=\"stylesheet\"", html)

if __name__ == "__main__":
    unittest.main()
