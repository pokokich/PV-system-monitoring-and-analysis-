"""Goals, their status over time, and the payback scenarios (docs/03-goals-and-analytics.md).

A goal is a KPI, a period window, a target with a direction (ge / le) and a warning
level. Its status at a date:
- the period is over: met / behind;
- the period is still running: the forecast for its end (the sums so far plus the
  seasonal profile for the remaining days) decides: on_track when it reaches the target,
  at_risk when it only reaches the warning level, otherwise behind;
- rolling 12 months and "since commissioning": the current value decides (met / at_risk / behind).

Forecasts use the configuration as it is now: days before an expansion (arrays or battery
modules added later) are adjusted by what the added parts would have done (see `adjusted_day`).
"""
from __future__ import annotations

import calendar
from datetime import date, timedelta

from .data import Model

# KPI id -> (name, unit, how the value comes from period sums, default direction)
GOAL_KPIS: dict[str, tuple[str, str, str]] = {
    "U02": ("Самозадоволяване", "%", "ge"),
    "U01": ("Самопотребление", "%", "ge"),
    "U03": ("Нощно покритие от батерията", "%", "ge"),
    "U06": ("Пропусната PV енергия (дял)", "%", "le"),
    "P04": ("Производство спрямо PVGIS", "%", "ge"),
    "P01": ("Специфичен добив", "kWh/kWp", "ge"),
    "E01": ("PV производство", "kWh", "ge"),
    "E03": ("Импорт от мрежата", "kWh", "le"),
    "B04": ("Ефективност на батерията (разряд / заряд)", "%", "ge"),
    "F01": ("Нетна полза", "€", "ge"),
    "F10": ("Нетна полза спрямо бизнес плана", "%", "ge"),
    "F04": ("Окупаемост (по сценарий)", "години", "le"),
}
PERIOD_TYPES = {"year": "календарна година", "month": "календарен месец", "rolling12": "последните 12 месеца",
                "ltd": "от пускането", "custom": "от–до"}
STATUS_LABEL = {"met": "изпълнена", "on_track": "в график", "at_risk": "риск", "behind": "изостава", "no_data": "няма данни"}

SUMS = ("pv", "exp", "load", "imp", "dis_night", "load_night", "chg", "dis", "curtailed", "exp_pvgis",
        "specific", "benefit", "plan")
BATTERY_EFF = 0.92


def _ratio(a, b):
    return a / b * 100 if b else None


def value_of(kpi: str, s: dict) -> float | None:
    if kpi == "U02":
        return _ratio(s["load"] - s["imp"], s["load"])
    if kpi == "U01":
        return _ratio(s["pv"] - s["exp"], s["pv"])
    if kpi == "U03":
        return _ratio(s["dis_night"], s["load_night"])
    if kpi == "U06":
        return _ratio(s["curtailed"], s["pv"] + s["curtailed"])
    if kpi == "P04":
        return _ratio(s["pv"], s["exp_pvgis"])
    if kpi == "P01":
        return s["specific"]
    if kpi == "E01":
        return s["pv"]
    if kpi == "E03":
        return s["imp"]
    if kpi == "B04":
        return _ratio(s["dis"], s["chg"])
    if kpi == "F01":
        return s["benefit"]
    if kpi == "F10":
        return _ratio(s["benefit"], s["plan"])
    return None


def day_sums(d: dict) -> dict:
    return {"pv": d["pv"], "exp": d["exp"], "load": d["load"], "imp": d["imp"], "dis_night": d["dis_night"],
            "load_night": d["load_night"], "chg": d["chg"], "dis": d["dis"], "curtailed": d["curtailed"],
            "exp_pvgis": d["exp_pvgis"], "specific": d["pv"] / d["kwp"] if d["kwp"] else 0.0,
            "benefit": d["benefit"], "plan": d["plan"] or 0.0}


def adjusted_day(d: dict, kwp_now: float, batt_now: tuple[float, float] = (0.0, 0.0)) -> dict:
    """A day as if the current arrays and batteries had been there (estimate for forecasts).

    The expected energy of the arrays added later (Open-Meteo irradiance × their calibrated PR,
    else PVGIS) first replaces daytime import; what is left, plus the energy that was curtailed,
    charges the battery capacity added later and replaces night import. The rest is curtailed.
    `batt_now` = (current pack kWh, current minimum SOC %).
    """
    s = day_sums(d)
    extra = d["exp_wx_missing"] if d.get("wx_ok") else max(d["exp_pvgis_now"] - d["exp_pvgis"], 0.0)
    if extra <= 0:
        s["exp_pvgis"] = d["exp_pvgis_now"]
        return s
    imp_day, imp_night = d["meter_imp_day"], d["meter_imp_night"]
    u_day = min(extra, imp_day)
    added_kwh = max(batt_now[0] - (d["batt_kwh"] or 0.0), 0.0) * (1 - batt_now[1] / 100)
    store = min((extra - u_day + d["curtailed"]) * BATTERY_EFF, added_kwh, imp_night)
    used = store / BATTERY_EFF                       # PV that charged the added modules
    from_extra = min(extra - u_day, used)
    pd_, pn = d["price_day"] or 0.0, d["price_night"] or 0.0
    s["pv"] += u_day + used
    s["curtailed"] = max(s["curtailed"] - (used - from_extra) + (extra - u_day - from_extra), 0.0)
    s["imp"] = max(s["imp"] - u_day - store, 0.0)
    s["dis_night"] += store
    s["chg"] += used
    s["dis"] += store
    s["benefit"] += u_day * pd_ + store * pn
    s["exp_pvgis"] = d["exp_pvgis_now"]
    s["specific"] = s["pv"] / kwp_now if kwp_now else 0.0
    return s


def _add(acc: dict, s: dict, w: float = 1.0) -> None:
    for k in SUMS:
        acc[k] = acc.get(k, 0.0) + (s.get(k) or 0.0) * w


def _zero() -> dict:
    return {k: 0.0 for k in SUMS}


class Forecaster:
    """Seasonal profile (mean adjusted day per calendar month) from the last 365 days before `as_of`."""

    def __init__(self, m: Model, as_of: str):
        self.m = m
        kwp_now = sum(a["panels"] * a["panel_wp"] / 1000 for a in m.arrays if a["valid_to"] is None)
        bat = [b for b in m.batteries if b["valid_to"] is None and b["modules"] and b["kwh_per_module"]]
        batt_now = (bat[-1]["modules"] * bat[-1]["kwh_per_module"], bat[-1]["soc_min_normal"] or 20.0) if bat else (0.0, 0.0)
        start = (date.fromisoformat(as_of) - timedelta(days=364)).isoformat()
        by_month: dict[int, list[dict]] = {}
        for day in m.order:
            if day < start or day > as_of:
                continue
            d = m.days[day]
            if d["covered_s"] < 0.8 * (d["expected_s"] or 86400) or d["soc_max"] is None:
                continue
            by_month.setdefault(int(day[5:7]), []).append(adjusted_day(d, kwp_now, batt_now))
        self.profile: dict[int, dict | None] = {}
        for mo, rows in by_month.items():
            acc = _zero()
            for r in rows:
                _add(acc, r, 1 / len(rows))
            self.profile[mo] = acc
        self.estimated = [mo for mo in range(1, 13) if mo not in self.profile]
        for mo in self.estimated:
            near = []
            for step in (1, -1):
                k = mo
                for _ in range(11):
                    k = (k - 1 + step) % 12 + 1
                    if k in by_month:
                        near.append(self.profile[k])
                        break
            if near:
                acc = _zero()
                for r in near:
                    _add(acc, r, 1 / len(near))
                self.profile[mo] = acc
            else:
                self.profile[mo] = None
        self.plan = m.bench.get("plan", {})     # month -> plan € (known, not forecast)

    def future(self, start: date, end: date) -> dict | None:
        acc = _zero()
        d = start
        while d <= end:
            p = self.profile.get(d.month)
            if p is None:
                return None
            _add(acc, p)
            acc["plan"] += self.plan.get(d.month, 0.0) / calendar.monthrange(d.year, d.month)[1] - p["plan"]
            d += timedelta(days=1)
        return acc

    def annual_benefit(self) -> list[float] | None:
        if any(self.profile.get(mo) is None for mo in range(1, 13)):
            return None
        return [self.profile[mo]["benefit"] for mo in range(1, 13)]


# ---------------------------------------------------------------- payback scenarios

def payback_scenario(m: Model, fc: Forecaster, as_of: str, sc: dict) -> dict:
    """Cumulative benefit walked forward day by day with price escalation, degradation and replacements."""
    first = m.order[0] if m.order else as_of
    commissioning = date.fromisoformat(next((c["date"] for c in m.capex), first))
    rows = [m.days[d] for d in m.order if d <= as_of]
    cum = sum(r["benefit"] for r in rows)
    capex_total = sum(c["amount_ccy"] or 0.0 for c in m.capex)
    out = {"name": sc["name"], "label": sc["label"], "price_escalation_pct": sc["price_escalation_pct"],
           "degradation_pct": sc["degradation_pct"], "cumulative": cum, "capex_total": capex_total,
           "annual": None, "years": None, "date": None, "discounted_date": None, "discounted_years": None,
           "forecast": [], "estimated_months": fc.estimated}
    profile = fc.annual_benefit()
    if profile is None or not rows:
        return out
    esc = (sc["price_escalation_pct"] or 0) / 100
    deg = (sc["degradation_pct"] or 0) / 100
    rate = (sc["discount_rate_pct"] or 0) / 100
    events = {}
    for kind in ("battery", "inverter"):
        y, eur = sc.get(f"{kind}_replacement_year"), sc.get(f"{kind}_replacement_ccy")
        if y and eur:
            d = commissioning.replace(year=commissioning.year + int(y))
            events[d] = events.get(d, 0.0) + eur
    out["annual"] = sum(profile[mo - 1] * calendar.monthrange(2027, mo)[1] for mo in range(1, 13))
    last = date.fromisoformat(rows[-1]["day"])
    # discounted cumulative so far (benefit of each past day discounted to commissioning)
    disc = sum(r["benefit"] / (1 + rate) ** (max((date.fromisoformat(r["day"]) - commissioning).days, 0) / 365.25)
               for r in rows)
    disc_capex = sum((c["amount_ccy"] or 0.0) / (1 + rate) ** (max((date.fromisoformat(c["date"]) - commissioning).days, 0) / 365.25)
                     for c in m.capex)
    total, day = cum, last
    points = [(day.isoformat(), round(total, 2))]
    for _ in range(366 * 40):
        day += timedelta(days=1)
        t_years = (day - last).days / 365.25
        age = (day - commissioning).days / 365.25
        b = profile[day.month - 1] * (1 + esc) ** t_years * (1 - deg) ** age
        b -= events.get(day, 0.0)
        total += b
        disc += b / (1 + rate) ** age
        if out["date"] is None and total >= capex_total:
            out["date"] = day.isoformat()
            out["years"] = (day - commissioning).days / 365.25
        if out["discounted_date"] is None and disc >= disc_capex:
            out["discounted_date"] = day.isoformat()
            out["discounted_years"] = (day - commissioning).days / 365.25
        drawing = out["date"] is None or total <= capex_total * 1.1
        if drawing and (day + timedelta(days=1)).day == 1:
            points.append((day.isoformat(), round(total, 2)))
        if not drawing and (out["discounted_date"] or rate == 0):
            break
    out["forecast"] = points
    return out


# ---------------------------------------------------------------- goal evaluation

def window(goal: dict, as_of: str, first: str) -> tuple[str, str, bool]:
    """(start, end, has_end) of the goal's period as seen on `as_of`."""
    a = date.fromisoformat(as_of)
    pt = goal["period_type"]
    if pt == "year":
        y = int(goal["period_start"][:4]) if goal.get("period_start") else a.year
        return max(f"{y}-01-01", first), f"{y}-12-31", True
    if pt == "month":
        ym = goal["period_start"][:7] if goal.get("period_start") else as_of[:7]
        y, mo = int(ym[:4]), int(ym[5:7])
        return max(f"{ym}-01", first), f"{ym}-{calendar.monthrange(y, mo)[1]:02d}", True
    if pt == "rolling12":
        return max((a - timedelta(days=364)).isoformat(), first), as_of, False
    if pt == "custom" and goal.get("period_start") and goal.get("period_end"):
        return max(goal["period_start"], first), goal["period_end"], True
    return first, as_of, False


def _good(x, goal, level):
    if x is None or level is None:
        return False
    return x >= level if goal["direction"] == "ge" else x <= level


def status_of(goal: dict, actual, forecast, ended: bool, has_end: bool) -> str:
    x = actual if (ended or not has_end) else forecast
    if x is None:
        return "no_data"
    if _good(x, goal, goal["target"]):
        return "met" if (ended or not has_end) and goal["kpi_id"] != "F04" else "on_track"
    warn = goal.get("warn_level")
    if warn is not None and _good(x, goal, warn) and not ended:
        return "at_risk"
    return "behind"


def evaluate(m: Model, goal: dict, as_of: str, scenarios: dict[str, dict], fc: Forecaster | None = None) -> dict:
    first = m.order[0] if m.order else as_of
    fc = fc or Forecaster(m, as_of)
    out = {"as_of": as_of}
    if goal["kpi_id"] == "F04":
        sc = scenarios.get(goal.get("scenario") or "base") or next(iter(scenarios.values()))
        pb = payback_scenario(m, fc, as_of, sc)
        out.update(start=first, end=None, actual=pb["years"], forecast=pb["years"], date=pb["date"],
                   scenario=sc["name"], has_end=False, ended=False, progress=_ratio(pb["cumulative"], pb["capex_total"]))
        out["status"] = status_of(goal, pb["years"], pb["years"], False, False)
        return out
    ws, we, has_end = window(goal, as_of, first)
    upto = min(we, as_of)
    acc = _zero()
    n = 0
    for day in m.order:
        if ws <= day <= upto:
            _add(acc, day_sums(m.days[day]))
            n += 1
    actual = value_of(goal["kpi_id"], acc) if n else None
    ended = has_end and we <= as_of
    forecast = actual
    if has_end and not ended and n:
        rest = fc.future(date.fromisoformat(upto) + timedelta(days=1), date.fromisoformat(we))
        if rest is not None:
            f = dict(acc)
            _add(f, rest)
            forecast = value_of(goal["kpi_id"], f)
        else:
            forecast = None
    out.update(start=ws, end=we if has_end else None, has_end=has_end, ended=ended, days=n,
               actual=actual, forecast=forecast, status=status_of(goal, actual, forecast, ended, has_end))
    return out


def month_ends(m: Model, as_of: str) -> list[str]:
    if not m.order:
        return []
    out = []
    d = date.fromisoformat(m.order[0])
    end = date.fromisoformat(as_of)
    while True:
        last = d.replace(day=calendar.monthrange(d.year, d.month)[1])
        if last >= end:
            break
        if (last - date.fromisoformat(m.order[0])).days >= 27:
            out.append(last.isoformat())
        d = last + timedelta(days=1)
    out.append(as_of)
    return out


def evaluate_all(m: Model, goals: list[dict], scenarios: dict[str, dict], as_of: str, history: bool = True) -> list[dict]:
    fc_now = Forecaster(m, as_of)
    forecasters = {as_of: fc_now}
    out = []
    for g in goals:
        meta = GOAL_KPIS.get(g["kpi_id"], (g["kpi_id"], "", "ge"))
        cur = evaluate(m, g, as_of, scenarios, fc_now)
        hist = []
        if history and g.get("active", 1):
            for a in month_ends(m, as_of):
                fc = forecasters.get(a) or forecasters.setdefault(a, Forecaster(m, a))
                e = evaluate(m, g, a, scenarios, fc)
                hist.append({"as_of": a, "actual": e["actual"], "forecast": e["forecast"], "status": e["status"],
                             "start": e.get("start"), "end": e.get("end")})
        out.append(dict(g, kpi_name=meta[0], unit=meta[1], period_label=PERIOD_TYPES.get(g["period_type"], g["period_type"]),
                        current=cur, status_label=STATUS_LABEL[cur["status"]], history=hist))
    return out
