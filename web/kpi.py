"""KPI registry and period aggregation (KPI codes from docs/02-kpi-catalog.md).

Every KPI is computed from the per-day model in web/data.py. A period is an inclusive
range of local dates; ratios are computed from the period sums, never averaged.
"""
from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta
from statistics import median

from .data import TZ, Model, capex_at

# id: (group, name, unit, description)
CATALOG: dict[str, tuple[str, str, str, str]] = {
    "E01": ("energy", "PV производство", "kWh", "Σ PV"),
    "E02": ("energy", "Консумация на дома", "kWh", "Σ товар"),
    "E03": ("energy", "Импорт от мрежата", "kWh", "Σ импорт (по Deye)"),
    "E04d": ("energy", "Импорт, дневна тарифа", "kWh", "Σ импорт в дневната зона"),
    "E04n": ("energy", "Импорт, нощна тарифа", "kWh", "Σ импорт в нощната зона"),
    "E05": ("energy", "Експорт към мрежата", "kWh", "Σ експорт"),
    "E06c": ("energy", "Заряд на батерията", "kWh", "Σ заряд"),
    "E06d": ("energy", "Разряд на батерията", "kWh", "Σ разряд"),
    "E07": ("energy", "Директно самопотребление", "kWh", "PV − заряд от PV − експорт, по 5-минутни интервали"),
    "U01": ("use", "Самопотребление", "%", "(PV − експорт) / PV"),
    "U02": ("use", "Самозадоволяване", "%", "(товар − импорт) / товар"),
    "U03": ("use", "Нощно покритие от батерията", "%", "разряд в нощната зона / товар в нощната зона"),
    "U06": ("use", "Пропусната PV енергия (ограничаване)", "kWh", "очаквано − реално PV в 5-минутните интервали с пълна батерия (SOC ≥ 98%) при нулев износ; оценка по облъчването от Open-Meteo"),
    "U06p": ("use", "Пропусната PV енергия, дял", "%", "U06 / (PV + U06)"),
    "U06v": ("use", "Стойност на пропуснатата енергия", "€", "U06 × цената „ако имаше договор за продажба“ (параметър goal_curtail_value_ccy_kwh)"),
    "U05": ("use", "Час на пълна батерия", "час", "медиана на първия час със SOC ≥ 99% (дни, в които е достигнат)"),
    "P01": ("pv", "Специфичен добив", "kWh/kWp", "Σ PV / kWp за всеки ден"),
    "P02r": ("pv", "Специфичен добив, PV1", "kWh/kWp", "DCPowerPV1 / kWp на масива на MPPT 1 (от инверторните данни)"),
    "P02c": ("pv", "Специфичен добив, PV2", "kWh/kWp", "DCPowerPV2 / kWp на масива на MPPT 2 (от инверторните данни)"),
    "P03": ("pv", "Performance Ratio", "%", "PV / (облъчване в равнината на всеки масив × kWp / 1 kW/m²); облъчване от Open-Meteo"),
    "P04": ("pv", "Производство спрямо PVGIS", "%", "PV / PVGIS очакване за масивите, работили в периода"),
    "P04r": ("pv", "PV1 спрямо PVGIS", "%", "DCPowerPV1 / PVGIS за масива на MPPT 1 (от инверторните данни)"),
    "P04c": ("pv", "PV2 спрямо PVGIS", "%", "DCPowerPV2 / PVGIS за масива на MPPT 2 (от инверторните данни; PVGIS не знае за местната сянка)"),
    "P04e": ("pv", "PVGIS очакване", "kWh", "Σ PVGIS месечно / дни в месеца за масивите, работили всеки ден"),
    "C02": ("pv", "Облъчване (хоризонтално)", "kWh/m²", "Open-Meteo shortwave_radiation"),
    "I04": ("inverter", "Температура на инвертора (AC), макс.", "°C", "само AC радиатор; DC няма в API"),
    "I04a": ("inverter", "Температура на инвертора (AC), средна", "°C", ""),
    "I06": ("inverter", "Наличност", "%", "време с данни / време в периода (без точка за статус)"),
    "I07": ("inverter", "Аларми", "бр.", "записи в таблица alert"),
    "B01min": ("battery", "SOC мин.", "%", "най-ниският отчетен SOC в периода (наблюдение, не настройка)"),
    "B01max": ("battery", "SOC макс.", "%", ""),
    "B02": ("battery", "Еквивалентни пълни цикли", "цикъла", "Σ разряд / капацитет на целия пакет към края на периода"),
    "B03": ("battery", "Пропусната енергия (разряд)", "kWh", "Σ разряд"),
    "B04": ("battery", "Разряд / заряд", "%", "Σ разряд / Σ заряд; горна граница на round-trip ефективността"),
    "B05": ("battery", "Температура на батерията, макс.", "°C", ""),
    "B05a": ("battery", "Температура на батерията, средна", "°C", ""),
    "B05h": ("battery", "Градусо-часове над 35 °C", "°C·ч", ""),
    "B06": ("battery", "Часове на 100% SOC", "ч/ден", "средно на ден с данни за SOC, SOC ≥ 99%"),
    "B07": ("battery", "Часове на минимума", "ч/ден", "средно на ден с данни за SOC, SOC ≤ минималния SOC от настройките за всеки ден"),
    "G01": ("money", "Разход за ток от мрежата", "€", "импорт × цена на зоната, с ДДС; импортът е по електромера, където има фактура"),
    "G02": ("money", "Сметка без PV", "€", "товар × цена на зоната, с ДДС"),
    "G03": ("money", "Приход от продажба", "€", "експорт × изкупна цена (няма договор: 0)"),
    "F01": ("money", "Нетна полза", "€", "G02 − G01 + G03 − O&M"),
    "F02": ("money", "Кумулативна нетна полза", "€", "Σ F01 от пускането до края на периода"),
    "F03": ("money", "Възвръщаемост до момента", "%", "F02 / CAPEX"),
    "F04": ("money", "Прогнозна проста окупаемост", "години", "CAPEX / годишна нетна полза по сезонния профил"),
    "F05": ("money", "Прогнозна дата на окупаемост", "дата", "кога F02 по прогнозата достига CAPEX"),
    "F10": ("money", "Нетна полза спрямо бизнес плана", "%", "F01 / план за периода (бенчмарк plan F01, по подразбиране старият модел от 11.2025)"),
    "D01": ("quality", "Пълнота на данните", "%", "интегрирано време / време в периода"),
    "D02": ("quality", "Замръзнали стойности", "интервала", "5-минутни интервали с PV от баланса (pv_estimated)"),
    "D03": ("quality", "Закъснение на данните", "мин", "сега − последен кадър"),
    "D05": ("quality", "Невъзможни стойности", "бр.", "SOC извън 0–100, отрицателен товар, PV > 200 W нощем"),
}

SUM_FIELDS = ("pv", "load", "imp", "exp", "chg", "dis", "imp_day", "imp_night", "load_day", "load_night",
              "dis_night", "pv_direct", "chg_pv", "chg_grid", "meter_imp_day", "meter_imp_night", "meter_load_day", "meter_load_night",
              "cost_without", "cost_with", "cost_with_deye", "revenue", "opex", "benefit",
              "save_day", "save_night", "cycles", "covered_s", "expected_s", "day_frac", "pv_estimated", "interp",
              "h_full", "h_low", "h_soc", "bad_soc", "bad_load", "pv_night",
              "exp_pvgis", "exp_wx", "curtailed", "poa_kwp", "ghi", "plan", "legacy_pv")


def ratio(a: float, b: float) -> float | None:
    return a / b * 100 if b else None


def aggregate(rows: list[dict]) -> dict:
    """Period sums and derived values for a list of day records."""
    s = {f: sum(r[f] or 0 for r in rows) for f in SUM_FIELDS}
    s["days"] = len(rows)
    s["start"] = rows[0]["day"] if rows else None
    s["end"] = rows[-1]["day"] if rows else None

    def pick(field, fn):
        vals = [r[field] for r in rows if r[field] is not None]
        return fn(vals) if vals else None

    s["soc_min"] = pick("soc_min", min)
    s["soc_max"] = pick("soc_max", max)
    # when the lowest SOC was seen and which minimum SOC settings were valid in the period
    low = min((r for r in rows if r["soc_min"] is not None), key=lambda r: (r["soc_min"], r["day"]), default=None)
    s["soc_min_day"] = low["day"] if low else None
    s["soc_min_ts"] = low["soc_min_ts"] if low else None
    s["soc_min_setting_then"] = low["soc_min_normal"] if low else None
    s["soc_min_settings"] = sorted({r["soc_min_normal"] for r in rows if r["h_soc"]}) or None
    s["soc_days"] = s["h_soc"] / 24
    s["pv_peak_w"] = pick("pv_peak_w", max)
    s["import_peak_w"] = pick("import_peak_w", max)
    s["temp_batt_max"] = pick("temp_batt_max", max)
    s["temp_inv_max"] = pick("temp_inv_max", max)
    s["temp_batt_avg"] = pick("temp_batt_avg", lambda v: sum(v) / len(v))
    s["temp_inv_avg"] = pick("temp_inv_avg", lambda v: sum(v) / len(v))
    s["temp_batt_dh35"] = pick("temp_batt_dh35", sum)
    s["temp_batt_dh40"] = pick("temp_batt_dh40", sum)
    s["t_full"] = pick("t_full", median)
    s["full_days"] = sum(1 for r in rows if r["t_full"] is not None)
    s["specific"] = sum(r["pv"] / r["kwp"] for r in rows if r["kwp"])
    s["pv1"] = pick("pv1", sum)
    s["pv2"] = pick("pv2", sum)
    s["specific1"] = sum(r["pv1"] / r["kwp1"] for r in rows if r["pv1"] is not None and r["kwp1"]) or None
    s["specific2"] = sum(r["pv2"] / r["kwp2"] for r in rows if r["pv2"] is not None and r["kwp2"]) or None
    s["string_days"] = sum(1 for r in rows if r["pv1"] is not None)
    for ch in (1, 2):
        rr = [r for r in rows if r[f"pv{ch}"] is not None and r.get(f"exp_pvgis{ch}")]
        s[f"p04_{ch}"] = ratio(sum(r[f"pv{ch}"] for r in rr), sum(r[f"exp_pvgis{ch}"] for r in rr)) if rr else None
    wx = [r for r in rows if r.get("wx_ok")]
    s["wx_days"] = len(wx)
    s["pr"] = ratio(sum(r["pv"] for r in wx), sum(r["poa_kwp"] for r in wx)) if wx else None
    # comparisons with a whole-day expectation (PVGIS, the plan) leave out today's unfinished day,
    # unless the period is only today
    whole = [r for r in rows if r["day_frac"] >= 0.999] or rows
    s["pvgis_ok"] = all(r.get("pvgis_ok", False) for r in whole) if whole else False
    s["p04"] = ratio(sum(r["pv"] for r in whole), sum(r["exp_pvgis"] for r in whole)) if s["pvgis_ok"] else None
    s["curtailed_share"] = ratio(s["curtailed"], s["pv"] + s["curtailed"]) if wx else None
    planned = [r for r in whole if r.get("plan") is not None]
    s["f10"] = ratio(sum(r["benefit"] for r in planned), sum(r["plan"] for r in planned)) if planned else None
    s["b04"] = ratio(s["dis"], s["chg"])
    s["kwp"] = rows[-1]["kwp"] if rows else None
    s["batt_kwh"] = rows[-1]["batt_kwh"] if rows else None
    # cycles of the whole pack (Dido, PR #16): all discharge / the pack at the end of the period
    s["pack_cycles"] = s["dis"] / s["batt_kwh"] if s["batt_kwh"] else None
    s["metered_days"] = sum(1 for r in rows if r["metered"])
    s["self_consumption"] = ratio(s["pv"] - s["exp"], s["pv"])
    s["self_sufficiency"] = ratio(s["load"] - s["imp"], s["load"])
    s["night_coverage"] = ratio(s["dis_night"], s["load_night"])
    s["coverage"] = ratio(s["covered_s"], s["expected_s"])
    return s


def payback(m: Model, as_of: str) -> dict:
    """Cumulative benefit to `as_of`, CAPEX and a seasonal forecast of the payback date.

    The forecast takes the mean daily benefit of each calendar month from the last 365
    days of data; a month without data takes the mean of its neighbours. It is based
    on the system as it has worked so far (before an expansion: without the added parts),
    so it is marked as conditional.
    """
    rows = [m.days[d] for d in m.order if d <= as_of]
    cum = sum(r["benefit"] for r in rows)
    capex = capex_at(m, as_of)
    capex_total = sum(c["amount_ccy"] or 0.0 for c in m.capex)
    if not rows:
        return {"cumulative": 0.0, "capex": capex, "capex_total": capex_total}
    last = date.fromisoformat(rows[-1]["day"])
    window_start = (last - timedelta(days=364)).isoformat()
    by_month: dict[int, list[float]] = {}
    for r in rows:
        if r["day"] >= window_start and r["covered_s"] >= 0.8 * (r["expected_s"] or 86400):
            by_month.setdefault(int(r["day"][5:7]), []).append(r["benefit"])
    profile: dict[int, float | None] = {k: sum(v) / len(v) for k, v in by_month.items()}
    for mo in range(1, 13):
        if mo not in profile:
            near = []
            for step in (1, -1):
                k = mo
                for _ in range(11):
                    k = (k - 1 + step) % 12 + 1
                    if k in profile and profile[k] is not None:
                        near.append(profile[k])
                        break
            profile[mo] = sum(near) / len(near) if near else None
    estimated_months = [mo for mo in range(1, 13) if mo not in by_month]
    # roi: against the CAPEX paid by `as_of` (the expansion only counts from its invoice)
    out = {"cumulative": cum, "capex": capex, "capex_total": capex_total,
           "roi": ratio(cum, capex), "profile": profile, "estimated_months": estimated_months,
           "annual": None, "years": None, "date": None, "forecast": []}
    if any(v is None for v in profile.values()):
        return out
    annual = sum(profile[mo] * calendar.monthrange(2027, mo)[1] for mo in range(1, 13))
    out["annual"] = annual
    if annual <= 0:
        return out
    out["years"] = capex_total / annual
    # walk forward day by day (40 years at most), keeping month ends for the chart
    total, day = cum, last
    points = [(day.isoformat(), round(total, 2))]
    for _ in range(366 * 40):
        day += timedelta(days=1)
        total += profile[day.month]
        if total >= capex_total:
            out["date"] = day.isoformat()
            points.append((day.isoformat(), round(total, 2)))
            break
        if (day + timedelta(days=1)).day == 1:
            points.append((day.isoformat(), round(total, 2)))
    out["forecast"] = points
    return out


def summary(m: Model, start: str, end: str, curtail_price: float | None = None) -> dict:
    rows = m.range(start, end)
    s = aggregate(rows)
    pb = payback(m, end)
    now = datetime.now(TZ).timestamp()
    latency = (now - m.last_station_ts) / 60 if m.last_station_ts else None

    def per_soc_day(x):
        return x / s["soc_days"] if s["soc_days"] else None

    values = {
        "E01": s["pv"], "E02": s["load"], "E03": s["imp"], "E04d": s["imp_day"], "E04n": s["imp_night"],
        "E05": s["exp"], "E06c": s["chg"], "E06d": s["dis"], "E07": s["pv_direct"],
        "U01": s["self_consumption"], "U02": s["self_sufficiency"], "U03": s["night_coverage"],
        "U05": s["t_full"] / 60 if s["t_full"] is not None else None,
        "P01": s["specific"], "P02r": s["specific1"], "P02c": s["specific2"],
        "I04": s["temp_inv_max"], "I04a": s["temp_inv_avg"], "I06": s["coverage"], "I07": m.alerts,
        "B01min": s["soc_min"], "B01max": s["soc_max"], "B02": s["pack_cycles"], "B03": s["dis"],
        "B05": s["temp_batt_max"], "B05a": s["temp_batt_avg"], "B05h": s["temp_batt_dh35"],
        "B06": per_soc_day(s["h_full"]), "B07": per_soc_day(s["h_low"]),
        "G01": s["cost_with"], "G02": s["cost_without"], "G03": s["revenue"], "F01": s["benefit"],
        "F02": pb["cumulative"], "F03": pb.get("roi"), "F04": pb.get("years"), "F05": pb.get("date"),
        "F10": s["f10"],
        "P03": s["pr"], "P04": s["p04"], "P04r": s["p04_1"], "P04c": s["p04_2"], "P04e": s["exp_pvgis"],
        "C02": s["ghi"] if s["wx_days"] else None, "B04": s["b04"],
        "U06": s["curtailed"] if s["wx_days"] else None, "U06p": s["curtailed_share"],
        "U06v": s["curtailed"] * curtail_price if s["wx_days"] and curtail_price is not None else None,
        "D01": s["coverage"], "D02": s["pv_estimated"], "D03": latency,
        "D05": s["bad_soc"] + s["bad_load"] + s["pv_night"],
    }
    kpis = {k: {"group": g, "name": n, "unit": u, "note": note, "value": values.get(k)}
            for k, (g, n, u, note) in CATALOG.items()}
    return {"start": start, "end": end, "days": s["days"], "kpis": kpis, "totals": s, "payback": pb}
