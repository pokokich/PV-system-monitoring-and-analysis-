"""Analysis page: findings and recommendations per component and for the system as a whole.

Every finding is generated from the database by a rule with a threshold, and carries the
numbers and the period behind it, so the text follows the data. Three bases are used:

- the typical year with the current system: the seasonal profile of the goals forecaster
  (web/goals.py, last 365 days, days before the expansion adjusted to today's arrays and
  batteries), for yearly figures and the levers;
- the measured period since the expansion (09.07.2026) for what the system does today;
- the inverter points (from 22.07.2026) for strings and temperatures.

Levers ("what would change the result") are estimates; each one names its assumption and
who can act on it. The dashboard never changes anything on the inverter: a lever that
needs a Deye setting is for the owner to apply.
"""
from __future__ import annotations

import calendar
import copy
from collections import defaultdict
from datetime import date, timedelta
from statistics import median

from . import goals, health

LEVEL_LABEL = health.LEVEL_LABEL
LEVEL = health.LEVEL
EV_HOUR_KWH = 3.0
DHW_KWH_DAY = 6.0           # hot water for a household, kWh of heat a day (estimate)           # a night hour with at least this load is taken as car charging
BATTERY_EFF = goals.BATTERY_EFF
NIGHT_HOURS = set(range(22, 24)) | set(range(0, 7))
DIM = {mo: calendar.monthrange(2027, mo)[1] for mo in range(1, 13)}
MONTHS_BG = ["яну", "фев", "мар", "апр", "май", "юни", "юли", "авг", "сеп", "окт", "ное", "дек"]


# ---------------------------------------------------------------- formatting

def n(v, d: int = 0) -> str:
    """Bulgarian number format: 12 345,6."""
    if v is None:
        return "–"
    s = f"{v:,.{d}f}".replace(",", " ").replace(".", ",")
    return s


def dmy(iso: str | None) -> str:
    if not iso:
        return "–"
    y, m, *d = iso.split("-")
    return f"{d[0]}.{m}.{y}" if d else f"{m}.{y}"


def worst(levels) -> str:
    lv = [x for x in levels if x and x != "no_data"]
    return max(lv, key=lambda x: LEVEL[x]) if lv else "no_data"


def lvl(value, watch, bad, higher_is_worse=True) -> str:
    return health._lvl(value, watch, bad, higher_is_worse)


def ev(label: str, value, unit: str = "", d: int = 0, period: str | None = None) -> dict:
    return {"label": label, "value": n(value, d) if isinstance(value, (int, float)) else (value or "–"),
            "unit": unit, "period": period}


def finding(level: str, title: str, text: str, evidence: list[dict] | None = None) -> dict:
    return {"level": level, "level_label": LEVEL_LABEL[level], "title": title, "text": text, "evidence": evidence or []}


# ---------------------------------------------------------------- inputs not in the day model

def hourly_profile(conn, station_id: int, start: str, end: str) -> dict:
    """Base load per calendar month: median hourly load between 1 and 5 h without car charging."""
    days: dict[str, set] = defaultdict(set)
    base: dict[str, list[float]] = defaultdict(list)
    for r in conn.execute("SELECT day, hour, load_kwh FROM energy_hourly WHERE station_id = ? AND day BETWEEN ? AND ? "
                          "AND covered_s >= 2700", (station_id, start, end)):
        load = r["load_kwh"] or 0.0
        days[r["day"][:7]].add(r["day"])
        if 1 <= r["hour"] <= 5 and load < EV_HOUR_KWH:
            base[r["day"][:7]].append(load)
    return {"months": {mo: {"days": len(ds), "base_kw": median(base[mo]) if base[mo] else None} for mo, ds in days.items()}}


def ev_hours(conn, station_id: int, start: str, end: str) -> dict[str, tuple[float, int]]:
    """Per month: energy of the night hours that look like car charging and their count."""
    out: dict[str, list] = defaultdict(lambda: [0.0, 0])
    for r in conn.execute("SELECT day, hour, load_kwh FROM energy_hourly WHERE station_id = ? AND day BETWEEN ? AND ? "
                          "AND covered_s >= 2700 AND load_kwh >= ?", (station_id, start, end, EV_HOUR_KWH)):
        if r["hour"] in NIGHT_HOURS:
            out[r["day"][:7]][0] += r["load_kwh"]
            out[r["day"][:7]][1] += 1
    return {k: (v[0], v[1]) for k, v in out.items()}


# ---------------------------------------------------------------- payback what-if

def payback_with(m, fc, as_of: str, sc: dict, extra_per_day: dict[int, float] | None = None,
                 no_replacements: bool = False) -> dict:
    """The scenario's payback with extra benefit € per day for each calendar month (a lever)."""
    f = copy.copy(fc)
    f.profile = {mo: dict(p) if p else p for mo, p in fc.profile.items()}
    for mo, extra in (extra_per_day or {}).items():
        if f.profile.get(mo):
            f.profile[mo]["benefit"] += extra
    s = dict(sc)
    if no_replacements:
        s.update(battery_replacement_ccy=None, inverter_replacement_ccy=None)
    return goals.payback_scenario(m, f, as_of, s)


# ---------------------------------------------------------------- the analysis

def build(m, conn, scenarios: dict[str, dict], goal_rows: list[dict], curtail_price: float | None, now_ts: float) -> dict:
    if not m.order:
        return {"as_of": None, "sections": [], "levers": [], "overall": None, "year": []}
    last = m.order[-1]
    d_last = m.days[last]
    kwp = d_last["kwp"]
    cap = d_last["batt_kwh"]
    usable = cap * (1 - d_last["soc_min_normal"] / 100)
    p_day, p_night = d_last["price_day"] or 0.0, d_last["price_night"] or 0.0
    expansion = next((d for d in m.order if m.days[d]["kwp"] == kwp and m.days[d]["batt_kwh"] == cap), m.order[0])
    post = [m.days[d] for d in m.order if d >= expansion]
    strings = [m.days[d] for d in m.order if m.days[d]["pv1"] is not None]
    year_start = max((date.fromisoformat(last) - timedelta(days=364)).isoformat(), m.order[0])
    last365 = [m.days[d] for d in m.order if d >= year_start]
    h = m.health
    hs = health.status(m, h, now_ts)

    fc = goals.Forecaster(m, last)
    prof = fc.profile
    have_year = all(prof.get(mo) for mo in range(1, 13))
    Y = {k: sum(prof[mo][k] * DIM[mo] for mo in range(1, 13)) for k in goals.SUMS} if have_year else None

    def per_month(key):
        return {mo: prof[mo][key] for mo in range(1, 13)} if have_year else {}

    sum_ = lambda rows, k: sum(r[k] or 0.0 for r in rows)  # noqa: E731
    span_post = f"от {dmy(expansion)}"
    span_str = f"от {dmy(strings[0]['day'])}" if strings else None
    span_year = "типична година с текущата система"

    sections = []
    levers = []

    # ============================================================ panels
    pv_find = []
    free_exp = sum_(post, "free_exp")
    free_act = sum_(post, "free_act")
    free_ratio = free_act / free_exp * 100 if free_exp > 50 else None
    pr = m.bench.get("pr") or {}
    pr_vals = {k: v * 100 for k, v in pr.items() if v}
    pr_min = min(pr_vals.values()) if pr_vals else None
    lv_hw = worst([lvl(free_ratio, 90, 75, False), lvl(pr_min, 72, 65, False)])
    pv_find.append(finding(
        lv_hw, "Панелите произвеждат колкото трябва, когато има къде да отиде енергията",
        (f"В часовете, в които батерията не е пълна (нищо не се губи), реалното производство е {n(free_ratio)}% от очакваното "
         f"по облъчването от Open-Meteo. Performance Ratio в тези часове е "
         + ", ".join(f"{k.lower()} {n(v)}%" for k, v in pr_vals.items())
         + ". Над 75% е нормално за масив с инвертор и кабели; под 90% от очакваното би подсказало замърсяване, сняг или проблем в стринг.")
        if free_ratio is not None else "Няма достатъчно часове със свободна батерия и облъчване, за да се прецени.",
        [ev("Реално / очаквано при свободна батерия", free_ratio, "%", 0, span_post)]
        + [ev(f"PR, {k.lower()}", v, "%", 0, "часове със свободна батерия") for k, v in pr_vals.items()]))
    if strings and strings[-1]["kwp1"] and strings[-1]["kwp2"]:
        s1 = sum(r["pv1"] / r["kwp1"] for r in strings if r["kwp1"])
        s2 = sum(r["pv2"] / r["kwp2"] for r in strings if r["pv2"] is not None and r["kwp2"])
        ratio = s2 / s1 * 100 if s1 else None
        rr = [r for r in strings if r.get("exp_pvgis1") and r.get("exp_pvgis2")]
        g1 = sum(r["exp_pvgis1"] for r in rr) / strings[-1]["kwp1"] if rr else None
        g2 = sum(r["exp_pvgis2"] for r in rr) / strings[-1]["kwp2"] if rr else None
        exp_ratio = g2 / g1 * 100 if g1 else None
        gap = (exp_ratio - ratio) if (exp_ratio is not None and ratio is not None) else None
        pv_find.append(finding(
            lvl(gap, 10, 20), "Навесът дава по-малко на киловат от покрива",
            (f"От {dmy(strings[0]['day'])} навесът е произвел {n(s2)} kWh/kWp, покривът {n(s1)} kWh/kWp: навесът дава {n(ratio)}% от покрива. "
             f"По PVGIS за същите дни се очаква {n(exp_ratio)}% (по-малък наклон и югозападна посока, 225° е допускане). "
             + (f"Разликата от {n(gap)} пункта е следобедната сянка; заслужава проверка на място, ако расте." if gap and gap > 3 else
                "Разликата е в рамките на очакваното за посоката и наклона.")
             + " И двата масива са еднакво засегнати от ограничаването, затова сравнението между тях остава честно."),
            [ev("Специфичен добив, покрив", s1, "kWh/kWp", 0, span_str), ev("Специфичен добив, навес", s2, "kWh/kWp", 0, span_str),
             ev("Навес спрямо покрив", ratio, "%", 0, span_str), ev("Очаквано по PVGIS", exp_ratio, "%", 0, span_str)]))
    if Y:
        potential = Y["pv"] + Y["curtailed"]
        util = Y["pv"] / potential * 100 if potential else None
        pv_find.append(finding(
            lvl(100 - util if util is not None else None, 15, 30), "Голяма част от възможното производство не се използва",
            (f"За типична година с текущата система панелите могат да дадат около {n(potential, 0)} kWh, а реално се ползват {n(Y['pv'])} kWh "
             f"({n(util)}%). Останалите {n(Y['curtailed'])} kWh се губят, защото при нулев износ инверторът намалява мощността, "
             f"щом батерията се напълни (медианно в {_hhmm(_median_full(post))}). Това не е повреда на панелите, а въпрос на това "
             f"къде да отиде енергията (виж „Какво би променило резултата“)."),
            [ev("Възможно производство", potential, "kWh/год", 0, span_year), ev("Използвано", Y["pv"], "kWh/год", 0, span_year),
             ev("Пропуснато (ограничаване)", Y["curtailed"], "kWh/год", 0, span_year),
             ev("Специфичен добив, използван", Y["pv"] / kwp, "kWh/kWp", 0, span_year),
             ev("Специфичен добив, възможен", potential / kwp, "kWh/kWp", 0, span_year),
             ev("PVGIS очакване", Y["exp_pvgis"] / kwp, "kWh/kWp", 0, "година")]))
    sections.append({"key": "pv", "title": "Панели (покрив и навес)", "level": lv_hw, "findings": pv_find,
                     "summary": "Хардуерът е в норма; ограничението идва от режима без износ." if LEVEL.get(lv_hw, -1) <= 0
                     else "Има отклонение в производството, което заслужава проверка."})

    # ============================================================ inverter
    inv_find = []
    months = h["months"]
    it_hot = sum((x["it_bands"] or [0] * 6)[4] + (x["it_bands"] or [0] * 6)[5] for x in months)
    it_hours = sum(x["it_hours"] or 0 for x in months)
    t_inv_max = max((r["temp_inv_max"] for r in strings if r["temp_inv_max"] is not None), default=None)
    t_inv_avg = _mean([r["temp_inv_avg"] for r in strings])
    clip = sum(x["clip_h"] or 0 for x in months)
    outages = sum(x["outages"] or 0 for x in months)
    over = sum(x["overvoltage_frames"] or 0 for x in months)
    pv_peak = max((r["pv_peak_w"] for r in post if r["pv_peak_w"]), default=None)
    inv_fc = h["inverter_forecast"]
    lv_t = lvl(t_inv_max, 60, 70)
    inv_find.append(finding(
        lv_t, "Температурата на инвертора е в нормата",
        (f"Най-високата AC температура от {dmy(strings[0]['day']) if strings else '–'} е {n(t_inv_max)} °C при средна {n(t_inv_avg)} °C; "
         f"над 55 °C е бил {n(it_hot)} ч от {n(it_hours)} ч с данни. По модела на здравето това отговаря на около "
         f"{n(inv_fc['life_years'], 1)} години живот при проектни {n(h['params']['health_inv_design_life_years'])} години при 45 °C "
         "(ориентир, Deye не дава DC температура)."),
        [ev("Макс. температура (AC)", t_inv_max, "°C", 0, span_str), ev("Средна температура (AC)", t_inv_avg, "°C", 0, span_str),
         ev("Часове над 55 °C", it_hot, "ч", 0, span_str), ev("Очакван живот по модела", inv_fc["life_years"], "години", 1)]))
    inv_find.append(finding(
        "good" if clip < 10 else "watch", "Инверторът не е тясно място",
        (f"Панелите са {n(kwp, 2)} kWp при инвертор 15 kW (DC/AC {n(kwp / 15, 2)}). Пиковата PV мощност {span_post} е {n((pv_peak or 0) / 1000, 1)} kW "
         f"(DC страната зарежда батерията директно), а изходът е стоял на лимита от 15 kW {n(clip, 1)} ч: производството спира от пълната батерия, преди инверторът да стане тясно място. "
         "Ако се разреши износ, по-високото DC/AC ще започне да се усеща през лятото около обед, но загубата там е малка спрямо днешното ограничаване."),
        [ev("DC/AC", kwp / 15, "", 2), ev("Пикова PV мощност", (pv_peak or 0) / 1000, "kW", 1, span_post),
         ev("Време на лимита 15 kW", clip, "ч", 1, "всички месеци")]))
    lv_g = worst([lvl(outages, 3, 10), lvl(over, 1, 20)])
    inv_find.append(finding(
        lv_g, "Мрежата е стабилна" if lv_g == "good" else "Има събития в мрежата",
        f"Прекъсвания на мрежата: {n(outages)}; кадри с напрежение над 253 V: {n(over)} (EN 50160 допуска +10% от 230 V).",
        [ev("Прекъсвания", outages, "бр.", 0, span_str), ev("Свръхнапрежение", over, "кадъра", 0, span_str)]))
    lv_inv = worst([lv_t, lv_g])
    sections.append({"key": "inverter", "title": "Инвертор и мрежа", "level": lv_inv, "findings": inv_find,
                     "summary": "Работи хладно и далеч от лимита си." if lv_inv == "good" else "Има за наблюдение."})

    # ============================================================ battery
    bat_find = []
    cyc = hs["cycles"]
    bfc = h["battery_forecast"]["actual"]
    years_cycles = cyc["rated"] / cyc["per_year"] if cyc.get("per_year") else None
    base_sc = scenarios.get("base") or next(iter(scenarios.values()), {})
    repl_year = base_sc.get("battery_replacement_year")
    commissioning = date.fromisoformat(next((c["date"] for c in m.capex), m.order[0]))
    repl_date = commissioning.replace(year=commissioning.year + int(repl_year)).isoformat() if repl_year else None
    bat_find.append(finding(
        "good", "Батерията старее от календара и температурата, не от циклите",
        (f"До момента {n(cyc['total'])} еквивалентни цикъла, около {n(cyc['per_year'])} на година. При {n(cyc['rated'])} цикъла по каталог "
         f"циклите биха стигнали за ~{n(years_cycles)} години, така че те не са ограничението. Моделът на здравето дава {n(bfc['soh_now'], 1)}% "
         f"спрямо номинала днес и 70% около {dmy(bfc['eol_date'])}"
         + (f"; базовият сценарий предвижда подмяна през {dmy(repl_date)[3:]}, което съвпада с прогнозата." if repl_date and bfc.get("eol_date")
            and abs(int(repl_date[:4]) - int(bfc["eol_date"][:4])) <= 1 else
            f"; базовият сценарий предвижда подмяна през {dmy(repl_date)[3:]}, а прогнозата е {dmy(bfc['eol_date'])[3:]}: струва си сценарият да се изравни." if repl_date else ".")),
        [ev("Цикли до момента", cyc["total"], "", 0), ev("Цикли на година", cyc["per_year"], "", 0),
         ev("Здраве по модела", bfc["soh_now"], "%", 1), ev("70% по модела", dmy(bfc["eol_date"]))]))
    hot = [x for x in months if x.get("bt_bands")]
    bt35 = sum(x["bt_bands"][4] + x["bt_bands"][5] for x in hot)
    bt40 = sum(x["bt_bands"][5] for x in hot)
    bt_hours = sum(x["bt_hours"] for x in hot)
    bt_max = max((x["bt_max"] for x in hot if x["bt_max"] is not None), default=None)
    lv_bt = worst([lvl(bt35, 100, 10 ** 9), lvl(bt_max, 40, 45)])
    bat_find.append(finding(
        lv_bt, "Лятната топлина е основният фактор за износването",
        (f"{(span_str or '').capitalize()} батерията е била {n(bt35)} ч над 35 °C ({n(bt35 / bt_hours * 100 if bt_hours else None)}% от времето), от тях {n(bt40)} ч над 40 °C; "
         f"максимумът е {n(bt_max)} °C. За LFP всеки 10 °C над 25 °C удвояват календарното стареене, затова сянка или вентилация на мястото "
         "на батерията през юли–септември е най-евтината мярка за по-дълъг живот.")
        if hot else "Няма данни за температурата на батерията.",
        [ev("Часове над 35 °C", bt35, "ч", 0, span_str), ev("Часове над 40 °C", bt40, "ч", 0, span_str),
         ev("Макс. температура", bt_max, "°C", 0, span_str)]))
    summer = [mo for mo in (6, 7, 8, 9)]
    if have_year:
        night_summer = _mean([prof[mo]["dis_night"] for mo in summer])
        full_h = _mean([x["soc_full_h_day"] for x in months if int(x["month"][5:]) in summer and x["soc_full_h_day"] is not None])
        share = night_summer / usable * 100 if usable else None
        bat_find.append(finding(
            lvl(full_h, 4, 8), "През лятото батерията стои дълго пълна, а нощта ползва по-малко от половината",
            (f"Юни–септември батерията стои средно {n(full_h, 1)} ч/ден на 100%, а нощем се разреждат около {n(night_summer, 1)} kWh от "
             f"{n(usable, 1)} kWh използваем капацитет ({n(share)}%). Времето на висок заряд ускорява стареенето на LFP. "
             "Горна граница на заряда около 90% през лятото (настройка в Deye, прави се от теб) не би струвала нищо, защото излишъкът така или иначе се губи."),
            [ev("Часове на 100%, лято", full_h, "ч/ден", 1), ev("Нощен разряд, лято", night_summer, "kWh/нощ", 1, span_year),
             ev("Използваем капацитет", usable, "kWh", 1)]))
        winter = (12, 1, 2)
        dis_w = _mean([prof[mo]["dis"] for mo in winter])
        bat_find.append(finding(
            "watch" if dis_w < usable * 0.6 else "good", "През зимата батерията остава недоизползвана",
            (f"Декември–февруари батерията разрежда средно {n(dis_w, 1)} kWh/ден от {n(usable, 1)} kWh възможни: слънцето не стига да я напълни. "
             "Свободният капацитет може да се зарежда от мрежата по нощна тарифа и да покрива дневната консумация (виж лостовете)."),
            [ev("Разряд, зима", dis_w, "kWh/ден", 1, span_year), ev("Използваем капацитет", usable, "kWh", 1)]))
    b04 = sum_(post, "dis") / sum_(post, "chg") * 100 if sum_(post, "chg") else None
    bat_find.append(finding(
        lvl(b04, 90, 85, False), "Ефективността заряд/разряд е добра",
        f"{span_post}: разредени {n(sum_(post, 'dis'))} kWh срещу заредени {n(sum_(post, 'chg'))} kWh ({n(b04)}%). "
        "Типично за HV LFP система е 90–95%; цифрата включва и собственото потребление на BMS.",
        [ev("Разряд / заряд", b04, "%", 0, span_post)]))
    lv_bat = worst([lv_bt, hs["components"]["battery"]["level"]])
    sections.append({"key": "battery", "title": "Батерия", "level": lv_bat, "findings": bat_find,
                     "summary": "Здрава; основният ѝ враг е лятната температура и дългото стоене на 100%."})

    # ============================================================ consumption and grid
    load_find = []
    hp = hourly_profile(conn, m.station_id, year_start, last)
    evh = ev_hours(conn, m.station_id, year_start, last)
    ev_year = sum(v[0] for v in evh.values())
    load_year = sum_(last365, "meter_load_day") + sum_(last365, "meter_load_night")
    days365 = len(last365)
    by_mo = {int(k[5:]): v["base_kw"] for k, v in hp["months"].items() if v["base_kw"] is not None}
    base_kw = _mean([by_mo.get(mo) for mo in (5, 6, 7, 8, 9)])          # no heating, no lights at night
    base_winter = _mean([by_mo.get(mo) for mo in (12, 1, 2)])
    if base_kw is not None:
        base_year = base_kw * 8760
        load_find.append(finding(
            lvl(base_kw, 0.3, 0.6), "Постоянният товар на дома е около " + n(base_kw * 1000) + " W",
            (f"Между 1 и 5 ч, без зареждане на колата, домът тегли медианно {n(base_kw * 1000)} W през май–септември"
             + (f" и {n(base_winter * 1000)} W през зимата (отоплението: помпи, вентилатор на пелетния котел)" if base_winter else "")
             + f". Летният товар е постоянните консуматори (хладилници, рутери, помпи, техника в режим на готовност): ~{n(base_year)} kWh/год. "
             "Всеки 100 W по-малко са ~876 kWh/год; измервателен контакт или изключване на предпазителите един по един нощем показват кои са."),
            [ev("Постоянен товар, лято", base_kw * 1000, "W", 0, "1–5 ч, май–септември")]
            + ([ev("Нощен товар, зима", base_winter * 1000, "W", 0, "1–5 ч, декември–февруари")] if base_winter else [])
            + [ev("Годишно при летния товар", base_year, "kWh", 0)]))
    ev_share = ev_year / load_year * 100 if load_year else None
    ev_days = ev_year / max(days365, 1)
    if ev_year:
        load_find.append(finding(
            "watch", "Колата се зарежда нощем",
            (f"Нощните часове с над {n(EV_HOUR_KWH)} kWh консумация (на практика зареждане на Tesla) са дали {n(ev_year)} kWh за последните "
             f"{days365} дни ({n(ev_share)}% от консумацията, средно {n(ev_days, 1)} kWh/ден). През лятото след разширението батерията ги покрива, "
             "но през пролетта, есента и зимата идват от мрежата, а излишъкът по обед се губи."),
            [ev("Нощно зареждане", ev_year, "kWh", 0, "последните 12 месеца"), ev("Дял от консумацията", ev_share, "%", 0)]
            + [ev(f"{MONTHS_BG[int(k[5:]) - 1]} {k[2:4]}", v[0], "kWh") for k, v in sorted(evh.items())]))
    if Y:
        ss = (Y["load"] - Y["imp"]) / Y["load"] * 100 if Y["load"] else None
        g_ss = next((g for g in goal_rows if g["kpi_id"] == "U02" and g["active"]), None)
        imp_w = sum(prof[mo]["imp"] * DIM[mo] for mo in (11, 12, 1, 2))
        load_find.append(finding(
            lvl(ss, g_ss["target"] if g_ss else 75, (g_ss["target"] if g_ss else 75) - 15, False),
            "Самозадоволяването с текущата система е около " + n(ss) + "%",
            (f"За типична година домът ползва ~{n(Y['load'])} kWh и взема от мрежата ~{n(Y['imp'])} kWh; {n(imp_w / Y['imp'] * 100 if Y['imp'] else None)}% от вноса е ноември–февруари. "
             f"Целта е {n(g_ss['target']) if g_ss else '–'}%. Измерената 2026 г. е по-ниска, защото до юли системата беше наполовина; "
             "оттук нататък пролет, лято и есен са почти без внос, а зимата остава зависима от мрежата."),
            [ev("Консумация", Y["load"], "kWh/год", 0, span_year), ev("Внос", Y["imp"], "kWh/год", 0, span_year),
             ev("Самозадоволяване", ss, "%", 0, span_year), ev("Внос ноември–февруари", imp_w, "kWh", 0, span_year)]))
    inv_before = [i for i in m.invoices if i["period_to"] < m.order[0]][-12:]
    inv_after = [i for i in m.invoices if i["period_from"] >= expansion]
    if inv_before and inv_after:
        b = sum(i["total_ccy"] for i in inv_before) / len(inv_before)
        a = sum(i["total_ccy"] for i in inv_after) / len(inv_after)
        load_find.append(finding(
            "good", "Сметката за ток падна с " + n((1 - a / b) * 100 if b else None) + "%",
            f"Преди PV средната месечна фактура беше {n(b, 0)} €, след разширението {n(a, 0)} € (по фактурите на Електрохолд, с ДДС).",
            [ev("Преди PV", b, "€/месец", 0, f"{len(inv_before)} фактури"), ev("След разширението", a, "€/месец", 0, f"{len(inv_after)} фактури")]))
    lv_load = worst([f["level"] for f in load_find])
    sections.append({"key": "load", "title": "Потребление, мрежа и тарифи", "level": lv_load, "findings": load_find,
                     "summary": "Сметката е почти нула от юли; остават зимата, нощното зареждане на колата и постоянният товар."})

    # ============================================================ levers
    def lever(key, title, text, kwh_year, eur_month: dict[int, float], who, assumption):
        eur_year = sum(eur_month.get(mo, 0.0) * DIM[mo] for mo in range(1, 13))
        pb = payback_with(m, fc, last, base_sc, eur_month) if base_sc and eur_year > 0 else None
        levers.append({"key": key, "title": title, "text": text, "kwh_year": round(kwh_year), "eur_year": round(eur_year),
                       "payback_years": round(pb["years"], 1) if pb and pb["years"] else None, "who": who,
                       "assumption": assumption})

    if have_year:
        price = curtail_price or 0.0
        export = {mo: prof[mo]["curtailed"] * price for mo in range(1, 13)}
        lever("export", "Продажба на излишъка в мрежата",
              f"~{n(Y['curtailed'])} kWh/год сега се губят. С договор за изкупуване и разрешен износ в Deye (вместо нулев износ) те носят пари.",
              Y["curtailed"], export, "ти: договор с ЕРМ Запад / търговец и настройка в Deye",
              f"цена {n(price, 4)} €/kWh (параметър „цена на пропуснатата енергия“ в „Цели“), без такси за достъп")
        ev_mo = {}
        for mo in range(1, 13):
            vals = [v[0] / max(hp["months"].get(k, {}).get("days", 1), 1) for k, v in evh.items() if int(k[5:]) == mo]
            ev_mo[mo] = _mean(vals) or 0.0
        shift_ccy, shift_kwh = {}, 0.0
        for mo in range(1, 13):
            s = min(ev_mo[mo], prof[mo]["curtailed"])          # what the midday surplus can carry
            avoided = min(s, prof[mo]["imp"])                   # night import it replaces (the rest spares the battery)
            shift_ccy[mo] = avoided * p_night
            shift_kwh += s * DIM[mo]
        lever("ev", "Зареждане на колата по обед",
              "Tesla по график 11–16 ч в слънчеви дни (в приложението на Tesla) вместо нощем. Пролет и есен това маха вноса; "
              f"през лятото спестява до ~{n(shift_kwh / cap if cap else 0)} цикъла на батерията годишно.",
              shift_kwh, shift_ccy, "ти: график в приложението на Tesla",
              "колата е вкъщи по обед; нощните часове над 3 kWh са колата; спестява се нощна тарифа")
        tou_ccy, tou_kwh = {}, 0.0
        imp_day_mo = defaultdict(list)
        for r in last365:
            imp_day_mo[int(r["day"][5:7])].append(r["meter_imp_day"])
        gain = p_day - p_night / BATTERY_EFF
        for mo in (11, 12, 1, 2):
            spare = max(usable - prof[mo]["dis"], 0.0)
            k = min(_mean(imp_day_mo.get(mo, [])) or 0.0, spare)
            tou_ccy[mo] = k * gain if gain > 0 else 0.0
            tou_kwh += k * DIM[mo]
        lever("tou", "Зимно зареждане от мрежата по нощна тарифа",
              f"Ноември–февруари батерията остава полупразна. Зареждането ѝ нощем (по {n(p_night, 3)} €/kWh) за деня (по {n(p_day, 3)} €/kWh) "
              f"печели {n(gain, 3)} €/kWh след загубите. Настройва се в Deye (Time of Use), от теб.",
              tou_kwh, tou_ccy, "ти: Time of Use в Deye, само през зимата",
              f"дневният внос от последните 12 месеца, до свободния капацитет; ефективност {n(BATTERY_EFF * 100)}%")
        if base_kw:
            share_grid = Y["imp"] / Y["load"] if Y["load"] else 0
            avg_price = (p_day + p_night) / 2
            red = 0.1 * 24
            base_ccy = {mo: red * prof[mo]["imp"] / prof[mo]["load"] * avg_price if prof[mo]["load"] else 0.0 for mo in range(1, 13)}
            lever("base", "100 W по-малко постоянен товар",
                  f"Намиране и изключване на консуматори в режим на готовност. Пести {n(red * 365)} kWh/год; парите идват от зимата, "
                  "когато енергията е от мрежата, а през лятото освобождава батерията.",
                  red * 365, base_ccy, "ти: проверка на уредите", f"дял от мрежата по месеци ({n(share_grid * 100)}% за година)")
        dhw = sum(min(prof[mo]["curtailed"], DHW_KWH_DAY) * DIM[mo] for mo in range(1, 13))
        levers.append({"key": "dhw", "title": "Топла вода от излишъка", "kwh_year": round(dhw), "eur_year": 0, "payback_years": None,
                       "text": f"Електрически бойлер или термопомпа за вода, включвани от излишъка по обед, биха поели ~{n(dhw)} kWh/год "
                               "и биха пестили пелети извън отоплителния сезон. Стойността зависи от цената на пелетите, която не е въведена.",
                       "who": "ти: бойлер с управление по излишък", "assumption": f"до {n(DHW_KWH_DAY)} kWh/ден, колкото е излишъкът в месеца"})
    levers.sort(key=lambda x: -x["eur_year"])

    # ============================================================ economics
    eco_find = []
    pb = {k: payback_with(m, fc, last, sc) for k, sc in scenarios.items()}
    pb_norep = payback_with(m, fc, last, base_sc, no_replacements=True) if base_sc else None
    capex_total = sum(c["amount_ccy"] or 0 for c in m.capex)
    cum = sum(m.days[d]["benefit"] for d in m.order)
    g_pb = next((g for g in goal_rows if g["kpi_id"] == "F04" and g["active"]), None)
    base_pb = pb.get("base") or {}
    lv_pb = lvl(base_pb.get("years"), g_pb["target"] if g_pb else 12, g_pb["warn_level"] if g_pb and g_pb.get("warn_level") else 15)
    eco_find.append(finding(
        lv_pb, "Окупаемост: " + " / ".join(f"{x['label'].lower()} {n(x['years'], 1)} г." for x in pb.values() if x.get("years")),
        (f"Инвестицията е {n(capex_total)} €; до {dmy(last)} системата е спестила {n(cum)} € ({n(cum / capex_total * 100 if capex_total else None, 1)}%). "
         f"С текущата система типичната година носи ~{n(base_pb.get('annual'))} €. Базовият сценарий стига CAPEX около {dmy(base_pb.get('date'))[3:]}"
         + (f"; без подмяната на батерията и инвертора в сценария би бил {n(pb_norep['years'], 1)} г." if pb_norep and pb_norep.get("years") else "")
         + (f" Целта е {n(g_pb['target'])} г." if g_pb else "")),
        [ev("CAPEX", capex_total, "€"), ev("Спестено до момента", cum, "€", 0, f"до {dmy(last)}"),
         ev("Полза за типична година", base_pb.get("annual"), "€/год", 0, span_year)]
        + [ev(f"Окупаемост, {x['label'].lower()}", x.get("years"), "години", 1) for x in pb.values()]
        + ([ev("Базов, без подмени", pb_norep.get("years"), "години", 1)] if pb_norep else [])))
    if Y:
        val = base_pb.get("annual") / Y["pv"] if Y["pv"] and base_pb.get("annual") else None
        eco_find.append(finding(
            "watch" if val and val < p_day * 0.8 else "good", "Всеки използван kWh струва " + n(val, 3) + " €",
            (f"Ползата за година, разделена на използваното производство, е {n(val, 3)} €/kWh при дневна цена {n(p_day, 3)} € и нощна {n(p_night, 3)} €. "
             "Под дневната цена е, защото голяма част от PV замества нощна (по-евтина) енергия през батерията. Ниските цени на тока са причината "
             "окупаемостта да е дълга; най-силният лост е да се използва изгубената енергия."),
            [ev("Стойност на използван kWh", val, "€/kWh", 3), ev("Дневна цена", p_day, "€/kWh", 3), ev("Нощна цена", p_night, "€/kWh", 3)]))
    ev_goals = goals.evaluate_all(m, [g for g in goal_rows if g["active"]], scenarios, last, history=False)
    st_count = defaultdict(int)
    for g in ev_goals:
        st_count[g["current"]["status"]] += 1
    bad_goals = [g for g in ev_goals if g["current"]["status"] in ("behind", "at_risk")]
    eco_find.append(finding(
        "bad" if any(g["current"]["status"] == "behind" for g in ev_goals) else "watch" if bad_goals else "good",
        "Цели: " + ", ".join(f"{v} {goals.STATUS_LABEL[k]}" for k, v in st_count.items()),
        ("Изостават или са в риск: " + "; ".join(f"{g['title']} ({_goal_value(g)})" for g in bad_goals) + ". "
         if bad_goals else "Всички активни цели са изпълнени или в график. ")
        + "Целите се сменят на страницата „Цели“.",
        [ev(g["title"], _goal_value(g), "", 0) for g in ev_goals]))
    lv_eco = worst([lv_pb])
    sections.append({"key": "economics", "title": "Икономика и окупаемост", "level": lv_eco, "findings": eco_find,
                     "summary": "Сметката е почти нула, но цените на тока са ниски и окупаемостта зависи от това да се ползва изгубената енергия."})

    # ============================================================ data
    cov_post = sum_(post, "covered_s") / sum_(post, "expected_s") * 100 if sum_(post, "expected_s") else None
    data_find = [finding(
        hs["components"]["data"]["level"], "Данните са пълни и навреме",
        (f"Пълнота {span_post}: {n(cov_post, 1)}%. Последният кадър е отпреди {n(hs['components']['data']['checks'][0]['value'])} мин. "
         "Deye пази 5-минутната история на инвертора само ~90 дни, затова колекторът трябва да работи постоянно; "
         "декември–март вносът по Deye е под електромера и разликата е отчетена като консумация, която Deye не вижда."),
        [ev("Пълнота", cov_post, "%", 1, span_post)])]
    sections.append({"key": "data", "title": "Данни", "level": hs["components"]["data"]["level"], "findings": data_find,
                     "summary": "Надеждни за изводите по-горе."})

    # ============================================================ system as a whole
    hw = worst([lv_hw, lv_inv, lv_bat])
    util = Y["pv"] / (Y["pv"] + Y["curtailed"]) * 100 if Y and (Y["pv"] + Y["curtailed"]) else None
    lv_util = lvl(100 - util if util is not None else None, 15, 30)
    watch = [s["title"].lower() for s in sections if s["key"] in ("pv", "inverter", "battery") and s["level"] != "good"]
    top = next((x for x in levers if x["eur_year"]), None)
    headline = [
        ("Технически системата е в добро състояние: панели, инвертор и батерия работят в нормата си." if hw == "good" else
         "Технически системата работи, с неща за наблюдение при: " + ", ".join(watch) + "." if hw == "watch" else
         "Има технически проблем при: " + ", ".join(watch) + "."),
        f"Използва се {n(util)}% от възможното производство; ~{n(Y['curtailed'] if Y else None)} kWh/год се губят при нулев износ.",
        f"Окупаемостта по базовия сценарий е {n(base_pb.get('years'), 1)} години" + (f" при цел {n(g_pb['target'])}." if g_pb else "."),
    ]
    if top:
        headline.append(f"Най-силният лост е {top['title'].lower()}: ~{n(top['eur_year'])} €/год и окупаемост {n(top['payback_years'], 1)} г. вместо {n(base_pb.get('years'), 1)}.")
    pillars = [
        {"key": "tech", "label": "Техническо състояние", "level": hw, "level_label": LEVEL_LABEL[hw],
         "value": {"good": "в норма", "watch": "работи"}.get(hw, LEVEL_LABEL[hw]), "sub": "панели, инвертор, батерия"},
        {"key": "use", "label": "Оползотворяване", "level": lv_util, "level_label": LEVEL_LABEL[lv_util],
         "value": f"{n(util)}%", "sub": "от възможното производство, типична година"},
        {"key": "money", "label": "Окупаемост", "level": lv_pb, "level_label": LEVEL_LABEL[lv_pb],
         "value": f"{n(base_pb.get('years'), 1)} г.", "sub": "базов сценарий" + (f", цел {n(g_pb['target'])} г." if g_pb else "")},
    ]
    overall_level = worst([hw, lv_pb, lvl(100 - util if util is not None else None, 15, 30)])
    sections.insert(0, {
        "key": "system", "title": "Системата като цяло", "level": overall_level,
        "summary": "Технически здрава система, която е оразмерена за повече, отколкото режимът без износ ѝ позволява да даде.",
        "findings": [finding(
            overall_level, "Балансът на енергията за типична година",
            (f"Панели ~{n(Y['pv'] + Y['curtailed'])} kWh възможни → {n(Y['pv'])} kWh използвани, от тях {n(Y['chg'])} kWh минават през батерията. "
             f"Домът ползва ~{n(Y['load'])} kWh, от мрежата идват ~{n(Y['imp'])} kWh, почти само през зимата. "
             "Системата е голяма за дома: през лятото произвежда двойно повече, отколкото може да се използва, а през зимата не стига. "
             "Затова най-ценното е да се намери къде да отива летният излишък (продажба, колата по обед, топла вода), а зимата да се облекчи с нощна тарифа.")
            if Y else "Няма достатъчно данни за типична година.",
            [ev("Възможно производство", (Y["pv"] + Y["curtailed"]) if Y else None, "kWh/год", 0, span_year),
             ev("Използвано", Y["pv"] if Y else None, "kWh/год", 0), ev("През батерията", Y["chg"] if Y else None, "kWh/год", 0),
             ev("Консумация", Y["load"] if Y else None, "kWh/год", 0), ev("Внос", Y["imp"] if Y else None, "kWh/год", 0)])]})

    year = [{"month": MONTHS_BG[mo - 1], "pv": round(prof[mo]["pv"] * DIM[mo]), "curtailed": round(prof[mo]["curtailed"] * DIM[mo]),
             "load": round(prof[mo]["load"] * DIM[mo]), "imp": round(prof[mo]["imp"] * DIM[mo]),
             "estimated": mo in fc.estimated} for mo in range(1, 13)] if have_year else []
    return {"as_of": last, "expansion": expansion, "overall": {"level": overall_level, "level_label": LEVEL_LABEL[overall_level],
            "headline": headline, "pillars": pillars, "utilisation": util, "payback_base": base_pb.get("years"),
            "hardware": hw, "hardware_label": LEVEL_LABEL[hw]},
            "sections": sections, "levers": levers, "year": year, "estimated_months": fc.estimated}


def _mean(vals):
    v = [x for x in vals if x is not None]
    return sum(v) / len(v) if v else None


def _median_full(rows):
    v = [r["t_full"] for r in rows if r["t_full"] is not None]
    return median(v) / 60 if v else None


def _hhmm(h):
    if h is None:
        return "–"
    mins = round(h * 60)
    return f"{mins // 60}:{mins % 60:02d}"


def _goal_value(g) -> str:
    c = g["current"]
    v = c.get("forecast") if c.get("forecast") is not None else c.get("actual")
    unit = goals.GOAL_KPIS.get(g["kpi_id"], ("", "", ""))[1]
    return f"{n(v, 1)} {unit} при цел {n(g['target'], 1)} {unit}".replace(" %", "%")
