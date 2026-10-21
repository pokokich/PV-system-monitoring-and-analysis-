"""Turn Deye responses into rows. Every write is an upsert, so re-running is safe."""
from __future__ import annotations

import json
import sqlite3
import time

from .db import MetricCache

STATION_FIELDS = {
    "generationPower": "generation_w",
    "consumptionPower": "consumption_w",
    "purchasePower": "purchase_w",
    "wirePower": "wire_w",
    "chargePower": "charge_w",
    "dischargePower": "discharge_w",
    "batteryPower": "battery_w",
    "batterySOC": "soc",
}

# Deye daily/monthly totals (/station/history granularity 2/3) -> our columns
STATION_TOTALS = {
    "generationValue": "generation_kwh",
    "consumptionValue": "consumption_kwh",
    "purchaseValue": "purchase_kwh",
    "gridValue": "export_kwh",
    "chargeValue": "charge_kwh",
    "dischargeValue": "discharge_kwh",
    "fullPowerHours": "full_power_hours",
}

# Inverter daily totals (/device/history granularity 2)
INVERTER_TOTALS = {
    "Production": "generation_kwh",
    "Consumption": "consumption_kwh",
    "ElectricityPurchasing": "purchase_kwh",
    "GridFeed-in": "export_kwh",
    "ChargingCapacity": "charge_kwh",
    "DischargingCapacity": "discharge_kwh",
}


def _num(value) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def upsert_station_frames(conn: sqlite3.Connection, station_id: int, items: list[dict], source: str) -> int:
    """Store 5-minute station frames; returns the number of frames seen."""
    cols = list(STATION_FIELDS.values())
    sql = (f"INSERT INTO station_frame (station_id, ts, {', '.join(cols)}, source) "
           f"VALUES (?, ?, {', '.join('?' for _ in cols)}, ?) "
           f"ON CONFLICT(station_id, ts) DO UPDATE SET "
           + ", ".join(f"{c} = coalesce(excluded.{c}, {c})" for c in cols))
    rows = []
    for item in items:
        ts = item.get("timeStamp")
        if ts is None:
            continue
        rows.append((station_id, int(ts), *(_num(item.get(k)) for k in STATION_FIELDS), source))
    conn.executemany(sql, rows)
    return len(rows)


def upsert_deye_daily(conn: sqlite3.Connection, station_id: int, items: list[dict]) -> int:
    now = int(time.time())
    n = 0
    for item in items:
        if not item.get("day"):
            continue
        day = f"{item['year']:04d}-{item['month']:02d}-{item['day']:02d}"
        _upsert_totals(conn, "deye_daily", ("station_id", "day", "source"), (station_id, day, "station"),
                       {col: _num(item.get(k)) for k, col in STATION_TOTALS.items()}, now)
        n += 1
    return n


def upsert_deye_monthly(conn: sqlite3.Connection, station_id: int, items: list[dict]) -> int:
    now = int(time.time())
    for item in items:
        month = f"{item['year']:04d}-{item['month']:02d}"
        _upsert_totals(conn, "deye_monthly", ("station_id", "month"), (station_id, month),
                       {col: _num(item.get(k)) for k, col in STATION_TOTALS.items()}, now)
    return len(items)


def upsert_inverter_daily(conn: sqlite3.Connection, station_id: int, data_list: list[dict]) -> int:
    now = int(time.time())
    for entry in data_list:
        values = {INVERTER_TOTALS[i["key"]]: _num(i.get("value"))
                  for i in entry.get("itemList", []) if i.get("key") in INVERTER_TOTALS}
        _upsert_totals(conn, "deye_daily", ("station_id", "day", "source"),
                       (station_id, entry["time"], "inverter"), values, now)
    return len(data_list)


def _upsert_totals(conn, table, key_cols, key_vals, values: dict, now: int) -> None:
    cols = list(key_cols) + list(values) + ["fetched_at"]
    conn.execute(
        f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)}) "
        f"ON CONFLICT({', '.join(key_cols)}) DO UPDATE SET "
        + ", ".join(f"{c} = excluded.{c}" for c in list(values) + ["fetched_at"]),
        (*key_vals, *values.values(), now),
    )


def upsert_device(conn: sqlite3.Connection, item: dict) -> None:
    conn.execute(
        "INSERT INTO device (sn, deye_device_id, station_id, type, product_id, first_seen) VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(sn) DO UPDATE SET deye_device_id = excluded.deye_device_id, station_id = excluded.station_id, "
        "type = excluded.type, product_id = excluded.product_id",
        (item["deviceSn"], item.get("deviceId"), item.get("stationId"), item.get("deviceType"),
         item.get("productId"), int(time.time())),
    )


def store_device_frame(conn: sqlite3.Connection, metrics: MetricCache, device_sn: str, ts: int,
                       points: list[dict], source: str, merge_window: int = 0) -> bool:
    """Store one inverter frame. Returns False if it was already stored.

    `merge_window` > 0 attaches history points to an existing frame within that many
    seconds instead of creating a near-duplicate frame (live and history timestamps of
    the same logger upload can differ by a few seconds).
    """
    if merge_window:
        row = conn.execute(
            "SELECT ts FROM device_frame WHERE device_sn = ? AND ts BETWEEN ? AND ? ORDER BY abs(ts - ?) LIMIT 1",
            (device_sn, ts - merge_window, ts + merge_window, ts),
        ).fetchone()
        if row:
            ts = row[0]
    cur = conn.execute(
        "INSERT INTO device_frame (device_sn, ts, source, fetched_at) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(device_sn, ts) DO NOTHING",
        (device_sn, ts, source, int(time.time())),
    )
    is_new = cur.rowcount == 1
    numeric, text = [], []
    for p in points:
        key = p.get("key")
        if not key:
            continue
        mid = metrics.id(key, p.get("unit"))
        value = _num(p.get("value"))
        if value is not None:
            numeric.append((device_sn, ts, mid, value))
        elif p.get("value") not in (None, ""):
            text.append((device_sn, mid, str(p["value"]), ts, ts))
    conn.executemany("INSERT OR IGNORE INTO measurement VALUES (?, ?, ?, ?)", numeric)
    conn.executemany(
        "INSERT INTO device_text VALUES (?, ?, ?, ?, ?) ON CONFLICT(device_sn, metric_id, value) DO UPDATE SET "
        "first_seen_ts = min(first_seen_ts, excluded.first_seen_ts), last_seen_ts = max(last_seen_ts, excluded.last_seen_ts)",
        text,
    )
    return is_new


SETTINGS_TITLES = {"battery": "Настройки на батерията", "system": "Режим на работа", "tou": "Часови зони (TOU)"}


def store_device_settings(conn: sqlite3.Connection, device_sn: str, kind: str, response: dict) -> bool:
    """Keep a settings snapshot if it differs from the last one; log the change. Returns True if changed."""
    settings = {k: v for k, v in response.items() if k not in ("code", "msg", "success", "requestId")}
    text = json.dumps(settings, ensure_ascii=False, sort_keys=True)
    last = conn.execute(
        "SELECT settings_json FROM device_settings WHERE device_sn = ? AND kind = ? ORDER BY fetched_at DESC LIMIT 1",
        (device_sn, kind),
    ).fetchone()
    if last and last[0] == text:
        return False
    now = int(time.time())
    conn.execute("INSERT INTO device_settings VALUES (?, ?, ?, ?)", (device_sn, kind, now, text))
    title = SETTINGS_TITLES.get(kind, kind)
    conn.execute(
        "INSERT INTO event_log (ts, kind, title, details, source) VALUES (?, 'setting_change', ?, ?, 'deye_config')",
        (now, f"{title}: {'първо четене' if last is None else 'промяна'}", text),
    )
    return True
