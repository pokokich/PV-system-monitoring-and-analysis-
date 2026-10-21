"""Analysis page: the owner's own language model reads a summary of the data and writes the findings.

Without a configured model the page stays empty apart from the typical-year chart: the
dashboard has no built-in rules that would fit every installation. The summary (context())
holds the system as entered in Settings, the KPIs of three periods, monthly totals, the
typical year with the current system, goals, health checks and the payback scenarios. It
never contains Deye credentials, invoice numbers or the exact location (rounded to ~10 km).

The model answers in JSON (SCHEMA_HINT), stored in ai_analysis with its date, provider and
model, so the page shows the last answer until the owner asks for a new one. The model is
told that this software is read-only towards the inverter: recommendations are for the owner.
"""
from __future__ import annotations

import calendar
import json
import sqlite3
import threading
import time
from collections import defaultdict
from datetime import date, datetime, timedelta

from collector import appconf

from . import data, goals, health, kpi, llm, settings

DIM = {mo: calendar.monthrange(2027, mo)[1] for mo in range(1, 13)}
LEVELS = ("good", "watch", "bad", "no_data")
LANG_NAMES = {"bg": "Bulgarian", "en": "English", "de": "German", "pl": "Polish", "ro": "Romanian", "es": "Spanish",
              "it": "Italian", "fr": "French", "cs": "Czech", "hu": "Hungarian", "uk": "Ukrainian", "nl": "Dutch"}
_lock = threading.Lock()


def _r(x, n=2):
    if isinstance(x, float):
        return round(x, n)
    if isinstance(x, dict):
        return {k: _r(v, n) for k, v in x.items()}
    if isinstance(x, list):
        return [_r(v, n) for v in x]
    return x


# ---------------------------------------------------------------- the typical year (chart, no model needed)

def typical_year(m: data.Model) -> dict:
    if not m.order:
        return {"year": [], "estimated_months": []}
    fc = goals.Forecaster(m, m.order[-1])
    prof = fc.profile
    if not all(prof.get(mo) for mo in range(1, 13)):
        return {"year": [], "estimated_months": fc.estimated}
    year = [{"month": mo, "pv": round(prof[mo]["pv"] * DIM[mo]), "curtailed": round(prof[mo]["curtailed"] * DIM[mo]),
             "load": round(prof[mo]["load"] * DIM[mo]), "imp": round(prof[mo]["imp"] * DIM[mo]),
             "exp": round(prof[mo]["exp"] * DIM[mo]), "benefit": round(prof[mo]["benefit"] * DIM[mo], 1),
             "estimated": mo in fc.estimated} for mo in range(1, 13)]
    return {"year": year, "estimated_months": fc.estimated}


# ---------------------------------------------------------------- the summary sent to the model

KPI_SKIP = {"D03"}   # data latency changes every minute and says nothing about the system


def _kpis(m: data.Model, start: str, end: str, price) -> dict:
    out = kpi.summary(m, start, end, price)
    return {"from": start, "to": end, "days": out["days"],
            "kpis": {k: {"name": v["name"], "unit": v["unit"], "value": v["value"]}
                     for k, v in out["kpis"].items() if v["value"] is not None and k not in KPI_SKIP}}


def _monthly(m: data.Model) -> list[dict]:
    by = defaultdict(list)
    for d in m.order:
        by[d[:7]].append(m.days[d])
    out = []
    for month in sorted(by)[-24:]:
        s = kpi.aggregate(by[month])
        out.append({"month": month, "days": s["days"], "pv_kwh": s["pv"], "load_kwh": s["load"], "import_kwh": s["imp"],
                    "export_kwh": s["exp"], "battery_discharge_kwh": s["dis"], "curtailed_kwh": s["curtailed"] if s["wx_days"] else None,
                    "self_sufficiency_pct": s["self_sufficiency"], "night_coverage_pct": s["night_coverage"],
                    "specific_yield_kwh_kwp": s["specific"], "pv_vs_pvgis_pct": s["p04"],
                    "cost_with_pv": s["cost_with"], "cost_without_pv": s["cost_without"], "benefit": s["benefit"],
                    "battery_temp_max_c": s["temp_batt_max"], "inverter_temp_max_c": s["temp_inv_max"],
                    "data_coverage_pct": s["coverage"]})
    return out


def _system(m: data.Model, conn) -> dict:
    st = m.settings
    def arr(a):
        return {"name": a["name"], "mppt": a["mppt_channel"], "panels": a["panels"], "panel_wp": a["panel_wp"],
                "kwp": round((a["panels"] or 0) * (a["panel_wp"] or 0) / 1000, 2), "tilt_deg": a["tilt_deg"],
                "azimuth_deg": a["azimuth_deg"], "shading": a["shading_note"], "from": a["valid_from"], "to": a["valid_to"]}
    def bat(b):
        return {"modules": b["modules"], "kwh_per_module": b["kwh_per_module"],
                "kwh": round((b["modules"] or 0) * (b["kwh_per_module"] or 0), 2), "soc_min_pct": b["soc_min_normal"],
                "from": b["valid_from"], "to": b["valid_to"]}
    inverters = [json.loads(r[0] or "{}") for r in conn.execute("SELECT meta_json FROM device WHERE type = 'INVERTER'")]
    tariff = conn.execute("SELECT * FROM tariff ORDER BY valid_from DESC LIMIT 1").fetchone()
    feed = conn.execute("SELECT * FROM feed_in_price ORDER BY valid_from DESC LIMIT 1").fetchone()
    lat, lon = m.site or (None, None)
    return {
        "commissioned": m.order[0] if m.order else None,
        "location_approx": {"lat": round(lat, 1), "lon": round(lon, 1)} if lat is not None and lon is not None else None,
        "timezone": appconf.timezone(conn).key, "currency": appconf.get(conn, "currency", "EUR"),
        "pv_arrays": [arr(a) for a in m.arrays], "battery": [bat(b) for b in m.batteries],
        "inverter": [{k: i.get(k) for k in ("deviceType", "productId", "deviceModel", "ratedPower") if i.get(k)} for i in inverters],
        "tariff_now": {k: tariff[k] for k in tariff.keys() if k not in ("id", "note")} if tariff else None,
        "night_tariff_hours": appconf.night_windows(conn),
        "feed_in_now": {k: feed[k] for k in feed.keys() if k not in ("id", "note", "contract_ref")} if feed else None,
        "capex_total": round(sum(c["amount_ccy"] or 0 for c in m.capex), 2),
        "capex_items": [{"date": c["date"], "amount": c["amount_ccy"], "what": c["description"]} for c in m.capex],
    }


def context(m: data.Model, conn, scenarios: dict, goal_rows: list[dict], price) -> dict:
    last = m.order[-1]
    first = m.order[0]
    d30 = max(first, (date.fromisoformat(last) - timedelta(days=29)).isoformat())
    d365 = max(first, (date.fromisoformat(last) - timedelta(days=364)).isoformat())
    fc = goals.Forecaster(m, last)
    evaluated = goals.evaluate_all(m, [g for g in goal_rows if g["active"]], scenarios, last, history=False)
    hs = health.status(m, m.health, time.time())
    pb = {name: goals.payback_scenario(m, fc, last, sc) for name, sc in scenarios.items()}
    return _r({
        "as_of": last,
        "system": _system(m, conn),
        "periods": {"last_30_days": _kpis(m, d30, last, price), "last_365_days": _kpis(m, d365, last, price),
                    "since_commissioning": _kpis(m, first, last, price)},
        "monthly": _monthly(m),
        "typical_year_current_system": typical_year(m),
        "goals": [{"title": g["title"], "kpi": g["kpi_id"], "target": g["target"], "direction": g["direction"],
                   "period": g["period_type"], "actual": g["current"]["actual"], "forecast": g["current"]["forecast"],
                   "status": g["current"]["status"]} for g in evaluated],
        "health": {"overall": hs["overall"], "cycles": hs["cycles"],
                   "checks": {k: [{"name": c["name"], "value": c["value"], "unit": c["unit"], "level": c["level"], "rule": c["rule"]}
                                  for c in v["checks"]] for k, v in hs["components"].items()}},
        "payback_scenarios": {name: {k: v for k, v in p.items() if k != "forecast"} for name, p in pb.items()},
        "scenario_assumptions": scenarios,
    })


# ---------------------------------------------------------------- the prompt

SCHEMA_HINT = """{
  "overall": {
    "level": "good|watch|bad",
    "headline": ["3-5 short sentences: the most important conclusions, each with its number"],
    "pillars": [{"label": "e.g. Self-sufficiency", "value": "66 %", "sub": "goal 75 %", "level": "good|watch|bad|no_data"}]
  },
  "sections": [{
    "key": "pv|battery|inverter|consumption|economics|goals|data (or another short slug)",
    "title": "...", "level": "good|watch|bad|no_data", "summary": "one sentence",
    "findings": [{"level": "good|watch|bad|no_data", "title": "...", "text": "2-4 sentences, concrete, with numbers",
                  "evidence": [{"label": "...", "value": "number as text", "unit": "...", "period": "..."}]}]
  }],
  "levers": [{"title": "...", "text": "what to do and why", "who": "who can do it (owner, installer, grid operator...)",
              "assumption": "the estimate's assumption", "kwh_year": 0, "money_year": 0, "payback_years": null}]
}"""


def system_prompt(lang: str, currency: str) -> str:
    language = LANG_NAMES.get(lang, lang)
    return f"""You are an experienced solar PV and home battery analyst. You review the data of one home installation
with a Deye hybrid inverter and write findings for its owner.

Rules:
- Use only the numbers in the data. Do not invent measurements. When something cannot be judged from the data, say so
  (level "no_data").
- Every finding names the numbers and the period behind it (evidence). Prefer whole-system conclusions over details.
- Compare with the owner's goals, PVGIS expectations and typical values for the location and equipment.
- The monitoring software is read-only towards the inverter. Recommendations are for the owner to apply; never claim
  the software will change a setting.
- Money is in {currency}; amounts are never converted. kWh, kW, %, °C as units.
- Order levers by yearly benefit, biggest first; give your estimate's assumption.
- Write all text in {language}. Use the number style of that language.
- Answer with one JSON object only, no prose around it, with this shape:
{SCHEMA_HINT}"""


def user_prompt(ctx: dict, question: str | None) -> str:
    q = f"\n\nThe owner also asks: {question.strip()}\nAnswer it inside the findings." if question and question.strip() else ""
    return ("Data of the installation (JSON; KPI names may be in Bulgarian, the units say what they are):\n"
            + json.dumps(ctx, ensure_ascii=False, separators=(",", ":")) + q)


def clean(obj: dict) -> dict:
    """Keep only the expected shape, so the page renders whatever the model returned."""
    def lv(x):
        return x if x in LEVELS else "no_data"
    def s(x, n=2000):
        return str(x)[:n] if x is not None else ""
    def num(x):
        return x if isinstance(x, (int, float)) and not isinstance(x, bool) else None
    ov = obj.get("overall") if isinstance(obj.get("overall"), dict) else {}
    out = {"overall": {"level": lv(ov.get("level")), "headline": [s(h, 400) for h in (ov.get("headline") or [])][:8],
                       "pillars": [{"label": s(p.get("label"), 80), "value": s(p.get("value"), 40), "sub": s(p.get("sub"), 120),
                                    "level": lv(p.get("level"))} for p in (ov.get("pillars") or []) if isinstance(p, dict)][:6]},
           "sections": [], "levers": []}
    for i, sec in enumerate(x for x in (obj.get("sections") or []) if isinstance(x, dict)):
        out["sections"].append({
            "key": "".join(ch for ch in s(sec.get("key"), 30) if ch.isalnum() or ch in "-_") or f"s{i}",
            "title": s(sec.get("title"), 120), "level": lv(sec.get("level")), "summary": s(sec.get("summary"), 400),
            "findings": [{"level": lv(f.get("level")), "title": s(f.get("title"), 160), "text": s(f.get("text")),
                          "evidence": [{"label": s(e.get("label"), 120), "value": s(e.get("value"), 40), "unit": s(e.get("unit"), 20),
                                        "period": s(e.get("period"), 60)} for e in (f.get("evidence") or []) if isinstance(e, dict)][:12]}
                         for f in (sec.get("findings") or []) if isinstance(f, dict)][:12]})
    for lever in (x for x in (obj.get("levers") or []) if isinstance(x, dict)):
        out["levers"].append({"title": s(lever.get("title"), 160), "text": s(lever.get("text")), "who": s(lever.get("who"), 160),
                              "assumption": s(lever.get("assumption"), 400), "kwh_year": num(lever.get("kwh_year")),
                              "money_year": num(lever.get("money_year")), "payback_years": num(lever.get("payback_years"))})
    out["levers"].sort(key=lambda x: -(x["money_year"] or 0))
    return out


# ---------------------------------------------------------------- runs

def latest(conn) -> dict | None:
    try:
        row = conn.execute("SELECT * FROM ai_analysis ORDER BY id DESC LIMIT 1").fetchone()
    except sqlite3.OperationalError:   # a database from before migration 007
        return None
    if not row:
        return None
    out = {k: row[k] for k in ("id", "created_at", "finished_at", "status", "provider", "model", "language", "as_of",
                               "question", "error")}
    out["result"] = json.loads(row["result_json"]) if row["result_json"] else None
    if out["status"] != "done":   # the last good answer stays visible while a new one runs or after an error
        prev = conn.execute("SELECT * FROM ai_analysis WHERE status = 'done' ORDER BY id DESC LIMIT 1").fetchone()
        if prev:
            out["previous"] = {"id": prev["id"], "finished_at": prev["finished_at"], "model": prev["model"],
                               "as_of": prev["as_of"], "result": json.loads(prev["result_json"])}
    return out


def start(m: data.Model, scenarios: dict, goal_rows: list[dict], price, lang: str, question: str | None) -> int:
    """Start a run in the background; returns its id. One run at a time."""
    conn = data.connect()
    try:
        cfg = llm.config(conn)
        ok, why = llm.ready(cfg)
        if not ok:
            raise llm.LlmError(why)
        if not m.order:
            raise llm.LlmError("Още няма данни за анализ.")
        ctx = context(m, conn, scenarios, goal_rows, price)
        currency = appconf.get(conn, "currency", "EUR")
    finally:
        conn.close()
    if not _lock.acquire(blocking=False):
        raise llm.LlmError("Вече тече анализ.")
    try:
        run_id = settings.write(lambda c: c.execute(
            "INSERT INTO ai_analysis (created_at, status, provider, model, language, as_of, question) VALUES (?, 'running', ?, ?, ?, ?, ?)",
            (settings.now(), cfg["provider"], cfg["model"] or cfg["cli_command"], lang, ctx["as_of"], question)).lastrowid)
    except Exception:
        _lock.release()
        raise

    def work():
        try:
            text, usage = llm.complete(cfg, system_prompt(lang, currency), user_prompt(ctx, question))
            result = clean(llm.parse_json(text))
            settings.write(lambda c: c.execute(
                "UPDATE ai_analysis SET status = 'done', finished_at = ?, result_json = ?, usage_json = ? WHERE id = ?",
                (settings.now(), json.dumps(result, ensure_ascii=False), json.dumps(usage), run_id)))
        except Exception as exc:  # shown on the page
            msg = str(exc)[:1000]
            settings.write(lambda c: c.execute(
                "UPDATE ai_analysis SET status = 'error', finished_at = ?, error = ? WHERE id = ?", (settings.now(), msg, run_id)))
        finally:
            _lock.release()

    threading.Thread(target=work, name="ai-analysis", daemon=True).start()
    return run_id


def fail_stale(conn_rw) -> None:
    """A run left 'running' by a restart is marked as failed."""
    conn_rw.execute("UPDATE ai_analysis SET status = 'error', finished_at = ?, error = 'прекъснат при рестарт' "
                    "WHERE status = 'running' AND created_at < ?", (settings.now(), settings.now() - llm.TIMEOUT_S - 60))
