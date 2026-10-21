"""Benchmarks on the per-day model: PVGIS expectation (P04), irradiance (C02), performance
ratio (P03) and the energy lost to curtailment when the battery is full (U06).

How the curtailment is estimated:
1. For every hour, the expected AC energy of each array = kWp × irradiance in its plane
   (Open-Meteo) / 1000 × PR(array, local hour). The PR per array and hour of day is
   calibrated on hours in which the battery was not full (SOC < 95%), so nothing was
   curtailed; for a shaded array the hourly PR also captures the shading.
2. Each 5-minute interval that starts and ends with SOC ≥ 98% is "battery full": the
   inverter in zero-export mode limits PV to the load. The lost energy is the expected
   energy of that interval (the hour's expectation / 12) minus the real PV, summed per day
   (positive and negative differences cancel out; a negative day total counts as 0).
"""
from __future__ import annotations

import calendar
from collections import defaultdict
from datetime import date, datetime

SOC_FULL = 98.0
SOC_FREE = 95.0
MIN_POA_WM2 = 100.0
MIN_CAL_KWH = 20.0          # expected kWh needed before an hour-of-day PR is trusted


def _in(a: dict, day: str) -> bool:
    return a["valid_from"] <= day and (a["valid_to"] is None or day < a["valid_to"])


def _kwp(a: dict) -> float:
    return a["panels"] * a["panel_wp"] / 1000


def enrich(conn, m) -> None:
    """Adds PVGIS, weather, PR and curtailment fields to every day record and m.bench."""
    from .data import TZ  # local import: data imports this module

    days = m.days
    arrays = m.arrays
    pvgis = {(r["array_id"], r["month"]): r for r in conn.execute("SELECT * FROM pvgis_monthly")}
    params = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM param ORDER BY valid_from")}
    ac_limit_kwh = (params.get("inverter_rated_power_w") or 15000) / 1000  # per hour
    current = [a for a in arrays if a["valid_to"] is None]
    monthly: dict[tuple[str, str], dict[int, float]] = defaultdict(dict)   # (kind, kpi) -> month -> value
    for r in conn.execute("SELECT kind, kpi_id, period, value FROM benchmark WHERE value IS NOT NULL"):
        if len(r["period"]) == 2 and r["period"].isdigit():
            monthly[(r["kind"], r["kpi_id"])][int(r["period"])] = r["value"]
    plan = monthly.get(("plan", "F01"), {})
    legacy_pv = monthly.get(("legacy_model", "E01"), {})
    m.bench = {"plan": dict(plan), "legacy_pv": dict(legacy_pv)}

    for day, d in days.items():
        dt = date.fromisoformat(day)
        dim = calendar.monthrange(dt.year, dt.month)[1]
        d.update(exp_pvgis=0.0, exp_pvgis_now=0.0, exp_pvgis1=None, exp_pvgis2=None, ghi=None, poa_kwp=0.0,
                 exp_wx=0.0, exp_wx_missing=0.0, free_exp=0.0, free_act=0.0, curtailed=0.0, wx_hours=0, wx_ok=False, pvgis_ok=True,
                 plan=plan[dt.month] / dim if dt.month in plan else None,
                 legacy_pv=legacy_pv[dt.month] / dim if dt.month in legacy_pv else None)
        for a in arrays:
            p = pvgis.get((a["id"], dt.month))
            active = _in(a, day)
            if p is None:
                if active:
                    d["pvgis_ok"] = False
                continue
            e = _kwp(a) * p["kwh_per_kwp"] / dim
            if active:
                d["exp_pvgis"] += e
                if a["mppt_channel"] in (1, 2):
                    d[f"exp_pvgis{a['mppt_channel']}"] = e
            if a in current:
                d["exp_pvgis_now"] += e
        if not any(_in(a, day) for a in arrays):
            d["pvgis_ok"] = False

    # ------------------------------------------------------------ weather per hour
    ghi: dict[int, float] = {}
    for r in conn.execute("SELECT hour_ts, ghi_wm2 FROM weather_hourly"):
        ghi[r[0]] = r[1]
    poa: dict[tuple[int, int], float] = {}
    for r in conn.execute("SELECT hour_ts, array_id, gti_wm2 FROM poa_hourly"):
        poa[(r[0], r[1])] = r[2]
    for ts, g in ghi.items():
        d = days.get(datetime.fromtimestamp(ts, TZ).date().isoformat())
        if d is not None and g is not None:
            d["ghi"] = (d["ghi"] or 0.0) + g / 1000
    m.bench.update(weather_days=0, pr={}, pvgis_months=len(pvgis))
    if not poa or m.station_id is None:
        return

    # ------------------------------------------------------------ per-string energy per hour (inverter frames)
    metric_ids = {r["key"]: r["id"] for r in conn.execute("SELECT id, key FROM metric")}
    string_h: dict[int, dict[int, float]] = {1: defaultdict(float), 2: defaultdict(float)}
    for ch in (1, 2):
        mid = metric_ids.get(f"DCPowerPV{ch}")
        if mid is None:
            continue
        prev = None
        for ts, w in conn.execute("SELECT ts, value FROM measurement WHERE metric_id = ? ORDER BY ts", (mid,)):
            if prev is not None and w is not None and prev[1] is not None and ts - prev[0] <= 900:
                string_h[ch][(prev[0] // 3600) * 3600] += (prev[1] + w) / 2 * (ts - prev[0]) / 3_600_000
            prev = (ts, w)

    # ------------------------------------------------------------ hourly energy and 5-minute "battery full" intervals
    hours = {r["hour_ts"]: dict(r) for r in conn.execute(
        "SELECT hour_ts, day, hour, pv_kwh, soc_max, covered_s FROM energy_hourly WHERE station_id = ?", (m.station_id,))}
    full_iv: dict[int, list[float]] = defaultdict(list)     # hour -> pv of the full intervals
    for ts, pv, s0, s1 in conn.execute(
            "SELECT ts_start, pv_kwh, soc_start, soc_end FROM energy_5min WHERE station_id = ? AND soc_start >= ? "
            "AND soc_end >= ? AND quality != 'pv_estimated'", (m.station_id, SOC_FULL, SOC_FULL)):
        full_iv[(ts // 3600) * 3600].append(pv or 0.0)

    by_array = {a["id"]: a for a in arrays}

    def array_ac(h: dict) -> dict[int, float] | None:
        """Real AC energy of each array in the hour (split by the DC strings when known)."""
        act = [a for a in arrays if _in(a, h["day"])]
        pv = h["pv_kwh"] or 0.0
        if len(act) == 1:
            return {act[0]["id"]: pv}
        dc = {a["id"]: string_h.get(a["mppt_channel"], {}).get(h["hour_ts"]) for a in act}
        if any(v is None for v in dc.values()):
            return None
        tot = sum(dc.values())
        return {k: (pv * v / tot if tot > 0 else 0.0) for k, v in dc.items()}

    # calibration: PR per array and local hour on hours with a free battery
    num: dict[tuple[int, int], float] = defaultdict(float)
    den: dict[tuple[int, int], float] = defaultdict(float)
    for ts, h in hours.items():
        if (h["soc_max"] is None or h["soc_max"] >= SOC_FREE or (h["covered_s"] or 0) < 3000
                or (h["pv_kwh"] or 0) >= 0.95 * ac_limit_kwh):
            continue
        split = array_ac(h)
        if split is None:
            continue
        for aid, real in split.items():
            g = poa.get((ts, aid))
            if g is None or g < MIN_POA_WM2:
                continue
            ideal = _kwp(by_array[aid]) * g / 1000
            num[(aid, h["hour"])] += real
            den[(aid, h["hour"])] += ideal
            num[(aid, -1)] += real
            den[(aid, -1)] += ideal
    pr: dict[tuple[int, int], float] = {}
    for (aid, hr), dd in den.items():
        overall = num[(aid, -1)] / den[(aid, -1)] if den[(aid, -1)] else None
        if hr == -1:
            pr[(aid, -1)] = overall
        elif overall is not None:
            # shrink toward the array's overall PR while an hour has little data
            w = min(dd / MIN_CAL_KWH, 1.0)
            pr[(aid, hr)] = w * (num[(aid, hr)] / dd) + (1 - w) * overall
    m.bench["pr"] = {by_array[aid]["name"]: round(v, 3) for (aid, hr), v in pr.items() if hr == -1 and v}
    m.bench["pr_hourly"] = {by_array[aid]["name"]: {hr: round(v, 3) for (a2, hr), v in pr.items() if a2 == aid and hr >= 0}
                            for aid in by_array}

    # expectation per hour, curtailment per day
    signed: dict[str, float] = defaultdict(float)
    wx_days = set()
    for ts, h in hours.items():
        d = days.get(h["day"])
        if d is None:
            continue
        exp = ideal = 0.0
        ok = True
        for a in arrays:
            if not _in(a, h["day"]):
                continue
            g = poa.get((ts, a["id"]))
            p = pr.get((a["id"], h["hour"])) or pr.get((a["id"], -1))
            if g is None or p is None:
                ok = False
                continue
            exp += _kwp(a) * g / 1000 * p
            ideal += _kwp(a) * g / 1000
        for a in current:
            if _in(a, h["day"]):
                continue
            g = poa.get((ts, a["id"]))
            p = pr.get((a["id"], h["hour"])) or pr.get((a["id"], -1))
            if g is not None and p is not None:
                d["exp_wx_missing"] += _kwp(a) * g / 1000 * p
        if not ok:
            continue
        d["poa_kwp"] += ideal
        exp = min(exp, ac_limit_kwh)
        d["exp_wx"] += exp
        d["wx_hours"] += 1
        if h["soc_max"] is not None and h["soc_max"] < SOC_FREE and (h["covered_s"] or 0) >= 3000:
            d["free_exp"] += exp          # hours nothing was curtailed: PV health check
            d["free_act"] += h["pv_kwh"] or 0.0
        wx_days.add(h["day"])
        for pv in full_iv.get(ts, ()):
            signed[h["day"]] += exp / 12 - pv
    for day, v in signed.items():
        days[day]["curtailed"] = max(v, 0.0)
    for day in wx_days:
        d = days[day]
        d["wx_ok"] = d["wx_hours"] >= 23
    m.bench["weather_days"] = len(wx_days)
