"""Read-only access to the collector database and the per-day model the KPIs are built on.

The dashboard never talks to Deye and never writes: the database is opened with
SQLite's `mode=ro`. Everything the pages show is derived from one in-memory model
(one record per local day), rebuilt when the database file changes.
"""
from __future__ import annotations

import os
import sqlite3
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

from collector import appconf
from collector.tariffs import zone as tariff_zone

from . import bench, health

DB_PATH = os.environ.get("DB_PATH", str(Path(__file__).resolve().parents[1] / "data" / "pv.sqlite"))
# The installation's zone (setting `timezone`, else TZ, else the computer's); build() refreshes it.
TZ = appconf.DynamicZone(appconf.timezone_from_db(DB_PATH))

# Frames further apart than this are not integrated (inverter points)
MAX_GAP_S = 15 * 60
SOC_FULL = 99.0


def connect() -> sqlite3.Connection:
    """Read-only connection; raises sqlite3.OperationalError if the file is missing."""
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def db_version() -> tuple[float, float]:
    """Changes whenever the collector commits (main file or WAL)."""
    out = []
    for suffix in ("", "-wal"):
        try:
            out.append(os.stat(DB_PATH + suffix).st_mtime)
        except FileNotFoundError:
            out.append(0.0)
    return tuple(out)


def local_day(ts: int) -> str:
    return datetime.fromtimestamp(ts, TZ).date().isoformat()


def day_bounds(day: str) -> tuple[int, int]:
    """Unix start and end of a local day (23 or 25 hours on DST days)."""
    d = date.fromisoformat(day)
    start = datetime(d.year, d.month, d.day, tzinfo=TZ)
    nxt = d + timedelta(days=1)
    end = datetime(nxt.year, nxt.month, nxt.day, tzinfo=TZ)
    return int(start.timestamp()), int(end.timestamp())


class Versioned:
    """Rows with valid_from (inclusive) / valid_to (exclusive) local dates."""

    def __init__(self, rows: list[dict]):
        self.rows = sorted(rows, key=lambda r: r["valid_from"])

    def at(self, day: str) -> list[dict]:
        return [r for r in self.rows if r["valid_from"] <= day and (r["valid_to"] is None or day < r["valid_to"])]


@dataclass
class Model:
    built_at: float
    version: tuple
    station_id: int | None
    station_name: str | None = None
    days: dict[str, dict] = field(default_factory=dict)          # day -> record
    order: list[str] = field(default_factory=list)              # sorted days
    soc_grid: dict[tuple[str, int], float] = field(default_factory=dict)  # (day, hour) -> mean SOC
    capex: list[dict] = field(default_factory=list)
    opex: list[dict] = field(default_factory=list)
    arrays: list[dict] = field(default_factory=list)
    batteries: list[dict] = field(default_factory=list)
    invoices: list[dict] = field(default_factory=list)
    last_station_ts: int | None = None
    last_device_ts: int | None = None
    alerts: int = 0
    settings: dict = field(default_factory=dict)          # app_setting: currency, site name...
    site: tuple[float, float] | None = None                 # latitude, longitude (params site_lat / site_lon)
    bench: dict = field(default_factory=dict)
    health: dict = field(default_factory=dict)

    def range(self, start: str, end: str) -> list[dict]:
        return [self.days[d] for d in self.order if start <= d <= end]


# ---------------------------------------------------------------- building

def _params(conn) -> dict[str, list[tuple[str, float]]]:
    out: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for r in conn.execute("SELECT key, valid_from, value FROM param ORDER BY valid_from"):
        out[r["key"]].append((r["valid_from"], r["value"]))
    return out


def _param_at(params, key: str, day: str, default: float) -> float:
    val = default
    for valid_from, value in params.get(key, []):
        if valid_from <= day and value is not None:
            val = value
    return val


def _empty_day(day: str) -> dict:
    return {
        "day": day, "pv": 0.0, "load": 0.0, "imp": 0.0, "exp": 0.0, "chg": 0.0, "dis": 0.0,
        "imp_day": 0.0, "imp_night": 0.0, "load_day": 0.0, "load_night": 0.0, "dis_night": 0.0,
        "pv_direct": 0.0, "chg_pv": 0.0, "chg_grid": 0.0,
        "soc_min": None, "soc_max": None, "pv_peak_w": None, "import_peak_w": None,
        "frames": 0, "covered_s": 0, "expected_s": 0, "day_frac": 1.0, "pv_estimated": 0, "interp": 0,
        "h_full": 0.0, "h_low": 0.0, "h_soc": 0.0, "t_full": None, "soc_min_ts": None,
        "bad_soc": 0, "bad_load": 0, "pv_night": 0,
        "temp_batt_max": None, "temp_batt_avg": None, "temp_batt_dh35": None, "temp_batt_dh40": None,
        "temp_inv_max": None, "temp_inv_avg": None,
        "pv1": None, "pv2": None, "kwp": 0.0, "kwp1": 0.0, "kwp2": 0.0, "batt_kwh": 0.0,
        "soc_min_normal": 30.0,
    }


def build(conn: sqlite3.Connection) -> Model:
    TZ.set(appconf.timezone(conn))
    row = conn.execute("SELECT id, name FROM station ORDER BY id LIMIT 1").fetchone()
    station_id = row["id"] if row else None
    m = Model(built_at=time.time(), version=db_version(), station_id=station_id, station_name=row["name"] if row else None,
              settings=appconf.all_settings(conn))
    if station_id is None:
        return m
    days = m.days
    now_ts = int(time.time())

    for r in conn.execute("SELECT * FROM energy_daily WHERE station_id = ? ORDER BY day", (station_id,)):
        d = days.setdefault(r["day"], _empty_day(r["day"]))
        d.update(pv=r["pv_kwh"] or 0.0, load=r["load_kwh"] or 0.0, imp=r["import_kwh"] or 0.0,
                 exp=r["export_kwh"] or 0.0, chg=r["charge_kwh"] or 0.0, dis=r["discharge_kwh"] or 0.0,
                 pv_peak_w=r["pv_peak_w"],
                 import_peak_w=r["import_peak_w"], frames=r["frames"] or 0, covered_s=r["covered_s"] or 0)

    # Tariff zones per hour (zone boundaries are whole local hours)
    windows = appconf.night_windows(conn)
    hour_day: dict[int, str] = {}
    for r in conn.execute("SELECT hour_ts, day, hour, load_kwh, import_kwh, discharge_kwh FROM energy_hourly "
                          "WHERE station_id = ?", (station_id,)):
        hour_day[r["hour_ts"]] = r["day"]
        d = days.get(r["day"])
        if d is None:
            continue
        local = datetime.fromtimestamp(r["hour_ts"], TZ)
        z = tariff_zone(local, windows)
        d["imp_" + z] += r["import_kwh"] or 0.0
        d["load_" + z] += r["load_kwh"] or 0.0
        if z == "night":
            d["dis_night"] += r["discharge_kwh"] or 0.0

    # Direct self-consumption and battery charge split per 5-minute interval
    for r in conn.execute(
        "SELECT (ts_start / 3600) * 3600 AS h, "
        "SUM(MIN(charge_kwh, pv_kwh)) AS chg_pv, "
        "SUM(charge_kwh - MIN(charge_kwh, pv_kwh)) AS chg_grid, "
        "SUM(MAX(pv_kwh - MIN(charge_kwh, pv_kwh) - export_kwh, 0)) AS direct, "
        "SUM(quality = 'pv_estimated') AS est, SUM(quality = 'interp') AS interp "
        "FROM energy_5min WHERE station_id = ? GROUP BY h", (station_id,)):
        day = hour_day.get(r["h"]) or local_day(r["h"])
        d = days.get(day)
        if d is None:
            continue
        d["chg_pv"] += r["chg_pv"] or 0.0
        d["chg_grid"] += r["chg_grid"] or 0.0
        d["pv_direct"] += r["direct"] or 0.0
        d["pv_estimated"] += r["est"] or 0
        d["interp"] += r["interp"] or 0

    # Manual parameters
    params = _params(conn)
    lat, lon = _param_at(params, "site_lat", "9999-12-31", None), _param_at(params, "site_lon", "9999-12-31", None)
    m.site = (lat, lon) if lat is not None and lon is not None else None
    m.arrays = [dict(r) for r in conn.execute("SELECT * FROM pv_array ORDER BY valid_from")]
    m.batteries = [dict(r) for r in conn.execute("SELECT * FROM battery_config ORDER BY valid_from")]
    arrays = Versioned(m.arrays)
    batteries = Versioned(m.batteries)
    tariffs = Versioned([dict(r) for r in conn.execute("SELECT * FROM tariff")])
    feed_in = Versioned([dict(r) for r in conn.execute("SELECT * FROM feed_in_price")])
    m.capex = [dict(r) for r in conn.execute("SELECT * FROM capex_item ORDER BY date")]
    m.opex = [dict(r) for r in conn.execute("SELECT * FROM opex_item ORDER BY date")]
    opex_by_day: dict[str, float] = defaultdict(float)
    for o in m.opex:
        opex_by_day[o["date"]] += o["amount_ccy"] or 0.0

    # Per-day parameters, prices and money
    for day in sorted(days):
        d = days[day]
        start, end = day_bounds(day)
        d["expected_s"] = max(min(end, now_ts) - start, 0)
        d["day_frac"] = d["expected_s"] / (end - start)   # < 1 only for today: the part of the day that has passed
        arr = arrays.at(day)
        d["kwp"] = sum(a["panels"] * a["panel_wp"] / 1000 for a in arr)
        for a in arr:
            if a["mppt_channel"] in (1, 2):
                d[f"kwp{a['mppt_channel']}"] = a["panels"] * a["panel_wp"] / 1000
        bat = batteries.at(day)
        if bat:
            d["batt_kwh"] = bat[-1]["modules"] * bat[-1]["kwh_per_module"]
            d["soc_min_normal"] = bat[-1]["soc_min_normal"] or 30.0
        vat = _param_at(params, "vat_rate", day, 0.20)
        for t in tariffs.at(day):
            price = t["price_per_kwh"] * (1 if t["vat_included"] else 1 + vat)
            if t["zone"] == "single":
                d["price_day"] = d["price_night"] = price
            else:
                d["price_" + t["zone"]] = price
        d.setdefault("price_day", None)
        d.setdefault("price_night", None)
        fi = feed_in.at(day)
        d["feed_in_price"] = fi[-1]["price_per_kwh"] if fi else 0.0
        d["meter_imp_day"], d["meter_imp_night"] = d["imp_day"], d["imp_night"]
        d["meter_load_day"], d["meter_load_night"] = d["load_day"], d["load_night"]
        d["metered"] = False
        d["opex"] = opex_by_day.get(day, 0.0)

    # SOC: hours full / at or below the normal minimum (the setting valid on that day),
    # hours with SOC data (the divisor of the per-day averages), first time full, mean per hour
    last_ts = None
    soc_acc: dict[tuple[str, int], list[float]] = defaultdict(list)
    prev = None
    for r in conn.execute("SELECT ts, soc, generation_w, consumption_w FROM station_frame "
                          "WHERE station_id = ? ORDER BY ts", (station_id,)):
        ts, soc = r["ts"], r["soc"]
        if not soc and not r["consumption_w"] and not r["generation_w"]:
            soc = None  # empty frames before commissioning (logger online, nothing connected)
        local = datetime.fromtimestamp(ts, TZ)
        day = local.date().isoformat()
        last_ts = ts
        d = days.get(day)
        if d is None:
            prev = None
            continue
        if soc is not None:
            if soc > 100 or soc < 0:
                d["bad_soc"] += 1
            soc_acc[(day, local.hour)].append(soc)
            if d["soc_min"] is None or soc < d["soc_min"]:
                d["soc_min"], d["soc_min_ts"] = soc, ts
            d["soc_max"] = soc if d["soc_max"] is None else max(d["soc_max"], soc)
            if soc >= SOC_FULL and d["t_full"] is None:
                d["t_full"] = local.hour * 60 + local.minute
        if r["consumption_w"] is not None and r["consumption_w"] < 0:
            d["bad_load"] += 1
        if r["generation_w"] and r["generation_w"] > 200 and (local.hour < 4 or local.hour >= 22):
            d["pv_night"] += 1
        if prev is not None and soc is not None and prev[1] is not None and ts - prev[0] <= 30 * 60:
            dt_h = (ts - prev[0]) / 3600
            pd = days.get(local_day(prev[0]))
            if pd is not None:
                pd["h_soc"] += dt_h
                if prev[1] >= SOC_FULL:
                    pd["h_full"] += dt_h
                if prev[1] <= pd["soc_min_normal"]:
                    pd["h_low"] += dt_h
        prev = (ts, soc)
    m.last_station_ts = last_ts
    m.soc_grid = {k: sum(v) / len(v) for k, v in soc_acc.items()}

    # Days before commissioning (the logger was online, no PV and no battery)
    # are not days of the system: they would dilute the per-day averages, the plan and the profiles.
    started = [x for x in sorted(days) if days[x]["soc_max"] is not None or days[x]["pv"] > 0]
    for x in [x for x in days if started and x < started[0]]:
        del days[x]

    # Inverter points (Deye keeps their history ~90 days, so they start later than the station frames)
    metric_ids = {r["key"]: r["id"] for r in conn.execute("SELECT id, key FROM metric")}

    def series(key: str):
        mid = metric_ids.get(key)
        if mid is None:
            return []
        return conn.execute("SELECT ts, value FROM measurement WHERE metric_id = ? ORDER BY ts", (mid,)).fetchall()

    for key, name in (("DCPowerPV1", "pv1"), ("DCPowerPV2", "pv2")):
        prev = None
        for ts, w in series(key):
            if prev is not None and ts - prev[0] <= MAX_GAP_S and w is not None and prev[1] is not None:
                d = days.get(local_day(prev[0]))
                if d is not None:
                    d[name] = (d[name] or 0.0) + (prev[1] + w) / 2 * (ts - prev[0]) / 3_600_000
            prev = (ts, w)

    for key, name in (("Temperature- Battery", "temp_batt"), ("AC Temperature", "temp_inv")):
        acc: dict[str, list] = defaultdict(lambda: [None, 0.0, 0, 0.0, 0.0])  # max, sum, n, dh35, dh40
        prev = None
        for ts, t in series(key):
            if t is None:
                continue
            a = acc[local_day(ts)]
            a[0] = t if a[0] is None else max(a[0], t)
            a[1] += t
            a[2] += 1
            if prev is not None and ts - prev[0] <= MAX_GAP_S:
                dt_h = (ts - prev[0]) / 3600
                a[3] += max(prev[1] - 35, 0) * dt_h
                a[4] += max(prev[1] - 40, 0) * dt_h
            prev = (ts, t)
        for day, a in acc.items():
            d = days.get(day)
            if d is not None and a[2]:
                d[name + "_max"] = a[0]
                d[name + "_avg"] = a[1] / a[2]
                if name == "temp_batt":
                    d["temp_batt_dh35"], d["temp_batt_dh40"] = a[3], a[4]
    row = conn.execute("SELECT MAX(ts) FROM device_frame").fetchone()
    m.last_device_ts = row[0] if row else None
    m.alerts = conn.execute("SELECT COUNT(*) FROM alert").fetchone()[0]

    # Grid import by the utility meter (invoices): authoritative for money
    readings = [dict(r) for r in conn.execute(
        "SELECT r.*, g.issued FROM grid_invoice_reading r JOIN grid_invoice g USING (invoice_no) ORDER BY r.period_from")]
    m.invoices = [dict(r) for r in conn.execute("SELECT * FROM grid_invoice ORDER BY period_from")]

    # Spread the meter-vs-Deye import difference of each reading period over its days,
    # in proportion to the Deye import of that zone (evenly when Deye saw none). When the
    # utility meter shows more import than Deye (seen: even more than Deye's whole
    # consumption), the difference is consumption Deye does not see.
    # It is added to both import and consumption: the bill (G01) matches the meter and
    # the saving is unchanged.
    for r in readings:
        span = [days[x] for x in sorted(days) if r["period_from"] <= x <= r["period_to"]]
        if not span or len(span) < (date.fromisoformat(r["period_to"]) - date.fromisoformat(r["period_from"])).days + 1:
            continue  # the reading period starts before the PV data (meter includes pre-PV days)
        z = r["zone"]
        deye = sum(x["imp_" + z] for x in span)
        gap = (r["kwh"] or 0.0) - deye
        for x in span:
            share = x["imp_" + z] / deye if deye > 0 else 1 / len(span)
            x["meter_imp_" + z] = x["imp_" + z] + gap * share
            x["meter_load_" + z] = x["load_" + z] + gap * share
            x["metered"] = True

    for d in days.values():
        pd_, pn = d["price_day"] or 0.0, d["price_night"] or 0.0
        d["cost_without"] = d["meter_load_day"] * pd_ + d["meter_load_night"] * pn   # G02
        d["cost_with_deye"] = d["imp_day"] * pd_ + d["imp_night"] * pn
        d["cost_with"] = d["meter_imp_day"] * pd_ + d["meter_imp_night"] * pn         # G01
        d["revenue"] = d["exp"] * d["feed_in_price"]                                   # G03
        d["benefit"] = d["cost_without"] - d["cost_with"] + d["revenue"] - d["opex"]  # F01
        d["save_day"] = (d["meter_load_day"] - d["meter_imp_day"]) * pd_
        d["save_night"] = (d["meter_load_night"] - d["meter_imp_night"]) * pn
        d["cycles"] = d["dis"] / d["batt_kwh"] if d["batt_kwh"] else 0.0

    m.order = sorted(days)
    bench.enrich(conn, m)
    m.health = health.compute(conn, m)
    return m


_lock = threading.Lock()
_model: Model | None = None
MIN_REBUILD_S = 60


def model() -> Model:
    """The current model; rebuilt at most once a minute after the database changed."""
    global _model
    with _lock:
        if _model is None or (_model.version != db_version() and time.time() - _model.built_at > MIN_REBUILD_S):
            conn = connect()
            try:
                _model = build(conn)
            finally:
                conn.close()
        return _model


def invalidate() -> None:
    """Force a rebuild on the next request (after the dashboard changed a goal or parameter)."""
    global _model
    with _lock:
        _model = None


def capex_at(m: Model, day: str) -> float:
    return sum(c["amount_ccy"] or 0.0 for c in m.capex if c["date"] <= day)
