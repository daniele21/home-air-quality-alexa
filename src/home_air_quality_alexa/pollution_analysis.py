"""Read-only pollution exploration built from canonical Alexa hourly buckets.

Observational analytics only. Relative event thresholds describe the selected
sample; the WHO PM2.5 comparison is a contextual 24h ambient-air guideline,
not a calibrated indoor safety evaluation or an instant alarm.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
import math
from pathlib import Path
from statistics import median
from zoneinfo import ZoneInfo
from typing import Any

from .dashboard import (
    CHANNELS, DashboardError, _as_utc_hour, _read_db, _source_relation,
    build_dashboard,
)

ROME = ZoneInfo("Europe/Rome")
WHO_PM25_24H = 15.0
MIN_DAILY_HOURS = 18
MIN_EVENT_HOURS = 2
MIN_PERCENTILE_HOURS = 48
MIN_CORRELATION_PAIRS = 48


def percentile(values: list[float], fraction: float) -> float | None:
    """Interpolated empirical percentile (not an air-quality health limit)."""
    if not values:
        return None
    ordered = sorted(values)
    at = (len(ordered) - 1) * fraction
    low = int(at)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (at - low)


def pearson(xs: list[float], ys: list[float]) -> float | None:
    """Synchronous hourly Pearson r, only if sample and variation suffice."""
    if len(xs) != len(ys) or len(xs) < MIN_CORRELATION_PAIRS:
        return None
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    xx = sum((x - mx) ** 2 for x in xs)
    yy = sum((y - my) ** 2 for y in ys)
    if xx <= 1e-12 or yy <= 1e-12:
        return None
    return round(max(-1, min(1, sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / math.sqrt(xx * yy))), 3)


def load_series(db: Path, start_day: date, end_day: date) -> dict[str, dict[datetime, float]]:
    with _read_db(db) as conn:
        relation = _source_relation(conn)
        if not relation:
            raise DashboardError("Nessun dato storico utilizzabile")
        keys = tuple(ch[0] for ch in CHANNELS)
        rows = conn.execute(
            f"SELECT device_serial, sensor_key, bucket_timestamp_utc, numeric_value "
            f"FROM {relation} WHERE resolution='ONE_HOUR' "
            "AND sensor_key IN (?,?,?,?,?) AND bucket_timestamp_utc >= ? "
            "AND bucket_timestamp_utc < ? ORDER BY sensor_key,bucket_timestamp_utc",
            (*keys,
             datetime.combine(start_day, time.min, tzinfo=timezone.utc).isoformat(),
             datetime.combine(end_day, time.min, tzinfo=timezone.utc).isoformat()),
        ).fetchall()

    sources = {row[0] for row in rows}
    if len(sources) > 1:
        raise DashboardError("Dati di più monitor: selezionare un dispositivo prima di confrontare le misure")

    out: dict[str, dict[datetime, float]] = defaultdict(dict)
    for _, sensor, timestamp, value in rows:
        dt = _as_utc_hour(timestamp)
        if dt is None or isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        if not math.isfinite(value):
            continue
        out[sensor][dt] = float(value)
    return out


def pm25_daily(series: dict[datetime, float], start: date, end: date) -> list[dict[str, Any]]:
    days = (end - start).days
    result = []
    for i in range(days):
        day = start + timedelta(days=i)
        values = [v for dt, v in series.items() if dt.date() == day]
        n = len(values)
        mean = round(sum(values) / n, 2) if n else None
        reliable = n >= MIN_DAILY_HOURS
        result.append({
            "date_utc": day.isoformat(),
            "mean": mean,
            "maximum_hourly": max(values) if values else None,
            "hours": n,
            "complete_enough_for_24h_comparison": reliable,
            "above_who_ambient_reference": mean > WHO_PM25_24H if reliable else None,
        })
    return result


def detect_episodes(series: dict[datetime, float], threshold: float | None) -> list[dict[str, Any]]:
    """Consecutive observed UTC hours above empirical p90; missing hour breaks."""
    if threshold is None:
        return []
    run: list[tuple[datetime, float]] = []
    episodes: list[dict[str, Any]] = []

    def finish() -> None:
        if len(run) >= MIN_EVENT_HOURS:
            start, end = run[0][0], run[-1][0]
            episodes.append({
                "start_utc": start.isoformat(),
                "end_utc_exclusive": (end + timedelta(hours=1)).isoformat(),
                "duration_hours": len(run),
                "peak": max(v for _, v in run),
                "mean": round(sum(v for _, v in run) / len(run), 2),
                "excess_above_relative_p90": round(sum(v - threshold for _, v in run), 2),
            })
        run.clear()

    for dt, value in sorted(series.items()):
        if value > threshold:
            if run and dt != run[-1][0] + timedelta(hours=1):
                finish()
            run.append((dt, value))
        else:
            finish()
    finish()
    return sorted(episodes, key=lambda x: (-x["excess_above_relative_p90"], -x["peak"], x["start_utc"]))[:12]


def diurnal_profiles(data: dict[str, dict[datetime, float]]) -> dict[str, list[dict[str, Any]]]:
    result = {}
    for sensor, values in data.items():
        buckets: dict[int, list[float]] = defaultdict(list)
        for dt, value in values.items():
            buckets[dt.astimezone(ROME).hour].append(value)
        result[sensor] = [{
            "hour_local": hour,
            "mean": round(sum(buckets[hour]) / len(buckets[hour]), 2) if buckets[hour] else None,
            "sample_count": len(buckets[hour]),
        } for hour in range(24)]
    return result


def weekday_hour_heatmaps(data: dict[str, dict[datetime, float]]) -> dict[str, list[list[dict[str, Any]]]]:
    """Genuine joint local-time weekday x hour cell means, never products of marginals."""
    result = {}
    for sensor, values in data.items():
        buckets: dict[tuple[int, int], list[float]] = defaultdict(list)
        for dt, value in values.items():
            local = dt.astimezone(ROME)
            buckets[(local.weekday(), local.hour)].append(value)
        result[sensor] = [[
            {
                "weekday_local": weekday,
                "hour_local": hour,
                "mean": round(sum(buckets[(weekday, hour)]) / len(buckets[(weekday, hour)]), 2)
                        if buckets[(weekday, hour)] else None,
                "sample_count": len(buckets[(weekday, hour)]),
            }
            for hour in range(24)
        ] for weekday in range(7)]
    return result


def weekday_profiles(data: dict[str, dict[datetime, float]]) -> dict[str, list[dict[str, Any]]]:
    result = {}
    for sensor, values in data.items():
        buckets: dict[int, list[float]] = defaultdict(list)
        for dt, value in values.items():
            buckets[dt.astimezone(ROME).weekday()].append(value)
        result[sensor] = [{
            "weekday_local": i,
            "mean": round(sum(buckets[i]) / len(buckets[i]), 2) if buckets[i] else None,
            "sample_count": len(buckets[i]),
        } for i in range(7)]
    return result


def associations(data: dict[str, dict[datetime, float]]) -> list[dict[str, Any]]:
    pm = data.get("PM25", {})
    out = []
    for sensor, label in (("VOC", "VOC"), ("CO", "CO"), ("Humidity", "Umidità")):
        other = data.get(sensor, {})
        shared = sorted(set(pm) & set(other))
        xs, ys = [pm[t] for t in shared], [other[t] for t in shared]
        out.append({
            "sensor": sensor,
            "label": label,
            "paired_hours": len(shared),
            "r": pearson(xs, ys),
            "sufficient_pairs": len(shared) >= MIN_CORRELATION_PAIRS,
            "note": "Associazione contemporanea, non causalità; può risentire di trend e autocorrelazione.",
        })
    return out


def build_pollution(db: Path, *, days: int = 30, offset: int = 0) -> dict[str, Any]:
    meta = build_dashboard(db, days=days, offset=offset)
    start_day = date.fromisoformat(meta["start_date_utc"])
    end_day = date.fromisoformat(meta["end_date_utc_exclusive"])
    series = load_series(db, start_day, end_day)
    pm = series.get("PM25", {})
    values = list(pm.values())
    enough = len(values) >= MIN_PERCENTILE_HOURS
    p90 = round(percentile(values, .90), 2) if enough else None
    p50 = round(median(values), 2) if values else None
    if p90 is not None and (p50 is None or p90 <= p50):
        p90 = None

    daily = pm25_daily(pm, start_day, end_day)
    evaluable = [day for day in daily if day["complete_enough_for_24h_comparison"]]
    above = [day for day in evaluable if day["above_who_ambient_reference"]]
    episodes = detect_episodes(pm, p90)

    result = {
        "schema_version": "1",
        "start_date_utc": meta["start_date_utc"],
        "end_date_utc_exclusive": meta["end_date_utc_exclusive"],
        "days": days,
        "offset_days": offset,
        "timezone_for_patterns": "Europe/Rome",
        "primary_pollutant": "PM25",
        "pm25": {
            "unit": "µg/m³",
            "hours_available": len(values),
            "hours_expected": days * 24,
            "coverage_pct": round(100 * len(values) / (days * 24), 1),
            "median": p50,
            "p90": p90,
            "maximum": max(values) if values else None,
            "daily": daily,
            "complete_days": len(evaluable),
            "days_above_who_reference": len(above),
            "who_24h_reference": {
                "value": WHO_PM25_24H,
                "unit": "µg/m³",
                "context": "OMS 2021: media 24 ore di PM2.5 per aria ambientale. Riferimento descrittivo, non allarme o certificazione indoor.",
                "minimum_observed_hours_for_display": MIN_DAILY_HOURS,
            },
            "episode_method": "Ore consecutive sopra il 90° percentile del periodo; minimo 2 ore. Soglia statistica relativa, NON sanitaria.",
            "episodes": episodes,
        },
        "diurnal": diurnal_profiles(series),
        "weekday": weekday_profiles(series),
        "weekday_hour_heatmap": weekday_hour_heatmaps(series),
        "associations": associations(series),
        "available_channels": [
            {"sensor": k, "label": label, "unit": unit, "hours": len(series.get(k, {}))}
            for k, label, unit in CHANNELS
        ],
        "limitations": [
            "Un monitor indoor non identifica la fonte dei contaminanti.",
            "Le medie orarie Alexa non permettono di stimare l'esposizione personale.",
            "Le correlazioni non dimostrano causalità.",
            "Il sensore CO non sostituisce un rilevatore di monossido certificato.",
            "Gli episodi sono individuati rispetto allo storico disponibile, non a limiti sanitari.",
        ],
    }
    return result
