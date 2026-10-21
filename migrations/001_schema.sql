-- PV dashboard database (SQLite). Times are unix seconds (UTC); `day`/`month` columns
-- are local dates in the time zone of the installation (setting `timezone`). Energy in kWh, power in W.

-- ---------------------------------------------------------------- reference
CREATE TABLE station (
    id                 INTEGER PRIMARY KEY,          -- Deye stationId
    name               TEXT,
    timezone           TEXT,
    start_operating_ts INTEGER,
    installed_kwp      REAL,                         -- as currently set in Deye
    meta_json          TEXT,
    updated_at         INTEGER
);

CREATE TABLE device (
    sn             TEXT PRIMARY KEY,                 -- Deye deviceSn
    deye_device_id INTEGER,
    station_id     INTEGER REFERENCES station(id),
    type           TEXT,                             -- INVERTER | BATTERY | COLLECTOR
    product_id     TEXT,
    first_seen     INTEGER,
    meta_json      TEXT
);

-- Deye measure point keys (DCPowerPV1, SOC, ...), created on first sight
CREATE TABLE metric (
    id         INTEGER PRIMARY KEY,
    key        TEXT NOT NULL UNIQUE,
    unit       TEXT,
    first_seen INTEGER
);

-- ---------------------------------------------------------------- raw layer
-- Every Deye response, zlib-compressed JSON, so values can be re-derived later.
CREATE TABLE raw_payload (
    id           INTEGER PRIMARY KEY,
    fetched_at   INTEGER NOT NULL,
    endpoint     TEXT NOT NULL,
    request_json TEXT,
    response_gz  BLOB
);
CREATE INDEX raw_payload_fetched ON raw_payload(fetched_at);

-- ---------------------------------------------------------------- 5-minute frames
-- Station frames from /station/history and /station/history/power. Deye aligns them
-- to a 5-minute grid. Signs as in Deye: wire_w > 0 import, < 0 export;
-- battery_w > 0 discharge, < 0 charge.
CREATE TABLE station_frame (
    station_id    INTEGER NOT NULL,
    ts            INTEGER NOT NULL,
    generation_w  REAL,
    consumption_w REAL,
    purchase_w    REAL,
    wire_w        REAL,
    charge_w      REAL,
    discharge_w   REAL,
    battery_w     REAL,
    soc           REAL,
    source        TEXT NOT NULL,                     -- history | power
    PRIMARY KEY (station_id, ts)
) WITHOUT ROWID;

-- Inverter frames at the logger's collectionTime (~every 5 min, not grid aligned).
-- source: latest = full /device/latest snapshot (all points);
--         history = /device/history (only the points in HISTORY_POINT_BATCHES).
CREATE TABLE device_frame (
    device_sn  TEXT NOT NULL,
    ts         INTEGER NOT NULL,
    source     TEXT NOT NULL,
    fetched_at INTEGER,
    PRIMARY KEY (device_sn, ts)
) WITHOUT ROWID;

CREATE TABLE measurement (
    device_sn TEXT NOT NULL,
    ts        INTEGER NOT NULL,
    metric_id INTEGER NOT NULL REFERENCES metric(id),
    value     REAL,
    PRIMARY KEY (device_sn, ts, metric_id)
) WITHOUT ROWID;
CREATE INDEX measurement_metric_ts ON measurement(metric_id, ts);

-- Non-numeric points (firmware versions etc.), one row per distinct value
CREATE TABLE device_text (
    device_sn     TEXT NOT NULL,
    metric_id     INTEGER NOT NULL REFERENCES metric(id),
    value         TEXT NOT NULL,
    first_seen_ts INTEGER,
    last_seen_ts  INTEGER,
    PRIMARY KEY (device_sn, metric_id, value)
) WITHOUT ROWID;

-- ---------------------------------------------------------------- energy (calculated)
-- Energy between adjacent station frames (trapezoid on power). An interval belongs
-- to the local hour/day in which it starts. quality: ok (<= 7 min), interp (<= 30 min),
-- pv_estimated (Deye's PV power was frozen, PV taken from the energy balance);
-- longer gaps are not integrated and leave no row.
CREATE TABLE energy_5min (
    station_id    INTEGER NOT NULL,
    ts_start      INTEGER NOT NULL,
    ts_end        INTEGER NOT NULL,
    pv_kwh        REAL,
    load_kwh      REAL,
    import_kwh    REAL,
    export_kwh    REAL,
    charge_kwh    REAL,
    discharge_kwh REAL,
    soc_start     REAL,
    soc_end       REAL,
    quality       TEXT NOT NULL,
    PRIMARY KEY (station_id, ts_start)
) WITHOUT ROWID;

CREATE TABLE energy_hourly (
    station_id    INTEGER NOT NULL,
    hour_ts       INTEGER NOT NULL,                  -- start of the local hour, unix
    day           TEXT NOT NULL,                     -- local date
    hour          INTEGER NOT NULL,                  -- local hour 0..23
    pv_kwh        REAL,
    load_kwh      REAL,
    import_kwh    REAL,
    export_kwh    REAL,
    charge_kwh    REAL,
    discharge_kwh REAL,
    soc_min       REAL,
    soc_max       REAL,
    covered_s     INTEGER,                           -- seconds of integrated intervals
    PRIMARY KEY (station_id, hour_ts)
) WITHOUT ROWID;

CREATE TABLE energy_daily (
    station_id    INTEGER NOT NULL,
    day           TEXT NOT NULL,
    pv_kwh        REAL,
    load_kwh      REAL,
    import_kwh    REAL,
    export_kwh    REAL,
    charge_kwh    REAL,
    discharge_kwh REAL,
    pv_peak_w     REAL,
    import_peak_w REAL,
    soc_min       REAL,
    soc_max       REAL,
    frames        INTEGER,
    covered_s     INTEGER,                           -- 86400 (82800/90000 on DST days) = complete
    PRIMARY KEY (station_id, day)
) WITHOUT ROWID;

-- ---------------------------------------------------------------- Deye's own totals
-- Daily totals as Deye reports them, for cross-checks.
-- source: station (/station/history granularity 2) | inverter (/device/history granularity 2)
CREATE TABLE deye_daily (
    station_id       INTEGER NOT NULL,
    day              TEXT NOT NULL,
    source           TEXT NOT NULL,
    generation_kwh   REAL,
    consumption_kwh  REAL,
    purchase_kwh     REAL,
    export_kwh       REAL,
    charge_kwh       REAL,
    discharge_kwh    REAL,
    full_power_hours REAL,
    fetched_at       INTEGER,
    PRIMARY KEY (station_id, day, source)
) WITHOUT ROWID;

CREATE TABLE deye_monthly (
    station_id       INTEGER NOT NULL,
    month            TEXT NOT NULL,                  -- YYYY-MM
    generation_kwh   REAL,
    consumption_kwh  REAL,
    purchase_kwh     REAL,
    export_kwh       REAL,
    charge_kwh       REAL,
    discharge_kwh    REAL,
    full_power_hours REAL,
    fetched_at       INTEGER,
    PRIMARY KEY (station_id, month)
) WITHOUT ROWID;

-- ---------------------------------------------------------------- manual parameters
-- Money amounts are in the currency of the installation (setting `currency`), never converted.
-- All versioned in time: valid_from inclusive, valid_to exclusive (local dates), NULL = open.
-- status: confirmed | assumed | estimate
CREATE TABLE pv_array (
    id           INTEGER PRIMARY KEY,
    name         TEXT NOT NULL,
    mppt_channel INTEGER,                            -- 1 => DCPowerPV1 ...
    strings      INTEGER,
    panels       INTEGER,
    panel_model  TEXT,
    panel_wp     REAL,
    azimuth_deg  REAL,
    tilt_deg     REAL,
    shading_note TEXT,
    valid_from   TEXT NOT NULL,
    valid_to     TEXT,
    status       TEXT NOT NULL DEFAULT 'confirmed',
    note         TEXT
);

CREATE TABLE battery_config (
    id                 INTEGER PRIMARY KEY,
    valid_from         TEXT NOT NULL,
    valid_to           TEXT,
    modules            INTEGER,
    kwh_per_module     REAL,
    soc_min_normal     REAL,
    soc_min_emergency  REAL,
    charge_limit_a     REAL,
    discharge_limit_a  REAL,
    status             TEXT NOT NULL DEFAULT 'confirmed',
    note               TEXT
);

CREATE TABLE tariff (
    id            INTEGER PRIMARY KEY,
    valid_from    TEXT NOT NULL,
    valid_to      TEXT,
    zone          TEXT NOT NULL,                     -- day | night | single
    price_per_kwh REAL NOT NULL,                     -- per kWh, installation currency
    vat_included  INTEGER NOT NULL DEFAULT 1,
    window_json   TEXT,                              -- local hours per season, months as [from, to]
    fees_json     TEXT,
    note          TEXT
);

CREATE TABLE feed_in_price (
    id            INTEGER PRIMARY KEY,
    valid_from    TEXT NOT NULL,
    valid_to      TEXT,
    price_per_kwh REAL NOT NULL,                     -- per kWh, installation currency
    contract_ref  TEXT,
    note          TEXT
);

CREATE TABLE capex_item (
    id           INTEGER PRIMARY KEY,
    date         TEXT NOT NULL,
    description  TEXT NOT NULL,
    amount_ccy   REAL,
    vat_included INTEGER NOT NULL DEFAULT 1,
    status       TEXT NOT NULL,                      -- invoiced | paid | estimate | unknown
    document_ref TEXT,
    note         TEXT
);

CREATE TABLE opex_item (
    id          INTEGER PRIMARY KEY,
    date        TEXT NOT NULL,
    description TEXT NOT NULL,
    amount_ccy  REAL NOT NULL,
    category    TEXT
);

-- Anything else that is a single number with a validity period (emission factor,
-- escalation assumptions...)
CREATE TABLE param (
    key        TEXT NOT NULL,
    valid_from TEXT NOT NULL,
    value      REAL,
    value_text TEXT,
    unit       TEXT,
    note       TEXT,
    PRIMARY KEY (key, valid_from)
);

CREATE TABLE threshold (
    id           INTEGER PRIMARY KEY,
    metric_code  TEXT NOT NULL,
    op           TEXT NOT NULL,
    value        REAL NOT NULL,
    duration_min INTEGER,
    severity     TEXT
);

-- ---------------------------------------------------------------- goals and benchmarks
CREATE TABLE benchmark (
    id     INTEGER PRIMARY KEY,
    kind   TEXT NOT NULL,                            -- pvgis | legacy_model | manual | last_year
    kpi_id TEXT NOT NULL,
    period TEXT NOT NULL,
    value  REAL,
    note   TEXT
);

CREATE TABLE goal (
    id           INTEGER PRIMARY KEY,
    kpi_id       TEXT NOT NULL,
    period_type  TEXT NOT NULL,                      -- day | month | year | rolling12 | ltd
    period_start TEXT,
    period_end   TEXT,
    target       REAL NOT NULL,
    direction    TEXT NOT NULL,                      -- ge | le
    warn_level   REAL,
    benchmark_id INTEGER REFERENCES benchmark(id),
    scenario     TEXT,
    note         TEXT,
    created_at   INTEGER
);

CREATE TABLE goal_status (
    goal_id  INTEGER NOT NULL REFERENCES goal(id),
    as_of    TEXT NOT NULL,
    actual   REAL,
    forecast REAL,
    status   TEXT,                                   -- met | on_track | at_risk | behind
    PRIMARY KEY (goal_id, as_of)
);

CREATE TABLE kpi_value (
    kpi_id         TEXT NOT NULL,
    period_type    TEXT NOT NULL,
    period_start   TEXT NOT NULL,
    scenario       TEXT NOT NULL DEFAULT 'base',
    value          REAL,
    is_conditional INTEGER NOT NULL DEFAULT 0,
    computed_at    INTEGER,
    PRIMARY KEY (kpi_id, period_type, period_start, scenario)
);

-- ---------------------------------------------------------------- events and data quality
CREATE TABLE event_log (
    id      INTEGER PRIMARY KEY,
    ts      INTEGER NOT NULL,
    kind    TEXT NOT NULL,                           -- setting_change | repair | cleaning | outage | expansion | note
    title   TEXT NOT NULL,
    details TEXT,
    source  TEXT NOT NULL DEFAULT 'manual'
);

CREATE TABLE alert (
    id        INTEGER PRIMARY KEY,
    ts_start  INTEGER,
    ts_end    INTEGER,
    device_sn TEXT,
    code      TEXT,
    message   TEXT,
    severity  TEXT,
    raw_json  TEXT,
    UNIQUE (device_sn, ts_start, code)
);

CREATE TABLE ingest_log (
    id          INTEGER PRIMARY KEY,
    ts          INTEGER NOT NULL,
    job         TEXT NOT NULL,
    endpoint    TEXT,
    status      TEXT NOT NULL,                       -- ok | error
    points      INTEGER,
    duration_ms INTEGER,
    error       TEXT
);
CREATE INDEX ingest_log_ts ON ingest_log(ts);

-- Collector bookkeeping (backfill progress etc.)
CREATE TABLE collector_state (
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- ---------------------------------------------------------------- views
-- Calculated daily energy next to Deye's own daily totals.
CREATE VIEW v_daily_check AS
SELECT e.station_id, e.day, e.covered_s,
       e.pv_kwh, s.generation_kwh AS deye_pv_kwh, i.generation_kwh AS inv_pv_kwh,
       e.load_kwh, s.consumption_kwh AS deye_load_kwh,
       e.import_kwh, s.purchase_kwh AS deye_import_kwh,
       e.export_kwh, s.export_kwh AS deye_export_kwh,
       e.charge_kwh, s.charge_kwh AS deye_charge_kwh,
       e.discharge_kwh, s.discharge_kwh AS deye_discharge_kwh
FROM energy_daily e
LEFT JOIN deye_daily s ON s.station_id = e.station_id AND s.day = e.day AND s.source = 'station'
LEFT JOIN deye_daily i ON i.station_id = e.station_id AND i.day = e.day AND i.source = 'inverter';

-- Installed kWp per array and in total, by validity period
CREATE VIEW v_pv_capacity AS
SELECT id, name, mppt_channel, valid_from, valid_to, panels, panel_wp,
       panels * panel_wp / 1000.0 AS kwp
FROM pv_array;
