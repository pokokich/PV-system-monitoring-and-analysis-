-- Electricity supplier invoices: entered by hand on the Settings page, or imported with
-- `collector.main import-invoices` (built-in reader: Electrohold, Bulgaria). Amounts are in the
-- installation currency. Meter readings give the grid import measured by the utility meter
-- (KPI D04) and the pre-PV consumption baseline.
CREATE TABLE grid_invoice (
    invoice_no     TEXT PRIMARY KEY,
    issued         TEXT NOT NULL,
    period_from    TEXT NOT NULL,                    -- first reading date (local)
    period_to      TEXT NOT NULL,                    -- last reading date (local)
    meter_no       TEXT,
    day_kwh        REAL,
    night_kwh      REAL,
    energy_ccy     REAL,                             -- supply (active energy), without VAT
    network_ccy    REAL,                             -- transmission and distribution, without VAT
    tax_base_ccy   REAL,
    vat_ccy        REAL,
    total_ccy      REAL,
    source_file    TEXT
);

-- One row per zone and reading sub-period (an invoice is split when prices change)
CREATE TABLE grid_invoice_reading (
    invoice_no  TEXT NOT NULL REFERENCES grid_invoice(invoice_no),
    period_from TEXT NOT NULL,
    period_to   TEXT NOT NULL,
    zone        TEXT NOT NULL,                       -- day | night
    old_reading REAL,
    new_reading REAL,
    kwh         REAL,
    PRIMARY KEY (invoice_no, period_from, zone)
);

CREATE TABLE grid_invoice_line (
    id             INTEGER PRIMARY KEY,
    invoice_no     TEXT NOT NULL REFERENCES grid_invoice(invoice_no),
    kind           TEXT NOT NULL,                    -- energy | excise | transmission | transmission_access | distribution_lv | distribution_access
    zone           TEXT,
    qty_kwh        REAL,
    unit_price_ccy REAL,
    amount_ccy     REAL
);
CREATE INDEX grid_invoice_line_invoice ON grid_invoice_line(invoice_no);
