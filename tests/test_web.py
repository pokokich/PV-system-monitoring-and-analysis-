import sqlite3
import tempfile
import unittest
from pathlib import Path
from zoneinfo import ZoneInfo

from collector import energy, store
from collector.db import connect

from .fixtures import seed_demo

try:
    from fastapi.testclient import TestClient
except ImportError:  # dashboard dependencies not installed (collector-only environment)
    TestClient = None

TZ = ZoneInfo("Europe/Sofia")
DAY0 = 1792357200  # 2026-10-19 00:00 Europe/Sofia
DAY_PRICE, NIGHT_PRICE = 0.12854 * 1.2, 0.0766 * 1.2  # seed tariff from 01.07.2026, with VAT


def frame(ts, gen=0.0, load=0.0, wire=0.0, batt=0.0, soc=50.0):
    return {"timeStamp": ts, "generationPower": gen, "consumptionPower": load, "wirePower": wire,
            "purchasePower": max(wire, 0), "batteryPower": batt, "batterySOC": soc}


def synthetic_day(hour: int) -> dict:
    """00-07 night: 1 kW load from the battery (02-03 from the grid); 10-14: 10 kW PV, 2 kW load, 8 kW charge."""
    if 10 <= hour < 14:
        return {"gen": 10000, "load": 2000, "batt": -8000, "soc": 100 if hour == 13 else 80}
    if hour == 2:
        return {"load": 1000, "wire": 1000, "soc": 40}
    return {"load": 1000, "batt": 1000, "soc": 60}


@unittest.skipIf(TestClient is None, "fastapi is not installed")
class DashboardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        path = Path(cls.tmp.name) / "pv.sqlite"
        db = connect(path)
        seed_demo(db)
        db.execute("INSERT INTO station (id, name) VALUES (1, 'test')")
        items = []
        for i in range(288):
            ts = DAY0 + i * 300
            p = synthetic_day(i // 12)
            items.append(frame(ts, p.get("gen", 0), p["load"], p.get("wire", 0), p.get("batt", 0), p["soc"]))
        items.append(frame(DAY0 + 86400, load=1000, batt=1000, soc=60))
        store.upsert_station_frames(db, 1, items, "power")
        energy.rebuild(db, 1, DAY0, DAY0 + 86400, TZ)
        db.commit()
        db.close()

        from web import data
        data.DB_PATH = str(path)
        data._model = None
        cls.data = data
        from web.app import app
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls):
        cls.data._model = None
        cls.tmp.cleanup()

    def kpi(self, **params):
        r = self.client.get("/api/kpi", params={"from": "2026-10-19", "to": "2026-10-19", **params})
        self.assertEqual(r.status_code, 200)
        return r.json()

    def test_energy_and_ratios(self):
        k = self.kpi()
        v = {key: x["value"] for key, x in k["kpis"].items()}
        self.assertAlmostEqual(v["E01"], 40.0, places=0)
        self.assertAlmostEqual(v["E03"], 1.0, places=0)
        self.assertAlmostEqual(v["E04n"], 1.0, places=0)  # 02:00 is night in October
        self.assertAlmostEqual(v["U01"], 100.0, places=1)
        self.assertAlmostEqual(v["U02"], (k["totals"]["load"] - k["totals"]["imp"]) / k["totals"]["load"] * 100, places=1)
        self.assertAlmostEqual(v["E07"], 8.0, places=0)  # PV straight to the 2 kW load for 4 h
        self.assertEqual(v["U05"], 13.0)  # SOC 100 from 13:00
        self.assertAlmostEqual(v["B02"], k["totals"]["dis"] / 40.96, places=2)

    def test_money_by_tariff_zone(self):
        t = self.kpi()["totals"]
        expected = t["load_day"] * DAY_PRICE + t["load_night"] * NIGHT_PRICE
        self.assertAlmostEqual(t["cost_without"], expected, places=2)
        self.assertAlmostEqual(t["cost_with"], t["imp_night"] * NIGHT_PRICE + t["imp_day"] * DAY_PRICE, places=2)
        self.assertAlmostEqual(t["benefit"], t["cost_without"] - t["cost_with"], places=2)

    def test_meter_difference_is_added_to_import_and_load(self):
        t0 = self.kpi()["totals"]
        db = sqlite3.connect(self.data.DB_PATH)
        db.execute("INSERT INTO grid_invoice (invoice_no, issued, period_from, period_to, day_kwh, night_kwh) "
                   "VALUES ('T1', '2026-10-20', '2026-10-19', '2026-10-19', 0, 11)")
        db.execute("INSERT INTO grid_invoice_reading VALUES ('T1', '2026-10-19', '2026-10-19', 'night', 0, 11, 11)")
        db.commit()
        db.close()
        self.data._model = None
        try:
            t = self.kpi()["totals"]
            self.assertAlmostEqual(t["meter_imp_night"], 11.0, places=2)
            self.assertAlmostEqual(t["cost_with"], 11.0 * NIGHT_PRICE + t["imp_day"] * DAY_PRICE, places=2)
            self.assertAlmostEqual(t["benefit"], t0["benefit"], places=2)  # saving unchanged
        finally:
            db = sqlite3.connect(self.data.DB_PATH)
            db.execute("DELETE FROM grid_invoice_reading")
            db.execute("DELETE FROM grid_invoice")
            db.commit()
            db.close()
            self.data._model = None

    def test_pages_and_series(self):
        for path in ("/", "/production", "/battery", "/grid", "/economics", "/quality", "/health", "/goals", "/analysis"):
            self.assertEqual(self.client.get(path).status_code, 200, path)
        s = self.client.get("/api/series", params={"from": "2026-10-19", "to": "2026-10-19"}).json()
        self.assertEqual(s["group"], "day")
        self.assertEqual(len(s["rows"]), 1)
        p = self.client.get("/api/power", params={"day": "2026-10-19"}).json()
        self.assertEqual(len(p["rows"]), 288)
        self.assertEqual(p["rows"][0][0], (DAY0 + 3 * 3600) * 1000)  # Sofia wall time, UTC+3 in October
        self.assertEqual(self.client.get("/api/payback").status_code, 200)
        g = self.client.get("/api/goals").json()
        self.assertEqual({x["kpi_id"] for x in g["goals"]}, {"F04", "U02", "U03", "P04", "B04"})
        self.assertEqual([x["name"] for x in g["scenarios"]], ["conservative", "base", "optimistic"])

    def test_database_is_opened_read_only(self):
        conn = self.data.connect()
        try:
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute("INSERT INTO param (key, valid_from, value) VALUES ('x', '2026-01-01', 1)")
        finally:
            conn.close()

    def test_goal_status_and_editing(self):
        g = self.client.get("/api/goals").json()
        u02 = next(x for x in g["goals"] if x["kpi_id"] == "U02")
        k = self.kpi()
        self.assertAlmostEqual(u02["current"]["actual"], k["kpis"]["U02"]["value"], places=1)
        self.assertAlmostEqual(u02["current"]["forecast"], u02["current"]["actual"], places=1)  # same profile every day
        self.assertEqual(u02["current"]["status"], "on_track" if u02["current"]["actual"] >= 75 else "behind")
        r = self.client.post("/api/goals", json={"kpi_id": "U02", "period_type": "month", "period_start": "2026-10",
                                                 "target": 1, "warn_level": 0.5})
        self.assertEqual(r.status_code, 201)
        gid = r.json()["id"]
        try:
            x = next(x for x in self.client.get("/api/goals").json()["goals"] if x["id"] == gid)
            self.assertEqual((x["current"]["start"], x["current"]["end"]), ("2026-10-19", "2026-10-31"))
            self.assertEqual(x["current"]["status"], "on_track")  # forecast for the month end reaches 1%
            r = self.client.put(f"/api/goals/{gid}", json={"kpi_id": "U02", "period_type": "month", "period_start": "2026-10",
                                                           "target": 101, "warn_level": 100})
            self.assertEqual(r.status_code, 200)
            x = next(x for x in self.client.get("/api/goals").json()["goals"] if x["id"] == gid)
            self.assertEqual(x["current"]["status"], "behind")
            self.assertEqual(self.client.post("/api/goals", json={"kpi_id": "XX", "target": 1}).status_code, 400)
        finally:
            self.assertEqual(self.client.delete(f"/api/goals/{gid}").status_code, 200)
        self.assertEqual(self.client.delete(f"/api/goals/{gid}").status_code, 404)

    def test_scenarios_plan_and_params(self):
        r = self.client.put("/api/scenarios/base", json={"price_escalation_pct": 4, "degradation_pct": 0.5})
        self.assertEqual(r.status_code, 200)
        sc = {x["name"]: x for x in self.client.get("/api/goals").json()["scenarios"]}
        self.assertEqual(sc["base"]["price_escalation_pct"], 4)
        self.assertEqual(self.client.put("/api/plan", json={"months": {"10": 31.0}}).status_code, 200)
        k = self.kpi()
        self.assertAlmostEqual(k["kpis"]["F10"]["value"], k["totals"]["benefit"] / 1.0 * 100, places=0)
        self.assertEqual(self.client.put("/api/params/vat_rate", json={"value": 0}).status_code, 403)
        self.assertEqual(self.client.put("/api/params/goal_curtail_value_ccy_kwh", json={"value": 0.1}).status_code, 200)

    def test_health_status_and_forecast(self):
        h = self.client.get("/api/health").json()
        self.assertEqual(set(h["status"]["components"]), {"battery", "inverter", "pv", "data"})
        self.assertIn(h["status"]["overall"], {"good", "watch", "bad", "no_data"})
        oct26 = next(r for r in h["months"] if r["month"] == "2026-10")
        # synthetic day: one hour at 100%, the rest between 40% and 80%
        self.assertAlmostEqual(oct26["soc_full"] + oct26["soc_high"] + oct26["soc_mid"] + oct26["soc_min"], 100, delta=0.5)
        self.assertGreater(oct26["soc_full"], 0)
        f = h["battery_forecast"]
        self.assertLessEqual(f["actual"]["soh_now"], 100)
        self.assertLessEqual(f["ideal"]["fade_per_year"], f["actual"]["fade_per_year"] + 1e-9)
        self.assertEqual({p["key"] for p in h["param_rows"]} >= {"health_batt_rated_cycles", "health_batt_eol_pct"}, True)
        short = self.client.get("/api/health", params={"detail": "false"}).json()
        self.assertEqual(set(short), {"status"})
        self.assertGreater(short["status"]["cycles"]["total"], 0)
        self.assertEqual(self.client.put("/api/params/health_batt_rated_cycles", json={"value": 5000}).status_code, 200)
        self.assertEqual(self.client.get("/api/health").json()["params"]["health_batt_rated_cycles"], 5000)
        self.assertEqual(self.client.put("/api/params/site_lat", json={"value": 0}).status_code, 403)
        # a reading from the Deye app is kept with today's date (one per day)
        self.assertEqual(self.client.put("/api/params/health_batt_app_soh_pct", json={"value": 99}).status_code, 200)
        f = self.client.get("/api/health").json()["battery_forecast"]
        self.assertEqual(len({d for d, _ in f["app_readings"]}), len(f["app_readings"]))
        self.assertEqual(f["app_readings"][-1][1], 99.0)
        self.assertLessEqual(f["soh_now"], 100)
        self.assertGreaterEqual(f["soh_now"], f["cells_now"])

    def test_analysis_without_model_is_empty(self):
        a = self.client.get("/api/analysis").json()
        self.assertEqual(a["as_of"], self.client.get("/api/meta").json()["last_day"])
        self.assertFalse(a["llm"]["ready"])
        r = self.client.post("/api/analysis/run", json={})
        self.assertEqual(r.status_code, 409)
        self.assertIn("модел", r.json()["detail"])

    def test_analysis_context_has_no_secrets(self):
        import json as _json
        from web import analysis, app as webapp
        conn = self.data.connect()
        try:
            ctx = analysis.context(self.data.model(), conn, webapp.scenarios(), webapp.read_rows("SELECT * FROM goal"), None)
        finally:
            conn.close()
        text = _json.dumps(ctx, ensure_ascii=False)
        for k in ("system", "periods", "monthly", "goals", "health", "payback_scenarios"):
            self.assertIn(k, ctx)
        for secret in ("DEYE_", "password", "appSecret", "invoice_no"):
            self.assertNotIn(secret, text)

    def test_analysis_with_a_model(self):
        import time as _time
        from web import llm
        answer = ('Here it is: {"overall": {"level": "good", "headline": ["Добре"], "pillars": [{"label": "A", "value": "1", '
                  '"sub": "", "level": "weird"}]}, "sections": [{"key": "pv x", "title": "Панели", "level": "watch", "summary": "s", '
                  '"findings": [{"level": "good", "title": "t", "text": "x", "evidence": [{"label": "l", "value": "5", "unit": "%"}]}]}], '
                  '"levers": [{"title": "b", "money_year": 10}, {"title": "a", "money_year": 50}]}')
        old_complete, old_ready = llm.complete, llm.ready
        llm.complete = lambda cfg, system, prompt: (answer, {"input_tokens": 1})
        llm.ready = lambda cfg: (True, None)
        try:
            r = self.client.post("/api/analysis/run", json={"question": "Да купя ли още батерия?"})
            self.assertEqual(r.status_code, 202, r.text)
            for _ in range(50):
                run = self.client.get("/api/analysis").json()["run"]
                if run["status"] != "running":
                    break
                _time.sleep(0.1)
        finally:
            llm.complete, llm.ready = old_complete, old_ready
        self.assertEqual(run["status"], "done", run.get("error"))
        res = run["result"]
        self.assertEqual(res["overall"]["pillars"][0]["level"], "no_data")   # unknown level cleaned
        self.assertEqual(res["sections"][0]["key"], "pvx")
        self.assertEqual([x["title"] for x in res["levers"]], ["a", "b"])     # biggest first
        self.assertEqual(run["question"], "Да купя ли още батерия?")

    def test_app_settings(self):
        s = self.client.get("/api/app-settings").json()
        self.assertEqual(s["settings"]["currency"], "EUR")
        self.assertIn(self.client.put("/api/app-settings", json={"currency": "eu1"}).status_code, (400, 422))
        self.assertEqual(self.client.put("/api/app-settings", json={"timezone": "Mars/Base"}).status_code, 400)
        bad = {"tariff_night_windows": [{"months": [1, 12], "start": "25:00", "end": "06:00"}]}
        self.assertEqual(self.client.put("/api/app-settings", json=bad).status_code, 400)
        r = self.client.put("/api/app-settings", json={"currency": "pln", "site_name": "  Дом  "})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["settings"]["currency"], "PLN")
        self.assertEqual(r.json()["settings"]["site_name"], "Дом")
        self.assertIn("zł", self.client.get("/economics").text)
        self.client.put("/api/app-settings", json={"currency": "EUR", "site_name": None})

    def test_llm_key_is_write_only_and_follows_the_provider(self):
        import os
        from collector import secrets
        os.environ["SECRETS_PATH"] = str(Path(self.tmp.name) / "secrets.json")
        try:
            self.client.put("/api/app-settings", json={"llm_provider": "anthropic"})
            self.assertEqual(self.client.put("/api/secrets/llm", json={"api_key": "sk-test"}).json(), {"has_key": True})
            s = self.client.get("/api/app-settings").text
            self.assertNotIn("sk-test", s)
            if os.name == "posix":  # Windows has no mode bits; %APPDATA% is per user
                self.assertEqual(oct(Path(os.environ["SECRETS_PATH"]).stat().st_mode & 0o777), "0o600")
            # a new address must not receive the old key
            self.client.put("/api/app-settings", json={"llm_base_url": "https://example.org"})
            self.assertIsNone(secrets.get("LLM_API_KEY"))
            self.assertEqual(self.client.put("/api/app-settings", json={"llm_provider": "nope"}).status_code, 400)
        finally:
            self.client.put("/api/app-settings", json={"llm_provider": None, "llm_base_url": None, "llm_model": None})
            os.environ.pop("SECRETS_PATH")

    def test_setup_page(self):
        r = self.client.get("/setup")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Deye Cloud", r.text)
        bad = {"email": "a@b.c", "password": "x", "app_id": "1", "app_secret": "2", "region": "custom",
               "base_url": "https://evil.example.com/v1.0"}
        self.assertEqual(self.client.post("/api/setup/deye", json=bad).status_code, 400)

    def test_write_connection_only_touches_goal_tables(self):
        from web import settings
        conn = settings.connect_rw()
        try:
            for sql in ("INSERT INTO energy_daily (station_id, day) VALUES (1, '2030-01-01')",
                        "DELETE FROM station_frame", "UPDATE device_settings SET settings_json = '{}'",
                        "DELETE FROM measurement", "DROP TABLE goal",
                        "CREATE TABLE x (a)"):
                with self.assertRaises(sqlite3.DatabaseError, msg=sql):
                    conn.execute(sql)
            conn.execute("UPDATE goal SET note = note WHERE id = -1")
        finally:
            conn.close()

    def test_settings_tables_and_params(self):
        self.assertEqual(self.client.get("/settings").status_code, 200)
        s = self.client.get("/api/settings").json()
        self.assertEqual(set(s["tables"]), {"pv_array", "battery_config", "tariff", "feed_in_price", "capex_item", "opex_item"})
        # a new open-ended battery closes the current one; deleting it opens the old one again
        r = self.client.post("/api/settings/table/battery_config",
                             json={"modules": 9, "kwh_per_module": 5.12, "valid_from": "2030-01-01", "status": "assumed"})
        self.assertEqual(r.status_code, 201)
        rows = self.client.get("/api/settings").json()["tables"]["battery_config"]["rows"]
        self.assertEqual([x["valid_to"] for x in rows], ["2026-07-09", "2030-01-01", None])
        self.assertEqual(self.client.delete(f"/api/settings/table/battery_config/{r.json()['id']}").status_code, 200)
        rows = self.client.get("/api/settings").json()["tables"]["battery_config"]["rows"]
        self.assertEqual(rows[-1]["valid_to"], None)
        self.assertEqual(self.client.post("/api/settings/table/battery_config",
                                          json={"modules": 0, "kwh_per_module": 5, "valid_from": "2030-01-01",
                                                "status": "assumed"}).status_code, 400)
        self.assertEqual(self.client.delete("/api/settings/table/pv_array/1").status_code, 400)
        self.assertEqual(self.client.post("/api/settings/table/station", json={}).status_code, 404)
        # a maintenance cost lowers the net benefit of its day
        before = self.kpi()["totals"]["benefit"]
        r = self.client.post("/api/settings/table/opex_item",
                             json={"date": "2026-10-19", "description": "почистване", "amount_ccy": 5})
        self.assertEqual(r.status_code, 201)
        self.assertAlmostEqual(self.kpi()["totals"]["benefit"], before - 5, places=2)
        self.assertEqual(self.client.delete(f"/api/settings/table/opex_item/{r.json()['id']}").status_code, 200)
        # params: correct the value, or add one valid from a date
        self.assertEqual(self.client.put("/api/settings/param/vat_rate", json={"value": 0.9}).status_code, 400)
        self.assertEqual(self.client.put("/api/settings/param/vat_rate",
                                         json={"value": 0.25, "mode": "new", "valid_from": "2030-01-01"}).status_code, 200)
        vat = next(p for p in self.client.get("/api/settings").json()["params"] if p["key"] == "vat_rate")
        self.assertEqual([h["value"] for h in vat["history"]], [0.2, 0.25])
        self.assertEqual(self.client.delete("/api/settings/param/vat_rate/2030-01-01").status_code, 200)
        self.assertEqual(self.client.delete("/api/settings/param/vat_rate/2007-01-01").status_code, 400)

    def test_battery_minimum_follows_dated_setting(self):
        """B07 counts SOC at or below the minimum valid on each day and recomputes after a settings edit."""
        rows = self.client.get("/api/settings").json()["tables"]["battery_config"]["rows"]
        row = next(x for x in rows if x["valid_to"] is None)
        fields = ("modules", "kwh_per_module", "soc_min_normal", "soc_min_emergency", "valid_from", "valid_to", "status", "note")

        def set_min(v):
            body = {k: row[k] for k in fields} | {"soc_min_normal": v}
            self.assertEqual(self.client.put(f"/api/settings/table/battery_config/{row['id']}", json=body).status_code, 200)

        try:
            k = self.kpi()
            self.assertEqual(k["totals"]["soc_min_settings"], [row["soc_min_normal"]])
            self.assertEqual(k["kpis"]["B07"]["value"], 0)  # SOC never below 40 on the synthetic day
            set_min(40)  # 02:00-03:00 sits at 40%: one hour at the minimum, also when SOC equals it
            k = self.kpi()
            self.assertEqual(k["totals"]["soc_min_settings"], [40])
            self.assertAlmostEqual(k["kpis"]["B07"]["value"], 1.0, places=1)
            # B01: the lowest SOC is an observation with its day, not the setting
            self.assertEqual(k["kpis"]["B01min"]["value"], 40)
            self.assertEqual(k["totals"]["soc_min_day"], "2026-10-19")
            self.assertEqual(k["totals"]["soc_min_setting_then"], 40)
        finally:
            set_min(row["soc_min_normal"])
        self.assertEqual(self.kpi()["kpis"]["B07"]["value"], 0)

    def test_per_day_hours_divide_by_days_with_soc(self):
        """A day without SOC frames (before commissioning) does not dilute the hours per day."""
        from web import kpi
        day = dict(self.data.model().days["2026-10-19"])
        empty = dict(day, day="2026-10-18", h_full=0.0, h_low=0.0, h_soc=0.0, soc_min=None, soc_max=None, soc_min_ts=None)
        one = kpi.aggregate([day])
        two = kpi.aggregate([empty, day])
        self.assertAlmostEqual(two["h_full"] / two["soc_days"], one["h_full"] / one["soc_days"], places=6)
        self.assertAlmostEqual(one["soc_days"], 1.0, places=2)
        self.assertEqual(two["soc_min_day"], "2026-10-19")

    def test_unfinished_today_left_out_of_plan_and_capex_paid_so_far(self):
        from web import kpi
        day = dict(self.data.model().days["2026-10-19"], plan=1.0, benefit=2.0, day_frac=1.0)
        today = dict(day, day="2026-10-20", plan=1.0, benefit=0.1, day_frac=0.3)
        self.assertAlmostEqual(kpi.aggregate([day, today])["f10"], 200.0)   # half a day is not set against a full-day plan
        self.assertAlmostEqual(kpi.aggregate([today])["f10"], 10.0)         # only today: compared as it is
        self.assertAlmostEqual(kpi.aggregate([day, today])["day_frac"], 1.3)
        m = self.data.model()
        pb = kpi.payback(m, "2026-10-19")
        self.assertAlmostEqual(pb["roi"], pb["cumulative"] / pb["capex"] * 100)

    def test_manual_invoice_counts_as_meter_reading(self):
        k0 = self.kpi()
        body = {"period_from": "2026-10-19", "period_to": "2026-10-19", "day_kwh": 10, "night_kwh": 2, "total_ccy": 3.5}
        r = self.client.post("/api/settings/invoices", json=body)
        self.assertEqual(r.status_code, 201)
        no = r.json()["invoice_no"]
        try:
            inv = next(x for x in self.client.get("/api/settings").json()["invoices"] if x["invoice_no"] == no)
            self.assertTrue(inv["manual"])
            self.assertAlmostEqual(inv["vat_ccy"], 3.5 - round(3.5 / 1.2, 2), places=2)
            k = self.kpi()
            self.assertAlmostEqual(k["totals"]["cost_with"], 10 * DAY_PRICE + 2 * NIGHT_PRICE, places=2)
            self.assertGreater(k["totals"]["cost_without"], k0["totals"]["cost_without"])  # meter gap is load Deye missed
            self.assertEqual(self.client.post("/api/settings/invoices", json=dict(body, day_kwh=1)).status_code, 409)
            r = self.client.put(f"/api/settings/invoices/{no}", json=dict(body, invoice_no=no, day_kwh=11))
            self.assertEqual(r.status_code, 200)
            self.assertAlmostEqual(self.kpi()["totals"]["cost_with"], 11 * DAY_PRICE + 2 * NIGHT_PRICE, places=2)
            self.assertEqual(self.client.post("/api/settings/invoices",
                                              json=dict(body, period_to="2026-10-18")).status_code, 400)
        finally:
            self.assertEqual(self.client.delete(f"/api/settings/invoices/{no}").status_code, 200)
        self.assertAlmostEqual(self.kpi()["totals"]["cost_with"], k0["totals"]["cost_with"], places=3)

    def test_invoice_pdf_rejects_other_files(self):
        r = self.client.post("/api/settings/invoices/pdf", content=b"hello", headers={"Content-Type": "application/pdf"})
        self.assertEqual(r.status_code, 400)

    def test_writes_only_from_own_pages(self):
        body = {"value": 1.0}
        foreign = self.client.put("/api/params/goal_test", json=body, headers={"Origin": "http://evil.example"})
        self.assertEqual(foreign.status_code, 403)
        plain = self.client.post("/api/settings/invoices", content=b"{}", headers={"Content-Type": "text/plain"})
        self.assertEqual(plain.status_code, 415)
        own = self.client.get("/api/status", headers={"Origin": "http://testserver"})
        self.assertEqual(own.status_code, 200)
        self.assertIn(own.json()["level"], ("ok", "warn", "bad"))
        self.assertEqual(self.client.get("/healthz").json()["ok"], True)

    def test_write_password(self):
        import os
        from unittest import mock
        with mock.patch.dict(os.environ, {"WRITE_PASSWORD": "тайна"}):
            r = self.client.post("/api/settings/invoices/pdf", content=b"hello", headers={"Content-Type": "application/pdf"})
            self.assertEqual(r.status_code, 401)
            self.assertEqual(r.json()["code"], "password_required")
            self.assertEqual(self.client.post("/api/login", json={"password": "грешна"}).status_code, 401)
            self.assertEqual(self.client.post("/api/login", json={"password": "тайна"}).status_code, 200)
            r = self.client.post("/api/settings/invoices/pdf", content=b"hello", headers={"Content-Type": "application/pdf"})
            self.assertEqual(r.status_code, 400)   # past the password: not a PDF
            self.client.post("/api/logout")
            self.client.cookies.clear()
            self.assertTrue(self.client.get("/api/login").json()["password_set"])

    def test_dashboard_calls_deye_only_to_test_the_setup(self):
        root = Path(__file__).resolve().parents[1] / "web"
        for py in root.rglob("*.py"):
            text = py.read_text(encoding="utf-8")
            if py.name == "setup.py":   # sign-in and the station list, through the read-only client
                calls = {c for c in ("station_list", "station_latest", "device_latest", "device_history", "device_config")
                         if f".{c}(" in text}
                self.assertEqual(calls, {"station_list"})
                continue
            self.assertNotIn("deye_client", text, py)
            self.assertNotIn("deyecloud", text, py)


if __name__ == "__main__":
    unittest.main()
