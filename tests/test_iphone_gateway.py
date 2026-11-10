"""Private Airwise iPhone gateway: no network/identity source besides loopback fixtures."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from urllib.request import build_opener, ProxyHandler, Request
from urllib.error import HTTPError
from pathlib import Path
import json
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
from home_air_quality_alexa.iphone_gateway import (
    make_gateway_handler, validate_gateway_settings,
    LISTEN_IP, LISTEN_PORT, READ_PATHS, WRITE_PATHS,
)

HOST="mac.example.ts.net"
LOGIN="alice@example.test"

class FakeAirwise(BaseHTTPRequestHandler):
    requests=[]
    def log_message(self,*args):pass
    def do_GET(self):
        FakeAirwise.requests.append(("GET",self.path,dict(self.headers)))
        data=(json.dumps({"demo_only":True}) if self.path.startswith("/api/")
              else "<html lang='it'><title>Fixture</title>Airwise demo</html>").encode()
        self.send_response(200)
        self.send_header("Content-Type","application/json" if self.path.startswith("/api/") else "text/html")
        self.send_header("Cache-Control","no-store")
        self.send_header("Content-Length",str(len(data)))
        self.end_headers()
        self.wfile.write(data)
    def do_POST(self):
        length=int(self.headers["Content-Length"])
        FakeAirwise.requests.append(("POST",self.path,dict(self.headers)))
        data=json.dumps({"ok":True,"received_bytes":length}).encode()
        self.send_response(201)
        self.send_header("Content-Type","application/json")
        self.send_header("Content-Length",str(len(data)))
        self.end_headers()
        self.wfile.write(data)

class GatewayTests(unittest.TestCase):
    def setUp(self):
        self.assertEqual(LISTEN_IP,"127.0.0.1")
        FakeAirwise.requests=[]
        self.backend=ThreadingHTTPServer(("127.0.0.1",0),FakeAirwise)
        self.backend_runner=Thread(target=self.backend.serve_forever,daemon=True)
        self.backend_runner.start()
        self.gateway=ThreadingHTTPServer(("127.0.0.1",0),
            make_gateway_handler(HOST,LOGIN,backend_port=self.backend.server_port))
        self.gateway_runner=Thread(target=self.gateway.serve_forever,daemon=True)
        self.gateway_runner.start()
        self.root=f"http://127.0.0.1:{self.gateway.server_port}"
        self.open=build_opener(ProxyHandler({})).open

    def tearDown(self):
        for server,thread in ((self.gateway,self.gateway_runner),(self.backend,self.backend_runner)):
            server.shutdown();server.server_close();thread.join(timeout=5)

    def get(self,path,*,host=HOST,login=LOGIN,headers=None):
        h={"Host":host,"Tailscale-User-Login":login}
        h.update(headers or {})
        return self.open(Request(self.root+path,headers=h),timeout=5)

    def post(self,path,data,*,host=HOST,login=LOGIN,headers=None):
        h={"Host":host,"Tailscale-User-Login":login,
            "Origin":f"https://{HOST}","Content-Type":"application/json"}
        h.update(headers or {})
        return self.open(Request(self.root+path,data=json.dumps(data).encode(),
                                 headers=h,method="POST"),timeout=5)

    def test_loopback_host_validation(self):
        self.assertEqual(LISTEN_PORT,18767)
        self.assertEqual(validate_gateway_settings("Mac.Example.Ts.Net","ALICE@Example.Test"),
                         (HOST,LOGIN))
        for h in ("127.0.0.1","evil.example.com","example.ts.net.evil.com",
                  "a..b.ts.net","mac.example.com","http://mac.example.ts.net"):
            with self.subTest(host=h),self.assertRaises(ValueError):
                validate_gateway_settings(h,LOGIN)
        for email in ("", "malicious header","alice\r\nother@example.test", "alice"):
            with self.subTest(identity=email),self.assertRaises(ValueError):
                validate_gateway_settings(HOST,email)

    def test_allowed_get_routes_rewrite_upstream_host_no_identity_leak(self):
        for path in ("/","/pollution","/context","/api/dashboard?days=30",
                     "/api/pollution?days=7","/api/context?days=14"):
            with self.subTest(path=path):
                with self.get(path) as result:
                    self.assertEqual(result.status,200)
                    self.assertIn(b"demo",result.read())
                    self.assertEqual(result.headers["Cache-Control"],"no-store")
        self.assertEqual(len(FakeAirwise.requests),6)
        for method,path,h in FakeAirwise.requests:
            self.assertEqual(method,"GET")
            self.assertEqual(h["Host"],f"127.0.0.1:{self.backend.server_port}")
            self.assertNotIn("Tailscale-User-Login",h)
            self.assertNotIn("Authorization",h)

    def test_host_identity_and_unlisted_paths_fail_closed(self):
        attempts=[
            ("/",{"host":"evil.example.org"},403),
            ("/",{"host":"127.0.0.1:18767"},403),
            ("/",{"login":"mallory@example.test"},403),
            ("/",{"login":""},403),
            ("/secret",{},404),
            ("/api/admin",{},404),
            ("/api/dashboard/../../secret",{},404),
        ]
        for path,kwargs,code in attempts:
            with self.subTest(path=path,code=code),self.assertRaises(HTTPError) as ex:
                self.get(path,**kwargs)
            self.assertEqual(ex.exception.code,code)
        self.assertFalse(FakeAirwise.requests)

    def test_writes_disabled_by_default(self):
        for path in WRITE_PATHS:
            with self.subTest(path=path),self.assertRaises(HTTPError) as ex:
                self.post(path,{"activity":"cooking"})
            self.assertEqual(ex.exception.code,403)
        self.assertFalse(FakeAirwise.requests)

    def test_optin_writes_still_require_same_origin_json_and_identity(self):
        with ThreadingHTTPServer(("127.0.0.1",0),
            make_gateway_handler(HOST,LOGIN,backend_port=self.backend.server_port,
                                 enable_writes=True)) as allow:
            worker=Thread(target=allow.serve_forever,daemon=True);worker.start()
            root=f"http://127.0.0.1:{allow.server_port}"
            def send(headers):
                h={"Host":HOST,"Tailscale-User-Login":LOGIN,
                   "Content-Type":"application/json","Origin":f"https://{HOST}"}
                h.update(headers)
                return self.open(Request(root+"/api/context/activity",
                    headers=h,data=b'{"activity":"cooking"}',method="POST"),timeout=5)
            try:
                for bad in [
                    {"Origin":"https://evil.example.com"},
                    {"Tailscale-User-Login":"other@example.test"},
                    {"Content-Type":"text/plain"},
                    {"Sec-Fetch-Site":"cross-site"},
                ]:
                    with self.subTest(headers=bad),self.assertRaises(HTTPError):
                        send(bad)
                self.assertFalse(FakeAirwise.requests)
                with send({}) as result:self.assertEqual(result.status,201)
                self.assertEqual(len(FakeAirwise.requests),1)
                method,path,headers=FakeAirwise.requests[0]
                self.assertEqual(method,"POST")
                self.assertEqual(headers.get("Origin"),
                                 f"http://127.0.0.1:{self.backend.server_port}")
                self.assertNotIn("Tailscale-User-Login",headers)
            finally:
                allow.shutdown();worker.join(timeout=5)

if __name__=="__main__":
    unittest.main()
