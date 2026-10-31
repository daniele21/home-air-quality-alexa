"""Local, privacy-minded collector for Amazon Air Quality Monitor readings.

The Amazon API is private/unsupported. This module deliberately uses a
version-pinned community library rather than depending on undocumented URLs.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
from datetime import datetime, timezone
import getpass
import json
import logging
import os
from pathlib import Path
import signal
import sqlite3
import sys
from typing import Any

LOG = logging.getLogger("home_air_quality_alexa")
SENSOR_UNITS = {
    "Humidity": "%", "PM10": "µg/m³", "PM25": "µg/m³",
    "CO": "ppm", "VOC": "index", "Air Quality": "index",
}
AIR_SENSOR_KEYS = set(SENSOR_UNITS)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def secure_directory(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError(f"Refusing symlink directory: {path}")
    os.chmod(path, 0o700)


def save_private_json(path: Path, data: dict[str, Any]) -> None:
    secure_directory(path.parent)
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        os.chmod(path, 0o600)
    finally:
        tmp.unlink(missing_ok=True)


def load_private_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    if path.is_symlink() or path.stat().st_mode & 0o077:
        raise PermissionError(f"Insecure session file permissions: {path}; run chmod 600")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Invalid stored Amazon login data")
    return data


def open_database(path: Path) -> sqlite3.Connection:
    secure_directory(path.parent)
    conn = sqlite3.connect(path)
    os.chmod(path, 0o600)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS readings (
            device_serial TEXT NOT NULL,
            device_name TEXT NOT NULL,
            sensor_key TEXT NOT NULL,
            sampled_at_utc TEXT NOT NULL,
            observed_at_utc TEXT NOT NULL,
            timestamp_source TEXT NOT NULL,
            numeric_value REAL,
            text_value TEXT,
            unit TEXT,
            PRIMARY KEY (device_serial, sensor_key, sampled_at_utc)
        );
        CREATE INDEX IF NOT EXISTS idx_readings_observed ON readings(observed_at_utc);
        CREATE TABLE IF NOT EXISTS collection_events (
            observed_at_utc TEXT NOT NULL,
            status TEXT NOT NULL,
            inserted_count INTEGER NOT NULL,
            error_type TEXT
        );
    """)
    return conn


def is_air_monitor(device: Any, sensors: dict[str, Any]) -> bool:
    # Require an AQM-specific sensor; generic temperature must not match an Echo.
    return bool(set(sensors) & AIR_SENSOR_KEYS)


def selected_devices(devices: dict[str, Any], sensors: dict[str, dict[str, Any]], serial: str | None):
    for device in devices.values():
        if serial and device.serial_number != serial:
            continue
        vals = sensors.get(device.serial_number, {})
        if is_air_monitor(device, vals):
            yield device, vals


def insert_readings(conn: sqlite3.Connection, selected: list[tuple[Any, dict[str, Any]]], observed_at: str) -> int:
    count = 0
    with conn:
        for device, sensor_map in selected:
            for key, item in sensor_map.items():
                if key not in AIR_SENSOR_KEYS and key != "temperature":
                    continue
                if item.error or item.value is None:
                    continue
                sample_time = item.time_of_sample
                if sample_time is not None:
                    if sample_time.tzinfo is None:
                        # Do not claim an unknown timezone is UTC.
                        LOG.warning("Skipping %s reading with naive sample timestamp", key)
                        continue
                    sampled_at = sample_time.astimezone(timezone.utc).isoformat(timespec="microseconds")
                    timestamp_source = "device"
                else:
                    sampled_at = observed_at
                    timestamp_source = "poll"
                try:
                    numeric = float(item.value)
                except (TypeError, ValueError):
                    numeric = None
                unit = (item.scale if key == "temperature" else SENSOR_UNITS.get(key))
                before = conn.total_changes
                conn.execute("""
                    INSERT OR IGNORE INTO readings
                        (device_serial,device_name,sensor_key,sampled_at_utc,observed_at_utc,
                         timestamp_source,numeric_value,text_value,unit)
                    VALUES (?,?,?,?,?,?,?,?,?)
                """, (device.serial_number, device.account_name, key, sampled_at,
                      observed_at, timestamp_source, numeric, str(item.value), unit))
                count += conn.total_changes - before
    return count


def record_event(conn: sqlite3.Connection, status: str, count: int = 0, error_type: str | None = None) -> None:
    with conn:
        conn.execute("INSERT INTO collection_events VALUES (?,?,?,?)", (utc_now(), status, count, error_type))


def export_csv(conn: sqlite3.Connection, output: Path) -> int:
    if output.is_symlink():
        raise ValueError("Refusing symlink CSV output")
    output.parent.mkdir(parents=True, exist_ok=True)
    rows = conn.execute("""SELECT device_serial,device_name,sensor_key,sampled_at_utc,
        observed_at_utc,timestamp_source,numeric_value,text_value,unit
        FROM readings ORDER BY sampled_at_utc,device_serial,sensor_key""")
    with output.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["device_serial", "device_name", "sensor_key", "sampled_at_utc",
                         "observed_at_utc", "timestamp_source", "numeric_value", "text_value", "unit"])
        count = 0
        for row in rows:
            writer.writerow(row)
            count += 1
    return count


async def amazon_command(args: argparse.Namespace) -> int:
    # Import only when an Alexa command is used; offline unit tests need no credentials.
    import httpx
    from aiohttp import ClientSession
    from aioamazondevices.api import AmazonEchoApi
    from aioamazondevices.structures import AmazonSaveDataConfig
    from aioamazondevices.exceptions import CannotAuthenticate

    session_file = args.session_file.expanduser()
    stored = load_private_json(session_file)
    if args.command != "login" and not stored:
        LOG.error("Missing login session: first run 'air-quality-alexa login --email YOUR_EMAIL'")
        return 2
    email = args.email or os.getenv("ALEXA_EMAIL")
    if not email:
        LOG.error("Provide --email or set ALEXA_EMAIL")
        return 2
    if args.command == "login" and stored:
        LOG.info("Replacing existing session using interactive login")
    password = ""
    if args.command == "login":
        password = os.getenv("ALEXA_PASSWORD") or getpass.getpass("Amazon password (not stored): ")
        otp = getpass.getpass("Authenticator OTP (not stored): ")
    else:
        otp = ""
    secure_directory(session_file.parent)
    async with ClientSession() as web_session, httpx.AsyncClient(http2=True, timeout=None) as http2:
        api = AmazonEchoApi(
            client_session=web_session, login_email=email, login_password=password,
            login_data=None if args.command == "login" else stored,
            save_data=AmazonSaveDataConfig(path=session_file.parent),
        )
        task = None
        try:
            if args.command == "login":
                login_state = await api.login.login_mode_interactive(otp)
                save_private_json(session_file, login_state)
                print("Session saved locally with permissions 0600. Password and OTP not saved.")
                return 0
            login_state = await api.login.login_mode_stored_data()
            devices = await api.get_devices_data()
            if args.command == "devices":
                for device in devices.values():
                    keys = sorted(api.sensors.get(device.serial_number, {}))
                    print(json.dumps({"name": device.account_name, "serial": device.serial_number,
                                      "online": device.online, "sensor_keys": keys}, ensure_ascii=False))
                return 0
            db = open_database(args.db.expanduser())
            stop = asyncio.Event()
            reauth = asyncio.Event()
            loop = asyncio.get_running_loop()
            for signum in (signal.SIGINT, signal.SIGTERM):
                try:
                    loop.add_signal_handler(signum, stop.set)
                except NotImplementedError:
                    pass

            async def reauth_needed() -> None:
                reauth.set()

            try:
                if not args.once:
                    task = await api.start_http2_processing(http2, on_reauth_required=reauth_needed)
                delay = args.interval
                while not stop.is_set():
                    if reauth.is_set():
                        LOG.error("Amazon session requires reauthentication: run login again")
                        record_event(db, "auth_required")
                        return 3
                    if task is not None and task.done():
                        LOG.error("Alexa HTTP2 stream stopped; refusing to store potentially stale data")
                        record_event(db, "push_stream_stopped")
                        return 4
                    try:
                        # First iteration can reuse the initial snapshot.
                        batch = list(selected_devices(devices, api.sensors, args.device))
                        if not batch:
                            LOG.error("No AQM sensors found (device may not be exposed by Alexa)")
                            record_event(db, "monitor_not_found")
                            return 5
                        now = utc_now()
                        count = insert_readings(db, batch, now)
                        record_event(db, "ok", count)
                        print(f"{now} devices={len(batch)} new_readings={count}", flush=True)
                        delay = args.interval
                    except Exception as error:
                        # Never log exception text: library errors may contain responses/tokens.
                        name = type(error).__name__
                        LOG.error("Collection failed (%s), retrying in %ss", name, delay)
                        record_event(db, "failed", error_type=name)
                        if isinstance(error, CannotAuthenticate):
                            return 3
                        delay = min(max(delay * 2, args.interval), 1800)
                    if args.once:
                        return 0
                    try:
                        await asyncio.wait_for(stop.wait(), timeout=delay)
                    except TimeoutError:
                        pass
                    if not stop.is_set():
                        try:
                            await api.login.login_mode_stored_data()
                            devices = await api.get_devices_data()
                            # Access only the public login return object for persistence.
                            if login_state != stored:
                                save_private_json(session_file, login_state)
                                stored = json.loads(json.dumps(login_state))
                        except CannotAuthenticate:
                            record_event(db, "auth_required")
                            LOG.error("Amazon session expired; run login again")
                            return 3
                        except Exception as error:
                            LOG.error("Poll failed (%s)", type(error).__name__)
                            record_event(db, "poll_failed", error_type=type(error).__name__)
                            await asyncio.sleep(min(delay, 60))
                return 0
            finally:
                db.close()
        finally:
            if task is not None:
                await api.stop_http2_processing()


def main() -> int:
    default_config = Path.home() / ".config/home-air-quality-alexa/amazon-login.json"
    default_db = Path.home() / ".local/share/home-air-quality-alexa/readings.sqlite3"
    parser = argparse.ArgumentParser(description="Amazon Air Quality Monitor local collector")
    parser.add_argument("--email", help="Amazon login email (or ALEXA_EMAIL)")
    parser.add_argument("--session-file", type=Path, default=default_config)
    parser.add_argument("--db", type=Path, default=default_db)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("login", help="Interactive OTP login, saves local session")
    sub.add_parser("devices", help="List devices and reported sensors")
    collector = sub.add_parser("collect", help="Collect measurements")
    collector.add_argument("--interval", type=int, default=300, help="Poll interval, at least 300s")
    collector.add_argument("--device", help="Limit to a device serial shown by 'devices'")
    collector.add_argument("--once", action="store_true", help="One-time snapshot (no HTTP2 updates)")
    exporter = sub.add_parser("export", help="Export SQLite history to CSV")
    exporter.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if args.command == "export":
        if not args.db.expanduser().is_file():
            parser.error("Database not found; collect readings first")
        with sqlite3.connect(args.db.expanduser()) as conn:
            count = export_csv(conn, args.output.expanduser())
        print(f"Exported {count} readings to {args.output}")
        return 0
    if args.command == "collect" and args.interval < 300:
        parser.error("--interval must be >= 300 seconds")
    try:
        return asyncio.run(amazon_command(args))
    except KeyboardInterrupt:
        return 130
    except Exception as error:
        LOG.error("Command failed (%s); see README troubleshooting", type(error).__name__)
        return 1


if __name__ == "__main__":
    sys.exit(main())
