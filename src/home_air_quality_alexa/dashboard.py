"""Local-only, read-only Alexa historical air-quality dashboard.

No account data, device identifiers, tokens, or raw HTTP logs leave the Mac.
The web UI uses only canonical UTC-hour historical buckets and runs on loopback.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
from pathlib import Path
import sqlite3
from urllib.parse import parse_qs, urlparse
from typing import Any


CHANNELS = (
    ("PM25", "PM2.5", "µg/m³"),
    ("VOC", "VOC", "indice"),
    ("CO", "CO", "ppm"),
    ("Humidity", "Umidità", "%"),
    ("Air Quality", "Qualità aria", "indice"),
)
DEFAULT_DB = Path.home() / ".local/share/home-air-quality-alexa/readings.sqlite3"
STATIC_FILE = Path(__file__).with_name("dashboard.html")
POLLUTION_FILE = Path(__file__).with_name("pollution.html")
ALLOWED_DAYS = (7, 14, 30)
MAX_OFFSET = 90


class DashboardError(Exception):
    """A user-safe dashboard API error."""


def _as_utc_hour(timestamp: Any) -> datetime | None:
    if not isinstance(timestamp, str):
        return None
    try:
        value = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    if value.tzinfo is None:
        return None
    value = value.astimezone(timezone.utc)
    if value.minute or value.second or value.microsecond:
        return None
    return value


def _read_db(db: Path) -> sqlite3.Connection:
    if db.is_symlink() or not db.is_file():
        raise DashboardError("Database SQLite locale non disponibile")
    # URI mode=ro, no DB creation or WAL mutation.
    connection = sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True)
    connection.execute("PRAGMA query_only=ON")
    return connection


def _source_relation(connection: sqlite3.Connection) -> str | None:
    relations = {
        row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table','view')"
        )
    }
    if "historical_hourly_utc" in relations:
        return "historical_hourly_utc"
    if "historical_readings" in relations:
        return "historical_readings"
    return None


def _date_rows(start: date, end: date) -> list[date]:
    return [start + timedelta(days=i) for i in range((end - start).days)]


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 3) if values else None


def _period_summary(values: list[float], total_hours: int) -> dict[str, Any]:
    return {
        "mean": _mean(values),
        "minimum": min(values) if values else None,
        "maximum": max(values) if values else None,
        "observed_hours": len(values),
        "expected_hours": total_hours,
        "coverage_pct": round(100 * len(values) / total_hours, 1) if total_hours else 0,
    }


def build_dashboard(db: Path, *, days: int = 7, offset: int = 0) -> dict[str, Any]:
    """Return a unit-preserving, timezone-explicit, privacy-safe dashboard."""
    if type(days) is not int or days not in ALLOWED_DAYS:
        raise DashboardError("Periodo non supportato: scegliere 7, 14 o 30 giorni")
    if type(offset) is not int or offset < 0 or offset > MAX_OFFSET:
        raise DashboardError("Offset non valido: scegliere tra 0 e 90 giorni")

    try:
        with _read_db(db) as connection:
            relation = _source_relation(connection)
            if relation is None:
                raise DashboardError("Nessuna serie storica oraria ancora importata")
            latest = connection.execute(
                f"SELECT MAX(bucket_timestamp_utc) FROM {relation} "
                "WHERE resolution = 'ONE_HOUR' AND sensor_key IN (?,?,?,?,?) "
                "AND substr(bucket_timestamp_utc,15,2)='00' "
                "AND substr(bucket_timestamp_utc,18,2)='00'",
                tuple(channel[0] for channel in CHANNELS),
            ).fetchone()[0]
            if _as_utc_hour(latest) is None:
                raise DashboardError("Nessun intervallo orario UTC valido disponibile")
            last_time = _as_utc_hour(latest)
            assert last_time is not None
            end_day = last_time.date() + timedelta(days=1) - timedelta(days=offset)
            start_day = end_day - timedelta(days=days)
            previous_start = start_day - timedelta(days=days)
            start_ts = datetime.combine(previous_start, time.min, tzinfo=timezone.utc).isoformat()
            end_ts = datetime.combine(end_day, time.min, tzinfo=timezone.utc).isoformat()

            rows = connection.execute(
                f"SELECT sensor_key,bucket_timestamp_utc,numeric_value FROM {relation} "
                "WHERE resolution='ONE_HOUR' AND sensor_key IN (?,?,?,?,?) "
                "AND bucket_timestamp_utc >= ? AND bucket_timestamp_utc < ? "
                "ORDER BY sensor_key,bucket_timestamp_utc",
                (*tuple(channel[0] for channel in CHANNELS), start_ts, end_ts),
            ).fetchall()
    except sqlite3.Error as exc:
        raise DashboardError("Impossibile leggere lo storico SQLite locale") from exc

    # Deduplication is at the UTC hour, never by a rolling-window offset.
    history: dict[str, dict[datetime, float]] = defaultdict(dict)
    for sensor, timestamp, raw_value in rows:
        dt = _as_utc_hour(timestamp)
        if dt is None or isinstance(raw_value, bool) or not isinstance(raw_value, (int, float)):
            continue
        if not math.isfinite(raw_value):
            continue
        history[sensor][dt] = float(raw_value)

    current_days = _date_rows(start_day, end_day)
    previous_days = _date_rows(previous_start, start_day)
    channels: list[dict[str, Any]] = []
    for key, label, unit in CHANNELS:
        readings = history.get(key, {})
        current_points = [
            (dt, value) for dt, value in sorted(readings.items())
            if start_day <= dt.date() < end_day
        ]
        previous_points = [
            value for dt, value in readings.items()
            if previous_start <= dt.date() < start_day
        ]
        current_values = [val for _, val in current_points]
        summary = _period_summary(current_values, days * 24)
        previous = _period_summary(previous_points, days * 24)
        daily = [
            {
                "day_utc": day.isoformat(),
                "mean": _mean([v for dt, v in current_points if dt.date() == day]),
                "hours": sum(dt.date() == day for dt, _ in current_points),
            }
            for day in current_days
        ]
        previous_daily = [
            {
                "day_utc": day.isoformat(),
                "mean": _mean([v for dt, v in readings.items() if dt.date() == day]),
                "hours": sum(dt.date() == day for dt in readings),
            }
            for day in previous_days
        ]
        comparison_valid = (
            summary["coverage_pct"] >= 50
            and previous["coverage_pct"] >= 50
            and summary["mean"] is not None
            and previous["mean"] is not None
        )
        channels.append({
            "key": key,
            "label": label,
            "unit": unit,
            "summary": summary,
            "previous": previous,
            "comparison_pct": (
                round((summary["mean"] / previous["mean"] - 1) * 100, 1)
                if comparison_valid and previous["mean"] != 0 else None
            ),
            "daily": daily,
            "previous_daily": previous_daily,
            "hourly": [
                {"timestamp_utc": dt.isoformat(), "value": value}
                for dt, value in current_points
            ],
        })

    return {
        "schema_version": "1",
        "timezone": "UTC",
        "days": days,
        "offset_days": offset,
        "start_date_utc": start_day.isoformat(),
        "end_date_utc_exclusive": end_day.isoformat(),
        "previous_start_date_utc": previous_start.isoformat(),
        "latest_available_hour_utc": last_time.isoformat(),
        "disclaimer": (
            "Misure aggregate Alexa; non costituiscono indicazioni di sicurezza "
            "o un indice di rischio sanitario. Le medie di periodi con copertura "
            "insufficiente non sono confrontate."
        ),
        "channels": channels,
    }


def _make_handler(db: Path):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            # Avoid access logs containing paths, queries or headers.
            return

        def _send(self, status: int, content: bytes, mime: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Content-Security-Policy", (
                "default-src 'none'; img-src 'self' data:; "
                "script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
                "connect-src 'self'; font-src 'self'; base-uri 'none'; "
                "form-action 'none'; frame-ancestors 'none'"
            ))
            self.end_headers()
            self.wfile.write(content)

        def do_GET(self) -> None:
            # Prevent DNS rebinding onto a local-only service.
            host = self.headers.get("Host", "").lower().split(":")[0]
            if host not in ("localhost", "127.0.0.1"):
                self._send(403, b"Forbidden", "text/plain; charset=utf-8")
                return
            parsed = urlparse(self.path)
            if parsed.path == "/":
                try:
                    content = STATIC_FILE.read_bytes()
                except OSError:
                    self._send(503, b"Dashboard HTML not installed", "text/plain; charset=utf-8")
                    return
                self._send(200, content, "text/html; charset=utf-8")
                return
            if parsed.path == "/pollution":
                try:
                    content = POLLUTION_FILE.read_bytes()
                except OSError:
                    self._send(503, b"Pollution view not installed", "text/plain; charset=utf-8")
                    return
                self._send(200, content, "text/html; charset=utf-8")
                return
            if parsed.path in ("/api/dashboard", "/api/pollution"):
                try:
                    params = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True)
                    if any(key not in ("days", "offset") or len(val) != 1 for key, val in params.items()):
                        raise DashboardError("Parametri API non validi")
                    days = int(params.get("days", ["7"])[0])
                    offset = int(params.get("offset", ["0"])[0])
                    # Reject unsupported windows immediately, before database
                    # queries or statistical functions. The API always emits a
                    # bounded JSON response for these invalid parameter values.
                    if days not in ALLOWED_DAYS or not 0 <= offset <= MAX_OFFSET:
                        self._send(
                            400,
                            b'{"error":"Periodo o offset non supportato"}',
                            "application/json; charset=utf-8",
                        )
                        return
                    if parsed.path == "/api/dashboard":
                        report = build_dashboard(db, days=days, offset=offset)
                    else:
                        from .pollution_analysis import build_pollution
                        report = build_pollution(db, days=days, offset=offset)
                    response = json.dumps(report, ensure_ascii=False, allow_nan=False).encode("utf-8")
                    self._send(200, response, "application/json; charset=utf-8")
                except (ValueError, DashboardError) as exc:
                    # Our own fixed safe messages only, not raw SQLite errors.
                    msg = str(exc) if isinstance(exc, DashboardError) else "Parametri numerici non validi"
                    self._send(400, json.dumps({"error": msg}).encode("utf-8"), "application/json; charset=utf-8")
                return
            self._send(404, b"Not found", "text/plain; charset=utf-8")

    return Handler


def main() -> int:
    parser = argparse.ArgumentParser(description="Dashboard aria Alexa, solo localhost e SQLite lettura")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--host", choices=("127.0.0.1",), default="127.0.0.1")
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("La porta deve essere tra 1024 e 65535")
    with ThreadingHTTPServer((args.host, args.port), _make_handler(args.db.expanduser())) as server:
        print(f"Alexa dashboard pronta su http://127.0.0.1:{args.port} (solo locale)", flush=True)
        try:
            server.serve_forever(poll_interval=0.5)
        except KeyboardInterrupt:
            return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
