"""A small demo installation for the tests: two arrays (the second added later), a battery that
was extended, a two-rate tariff with Bulgarian-style seasonal night hours, VAT and coordinates."""
from __future__ import annotations

import json

from collector.tariffs import PRESETS

DEMO_SQL = """
INSERT INTO pv_array (name, mppt_channel, strings, panels, panel_wp, azimuth_deg, tilt_deg, valid_from, status)
VALUES ('Покрив', 1, 2, 18, 620, 170, 23, '2025-12-03', 'confirmed'),
       ('Навес', 2, 1, 16, 620, 225, 11, '2026-07-09', 'confirmed');
INSERT INTO battery_config (valid_from, valid_to, modules, kwh_per_module, soc_min_normal, soc_min_emergency, status)
VALUES ('2025-12-03', '2026-07-09', 4, 5.12, 30, 10, 'confirmed'),
       ('2026-07-09', NULL, 8, 5.12, 30, 10, 'confirmed');
INSERT INTO capex_item (date, description, amount_ccy, vat_included, status)
VALUES ('2025-12-09', 'Базова инсталация', 14834.88, 1, 'invoiced'),
       ('2026-06-08', 'Разширение', 9200.00, 1, 'invoiced');
INSERT INTO tariff (valid_from, valid_to, zone, price_per_kwh, vat_included, note)
VALUES ('2026-07-01', NULL, 'day', 0.12854, 0, 'тест'),
       ('2026-07-01', NULL, 'night', 0.0766, 0, 'тест');
UPDATE param SET value = 0.20 WHERE key = 'vat_rate';
INSERT INTO param (key, valid_from, value, unit) VALUES ('site_lat', '2000-01-01', 42.8, '°'), ('site_lon', '2000-01-01', 23.38, '°');
"""


def seed_demo(db, tz: str = "Europe/Sofia") -> None:
    db.executescript(DEMO_SQL)
    db.execute("UPDATE app_setting SET value = ? WHERE key = 'timezone'", (tz,))
    db.execute("UPDATE app_setting SET value = ? WHERE key = 'tariff_night_windows'", (json.dumps(PRESETS["bg_seasonal"]),))
    db.commit()
