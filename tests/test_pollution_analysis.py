"""Offline quality and statistical correctness tests for pollution views."""

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import sqlite3
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from home_air_quality_alexa.history_fetch import store_history
from home_air_quality_alexa.history_backfill import ensure_backfill_schema
from home_air_quality_alexa.dashboard import DashboardError, STATIC_FILE, POLLUTION_FILE, _make_handler
from home_air_quality_alexa.pollution_analysis import (
    MIN_DAILY_HOURS, WHO_PM25_24H, associations,
    build_pollution, detect_episodes, weekday_hour_heatmaps,
    pearson, percentile, pm25_daily,
)


UTC = timezone.utc


def dt(day, hour=0):
    return datetime(2026, 10, day, hour, tzinfo=UTC)


class PollutionStatsTests(unittest.TestCase):
    def test_percentile_and_constant_correlations(self):
        self.assertIsNone(percentile([], .9))
        self.assertAlmostEqual(percentile([1, 2, 3, 4, 5], .9), 4.6)
        self.assertIsNone(pearson([1.0] * 48, [2.0] * 48))
        self.assertIsNone(pearson(list(range(47)), list(range(47))))
        self.assertEqual(pearson(list(range(50)), list(range(50))), 1)
        self.assertEqual(pearson(list(range(50)), list(range(49, -1, -1))), -1)

    def test_episode_gaps_break_consecutive_intervals(self):
        # One missing hour must split two contiguous runs.
        points = {
            dt(20, 0): 7., dt(20, 1): 9.,
            dt(20, 3): 15., dt(20, 4): 16.,
            dt(20, 5): 3., dt(20, 6): 11.,
        }
        episodes = detect_episodes(points, 6.)
        self.assertEqual(len(episodes), 2)
        self.assertEqual([e["duration_hours"] for e in episodes], [2, 2])
        self.assertEqual(episodes[0]["start_utc"], dt(20, 3).isoformat())
        self.assertEqual(episodes[0]["end_utc_exclusive"], dt(20, 5).isoformat())

    def test_one_hour_spike_is_not_an_episode(self):
        points = {dt(20, 0): 3., dt(20, 1): 25., dt(20, 2): 4.}
        self.assertEqual(detect_episodes(points, 20.), [])

    def test_pm25_who_24h_requires_all_24_hours(self):
        start, end = date(2026, 10, 20), date(2026, 10, 23)
        series = {}
        for i in range(17):
            series[dt(20, i)] = 30.0
        for i in range(23):
            series[dt(21, i)] = 16.0
        for i in range(24):
            series[dt(22, i)] = 0.0
        days = pm25_daily(series, start, end)
        self.assertEqual(len(days), 3)
        self.assertEqual(days[0]["hours"], 17)
        self.assertIsNone(days[0]["above_who_ambient_reference"])
        self.assertIsNone(days[1]["above_who_ambient_reference"])
        self.assertFalse(days[2]["above_who_ambient_reference"])
        self.assertEqual(MIN_DAILY_HOURS, 24)
        self.assertEqual(WHO_PM25_24H, 15)

    def test_rome_fall_dst_hour_has_two_real_observations(self):
        # 2026-10-25 00:00 UTC and 01:00 UTC both correspond to 02:00 in Rome.
        from home_air_quality_alexa.pollution_analysis import diurnal_profiles
        data = {"PM25": {
            dt(25, 0): 10.0,
            dt(25, 1): 20.0,
            dt(25, 2): 30.0,
        }}
        profile = diurnal_profiles(data)["PM25"]
        self.assertEqual(profile[2]["sample_count"], 2)
        self.assertEqual(profile[2]["mean"], 15.0)
        self.assertEqual(profile[3]["sample_count"], 1)
        heat = weekday_hour_heatmaps(data)["PM25"]
        # Sunday=6 in Python's weekday()
        self.assertEqual(heat[6][2]["sample_count"], 2)
        self.assertEqual(heat[6][2]["mean"], 15.0)

    def test_correlations_exclude_unpaired_hours(self):
        base = dt(20)
        pm = {base + timedelta(hours=i): float(i) for i in range(60)}
        voc = {base + timedelta(hours=i): float(i) * 3 for i in range(20, 60)}
        co = {base + timedelta(hours=i): float(i) for i in range(60)}
        humidity = {base + timedelta(hours=i): 50.0 for i in range(60)}
        result = {item["sensor"]: item for item in associations({
            "PM25": pm, "VOC": voc, "CO": co, "Humidity": humidity
        })}
        self.assertEqual(result["VOC"]["paired_hours"], 40)
        self.assertIsNone(result["VOC"]["r"])
        self.assertEqual(result["CO"]["r"], 1.0)
        self.assertIsNone(result["Humidity"]["r"])
        self.assertNotIn("Air Quality", result)


class PollutionDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.db = Path(self.tmp.name) / "air-quality.sqlite3"
        with sqlite3.connect(self.db) as conn:
            rows = [
                ((dt(3) + timedelta(hours=i)).isoformat(timespec="microseconds"),
                 25.0 if 95 <= i < 101 else 10.0,
                 1)
                for i in range(29 * 24)
            ]
            store_history(
                conn, serial="SECRET_SERIAL", sensor_key="PM25",
                resolution="ONE_HOUR", points=rows, unit="µg/m³",
                fetched_at="2026-11-01T00:00:00+00:00",
            )
            store_history(
                conn, serial="SECRET_SERIAL", sensor_key="VOC",
                resolution="ONE_HOUR",
                points=[(t, float(i % 20 + 1), 1) for i, (t, _, _) in enumerate(rows)],
                fetched_at="2026-11-01T00:00:00+00:00",
            )
            store_history(
                conn, serial="SECRET_SERIAL", sensor_key="PM25",
                resolution="ONE_HOUR",
                points=[("2026-10-30T15:47:17.226000+00:00", 999999, 1)],
                fetched_at="2026-11-01T00:00:00+00:00",
            )
            ensure_backfill_schema(conn)

    def tearDown(self):
        self.tmp.cleanup()

    def test_last_30_days_uses_all_696_pm25_points_not_legacy(self):
        before = self.db.read_bytes()
        report = build_pollution(self.db, days=30)
        pm = report["pm25"]
        self.assertEqual(pm["hours_available"], 696)
        self.assertEqual(pm["hours_expected"], 720)
        self.assertAlmostEqual(pm["coverage_pct"], 96.7, places=1)
        self.assertEqual(pm["median"], 10.0)
        self.assertEqual(pm["maximum"], 25.0)
        self.assertEqual(len(pm["episodes"]), 1)
        self.assertEqual(pm["episodes"][0]["duration_hours"], 6)
        self.assertEqual(len(pm["daily"]), 30)
        self.assertEqual(pm["complete_days"], 29)
        self.assertEqual(pm["days_above_who_reference"], 0)
        self.assertEqual(len(report["diurnal"]["PM25"]), 24)
        self.assertEqual(len(report["weekday"]["PM25"]), 7)
        self.assertEqual(len(report["weekday_hour_heatmap"]["PM25"]), 7)
        self.assertTrue(all(len(row) == 24 for row in report["weekday_hour_heatmap"]["PM25"]))
        self.assertEqual(len(report["associations"]), 3)
        self.assertNotIn("SECRET_SERIAL", str(report))
        self.assertNotIn("999999", str(report))
        self.assertEqual(before, self.db.read_bytes())

    def test_invalid_parameters_rejected(self):
        for days, offset in ((31, 0),(6,0),(30,-1),(30,91)):
            with self.assertRaises(DashboardError):
                build_pollution(self.db, days=days, offset=offset)

    def test_multiple_monitor_series_never_mixed(self):
        with sqlite3.connect(self.db) as con:
            store_history(con, serial="SECOND_DEVICE", sensor_key="PM25",
                          resolution="ONE_HOUR", unit="µg/m³",
                          fetched_at="2026-11-01T00:00:00+00:00",
                          points=[("2026-10-30T20:00:00.000000+00:00", 200.0, 1)])
        with self.assertRaisesRegex(DashboardError, "più monitor"):
            build_pollution(self.db, days=30)

    def test_website_files_and_offline_ui(self):
        self.assertTrue(POLLUTION_FILE.is_file())
        html = POLLUTION_FILE.read_text("utf-8")
        self.assertIn("/api/pollution", html)
        self.assertIn("id=\"heatmap\"", html)
        self.assertIn("id=\"events\"", html)
        self.assertIn("id=\"relations\"", html)
        self.assertIn('href="/"', html)
        self.assertIn('href="/pollution"', STATIC_FILE.read_text("utf-8"))
        self.assertNotIn("https://", html)
        self.assertNotIn("localStorage", html)


if __name__ == "__main__":
    unittest.main()
