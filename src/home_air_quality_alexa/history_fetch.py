"""Read-only Alexa air quality history request, with separate SQLite storage.

One known GraphQL operation (airQualityHistory) per invocation. The query
and input types are grounded in Alexa's introspected GraphQL schema, independently
published by alexactl and go-alexa. This is an undocumented/unsupported API.

No secret-bearing response is logged or persisted. Existing collector readings
are never overwritten.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from http import HTTPMethod
import math
import sqlite3
from typing import Any

QUERY = """
query AirQualityLocalHistory($input: AirQualityHistoryInput!) {
  airQualityHistory(airQualityHistoryInput: $input) {
    resolution
    interfaceName
    instance
    averageValue
    data {
      endpointIds
      timestamp
      value
    }
  }
}
"""
RESOLUTIONS = ("FIVE_MINS", "ONE_HOUR", "ONE_DAY")
SENSORS = ("PM25", "PM10", "VOC", "CO", "Humidity", "Air Quality", "temperature")
MAX_DAYS = 31

# aioamazondevices 17.2.3 represents the AQM sensor instances using the
# local feature name 'range', while the Alexa GraphQL history API returned
# 'Alexa.RangeController'. The instance numbers (4..9) identify the actual
# sensor. Keep this mapping narrow and explicit: no fuzzy prefix matching.
AQM_RANGE_INSTANCES = {
    "Humidity": "4",
    "VOC": "5",
    "PM25": "6",
    "PM10": "7",
    "CO": "8",
    "Air Quality": "9",
}
RANGE_HISTORY_INTERFACE = "Alexa.RangeController"


def history_identity_matches(sensor_key: str, sensor: Any, returned_interface: Any, returned_instance: Any) -> bool:
    """Require both the reported interface and the AQM instance to match.

    The history API canonicalizes the feature 'range' to
    'Alexa.RangeController'; only this proven alias is accepted.
    """
    feature = getattr(sensor, "feature_name", None)
    instance = getattr(sensor, "instance", None) or None
    returned_instance = returned_instance or None
    if not isinstance(feature, str) or not isinstance(returned_interface, str):
        return False
    if instance != returned_instance:
        return False
    if returned_interface == feature:
        # Exact matching is safe only when the sensor's own identity is
        # internally consistent with the AQM pinned library mapping.
        return feature != "range" or AQM_RANGE_INSTANCES.get(sensor_key) == instance
    return (
        feature == "range"
        and returned_interface == RANGE_HISTORY_INTERFACE
        and AQM_RANGE_INSTANCES.get(sensor_key) == instance
    )


class HistoryFetchError(Exception):
    """A safe human-readable error that contains no Amazon response details."""


def safe_identifier(value: Any) -> str | None:
    """Expose only short schema-like names, never opaque or account-like values."""
    if value is None or value == "":
        return None
    if not isinstance(value, str) or len(value) > 80:
        return "<unrecognized>"
    if not all(char.isalnum() or char in "._:/-" for char in value):
        return "<unrecognized>"
    return value


def history_period(days: int, *, now: datetime | None = None) -> tuple[datetime, datetime]:
    if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= MAX_DAYS:
        raise HistoryFetchError("Choose between 1 and 31 days per request")
    end = now or datetime.now(timezone.utc)
    if end.tzinfo is None:
        raise HistoryFetchError("Current time must have a timezone")
    end = end.astimezone(timezone.utc)
    return end - timedelta(days=days), end


def choose_device_sensor(
    devices: dict[str, Any],
    sensors: dict[str, dict[str, Any]],
    *,
    serial: str | None,
    key: str,
) -> tuple[Any, Any]:
    if key not in SENSORS:
        raise HistoryFetchError("Unsupported sensor selection")
    candidates = [
        (device, sensors.get(device.serial_number, {}).get(key))
        for device in devices.values()
        if serial is None or device.serial_number == serial
    ]
    candidates = [
        (dev, sensor) for dev, sensor in candidates
        if sensor is not None and getattr(dev, "endpoint_id", None)
        and isinstance(getattr(sensor, "feature_name", None), str)
    ]
    if not candidates:
        raise HistoryFetchError(f"No monitor exposes {key}; verify 'devices' and the device serial")
    if len(candidates) != 1:
        raise HistoryFetchError("Multiple monitors found. Repeat with --device SERIAL")
    return candidates[0]


def build_request(
    device: Any,
    sensor: Any,
    start: datetime,
    end: datetime,
    resolution: str,
) -> dict[str, Any]:
    if resolution not in RESOLUTIONS:
        raise HistoryFetchError("Unsupported aggregation resolution")
    if not start.tzinfo or not end.tzinfo or not start < end:
        raise HistoryFetchError("Invalid timezone-aware time interval")
    if end - start > timedelta(days=MAX_DAYS):
        raise HistoryFetchError("Maximum one-month window per request")
    if not isinstance(device.endpoint_id, str) or not device.endpoint_id:
        raise HistoryFetchError("Missing Alexa endpoint ID")
    feature = getattr(sensor, "feature_name", "")
    instance = getattr(sensor, "instance", None)
    if not isinstance(feature, str) or not feature:
        raise HistoryFetchError("Missing Alexa sensor interface name")
    endpoint = {
        "endpointId": device.endpoint_id,
        "interfaceName": feature,
    }
    if isinstance(instance, str) and instance:
        endpoint["instance"] = instance
    return {
        "operationName": "AirQualityLocalHistory",
        "query": QUERY,
        "variables": {
            "input": {
                "devices": [endpoint],
                "startDate": start.astimezone(timezone.utc).isoformat(),
                "endDate": end.astimezone(timezone.utc).isoformat(),
                "aggregationResolution": resolution,
            }
        },
    }


def _timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        return None
    return dt.astimezone(timezone.utc)


def parse_history(
    payload: dict[str, Any],
    *,
    endpoint_id: str,
    start: datetime,
    end: datetime,
    resolution: str,
) -> tuple[dict[str, Any], list[tuple[str, float, int]]]:
    # A partial GraphQL response may contain both errors and data. Reject it:
    # mixing partial failures into backfill would silently hide gaps.
    if payload.get("errors"):
        raise HistoryFetchError("Alexa GraphQL returned errors (details withheld for privacy)")
    data = payload.get("data")
    history = data.get("airQualityHistory") if isinstance(data, dict) else None
    if not isinstance(history, dict):
        raise HistoryFetchError("Alexa did not return an airQualityHistory object")
    if history.get("resolution") != resolution:
        raise HistoryFetchError("Alexa returned a different aggregation resolution")
    interface_name = history.get("interfaceName")
    instance = history.get("instance")
    if not isinstance(interface_name, str):
        raise HistoryFetchError("Alexa history returned no sensor interface")
    rows = history.get("data")
    if not isinstance(rows, list):
        raise HistoryFetchError("Alexa history returned no data array")
    accepted = []
    invalid = 0
    missing = 0
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            invalid += 1
            continue
        endpoint_ids = row.get("endpointIds")
        dt = _timestamp(row.get("timestamp"))
        value = row.get("value")
        if not isinstance(endpoint_ids, list):
            invalid += 1
            continue
        if not endpoint_ids:
            missing += 1
            continue
        # A single endpoint is requested. Never store a value from another device.
        if endpoint_id not in endpoint_ids:
            invalid += 1
            continue
        if not dt or dt < start - timedelta(days=2) or dt > end + timedelta(days=2):
            invalid += 1
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            invalid += 1
            continue
        bucket = dt.isoformat(timespec="microseconds")
        if bucket in seen:
            continue
        seen.add(bucket)
        accepted.append((bucket, float(value), len(endpoint_ids)))
    meta = {
        "points_reported": len(rows),
        "valid_points": len(accepted),
        "missing_buckets": missing,
        "invalid_buckets": invalid,
        "resolution": history["resolution"],
        "interface": interface_name,
        "instance": instance if isinstance(instance, str) else None,
        "average_value": history.get("averageValue") if (
            isinstance(history.get("averageValue"), (float, int))
            and not isinstance(history.get("averageValue"), bool)
            and math.isfinite(history.get("averageValue"))
        ) else None,
    }
    return meta, accepted


def store_history(
    conn: sqlite3.Connection,
    *,
    serial: str,
    sensor_key: str,
    resolution: str,
    points: list[tuple[str, float, int]],
    fetched_at: str,
    unit: str | None = None,
) -> int:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS historical_readings (
          device_serial TEXT NOT NULL,
          sensor_key TEXT NOT NULL,
          resolution TEXT NOT NULL,
          bucket_timestamp_utc TEXT NOT NULL,
          numeric_value REAL NOT NULL,
          unit TEXT,
          endpoint_count INTEGER NOT NULL,
          fetched_at_utc TEXT NOT NULL,
          PRIMARY KEY (device_serial, sensor_key, resolution, bucket_timestamp_utc)
        )
    """)
    with conn:
        before = conn.total_changes
        conn.executemany("""
            INSERT OR IGNORE INTO historical_readings
            (device_serial,sensor_key,resolution,bucket_timestamp_utc,
             numeric_value,unit,endpoint_count,fetched_at_utc)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, [(serial, sensor_key, resolution, ts, value, unit, count, fetched_at)
              for ts, value, count in points])
        return conn.total_changes - before


async def fetch_history(api: Any, request: dict[str, Any]) -> dict[str, Any]:
    from aioamazondevices.const.http import REQUEST_AGENT, URI_NEXUS_GRAPHQL
    from yarl import URL
    try:
        _, resp = await api._http_wrapper.session_request(
            method=HTTPMethod.POST,
            url=URL.joinpath(api._session_state_data.alexa_website_url, URI_NEXUS_GRAPHQL),
            input_data=request,
            json_data=True,
            extended_headers={"User-Agent": REQUEST_AGENT["Amazon"]},
            fail_fast=True,
        )
        response = await api._http_wrapper.response_to_json(resp, "")
        if not isinstance(response, dict):
            raise HistoryFetchError("Unexpected Alexa history response")
        return response
    except HistoryFetchError:
        raise
    except Exception as exc:
        raise HistoryFetchError(
            f"Alexa history request failed ({type(exc).__name__}); response details not logged"
        ) from exc


async def run_history_fetch(api: Any, devices: dict[str, Any], args: Any, db: sqlite3.Connection) -> dict[str, Any]:
    device, sensor = choose_device_sensor(
        devices, api.sensors, serial=args.device, key=args.sensor
    )
    start, end = history_period(args.days)
    request = build_request(device, sensor, start, end, args.resolution)
    response = await fetch_history(api, request)
    meta, points = parse_history(
        response,
        endpoint_id=device.endpoint_id,
        start=start,
        end=end,
        resolution=args.resolution,
    )
    # Alexa's GraphQL history response may use a different interface/instance
    # naming convention than the generic device features used by aioamazondevices.
    # Do NOT assume the response belongs to PM25 merely because it was requested.
    # Report sanitized metadata and counts, never device IDs, sample values,
    # raw GraphQL responses, or token-bearing error messages.
    expected_instance = getattr(sensor, "instance", None) or None
    returned_instance = meta["instance"] or None
    if not history_identity_matches(args.sensor, sensor, meta["interface"], returned_instance):
        return {
            "status": "identity_mismatch",
            "sensor_requested": args.sensor,
            "requested": {
                "interface": safe_identifier(sensor.feature_name),
                "instance": safe_identifier(expected_instance),
            },
            "returned": {
                "interface": safe_identifier(meta["interface"]),
                "instance": safe_identifier(returned_instance),
            },
            "points_reported": meta["points_reported"],
            "points_for_selected_endpoint": meta["valid_points"],
            "missing_buckets": meta["missing_buckets"],
            "invalid_buckets": meta["invalid_buckets"],
            "new_saved": 0,
            "note": (
                "Amazon returned history data, but sensor identity is not verified. "
                "Nothing was saved. Use these metadata fields to establish a safe mapping."
            ),
        }
    from .collector import SENSOR_UNITS, utc_now
    unit = SENSOR_UNITS.get(args.sensor) or getattr(sensor, "scale", None)
    count = store_history(
        db,
        serial=device.serial_number,
        sensor_key=args.sensor,
        resolution=args.resolution,
        points=points,
        fetched_at=utc_now(),
        unit=unit,
    )
    return {
        "status": "ok",
        "device_name": device.account_name,
        "sensor": args.sensor,
        "requested_days": args.days,
        "resolution": args.resolution,
        "points_reported": meta["points_reported"],
        "valid_points": meta["valid_points"],
        "missing_buckets": meta["missing_buckets"],
        "invalid_buckets": meta["invalid_buckets"],
        "new_saved": count,
        "interface_verified": True,
        "average_value": meta["average_value"],
        "oldest_bucket": min((p[0] for p in points), default=None),
        "newest_bucket": max((p[0] for p in points), default=None),
        "sample": [{"timestamp": t, "value": v} for t, v, _ in points[:3]],
        "note": "Historical API data is aggregated; stored separately from live sensor readings.",
    }
