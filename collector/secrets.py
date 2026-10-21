"""Credentials kept outside the database: the Deye access and the AI provider's key.

An environment variable (or .env) always wins. Otherwise the value comes from secrets.json next
to the database (data/secrets.json), which the first-run setup and the Settings page write.
The file is readable by its owner only, is never sent back to a browser and is not part of the
database backups.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

DEYE_KEYS = ("DEYE_EMAIL", "DEYE_PASSWORD", "DEYE_APP_ID", "DEYE_APP_SECRET", "DEYE_BASE_URL")
LLM_KEYS = ("LLM_API_KEY",)
KNOWN = set(DEYE_KEYS) | set(LLM_KEYS)


def path() -> Path:
    if os.environ.get("SECRETS_PATH"):
        return Path(os.environ["SECRETS_PATH"])
    return Path(os.environ.get("DB_PATH", "./data/pv.sqlite")).resolve().parent / "secrets.json"


def _load() -> dict[str, str]:
    try:
        out = json.loads(path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: str(v) for k, v in out.items() if isinstance(k, str) and v not in (None, "")} if isinstance(out, dict) else {}


def get(key: str, default: str | None = None) -> str | None:
    return os.environ.get(key) or _load().get(key) or default


def from_env(key: str) -> bool:
    """True when the value is pinned by the environment, so the web page cannot change it."""
    return bool(os.environ.get(key))


def update(values: dict[str, str | None]) -> None:
    """Set (or with None/'' remove) values; written atomically with mode 600."""
    unknown = set(values) - KNOWN
    if unknown:
        raise KeyError(f"unknown secret: {', '.join(sorted(unknown))}")
    cur = _load()
    for k, v in values.items():
        if v in (None, ""):
            cur.pop(k, None)
        else:
            cur[k] = str(v)
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=".secrets-")
    try:
        os.chmod(tmp, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(cur, f, indent=1)
        os.replace(tmp, p)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def deye_ready() -> bool:
    return all(get(k) for k in ("DEYE_EMAIL", "DEYE_PASSWORD", "DEYE_APP_ID", "DEYE_APP_SECRET"))
