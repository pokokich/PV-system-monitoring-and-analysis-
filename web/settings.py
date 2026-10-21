"""The only place the dashboard writes: goals, scenarios, benchmarks and the manual parameters
(param, arrays, battery, tariffs, CAPEX/OPEX) and electricity invoices of the Settings page.

The connection has an SQLite authorizer that denies every write outside those tables,
so energy data, frames and the inverter's settings stay read-only for the dashboard.
Nothing here talks to Deye.
"""
from __future__ import annotations

import sqlite3
import time

from . import data

WRITABLE = {"goal", "goal_status", "benchmark", "scenario", "param",
            "pv_array", "battery_config", "tariff", "feed_in_price", "capex_item", "opex_item",
            "grid_invoice", "grid_invoice_reading", "grid_invoice_line"}
PARAM_PREFIXES = ("goal_", "health_")

_WRITE_ACTIONS = {sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE}
_DDL = {getattr(sqlite3, n) for n in dir(sqlite3) if n.startswith(("SQLITE_CREATE_", "SQLITE_DROP_"))} | {
    sqlite3.SQLITE_ALTER_TABLE, sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH, sqlite3.SQLITE_PRAGMA}


def _authorizer(action, arg1, arg2, dbname, source):
    if action in _DDL:
        # read-only pragmas the sqlite3 module issues itself are harmless; everything else is refused
        return sqlite3.SQLITE_DENY if action != sqlite3.SQLITE_PRAGMA or arg2 is not None else sqlite3.SQLITE_OK
    if action in _WRITE_ACTIONS and arg1 not in WRITABLE:
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


def connect_rw() -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{data.DB_PATH}?mode=rw", uri=True, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.set_authorizer(_authorizer)
    return conn


def write(fn):
    """Run fn(conn) in one transaction and invalidate the cached model."""
    conn = connect_rw()
    try:
        with conn:
            out = fn(conn)
    finally:
        conn.close()
    data.invalidate()
    return out


def now() -> int:
    return int(time.time())
