"""Health of the battery, the inverter, the PV arrays and the data, with a wear forecast.

Deye gives no BMS state of health and no per-module data, so everything here is derived
from what the collector stores: SOC and battery power (station frames, from 12.2025),
battery and inverter temperature (inverter frames, Deye keeps ~90 days of them), grid voltage and
the PV expectation from web/bench.py. The wear model is a simple semi-empirical LFP model
with editable parameters (param 'health_*'); it is an orientation, not a measurement:

    fade per day = calendar_fade/365 × g(age) × 2^((T − 25)/10) × (1 + high_soc_factor × share of time at SOC ≥ 90%)
                 + equivalent full cycles × (100 − EOL) / rated cycles

g(age) = sqrt(break_in / age) during the break-in (18 months), 1 afterwards: LFP loses capacity
faster at first (SEI layer growth, ~sqrt(t)) and then roughly linearly; the curve keeps its slope at
the end of the break-in. Fade is of the cells against their capacity at delivery. Packs ship with a
reserve above nominal, so the SOH against nominal (what the BMS and the Deye app show) is
min(100, (100 − fade) × (1 + reserve)) and stays at 100% until the reserve is used up.

Days without a battery temperature (before the inverter history starts) use the temperature estimated from
the outdoor temperature (Open-Meteo) by a linear fit over the days that have both.
"""
from __future__ import annotations

import calendar
from collections import defaultdict
from datetime import date, timedelta

BATT_BANDS = [(None, 15, "под 15 °C"), (15, 25, "15–25 °C"), (25, 30, "25–30 °C"), (30, 35, "30–35 °C"),
              (35, 40, "35–40 °C"), (40, None, "над 40 °C")]
INV_BANDS = [(None, 40, "под 40 °C"), (40, 45, "40–45 °C"), (45, 50, "45–50 °C"), (50, 55, "50–55 °C"),
             (55, 60, "55–60 °C"), (60, None, "над 60 °C")]
SOC_BANDS = ["full", "high", "mid", "min"]          # ≥ 99, 90–99, between, at the normal minimum (+1)
MAX_GAP_S = 15 * 60

DEFAULTS = {
    "health_batt_rated_cycles": 6000.0,      # Deye BOS-G datasheet: ≥ 6000 cycles at 25 °C, 0.5C, 90% DoD, to 70%
    "health_batt_eol_pct": 70.0,
    "health_batt_calendar_fade_pct": 1.0,    # %/year at 25 °C and medium SOC after the break-in (typical LFP)
    "health_batt_break_in_months": 18.0,     # faster, sqrt(t)-shaped fade before this age
    "health_batt_reserve_pct": 3.0,          # real capacity above nominal at delivery (estimate)
    "health_batt_high_soc_factor": 0.6,      # extra calendar ageing while SOC ≥ 90%
    "health_inv_design_life_years": 10.0,    # at the reference temperature
    "health_inv_ref_temp_c": 45.0,           # AC radiator temperature taken as "normal"
}


def _band(v, bands):
    for i, (lo, hi, _) in enumerate(bands):
        if (lo is None or v >= lo) and (hi is None or v < hi):
            return i
    return len(bands) - 1


def _arrhenius(t, ref=25.0):
    return 2 ** ((t - ref) / 10)


def _series(conn, metric_ids, key):
    mid = metric_ids.get(key)
    if mid is None:
        return []
    return conn.execute("SELECT ts, value FROM measurement WHERE metric_id = ? ORDER BY ts", (mid,)).fetchall()


def _fit(xs, ys):
    n = len(xs)
    if n < 10:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if not sxx:
        return None
    b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    a = my - b * mx
    res = (sum((y - a - b * x) ** 2 for x, y in zip(xs, ys)) / n) ** 0.5
    return {"a": a, "b": b, "rmse": res, "n": n}


def compute(conn, m) -> dict:
    from .data import local_day  # data imports this module

    params = dict(DEFAULTS)
    for r in conn.execute("SELECT key, value FROM param WHERE key LIKE 'health_%' ORDER BY valid_from"):
        if r["value"] is not None:
            params[r["key"]] = r["value"]
    days = m.days
    metric_ids = {r["key"]: r["id"] for r in conn.execute("SELECT id, key FROM metric")}
    month = lambda day: day[:7]  # noqa: E731
    months: dict[str, dict] = defaultdict(lambda: defaultdict(float))

    # ------------------------------------------------------------ SOC bands and C-rate (station frames, whole history)
    prev = None
    day_high: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])   # day -> [h at SOC ≥ 90, h with SOC]
    for ts, soc, gen, load, batt in conn.execute(
            "SELECT ts, soc, generation_w, consumption_w, battery_w FROM station_frame WHERE station_id = ? ORDER BY ts",
            (m.station_id,)):
        if not soc and not gen and not load:
            prev = None  # empty frames before commissioning
            continue
        if prev is not None and ts - prev[0] <= 30 * 60 and prev[1] is not None:
            day = local_day(prev[0])
            d = days.get(day)
            if d is not None:
                dt_h = (ts - prev[0]) / 3600
                mo = months[month(day)]
                s = prev[1]
                band = "full" if s >= 99 else "high" if s >= 90 else "min" if s <= d["soc_min_normal"] + 1 else "mid"
                mo[f"soc_{band}"] += dt_h
                mo["soc_h"] += dt_h
                day_high[day][1] += dt_h
                if s >= 90:
                    day_high[day][0] += dt_h
                cap = d["batt_kwh"] or 0
                if cap and prev[2] is not None:
                    c = abs(prev[2]) / 1000 / cap
                    if c > 0.25:
                        mo["c25_h"] += dt_h
                    if c > 0.5:
                        mo["c50_h"] += dt_h
                    mo["c_max"] = max(mo["c_max"], c)
        prev = (ts, soc, batt)

    # ------------------------------------------------------------ temperatures (inverter frames)
    batt_day: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])  # day -> [Σ T·dt, Σ dt]
    for key, bands, prefix in (("Temperature- Battery", BATT_BANDS, "bt"), ("AC Temperature", INV_BANDS, "it")):
        prev = None
        for ts, t in _series(conn, metric_ids, key):
            if t is None:
                continue
            if prev is not None and ts - prev[0] <= MAX_GAP_S:
                day = local_day(prev[0])
                dt_h = (ts - prev[0]) / 3600
                mo = months[month(day)]
                mo[f"{prefix}{_band(prev[1], bands)}"] += dt_h
                mo[f"{prefix}_h"] += dt_h
                mo[f"{prefix}_sum"] += prev[1] * dt_h
                mo[f"{prefix}_max"] = max(mo[f"{prefix}_max"], prev[1])
                if prefix == "bt":
                    batt_day[day][0] += prev[1] * dt_h
                    batt_day[day][1] += dt_h
                else:
                    mo["it_acc"] += _arrhenius(prev[1], params["health_inv_ref_temp_c"]) * dt_h
            prev = (ts, t)

    # inverter: temperature against output power per hour, daily maximum
    inv_hour: dict[int, list[float]] = defaultdict(lambda: [0.0, 0, 0.0, 0])
    for key, idx in (("AC Temperature", 0), ("TotalInverterOutputPower", 2)):
        for ts, v in _series(conn, metric_ids, key):
            if v is None:
                continue
            h = inv_hour[(ts // 3600) * 3600]
            h[idx] += v
            h[idx + 1] += 1
    scatter = []
    inv_daily_max: dict[str, float] = {}
    for hts, (ts_, nt, ps, np_) in sorted(inv_hour.items()):
        if nt and np_:
            t = ts_ / nt
            scatter.append([round(ps / np_ / 1000, 2), round(t, 1)])
            day = local_day(hts)
            inv_daily_max[day] = max(inv_daily_max.get(day, t), t)
    # true per-frame daily maximum
    for d in days.values():
        if d.get("temp_inv_max") is not None:
            inv_daily_max[d["day"]] = d["temp_inv_max"]

    # grid quality: outages (all three phases 0 V) and overvoltage (> 253 V, EN 50160 +10%)
    volts: dict[int, list] = defaultdict(lambda: [None, None, None])
    for i, key in enumerate(("GridVoltageL1", "GridVoltageL2", "GridVoltageL3")):
        for ts, v in _series(conn, metric_ids, key):
            volts[ts][i] = v
    outages, over = defaultdict(int), defaultdict(int)
    last_out = None
    for ts in sorted(volts):
        v = volts[ts]
        if any(x is None for x in v):
            continue
        day = local_day(ts)
        if all(x < 50 for x in v):
            if last_out is None or ts - last_out > 1800:
                outages[month(day)] += 1
            last_out = ts
        elif max(v) > 253:
            over[month(day)] += 1

    # clipping at the AC limit (I03): hours with the inverter output ≥ 97% of rated power
    rated = 15000.0
    for r in conn.execute("SELECT value FROM param WHERE key = 'inverter_rated_power_w' ORDER BY valid_from DESC LIMIT 1"):
        rated = r[0] or rated
    prev = None
    for ts, p in _series(conn, metric_ids, "TotalInverterOutputPower"):
        if prev is not None and ts - prev[0] <= MAX_GAP_S and prev[1] is not None and prev[1] >= 0.97 * rated:
            months[month(local_day(prev[0]))]["clip_h"] += (ts - prev[0]) / 3600
        prev = (ts, p)

    # ------------------------------------------------------------ battery temperature model from the outdoor temperature
    out_day: dict[str, list[float]] = defaultdict(lambda: [0.0, 0])
    for r in conn.execute("SELECT day, temp_c FROM weather_hourly WHERE temp_c IS NOT NULL"):
        out_day[r[0]][0] += r[1]
        out_day[r[0]][1] += 1
    pairs = [(out_day[d][0] / out_day[d][1], v[0] / v[1]) for d, v in batt_day.items()
             if v[1] >= 20 and out_day.get(d, [0, 0])[1] >= 20]
    fit = _fit([p[0] for p in pairs], [p[1] for p in pairs])

    def batt_temp(day: str) -> tuple[float | None, str]:
        v = batt_day.get(day)
        if v and v[1] >= 12:
            return v[0] / v[1], "measured"
        o = out_day.get(day)
        if fit and o and o[1] >= 20:
            return fit["a"] + fit["b"] * o[0] / o[1], "estimated"
        return None, "none"

    # ------------------------------------------------------------ usable capacity estimate (B09), per month and pack size
    cap: dict[tuple[str, float], list[float]] = defaultdict(lambda: [0.0, 0.0])
    change_days = {b["valid_from"] for b in m.batteries[1:]}   # the day modules were added mixes two pack sizes
    for ts, dis, chg, s0, s1 in conn.execute(
            "SELECT ts_start, discharge_kwh, charge_kwh, soc_start, soc_end FROM energy_5min WHERE station_id = ? "
            "AND discharge_kwh > 0 AND (charge_kwh IS NULL OR charge_kwh = 0) AND soc_start >= soc_end", (m.station_id,)):
        day = local_day(ts)
        d = days.get(day)
        if d is None or not d["batt_kwh"] or day in change_days:
            continue
        k = (month(day), d["batt_kwh"])
        cap[k][0] += dis
        cap[k][1] += s0 - s1
    capacity = [{"month": mo, "nominal": nom, "kwh": round(e / (ds / 100), 2), "pct": round(e / (ds / 100) / nom * 100, 1),
                 "soc_drop": round(ds)} for (mo, nom), (e, ds) in sorted(cap.items()) if ds >= 150]

    # ------------------------------------------------------------ wear model, day by day
    cal = params["health_batt_calendar_fade_pct"]
    commissioning = date.fromisoformat(m.order[0]) if m.order else None
    hsf = params["health_batt_high_soc_factor"]
    cyc_fade = (100 - params["health_batt_eol_pct"]) / params["health_batt_rated_cycles"]
    wear_days = []
    fade = 0.0
    for day in m.order:
        d = days[day]
        if d["soc_max"] is None:
            continue
        t, src = batt_temp(day)
        hi = day_high.get(day)
        share = hi[0] / hi[1] if hi and hi[1] else 0.0
        f_t = _arrhenius(t) if t is not None else 1.0
        c_base = cal / 365 * f_t * (1 + hsf * share)
        c_cal = c_base * _break_in(date.fromisoformat(day), commissioning, params)
        c_cyc = d["cycles"] * cyc_fade
        fade += c_cal + c_cyc
        mo = months[month(day)]
        mo["fade_cal"] += c_cal
        mo["fade_cyc"] += c_cyc
        mo["days"] += 1
        mo["dod_sum"] += (d["soc_max"] - d["soc_min"]) if d["soc_min"] is not None else 0
        mo["cycles"] += d["cycles"]
        mo["t_model_sum"] += t if t is not None else 0
        mo["t_model_n"] += 1 if t is not None else 0
        mo["t_est_days"] += 1 if src == "estimated" else 0
        wear_days.append({"day": day, "c_base": c_base, "c_cal": c_cal, "c_cyc": c_cyc, "soh": nominal_soh(100 - fade, params),
                          "cells": 100 - fade, "t": t, "src": src,
                          "share_high": share})
    cells_now = 100 - fade

    # forecast: the mean fade per calendar month of the last 365 days (calendar part without the
    # break-in, which is applied by age), repeated; "ideal" = the same cycles at 25 °C and without
    # time at high SOC (what the conditions cost)
    ideal_fade = sum(cal / 365 * _break_in(date.fromisoformat(w["day"]), commissioning, params) + w["c_cyc"]
                     for w in wear_days)
    app = conn.execute("SELECT valid_from, value FROM param WHERE key = 'health_batt_app_soh_pct' AND value IS NOT NULL "
                       "ORDER BY valid_from").fetchall()
    out_fc = {"soh_now": nominal_soh(cells_now, params), "cells_now": cells_now,
              "history": [(w["day"], round(w["soh"], 3)) for w in wear_days],
              "cells_history": [(w["day"], round(w["cells"], 3)) for w in wear_days],
              "app_readings": [(r[0], r[1]) for r in app],
              "actual": _project(wear_days, cells_now, commissioning, params, lambda w: (w["c_base"], w["c_cyc"])),
              "ideal": _project(wear_days, 100 - ideal_fade, commissioning, params, lambda w: (cal / 365, w["c_cyc"]))}

    # ------------------------------------------------------------ per-month table
    table = []
    for mo in sorted(months):
        x = months[mo]
        row = {"month": mo, "days": int(x["days"])}
        sh = x["soc_h"] or 0
        for b in SOC_BANDS:
            row[f"soc_{b}"] = round(x[f"soc_{b}"] / sh * 100, 1) if sh else None
        row["soc_full_h_day"] = round(x["soc_full"] / x["days"], 2) if x["days"] else None
        row["soc_min_h_day"] = round(x["soc_min"] / x["days"], 2) if x["days"] else None
        for prefix, bands in (("bt", BATT_BANDS), ("it", INV_BANDS)):
            h = x[f"{prefix}_h"]
            row[f"{prefix}_hours"] = round(h, 1)
            row[f"{prefix}_bands"] = [round(x[f"{prefix}{i}"], 1) for i in range(len(bands))] if h else None
            row[f"{prefix}_avg"] = round(x[f"{prefix}_sum"] / h, 1) if h else None
            row[f"{prefix}_max"] = round(x[f"{prefix}_max"], 1) if h else None
        row["bt_model_avg"] = round(x["t_model_sum"] / x["t_model_n"], 1) if x["t_model_n"] else None
        row["bt_estimated_days"] = int(x["t_est_days"])
        row["inv_ageing"] = round(x["it_acc"] / x["it_h"], 2) if x["it_h"] else None
        row["c25_h"] = round(x["c25_h"], 1)
        row["c50_h"] = round(x["c50_h"], 1)
        row["c_max"] = round(x["c_max"], 2)
        row["dod"] = round(x["dod_sum"] / x["days"], 1) if x["days"] else None
        row["cycles"] = round(x["cycles"], 1)
        row["fade_cal"] = round(x["fade_cal"], 3)
        row["fade_cyc"] = round(x["fade_cyc"], 3)
        row["outages"] = outages.get(mo, 0)
        row["overvoltage_frames"] = over.get(mo, 0)
        row["clip_h"] = round(x["clip_h"], 1)
        row["stress"] = stress_index(row)
        table.append(row)

    acc = sum(months[mo]["it_acc"] for mo in months)
    hrs = sum(months[mo]["it_h"] for mo in months)
    factor = acc / hrs if hrs else None
    inv_fc = {"ageing_factor": factor, "hours": hrs,
              "life_years": params["health_inv_design_life_years"] / factor if factor else None,
              "months_with_data": sum(1 for mo in months if months[mo]["it_h"])}
    return {"params": params, "months": table, "inverter_forecast": inv_fc, "capacity": capacity, "battery_forecast": out_fc,
            "temp_fit": fit, "inv_scatter": scatter[-24 * 120:], "inv_daily_max": sorted(inv_daily_max.items()),
            "bands": {"battery": [b[2] for b in BATT_BANDS], "inverter": [b[2] for b in INV_BANDS]}}


def _break_in(day: date, commissioning, params: dict) -> float:
    """Calendar-ageing multiplier by age: sqrt(break_in / age) before the end of the break-in, 1 after."""
    t0 = params["health_batt_break_in_months"] * 30.44
    if commissioning is None or t0 <= 0:
        return 1.0
    age = (day - commissioning).days + 0.5
    return (t0 / age) ** 0.5 if age < t0 else 1.0


def nominal_soh(cells: float, params: dict) -> float:
    """SOH against nominal capacity (as the BMS shows it) from the cells' remaining share of their delivered capacity."""
    return min(100.0, cells * (1 + params["health_batt_reserve_pct"] / 100))


def _project(wear_days: list[dict], cells_now: float, commissioning, params: dict, fade_of) -> dict:
    """fade_of(w) -> (calendar fade without the break-in, cycle fade) per day; SOH values are against nominal."""
    out = {"soh_now": nominal_soh(cells_now, params), "years_to_eol": None, "eol_date": None, "soh_10y": None,
           "fade_per_year": None, "fade_per_year_long": None, "points": []}
    if not wear_days:
        return out
    last = date.fromisoformat(wear_days[-1]["day"])
    start = (last - timedelta(days=364)).isoformat()
    prof: dict[int, list[tuple[float, float]]] = defaultdict(list)
    for w in wear_days:
        if w["day"] >= start:
            prof[int(w["day"][5:7])].append(fade_of(w))
    n_all = max(sum(len(v) for v in prof.values()), 1)
    mean_all = tuple(sum(x[i] for v in prof.values() for x in v) / n_all for i in (0, 1))
    per_day = {mo: (tuple(sum(x[i] for x in prof[mo]) / len(prof[mo]) for i in (0, 1)) if prof.get(mo) else mean_all)
               for mo in range(1, 13)}
    ten = commissioning.replace(year=commissioning.year + 10)
    cells, day = cells_now, last
    pts = [(day.isoformat(), round(nominal_soh(cells, params), 2))]
    year_ago = cells
    for i in range(366 * 30):
        day += timedelta(days=1)
        c_base, c_cyc = per_day[day.month]
        cells -= c_base * _break_in(day, commissioning, params) + c_cyc
        soh = nominal_soh(cells, params)
        if i == 364:
            out["fade_per_year"] = year_ago - cells   # the next 12 months, cells
        if (day + timedelta(days=1)).day == 1:
            pts.append((day.isoformat(), round(soh, 2)))
        if out["soh_10y"] is None and day >= ten:
            out["soh_10y"] = soh
        if out["eol_date"] is None and soh <= params["health_batt_eol_pct"]:
            out["eol_date"] = day.isoformat()
            out["years_to_eol"] = (day - commissioning).days / 365.25
            pts.append((day.isoformat(), round(soh, 2)))
        if out["eol_date"] and out["soh_10y"] is not None:
            break
    out["fade_per_year_long"] = sum((per_day[mo][0] + per_day[mo][1]) * calendar.monthrange(2027, mo)[1]
                                    for mo in range(1, 13))   # after the break-in
    out["points"] = pts
    return out


def stress_index(row: dict) -> dict | None:
    """Battery stress 0-100 (B13): time at high SOC, temperature, depth of discharge and C-rate.

    Each part is 0-100 against a "clearly harmful" level: half the time at SOC ≥ 99%,
    a mean temperature of 35 °C (twice the ageing rate of 25 °C), 100% daily DoD, and
    a fifth of the time above 0.25C. Weights 30 / 35 / 20 / 15.
    """
    if not row.get("days"):
        return None
    t = row["bt_avg"] if row.get("bt_avg") is not None else row.get("bt_model_avg")
    parts = {
        "soc": min((row["soc_full"] or 0) / 50, 1) * 100,
        "temp": min(max(_arrhenius(t) - 1, 0), 1) * 100 if t is not None else None,
        "dod": min((row["dod"] or 0) / 100, 1) * 100,
        "crate": min(row["c25_h"] / (row["days"] * 24 * 0.2), 1) * 100 if row["days"] else 0,
    }
    w = {"soc": 0.30, "temp": 0.35, "dod": 0.20, "crate": 0.15}
    have = {k: v for k, v in parts.items() if v is not None}
    total = sum(w[k] * v for k, v in have.items()) / sum(w[k] for k in have)
    return {"index": round(total, 1), **{k: (round(v, 1) if v is not None else None) for k, v in parts.items()},
            "temp_estimated": row.get("bt_avg") is None}


# ---------------------------------------------------------------- current status

LEVEL = {"good": 0, "watch": 1, "bad": 2, "no_data": -1}
LEVEL_LABEL = {"good": "добро", "watch": "за наблюдение", "bad": "проблем", "no_data": "няма данни"}


def _lvl(value, watch, bad, higher_is_worse=True):
    if value is None:
        return "no_data"
    if higher_is_worse:
        return "bad" if value >= bad else "watch" if value >= watch else "good"
    return "bad" if value <= bad else "watch" if value <= watch else "good"


def status(m, h: dict, now_ts: float) -> dict:
    """Current health per component, each check with its value, level and reason."""
    if not m.order or "months" not in h:   # a fresh install before the first data
        return {"as_of": None, "overall": "no_data", "overall_label": LEVEL_LABEL["no_data"], "components": {},
                "cycles": {"total": None, "per_year": None, "rated": None, "capacity_kwh": None}}
    last = m.order[-1] if m.order else None
    recent = [m.days[d] for d in m.order[-7:]] if m.order else []
    last30 = [m.days[d] for d in m.order[-30:]] if m.order else []
    months = h["months"]
    cur = months[-1] if months else {}
    prev_full = months[-2] if len(months) > 1 else cur

    def mx(rows, k):
        v = [r[k] for r in rows if r.get(k) is not None]
        return max(v) if v else None

    cap_last = h["capacity"][-1] if h["capacity"] else None
    fc = h["battery_forecast"]["actual"]
    stress = (cur.get("stress") or {}).get("index") if cur.get("days", 0) >= 10 else (prev_full.get("stress") or {}).get("index")
    battery = [
        {"name": "Макс. температура, 7 дни", "value": mx(recent, "temp_batt_max"), "unit": "°C",
         "level": _lvl(mx(recent, "temp_batt_max"), 40, 45), "rule": "за наблюдение от 40 °C, проблем от 45 °C"},
        {"name": "Използваем капацитет (оценка)", "value": cap_last["pct"] if cap_last else None, "unit": "%",
         "level": _lvl(cap_last["pct"] if cap_last else None, 95, 85, higher_is_worse=False),
         "rule": "по ΔSOC при разряд; за наблюдение под 95%, проблем под 85%"},
        {"name": "Индекс на натоварване", "value": stress, "unit": "от 100",
         "level": _lvl(stress, 40, 60), "rule": "за наблюдение от 40, проблем от 60"},
        {"name": "Здраве по модела, спрямо номинала", "value": fc["soh_now"], "unit": "%",
         "level": _lvl(fc["soh_now"], 90, 80, higher_is_worse=False),
         "rule": "както го показва BMS; за наблюдение под 90%, проблем под 80%"},
    ]
    app = h["battery_forecast"]["app_readings"]
    if app:
        battery.append({"name": f"SOH в приложението Deye ({app[-1][0][8:10]}.{app[-1][0][5:7]}.{app[-1][0][:4]})",
                        "value": app[-1][1], "unit": "%", "level": _lvl(app[-1][1], 90, 80, higher_is_worse=False),
                        "rule": "въведено ръчно на страницата „Здраве“"})
    inverter = [
        {"name": "Макс. температура (AC), 7 дни", "value": mx(recent, "temp_inv_max"), "unit": "°C",
         "level": _lvl(mx(recent, "temp_inv_max"), 60, 70), "rule": "за наблюдение от 60 °C, проблем от 70 °C"},
        {"name": "Прекъсвания на мрежата, този месец", "value": cur.get("outages"), "unit": "бр.",
         "level": _lvl(cur.get("outages"), 1, 5), "rule": "всички фази под 50 V"},
        {"name": "Кадри с напрежение над 253 V, този месец", "value": cur.get("overvoltage_frames"), "unit": "бр.",
         "level": _lvl(cur.get("overvoltage_frames"), 1, 20), "rule": "EN 50160: +10% от 230 V"},
    ]
    free_exp = sum(d.get("free_exp", 0) for d in last30)
    free_act = sum(d.get("free_act", 0) for d in last30)
    pv_ratio = free_act / free_exp * 100 if free_exp > 50 else None
    pv = [
        {"name": "Реално / очаквано при свободна батерия, 30 дни", "value": pv_ratio, "unit": "%",
         "level": _lvl(pv_ratio, 90, 75, higher_is_worse=False),
         "rule": "часовете без ограничаване; под 90% подсказва замърсяване, сняг или проблем в стринг"},
    ]
    cov = sum(d["covered_s"] for d in recent)
    exp = sum(d["expected_s"] for d in recent)
    latency = (now_ts - m.last_station_ts) / 60 if m.last_station_ts else None
    data = [
        {"name": "Закъснение на данните", "value": latency, "unit": "мин", "level": _lvl(latency, 30, 120),
         "rule": "за наблюдение от 30 мин, проблем от 2 часа"},
        {"name": "Пълнота, 7 дни", "value": cov / exp * 100 if exp else None, "unit": "%",
         "level": _lvl(cov / exp * 100 if exp else None, 97, 90, higher_is_worse=False), "rule": "за наблюдение под 97%"},
    ]
    comps = {"battery": ("Батерия", battery), "inverter": ("Инвертор и мрежа", inverter), "pv": ("Панели", pv),
             "data": ("Данни", data)}
    out = {}
    worst_all = "no_data"
    for key, (label, checks) in comps.items():
        levels = [c["level"] for c in checks if c["level"] != "no_data"]
        worst = max(levels, key=lambda x: LEVEL[x]) if levels else "no_data"
        for c in checks:
            c["level_label"] = LEVEL_LABEL[c["level"]]
            if isinstance(c["value"], float):
                c["value"] = round(c["value"], 1)
        out[key] = {"label": label, "level": worst, "level_label": LEVEL_LABEL[worst], "checks": checks}
        if LEVEL[worst] > LEVEL[worst_all]:
            worst_all = worst
    # whole pack: all discharged energy over today's capacity (modules added later count from the
    # start too; over the years the difference fades)
    cap_now = m.days[m.order[-1]]["batt_kwh"] if m.order else None
    dis = lambda days: sum(m.days[d]["dis"] or 0 for d in days)  # noqa: E731
    span = min(len(m.order), 365)
    cycles = {"total": dis(m.order) / cap_now if cap_now else None,
              "per_year": dis(m.order[-365:]) / cap_now / span * 365 if cap_now and span >= 30 else None,
              "rated": h["params"]["health_batt_rated_cycles"], "capacity_kwh": cap_now}
    return {"as_of": last, "overall": worst_all, "overall_label": LEVEL_LABEL[worst_all], "components": out,
            "cycles": cycles}
