-- Snapshots of the inverter's settings read from /config/battery, /config/system and
-- /config/tou (read-only). A new row is stored only when the settings change, and the
-- change is also written to event_log so it shows on the charts.
CREATE TABLE device_settings (
    device_sn     TEXT NOT NULL,
    kind          TEXT NOT NULL,                     -- battery | system | tou
    fetched_at    INTEGER NOT NULL,
    settings_json TEXT NOT NULL,
    PRIMARY KEY (device_sn, kind, fetched_at)
) WITHOUT ROWID;

CREATE VIEW v_device_settings_current AS
SELECT s.* FROM device_settings s
WHERE s.fetched_at = (SELECT max(fetched_at) FROM device_settings x
                      WHERE x.device_sn = s.device_sn AND x.kind = s.kind);
