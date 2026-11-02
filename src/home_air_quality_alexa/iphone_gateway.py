"""Optional authenticated gateway for Airwise over *private* Tailscale Serve.

The existing Airwise service stays on 127.0.0.1:8766. This sidecar listens
on 127.0.0.1:18767 and should only be reached through Tailscale Serve, which
strips spoofed identity headers and injects Tailscale-User-Login. In addition
to tailnet authorization, require a locally configured user allowlist.

No Tailscale client installation, Serve activation or system setting change
is performed by this module. Writes are disabled by default.

Threat boundary: local processes on this Mac can forge identity headers.
This is not protection from another compromised account/process on this Mac.
"""
from __future__ import annotations

import argparse
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import re
from urllib.parse import urlsplit


LISTEN_IP = "127.0.0.1"
LISTEN_PORT = 18767
AIRWISE_IP = "127.0.0.1"
AIRWISE_PORT = 8766
READ_PATHS = frozenset(("/", "/pollution", "/context",
                       "/api/dashboard", "/api/pollution", "/api/context"))
WRITE_PATHS = frozenset(("/api/context/activity", "/api/context/delete",
                        "/api/outdoor/sync"))
COPY_HEADERS = ("Content-Type", "Cache-Control", "Content-Security-Policy",
                "X-Frame-Options", "X-Content-Type-Options", "Referrer-Policy")
HOST_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9.-]{0,235}[a-z0-9])?\\.ts\\.net$")
LOGIN_PATTERN = re.compile(r"^[^\\s<>\\x00-\\x1f]{1,240}@" r"[^\\s<>\\x00-\\x1f]{1,150}$")


def validate_gateway_settings(host: str, login: str) -> tuple[str, str]:
    if not isinstance(host, str) or not isinstance(login, str):
        raise ValueError("Host and approved Tailscale login are required")
    host = host.strip().lower().rstrip(".")
    login = login.strip().lower()
    if not HOST_PATTERN.fullmatch(host) or ".." in host or host.startswith("-"):
        raise ValueError("An explicit, valid Tailscale *.ts.net hostname is required")
    if not LOGIN_PATTERN.fullmatch(login):
        raise ValueError("An explicit Tailscale user login is required")
    return host, login


def make_gateway_handler(
    host: str, allowed_login: str, *,
    backend_port: int = AIRWISE_PORT,
    enable_writes: bool = False,
):
    host, allowed_login = validate_gateway_settings(host, allowed_login)
    if not 1 <= backend_port <= 65535:
        raise ValueError("Invalid backend port")

    class Gateway(BaseHTTPRequestHandler):
        server_version = "Airwise-private-gateway"
        sys_version = ""

        def log_message(self, fmt, *args):
            # Don't log paths, identities, headers, user diary or values.
            return

        def _reject(self, code: int, message: str):
            body=json.dumps({"error":message},ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type","application/json; charset=utf-8")
            self.send_header("Cache-Control","no-store")
            self.send_header("X-Content-Type-Options","nosniff")
            self.send_header("Content-Length",str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            self._handle("GET")

        def do_POST(self):
            self._handle("POST")

        def do_HEAD(self):
            self._reject(405,"Metodo non supportato")

        def _handle(self, method: str):
            # Serve is configured as a private tailnet-only HTTPS proxy.
            # The forwarded Host must exactly match the locally pinned name.
            request_host=self.headers.get("Host","").strip().lower()
            if request_host not in (host,host+":443"):
                self._reject(403,"Host non autorizzato")
                return
            # Tailscale Serve strips spoofed identity headers. Tagged nodes
            # lack a user login and are denied by this fail-closed policy.
            identity=self.headers.get("Tailscale-User-Login","").strip().lower()
            if identity != allowed_login:
                self._reject(403,"Identità Tailscale non autorizzata")
                return
            if len(self.path)>256 or "\\x00" in self.path:
                self._reject(400,"Percorso non valido")
                return
            try:
                uri=urlsplit(self.path)
            except ValueError:
                self._reject(400,"Percorso non valido")
                return
            if uri.scheme or uri.netloc or uri.fragment or uri.path not in (
                READ_PATHS if method=="GET" else WRITE_PATHS
            ):
                self._reject(404,"Risorsa non disponibile")
                return
            body=None
            headers={"Host":f"{AIRWISE_IP}:{backend_port}",
                     "Accept":"application/json, text/html"}
            if method=="POST":
                if not enable_writes:
                    self._reject(403,"Modifiche da iPhone disabilitate")
                    return
                if self.headers.get("Origin","") != "https://"+host:
                    self._reject(403,"Origin non autorizzata")
                    return
                if self.headers.get("Sec-Fetch-Site","same-origin") not in ("same-origin","none"):
                    self._reject(403,"Richiesta cross-site non autorizzata")
                    return
                if self.headers.get("Content-Type","").split(";")[0].strip().lower()!="application/json":
                    self._reject(415,"Richiesto JSON")
                    return
                try:
                    length=int(self.headers.get("Content-Length","0"))
                except ValueError:
                    self._reject(400,"Dimensione non valida")
                    return
                if not 2<=length<=2048:
                    self._reject(413,"Dimensione fuori limite")
                    return
                body=self.rfile.read(length)
                headers["Content-Type"]="application/json"
                headers["Origin"]=f"http://{AIRWISE_IP}:{backend_port}"
            try:
                upstream=HTTPConnection(AIRWISE_IP,backend_port,timeout=8)
                try:
                    upstream.request(method,self.path,body=body,headers=headers)
                    response=upstream.getresponse()
                    data=response.read(8_000_001)
                    if len(data)>8_000_000:
                        self._reject(502,"Risposta troppo grande")
                        return
                    status=response.status
                    safe_headers={name:response.getheader(name) for name in COPY_HEADERS}
                finally:
                    upstream.close()
            except (OSError,TimeoutError):
                self._reject(503,"Servizio Airwise non disponibile")
                return
            self.send_response(status)
            for name,value in safe_headers.items():
                if value is not None:self.send_header(name,value)
            self.send_header("Content-Length",str(len(data)))
            self.send_header("Cache-Control","no-store")
            self.end_headers()
            self.wfile.write(data)

    return Gateway


def main() -> None:
    parser=argparse.ArgumentParser(
        description="Private tailnet-only Airwise sidecar (requires Tailscale Serve)"
    )
    parser.add_argument("--tailnet-host",required=True,
        help="Explicit Mac tailnet FQDN, e.g. mac.example.ts.net")
    parser.add_argument("--allowed-login",required=True,
        help="Exact Tailscale-User-Login identity; do not put in public CI logs")
    parser.add_argument("--port",type=int,default=LISTEN_PORT)
    parser.add_argument("--enable-writes",action="store_true",
        help="Allow same-origin JSON activity/outdoor POST (default: read-only)")
    args=parser.parse_args()
    if not 1024<=args.port<=65535 or args.port==AIRWISE_PORT:
        parser.error("Gateway must use an unprivileged, separate local port")
    try:
        handler=make_gateway_handler(
            args.tailnet_host,args.allowed_login,enable_writes=args.enable_writes)
    except ValueError as exc:
        parser.error(str(exc))
    with ThreadingHTTPServer((LISTEN_IP,args.port),handler) as server:
        print("Airwise private gateway on loopback; "
              "tailnet HTTPS and ACLs must be independently configured.",flush=True)
        try:server.serve_forever(poll_interval=.5)
        except KeyboardInterrupt:pass


if __name__=="__main__":
    main()
