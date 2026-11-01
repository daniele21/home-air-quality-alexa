import csv
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import sqlite3
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from home_air_quality_alexa.collector import (export_csv, insert_readings, load_private_json,
                                               open_database, save_private_json, selected_devices)


class StorageTests(unittest.TestCase):
    def test_deduplicates_by_sensor_sample_timestamp(self):
        with TemporaryDirectory() as td:
            db = open_database(Path(td) / "data" / "readings.sqlite3")
            sample = SimpleNamespace(value=17.2, error=False, time_of_sample=datetime(2026, 10, 31, tzinfo=timezone.utc), scale="MicroGramsPerCubicMeter")
            device = SimpleNamespace(serial_number="s1", account_name="Office")
            batch = [(device, {"PM25": sample})]
            self.assertEqual(insert_readings(db, batch, "2026-10-31T12:00:00+00:00"), 1)
            self.assertEqual(insert_readings(db, batch, "2026-10-31T12:05:00+00:00"), 0)
            self.assertEqual(db.execute("SELECT count(*) FROM readings").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT timestamp_source FROM readings").fetchone()[0], "device")
            path = Path(td) / "export.csv"
            self.assertEqual(export_csv(db, path), 1)
            with path.open(newline="") as f:
                self.assertEqual(list(csv.DictReader(f))[0]["numeric_value"], "17.2")
            db.close()

    def test_selects_only_air_quality_devices(self):
        echo = SimpleNamespace(serial_number="1")
        monitor = SimpleNamespace(serial_number="2")
        found = list(selected_devices({"1": echo, "2": monitor},
                 {"1": {"temperature": 20}, "2": {"PM25": 8}}, None))
        self.assertEqual([item[0].serial_number for item in found], ["2"])

    def test_private_json_permissions(self):
        with TemporaryDirectory() as td:
            f = Path(td) / "secure" / "login.json"
            save_private_json(f, {"token": "not-for-git"})
            self.assertEqual(f.stat().st_mode & 0o777, 0o600)
            self.assertEqual(load_private_json(f), {"token": "not-for-git"})
            f.chmod(0o644)
            with self.assertRaises(PermissionError):
                load_private_json(f)

    def test_no_time_sample_uses_observed_timestamp(self):
        with TemporaryDirectory() as td:
            db = open_database(Path(td) / "r.sqlite3")
            sample = SimpleNamespace(value=25, error=False, time_of_sample=None, scale="%")
            d = SimpleNamespace(serial_number="s2", account_name="Bedroom")
            insert_readings(db, [(d, {"Humidity": sample})], "2026-10-31T12:00:00+00:00")
            self.assertEqual(db.execute("SELECT timestamp_source FROM readings").fetchone()[0], "poll")
            db.close()


if __name__ == "__main__":
    unittest.main()
