"""Settings page API: every manual parameter of the system and the electricity invoices.

Writes go through settings.connect_rw() (an SQLite authorizer limits them to the manual
tables). Energy data, frames and the inverter's own settings stay read-only, and nothing
here talks to Deye.
"""
from __future__ import annotations

import json
import subprocess
import tempfile
from datetime import date, timedelta
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from collector import invoices as inv_parser

from . import data, settings

router = APIRouter()
MAX_PDF_BYTES = 10 * 1024 * 1024

STATUS = [["confirmed", "потвърдено"], ["assumed", "прието"], ["estimate", "оценка"]]
CAPEX_STATUS = [["invoiced", "фактурирано"], ["paid", "платено"], ["estimate", "оценка"], ["unknown", "неизвестно"]]


def f(key, label, kind="number", **kw):
    return {"key": key, "label": label, "type": kind, **kw}


# Editable tables: fields, validation and how a new row closes the previous one.
# close_by: a new open-ended row (valid_to empty) ends the open row of the same group on its valid_from.
TABLES = {
    "pv_array": {
        "title": "Панелни масиви", "order": "valid_from, mppt_channel", "close_by": ("mppt_channel",), "delete": False,
        "fields": [
            f("name", "Име", "text", required=True, maxlength=60),
            f("mppt_channel", "MPPT вход", "int", min=1, max=4, required=True),
            f("panels", "Брой панели", "int", min=1, max=200, required=True),
            f("panel_wp", "Мощност на панел", min=50, max=1000, unit="Wp", required=True),
            f("strings", "Стрингове", "int", min=1, max=10),
            f("panel_model", "Модел", "text", maxlength=80),
            f("azimuth_deg", "Азимут (180 = юг)", min=0, max=360, unit="°"),
            f("tilt_deg", "Наклон", min=0, max=90, unit="°"),
            f("shading_note", "Засенчване", "text", maxlength=200),
            f("valid_from", "В сила от", "date", required=True),
            f("valid_to", "До (без да е включен)", "date"),
            f("status", "Статус", "select", options=STATUS, required=True),
            f("note", "Бележка", "text", maxlength=500),
        ],
    },
    "battery_config": {
        "title": "Батерия", "order": "valid_from", "close_by": (), "delete": True,
        "fields": [
            f("modules", "Брой модули", "int", min=1, max=40, required=True),
            f("kwh_per_module", "kWh на модул", min=0.5, max=50, unit="kWh", required=True),
            f("soc_min_normal", "Минимален SOC (нормален)", min=0, max=100, unit="%"),
            f("soc_min_emergency", "Минимален SOC (авариен)", min=0, max=100, unit="%"),
            f("charge_limit_a", "Ток на заряд", min=0, max=500, unit="A"),
            f("discharge_limit_a", "Ток на разряд", min=0, max=500, unit="A"),
            f("valid_from", "В сила от", "date", required=True),
            f("valid_to", "До (без да е включен)", "date"),
            f("status", "Статус", "select", options=STATUS, required=True),
            f("note", "Бележка", "text", maxlength=500),
        ],
    },
    "tariff": {
        "title": "Тарифи за ток", "order": "valid_from, zone", "close_by": ("zone",), "delete": True,
        "fields": [
            f("zone", "Зона", "select", options=[["day", "дневна"], ["night", "нощна"], ["single", "единна"]], required=True),
            f("price_per_kwh", "Цена с всички такси", min=0, max=2, step=0.00001, unit="€/kWh", required=True),
            f("vat_included", "Цената е с ДДС", "bool"),
            f("valid_from", "В сила от", "date", required=True),
            f("valid_to", "До (без да е включен)", "date"),
            f("note", "Бележка", "text", maxlength=200),
        ],
    },
    "feed_in_price": {
        "title": "Изкупна цена", "order": "valid_from", "close_by": (), "delete": True,
        "fields": [
            f("price_per_kwh", "Цена", min=0, max=2, step=0.00001, unit="€/kWh", required=True),
            f("valid_from", "В сила от", "date", required=True),
            f("valid_to", "До (без да е включен)", "date"),
            f("contract_ref", "Договор", "text", maxlength=120),
            f("note", "Бележка", "text", maxlength=500),
        ],
    },
    "capex_item": {
        "title": "Инвестиция (CAPEX)", "order": "date", "close_by": None, "delete": True,
        "fields": [
            f("date", "Дата", "date", required=True),
            f("description", "Описание", "text", required=True, maxlength=200),
            f("amount_ccy", "Сума", min=-1e6, max=1e6, step=0.01, unit="€", required=True),
            f("vat_included", "Сумата е с ДДС", "bool"),
            f("status", "Статус", "select", options=CAPEX_STATUS, required=True),
            f("document_ref", "Документ", "text", maxlength=120),
            f("note", "Бележка", "text", maxlength=500),
        ],
    },
    "opex_item": {
        "title": "Разходи за поддръжка (OPEX)", "order": "date", "close_by": None, "delete": True,
        "fields": [
            f("date", "Дата", "date", required=True),
            f("description", "Описание", "text", required=True, maxlength=200),
            f("amount_ccy", "Сума с ДДС", min=-1e5, max=1e5, step=0.01, unit="€", required=True),
            f("category", "Категория", "select", required=False,
              options=[["service", "сервиз"], ["cleaning", "почистване"], ["insurance", "застраховка"],
                       ["repair", "ремонт"], ["other", "друго"]]),
        ],
    },
}

# Parameters in the param table: label, group and allowed range. Unknown keys are shown
# with their note and accept any number.
PARAM_GROUPS = [
    ["economy", "Икономика"], ["site", "Обект и модели"], ["goal", "Цели (и на страница „Цели“)"],
    ["health", "Модел за износване (и на страница „Здраве“)"], ["other", "Други"],
]
PARAMS = {
    "vat_rate": ("economy", "ДДС върху тока (0.20 = 20%)", 0, 0.5),
    "goal_curtail_value_ccy_kwh": ("goal", "Цена на пропуснатата енергия", 0, 2),
    "inverter_rated_power_w": ("site", "Номинална мощност на инвертора", 1000, 100000),
    "site_lat": ("site", "Географска ширина", -90, 90),
    "site_lon": ("site", "Географска дължина", -180, 180),
    "pvgis_loss_pct": ("site", "Системни загуби за PVGIS", 0, 50),
    "health_batt_rated_cycles": ("health", "Цикли до края на живота", 100, 50000),
    "health_batt_eol_pct": ("health", "Край на живота: остатъчен капацитет", 30, 99),
    "health_batt_calendar_fade_pct": ("health", "Календарно стареене", 0, 10),
    "health_batt_high_soc_factor": ("health", "Ускорение при SOC ≥ 90%", 0, 5),
    "health_batt_break_in_months": ("health", "Начален период на по-бързо стареене", 0, 60),
    "health_batt_reserve_pct": ("health", "Резерв на капацитета над номиналния", 0, 20),
    "health_batt_app_soh_pct": ("health", "SOH в приложението Deye", 0, 110),
    "health_inv_design_life_years": ("health", "Проектен живот на инвертора", 1, 40),
    "health_inv_ref_temp_c": ("health", "Нормална температура на инвертора", 0, 90),
}

DEVICE_LABELS = {
    "battCapacity": ("Капацитет на батерията (по настройка)", "Ah"), "battLowCapacity": ("Нисък SOC", "%"),
    "battShutDownCapacity": ("SOC на изключване", "%"), "maxChargeCurrent": ("Макс. ток на заряд", "A"),
    "maxDischargeCurrent": ("Макс. ток на разряд", "A"), "energyPattern": ("Приоритет на енергията", ""),
    "maxSellPower": ("Макс. мощност към мрежата", "W"), "maxSolarPower": ("Макс. PV мощност", "W"),
    "systemWorkMode": ("Режим на работа", ""), "zeroExportPower": ("Мощност при нулев износ", "W"),
}
DEVICE_KIND = {"battery": "Батерия", "system": "Система", "tou": "Времеви интервали (TOU)"}


def rows(conn, sql: str, args=()) -> list[dict]:
    return [dict(x) for x in conn.execute(sql, args)]


def r2(x, n=2):
    return round(x, n) if isinstance(x, float) else x


def iso(v: str, label: str) -> str:
    try:
        return date.fromisoformat(v).isoformat()
    except (TypeError, ValueError):
        raise HTTPException(400, f"{label}: датата е във формат ГГГГ-ММ-ДД")


def clean(spec: dict, body: dict) -> dict:
    """Validate a row against its field list; returns the values to store."""
    out = {}
    for fd in spec["fields"]:
        k, lbl, kind = fd["key"], fd["label"], fd["type"]
        v = body.get(k)
        if isinstance(v, str):
            v = v.strip()
        if v in (None, ""):
            if fd.get("required"):
                raise HTTPException(400, f"„{lbl}“ е задължително")
            out[k] = 0 if kind == "bool" else None
            continue
        if kind == "bool":
            out[k] = 1 if v in (True, 1, "1", "true", "on") else 0
        elif kind in ("number", "int"):
            try:
                v = float(v)
            except (TypeError, ValueError):
                raise HTTPException(400, f"„{lbl}“ трябва да е число")
            if kind == "int":
                if v != int(v):
                    raise HTTPException(400, f"„{lbl}“ трябва да е цяло число")
                v = int(v)
            if ("min" in fd and v < fd["min"]) or ("max" in fd and v > fd["max"]):
                raise HTTPException(400, f"„{lbl}“ е между {fd.get('min')} и {fd.get('max')}")
            out[k] = v
        elif kind == "date":
            out[k] = iso(v, lbl)
        elif kind == "select":
            if v not in [o[0] for o in fd["options"]]:
                raise HTTPException(400, f"„{lbl}“: невалидна стойност")
            out[k] = v
        else:
            v = str(v)
            if len(v) > fd.get("maxlength", 500):
                raise HTTPException(400, f"„{lbl}“ е твърде дълго")
            out[k] = v
    if out.get("valid_to") and out.get("valid_from") and out["valid_to"] <= out["valid_from"]:
        raise HTTPException(400, "„До“ трябва да е след „В сила от“")
    return out


def spec_of(table: str) -> dict:
    if table not in TABLES:
        raise HTTPException(404, "Няма такава таблица")
    return TABLES[table]


# ---------------------------------------------------------------- read

def invoice_rows(conn, m: data.Model) -> tuple[list[dict], list[dict]]:
    """Invoices with the Deye import over the same period (where Deye has data) and the gaps between them."""
    out = []
    lines = {r["invoice_no"]: r["n"] for r in conn.execute("SELECT invoice_no, COUNT(*) AS n FROM grid_invoice_line GROUP BY invoice_no")}
    subs = {r["invoice_no"]: r["n"] for r in conn.execute(
        "SELECT invoice_no, COUNT(DISTINCT period_from) AS n FROM grid_invoice_reading GROUP BY invoice_no")}
    for i in rows(conn, "SELECT * FROM grid_invoice ORDER BY period_from"):
        days = [m.days[d] for d in m.order if i["period_from"] <= d <= i["period_to"]]
        full = len(days) == (date.fromisoformat(i["period_to"]) - date.fromisoformat(i["period_from"])).days + 1
        kwh = (i["day_kwh"] or 0) + (i["night_kwh"] or 0)
        out.append({**{k: r2(v) for k, v in i.items()},
                    "kwh": r2(kwh, 1), "lines": lines.get(i["invoice_no"], 0), "sub_periods": subs.get(i["invoice_no"], 0),
                    "manual": not lines.get(i["invoice_no"]),
                    "eur_per_kwh": r2(i["total_ccy"] / kwh, 4) if i["total_ccy"] and kwh else None,
                    "deye_kwh": r2(sum(d["imp_day"] + d["imp_night"] for d in days), 1) if full and days else None})
    gaps = []
    for a, b in zip(out, out[1:]):
        nxt = (date.fromisoformat(a["period_to"]) + timedelta(days=1)).isoformat()
        if b["period_from"] > nxt:
            gaps.append({"from": nxt, "to": (date.fromisoformat(b["period_from"]) - timedelta(days=1)).isoformat()})
    return out, gaps


@router.get("/api/settings")
def api_settings():
    m = data.model()
    conn = data.connect()
    try:
        tables = {}
        for name, spec in TABLES.items():
            items = rows(conn, f"SELECT * FROM {name} ORDER BY {spec['order']}")
            if name == "tariff":
                vat = settings_vat(conn)
                for t in items:
                    t["price_with_vat"] = round(t["price_per_kwh"] * (1 if t["vat_included"] else 1 + vat), 5)
                    t["from_invoices"] = (t["note"] or "").startswith("Електрохолд")
                    t.pop("window_json", None)
                    t["fees"] = json.loads(t.pop("fees_json") or "null")
            tables[name] = {"title": spec["title"], "fields": spec["fields"], "can_delete": spec["delete"],
                            "rows": [{k: r2(v, 5) for k, v in x.items()} for x in items]}
        params: dict[str, dict] = {}
        for p in rows(conn, "SELECT * FROM param ORDER BY key, valid_from"):
            group, label, lo, hi = PARAMS.get(p["key"], (p["key"].split("_")[0] if p["key"].startswith(("goal_", "health_")) else "other",
                                                         p["note"] or p["key"], None, None))
            x = params.setdefault(p["key"], {"key": p["key"], "group": group, "label": label, "unit": p["unit"],
                                             "min": lo, "max": hi, "history": []})
            x["history"].append({"valid_from": p["valid_from"], "value": p["value"], "note": p["note"]})
            x["value"], x["valid_from"], x["note"] = p["value"], p["valid_from"], p["note"]
            x["unit"] = p["unit"] or x["unit"]
        device = []
        for s in rows(conn, "SELECT * FROM v_device_settings_current ORDER BY kind"):
            js = json.loads(s["settings_json"])
            if s["kind"] == "tou":
                items = [{"label": f"{x.get('time', '')[:2]}:{x.get('time', '')[2:]}",
                          "value": f"SOC {x.get('soc')}% · {x.get('power')} W · заряд от мрежата {'да' if x.get('enableGridCharge') else 'не'}"}
                         for x in js.get("timeUseSettingItems", [])]
            else:
                items = [{"label": DEVICE_LABELS.get(k, (k, ""))[0], "value": f"{v} {DEVICE_LABELS.get(k, (k, ''))[1]}".strip()}
                         for k, v in js.items()]
            device.append({"kind": s["kind"], "title": DEVICE_KIND.get(s["kind"], s["kind"]), "fetched_at": s["fetched_at"],
                           "items": items})
        inv, gaps = invoice_rows(conn, m)
    finally:
        conn.close()
    return {"tables": tables, "params": list(params.values()), "param_groups": PARAM_GROUPS, "device": device,
            "invoices": inv, "invoice_gaps": gaps, "pv_start": m.order[0] if m.order else None}


def settings_vat(conn) -> float:
    row = conn.execute("SELECT value FROM param WHERE key = 'vat_rate' ORDER BY valid_from DESC LIMIT 1").fetchone()
    return row[0] if row and row[0] is not None else 0.20


# ---------------------------------------------------------------- tables

def close_previous(c, table: str, spec: dict, row: dict, row_id: int | None) -> None:
    """A new open-ended row ends the open row of the same group (e.g. the old battery) on its start date."""
    if spec["close_by"] is None or row.get("valid_to"):
        return
    where = " AND ".join([f"{k} = ?" for k in spec["close_by"]] + ["valid_to IS NULL", "valid_from < ?", "id != ?"])
    c.execute(f"UPDATE {table} SET valid_to = ? WHERE {where}",
              [row["valid_from"]] + [row[k] for k in spec["close_by"]] + [row["valid_from"], row_id or -1])


@router.post("/api/settings/table/{table}", status_code=201)
async def create_row(table: str, request: Request):
    spec = spec_of(table)
    row = clean(spec, await request.json())

    def fn(c):
        if table == "tariff" and (row["note"] or "").startswith("Електрохолд"):
            row["note"] = "ръчно: " + row["note"]   # rows from invoices are rebuilt on each PDF import
        cur = c.execute(f"INSERT INTO {table} ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})", list(row.values()))
        close_previous(c, table, spec, row, cur.lastrowid)
        return cur.lastrowid
    return {"id": settings.write(fn)}


@router.put("/api/settings/table/{table}/{row_id}")
async def update_row(table: str, row_id: int, request: Request):
    spec = spec_of(table)
    row = clean(spec, await request.json())

    def fn(c):
        cur = c.execute(f"UPDATE {table} SET {', '.join(k + ' = ?' for k in row)} WHERE id = ?", list(row.values()) + [row_id])
        if cur.rowcount == 0:
            raise HTTPException(404, "Няма такъв запис")
    settings.write(fn)
    return {"id": row_id}


@router.delete("/api/settings/table/{table}/{row_id}")
def delete_row(table: str, row_id: int):
    spec = spec_of(table)
    if not spec["delete"]:
        raise HTTPException(400, "Този запис не се трие; затвори го с дата „До“ и добави нов")

    def fn(c):
        if table == "battery_config" and c.execute("SELECT COUNT(*) FROM battery_config").fetchone()[0] <= 1:
            raise HTTPException(400, "Остава поне една конфигурация на батерията")
        old = c.execute(f"SELECT * FROM {table} WHERE id = ?", (row_id,)).fetchone()
        if old is None:
            raise HTTPException(404, "Няма такъв запис")
        c.execute(f"DELETE FROM {table} WHERE id = ?", (row_id,))
        if spec["close_by"] is not None:  # the row it had closed runs on again, so no gap is left
            where = " AND ".join([f"{k} = ?" for k in spec["close_by"]] + ["valid_to = ?"])
            c.execute(f"UPDATE {table} SET valid_to = ? WHERE {where}",
                      [old["valid_to"]] + [old[k] for k in spec["close_by"]] + [old["valid_from"]])
    settings.write(fn)
    return {"deleted": row_id}


# ---------------------------------------------------------------- params

@router.put("/api/settings/param/{key}")
async def update_param(key: str, request: Request):
    """mode 'correct' changes the value from valid_from (default: the latest); 'new' adds a value from a date on."""
    body = await request.json()
    try:
        value = float(body.get("value"))
    except (TypeError, ValueError):
        raise HTTPException(400, "Стойността трябва да е число")
    _, label, lo, hi = PARAMS.get(key, (None, key, None, None))
    if (lo is not None and value < lo) or (hi is not None and value > hi):
        raise HTTPException(400, f"„{label}“ е между {lo} и {hi}")
    mode = body.get("mode", "correct")
    valid_from = iso(body["valid_from"], "От дата") if body.get("valid_from") else None

    def fn(c):
        if c.execute("SELECT 1 FROM param WHERE key = ?", (key,)).fetchone() is None:
            raise HTTPException(404, "Няма такъв параметър")
        if mode == "new":
            if not valid_from:
                raise HTTPException(400, "Посочи от коя дата важи новата стойност")
            c.execute("INSERT INTO param (key, valid_from, value, unit, note) SELECT key, ?, ?, unit, note FROM param "
                      "WHERE key = ? ORDER BY valid_from DESC LIMIT 1 "
                      "ON CONFLICT (key, valid_from) DO UPDATE SET value = excluded.value", (valid_from, value, key))
        else:
            cur = c.execute("UPDATE param SET value = ? WHERE key = ? AND valid_from = "
                            "COALESCE(?, (SELECT max(valid_from) FROM param WHERE key = ?))", (value, key, valid_from, key))
            if cur.rowcount == 0:
                raise HTTPException(404, "Няма стойност от тази дата")
    settings.write(fn)
    return {"key": key, "value": value}


@router.delete("/api/settings/param/{key}/{valid_from}")
def delete_param_value(key: str, valid_from: str):
    def fn(c):
        if c.execute("SELECT COUNT(*) FROM param WHERE key = ?", (key,)).fetchone()[0] <= 1:
            raise HTTPException(400, "Това е единствената стойност на параметъра; промени я вместо да я триеш")
        if c.execute("DELETE FROM param WHERE key = ? AND valid_from = ?", (key, valid_from)).rowcount == 0:
            raise HTTPException(404, "Няма стойност от тази дата")
    settings.write(fn)
    return {"deleted": [key, valid_from]}


# ---------------------------------------------------------------- invoices

INVOICE_FIELDS = {
    "fields": [
        f("invoice_no", "Номер на фактурата", "text", maxlength=40),
        f("issued", "Дата на издаване", "date"),
        f("period_from", "Период от", "date", required=True),
        f("period_to", "Период до (включително)", "date", required=True),
        f("day_kwh", "Дневна енергия", min=0, max=100000, unit="kWh", required=True),
        f("night_kwh", "Нощна енергия", min=0, max=100000, unit="kWh", required=True),
        f("total_ccy", "Сума за плащане (с ДДС)", min=-100000, max=100000, step=0.01, unit="€", required=True),
        f("tax_base_ccy", "Данъчна основа (без ДДС)", min=-100000, max=100000, step=0.01, unit="€"),
        f("energy_ccy", "От тях за енергия (без ДДС)", min=-100000, max=100000, step=0.01, unit="€"),
        f("network_ccy", "От тях мрежови услуги (без ДДС)", min=-100000, max=100000, step=0.01, unit="€"),
        f("meter_no", "Електромер №", "text", maxlength=40),
    ],
}


def overlap(c, p_from: str, p_to: str, own: str | None) -> dict | None:
    row = c.execute("SELECT invoice_no, period_from, period_to FROM grid_invoice WHERE period_from <= ? AND period_to >= ? "
                    "AND invoice_no != ? ORDER BY period_from LIMIT 1", (p_to, p_from, own or "")).fetchone()
    return dict(row) if row else None


def overlap_error(o: dict) -> HTTPException:
    return HTTPException(409, f"Периодът се застъпва с фактура {o['invoice_no']} "
                              f"({date.fromisoformat(o['period_from']):%d.%m.%Y} – {date.fromisoformat(o['period_to']):%d.%m.%Y})")


def manual_invoice(body: dict) -> dict:
    x = clean(INVOICE_FIELDS, body)
    if x["period_to"] < x["period_from"]:
        raise HTTPException(400, "Краят на периода е преди началото")
    x["invoice_no"] = x["invoice_no"] or f"ръчно-{x['period_from']}"
    x["issued"] = x["issued"] or x["period_to"]
    if x["tax_base_ccy"] is None:
        x["tax_base_ccy"] = round(x["total_ccy"] / 1.2, 2)
    x["vat_ccy"] = round(x["total_ccy"] - x["tax_base_ccy"], 2)
    return x


def store_manual(c, x: dict, old_no: str | None) -> None:
    """Store a hand-entered invoice. Editing an imported one keeps its readings and lines while period and kWh stay."""
    keep = False
    if old_no:
        old = c.execute("SELECT * FROM grid_invoice WHERE invoice_no = ?", (old_no,)).fetchone()
        if old is None:
            raise HTTPException(404, "Няма такава фактура")
        keep = (old["period_from"], old["period_to"], old["day_kwh"], old["night_kwh"]) == \
               (x["period_from"], x["period_to"], x["day_kwh"], x["night_kwh"]) and old_no == x["invoice_no"]
        source = old["source_file"]
        if not keep:
            c.execute("DELETE FROM grid_invoice_reading WHERE invoice_no = ?", (old_no,))
            c.execute("DELETE FROM grid_invoice_line WHERE invoice_no = ?", (old_no,))
            source = "ръчно въведена"
        c.execute("DELETE FROM grid_invoice WHERE invoice_no = ?", (old_no,))
    else:
        if c.execute("SELECT 1 FROM grid_invoice WHERE invoice_no = ?", (x["invoice_no"],)).fetchone():
            raise HTTPException(409, f"Фактура {x['invoice_no']} вече е въведена")
        source = "ръчно въведена"
    c.execute("INSERT INTO grid_invoice (invoice_no, issued, period_from, period_to, meter_no, day_kwh, night_kwh, energy_ccy, "
              "network_ccy, tax_base_ccy, vat_ccy, total_ccy, source_file) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
              (x["invoice_no"], x["issued"], x["period_from"], x["period_to"], x["meter_no"], x["day_kwh"], x["night_kwh"],
               x["energy_ccy"], x["network_ccy"], x["tax_base_ccy"], x["vat_ccy"], x["total_ccy"], source))
    if not keep:
        c.executemany("INSERT INTO grid_invoice_reading (invoice_no, period_from, period_to, zone, kwh) VALUES (?, ?, ?, ?, ?)",
                      [(x["invoice_no"], x["period_from"], x["period_to"], z, x[z + "_kwh"]) for z in ("day", "night")])


@router.get("/api/settings/invoice-fields")
def invoice_fields():
    return INVOICE_FIELDS


@router.post("/api/settings/invoices", status_code=201)
async def create_invoice(request: Request):
    x = manual_invoice(await request.json())

    def fn(c):
        if o := overlap(c, x["period_from"], x["period_to"], None):
            raise overlap_error(o)
        store_manual(c, x, None)
    settings.write(fn)
    return {"invoice_no": x["invoice_no"]}


@router.put("/api/settings/invoices/{invoice_no}")
async def update_invoice(invoice_no: str, request: Request):
    x = manual_invoice(await request.json())

    def fn(c):
        if x["invoice_no"] != invoice_no and c.execute("SELECT 1 FROM grid_invoice WHERE invoice_no = ?", (x["invoice_no"],)).fetchone():
            raise HTTPException(409, f"Фактура {x['invoice_no']} вече е въведена")
        if o := overlap(c, x["period_from"], x["period_to"], invoice_no):
            raise overlap_error(o)
        store_manual(c, x, invoice_no)
    settings.write(fn)
    return {"invoice_no": x["invoice_no"]}


@router.delete("/api/settings/invoices/{invoice_no}")
def delete_invoice(invoice_no: str):
    def fn(c):
        c.execute("DELETE FROM grid_invoice_reading WHERE invoice_no = ?", (invoice_no,))
        c.execute("DELETE FROM grid_invoice_line WHERE invoice_no = ?", (invoice_no,))
        if c.execute("DELETE FROM grid_invoice WHERE invoice_no = ?", (invoice_no,)).rowcount == 0:
            raise HTTPException(404, "Няма такава фактура")
    settings.write(fn)
    return {"deleted": invoice_no}


def parse_pdf(content: bytes) -> inv_parser.Invoice:
    if not content.startswith(b"%PDF"):
        raise HTTPException(400, "Файлът не е PDF")
    if len(content) > MAX_PDF_BYTES:
        raise HTTPException(413, "Файлът е над 10 MB")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "invoice.pdf"
        path.write_bytes(content)
        try:
            text = inv_parser.pdf_text(path)
        except FileNotFoundError:
            raise HTTPException(500, "Липсва pdftotext (poppler-utils) на сървъра")
        except subprocess.CalledProcessError:
            raise HTTPException(400, "PDF файлът не може да се прочете")
    try:
        return inv_parser.parse_text(text)
    except ValueError:
        raise HTTPException(400, "Не разпознах фактура на Електрохолд в този PDF; въведи данните ръчно")


def preview(inv: inv_parser.Invoice) -> dict:
    day = sum(r["kwh"] for r in inv.readings if r["zone"] == "day")
    night = sum(r["kwh"] for r in inv.readings if r["zone"] == "night")
    lines_sum = sum(ln["amount"] for ln in inv.lines)
    checks = [
        {"label": "Показанията (ново − старо) дават kWh по зони",
         "ok": all(abs(r["new"] - r["old"] - r["kwh"]) < 0.5 for r in inv.readings)},
        {"label": "Сборът на редовете е равен на данъчната основа",
         "ok": inv.tax_base is not None and abs(lines_sum - inv.tax_base) < 0.05},
        {"label": "Основа + ДДС = сума за плащане",
         "ok": None not in (inv.tax_base, inv.vat, inv.total) and abs(inv.tax_base + inv.vat - inv.total) < 0.05},
    ]
    return {"invoice_no": inv.number, "issued": inv.issued, "currency": inv.currency,
            "period_from": inv.periods[0][0], "period_to": inv.periods[-1][1], "sub_periods": len(inv.periods),
            "day_kwh": day, "night_kwh": night, "meter_no": inv.meter,
            "total_ccy": r2(inv.eur(inv.total)), "tax_base_ccy": r2(inv.eur(inv.tax_base)), "vat_ccy": r2(inv.eur(inv.vat)),
            "energy_ccy": r2(inv.eur(sum(ln["amount"] for ln in inv.lines if ln["kind"] == "energy"))),
            "network_ccy": r2(inv.eur(sum(ln["amount"] for ln in inv.lines if ln["kind"] in inv_parser.NETWORK.values()))),
            "lines": len(inv.lines), "checks": checks}


@router.post("/api/settings/invoices/pdf")
async def upload_invoice_pdf(request: Request, save: bool = False, filename: str = "фактура.pdf"):
    """Body: the PDF itself. save=false returns what was read; save=true stores it and rebuilds the tariffs from the invoices."""
    inv = parse_pdf(await request.body())
    out = preview(inv)
    conn = data.connect()
    try:
        exists = conn.execute("SELECT 1 FROM grid_invoice WHERE invoice_no = ?", (inv.number,)).fetchone() is not None
        o = overlap(conn, out["period_from"], out["period_to"], inv.number)
    finally:
        conn.close()
    out["exists"], out["overlaps"] = exists, o
    if not save:
        return out
    if o:
        raise overlap_error(o)

    def fn(c):
        inv_parser.store(c, inv, Path(filename).name[:200])
        return inv_parser.derive_tariffs(c)
    out["tariff_periods"] = settings.write(fn)
    out["saved"] = True
    return out
