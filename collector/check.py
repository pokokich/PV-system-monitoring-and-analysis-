"""Compare calculated energy with Deye's daily totals and the inverter's Daily* counters.

    python3 -m collector.main check [--days N]
"""
from __future__ import annotations

import os
from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo

from . import appconf
from .db import connect
from .energy import local_day_bounds
from .tariffs import zone
from .deye_client import load_env

# Deye's own daily/monthly totals up to 26.01.2026 are 10 times too small (station and
# inverter alike, e.g. 1.9 kWh PV on 10.12.2025 vs 18.9 kWh from power); the lifetime
# counters agree with the power integral, so those days are left out of the statistics.
# Deye's daily totals of some stations are wrong for their first weeks; the setting
# `deye_totals_reliable_from` (YYYY-MM-DD) leaves those days out of the summary.


PAIRS = [("pv", "PV"), ("load", "товар"), ("import", "импорт"), ("charge", "заряд"), ("discharge", "разряд")]
COUNTERS = {
    "DailyActiveProduction": "pv_kwh",
    "DailyConsumption": "load_kwh",
    "DailyEnergyPurchased": "import_kwh",
    "DailyChargingEnergy": "charge_kwh",
    "DailyDischargingEnergy": "discharge_kwh",
}

LIFETIME = [
    ("TotalActiveProduction", "pv_kwh", "PV"),
    ("TotalConsumption", "load_kwh", "товар"),
    ("TotalEnergyBuy", "import_kwh", "импорт"),
    ("TotalChargeEnergy", "charge_kwh", "заряд"),
    ("TotalDischargeEnergy", "discharge_kwh", "разряд"),
]


def daily_rows(conn, days: int):
    sql = "SELECT * FROM v_daily_check ORDER BY day"
    rows = conn.execute(sql).fetchall()
    return rows[-days:] if days else rows


def hourly_counter_check(conn, tz: ZoneInfo):
    """For each local hour end that has an inverter frame with Daily* counters, compare the
    counter with the calculated energy since local midnight. Returns {column: [abs diff]}."""
    metric_ids = {r["key"]: r["id"] for r in conn.execute("SELECT id, key FROM metric")}
    diffs: dict[str, list[float]] = defaultdict(list)
    for key, col in COUNTERS.items():
        mid = metric_ids.get(key)
        if mid is None:
            continue
        # last counter reading in each local hour
        by_hour: dict[int, tuple[int, float]] = {}
        for ts, value in conn.execute("SELECT ts, value FROM measurement WHERE metric_id = ? ORDER BY ts", (mid,)):
            local = datetime.fromtimestamp(ts, tz)
            if local.minute < 50:            # keep readings close to the hour end
                continue
            by_hour[int(local.replace(minute=0, second=0).timestamp())] = (ts, value)
        for _, (ts, value) in by_hour.items():
            day_start = int(datetime.fromtimestamp(ts, tz).replace(hour=0, minute=0, second=0).timestamp())
            calc = conn.execute(
                f"SELECT sum({col}), sum(ts_end - ts_start) FROM energy_5min WHERE ts_start >= ? AND ts_end <= ?",
                (day_start, ts),
            ).fetchone()
            if calc[0] is None or calc[1] < (ts - day_start) * 0.95:
                continue
            diffs[col].append(abs(calc[0] - value))
    return diffs


def meter_check(conn, tz: ZoneInfo):
    windows = appconf.night_windows(conn)
    """Utility meter (invoice readings) vs grid import integrated from Deye frames, per zone."""
    first = conn.execute("SELECT min(ts) FROM station_frame").fetchone()[0]
    if first is None:
        return []
    out = []
    rows = conn.execute(
        "SELECT period_from, period_to, sum(CASE WHEN zone = 'day' THEN kwh END) d, "
        "sum(CASE WHEN zone = 'night' THEN kwh END) n FROM grid_invoice_reading "
        "GROUP BY period_from, period_to ORDER BY period_from").fetchall()
    for r in rows:
        start = local_day_bounds(r["period_from"], tz)[0]
        end = local_day_bounds(r["period_to"], tz)[1]
        if start < first:
            continue
        calc = {"day": 0.0, "night": 0.0}
        covered = 0
        for ts, ts_end, kwh in conn.execute(
                "SELECT ts_start, ts_end, import_kwh FROM energy_5min WHERE ts_start >= ? AND ts_start < ?",
                (start, end)):
            calc[zone(datetime.fromtimestamp(ts, tz), windows)] += kwh
            covered += ts_end - ts
        out.append((r["period_from"], r["period_to"], r["d"], r["n"], calc["day"], calc["night"],
                    covered / (end - start)))
    return out


def main(days: int = 0) -> None:
    load_env()
    conn = connect(os.environ.get("DB_PATH", "./data/pv.sqlite"))
    tz = appconf.timezone(conn)
    reliable_from = appconf.get(conn, "deye_totals_reliable_from", "0000-00-00")
    rows = daily_rows(conn, days)
    print("ден         покр.  " + "  ".join(f"{label:>17}" for _, label in PAIRS))
    print(" " * 19 + "  ".join(f"{'изч./Deye/инв.':>17}" for _ in PAIRS))
    stats: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        cells = []
        for key, _ in PAIRS:
            calc, deye = r[f"{key}_kwh"], r[f"deye_{key}_kwh"]
            inv = r["inv_pv_kwh"] if key == "pv" else None
            cells.append(f"{_f(calc):>5}/{_f(deye):>5}/{_f(inv):>5}")
            if (calc is not None and deye is not None and r["covered_s"] >= 82800
                    and r["day"] >= reliable_from):
                stats[key].append(calc - deye)
        print(f"{r['day']}  {(r['covered_s'] or 0) / 864:4.0f}%  " + "  ".join(cells))

    print(f"\nОтклонение изчислено − Deye за дни с пълно покритие от {reliable_from} (kWh/ден):")
    for key, label in PAIRS:
        d = stats[key]
        if d:
            print(f"  {label:8} дни {len(d):3}  средно {sum(d) / len(d):+6.2f}  "
                  f"средно абс. {sum(abs(x) for x in d) / len(d):5.2f}  макс. абс. {max(abs(x) for x in d):5.2f}")

    print("\nСверка на общите броячи на инвертора от началото (последен кадър):")
    for key, col, label in LIFETIME:
        row = conn.execute(
            "SELECT m.ts, m.value FROM measurement m JOIN metric k ON k.id = m.metric_id "
            "WHERE k.key = ? ORDER BY m.ts DESC LIMIT 1", (key,)).fetchone()
        if row:
            calc = conn.execute(f"SELECT sum({col}) FROM energy_5min WHERE ts_end <= ?", (row[0],)).fetchone()[0] or 0
            print(f"  {label:8} брояч {row[1]:8.0f}  изчислено {calc:8.0f}  разлика {(calc - row[1]) / row[1] * 100:+5.1f}%")

    print("\nЕлектромер (фактури) срещу импорта от Deye, kWh ден/нощ:")
    for p_from, p_to, md, mn, cd, cn, cov in meter_check(conn, tz):
        print(f"  {p_from}..{p_to}  електромер {md:6.0f} / {mn:6.0f}   Deye {cd:6.1f} / {cn:6.1f}   "
              f"покритие {cov * 100:3.0f}%")

    print("\nСверка по часове с дневните броячи на инвертора (резолюция 1 kWh):")
    for col, d in hourly_counter_check(conn, tz).items():
        if d:
            within = sum(1 for x in d if x <= 1.5) / len(d) * 100
            print(f"  {col:14} часове {len(d):5}  средно абс. {sum(d) / len(d):4.2f} kWh  "
                  f"до 1.5 kWh: {within:5.1f}%")


def _f(x) -> str:
    return "-" if x is None else f"{x:.1f}"


if __name__ == "__main__":
    import sys
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 0)
