"""5-minute energy from station power frames, rolled up to local hours and days.

The inverter's kWh counters have 1 kWh resolution, so short-interval energy is the
trapezoid integral of power between adjacent frames. Counters are only used to check
hourly/daily sums (see docs/deye-datapoints.md, section 5).
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

OK_GAP_S = 420         # a regular step (Deye frames are 300 s apart)
MAX_GAP_S = 1800       # up to this gap the trapezoid is still used, marked "interp"
STUCK_FRAMES = 12      # the same non-zero PV power for an hour or more is a frozen value
STUCK_MIN_W = 1000     # (Deye repeated 4850 W for 28 h from 10.09.2026 10:10, also at night)


def repair_stuck_generation(frames: list[dict]) -> None:
    """Replace a frozen generationPower with the energy balance load - grid - battery.

    Real PV power never repeats exactly for an hour at kW level, while the other
    channels keep moving. Marks repaired frames with pv_estimated = True.
    """
    i = 0
    while i < len(frames):
        j = i
        while j + 1 < len(frames) and frames[j + 1]["generation_w"] == frames[i]["generation_w"]:
            j += 1
        g = frames[i]["generation_w"]
        if g is not None and g >= STUCK_MIN_W and j - i + 1 >= STUCK_FRAMES:
            for f in frames[i:j + 1]:
                est = (f["consumption_w"] or 0) - (f["wire_w"] or 0) - (f["battery_w"] or 0)
                f["generation_w"] = max(0.0, est)
                f["pv_estimated"] = True
        i = j + 1


def _pos(x: float | None) -> float:
    return x if x and x > 0 else 0.0


def _neg(x: float | None) -> float:
    return -x if x and x < 0 else 0.0


def interval_energy(f0: sqlite3.Row | dict, f1: sqlite3.Row | dict) -> dict:
    """kWh between two station frames. Signs: wire > 0 import, battery > 0 discharge."""
    hours = (f1["ts"] - f0["ts"]) / 3600.0

    def trap(fn, key):
        return (fn(f0[key]) + fn(f1[key])) / 2 * hours / 1000.0

    return {
        "pv_kwh": trap(_pos, "generation_w"),
        "load_kwh": trap(_pos, "consumption_w"),
        "import_kwh": trap(_pos, "wire_w"),
        "export_kwh": trap(_neg, "wire_w"),
        "charge_kwh": trap(_neg, "battery_w"),
        "discharge_kwh": trap(_pos, "battery_w"),
    }


def local_day_bounds(day: str, tz: ZoneInfo) -> tuple[int, int]:
    d = datetime.fromisoformat(day).replace(tzinfo=tz)
    return int(d.timestamp()), int((d + timedelta(days=1)).timestamp())


def rebuild(conn: sqlite3.Connection, station_id: int, start_ts: int, end_ts: int, tz: ZoneInfo) -> None:
    """Recompute energy_5min for intervals starting in [start_ts, end_ts) and the
    hourly/daily rows of every local day those intervals touch."""
    first_day = datetime.fromtimestamp(start_ts, tz).date().isoformat()
    last_day = datetime.fromtimestamp(end_ts - 1, tz).date().isoformat()
    start_ts = local_day_bounds(first_day, tz)[0]
    end_ts = local_day_bounds(last_day, tz)[1]

    frames = [dict(r) for r in conn.execute(
        "SELECT * FROM station_frame WHERE station_id = ? AND ts >= ? AND ts <= "
        "(SELECT coalesce(min(ts), ?) FROM station_frame WHERE station_id = ? AND ts >= ?) ORDER BY ts",
        (station_id, start_ts - STUCK_FRAMES * 300, end_ts, station_id, end_ts),
    )]
    repair_stuck_generation(frames)
    frames = [f for f in frames if f["ts"] >= start_ts]
    conn.execute("DELETE FROM energy_5min WHERE station_id = ? AND ts_start >= ? AND ts_start < ?",
                 (station_id, start_ts, end_ts))
    rows = []
    for f0, f1 in zip(frames, frames[1:]):
        if f0["ts"] >= end_ts:
            break
        dt = f1["ts"] - f0["ts"]
        if dt <= 0 or dt > MAX_GAP_S:
            continue
        e = interval_energy(f0, f1)
        rows.append((station_id, f0["ts"], f1["ts"], e["pv_kwh"], e["load_kwh"], e["import_kwh"],
                     e["export_kwh"], e["charge_kwh"], e["discharge_kwh"], f0["soc"], f1["soc"],
                     "pv_estimated" if f0.get("pv_estimated") or f1.get("pv_estimated")
                     else "ok" if dt <= OK_GAP_S else "interp"))
    conn.executemany("INSERT INTO energy_5min VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)

    day = datetime.fromisoformat(first_day).date()
    while day.isoformat() <= last_day:
        _rollup_day(conn, station_id, day.isoformat(), tz)
        day += timedelta(days=1)


def _rollup_day(conn: sqlite3.Connection, station_id: int, day: str, tz: ZoneInfo) -> None:
    d0, d1 = local_day_bounds(day, tz)
    conn.execute("DELETE FROM energy_hourly WHERE station_id = ? AND day = ?", (station_id, day))
    conn.execute("DELETE FROM energy_daily WHERE station_id = ? AND day = ?", (station_id, day))
    intervals = conn.execute(
        "SELECT * FROM energy_5min WHERE station_id = ? AND ts_start >= ? AND ts_start < ? ORDER BY ts_start",
        (station_id, d0, d1),
    ).fetchall()
    frames = conn.execute(
        "SELECT count(*) n, max(generation_w) pv_peak, max(wire_w) imp_peak, min(soc) soc_min, max(soc) soc_max "
        "FROM station_frame WHERE station_id = ? AND ts >= ? AND ts < ?",
        (station_id, d0, d1),
    ).fetchone()
    if not intervals and not frames["n"]:
        return

    keys = ("pv_kwh", "load_kwh", "import_kwh", "export_kwh", "charge_kwh", "discharge_kwh")
    hours: dict[int, dict] = {}
    for r in intervals:
        local = datetime.fromtimestamp(r["ts_start"], tz)
        hour_ts = int(local.replace(minute=0, second=0, microsecond=0).timestamp())
        h = hours.setdefault(hour_ts, {"hour": local.hour, **{k: 0.0 for k in keys},
                                       "soc": [], "covered_s": 0})
        for k in keys:
            h[k] += r[k]
        h["soc"] += [s for s in (r["soc_start"], r["soc_end"]) if s is not None]
        h["covered_s"] += r["ts_end"] - r["ts_start"]
    conn.executemany(
        "INSERT INTO energy_hourly VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [(station_id, hts, day, h["hour"], *(h[k] for k in keys),
          min(h["soc"], default=None), max(h["soc"], default=None), h["covered_s"])
         for hts, h in sorted(hours.items())],
    )
    totals = {k: sum(h[k] for h in hours.values()) for k in keys}
    conn.execute(
        "INSERT INTO energy_daily VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (station_id, day, *(totals[k] for k in keys), frames["pv_peak"], frames["imp_peak"],
         frames["soc_min"], frames["soc_max"], frames["n"], sum(h["covered_s"] for h in hours.values())),
    )
