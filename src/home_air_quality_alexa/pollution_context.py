"""Local-first activity diary and opt-in modelled outdoor PM2.5 comparison.

All journal entries and coordinates live in a private separate SQLite file.
Outdoor calls are only made after an explicit local POST action. No location is
inferred, sent to GitHub, or included in request logs.
"""
from __future__ import annotations

from datetime import datetime, date, time, timedelta, timezone
from pathlib import Path
import json
import math
import os
import sqlite3
from statistics import mean
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import uuid

from .dashboard import DashboardError, DEFAULT_DB, _as_utc_hour, build_dashboard
from .pollution_analysis import load_series, pearson

CONTEXT_DB = Path.home() / ".local/share/home-air-quality-alexa/context.sqlite3"
ACTIVITIES = {
    "cooking": "Cucina", "windows_open": "Finestre aperte",
    "cleaning": "Pulizie", "candle": "Candele/incenso",
    "ventilation": "Ventilazione", "heating": "Riscaldamento",
    "other": "Altro",
}
MAX_EVENTS = 2000
OUTDOOR_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"
PROVIDER = "Open-Meteo / CAMS (modello, non centralina)"
MODEL_NOTE = ("PM2.5 esterno da modello CAMS tramite Open-Meteo: valori di griglia, "
              "non misurazioni presso casa; confronto esplorativo non causale.")
SOURCE_URL = "https://open-meteo.com/en/docs/air-quality-api"


def utc_time(value: Any) -> datetime:
    if not isinstance(value, str) or len(value) > 48:
        raise DashboardError("Timestamp non valido: richiesto ISO 8601 con fuso")
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise DashboardError("Timestamp non valido") from exc
    if dt.tzinfo is None:
        raise DashboardError("Specifica il fuso orario")
    dt = dt.astimezone(timezone.utc)
    if not 2020 <= dt.year <= 2100:
        raise DashboardError("Data fuori intervallo consentito")
    return dt


def private_db(path: Path = CONTEXT_DB) -> sqlite3.Connection:
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if parent.is_symlink() or path.is_symlink():
        raise DashboardError("Percorso dati privati non sicuro")
    if path.exists() and not path.is_file():
        raise DashboardError("Database attività non valido")
    if path.exists() and path.stat().st_mode & 0o077:
        raise DashboardError("Permessi database non sufficientemente privati")
    connection = sqlite3.connect(path, timeout=8)
    if not path.exists():  # not normally reached after connect
        connection.close()
        raise DashboardError("Database non creato")
    os.chmod(path, 0o600)
    connection.execute("PRAGMA busy_timeout=8000")
    with connection:
        connection.executescript("""
        CREATE TABLE IF NOT EXISTS activity_notes (
            id TEXT PRIMARY KEY,
            activity TEXT NOT NULL,
            start_utc TEXT NOT NULL,
            duration_minutes INTEGER NOT NULL,
            note TEXT NOT NULL,
            created_at_utc TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_activity_notes_start ON activity_notes(start_utc);
        CREATE TABLE IF NOT EXISTS outdoor_config (
            singleton INTEGER PRIMARY KEY CHECK(singleton=1),
            latitude REAL NOT NULL, longitude REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS outdoor_pm25 (
            timestamp_utc TEXT PRIMARY KEY,
            value REAL NOT NULL,
            fetched_at_utc TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS outdoor_sync_state (
            singleton INTEGER PRIMARY KEY CHECK(singleton=1),
            fetched_at_utc TEXT NOT NULL,
            range_start TEXT NOT NULL, range_end_exclusive TEXT NOT NULL,
            provider TEXT NOT NULL
        );
        """)
    return connection


def add_activity(payload: dict[str, Any], *, db: Path = CONTEXT_DB) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) - {"activity", "start", "duration_minutes", "note"}:
        raise DashboardError("Campi attività non validi")
    activity = payload.get("activity")
    if activity not in ACTIVITIES:
        raise DashboardError("Tipo di attività non valido")
    start = utc_time(payload.get("start"))
    duration = payload.get("duration_minutes", 30)
    if type(duration) is not int or not 0 <= duration <= 360:
        raise DashboardError("Durata consentita tra 0 e 360 minuti")
    note = payload.get("note", "")
    if not isinstance(note, str) or len(note) > 200 or any(ord(c) < 32 and c not in "\t\n" for c in note):
        raise DashboardError("Nota troppo lunga o non valida")
    normalized = start.isoformat(timespec="seconds")
    event_id = uuid.uuid4().hex
    created = datetime.now(timezone.utc).isoformat()
    with private_db(db) as conn:
        count = conn.execute("SELECT COUNT(*) FROM activity_notes").fetchone()[0]
        if count >= MAX_EVENTS:
            raise DashboardError("Numero massimo di annotazioni raggiunto")
        with conn:
            conn.execute("INSERT INTO activity_notes VALUES (?,?,?,?,?,?)",
                         (event_id, activity, normalized, duration, note.strip(), created))
    return {"id":event_id, "activity":activity, "label":ACTIVITIES[activity],
            "start_utc":normalized, "duration_minutes":duration, "note":note.strip()}


def delete_activity(event_id: str, *, db: Path = CONTEXT_DB) -> bool:
    if not isinstance(event_id, str) or len(event_id) != 32 or any(c not in "0123456789abcdef" for c in event_id):
        raise DashboardError("Identificativo evento non valido")
    with private_db(db) as conn:
        with conn:
            cur = conn.execute("DELETE FROM activity_notes WHERE id=?", (event_id,))
    return cur.rowcount > 0


def activity_list(start_day: date, end_day: date, *, db: Path = CONTEXT_DB) -> list[dict[str, Any]]:
    start = datetime.combine(start_day, time.min, timezone.utc).isoformat(timespec="seconds")
    end = datetime.combine(end_day, time.min, timezone.utc).isoformat(timespec="seconds")
    with private_db(db) as conn:
        rows = conn.execute(
            "SELECT id,activity,start_utc,duration_minutes,note FROM activity_notes "
            "WHERE start_utc >= ? AND start_utc < ? ORDER BY start_utc DESC LIMIT 2000",
            (start, end)).fetchall()
    return [{"id":id, "activity":act, "label":ACTIVITIES[act],
             "start_utc":t,"duration_minutes":duration, "note":note}
            for id,act,t,duration,note in rows]


def _position(latitude: Any, longitude: Any) -> tuple[float, float]:
    if type(latitude) not in (float,int) or type(longitude) not in (float,int):
        raise DashboardError("Inserisci coordinate numeriche")
    lat, lon = float(latitude), float(longitude)
    if not (math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180):
        raise DashboardError("Coordinate geografiche non valide")
    # Only coarse model grid resolution is useful; round for minimization
    # BEFORE sending to any outside service.
    return round(lat,2), round(lon,2)


def _parse_open_meteo(payload: Any, start: date, end_exclusive: date) -> list[tuple[str,float]]:
    if not isinstance(payload, dict) or payload.get("error"):
        raise DashboardError("Il provider outdoor ha restituito un errore")
    if payload.get("utc_offset_seconds") != 0 or payload.get("timezone") not in ("GMT","UTC"):
        raise DashboardError("Fuso orario outdoor non coerente")
    hourly = payload.get("hourly")
    unit = payload.get("hourly_units")
    if not isinstance(hourly, dict) or not isinstance(unit, dict) or unit.get("pm2_5") not in ("μg/m³","µg/m³"):
        raise DashboardError("Formato o unità PM2.5 outdoor inattesi")
    times, values = hourly.get("time"), hourly.get("pm2_5")
    if not isinstance(times, list) or not isinstance(values, list) or len(times)!=len(values) or len(times)>31*24+24:
        raise DashboardError("Risposta outdoor incompleta")
    start_t = datetime.combine(start,time.min,timezone.utc)
    end_t = datetime.combine(end_exclusive,time.min,timezone.utc)
    out = {}
    for t,v in zip(times,values):
        if not isinstance(t,str) or not isinstance(v,(float,int)) or isinstance(v,bool) or not math.isfinite(v) or v < 0:
            continue
        try:
            instant=datetime.fromisoformat(t).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if not start_t <= instant < end_t or instant.minute or instant.second:
            continue
        out[instant.isoformat(timespec="seconds")] = float(v)
    if not out:
        raise DashboardError("Nessuna previsione/stima PM2.5 outdoor valida")
    return sorted(out.items())


def sync_outdoor(start: date, end_exclusive: date, *, db: Path = CONTEXT_DB,
                 latitude: float | None = None, longitude: float | None = None,
                 fetcher: Any = None) -> dict[str, Any]:
    """Explicit opt-in only: one bounded HTTPS call; no calls on page load."""
    if not 1 <= (end_exclusive-start).days <= 31:
        raise DashboardError("Sincronizza al massimo 31 giorni per richiesta")
    with private_db(db) as conn:
        coords = conn.execute("SELECT latitude,longitude FROM outdoor_config WHERE singleton=1").fetchone()
        row = conn.execute("SELECT fetched_at_utc FROM outdoor_sync_state WHERE singleton=1").fetchone()
    if latitude is not None or longitude is not None:
        if latitude is None or longitude is None:
            raise DashboardError("Servono entrambe le coordinate")
        lat,lon=_position(latitude,longitude)
    elif coords:
        lat,lon=_position(*coords)
    else:
        raise DashboardError("Configura prima una posizione approssimativa")
    if row:
        # No accidental repeated calls within 15 minutes.
        last = utc_time(row[0])
        if datetime.now(timezone.utc)-last < timedelta(minutes=15):
            raise DashboardError("Outdoor già sincronizzato recentemente: riprova più tardi")
    params=urlencode({"latitude":lat, "longitude":lon, "hourly":"pm2_5",
                      "timezone":"GMT", "start_date":start.isoformat(),
                      "end_date":(end_exclusive-timedelta(days=1)).isoformat(),
                      "domains":"cams_europe"})
    req=Request(OUTDOOR_URL+"?"+params, headers={"User-Agent":"Airwise-local/0.1", "Accept":"application/json"})
    try:
        if fetcher is None:
            with urlopen(req,timeout=18) as resp:
                if resp.status!=200:
                    raise DashboardError("Fonte outdoor non disponibile")
                data=resp.read(2_000_001)
        else:
            data=fetcher(req)
        if len(data)>2_000_000:
            raise DashboardError("Risposta outdoor troppo grande")
        readings=_parse_open_meteo(json.loads(data),start,end_exclusive)
    except DashboardError:
        raise
    except Exception as exc:
        # Never echo URLs, coordinates, error bodies or private request traces.
        raise DashboardError("Impossibile recuperare dati dal modello outdoor") from exc
    fetched=datetime.now(timezone.utc).isoformat()
    with private_db(db) as conn:
        with conn:
            conn.execute("INSERT INTO outdoor_config VALUES (1,?,?) ON CONFLICT(singleton) DO UPDATE SET latitude=excluded.latitude,longitude=excluded.longitude", (lat,lon))
            conn.executemany("INSERT INTO outdoor_pm25 VALUES (?,?,?) ON CONFLICT(timestamp_utc) DO UPDATE SET value=excluded.value,fetched_at_utc=excluded.fetched_at_utc",
                             [(ts,val,fetched) for ts,val in readings])
            conn.execute("INSERT INTO outdoor_sync_state VALUES (1,?,?,?,?) ON CONFLICT(singleton) DO UPDATE SET fetched_at_utc=excluded.fetched_at_utc,range_start=excluded.range_start,range_end_exclusive=excluded.range_end_exclusive,provider=excluded.provider",
                         (fetched,start.isoformat(),end_exclusive.isoformat(),PROVIDER))
    return {"status":"ok","imported_hours":len(readings),"source":PROVIDER,"model_only":True,
            "note":MODEL_NOTE,"start_date_utc":start.isoformat(),
            "end_date_utc_exclusive":end_exclusive.isoformat()}


def context_report(history_db: Path=DEFAULT_DB, *, days:int=30, offset:int=0,
                   db:Path=CONTEXT_DB) -> dict[str, Any]:
    meta=build_dashboard(history_db,days=days,offset=offset)
    start=date.fromisoformat(meta["start_date_utc"])
    end=date.fromisoformat(meta["end_date_utc_exclusive"])
    events=activity_list(start,end,db=db)
    pm=load_series(history_db,start,end).get("PM25",{})
    with private_db(db) as conn:
        configured=conn.execute("SELECT 1 FROM outdoor_config WHERE singleton=1").fetchone() is not None
        info=conn.execute("SELECT fetched_at_utc,range_start,range_end_exclusive FROM outdoor_sync_state WHERE singleton=1").fetchone()
        start_iso=datetime.combine(start,time.min,timezone.utc).isoformat(timespec="seconds")
        end_iso=datetime.combine(end,time.min,timezone.utc).isoformat(timespec="seconds")
        rows=conn.execute("SELECT timestamp_utc,value FROM outdoor_pm25 WHERE timestamp_utc >= ? AND timestamp_utc < ? ORDER BY timestamp_utc",
                          (start_iso,end_iso)).fetchall()
    outdoor={}
    for t,v in rows:
        dt=_as_utc_hour(t)
        if dt is not None and math.isfinite(v):
            outdoor[dt]=v
    matched=sorted(set(pm)&set(outdoor))
    differences=[pm[t]-outdoor[t] for t in matched]
    joint=[{"time_utc":t.isoformat(),"indoor":pm[t],"outdoor_model":outdoor[t]} for t in matched]
    comparison={
        "paired_hours":len(matched),
        "mean_indoor":round(mean(pm[t] for t in matched),2) if matched else None,
        "mean_outdoor_model":round(mean(outdoor[t] for t in matched),2) if matched else None,
        "mean_difference":round(mean(differences),2) if matched else None,
        "indoor_higher_pct":round(100*sum(d>0 for d in differences)/len(differences),1) if matched else None,
        "r":pearson([pm[t] for t in matched],[outdoor[t] for t in matched]),
        "paired_hourly":joint,
        "limitations":"Confronto osservazionale fra aggregati orari indoor e modello esterno di griglia; non attribuisce origini o cause.",
    }
    # A pre/post event contrast is descriptive, not attributable to an event.
    enriched=[]
    for event in events:
        start_ev=utc_time(event["start_utc"])
        before=[pm.get(start_ev.replace(minute=0,second=0,microsecond=0)-timedelta(hours=h)) for h in (2,1)]
        during=[pm.get(start_ev.replace(minute=0,second=0,microsecond=0)+timedelta(hours=h)) for h in (0,1)]
        contrast=(round(mean(during)-mean(before),2)
                  if all(v is not None for v in before+during) else None)
        enriched.append({**event,"pm25_delta_two_hours_descriptive":contrast})
    return {"schema_version":"1","start_date_utc":start.isoformat(),
            "end_date_utc_exclusive":end.isoformat(),"timezone":"UTC",
            "activity_types":ACTIVITIES,"activities":enriched,
            "outdoor":{"configured":configured,"available_hours":len(outdoor),
                       "last_sync_utc":info[0] if info else None,"provider":PROVIDER,
                       "source_url":SOURCE_URL,"model_note":MODEL_NOTE},
            "comparison":comparison}
