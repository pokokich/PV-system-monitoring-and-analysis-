"""External benchmarks: PVGIS monthly yield per array and Open-Meteo hourly irradiance.

    python3 -m collector.main external          # fetch what is missing (also runs inside `run`)
    python3 -m collector.main external --force  # refetch PVGIS and the whole weather history

Hosts: re.jrc.ec.europa.eu (PVGIS), archive-api.open-meteo.com and api.open-meteo.com.
Nothing here talks to Deye; failures are logged and retried on the next round.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .db import get_state, log_ingest, set_state

log = logging.getLogger("collector.external")

PVGIS_URL = "https://re.jrc.ec.europa.eu/api/v5_3/PVcalc"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_LAG_DAYS = 3        # the archive (reanalysis) lags a few days; the rest comes from the forecast API
WEATHER_EVERY_S = 3600
CHUNK_DAYS = 92
REQUEST_PAUSE_S = 2


def _get_json(url: str, params: dict, timeout: int = 60) -> dict:
    full = f"{url}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(full, headers={"User-Agent": "deyehomePVsystem (read-only home dashboard)"})
    last = None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                out = json.loads(resp.read().decode())
            time.sleep(REQUEST_PAUSE_S)
            return out
        except urllib.error.HTTPError as exc:
            last = exc
            # 429: Open-Meteo's free tier counts calls per minute; wait it out
            time.sleep(65 if exc.code == 429 else 5 * (attempt + 1))
        except Exception as exc:  # network
            last = exc
            time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"{url}: {last}")


def _param(conn: sqlite3.Connection, key: str, default: float | None) -> float | None:
    row = conn.execute("SELECT value FROM param WHERE key = ? AND value IS NOT NULL ORDER BY valid_from DESC LIMIT 1",
                       (key,)).fetchone()
    return row[0] if row else default


def site(conn: sqlite3.Connection) -> tuple[float, float] | None:
    """Coordinates of the installation (from Deye at discovery, editable in Settings); None if unknown."""
    lat, lon = _param(conn, "site_lat", None), _param(conn, "site_lon", None)
    return (lat, lon) if lat is not None and lon is not None else None


def arrays(conn: sqlite3.Connection) -> list[dict]:
    """Current arrays with a known orientation (compass azimuth: 180 = south)."""
    return [dict(r) for r in conn.execute(
        "SELECT id, name, panels, panel_wp, azimuth_deg, tilt_deg, valid_from FROM pv_array "
        "WHERE valid_to IS NULL AND azimuth_deg IS NOT NULL AND tilt_deg IS NOT NULL ORDER BY id")]


def aspect(azimuth_deg: float) -> float:
    """Compass azimuth -> PVGIS / Open-Meteo convention (0 south, -90 east, 90 west)."""
    a = azimuth_deg - 180
    return (a + 180) % 360 - 180


# ---------------------------------------------------------------- PVGIS

def fetch_pvgis(conn: sqlite3.Connection, force: bool = False) -> int:
    lat, lon = site(conn)
    loss = _param(conn, "pvgis_loss_pct", 14)
    n = 0
    for a in arrays(conn):
        kwp = a["panels"] * a["panel_wp"] / 1000
        asp = aspect(a["azimuth_deg"])
        have = conn.execute("SELECT tilt_deg, aspect_deg, loss_pct FROM pvgis_monthly WHERE array_id = ? LIMIT 1",
                            (a["id"],)).fetchone()
        if have and not force and tuple(have) == (a["tilt_deg"], asp, loss):
            continue
        params = {"lat": lat, "lon": lon, "peakpower": kwp, "loss": loss, "angle": a["tilt_deg"],
                  "aspect": asp, "outputformat": "json"}
        t0 = time.monotonic()
        try:
            data = _get_json(PVGIS_URL, params)
        except RuntimeError as exc:
            log_ingest(conn, "external", "pvgis", "error", error=str(exc)[:500])
            conn.commit()
            log.warning("PVGIS for %s: %s", a["name"], exc)
            continue
        months = data["outputs"]["monthly"]["fixed"]
        db = data["inputs"]["meteo_data"].get("radiation_db")
        for m in months:
            conn.execute(
                "INSERT INTO pvgis_monthly VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(array_id, month) DO UPDATE SET "
                "kwh_per_kwp = excluded.kwh_per_kwp, irr_kwh_m2 = excluded.irr_kwh_m2, sd_kwh_per_kwp = excluded.sd_kwh_per_kwp, "
                "tilt_deg = excluded.tilt_deg, aspect_deg = excluded.aspect_deg, loss_pct = excluded.loss_pct, "
                "radiation_db = excluded.radiation_db, fetched_at = excluded.fetched_at",
                (a["id"], m["month"], m["E_m"] / kwp, m.get("H(i)_m"), (m.get("SD_m") or 0) / kwp,
                 a["tilt_deg"], asp, loss, db, int(time.time())))
        log_ingest(conn, "external", "pvgis", "ok", points=len(months), duration_ms=int((time.monotonic() - t0) * 1000))
        conn.commit()
        n += 1
        log.info("PVGIS %s: %.0f kWh/kWp a year", a["name"], sum(m["E_m"] for m in months) / kwp)
    return n


# ---------------------------------------------------------------- Open-Meteo

def _store_weather(conn, tz, data: dict, array_id: int | None, source: str) -> int:
    h = data.get("hourly") or {}
    times = h.get("time") or []
    n = 0
    now = int(time.time())
    for i, t in enumerate(times):
        # timezone=GMT; radiation is the mean of the preceding hour, so "10:00" is 09:00-10:00 UTC
        ts = int(datetime.fromisoformat(t).replace(tzinfo=timezone.utc).timestamp()) - 3600
        gti = (h.get("global_tilted_irradiance") or [None] * len(times))[i]
        if array_id is None:
            ghi = h["shortwave_radiation"][i]
            if ghi is None:
                continue
            local = datetime.fromtimestamp(ts, tz)
            conn.execute(
                "INSERT INTO weather_hourly VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(hour_ts) DO UPDATE SET "
                "ghi_wm2 = excluded.ghi_wm2, temp_c = excluded.temp_c, cloud_pct = excluded.cloud_pct, "
                "source = excluded.source, fetched_at = excluded.fetched_at "
                "WHERE weather_hourly.source = 'forecast' OR excluded.source = 'archive'",
                (ts, local.date().isoformat(), local.hour, ghi, h["temperature_2m"][i], h["cloud_cover"][i], source, now))
        if gti is None or array_id is None:
            if array_id is None:
                n += 1
            continue
        conn.execute(
            "INSERT INTO poa_hourly VALUES (?, ?, ?, ?) ON CONFLICT(hour_ts, array_id) DO UPDATE SET "
            "gti_wm2 = excluded.gti_wm2, source = excluded.source "
            "WHERE poa_hourly.source = 'forecast' OR excluded.source = 'archive'",
            (ts, array_id, gti, source))
        n += 1
    return n


def _weather_requests(conn) -> list[tuple[int | None, dict]]:
    """One request for the horizontal values, one per array plane."""
    lat, lon = site(conn)
    out: list[tuple[int | None, dict]] = [(None, {"latitude": lat, "longitude": lon,
                                                  "hourly": "shortwave_radiation,temperature_2m,cloud_cover"})]
    for a in arrays(conn):
        out.append((a["id"], {"latitude": lat, "longitude": lon, "hourly": "global_tilted_irradiance",
                              "tilt": a["tilt_deg"], "azimuth": aspect(a["azimuth_deg"])}))
    return out


def fetch_weather(conn: sqlite3.Connection, tz: ZoneInfo, first: date, force: bool = False) -> int:
    """Archive from `first` (or from the last archived day) and the forecast API for the last days."""
    today = datetime.now(tz).date()
    archive_end = today - timedelta(days=ARCHIVE_LAG_DAYS)
    done = get_state(conn, "weather_archive_until")
    start = first if force or not done else date.fromisoformat(done) - timedelta(days=2)
    n = 0
    t0 = time.monotonic()
    try:
        d = start
        while d <= archive_end:
            end = min(archive_end, d + timedelta(days=CHUNK_DAYS - 1))
            for array_id, params in _weather_requests(conn):
                data = _get_json(ARCHIVE_URL, dict(params, start_date=d.isoformat(), end_date=end.isoformat(),
                                                   timezone="GMT"))
                n += _store_weather(conn, tz, data, array_id, "archive")
            set_state(conn, "weather_archive_until", end.isoformat())
            conn.commit()
            d = end + timedelta(days=1)
        for array_id, params in _weather_requests(conn):
            data = _get_json(FORECAST_URL, dict(params, past_days=ARCHIVE_LAG_DAYS + 2, forecast_days=1, timezone="GMT"))
            n += _store_weather(conn, tz, data, array_id, "forecast")
    except RuntimeError as exc:
        log_ingest(conn, "external", "open-meteo", "error", error=str(exc)[:500])
        conn.commit()
        log.warning("Open-Meteo: %s", exc)
        return n
    log_ingest(conn, "external", "open-meteo", "ok", points=n, duration_ms=int((time.monotonic() - t0) * 1000))
    set_state(conn, "weather_fetched_at", str(int(time.time())))
    conn.commit()
    return n


def run(conn: sqlite3.Connection, tz: ZoneInfo, first: date, force: bool = False) -> None:
    """PVGIS when the arrays changed, weather at most once an hour (both need the coordinates)."""
    if site(conn) is None:
        log.info("PVGIS / Open-Meteo skipped: the installation has no coordinates yet")
        return
    fetch_pvgis(conn, force)
    last = int(get_state(conn, "weather_fetched_at") or 0)
    if force or time.time() - last >= WEATHER_EVERY_S:
        n = fetch_weather(conn, tz, first, force)
        log.info("Open-Meteo: %d hourly values", n)
