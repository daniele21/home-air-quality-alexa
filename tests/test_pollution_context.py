"""Activity journal and explicitly opted-in outdoor model, entirely offline tests."""
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import sys
from threading import Thread
from http.server import ThreadingHTTPServer
from urllib.request import build_opener, ProxyHandler, Request
from urllib.error import HTTPError
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from home_air_quality_alexa.dashboard import DashboardError, _make_handler
from home_air_quality_alexa.history_backfill import ensure_backfill_schema
from home_air_quality_alexa.history_fetch import store_history
from home_air_quality_alexa.pollution_context import (
    ACTIVITIES, add_activity, delete_activity, activity_list, context_report,
    private_db, sync_outdoor, _parse_open_meteo, _position,
)

UTC = timezone.utc

def api_data(start=date(2026,10,3),hours=696,override=None):
    base=datetime.combine(start,datetime.min.time(),timezone.utc)
    raw={"timezone":"GMT","utc_offset_seconds":0,
         "hourly_units":{"pm2_5":"μg/m³"},
         "hourly":{"time":[(base+timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M") for i in range(hours)],
                   "pm2_5":[float(i%9) for i in range(hours)]}}
    if override:
        raw.update(override)
    return json.dumps(raw).encode("utf-8")

class ContextTests(unittest.TestCase):
    def setUp(self):
        self.folder=TemporaryDirectory()
        self.root=Path(self.folder.name)
        self.context=self.root/"context.sqlite3"
        self.history=self.root/"history.sqlite3"
        with sqlite3.connect(self.history) as conn:
            start=datetime(2026,10,3,tzinfo=UTC)
            store_history(conn,serial="PRIVATE_DEVICE",sensor_key="PM25",
                          resolution="ONE_HOUR",unit="µg/m³",fetched_at="2026-11-01T00:00:00+00:00",
                          points=[((start+timedelta(hours=i)).isoformat(timespec="microseconds"),float(i%9)+4,1) for i in range(29*24)])
            ensure_backfill_schema(conn)
    def tearDown(self):
        self.folder.cleanup()

    def test_journal_local_only_timestamp_and_delete(self):
        payload={"activity":"cooking","start":"2026-10-30T13:20:00+01:00",
                 "duration_minutes":35,"note":"Pasta e cappa"}
        event=add_activity(payload,db=self.context)
        self.assertEqual(event["start_utc"],"2026-10-30T12:20:00+00:00")
        self.assertEqual(event["activity"],"cooking")
        self.assertTrue(self.context.is_file())
        self.assertEqual(self.context.stat().st_mode & 0o077,0)
        notes=activity_list(date(2026,10,30),date(2026,10,31),db=self.context)
        self.assertEqual(len(notes),1)
        self.assertEqual(notes[0]["note"],"Pasta e cappa")
        self.assertTrue(delete_activity(event["id"],db=self.context))
        self.assertFalse(delete_activity(event["id"],db=self.context))
        self.assertFalse(activity_list(date(2026,10,30),date(2026,10,31),db=self.context))

    def test_invalid_payloads_never_saved(self):
        base={"activity":"cooking","start":"2026-10-30T12:00:00Z",
              "duration_minutes":30,"note":""}
        bad=[
            {**base,"start":"2026-10-30T12:00:00"},
            {**base,"activity":"malicious"},
            {**base,"note":"a"*201},
            {**base,"duration_minutes":361},
            {**base,"duration_minutes":True},
            {**base,"secret_field":"abc"},
        ]
        for payload in bad:
            with self.subTest(payload=payload.get("activity")):
                with self.assertRaises(DashboardError):
                    add_activity(payload,db=self.context)
        with self.assertRaises(DashboardError):
            delete_activity("' OR 1=1 --",db=self.context)

    def test_openmeteo_schema_and_utc_bounds(self):
        values=_parse_open_meteo(json.loads(api_data(hours=49)),date(2026,10,3),date(2026,10,5))
        self.assertEqual(len(values),48)
        self.assertEqual(values[0][0],"2026-10-03T00:00:00+00:00")
        self.assertEqual(values[-1][0],"2026-10-04T23:00:00+00:00")
        with self.assertRaises(DashboardError):
            _parse_open_meteo(json.loads(api_data(hours=1,override={"utc_offset_seconds":3600})),date(2026,10,3),date(2026,10,4))
        with self.assertRaises(DashboardError):
            _parse_open_meteo(json.loads(api_data(hours=1,override={"hourly_units":{"pm2_5":"ppm"}})),date(2026,10,3),date(2026,10,4))
        self.assertEqual(_position(45.4963,9.2012),(45.5,9.2))
        for x,y in [(float("nan"),1),(100,0),("45.5",9.2)]:
            with self.assertRaises(DashboardError):
                _position(x,y)

    def test_opt_in_fetch_stores_modelled_series_and_comparison(self):
        seen=[]
        def fake(req):
            seen.append(req.full_url)
            return api_data()
        before=self.history.read_bytes()
        out=sync_outdoor(date(2026,10,3),date(2026,11,1),
                         db=self.context,latitude=45.4963,longitude=9.2012,fetcher=fake)
        self.assertEqual(out["imported_hours"],696)
        self.assertTrue(out["model_only"])
        self.assertEqual(len(seen),1)
        self.assertIn("latitude=45.5",seen[0])
        self.assertIn("longitude=9.2",seen[0])
        self.assertNotIn("45.4963",seen[0])
        report=context_report(self.history,days=30,db=self.context)
        self.assertEqual(report["comparison"]["paired_hours"],696)
        self.assertEqual(report["outdoor"]["available_hours"],696)
        self.assertTrue(report["outdoor"]["configured"])
        self.assertEqual(len(report["comparison"]["paired_hourly"]),696)
        self.assertNotIn("PRIVATE_DEVICE",str(report))
        self.assertNotIn("45.5",str(report))
        self.assertEqual(self.history.read_bytes(),before)
        with self.assertRaisesRegex(DashboardError,"recentemente"):
            sync_outdoor(date(2026,10,3),date(2026,11,1),db=self.context,fetcher=fake)
        self.assertEqual(len(seen),1)

    def test_no_network_or_outdoor_records_on_load_without_optin(self):
        report=context_report(self.history,days=30,db=self.context)
        self.assertEqual(report["comparison"]["paired_hours"],0)
        self.assertFalse(report["outdoor"]["configured"])
        self.assertIsNone(report["comparison"]["mean_difference"])

    def test_annotation_descriptive_before_after_not_causal(self):
        event=add_activity({"activity":"candle","start":"2026-10-30T12:30:00Z",
                             "duration_minutes":40,"note":"test"},db=self.context)
        r=context_report(self.history,days=30,db=self.context)
        self.assertEqual(len(r["activities"]),1)
        self.assertEqual(r["activities"][0]["id"],event["id"])
        self.assertIsNotNone(r["activities"][0]["pm25_delta_two_hours_descriptive"])

    def test_outdoor_error_does_not_store_location_or_values(self):
        def offline(req):
            return b'{"error":true,"reason":"Sensitive tokens or coordinates"}'
        with self.assertRaises(DashboardError) as caught:
            sync_outdoor(date(2026,10,3),date(2026,10,4),latitude=45.12,longitude=9.12,
                         db=self.context,fetcher=offline)
        self.assertNotIn("Sensitive",str(caught.exception))
        r=context_report(self.history,days=30,db=self.context)
        self.assertFalse(r["outdoor"]["configured"])

    def test_server_same_origin_and_json_controls(self):
        server=ThreadingHTTPServer(("127.0.0.1",0),_make_handler(self.history,self.context))
        worker=Thread(target=server.serve_forever,daemon=True);worker.start()
        root=f"http://127.0.0.1:{server.server_port}"
        opener=build_opener(ProxyHandler({})).open
        def post(url,data,origin=None,ctype="application/json"):
            headers={"Content-Type":ctype}
            if origin:headers["Origin"]=origin
            req=Request(root+url,data=json.dumps(data).encode("utf-8"),
                        headers=headers,method="POST")
            return opener(req,timeout=5)
        activity={"activity":"windows_open","start":"2026-10-30T11:00:00Z",
                  "duration_minutes":30,"note":"aria"}
        try:
            with opener(root+"/context",timeout=5) as resp:
                self.assertIn(b'id="activityForm"',resp.read())
            with opener(root+"/api/context?days=30",timeout=5) as resp:
                data=json.load(resp)
                self.assertEqual(data["comparison"]["paired_hours"],0)
            for url,data,origin,ctype,status in [
                ("/api/context/activity",activity,None,"application/json",403),
                ("/api/context/activity",activity,"https://evil.example","application/json",403),
                ("/api/context/activity",activity,root,"text/plain",415),
                ("/api/context/activity",{"bad":"x"},root,"application/json",400),
            ]:
                with self.subTest(status=status):
                    with self.assertRaises(HTTPError) as e:
                        post(url,data,origin,ctype)
                    self.assertEqual(e.exception.code,status)
            with post("/api/context/activity",activity,root) as r:
                self.assertEqual(r.status,201)
                saved=json.load(r)["activity"]["id"]
            with opener(root+"/api/context?days=30",timeout=5) as r:
                self.assertEqual(len(json.load(r)["activities"]),1)
            with post("/api/context/delete",{"id":saved},root) as r:
                self.assertTrue(json.load(r)["deleted"])
        finally:
            server.shutdown();server.server_close();worker.join(timeout=5)

if __name__ == "__main__":
    unittest.main()
