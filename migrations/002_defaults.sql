-- Settings of the installation that are not numbers with a validity period. The first-run
-- setup (/setup) and the Settings page write them; the collector fills what Deye knows
-- (station name, coordinates) when they are still empty.
CREATE TABLE app_setting (
    key        TEXT PRIMARY KEY,
    value      TEXT,
    updated_at INTEGER
);

INSERT INTO app_setting (key, value) VALUES
 ('currency', 'EUR'),                -- ISO 4217 code; amounts are never converted
 ('timezone', NULL),                 -- IANA name, e.g. Europe/Berlin; NULL = TZ env or the computer's zone
 ('site_name', NULL),                -- shown in the header; NULL = the Deye station name
 ('language', NULL),                 -- bg | en; NULL = the browser's language
 -- Night (low) tariff hours per season, local time; empty list = single-rate tariff.
 -- [{"months": [from, to], "start": "HH:MM", "end": "HH:MM"}], months wrap (11..3 = Nov-Mar).
 ('tariff_night_windows', '[]'),
 ('setup_done', NULL);               -- set by /setup when the owner finished the first-run wizard

-- Numbers every installation starts with; all editable on the Settings page.
INSERT INTO param (key, valid_from, value, unit, note) VALUES
 ('vat_rate', '2000-01-01', 0, 'fraction', 'VAT on electricity when tariffs are entered without VAT (0.20 = 20%)');
