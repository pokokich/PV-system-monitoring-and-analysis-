import tempfile
import unittest
from pathlib import Path
from zoneinfo import ZoneInfo

from collector import energy, store
from collector.db import MIGRATIONS, MetricCache, connect

TZ = ZoneInfo("Europe/Sofia")
DAY0 = 1792357200  # 2026-10-19 00:00 Europe/Sofia


def frame(ts, gen=0.0, load=0.0, wire=0.0, batt=0.0, soc=50.0):
    return {"timeStamp": ts, "generationPower": gen, "consumptionPower": load, "wirePower": wire,
            "purchasePower": max(wire, 0), "batteryPower": batt, "batterySOC": soc}


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = connect(Path(self.tmp.name) / "t.sqlite")

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_migrations_apply_once_and_seed(self):
        connect(Path(self.tmp.name) / "t.sqlite").close()  # second run is a no-op
        # a new installation starts empty: no arrays, battery, tariffs or invoices, only defaults
        for table in ("pv_array", "battery_config", "tariff", "capex_item", "grid_invoice", "benchmark"):
            self.assertEqual(self.db.execute(f"SELECT count(*) FROM {table}").fetchone()[0], 0, table)
        self.assertEqual(self.db.execute("SELECT value FROM app_setting WHERE key = 'currency'").fetchone()[0], "EUR")
        self.assertEqual(self.db.execute("SELECT count(*) FROM schema_version").fetchone()[0],
                         len(list(MIGRATIONS.glob("[0-9][0-9][0-9]_*.sql"))))

    def test_station_frames_upsert_and_energy(self):
        # 1 hour at ~6 kW PV (real power never repeats exactly), 2 kW load, 4 kW charging,
        # 1 kW import for the first 6 frames
        items = [frame(DAY0 + i * 300, gen=5990 if i % 2 else 6010, load=2000, batt=-4000, wire=1000 if i < 6 else 0)
                 for i in range(13)]
        store.upsert_station_frames(self.db, 1, items, "power")
        store.upsert_station_frames(self.db, 1, items, "power")  # idempotent
        energy.rebuild(self.db, 1, DAY0, DAY0 + 3600, TZ)
        day = self.db.execute("SELECT * FROM energy_daily WHERE day = '2026-10-19'").fetchone()
        self.assertEqual(day["frames"], 13)
        self.assertEqual(day["covered_s"], 3600)
        self.assertAlmostEqual(day["pv_kwh"], 6.0)
        self.assertAlmostEqual(day["load_kwh"], 2.0)
        self.assertAlmostEqual(day["charge_kwh"], 4.0)
        self.assertAlmostEqual(day["discharge_kwh"], 0.0)
        self.assertAlmostEqual(day["import_kwh"], 5.5 * 300 / 3600)  # 5 full steps + the ramp down
        hour = self.db.execute("SELECT * FROM energy_hourly").fetchone()
        self.assertEqual((hour["day"], hour["hour"]), ("2026-10-19", 0))

    def test_long_gap_not_integrated(self):
        items = [frame(DAY0, gen=5000), frame(DAY0 + 600, gen=5000), frame(DAY0 + 600 + 7200, gen=5000)]
        store.upsert_station_frames(self.db, 1, items, "power")
        energy.rebuild(self.db, 1, DAY0, DAY0 + 86400, TZ)
        rows = self.db.execute("SELECT quality, pv_kwh FROM energy_5min ORDER BY ts_start").fetchall()
        self.assertEqual([r["quality"] for r in rows], ["interp"])
        self.assertAlmostEqual(rows[0]["pv_kwh"], 5000 * 600 / 3600 / 1000)

    def test_frozen_pv_replaced_by_balance(self):
        # PV frozen at 4850 W for 2 h at night while the battery covers a 500 W load
        items = [frame(DAY0 + i * 300, gen=4850, load=500, batt=500) for i in range(25)]
        store.upsert_station_frames(self.db, 1, items, "power")
        energy.rebuild(self.db, 1, DAY0, DAY0 + 7200, TZ)
        day = self.db.execute("SELECT pv_kwh, discharge_kwh FROM energy_daily").fetchone()
        self.assertAlmostEqual(day["pv_kwh"], 0.0)
        self.assertAlmostEqual(day["discharge_kwh"], 1.0)
        self.assertEqual({r[0] for r in self.db.execute("SELECT quality FROM energy_5min")}, {"pv_estimated"})

    def test_device_frame_merge_and_text_values(self):
        metrics = MetricCache(self.db)
        pts = [{"key": "DCPowerPV1", "value": "7530.00", "unit": "W"}, {"key": "MAIN", "value": "3002-1098-1E08"}]
        self.assertTrue(store.store_device_frame(self.db, metrics, "INV", DAY0 + 100, pts, "latest"))
        self.assertFalse(store.store_device_frame(self.db, metrics, "INV", DAY0 + 100, pts, "latest"))
        # history point a few seconds off joins the same frame
        hist = [{"key": "SOC", "value": "41", "unit": "%"}]
        self.assertFalse(store.store_device_frame(self.db, metrics, "INV", DAY0 + 104, hist, "history",
                                                  merge_window=90))
        self.assertEqual(self.db.execute("SELECT count(*) FROM device_frame").fetchone()[0], 1)
        self.assertEqual(self.db.execute("SELECT count(*) FROM measurement").fetchone()[0], 2)
        self.assertEqual(self.db.execute("SELECT value FROM device_text").fetchone()[0], "3002-1098-1E08")

    def test_deye_totals(self):
        store.upsert_deye_daily(self.db, 1, [{"year": 2026, "month": 10, "day": 19, "generationValue": 66.0,
                                              "consumptionValue": 50.0, "gridValue": 0.0}])
        store.upsert_inverter_daily(self.db, 1, [{"time": "2026-10-19", "itemList": [
            {"key": "Production", "value": "66.00"}, {"key": "GridFeed-in", "value": "0.00"}]}])
        rows = self.db.execute("SELECT source, generation_kwh FROM deye_daily ORDER BY source").fetchall()
        self.assertEqual([tuple(r) for r in rows], [("inverter", 66.0), ("station", 66.0)])

    def test_settings_stored_only_on_change(self):
        resp = {"code": "1000000", "success": True, "maxChargeCurrent": 35, "maxDischargeCurrent": 32}
        self.assertTrue(store.store_device_settings(self.db, "INV", "battery", resp))
        self.assertFalse(store.store_device_settings(self.db, "INV", "battery", dict(resp, requestId="x")))
        self.assertEqual(self.db.execute("SELECT count(*) FROM device_settings").fetchone()[0], 1)
        self.assertEqual(self.db.execute(
            "SELECT count(*) FROM event_log WHERE kind = 'setting_change'").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
