"""Resumable, rate-limited backfill of Amazon Air Quality Monitor history.

The batch runner calls the existing, validated history-fetch path once per
device/sensor/UTC date window. Nothing runs unless the user passes --apply.

- Never prints credentials or opaque Amazon endpoint IDs
- Preserves the initial rolling-window samples; provides a separate UTC-aligned view
- Checkpoints each successful or empty window for safe restarts
- Stops on a failed or ambiguous response, without looping on private APIs
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import json
import sqlite3
from types import SimpleNamespace
from typing import Any

from .history_fetch import (
    AQM_RANGE_INSTANCES,
    HistoryFetchError,
    RESOLUTIONS,
    history_period,
    run_history_fetch,
)

# Live Alexa GraphQL on 2026-11-01 returned valid history for these five
# monitor channels. PM10 is exposed in library metadata but its historical
# query was rejected; keep PM10 available only through the manual probe path.
SENSOR_ORDER = ("PM25", "VOC", "CO", "Humidity", "Air Quality")
MAX_BATCH_DAYS = 7
MAX_BACKFILL_DAYS = 366
MAX_REQUESTS = 365 * len(SENSOR_ORDER)
MIN_REQUEST_DELAY = 3.0


@dataclass(frozen=True)
class HistoryTask:
    sensor: str
    start: str
    end: str


def utc_date(value: str) -> date:
    try:
        parsed = date.fromisoformat(value)
        if len(value) != 10 or parsed.isoformat() != value:
            raise ValueError("noncanonical ISO date")
        return parsed
    except (TypeError, ValueError) as exc:
        raise HistoryFetchError("Backfill dates must be YYYY-MM-DD (UTC)") from exc


def resolve_bounds(
    start: str | None,
    end: str | None,
    *,
    days: int,
    now: datetime | None = None,
) -> tuple[date, date]:
    if (start is None) != (end is None):
        raise HistoryFetchError("Provide both --start and --end, or neither")
    if start is None:
        if not isinstance(days, int) or isinstance(days, bool) or not 1 <= days <= MAX_BACKFILL_DAYS:
            raise HistoryFetchError("--days must be between 1 and 366")
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None:
            raise HistoryFetchError("Current time must be timezone-aware")
        end_date = now.astimezone(timezone.utc).date()
        start_date = end_date - timedelta(days=days)
    else:
        start_date, end_date = utc_date(start), utc_date(end)
    number = (end_date - start_date).days
    if not 1 <= number <= MAX_BACKFILL_DAYS:
        raise HistoryFetchError("Backfill duration must be 1 to 366 days (end exclusive)")
    if end_date > datetime.now(timezone.utc).date() + timedelta(days=1):
        raise HistoryFetchError("Do not request future history dates")
    return start_date, end_date


def plan_tasks(start: date, end: date, sensors: tuple[str, ...], *, chunk_days: int) -> list[HistoryTask]:
    if not isinstance(chunk_days, int) or isinstance(chunk_days, bool) or not 1 <= chunk_days <= MAX_BATCH_DAYS:
        raise HistoryFetchError("--chunk-days must be between 1 and 7")
    if not sensors or any(sensor not in SENSOR_ORDER for sensor in sensors):
        raise HistoryFetchError("Select at least one supported air-quality sensor")
    if len(sensors) != len(set(sensors)):
        raise HistoryFetchError("Duplicate sensors are not allowed")
    out: list[HistoryTask] = []
    # Dates first: batch fetches advance in chronological order and are easy
    # to inspect/restart. One date x six sensors by default.
    cursor = start
    while cursor < end:
        next_date = min(cursor + timedelta(days=chunk_days), end)
        for sensor in sensors:
            out.append(HistoryTask(sensor, cursor.isoformat(), next_date.isoformat()))
        cursor = next_date
    if len(out) > MAX_REQUESTS:
        raise HistoryFetchError("Too many tasks; reduce the dates or sensor selection")
    return out


def safe_failure_code(exc: Exception) -> str:
    """Describe a failure using only vetted, non-secret error classes.

    Never serialize exception messages or provider response bodies to CI.
    """
    if not isinstance(exc, HistoryFetchError):
        return "unexpected_exception"
    msg = str(exc)
    if msg == "Alexa GraphQL returned errors (details withheld for privacy)":
        return "graphql_rejected"
    if msg.startswith("Alexa history request failed ("):
        return "request_failed"
    if msg == "Alexa did not return an airQualityHistory object":
        return "no_history_object"
    if msg == "Alexa returned a different aggregation resolution":
        return "resolution_mismatch"
    if msg == "Alexa history returned no sensor interface":
        return "missing_interface"
    if msg == "Alexa history returned no data array":
        return "missing_data_array"
    if msg.startswith("No monitor exposes "):
        return "sensor_not_discovered"
    if msg in ("Missing Alexa sensor interface name", "Missing Alexa endpoint ID"):
        return "missing_sensor_identity"
    if msg == "Unexpected Alexa history response":
        return "unexpected_response"
    return "other_history_error"


def ensure_backfill_schema(conn: sqlite3.Connection) -> None:
    with conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS historical_backfill_windows (
                device_serial TEXT NOT NULL,
                sensor_key TEXT NOT NULL,
                resolution TEXT NOT NULL,
                start_date_utc TEXT NOT NULL,
                end_date_utc TEXT NOT NULL,
                status TEXT NOT NULL,
                valid_points INTEGER NOT NULL,
                missing_buckets INTEGER NOT NULL,
                outside_window_buckets INTEGER NOT NULL,
                invalid_buckets INTEGER NOT NULL,
                new_saved INTEGER NOT NULL,
                checked_at_utc TEXT NOT NULL,
                error_type TEXT,
                PRIMARY KEY (
                    device_serial, sensor_key, resolution,
                    start_date_utc, end_date_utc
                )
            );
            -- Keep the legacy :47 rolling-window buckets in the original
            -- historical_readings table, but expose strictly hour-aligned
            -- history separately to avoid double-counting overlapping periods.
            CREATE VIEW IF NOT EXISTS historical_hourly_utc AS
                SELECT device_serial, sensor_key, resolution,
                       bucket_timestamp_utc, numeric_value, unit,
                       endpoint_count, fetched_at_utc
                FROM historical_readings
                WHERE resolution = 'ONE_HOUR'
                  AND substr(bucket_timestamp_utc, 15, 2) = '00'
                  AND substr(bucket_timestamp_utc, 18, 2) = '00';
        """)


def completed_windows(conn: sqlite3.Connection, serial: str, resolution: str) -> set[tuple[str, str, str]]:
    return {
        (sensor, start, end)
        for sensor, start, end in conn.execute("""
            SELECT sensor_key, start_date_utc, end_date_utc
            FROM historical_backfill_windows
            WHERE device_serial = ? AND resolution = ?
              AND status IN ('ok', 'no_data')
        """, (serial, resolution))
    }


def record_window(
    conn: sqlite3.Connection, *,
    serial: str,
    resolution: str,
    task: HistoryTask,
    result: dict[str, Any],
    error_type: str | None = None,
) -> None:
    status = result.get("status")
    if status not in ("ok", "no_data", "identity_mismatch", "failed"):
        raise HistoryFetchError("Unexpected batch result status")
    with conn:
        conn.execute("""
            INSERT INTO historical_backfill_windows
                (device_serial, sensor_key, resolution, start_date_utc, end_date_utc,
                 status, valid_points, missing_buckets, outside_window_buckets,
                 invalid_buckets, new_saved, checked_at_utc, error_type)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(device_serial, sensor_key, resolution,
                        start_date_utc, end_date_utc)
            DO UPDATE SET
                status=excluded.status,
                valid_points=excluded.valid_points,
                missing_buckets=excluded.missing_buckets,
                outside_window_buckets=excluded.outside_window_buckets,
                invalid_buckets=excluded.invalid_buckets,
                new_saved=excluded.new_saved,
                checked_at_utc=excluded.checked_at_utc,
                error_type=excluded.error_type
        """, (
            serial, task.sensor, resolution, task.start, task.end, status,
            int(result.get("valid_points", 0)),
            int(result.get("missing_buckets", 0)),
            int(result.get("outside_window_buckets", 0)),
            int(result.get("invalid_buckets", 0)),
            int(result.get("new_saved", 0)),
            datetime.now(timezone.utc).isoformat(),
            error_type,
        ))


def select_monitor(api: Any, devices: dict[str, Any], serial: str | None) -> tuple[Any, dict[str, Any]]:
    known = set(AQM_RANGE_INSTANCES)
    candidates = []
    for device in devices.values():
        if serial is not None and device.serial_number != serial:
            continue
        sensors = api.sensors.get(device.serial_number, {})
        if getattr(device, "endpoint_id", None) and known.intersection(sensors):
            candidates.append((device, sensors))
    if not candidates:
        raise HistoryFetchError("No air-quality monitor found")
    if len(candidates) != 1:
        raise HistoryFetchError("Multiple monitors found; specify --device SERIAL")
    return candidates[0]


def available_sensors(sensor_map: dict[str, Any], requested: list[str] | None) -> tuple[tuple[str, ...], tuple[str, ...]]:
    requested = list(SENSOR_ORDER) if requested is None else requested
    if not requested or len(set(requested)) != len(requested):
        raise HistoryFetchError("Sensor selection is empty or contains duplicates")
    if any(sensor not in SENSOR_ORDER for sensor in requested):
        raise HistoryFetchError("Unknown sensor for AQM backfill")
    available = tuple(sensor for sensor in requested if sensor in sensor_map)
    missing = tuple(sensor for sensor in requested if sensor not in sensor_map)
    if not available:
        raise HistoryFetchError("None of the requested AQM sensors is present on the monitor")
    return available, missing


async def run_backfill(
    api: Any,
    devices: dict[str, Any],
    args: Any,
    conn: sqlite3.Connection | None,
) -> dict[str, Any]:
    if args.resolution not in RESOLUTIONS:
        raise HistoryFetchError("Unsupported aggregation resolution")
    if not MIN_REQUEST_DELAY <= args.delay <= 120:
        raise HistoryFetchError("--delay must be between 3 and 120 seconds")
    if not 1 <= args.max_requests <= MAX_REQUESTS:
        raise HistoryFetchError("--max-requests is out of bounds")
    start, end = resolve_bounds(args.start, args.end, days=args.days)
    device, sensor_map = select_monitor(api, devices, args.device)
    sensor_names, unavailable = available_sensors(sensor_map, args.sensor)
    tasks = plan_tasks(start, end, sensor_names, chunk_days=args.chunk_days)
    apply = bool(args.apply)
    plan = {
        "status": "plan" if not apply else "running",
        "device_name": device.account_name,
        "date_start_utc": start.isoformat(),
        "date_end_utc_exclusive": end.isoformat(),
        "resolution": args.resolution,
        "sensors": list(sensor_names),
        "unavailable_sensors": list(unavailable),
        "chunk_days": args.chunk_days,
        "planned_requests": len(tasks),
        "rate_limit_seconds_between_requests": args.delay,
        "max_requests_this_run": args.max_requests,
        "apply": apply,
    }
    if not apply:
        plan["note"] = "DRY RUN: no history API calls, SQLite reads or writes. Add --apply to start."
        return plan
    if conn is None:
        raise HistoryFetchError("A database connection is required with --apply")

    ensure_backfill_schema(conn)
    # Completed requests are only skipped if they exactly match the sensor,
    # UTC window, resolution and physical monitor. An incomplete window is
    # reattempted on the next invocation.
    already = completed_windows(conn, device.serial_number, args.resolution)
    pending = [task for task in tasks
               if (task.sensor, task.start, task.end) not in already or args.force]
    progress = dict(plan)
    progress.update({
        "status": "ok", "pending_before_limit": len(pending),
        "previously_completed": len(tasks) - len(pending),
        "requests_sent": 0, "windows_with_data": 0,
        "empty_windows": 0, "saved_new_rows": 0,
        "valid_points_received": 0,
        "missing_buckets": 0, "invalid_buckets": 0,
        "outside_window_buckets": 0,
        "failed_window": None, "error_class": None, "error_code": None,
    })

    for i, task in enumerate(pending[:args.max_requests]):
        # Avoid burst traffic through Alexa's undocumented private API.
        if i:
            await asyncio.sleep(args.delay)
        item_args = SimpleNamespace(
            device=device.serial_number,
            sensor=task.sensor,
            resolution=args.resolution,
            days=1,
            start=task.start,
            end=task.end,
            probe_only=False,
        )
        try:
            result = await run_history_fetch(api, devices, item_args, conn)
        except (Exception) as exc:
            # No exception message: a library error might contain account
            # tokens or a full HTTP body. Store only its class name.
            record_window(conn, serial=device.serial_number, resolution=args.resolution,
                          task=task, result={"status": "failed"}, error_type=type(exc).__name__)
            progress["requests_sent"] += 1
            progress["status"] = "stopped_on_error"
            progress["failed_window"] = {"sensor": task.sensor, "start": task.start, "end": task.end}
            progress["error_class"] = type(exc).__name__
            progress["error_code"] = safe_failure_code(exc)
            break

        progress["requests_sent"] += 1
        progress["valid_points_received"] += result.get("valid_points", 0)
        progress["saved_new_rows"] += result.get("new_saved", 0)
        progress["missing_buckets"] += result.get("missing_buckets", 0)
        progress["invalid_buckets"] += result.get("invalid_buckets", 0)
        progress["outside_window_buckets"] += result.get("outside_window_buckets", 0)
        record_window(conn, serial=device.serial_number, resolution=args.resolution,
                      task=task, result=result)
        if result["status"] == "ok":
            progress["windows_with_data"] += 1
        elif result["status"] == "no_data":
            progress["empty_windows"] += 1
        else:
            progress["status"] = "stopped_on_identity_mismatch"
            progress["failed_window"] = {"sensor": task.sensor, "start": task.start, "end": task.end}
            break
        # Print one small progress line per API call, never entire responses.
        print(json.dumps({
            "sensor": task.sensor, "start": task.start, "end": task.end,
            "status": result["status"], "valid": result["valid_points"],
            "new_saved": result["new_saved"],
        }, ensure_ascii=False), flush=True)

    progress["remaining_requests"] = len(pending) - progress["requests_sent"]
    if progress["status"] == "ok" and progress["remaining_requests"]:
        progress["status"] = "partial_limit_reached"
    progress["note"] = (
        "Legacy :47-hourly rows are preserved. For dashboards, query the "
        "historical_hourly_utc view to exclude non-aligned legacy samples."
    )
    return progress
