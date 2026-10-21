"""Collector entry point.

    python3 -m collector.main backfill   # one-off: all history Deye still has
    python3 -m collector.main poll       # one polling round
    python3 -m collector.main run        # continuous mode (backfill if needed, then poll every 5-7 min)
    python3 -m collector.main check      # calculated energy vs Deye's daily totals and counters
    python3 -m collector.main external   # PVGIS and Open-Meteo benchmarks (also hourly inside `run`)

Read-only towards Deye: only the allowlisted endpoints in deye_client are used.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import random
import time
from datetime import date, datetime, timedelta

from . import appconf, energy, external, store
from .db import MetricCache, connect, get_state, log_ingest, save_raw, set_state
from .deye_client import DeyeApiError, DeyeClient, DeyeConfig, load_env

log = logging.getLogger("collector")

# /device/history (granularity 1) takes at most 5 points per call and Deye keeps it
# only ~90 days back, so these are the points worth keeping from it. /device/latest
# (live polling) stores all ~103 points regardless.
HISTORY_POINT_BATCHES = [
    ["DCPowerPV1", "DCPowerPV2", "DCVoltagePV1", "DCVoltagePV2", "TotalSolarPower"],
    ["DCCurrentPV1", "DCCurrentPV2", "TotalInverterOutputPower", "AC Temperature", "Temperature- Battery"],
    ["BatteryPower", "BatteryVoltage", "BatteryCurrent", "SOC", "BMSSOC"],
    ["TotalGridPower", "TotalConsumptionPower", "LoadPowerL1", "LoadPowerL2", "LoadPowerL3"],
    ["GridVoltageL1", "GridVoltageL2", "GridVoltageL3", "GridFrequency", "UPSLoadPower"],
    ["DailyActiveProduction", "DailyConsumption", "DailyEnergyPurchased", "DailyChargingEnergy",
     "DailyDischargingEnergy"],
    ["TotalActiveProduction", "TotalConsumption", "TotalEnergyBuy", "TotalChargeEnergy", "TotalDischargeEnergy"],
]
DEVICE_HISTORY_DAYS = 92          # Deye answers empty lists further back
STATION_WINDOW_DAYS = 30          # /station/history/power window per call during backfill
LOGGER_STEP_S = 300
MERGE_WINDOW_S = 90               # history point within this of a stored frame = same frame


class Collector:
    def __init__(self):
        load_env()
        self.db = connect(os.environ.get("DB_PATH", "./data/pv.sqlite"))
        self.tz = appconf.timezone(self.db)
        self.client = DeyeClient(self._config())
        self.metrics = MetricCache(self.db)
        self.station_id: int | None = None
        self.inverter_sn: str | None = None

    @staticmethod
    def _config() -> DeyeConfig:
        cfg = DeyeConfig.from_env()
        alt = os.environ.get("DEYE_PASSWORD_ALT")
        if alt:
            try:
                DeyeClient(cfg)._get_token()
            except DeyeApiError:
                log.warning("login with DEYE_PASSWORD failed, trying DEYE_PASSWORD_ALT")
                cfg.password = alt
        return cfg

    # ------------------------------------------------------------ helpers
    def call(self, job: str, endpoint: str, request: dict, fn):
        t0 = time.monotonic()
        try:
            resp = fn()
        except Exception as exc:  # logged and re-raised; the loop decides what to do
            log_ingest(self.db, job, endpoint, "error", duration_ms=int((time.monotonic() - t0) * 1000),
                       error=str(exc)[:500])
            self.db.commit()
            raise
        save_raw(self.db, endpoint, request, resp)
        log_ingest(self.db, job, endpoint, "ok", duration_ms=int((time.monotonic() - t0) * 1000))
        return resp

    def today(self) -> date:
        return datetime.now(self.tz).date()

    def local_date(self, ts: float) -> date:
        return datetime.fromtimestamp(ts, self.tz).date()

    # ------------------------------------------------------------ discovery
    def discover(self) -> None:
        stations = self.call("discover", "/station/list", {}, self.client.station_list)["stationList"]
        wanted = os.environ.get("DEYE_STATION_ID")
        st = next((s for s in stations if not wanted or str(s["id"]) == wanted), None)
        if st is None:
            raise SystemExit("no station found for this account")
        self.station_id = int(st["id"])
        self.db.execute(
            "INSERT INTO station (id, name, timezone, start_operating_ts, installed_kwp, meta_json, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET name = excluded.name, "
            "timezone = excluded.timezone, start_operating_ts = excluded.start_operating_ts, "
            "installed_kwp = excluded.installed_kwp, meta_json = excluded.meta_json, updated_at = excluded.updated_at",
            (self.station_id, st.get("name"), st.get("regionTimezone"), int(st.get("startOperatingTime") or 0),
             st.get("installedCapacity"), json.dumps(st, ensure_ascii=False), int(time.time())),
        )
        self._seed_site(st)
        devices = self.call("discover", "/station/device", {"stationIds": [self.station_id]},
                            lambda: self.client.station_devices([self.station_id]))
        for d in devices.get("deviceListItems", []):
            store.upsert_device(self.db, d)
        wanted_sn = os.environ.get("DEYE_DEVICE_SN")
        inverters = [d["deviceSn"] for d in devices.get("deviceListItems", []) if d.get("deviceType") == "INVERTER"]
        self.inverter_sn = wanted_sn or (inverters[0] if inverters else None)
        self.db.commit()
        log.info("station %s, inverter %s", self.station_id, self.inverter_sn)

    def _seed_site(self, st: dict) -> None:
        """Coordinates and name from Deye, only where the owner has not set them."""
        for key, value in (("site_lat", st.get("locationLat")), ("site_lon", st.get("locationLng"))):
            if value in (None, "", 0):
                continue
            if self.db.execute("SELECT 1 FROM param WHERE key = ?", (key,)).fetchone() is None:
                self.db.execute("INSERT INTO param (key, valid_from, value, unit, note) VALUES (?, '2000-01-01', ?, '°', ?)",
                                (key, float(value), "координати на станцията в Deye"))
        if st.get("installedCapacity") and self.db.execute("SELECT 1 FROM pv_array").fetchone() is None:
            log.info("no PV arrays entered yet: Deye reports %s kWp; enter the arrays on /setup", st["installedCapacity"])

    def start_date(self) -> date:
        row = self.db.execute("SELECT start_operating_ts FROM station WHERE id = ?", (self.station_id,)).fetchone()
        return self.local_date(row[0])

    # ------------------------------------------------------------ station data
    def fetch_station_frames(self, start_ts: int, end_ts: int, job: str) -> int:
        req = {"stationId": self.station_id, "startTimestamp": start_ts, "endTimestamp": end_ts}
        resp = self.call(job, "/station/history/power", req,
                         lambda: self.client.station_history_power(self.station_id, start_ts, end_ts))
        n = store.upsert_station_frames(self.db, self.station_id, resp.get("stationDataItems") or [], "power")
        energy.rebuild(self.db, self.station_id, start_ts, end_ts, self.tz)
        self.db.commit()
        return n

    def fetch_daily_totals(self, first: date, last: date, job: str) -> None:
        """Deye's daily totals (station and inverter), max 31 days per call."""
        d = first
        while d <= last:
            end = min(last, d + timedelta(days=30))
            req = {"stationId": self.station_id, "granularity": 2, "startAt": d.isoformat(), "endAt": end.isoformat()}
            resp = self.call(job, "/station/history", req,
                             lambda: self.client.station_history(self.station_id, 2, d.isoformat(), end.isoformat()))
            store.upsert_deye_daily(self.db, self.station_id, resp.get("stationDataItems") or [])
            if self.inverter_sn:
                req = {"deviceSn": self.inverter_sn, "granularity": 2, "startAt": d.isoformat(), "endAt": end.isoformat()}
                try:
                    resp = self.call(job, "/device/history", req, lambda: self.client.device_history(
                        self.inverter_sn, 2, d.isoformat(), end.isoformat()))
                    store.upsert_inverter_daily(self.db, self.station_id, resp.get("dataList") or [])
                except DeyeApiError as exc:
                    log.warning("inverter daily totals %s..%s: %s", d, end, exc)
            self.db.commit()
            d = end + timedelta(days=1)

    def fetch_monthly_totals(self, first: date, last: date, job: str) -> None:
        start, end = first.strftime("%Y-%m"), last.strftime("%Y-%m")
        req = {"stationId": self.station_id, "granularity": 3, "startAt": start, "endAt": end}
        resp = self.call(job, "/station/history", req,
                         lambda: self.client.station_history(self.station_id, 3, start, end))
        store.upsert_deye_monthly(self.db, self.station_id, resp.get("stationDataItems") or [])
        self.db.commit()

    # ------------------------------------------------------------ inverter data
    def fetch_device_history_day(self, day: date, job: str, between: tuple[int, int] | None = None) -> int:
        """All HISTORY_POINT_BATCHES for one day; `between` limits which frames are kept."""
        frames: dict[int, list[dict]] = {}
        for points in HISTORY_POINT_BATCHES:
            req = {"deviceSn": self.inverter_sn, "granularity": 1, "startAt": day.isoformat(),
                   "endAt": day.isoformat(), "measurePoints": points}
            resp = self.call(job, "/device/history", req, lambda: self.client.device_history(
                self.inverter_sn, 1, day.isoformat(), day.isoformat(), points))
            for entry in resp.get("dataList") or []:
                ts = int(entry["time"])
                if between and not between[0] < ts < between[1]:
                    continue
                frames.setdefault(ts, []).extend(entry.get("itemList") or [])
        new = 0
        for ts, items in sorted(frames.items()):
            new += store.store_device_frame(self.db, self.metrics, self.inverter_sn, ts, items, "history",
                                            merge_window=MERGE_WINDOW_S)
        self.db.commit()
        return new

    def fetch_settings(self, job: str) -> None:
        """Read-only snapshot of battery, work-mode and TOU settings (stored on change)."""
        for kind in ("battery", "system", "tou"):
            try:
                resp = self.call(job, f"/config/{kind}", {"deviceSn": self.inverter_sn},
                                 lambda: self.client.device_config(kind, self.inverter_sn))
            except DeyeApiError as exc:
                log.warning("settings %s not readable: %s", kind, exc)
                continue
            if store.store_device_settings(self.db, self.inverter_sn, kind, resp):
                log.info("settings %s changed", kind)
        self.db.commit()

    def poll_latest(self) -> int | None:
        req = {"deviceList": [self.inverter_sn]}
        resp = self.call("poll", "/device/latest", req, lambda: self.client.device_latest([self.inverter_sn]))
        for item in resp.get("deviceDataList") or []:
            if item.get("deviceSn") != self.inverter_sn or not item.get("collectionTime"):
                continue
            ts = int(item["collectionTime"])
            prev = self.db.execute("SELECT max(ts) FROM device_frame WHERE device_sn = ?",
                                   (self.inverter_sn,)).fetchone()[0]
            new = store.store_device_frame(self.db, self.metrics, self.inverter_sn, ts,
                                           item.get("dataList") or [], "latest")
            self.db.commit()
            log.info("inverter frame %s %s", datetime.fromtimestamp(ts, self.tz).isoformat(), "new" if new else "seen")
            if new and prev and ts - prev > 1.5 * LOGGER_STEP_S + 60:
                self.fill_device_gap(prev, ts)
            return ts
        return None

    def fill_device_gap(self, after: int, before: int) -> None:
        oldest = self.today() - timedelta(days=DEVICE_HISTORY_DAYS - 2)
        day = max(self.local_date(after), oldest)
        log.info("filling inverter gap %s .. %s", datetime.fromtimestamp(after, self.tz),
                 datetime.fromtimestamp(before, self.tz))
        while day <= self.local_date(before):
            self.fetch_device_history_day(day, "gap_fill", between=(after, before))
            day += timedelta(days=1)

    # ------------------------------------------------------------ jobs
    def backfill(self) -> None:
        today = self.today()
        start = self.start_date()
        log.info("backfill from %s", start)
        if self.inverter_sn:
            self.fetch_settings("backfill")

        # station frames: whole history, 30-day windows
        cursor = int(get_state(self.db, "backfill_station_ts") or
                     datetime.combine(start, datetime.min.time(), self.tz).timestamp())
        now = int(time.time())
        while cursor < now:
            end = min(now, cursor + STATION_WINDOW_DAYS * 86400)
            n = self.fetch_station_frames(cursor, end, "backfill")
            log.info("station frames %s .. %s: %d", self.local_date(cursor), self.local_date(end), n)
            cursor = end
            set_state(self.db, "backfill_station_ts", str(cursor))
            self.db.commit()

        # Deye's own totals
        self.fetch_daily_totals(start, today, "backfill")
        self.fetch_monthly_totals(start, today, "backfill")
        log.info("daily and monthly totals stored")

        # inverter points: only what Deye still keeps (~90 days), oldest first since it expires
        day = max(start, today - timedelta(days=DEVICE_HISTORY_DAYS))
        done = set(json.loads(get_state(self.db, "backfill_device_days", "[]")))
        while day < today:
            if day.isoformat() not in done:
                new = self.fetch_device_history_day(day, "backfill")
                log.info("inverter history %s: %d frames", day, new)
                done.add(day.isoformat())
                set_state(self.db, "backfill_device_days", json.dumps(sorted(done)))
                self.db.commit()
            day += timedelta(days=1)
        set_state(self.db, "backfill_done", str(int(time.time())))
        self.db.commit()
        log.info("backfill finished")

    def poll(self) -> None:
        self.poll_latest()
        last = self.db.execute("SELECT max(ts) FROM station_frame WHERE station_id = ?",
                               (self.station_id,)).fetchone()[0]
        now = int(time.time())
        start = (last - 3600) if last else now - 86400
        self.fetch_station_frames(start, now, "poll")
        self.daily_job()
        self.external_job()

    def external_job(self) -> None:
        """PVGIS and Open-Meteo (not Deye); a failure never stops the polling."""
        try:
            external.run(self.db, self.tz, self.start_date())
        except Exception:
            log.exception("external benchmarks failed; retrying next round")

    def daily_job(self) -> None:
        """Once a day after 01:00 local: Deye's totals for the last days (they settle late)."""
        local_now = datetime.now(self.tz)
        today = local_now.date()
        if local_now.hour < 1 or get_state(self.db, "daily_job_day") == today.isoformat():
            return
        self.fetch_daily_totals(today - timedelta(days=3), today - timedelta(days=1), "daily")
        self.fetch_monthly_totals(today.replace(day=1) - timedelta(days=1), today, "daily")
        if self.inverter_sn:
            self.fetch_settings("daily")
        set_state(self.db, "daily_job_day", today.isoformat())
        self.db.commit()

    def run(self) -> None:
        if not get_state(self.db, "backfill_done"):
            self.backfill()
        base = int(os.environ.get("POLL_INTERVAL_SECONDS", "360"))
        while True:
            try:
                self.poll()
            except Exception:
                log.exception("poll failed; retrying next round")
            # 5-7 minutes with a little jitter so polls don't lock onto the logger's phase
            time.sleep(max(300, base + random.randint(-30, 60)))


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only Deye collector")
    parser.add_argument("command", choices=["migrate", "backfill", "poll", "run", "check", "rebuild",
                                            "import-invoices", "external"])
    parser.add_argument("paths", nargs="*", help="import-invoices: PDF files or folders")
    parser.add_argument("--days", type=int, default=0, help="check: only the last N days")
    parser.add_argument("--force", action="store_true", help="external: refetch PVGIS and all weather")
    args = parser.parse_args()
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(message)s")

    if args.command == "check":
        from .check import main as check_main
        check_main(args.days)
        return
    if args.command == "import-invoices":
        from .invoices import derive_tariffs, import_paths
        load_env()
        conn = connect(os.environ.get("DB_PATH", "./data/pv.sqlite"))
        import_paths(conn, args.paths)
        print(f"tariff periods: {derive_tariffs(conn)}")
        return
    if args.command == "external":
        load_env()
        conn = connect(os.environ.get("DB_PATH", "./data/pv.sqlite"))
        tz = appconf.timezone(conn)
        row = conn.execute("SELECT min(start_operating_ts) FROM station").fetchone()
        first = datetime.fromtimestamp(row[0], tz).date() if row and row[0] else date.today() - timedelta(days=365)
        external.run(conn, tz, first, force=args.force)
        return
    if args.command == "migrate":
        load_env()
        connect(os.environ.get("DB_PATH", "./data/pv.sqlite"))
        return

    c = Collector()
    if args.command == "rebuild":
        for (sid,) in c.db.execute("SELECT id FROM station").fetchall():
            lo, hi = c.db.execute("SELECT min(ts), max(ts) FROM station_frame WHERE station_id = ?", (sid,)).fetchone()
            if lo:
                energy.rebuild(c.db, sid, lo, hi + 1, c.tz)
        c.db.commit()
        return
    c.discover()
    if args.command == "backfill":
        c.backfill()
    elif args.command == "poll":
        c.poll()
    elif args.command == "run":
        c.run()


if __name__ == "__main__":
    main()
