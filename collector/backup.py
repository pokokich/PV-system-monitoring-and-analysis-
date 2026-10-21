"""Consistent daily copies of the SQLite database, with rotation.

VACUUM INTO makes a consistent copy while the collector and the dashboard keep running
(copying the live file with cp could catch a half-written WAL). The copy is checked with
PRAGMA quick_check, gzipped into BACKUP_DIR as pv-YYYY-MM-DD.sqlite.gz and old copies are
thinned out: the newest 7 days, 8 Sundays and 12 first-of-month copies stay.
"""
from __future__ import annotations

import gzip
import os
import re
import shutil
import sqlite3
import tempfile
import time
from datetime import date
from pathlib import Path

NAME = re.compile(r"^pv-(\d{4}-\d{2}-\d{2})\.sqlite\.gz$")
KEEP_DAILY, KEEP_WEEKLY, KEEP_MONTHLY = 7, 8, 12


def backup_dir() -> Path:
    """Where copies go inside this process. Docker Compose mounts the host folder BACKUP_DIR at /backups
    and sets BACKUP_PATH=/backups; without Docker BACKUP_PATH is the folder itself."""
    return Path(os.environ.get("BACKUP_PATH") or "./backups")


def make_backup(conn: sqlite3.Connection, dest_dir: Path, day: date) -> Path:
    """Copy the database behind `conn` to dest_dir/pv-<day>.sqlite.gz and return the path."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    final = dest_dir / f"pv-{day.isoformat()}.sqlite.gz"
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / "pv.sqlite"
        conn.commit()
        conn.execute("VACUUM INTO ?", (str(copy),))
        check = sqlite3.connect(str(copy))
        try:
            result = check.execute("PRAGMA quick_check").fetchone()[0]
        finally:
            check.close()
        if result != "ok":
            raise RuntimeError(f"the copy failed quick_check: {result}")
        part = final.with_name(final.name + ".part")
        with open(copy, "rb") as src, gzip.open(part, "wb", compresslevel=6) as dst:
            shutil.copyfileobj(src, dst, 1024 * 1024)
        os.replace(part, final)
    return final


def to_keep(days: list[date]) -> set[date]:
    newest = sorted(set(days), reverse=True)
    keep = set(newest[:KEEP_DAILY])
    keep |= set([d for d in newest if d.weekday() == 6][:KEEP_WEEKLY])
    keep |= set([d for d in newest if d.day == 1][:KEEP_MONTHLY])
    return keep


def rotate(dest_dir: Path) -> list[Path]:
    """Delete old copies (only files named pv-YYYY-MM-DD.sqlite.gz); returns what was deleted."""
    files = {}
    for p in dest_dir.glob("pv-*.sqlite.gz"):
        m = NAME.match(p.name)
        if m:
            files[date.fromisoformat(m.group(1))] = p
    keep = to_keep(list(files))
    gone = []
    for d, p in files.items():
        if d not in keep:
            p.unlink()
            gone.append(p)
    return gone


def latest(dest_dir: Path) -> tuple[Path, float] | None:
    files = [p for p in dest_dir.glob("pv-*.sqlite.gz") if NAME.match(p.name)]
    if not files:
        return None
    p = max(files, key=lambda f: f.name)
    return p, p.stat().st_mtime


def pre_migration_copy(conn: sqlite3.Connection, version: int) -> Path | None:
    """Plain copy next to the database before a migration runs on it (fresh databases are skipped)."""
    row = conn.execute("PRAGMA database_list").fetchone()
    if not row or not row[2]:
        return None
    path = Path(row[2])
    dest = path.with_name(f"{path.stem}.pre-{version:03d}-{int(time.time())}.sqlite")
    conn.commit()
    conn.execute("VACUUM INTO ?", (str(dest),))
    for old in sorted(path.parent.glob(f"{path.stem}.pre-*.sqlite"))[:-3]:   # keep the last three
        old.unlink()
    return dest
