"""First-run setup and the general settings of the installation.

- GET/PUT /api/app-settings: name, currency, time zone, language, night tariff hours, location,
  the AI model's provider/model/address, and whether the setup is finished;
- PUT /api/secrets/llm: the AI provider's API key (write-only, never read back);
- POST /api/setup/deye: tests a Deye Cloud access (sign-in and station list, read-only) and,
  when it works, saves it into data/secrets.json, where the collector picks it up.

Values pinned in the environment (.env) win and cannot be changed here. Writes go through the
same guard as every other change (same origin, WRITE_PASSWORD when set).
"""
from __future__ import annotations

import json
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from collector import appconf, secrets, tariffs
from collector.deye_client import DeyeApiError, DeyeClient, DeyeConfig

from . import data, llm, settings

router = APIRouter()

DEYE_REGIONS = {"eu1": "https://eu1-developer.deyecloud.com/v1.0", "us1": "https://us1-developer.deyecloud.com/v1.0"}
SETTING_KEYS = ("site_name", "currency", "timezone", "language", "tariff_night_windows", "analysis_language",
                "llm_provider", "llm_model", "llm_base_url", "setup_done")
HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def _mask(email: str | None) -> str | None:
    if not email or "@" not in email:
        return None
    user, dom = email.split("@", 1)
    return f"{user[:2]}…@{dom}"


@router.get("/api/app-settings")
def get_app_settings():
    conn = data.connect()
    try:
        st = appconf.all_settings(conn)
        cfg = llm.config(conn)
        site = conn.execute("SELECT key, value FROM param WHERE key IN ('site_lat', 'site_lon') ORDER BY valid_from").fetchall()
        tz_now = appconf.timezone(conn).key
    finally:
        conn.close()
    try:
        windows = json.loads(st.get("tariff_night_windows") or "[]")
    except ValueError:
        windows = []
    loc = {r[0]: r[1] for r in site}
    base = secrets.get("DEYE_BASE_URL") or DEYE_REGIONS["eu1"]
    return {
        "settings": {k: st.get(k) for k in SETTING_KEYS if k != "tariff_night_windows"} | {"tariff_night_windows": windows},
        "timezone_effective": tz_now,
        "location": {"lat": loc.get("site_lat"), "lon": loc.get("site_lon")},
        "tariff_presets": tariffs.PRESETS,
        "currencies": appconf.CURRENCY_SYMBOLS,
        "llm": {"provider": cfg["provider"], "model": cfg["model"], "base_url": cfg["base_url"], "has_key": cfg["has_key"],
                "cli_command": cfg["cli_command"], "pinned": cfg["pinned"],
                "providers": {k: {kk: vv for kk, vv in v.items()} for k, v in llm.PROVIDERS.items()},
                "ready": llm.ready(cfg)[0]},
        "deye": {"ready": secrets.deye_ready(), "email": _mask(secrets.get("DEYE_EMAIL")),
                 "region": next((k for k, v in DEYE_REGIONS.items() if v == base), "custom"), "base_url": base,
                 "pinned": any(secrets.from_env(k) for k in secrets.DEYE_KEYS), "regions": DEYE_REGIONS},
    }


class SettingsIn(BaseModel):
    site_name: str | None = Field(None, max_length=80)
    currency: str | None = Field(None, max_length=3)
    timezone: str | None = Field(None, max_length=64)
    language: str | None = Field(None, max_length=8)
    analysis_language: str | None = Field(None, max_length=8)
    tariff_night_windows: list[dict] | None = None
    llm_provider: str | None = Field(None, max_length=20)
    llm_model: str | None = Field(None, max_length=120)
    llm_base_url: str | None = Field(None, max_length=300)
    setup_done: bool | None = None
    lat: float | None = Field(None, ge=-90, le=90)
    lon: float | None = Field(None, ge=-180, le=180)


def _windows(ws: list[dict]) -> str:
    out = []
    for w in ws:
        months = w.get("months") or [1, 12]
        if (not isinstance(months, list) or len(months) != 2
                or not all(isinstance(x, int) and 1 <= x <= 12 for x in months)):
            raise HTTPException(400, "Месеците са две числа от 1 до 12")
        start, end = str(w.get("start", "")), str(w.get("end", ""))
        if not HHMM.match(start) or not HHMM.match(end) or start == end:
            raise HTTPException(400, "Часовете са във формат ЧЧ:ММ и различни")
        out.append({"months": months, "start": start, "end": end})
    return json.dumps(out)


@router.put("/api/app-settings")
def put_app_settings(x: SettingsIn):
    given = x.model_dump(exclude_unset=True)
    vals: dict[str, str | None] = {}
    for k in ("site_name", "llm_model"):
        if k in given:
            vals[k] = (given[k] or "").strip() or None
    if "currency" in given:
        c = (given["currency"] or "").upper()
        if not re.fullmatch(r"[A-Z]{3}", c):
            raise HTTPException(400, "Валутата е трибуквен код по ISO 4217, напр. EUR")
        vals["currency"] = c
    if "timezone" in given:
        tz = (given["timezone"] or "").strip() or None
        if tz:
            try:
                ZoneInfo(tz)
            except (ZoneInfoNotFoundError, ValueError):
                raise HTTPException(400, f"Непозната часова зона: {tz}")
        vals["timezone"] = tz
    for k in ("language", "analysis_language"):
        if k in given:
            v = (given[k] or "").strip().lower() or None
            if v and not re.fullmatch(r"[a-z]{2}", v):
                raise HTTPException(400, "Езикът е двубуквен код, напр. en")
            vals[k] = v
    if "tariff_night_windows" in given:
        vals["tariff_night_windows"] = _windows(given["tariff_night_windows"] or [])
    if "setup_done" in given:
        vals["setup_done"] = "1" if given["setup_done"] else None
    clear_key = False
    conn = data.connect()
    try:
        before = llm.config(conn)
    finally:
        conn.close()
    if "llm_provider" in given:
        p = given["llm_provider"] or None
        if p and p not in llm.PROVIDERS:
            raise HTTPException(400, "Непознат доставчик")
        vals["llm_provider"] = p
        clear_key |= p != before["provider"]
    if "llm_base_url" in given:
        u = (given["llm_base_url"] or "").strip().rstrip("/") or None
        if u and not re.match(r"^https?://", u):
            raise HTTPException(400, "Адресът започва с http:// или https://")
        vals["llm_base_url"] = u
        clear_key |= (u or "") != (before["base_url"] or "") and u is not None
    for k in ("llm_provider", "llm_model", "llm_base_url"):
        if k in vals and before["pinned"].get(k.removeprefix("llm_")):
            raise HTTPException(409, f"{k.upper()} е зададено в .env и се сменя там")

    def fn(c):
        for k, v in vals.items():
            c.execute("INSERT INTO app_setting (key, value, updated_at) VALUES (?, ?, ?) "
                      "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
                      (k, v, settings.now()))
        for key, v in (("site_lat", given.get("lat")), ("site_lon", given.get("lon"))):
            if key[5:] in given and v is not None:
                c.execute("INSERT INTO param (key, valid_from, value, unit, note) VALUES (?, '2000-01-01', ?, '°', 'от настройките') "
                          "ON CONFLICT (key, valid_from) DO UPDATE SET value = excluded.value", (key, v))
    settings.write(fn)
    if "timezone" in vals:
        conn = data.connect()
        try:
            data.TZ.set(appconf.timezone(conn))
        finally:
            conn.close()
    # a key must never follow the request to a different provider or address than it was entered for
    if clear_key and not secrets.from_env("LLM_API_KEY"):
        secrets.update({"LLM_API_KEY": None})
    return get_app_settings()


class KeyIn(BaseModel):
    api_key: str | None = Field(None, max_length=500)


@router.put("/api/secrets/llm")
def put_llm_key(x: KeyIn):
    if secrets.from_env("LLM_API_KEY"):
        raise HTTPException(409, "LLM_API_KEY е зададен в .env и се сменя там")
    secrets.update({"LLM_API_KEY": (x.api_key or "").strip() or None})
    return {"has_key": bool(secrets.get("LLM_API_KEY"))}


class DeyeIn(BaseModel):
    email: str = Field(max_length=200)
    password: str = Field(max_length=200)
    app_id: str = Field(max_length=200)
    app_secret: str = Field(max_length=300)
    region: str = Field("eu1", max_length=10)
    base_url: str | None = Field(None, max_length=300)


@router.post("/api/setup/deye")
def setup_deye(x: DeyeIn):
    """Sign in to Deye Cloud and list the stations (read-only); save the access only when that works."""
    if any(secrets.from_env(k) for k in secrets.DEYE_KEYS):
        raise HTTPException(409, "Достъпът до Deye е зададен в .env и се сменя там")
    base = (x.base_url or "").strip().rstrip("/") if x.region == "custom" else DEYE_REGIONS.get(x.region)
    if not base or not re.match(r"^https://[a-z0-9.-]+\.deyecloud\.com(/v1\.0)?$", base):
        raise HTTPException(400, "Адресът трябва да е на deyecloud.com")
    if not base.endswith("/v1.0"):
        base += "/v1.0"
    cfg = DeyeConfig(base_url=base, app_id=x.app_id.strip(), app_secret=x.app_secret.strip(), email=x.email.strip(),
                     password=x.password)
    try:
        client = DeyeClient(cfg, timeout=20, min_interval=1.0)
        stations = client.station_list().get("stationList") or []
    except (DeyeApiError, OSError, ValueError, KeyError) as exc:
        raise HTTPException(400, f"Deye не прие данните: {exc}")
    if not stations:
        raise HTTPException(400, "Входът работи, но в този акаунт няма станции")
    secrets.update({"DEYE_EMAIL": cfg.email, "DEYE_PASSWORD": cfg.password, "DEYE_APP_ID": cfg.app_id,
                    "DEYE_APP_SECRET": cfg.app_secret, "DEYE_BASE_URL": base})
    return {"ok": True, "stations": [{"id": s.get("id"), "name": s.get("name"), "kwp": s.get("installedCapacity")}
                                     for s in stations]}
