"""Dashboard: FastAPI pages and JSON API over the collector database.

Reads only, except goals, scenarios and the manual parameters and invoices of the Settings page (web/settings.py).

Run: uvicorn web.app:app --host 0.0.0.0 --port 8088
"""
from __future__ import annotations

import json
import os
import sqlite3
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from collector import appconf, db, guard

from . import admin, analysis, data, goals, health, kpi, llm, security, settings, setup
from .data import TZ, day_bounds

HERE = Path(__file__).resolve().parent


@asynccontextmanager
async def lifespan(_app):
    """Create or migrate the database: a fresh install has none until the collector signs in, and the
    setup needs the tables now; after an update the new tables are there before the first request."""
    try:
        db.connect(data.DB_PATH).close()
    except sqlite3.Error:
        pass   # the collector does it a moment later
    yield


app = FastAPI(title="PV monitoring", docs_url="/api/docs", redoc_url=None, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
app.include_router(admin.router)
app.include_router(security.router)
app.include_router(setup.router)
app.middleware("http")(security.guard_writes)


@app.middleware("http")
async def currency_symbol(request: Request, call_next):
    """The code writes money units as "€"; JSON answers carry the installation's own symbol instead."""
    resp = await call_next(request)
    if not request.url.path.startswith("/api/") or "json" not in resp.headers.get("content-type", ""):
        return resp
    try:
        conn = data.connect()
        try:
            sym = appconf.currency_symbol(appconf.get(conn, "currency", "EUR"))
        finally:
            conn.close()
    except sqlite3.Error:
        return resp
    if sym == "€":
        return resp
    body = b"".join([chunk async for chunk in resp.body_iterator])
    body = body.replace("€".encode(), json.dumps(sym)[1:-1].encode())
    headers = {k: v for k, v in resp.headers.items() if k.lower() != "content-length"}
    return Response(body, status_code=resp.status_code, headers=headers, media_type=resp.media_type)
templates = Jinja2Templates(directory=HERE / "templates")

PAGES = [
    ("overview", "/", "Обзор"),
    ("production", "/production", "Производство"),
    ("battery", "/battery", "Батерия"),
    ("grid", "/grid", "Потребление и мрежа"),
    ("economics", "/economics", "Икономика"),
    ("quality", "/quality", "Качество на данните"),
    ("health", "/health", "Здраве"),
    ("goals", "/goals", "Цели"),
    ("analysis", "/analysis", "Анализ"),
    ("setup", "/setup", "Първоначална настройка"),
    ("settings", "/settings", "Настройки"),
]
# Sidebar groups and icons by page key; a new page needs only its line in PAGES (unknown keys go to "Анализ").
NAV_GROUPS = ("Наблюдение", "Анализ", "Система")
NAV_GROUP = {"overview": "Наблюдение", "production": "Наблюдение", "battery": "Наблюдение", "grid": "Наблюдение",
             "economics": "Анализ", "goals": "Анализ", "health": "Анализ", "analysis": "Анализ",
             "quality": "Система", "setup": "Система", "settings": "Система"}
NAV_ICON = {"overview": "home", "production": "sun", "battery": "battery", "grid": "plug", "economics": "money",
            "goals": "target", "health": "pulse", "analysis": "chart", "quality": "shield", "setup": "plug", "settings": "sliders"}


def nav() -> list[tuple[str, list[tuple[str, str, str, str]]]]:
    groups: dict[str, list] = {g: [] for g in NAV_GROUPS}
    for key, href, label in PAGES:
        groups.setdefault(NAV_GROUP.get(key, "Анализ"), []).append((key, href, label, NAV_ICON.get(key, "dot")))
    return [(g, items) for g, items in groups.items() if items]


DEFAULT_PERIOD = {"production": "30d", "battery": "30d", "grid": "30d", "economics": "all", "quality": "30d"}


@app.exception_handler(sqlite3.OperationalError)
async def db_missing(request: Request, exc: sqlite3.OperationalError):
    msg = f"Базата не може да се отвори ({data.DB_PATH}): {exc}. Пусни първо колектора."
    if request.url.path.startswith("/api/"):
        return JSONResponse({"error": msg}, status_code=503)
    return HTMLResponse(f"<!doctype html><meta charset=utf-8><p>{msg}</p>", status_code=503)


def today() -> str:
    return datetime.now(TZ).date().isoformat()


def period(m: data.Model, start: str | None, end: str | None) -> tuple[str, str]:
    first = m.order[0] if m.order else today()
    last = m.order[-1] if m.order else today()
    try:
        s = date.fromisoformat(start).isoformat() if start else (date.fromisoformat(last) - timedelta(days=29)).isoformat()
        e = date.fromisoformat(end).isoformat() if end else last
    except ValueError:
        raise HTTPException(400, "Датите са във формат ГГГГ-ММ-ДД")
    s, e = max(s, first), min(e, last)
    if s > e:
        s = e
    return s, e


def wall_ms(ts: int) -> int:
    """Unix seconds -> milliseconds of Sofia wall time; charts show them as UTC so every browser sees local time."""
    return (ts + int(datetime.fromtimestamp(ts, TZ).utcoffset().total_seconds())) * 1000


def r(x, n=2):
    return round(x, n) if isinstance(x, float) else x


# ---------------------------------------------------------------- pages

def num_bg(x: float, digits: int = 2) -> str:
    """Bulgarian number style for text rendered on the server ("21,08"); the English mode converts it."""
    return f"{x:.{digits}f}".rstrip("0").rstrip(".").replace(".", ",")


def fmt_day(day: str) -> str:
    return f"{day[8:10]}.{day[5:7]}.{day[:4]}"


def site_context(m: data.Model) -> dict:
    """What every page shows about the installation: name, size, currency and the names of the MPPT inputs."""
    st = m.settings
    current = [a for a in m.arrays if a["valid_to"] is None]
    kwp = sum((a["panels"] or 0) * (a["panel_wp"] or 0) / 1000 for a in current)
    bat = [b for b in m.batteries if b["valid_to"] is None]
    kwh = bat[-1]["modules"] * bat[-1]["kwh_per_module"] if bat and bat[-1]["modules"] and bat[-1]["kwh_per_module"] else 0
    site_name = st.get("site_name") or m.station_name or ""
    parts = [site_name] if site_name else []
    if kwp:
        parts.append(f"{num_bg(kwp)} kWp")
    if kwh:
        parts.append(f"{num_bg(kwh)} kWh")
    mppt = {}
    for a in sorted(m.arrays, key=lambda a: a["valid_from"]):
        if a["mppt_channel"] in (1, 2):
            mppt[a["mppt_channel"]] = a["name"]
    def pack(b):
        return f"{b['modules']} × {num_bg(b['kwh_per_module'])} kWh = {num_bg(b['modules'] * b['kwh_per_module'])} kWh"
    batts = [b for b in m.batteries if b["modules"] and b["kwh_per_module"]]
    batt_desc = pack(batts[-1]) if batts else "не е въведена"
    if len(batts) > 1:
        batt_desc += f" от {fmt_day(batts[-1]['valid_from'])} (до тогава {pack(batts[-2])})"
    changes = [{"day": b["valid_from"], "label": f"{num_bg(b['modules'] * b['kwh_per_module'])} kWh"} for b in batts[1:]]
    ccy_code = (st.get("currency") or "EUR").upper()
    return {"tz_name": data.TZ.key, "site_name": site_name, "brand_line": " · ".join(parts) or "Deye", "ccy": appconf.currency_symbol(ccy_code),
            "ccy_code": ccy_code, "lang_default": st.get("language") or "",
            "mppt1": mppt.get(1, "PV1"), "mppt2": mppt.get(2, "PV2"), "has_mppt2": 2 in mppt,
            "batt_desc": batt_desc, "batt_changes": changes,
            "site_lat": m.site[0] if m.site else None, "site_lon": m.site[1] if m.site else None}


def page(request: Request, name: str, **ctx):
    m = data.model()
    return templates.TemplateResponse(request, f"{name}.html", {
        "pages": PAGES, "nav": nav(), "active": name, "first_day": m.order[0] if m.order else today(),
        "last_day": m.order[-1] if m.order else today(), "default_period": DEFAULT_PERIOD.get(name, "30d"),
        **site_context(m), **ctx})


@app.get("/", response_class=HTMLResponse)
def overview(request: Request):
    m = data.model()
    if not m.order and not m.settings.get("setup_done"):
        return RedirectResponse("/setup", status_code=303)
    return page(request, "overview")


@app.get("/setup", response_class=HTMLResponse)
def setup_page(request: Request):
    return page(request, "setup")


@app.get("/production", response_class=HTMLResponse)
def production(request: Request):
    return page(request, "production")


@app.get("/battery", response_class=HTMLResponse)
def battery(request: Request):
    return page(request, "battery")


@app.get("/grid", response_class=HTMLResponse)
def grid(request: Request):
    return page(request, "grid")


@app.get("/economics", response_class=HTMLResponse)
def economics(request: Request):
    return page(request, "economics")


@app.get("/quality", response_class=HTMLResponse)
def quality(request: Request):
    return page(request, "quality")


@app.get("/health", response_class=HTMLResponse)
def health_page(request: Request):
    return page(request, "health")


@app.get("/goals", response_class=HTMLResponse)
def goals_page(request: Request):
    return page(request, "goals")


@app.get("/analysis", response_class=HTMLResponse)
def analysis_page(request: Request):
    return page(request, "analysis")


@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request):
    return page(request, "settings")


# ---------------------------------------------------------------- API

@app.get("/api/meta")
def meta():
    m = data.model()
    return {"first_day": m.order[0] if m.order else None, "last_day": m.order[-1] if m.order else None,
            "today": today(), "arrays": m.arrays, "batteries": m.batteries, "capex": m.capex,
            "last_station_ts": m.last_station_ts, "last_device_ts": m.last_device_ts,
            "db_path": data.DB_PATH, "model_built_at": m.built_at}


@app.get("/api/catalog")
def catalog():
    return [{"id": k, "group": g, "name": n, "unit": u, "note": note} for k, (g, n, u, note) in kpi.CATALOG.items()]


@app.get("/api/kpi")
def api_kpi(start: str | None = Query(None, alias="from"), end: str | None = Query(None, alias="to")):
    m = data.model()
    s, e = period(m, start, end)
    out = kpi.summary(m, s, e, curtail_price())
    base = scenario_payback(m, e).get("base")
    if base:  # F04/F05 by the base scenario (expansion-adjusted profile, escalation, degradation)
        out["kpis"]["F04"]["value"] = base["years"]
        out["kpis"]["F04"]["note"] = "години от пускането до деня, в който натрупаната полза достига CAPEX (базов сценарий)"
        out["kpis"]["F05"]["value"] = base["date"]
        out["payback"]["scenario_years"] = base["years"]
        out["payback"]["scenario_date"] = base["date"]
        out["payback"]["scenario_annual"] = base["annual"]
    out["totals"] = {k: r(v, 3) if not isinstance(v, bool) else v for k, v in out["totals"].items()}
    out["payback"] = {k: r(v, 2) if not isinstance(v, (list, dict)) else v
                      for k, v in out["payback"].items() if k != "forecast"}
    for v in out["kpis"].values():
        v["value"] = r(v["value"], 2)
    return out


SERIES_FIELDS = ("pv", "load", "imp", "exp", "chg", "dis", "imp_day", "imp_night", "pv_direct", "chg_pv",
                 "chg_grid", "cost_with", "cost_without", "benefit", "save_day", "save_night", "cycles",
                 "h_full", "h_low", "h_soc", "pv_estimated", "kwp", "exp_pvgis", "exp_wx", "curtailed", "ghi", "plan",
                 "legacy_pv")


def series_row(label: str, rows: list[dict]) -> dict:
    s = kpi.aggregate(rows)
    out = {"label": label, "days": s["days"]}
    for f in SERIES_FIELDS:
        out[f] = r(s[f], 3) if f != "kwp" else s["kwp"]
    out.update({
        "self_sufficiency": r(s["self_sufficiency"], 1), "self_consumption": r(s["self_consumption"], 1),
        "night_coverage": r(s["night_coverage"], 1), "coverage": r(s["coverage"], 1),
        "specific": r(s["specific"], 3), "pv1": r(s["pv1"], 3), "pv2": r(s["pv2"], 3),
        "specific1": r(s["specific1"], 3), "specific2": r(s["specific2"], 3),
        "soc_min": s["soc_min"], "soc_max": s["soc_max"], "t_full": s["t_full"],
        "temp_batt_max": s["temp_batt_max"], "temp_batt_avg": r(s["temp_batt_avg"], 1),
        "temp_inv_max": s["temp_inv_max"], "temp_inv_avg": r(s["temp_inv_avg"], 1),
        "temp_batt_dh35": r(s["temp_batt_dh35"], 1), "bad": s["bad_soc"] + s["bad_load"] + s["pv_night"],
        "metered": s["metered_days"], "pr": r(s["pr"], 1), "p04": r(s["p04"], 1), "p04_roof": r(s["p04_1"], 1),
        "p04_carport": r(s["p04_2"], 1), "curtailed_share": r(s["curtailed_share"], 1), "f10": r(s["f10"], 1),
        "b04": r(s["b04"], 1), "wx_days": s["wx_days"],
    })
    if not s["wx_days"]:
        out["curtailed"] = out["ghi"] = out["exp_wx"] = None
    return out


@app.get("/api/series")
def api_series(start: str | None = Query(None, alias="from"), end: str | None = Query(None, alias="to"),
               group: str = Query("auto", pattern="^(auto|day|month)$")):
    """Per-day or per-month values for the charts (auto: months above 92 days)."""
    m = data.model()
    s, e = period(m, start, end)
    rows = m.range(s, e)
    if group == "auto":
        group = "month" if len(rows) > 92 else "day"
    if group == "day":
        return {"group": "day", "rows": [series_row(x["day"], [x]) for x in rows]}
    months: dict[str, list[dict]] = {}
    for x in rows:
        months.setdefault(x["day"][:7], []).append(x)
    return {"group": "month", "rows": [series_row(k, v) for k, v in months.items()]}


@app.get("/api/power")
def api_power(day: str | None = None):
    """5-minute station frames of one local day (kW and SOC)."""
    m = data.model()
    day = day or (m.order[-1] if m.order else today())
    t0, t1 = day_bounds(date.fromisoformat(day).isoformat())
    conn = data.connect()
    try:
        rows = conn.execute(
            "SELECT ts, generation_w, consumption_w, wire_w, battery_w, soc FROM station_frame "
            "WHERE station_id = ? AND ts >= ? AND ts < ? ORDER BY ts", (m.station_id, t0, t1)).fetchall()
    finally:
        conn.close()

    def kw(x):
        return round(x / 1000, 3) if x is not None else None

    return {"day": day, "rows": [[wall_ms(x["ts"]), kw(x["generation_w"]), kw(x["consumption_w"]), kw(x["wire_w"]),
                                  kw(x["battery_w"]), x["soc"]] for x in rows],
            "columns": ["ts", "pv_kw", "load_kw", "grid_kw", "battery_kw", "soc"]}


STRING_KEYS = ("DCPowerPV1", "DCPowerPV2", "DCVoltagePV1", "DCVoltagePV2", "DCCurrentPV1", "DCCurrentPV2",
               "Temperature- Battery", "AC Temperature")


@app.get("/api/inverter")
def api_inverter(day: str | None = None):
    """Inverter frames of one day: power, voltage and current per MPPT, temperatures."""
    m = data.model()
    day = day or (m.order[-1] if m.order else today())
    t0, t1 = day_bounds(date.fromisoformat(day).isoformat())
    conn = data.connect()
    try:
        ids = {x["key"]: x["id"] for x in conn.execute(
            f"SELECT id, key FROM metric WHERE key IN ({','.join('?' * len(STRING_KEYS))})", STRING_KEYS)}
        out = {}
        for key in STRING_KEYS:
            if key in ids:
                out[key] = [[wall_ms(t), v] for t, v in conn.execute(
                    "SELECT ts, value FROM measurement WHERE metric_id = ? AND ts >= ? AND ts < ? ORDER BY ts",
                    (ids[key], t0, t1))]
            else:
                out[key] = []
    finally:
        conn.close()
    return {"day": day, "series": out}


@app.get("/api/profile")
def api_profile(start: str | None = Query(None, alias="from"), end: str | None = Query(None, alias="to")):
    """Typical day: mean kWh per local hour over the period."""
    m = data.model()
    s, e = period(m, start, end)
    conn = data.connect()
    try:
        rows = conn.execute(
            "SELECT hour, AVG(pv_kwh) pv, AVG(load_kwh) load, AVG(import_kwh) imp, AVG(charge_kwh) chg, "
            "AVG(discharge_kwh) dis, AVG((soc_min + soc_max) / 2.0) soc FROM energy_hourly "
            "WHERE station_id = ? AND day BETWEEN ? AND ? AND covered_s >= 2700 GROUP BY hour ORDER BY hour",
            (m.station_id, s, e)).fetchall()
    finally:
        conn.close()
    return {"rows": [{k: r(x[k], 3) for k in x.keys()} for x in rows]}


@app.get("/api/soc-heatmap")
def api_soc_heatmap(start: str | None = Query(None, alias="from"), end: str | None = Query(None, alias="to")):
    m = data.model()
    s, e = period(m, start, end)
    days = [d for d in m.order if s <= d <= e]
    cells = [[i, h, round(m.soc_grid[(d, h)])] for i, d in enumerate(days) for h in range(24) if (d, h) in m.soc_grid]
    return {"days": days, "cells": cells}


@app.get("/api/payback")
def api_payback():
    m = data.model()
    last = m.order[-1] if m.order else today()
    pb = kpi.payback(m, last)
    cum, points = 0.0, []
    for d in m.order:
        cum += m.days[d]["benefit"]
        points.append([d, round(cum, 2)])
    capex_steps = []
    total = 0.0
    for c in m.capex:
        total += c["amount_ccy"] or 0.0
        capex_steps.append({"date": c["date"], "total": round(total, 2), "description": c["description"]})
    sc = scenario_payback(m, last)
    return {"actual": points, "forecast": pb.get("forecast", []), "capex": capex_steps,
            "scenarios": [{k: (r(v, 2) if isinstance(v, float) else v) for k, v in x.items()} for x in sc.values()],
            "summary": {k: r(v, 2) for k, v in pb.items() if k not in ("forecast", "profile")},
            "profile": {k: r(v, 3) for k, v in pb["profile"].items()} if "profile" in pb else {}}


@app.get("/api/invoices")
def api_invoices():
    m = data.model()
    pv_start = m.order[0] if m.order else None
    rows = [{"invoice_no": i["invoice_no"], "period_from": i["period_from"], "period_to": i["period_to"],
             "day_kwh": i["day_kwh"], "night_kwh": i["night_kwh"], "total_ccy": r(i["total_ccy"], 2),
             "with_pv": bool(pv_start and i["period_from"] >= pv_start)} for i in m.invoices]
    before = [x for x in rows if pv_start and x["period_to"] < pv_start]
    last12 = before[-12:]
    after = [x for x in rows if x["with_pv"]]
    return {"rows": rows,
            "before_avg_ccy": r(sum(x["total_ccy"] for x in last12) / len(last12), 2) if last12 else None,
            "before_avg_kwh": r(sum(x["day_kwh"] + x["night_kwh"] for x in last12) / len(last12), 0) if last12 else None,
            "after_avg_ccy": r(sum(x["total_ccy"] for x in after) / len(after), 2) if after else None,
            "after_avg_kwh": r(sum(x["day_kwh"] + x["night_kwh"] for x in after) / len(after), 0) if after else None}


@app.get("/healthz")
def healthz():
    """For Docker's healthcheck and the guard: the database opens and has a station frame."""
    conn = data.connect()
    try:
        last = conn.execute("SELECT max(ts) FROM station_frame").fetchone()[0]
    finally:
        conn.close()
    # the desktop app tells its own instance apart from another service on the same port
    return {"ok": True, "last_frame": last, **({"instance": os.environ["PVMON_INSTANCE"]} if os.environ.get("PVMON_INSTANCE") else {})}


def system_status(conn: sqlite3.Connection) -> dict:
    """Problems from the guard's checks (computed here too, so they show even if the guard is down)."""
    problems = guard.assess(conn, db_path=data.DB_PATH)
    hb = guard.guard_heartbeat_problem(conn)
    if hb:
        problems.append(hb)
    else:
        row = conn.execute("SELECT value FROM collector_state WHERE key = 'guard_status'").fetchone()
        seen = {p["code"] for p in problems}
        problems += [p for p in json.loads(row[0]).get("problems", []) if p["code"] not in seen]
    row = conn.execute("SELECT value FROM collector_state WHERE key = 'backup_last_ts'").fetchone()
    level = "bad" if any(p["level"] == "bad" for p in problems) else "warn" if problems else "ok"
    return {"level": level, "problems": problems, "backup_ts": int(float(row[0])) if row else None}


@app.get("/api/status")
def api_status():
    conn = data.connect()
    try:
        return system_status(conn)
    finally:
        conn.close()


@app.get("/api/now")
def api_now():
    """The latest station frame and inverter temperatures."""
    m = data.model()
    conn = data.connect()
    try:
        f = conn.execute("SELECT * FROM station_frame WHERE station_id = ? ORDER BY ts DESC LIMIT 1",
                         (m.station_id,)).fetchone()
        temps = {}
        for key in ("Temperature- Battery", "AC Temperature"):
            row = conn.execute("SELECT m.ts, m.value FROM measurement m JOIN metric k ON k.id = m.metric_id "
                               "WHERE k.key = ? ORDER BY m.ts DESC LIMIT 1", (key,)).fetchone()
            temps[key] = {"ts": row[0], "value": row[1]} if row else None
        system = system_status(conn)
    finally:
        conn.close()
    now = datetime.now(TZ).timestamp()
    if f is None:
        return {"frame": None, "system": system}
    return {"system": system, "ts": f["ts"], "age_min": round((now - f["ts"]) / 60, 1),
            "pv_kw": f["generation_w"] / 1000 if f["generation_w"] is not None else None,
            "load_kw": f["consumption_w"] / 1000 if f["consumption_w"] is not None else None,
            "grid_kw": f["wire_w"] / 1000 if f["wire_w"] is not None else None,
            "battery_kw": f["battery_w"] / 1000 if f["battery_w"] is not None else None,
            "soc": f["soc"], "battery_temp": temps["Temperature- Battery"], "inverter_temp": temps["AC Temperature"]}


@app.get("/api/quality")
def api_quality(start: str | None = Query(None, alias="from"), end: str | None = Query(None, alias="to")):
    m = data.model()
    s, e = period(m, start, end)
    rows = [{"day": x["day"], "coverage": r(x["covered_s"] / x["expected_s"] * 100, 1) if x["expected_s"] else None,
             "frames": x["frames"], "interp": x["interp"], "pv_estimated": x["pv_estimated"],
             "bad_soc": x["bad_soc"], "bad_load": x["bad_load"], "pv_night": x["pv_night"],
             "inverter_data": x["pv1"] is not None, "metered": x["metered"]} for x in m.range(s, e)]
    conn = data.connect()
    try:
        ingest = [dict(x) for x in conn.execute(
            "SELECT ts, job, endpoint, status, points, error FROM ingest_log ORDER BY ts DESC LIMIT 20")]
        check = [dict(x) for x in conn.execute(
            "SELECT * FROM v_daily_check WHERE day BETWEEN ? AND ? ORDER BY day", (s, e))]
    finally:
        conn.close()
    now = datetime.now(TZ).timestamp()
    return {"rows": rows, "ingest": ingest, "check": check,
            "last_station_ts": m.last_station_ts, "last_device_ts": m.last_device_ts,
            "station_age_min": r((now - m.last_station_ts) / 60, 1) if m.last_station_ts else None,
            "device_age_min": r((now - m.last_device_ts) / 60, 1) if m.last_device_ts else None}


# ---------------------------------------------------------------- goals, benchmarks, scenarios

def read_rows(sql: str, args=()) -> list[dict]:
    conn = data.connect()
    try:
        return [dict(x) for x in conn.execute(sql, args)]
    finally:
        conn.close()


def scenarios() -> dict[str, dict]:
    return {x["name"]: x for x in read_rows("SELECT * FROM scenario ORDER BY sort")}


def curtail_price() -> float | None:
    rows = read_rows("SELECT value FROM param WHERE key = 'goal_curtail_value_ccy_kwh' ORDER BY valid_from DESC LIMIT 1")
    return rows[0]["value"] if rows else None


_sc_cache: dict = {}


def scenario_payback(m: data.Model, as_of: str) -> dict[str, dict]:
    sc = scenarios()
    key = (m.built_at, as_of, tuple(tuple(v.items()) for v in sc.values()))
    if _sc_cache.get("key") != key:
        fc = goals.Forecaster(m, as_of)
        _sc_cache.update(key=key, value={n: goals.payback_scenario(m, fc, as_of, x) for n, x in sc.items()})
    return _sc_cache["value"]


def rounded(x):
    if isinstance(x, float):
        return round(x, 2)
    if isinstance(x, dict):
        return {k: rounded(v) for k, v in x.items()}
    if isinstance(x, list):
        return [rounded(v) for v in x]
    return x


_goal_cache: dict = {}


@app.get("/api/goals")
def api_goals(history: bool = True):
    """Goals with their status now and at every month end, the payback scenarios and the benchmarks."""
    m = data.model()
    as_of = m.order[-1] if m.order else today()
    rows = read_rows("SELECT * FROM goal ORDER BY active DESC, id")
    sc = scenarios()
    key = (m.built_at, as_of, history, tuple(tuple(g.items()) for g in rows), tuple(tuple(v.items()) for v in sc.values()))
    if _goal_cache.get("key") != key:
        evaluated = goals.evaluate_all(m, rows, sc, as_of, history=history) if m.order else []
        _goal_cache.update(key=key, value=evaluated)
        snapshot(evaluated, as_of)
    evaluated = _goal_cache["value"]
    pb = scenario_payback(m, as_of)
    params = read_rows("SELECT key, valid_from, value, unit, note FROM param WHERE key LIKE 'goal_%' ORDER BY key")
    return dict(rounded({
        "as_of": as_of, "goals": evaluated,
        "scenarios": [dict(x, payback={k: v for k, v in pb[x["name"]].items() if k != "forecast"}) for x in sc.values()],
        "benchmarks": read_rows("SELECT * FROM benchmark ORDER BY kind, kpi_id, period"),
        "pvgis": read_rows("SELECT p.*, a.name FROM pvgis_monthly p JOIN pv_array a ON a.id = p.array_id ORDER BY array_id, month"),
        "calibration": {"pr": m.bench.get("pr"), "pr_hourly": m.bench.get("pr_hourly"), "weather_days": m.bench.get("weather_days")},
        "kpis": [{"id": k, "name": n, "unit": u, "direction": d} for k, (n, u, d) in goals.GOAL_KPIS.items()],
        "period_types": goals.PERIOD_TYPES, "statuses": goals.STATUS_LABEL,
    }), params=params)  # params unrounded: the form writes them back


def snapshot(evaluated: list[dict], as_of: str) -> None:
    """Keep the status as it was seen each day (goal_status), once per goal and day."""
    def fn(conn):
        for g in evaluated:
            c = g["current"]
            conn.execute("INSERT INTO goal_status (goal_id, as_of, actual, forecast, status) VALUES (?, ?, ?, ?, ?) "
                         "ON CONFLICT(goal_id, as_of) DO UPDATE SET actual = excluded.actual, "
                         "forecast = excluded.forecast, status = excluded.status",
                         (g["id"], as_of, c["actual"], c["forecast"], c["status"]))
    try:
        conn = settings.connect_rw()
        try:
            with conn:
                fn(conn)
        finally:
            conn.close()
    except Exception:  # a read-only deployment still shows the goals
        pass


class GoalIn(BaseModel):
    kpi_id: str
    title: str | None = Field(None, max_length=120)
    period_type: str = "year"
    period_start: str | None = None
    period_end: str | None = None
    target: float
    direction: str | None = None
    warn_level: float | None = None
    scenario: str | None = None
    note: str | None = Field(None, max_length=500)
    active: bool = True


def check_goal(g: GoalIn) -> dict:
    if g.kpi_id not in goals.GOAL_KPIS:
        raise HTTPException(400, f"Неизвестен KPI за цел: {g.kpi_id}")
    if g.period_type not in goals.PERIOD_TYPES:
        raise HTTPException(400, "Невалиден период")
    direction = g.direction or goals.GOAL_KPIS[g.kpi_id][2]
    if direction not in ("ge", "le"):
        raise HTTPException(400, "Посоката е ge (поне) или le (най-много)")
    for v in (g.period_start, g.period_end):
        if v:
            try:
                date.fromisoformat(v if len(v) > 7 else v + "-01" if len(v) == 7 else v + "-01-01")
            except ValueError:
                raise HTTPException(400, "Датите са във формат ГГГГ, ГГГГ-ММ или ГГГГ-ММ-ДД")
    if g.period_type == "custom" and not (g.period_start and g.period_end):
        raise HTTPException(400, "За период от–до са нужни и двете дати")
    if g.scenario and g.scenario not in scenarios():
        raise HTTPException(400, "Няма такъв сценарий")
    return {"kpi_id": g.kpi_id, "title": g.title or goals.GOAL_KPIS[g.kpi_id][0], "period_type": g.period_type,
            "period_start": g.period_start or None, "period_end": g.period_end or None, "target": g.target,
            "direction": direction, "warn_level": g.warn_level, "scenario": g.scenario, "note": g.note,
            "active": int(g.active)}


@app.post("/api/goals", status_code=201)
def create_goal(g: GoalIn):
    row = check_goal(g)
    cols = list(row) + ["created_at", "updated_at"]
    vals = list(row.values()) + [settings.now(), settings.now()]
    gid = settings.write(lambda c: c.execute(
        f"INSERT INTO goal ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", vals).lastrowid)
    return {"id": gid}


@app.put("/api/goals/{goal_id}")
def update_goal(goal_id: int, g: GoalIn):
    row = check_goal(g)
    def fn(c):
        cur = c.execute(f"UPDATE goal SET {', '.join(k + ' = ?' for k in row)}, updated_at = ? WHERE id = ?",
                        list(row.values()) + [settings.now(), goal_id])
        if cur.rowcount == 0:
            raise HTTPException(404, "Няма такава цел")
        c.execute("DELETE FROM goal_status WHERE goal_id = ?", (goal_id,))  # the old history no longer applies
    settings.write(fn)
    return {"id": goal_id}


@app.delete("/api/goals/{goal_id}")
def delete_goal(goal_id: int):
    def fn(c):
        c.execute("DELETE FROM goal_status WHERE goal_id = ?", (goal_id,))
        if c.execute("DELETE FROM goal WHERE id = ?", (goal_id,)).rowcount == 0:
            raise HTTPException(404, "Няма такава цел")
    settings.write(fn)
    return {"deleted": goal_id}


class ScenarioIn(BaseModel):
    price_escalation_pct: float = Field(ge=-20, le=30)
    degradation_pct: float = Field(ge=0, le=5)
    battery_replacement_year: int | None = Field(None, ge=1, le=40)
    battery_replacement_ccy: float | None = Field(None, ge=0)
    inverter_replacement_year: int | None = Field(None, ge=1, le=40)
    inverter_replacement_ccy: float | None = Field(None, ge=0)
    discount_rate_pct: float | None = Field(None, ge=0, le=20)
    note: str | None = Field(None, max_length=500)


@app.put("/api/scenarios/{name}")
def update_scenario(name: str, x: ScenarioIn):
    vals = x.model_dump()
    def fn(c):
        cur = c.execute(f"UPDATE scenario SET {', '.join(k + ' = ?' for k in vals)}, updated_at = ? WHERE name = ?",
                        list(vals.values()) + [settings.now(), name])
        if cur.rowcount == 0:
            raise HTTPException(404, "Няма такъв сценарий")
    settings.write(fn)
    return {"name": name}


class PlanIn(BaseModel):
    months: dict[int, float | None]
    note: str | None = Field(None, max_length=500)


@app.put("/api/plan")
def update_plan(x: PlanIn):
    """The business plan for F10: net benefit € per calendar month (benchmark kind 'plan', F01)."""
    if any(k not in range(1, 13) for k in x.months):
        raise HTTPException(400, "Месеците са 1–12")
    def fn(c):
        for mo, v in x.months.items():
            period_ = f"{mo:02d}"
            c.execute("DELETE FROM benchmark WHERE kind = 'plan' AND kpi_id = 'F01' AND period = ?", (period_,))
            if v is not None:
                c.execute("INSERT INTO benchmark (kind, kpi_id, period, value, note) VALUES ('plan', 'F01', ?, ?, ?)",
                          (period_, v, x.note if mo == min(x.months) else None))
    settings.write(fn)
    return {"months": len(x.months)}


class ParamIn(BaseModel):
    value: float


DATED_PARAMS = {"health_batt_app_soh_pct"}


@app.put("/api/params/{key}")
def update_param(key: str, x: ParamIn):
    if not key.startswith(settings.PARAM_PREFIXES):
        raise HTTPException(403, "От сайта се сменят само параметрите на целите и на здравето")
    def fn(c):
        if key in DATED_PARAMS:   # readings: each one is kept with today's date
            c.execute("INSERT INTO param (key, valid_from, value, unit, note) SELECT key, ?, ?, unit, note FROM param "
                      "WHERE key = ? ORDER BY valid_from DESC LIMIT 1 ON CONFLICT (key, valid_from) DO UPDATE SET value = excluded.value",
                      (datetime.now(TZ).date().isoformat(), x.value, key))
            return
        cur = c.execute("UPDATE param SET value = ? WHERE key = ? AND valid_from = "
                        "(SELECT max(valid_from) FROM param WHERE key = ?)", (x.value, key, key))
        if cur.rowcount == 0:
            raise HTTPException(404, "Няма такъв параметър")
    settings.write(fn)
    return {"key": key, "value": x.value}


# ---------------------------------------------------------------- health

@app.get("/api/health")
def api_health(detail: bool = True):
    """Current health per component and, with detail, the monthly stress, temperatures and the wear forecast."""
    m = data.model()
    h = m.health
    out = {"status": health.status(m, h, datetime.now(TZ).timestamp())}
    if detail:
        out.update({k: v for k, v in h.items()})
        out["param_rows"] = read_rows("SELECT key, valid_from, value, unit, note FROM param p WHERE key LIKE 'health_%' AND valid_from = "
                                     "(SELECT max(valid_from) FROM param q WHERE q.key = p.key) ORDER BY key")
        out["daily"] = [{"day": d, "temp_batt_max": m.days[d]["temp_batt_max"], "temp_batt_avg": rounded(m.days[d]["temp_batt_avg"]),
                         "temp_inv_max": m.days[d]["temp_inv_max"], "h_full": rounded(m.days[d]["h_full"]),
                         "h_low": rounded(m.days[d]["h_low"])} for d in m.order]
    return out


# ---------------------------------------------------------------- analysis (the owner's own LLM)

@app.get("/api/analysis")
def api_analysis():
    """The typical-year chart, the model settings' state and the last AI analysis (web/analysis.py)."""
    m = data.model()
    conn = data.connect()
    try:
        cfg = llm.config(conn)
        ok, why = llm.ready(cfg)
        run = analysis.latest(conn)
    finally:
        conn.close()
    return {"as_of": m.order[-1] if m.order else None, **analysis.typical_year(m),
            "llm": {"ready": ok, "why": why, "provider": cfg["provider"], "model": cfg["model"] or cfg["cli_command"]},
            "run": run}


class AnalysisIn(BaseModel):
    language: str | None = Field(None, max_length=8)
    question: str | None = Field(None, max_length=1000)


@app.post("/api/analysis/run", status_code=202)
def run_analysis(x: AnalysisIn):
    m = data.model()
    conn = data.connect()
    try:
        lang = appconf.get(conn, "analysis_language") or x.language or appconf.get(conn, "language") or "en"
    finally:
        conn.close()
    try:
        settings.write(analysis.fail_stale)
        run_id = analysis.start(m, scenarios(), read_rows("SELECT * FROM goal ORDER BY id"), curtail_price(),
                                lang.lower()[:8], x.question)
    except llm.LlmError as exc:
        raise HTTPException(409, str(exc))
    return {"id": run_id}
