"""Guard: daily database backup and alerts when data stops coming.

    python3 -m collector.guard run      # continuous (Docker service `guard`): checks every 5 minutes
    python3 -m collector.guard check    # one check, prints the problems (exit code 1 if any)
    python3 -m collector.guard backup   # one backup now

Never talks to Deye. The checks (`assess`) are shared with the dashboard, which shows them in
its status line even when the guard itself is not running.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

from . import appconf, backup, notify, secrets
from .db import connect, get_state, set_state

log = logging.getLogger("guard")

CHECK_EVERY_S = 300


def env_num(key: str, default: float) -> float:
    try:
        return float(os.environ.get(key) or default)
    except ValueError:
        return default


def _state(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM collector_state WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def _ago(seconds: float) -> str:
    m = seconds / 60
    if m < 90:
        return f"{m:.0f} мин"
    if m < 48 * 60:
        return f"{m / 60:.1f} ч".replace(".", ",")
    return f"{m / 1440:.1f} дни".replace(".", ",")


def assess(conn: sqlite3.Connection, now: float | None = None, db_path: str | None = None) -> list[dict]:
    """Problems as [{code, level: warn|bad, text}]; empty list = all fine. Read-only."""
    now = now or time.time()
    stale_s = env_num("STALE_MINUTES", 45) * 60
    out = []

    last = conn.execute("SELECT max(ts) FROM station_frame").fetchone()[0]
    if last is None and not secrets.deye_ready():
        out.append({"code": "no_access", "level": "warn", "text": "Още няма достъп до Deye: довърши първоначалната настройка (/setup)"})
    elif last is None or now - last > stale_s:
        out.append({"code": "data_stale", "level": "bad",
                    "text": "Няма данни от колектора" if last is None else f"Няма нови данни от Deye от {_ago(now - last)}"})
    else:
        inv = conn.execute("SELECT max(ts) FROM device_frame").fetchone()[0]
        if inv is not None and now - inv > stale_s:
            out.append({"code": "inverter_stale", "level": "warn",
                        "text": f"Няма нови точки от инвертора от {_ago(now - inv)}"})

    row = conn.execute("SELECT sum(status = 'error'), sum(status = 'ok') FROM ingest_log WHERE ts > ?",
                       (now - 3600,)).fetchone()
    if row[0] and row[0] >= 3 and not row[1]:
        err = conn.execute("SELECT error FROM ingest_log WHERE status = 'error' ORDER BY ts DESC LIMIT 1").fetchone()[0]
        out.append({"code": "deye_errors", "level": "bad",
                    "text": f"Deye връща само грешки през последния час ({row[0]}): {(err or '')[:120]}"})

    b_err = _state(conn, "backup_last_error")
    b_ts = _state(conn, "backup_last_ts")
    max_age = env_num("BACKUP_MAX_AGE_HOURS", 30) * 3600
    if b_err:
        out.append({"code": "backup_failed", "level": "bad", "text": f"Архивът не успя: {b_err[:160]}"})
    elif b_ts is None:
        out.append({"code": "backup_missing", "level": "warn", "text": "Още няма архив на базата"})
    elif now - float(b_ts) > max_age:
        out.append({"code": "backup_old", "level": "warn", "text": f"Последният архив е отпреди {_ago(now - float(b_ts))}"})

    if db_path:
        try:
            du = shutil.disk_usage(Path(db_path).resolve().parent)
            if du.free < 2e9 or du.free / du.total < 0.05:
                out.append({"code": "disk_low", "level": "warn",
                            "text": f"Мястото на диска свършва: {du.free / 1e9:.1f} GB свободни".replace(".", ",")})
        except OSError:
            pass
    return out


def guard_heartbeat_problem(conn: sqlite3.Connection, now: float | None = None) -> dict | None:
    """For the dashboard: the guard writes guard_status every 5 minutes; silence means it is not running."""
    now = now or time.time()
    raw = _state(conn, "guard_status")
    ts = json.loads(raw).get("ts") if raw else None
    if ts is None or now - ts > 20 * 60:
        return {"code": "guard_down", "level": "warn",
                "text": "Наблюдението и архивът не работят (услугата guard)" if ts is None
                else f"Наблюдението не е проверявало от {_ago(now - ts)} (услугата guard)"}
    return None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None   # a 3xx answer is returned as is (HTTPError below 500 = the address works)


class Guard:
    def __init__(self):
        self.db_path = os.environ.get("DB_PATH", "./data/pv.sqlite")
        self.conn = connect(self.db_path)
        self.tz = appconf.timezone(self.conn)
        self.dest = backup.backup_dir()
        self.web_url = os.environ.get("WEB_HEALTH_URL")   # e.g. http://web:8088/healthz in Docker
        self.tunnel_url = os.environ.get("TUNNEL_PUBLIC_URL")      # e.g. https://pv.example.com
        self.tunnel_ready_url = os.environ.get("TUNNEL_READY_URL")  # cloudflared's /ready in Docker

    def backup_due(self, now: float) -> bool:
        last = get_state(self.conn, "backup_last_ts")
        if last is None or now - float(last) > 24 * 3600:
            return True
        local = datetime.fromtimestamp(now, self.tz)
        last_day = datetime.fromtimestamp(float(last), self.tz).date()
        return local.hour >= int(env_num("BACKUP_HOUR", 3)) and last_day < local.date()

    def do_backup(self) -> Path | None:
        now = time.time()
        try:
            path = backup.make_backup(self.conn, self.dest, datetime.fromtimestamp(now, self.tz).date())
            gone = backup.rotate(self.dest)
        except Exception as exc:
            log.exception("backup failed")
            set_state(self.conn, "backup_last_error", f"{type(exc).__name__}: {exc}")
            set_state(self.conn, "backup_last_try_ts", str(int(now)))
            self.conn.commit()
            return None
        for key, value in (("backup_last_ts", str(int(now))), ("backup_last_file", path.name),
                           ("backup_last_bytes", str(path.stat().st_size)), ("backup_last_error", None)):
            if value is None:
                self.conn.execute("DELETE FROM collector_state WHERE key = ?", (key,))
            else:
                set_state(self.conn, key, value)
        self.conn.commit()
        log.info("backup %s (%.1f MB), removed %d old", path, path.stat().st_size / 1e6, len(gone))
        return path

    def web_problem(self, attempts: int = 4, pause_s: float = 15) -> dict | None:
        """The site counts as down only after a minute of failed tries (it may still be starting after `up`)."""
        if not self.web_url:
            return None
        for attempt in range(attempts):
            try:
                urllib.request.urlopen(self.web_url, timeout=15).read()
                return None
            except Exception as exc:
                error = exc
                if attempt < attempts - 1:
                    time.sleep(pause_s)
        return {"code": "web_down", "level": "bad", "text": f"Сайтът не отговаря: {str(error)[:120]}"}

    def tunnel_problem(self, attempts: int = 3, pause_s: float = 15) -> dict | None:
        """Only with TUNNEL_PUBLIC_URL set: the tunnel (cloudflared or Tailscale) is connected and the public address answers.

        Any answer below 500 counts (a redirect to the Cloudflare Access login is fine); Cloudflare
        answers 502/530 when the tunnel or the site behind it is down.
        """
        if not self.tunnel_url:
            return None
        opener = urllib.request.build_opener(_NoRedirect)
        for attempt in range(attempts):
            try:
                if self.tunnel_ready_url:
                    try:
                        urllib.request.urlopen(self.tunnel_ready_url, timeout=10).read()
                    except Exception as exc:
                        raise RuntimeError(f"тунелът не е свързан ({str(exc)[:80]})") from exc
                req = urllib.request.Request(self.tunnel_url, headers={"User-Agent": "deyehome-guard"})
                try:
                    opener.open(req, timeout=20).read()
                except urllib.error.HTTPError as exc:
                    if exc.code >= 500:
                        raise RuntimeError(f"{self.tunnel_url} връща HTTP {exc.code}") from exc
                return None
            except Exception as exc:
                error = exc
                if attempt < attempts - 1:
                    time.sleep(pause_s)
        return {"code": "tunnel_down", "level": "warn", "text": f"Адресът отвън не работи: {str(error)[:140]}"}

    def check(self) -> list[dict]:
        now = time.time()
        if self.backup_due(now):
            # after a failure retry once an hour, not every 5 minutes
            tried = get_state(self.conn, "backup_last_try_ts")
            if not get_state(self.conn, "backup_last_error") or not tried or now - float(tried) > 3600:
                self.do_backup()
        problems = assess(self.conn, now, self.db_path)
        web = self.web_problem()
        if web:
            problems.append(web)
        tunnel = self.tunnel_problem()
        if tunnel:
            problems.append(tunnel)
        self.alert(problems, now)
        return problems

    def alert(self, problems: list[dict], now: float) -> None:
        """Alert on change (new problem or all fine again) and repeat every ALERT_REPEAT_HOURS while it lasts."""
        prev = json.loads(get_state(self.conn, "guard_status") or "{}")
        key = sorted(p["code"] for p in problems)
        repeat = env_num("ALERT_REPEAT_HOURS", 12) * 3600
        sent_ts = prev.get("notified_ts") or 0
        changed = key != prev.get("notified_key", [])
        if changed or (problems and now - sent_ts > repeat):
            if problems:
                title = "PV система: проблем"
                text = "\n".join(f"• {p['text']}" for p in problems)
            else:
                title, text = "PV система: всичко е наред отново", "Данните идват и архивът е актуален."
            # the first check after a fresh start does not announce "all fine"
            if problems or prev.get("notified_key"):
                result = notify.send(title, text)
            else:
                result = {}
            prev.update(notified_key=key, notified_ts=now, notified_result=result)
        prev.update(ts=now, problems=problems, channels=notify.configured())
        set_state(self.conn, "guard_status", json.dumps(prev, ensure_ascii=False))
        self.conn.commit()

    def run(self, stop: threading.Event | None = None) -> None:
        stop = stop or threading.Event()
        log.info("guard: backups to %s, alert channels: %s", self.dest, ", ".join(notify.configured()) or "none")
        while not stop.is_set():
            try:
                problems = self.check()
                if problems:
                    log.warning("problems: %s", "; ".join(p["text"] for p in problems))
            except Exception:
                log.exception("guard check failed")
            stop.wait(CHECK_EVERY_S)


def main() -> None:
    parser = argparse.ArgumentParser(description="Backups and alerts for the PV collector")
    parser.add_argument("command", choices=["run", "check", "backup"])
    args = parser.parse_args()
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
    from .deye_client import load_env   # only the .env loader; the guard never calls Deye
    load_env()
    g = Guard()
    if args.command == "backup":
        raise SystemExit(0 if g.do_backup() else 1)
    if args.command == "check":
        problems = assess(g.conn, db_path=g.db_path)
        for p in problems:
            print(f"{p['level']}: {p['text']}")
        raise SystemExit(1 if problems else 0)
    g.run()


if __name__ == "__main__":
    main()
