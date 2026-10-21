import tempfile
import unittest
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

from collector import external
from collector.db import connect

from .fixtures import seed_demo

TZ = ZoneInfo("Europe/Sofia")


class ExternalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = connect(Path(self.tmp.name) / "pv.sqlite")
        seed_demo(self.db)

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_aspect_convention(self):
        self.assertEqual(external.aspect(180), 0)
        self.assertEqual(external.aspect(170), -10)
        self.assertEqual(external.aspect(225), 45)
        self.assertEqual(external.aspect(90), -90)

    def test_seed_arrays_have_orientation(self):
        names = {a["name"]: external.aspect(a["azimuth_deg"]) for a in external.arrays(self.db)}
        self.assertEqual(names, {"Покрив": -10, "Навес": 45})

    def test_weather_hour_is_the_preceding_hour(self):
        data = {"hourly": {"time": ["2026-06-10T10:00"], "shortwave_radiation": [800.0], "temperature_2m": [25.0],
                           "cloud_cover": [10]}}
        external._store_weather(self.db, TZ, data, None, "archive")
        row = self.db.execute("SELECT hour_ts, day, hour, ghi_wm2 FROM weather_hourly").fetchone()
        self.assertEqual(tuple(row), (1781082000, "2026-06-10", 12, 800.0))  # 09:00 UTC = 12:00 Sofia
        # a forecast value never replaces an archived one
        data["hourly"]["shortwave_radiation"] = [1.0]
        external._store_weather(self.db, TZ, data, None, "forecast")
        self.assertEqual(self.db.execute("SELECT ghi_wm2 FROM weather_hourly").fetchone()[0], 800.0)

    def test_pvgis_is_stored_per_kwp_and_cached(self):
        reply = {"inputs": {"meteo_data": {"radiation_db": "PVGIS-SARAH3"}},
                 "outputs": {"monthly": {"fixed": [{"month": m, "E_m": 111.6, "H(i)_m": 100, "SD_m": 11.16}
                                                   for m in range(1, 13)]}}}
        with mock.patch.object(external, "_get_json", return_value=reply) as get:
            self.assertEqual(external.fetch_pvgis(self.db), 2)
            self.assertEqual(external.fetch_pvgis(self.db), 0)  # same orientation: no new request
            self.assertEqual(get.call_count, 2)
        v = self.db.execute("SELECT kwh_per_kwp FROM pvgis_monthly WHERE array_id = 1 AND month = 1").fetchone()[0]
        self.assertAlmostEqual(v, 111.6 / 11.16)

    def test_nothing_here_talks_to_deye(self):
        text = Path(external.__file__).read_text(encoding="utf-8")
        self.assertNotIn("deye_client", text)
        self.assertNotIn("deyecloud", text)


if __name__ == "__main__":
    unittest.main()
