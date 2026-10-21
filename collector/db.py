"""SQLite storage: migrations and idempotent writes for the collector."""
from __future__ import annotations

import json
import logging
import sqlite3
import time
import zlib
from pathlib import Path

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


def connect(path: str | Path) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    migrate(conn)
    return conn


def migrate(conn: sqlite3.Connection) -> None:
    """Apply migrations/NNN_*.sql in order, once each."""
    conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY, name TEXT, applied_at INTEGER)")
    done = {r[0] for r in conn.execute("SELECT version FROM schema_version")}
    pending = [f for f in sorted(MIGRATIONS.glob("[0-9][0-9][0-9]_*.sql")) if int(f.name[:3]) not in done]
    if pending and done:
        # an existing database gets a plain copy next to it before its schema changes
        from .backup import pre_migration_copy
        try:
            pre_migration_copy(conn, int(pending[0].name[:3]))
        except sqlite3.Error as exc:
            logging.getLogger("db").warning("copy before migration failed: %s", exc)
    for sql_file in pending:
        version = int(sql_file.name[:3])
        conn.commit()
        try:
            conn.executescript(
                "BEGIN;\n" + sql_file.read_text(encoding="utf-8")
                + f"\n;INSERT INTO schema_version VALUES ({version}, '{sql_file.name}', {int(time.time())});\nCOMMIT;"
            )
        except sqlite3.Error:
            conn.rollback()
            raise


def get_state(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM collector_state WHERE key = ?", (key,)).fetchone()
    return row[0] if row else default


def set_state(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute("INSERT INTO collector_state VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                 (key, value))


def save_raw(conn: sqlite3.Connection, endpoint: str, request: dict, response: dict) -> None:
    conn.execute(
        "INSERT INTO raw_payload (fetched_at, endpoint, request_json, response_gz) VALUES (?, ?, ?, ?)",
        (int(time.time()), endpoint, json.dumps(request, ensure_ascii=False),
         zlib.compress(json.dumps(response, ensure_ascii=False).encode())),
    )


def log_ingest(conn: sqlite3.Connection, job: str, endpoint: str | None, status: str,
               points: int | None = None, duration_ms: int | None = None, error: str | None = None) -> None:
    conn.execute(
        "INSERT INTO ingest_log (ts, job, endpoint, status, points, duration_ms, error) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (int(time.time()), job, endpoint, status, points, duration_ms, error),
    )


class MetricCache:
    """Maps Deye point keys to metric ids, creating rows on first sight."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self._ids = {r["key"]: r["id"] for r in conn.execute("SELECT id, key FROM metric")}

    def id(self, key: str, unit: str | None) -> int:
        if key not in self._ids:
            cur = self.conn.execute("INSERT INTO metric (key, unit, first_seen) VALUES (?, ?, ?)",
                                    (key, unit, int(time.time())))
            self._ids[key] = cur.lastrowid
        return self._ids[key]
