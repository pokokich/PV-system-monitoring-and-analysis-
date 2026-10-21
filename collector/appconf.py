"""Settings of the installation shared by the collector, the guard and the dashboard.

They live in the `app_setting` table (migration 002), written by the first-run setup and the
Settings page. The time zone falls back to the TZ environment variable, then to the
computer's own zone, then to UTC.
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import tzinfo
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def get(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    try:
        row = conn.execute("SELECT value FROM app_setting WHERE key = ?", (key,)).fetchone()
    except sqlite3.OperationalError:  # database from before migration 002
        return default
    return row[0] if row and row[0] not in (None, "") else default


def all_settings(conn: sqlite3.Connection) -> dict[str, str | None]:
    try:
        return {r[0]: r[1] for r in conn.execute("SELECT key, value FROM app_setting")}
    except sqlite3.OperationalError:
        return {}


def _zone(name: str | None) -> ZoneInfo | None:
    if not name:
        return None
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return None


def system_zone_name() -> str | None:
    """IANA name of the computer's zone (/etc/localtime on Linux and macOS), if it can be told."""
    try:
        target = os.path.realpath("/etc/localtime")
        if "zoneinfo/" in target:
            return target.split("zoneinfo/", 1)[1]
    except OSError:
        pass
    try:
        name = Path("/etc/timezone").read_text().strip()
        return name or None
    except OSError:
        return None


def timezone(conn: sqlite3.Connection | None = None) -> ZoneInfo:
    for name in ((get(conn, "timezone") if conn is not None else None), os.environ.get("TZ"), system_zone_name()):
        z = _zone(name)
        if z is not None:
            return z
    return ZoneInfo("UTC")


def timezone_from_db(path: str) -> ZoneInfo:
    """The zone for a database file that may not exist yet (read-only, never creates it)."""
    if Path(path).exists():
        try:
            conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
            try:
                return timezone(conn)
            finally:
                conn.close()
        except sqlite3.Error:
            pass
    return timezone(None)


def night_windows(conn: sqlite3.Connection) -> list[dict]:
    try:
        out = json.loads(get(conn, "tariff_night_windows", "[]") or "[]")
    except ValueError:
        return []
    return out if isinstance(out, list) else []


class DynamicZone(tzinfo):
    """A tzinfo that follows the installation's current zone, so modules can import one object
    and still see a zone changed on the Settings page (the dashboard calls set() on each rebuild)."""

    def __init__(self, zone: ZoneInfo | None = None):
        self.zone = zone or timezone(None)

    def set(self, zone: ZoneInfo) -> None:
        self.zone = zone

    @property
    def key(self) -> str:
        return getattr(self.zone, "key", "UTC")

    def utcoffset(self, dt):
        return self.zone.utcoffset(dt.replace(tzinfo=None) if dt is not None else None)

    def dst(self, dt):
        return self.zone.dst(dt.replace(tzinfo=None) if dt is not None else None)

    def tzname(self, dt):
        return self.zone.tzname(dt.replace(tzinfo=None) if dt is not None else None)

    def fromutc(self, dt):
        return self.zone.fromutc(dt.replace(tzinfo=self.zone)).replace(tzinfo=self)

    def __repr__(self) -> str:
        return f"DynamicZone({self.key})"


# Symbols for the most common currencies of Deye owners; any other ISO code is shown as is.
CURRENCY_SYMBOLS = {
    "EUR": "€", "BGN": "лв", "PLN": "zł", "RON": "lei", "CZK": "Kč", "HUF": "Ft", "GBP": "£", "USD": "$",
    "CHF": "CHF", "SEK": "kr", "NOK": "kr", "DKK": "kr", "UAH": "₴", "TRY": "₺", "ZAR": "R", "AUD": "A$",
    "INR": "₹", "BRL": "R$", "MXN": "MX$", "RSD": "дин", "MKD": "ден", "BAM": "KM", "ILS": "₪", "PKR": "₨",
}


def currency_symbol(code: str | None) -> str:
    code = (code or "EUR").upper()
    return CURRENCY_SYMBOLS.get(code, code)
