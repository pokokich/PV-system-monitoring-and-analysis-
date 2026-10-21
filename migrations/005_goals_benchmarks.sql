-- Step 3: goals, benchmarks and external data (PVGIS, Open-Meteo).
-- The collector fills pvgis_monthly, weather_hourly and poa_hourly; the dashboard
-- writes only goal, goal_status, benchmark, scenario and the 'goal_*' params
-- (see web/settings.py).

-- ---------------------------------------------------------------- external data
-- PVGIS PVcalc per array, normalised per kWp so a change of panels only rescales it
CREATE TABLE pvgis_monthly (
    array_id        INTEGER NOT NULL REFERENCES pv_array(id),
    month           INTEGER NOT NULL,                -- 1..12
    kwh_per_kwp     REAL NOT NULL,                   -- E_m / peakpower
    irr_kwh_m2      REAL,                            -- H(i)_m, in the array plane
    sd_kwh_per_kwp  REAL,                            -- SD_m / peakpower (year-to-year spread)
    tilt_deg        REAL,
    aspect_deg      REAL,                            -- PVGIS: 0 = south, -90 = east, 90 = west
    loss_pct        REAL,
    radiation_db    TEXT,
    fetched_at      INTEGER,
    PRIMARY KEY (array_id, month)
);

-- Open-Meteo hourly weather; radiation is the mean of the hour that starts at hour_ts
CREATE TABLE weather_hourly (
    hour_ts    INTEGER PRIMARY KEY,                  -- start of the hour, unix
    day        TEXT NOT NULL,                        -- local date
    hour       INTEGER NOT NULL,                     -- local hour
    ghi_wm2    REAL,                                 -- shortwave_radiation (horizontal)
    temp_c     REAL,
    cloud_pct  REAL,
    source     TEXT NOT NULL,                        -- archive | forecast
    fetched_at INTEGER
);

-- Irradiance in the plane of each array (Open-Meteo global_tilted_irradiance)
CREATE TABLE poa_hourly (
    hour_ts    INTEGER NOT NULL,
    array_id   INTEGER NOT NULL REFERENCES pv_array(id),
    gti_wm2    REAL,
    source     TEXT NOT NULL,
    PRIMARY KEY (hour_ts, array_id)
) WITHOUT ROWID;

INSERT INTO param (key, valid_from, value, unit, note) VALUES
 ('pvgis_loss_pct', '2000-01-01', 14, '%', 'системни загуби за PVGIS (кабели, инвертор, замърсяване); 14% е стойността по подразбиране на PVGIS'),
 ('goal_curtail_value_ccy_kwh', '2000-01-01', 0, 'per kWh',
  'с колко се остойностява пропуснатата енергия „ако имаше договор за продажба“ (цена за kWh); 0 = не се остойностява');

-- ---------------------------------------------------------------- payback scenarios
CREATE TABLE scenario (
    name                     TEXT PRIMARY KEY,     -- conservative | base | optimistic
    label                    TEXT NOT NULL,
    sort                     INTEGER NOT NULL,
    price_escalation_pct     REAL NOT NULL,        -- yearly increase of the electricity price
    degradation_pct          REAL NOT NULL,        -- yearly loss of PV yield
    battery_replacement_year INTEGER,              -- years after commissioning
    battery_replacement_ccy  REAL,                 -- NULL = unknown, not counted
    inverter_replacement_year INTEGER,
    inverter_replacement_ccy REAL,
    discount_rate_pct        REAL,
    note                     TEXT,
    updated_at               INTEGER
);

-- Starting proposals; the owner edits them on /goals
INSERT INTO scenario VALUES
 ('conservative', 'Консервативен', 1, 0, 0.6, 10, NULL, 10, NULL, 6, 'стартово предложение; цените на подмяна не са известни', NULL),
 ('base',         'Базов',         2, 3, 0.4, 13, NULL, 12, NULL, 4, 'стартово предложение; цените на подмяна не са известни', NULL),
 ('optimistic',   'Оптимистичен',  3, 5, 0.3, 16, NULL, NULL, NULL, 3, 'стартово предложение', NULL);

-- ---------------------------------------------------------------- goals
ALTER TABLE goal ADD COLUMN title TEXT;
ALTER TABLE goal ADD COLUMN active INTEGER NOT NULL DEFAULT 1;
ALTER TABLE goal ADD COLUMN updated_at INTEGER;

-- period_type 'year' with period_start NULL = the current calendar year (moves on by itself);
-- 'ltd' = from commissioning on; 'rolling12' = the last 12 months.
INSERT INTO goal (kpi_id, title, period_type, period_start, period_end, target, direction, warn_level,
                  scenario, note, created_at) VALUES
 ('F04', 'Окупаемост на цялата инвестиция', 'ltd', NULL, NULL, 12, 'le', 15, 'base',
  'стартово предложение; прогнозата е по базовия сценарий', strftime('%s', 'now')),
 ('U02', 'Самозадоволяване', 'year', NULL, NULL, 70, 'ge', 60, NULL,
  'стартово предложение: дял от консумацията, покрит от PV и батерията', strftime('%s', 'now')),
 ('U03', 'Нощно покритие от батерията', 'year', NULL, NULL, 70, 'ge', 60, NULL,
  'стартово предложение: дял от нощната консумация, покрит от батерията', strftime('%s', 'now')),
 ('P04', 'Производство спрямо PVGIS', 'year', NULL, NULL, 90, 'ge', 80, NULL,
  'реалното PV спрямо очакването по PVGIS за масивите; ограничаването при пълна батерия го намалява', strftime('%s', 'now')),
 ('B04', 'Ефективност на батерията (разряд / заряд)', 'rolling12', NULL, NULL, 90, 'ge', 88, NULL,
  'горна граница на round-trip ефективността', strftime('%s', 'now'));

-- ---------------------------------------------------------------- benchmarks
-- Rows of `benchmark` come from the owner: kind 'plan' (F01 per calendar month = the business
-- plan behind goal F10) and 'manual'. PVGIS values live in pvgis_monthly.
