"""Synthetic screenshot fixture correctness; no personal Alexa data or network."""
from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from synthetic_ui_server import data, pollution, context, ROOT


class SyntheticReviewTests(unittest.TestCase):
    def test_fixtures_shape_and_dates(self):
        d=data(7)
        self.assertEqual(len(d["channels"]),5)
        self.assertEqual(d["start_date_utc"],"2026-10-25")
        self.assertEqual(d["end_date_utc_exclusive"],"2026-11-01")
        for ch in d["channels"]:
            self.assertEqual(len(ch["hourly"]),168)
            self.assertEqual(len(ch["daily"]),7)
            self.assertEqual(ch["summary"]["expected_hours"],168)
        self.assertTrue(ROOT.is_dir())

    def test_pollution_data_is_explicit_demo_and_consistent(self):
        p=pollution(30)
        self.assertEqual(p["pm25"]["hours_available"],720)
        self.assertEqual(len(p["pm25"]["daily"]),30)
        self.assertEqual(len(p["weekday_hour_heatmap"]["PM25"]),7)
        self.assertEqual(len(p["weekday_hour_heatmap"]["VOC"][0]),24)
        self.assertEqual(len(p["associations"]),3)

    def test_outdoor_demo_is_partial_and_does_not_identify_user(self):
        c=context(30)
        self.assertEqual(c["comparison"]["paired_hours"],72)
        self.assertEqual(len(c["comparison"]["paired_hourly"]),72)
        self.assertTrue(c["activities"][0]["note"].endswith("(dato sintetico)"))
        self.assertNotIn("latitude",str(c))
        self.assertNotIn("longitude",str(c))
        self.assertNotIn("device_serial",str(c))


if __name__=="__main__":
    unittest.main()
