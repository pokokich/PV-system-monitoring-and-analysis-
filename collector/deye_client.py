"""Read-only client for the Deye Cloud Open API.

Only endpoints in READ_ONLY_ENDPOINTS can be called. Anything else, in particular
the /order/* and /strategy/* control endpoints, is rejected before any network
request is made. The app must never change inverter or battery settings.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

READ_ONLY_ENDPOINTS = frozenset({
    "/account/token",
    "/account/info",
    "/station/list",
    "/station/listWithDevice",
    "/station/device",
    "/station/detail",
    "/station/latest",
    "/station/history",
    "/station/history/power",
    "/station/alertList",
    "/device/list",
    "/device/latest",
    "/device/measurePoints",
    "/device/history",
    "/device/alertList",
    # reading the current settings (snapshot for the event log); /config/update stays forbidden
    "/config/battery",
    "/config/system",
    "/config/tou",
})

FORBIDDEN_PREFIXES = ("/order", "/strategy", "/device/register", "/config/update")


class ForbiddenEndpointError(RuntimeError):
    pass


class DeyeApiError(RuntimeError):
    pass


def check_endpoint(path: str) -> None:
    if path.startswith(FORBIDDEN_PREFIXES) or path not in READ_ONLY_ENDPOINTS:
        raise ForbiddenEndpointError(f"endpoint not allowed (read-only client): {path}")


def load_env(path: str | Path = ".env") -> None:
    """Minimal .env loader; existing environment variables win."""
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":   # KEY="a b" as Docker reads it
            value = value[1:-1]
        os.environ.setdefault(key.strip(), value)


@dataclass
class DeyeConfig:
    base_url: str
    app_id: str
    app_secret: str
    email: str
    password: str

    @classmethod
    def from_env(cls) -> "DeyeConfig":
        """From the environment, else from data/secrets.json (collector/secrets.py)."""
        from . import secrets

        missing = [k for k in ("DEYE_APP_ID", "DEYE_APP_SECRET", "DEYE_EMAIL", "DEYE_PASSWORD") if not secrets.get(k)]
        if missing:
            raise KeyError(", ".join(missing))
        return cls(
            base_url=(secrets.get("DEYE_BASE_URL") or "https://eu1-developer.deyecloud.com/v1.0").rstrip("/"),
            app_id=secrets.get("DEYE_APP_ID"),
            app_secret=secrets.get("DEYE_APP_SECRET"),
            email=secrets.get("DEYE_EMAIL"),
            password=secrets.get("DEYE_PASSWORD"),
        )


class DeyeClient:
    def __init__(self, config: DeyeConfig, timeout: float = 30.0, min_interval: float | None = None):
        self.config = config
        self.timeout = timeout
        # Deye answers HTTP 500 to bursts, so keep a gap between consecutive calls
        self.min_interval = float(os.environ.get("DEYE_MIN_INTERVAL", "1.5")) if min_interval is None else min_interval
        self._last_call = 0.0
        self._token: str | None = None
        self._token_expires_at = 0.0

    def _post(self, path: str, body: dict, query: str = "", auth: bool = True) -> dict:
        check_endpoint(path)
        headers = {"Content-Type": "application/json"}
        if auth:
            headers["Authorization"] = f"bearer {self._get_token()}"
        req = urllib.request.Request(
            f"{self.config.base_url}{path}{query}",
            data=json.dumps(body).encode(),
            headers=headers,
            method="POST",
        )
        payload = self._send(req)
        if not payload.get("success", False) and str(payload.get("code")) != "1000000":
            raise DeyeApiError(f"{path}: code={payload.get('code')} msg={payload.get('msg')}")
        return payload

    def _send(self, req: urllib.request.Request, attempts: int = 4) -> dict:
        """Send with backoff on HTTP 429/5xx; Deye answers 500 when calls come too fast."""
        for attempt in range(attempts):
            wait = self._last_call + self.min_interval - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._last_call = time.monotonic()
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    return json.loads(resp.read().decode())
            except urllib.error.HTTPError as exc:
                if exc.code != 429 and exc.code < 500 or attempt == attempts - 1:
                    raise
                time.sleep(2 ** (attempt + 1))
        raise AssertionError("unreachable")

    def _get_token(self) -> str:
        if self._token and time.time() < self._token_expires_at - 3600:
            return self._token
        body = {
            "appSecret": self.config.app_secret,
            "email": self.config.email,
            "password": hashlib.sha256(self.config.password.encode()).hexdigest(),
        }
        payload = self._post("/account/token", body, query=f"?appId={self.config.app_id}", auth=False)
        self._token = payload["accessToken"]
        self._token_expires_at = time.time() + float(payload.get("expiresIn", 3600))
        return self._token

    # --- read-only API -------------------------------------------------
    def account_info(self) -> dict:
        return self._post("/account/info", {})

    def station_list(self, page: int = 1, size: int = 20) -> dict:
        return self._post("/station/list", {"page": page, "size": size})

    def station_devices(self, station_ids: list[int], page: int = 1, size: int = 20) -> dict:
        return self._post("/station/device", {"page": page, "size": size, "stationIds": station_ids})

    def station_latest(self, station_id: int) -> dict:
        return self._post("/station/latest", {"stationId": station_id})

    def station_history(self, station_id: int, granularity: int, start_at: str, end_at: str | None = None) -> dict:
        body = {"stationId": station_id, "granularity": granularity, "startAt": start_at}
        if end_at:
            body["endAt"] = end_at
        return self._post("/station/history", body)

    def station_history_power(self, station_id: int, start_ts: int, end_ts: int) -> dict:
        """5-minute station frames between two unix timestamps (tested up to 62 days per call)."""
        return self._post("/station/history/power",
                          {"stationId": station_id, "startTimestamp": int(start_ts), "endTimestamp": int(end_ts)})

    def device_latest(self, device_sns: list[str]) -> dict:
        if len(device_sns) > 10:
            raise ValueError("Deye allows at most 10 devices per /device/latest call")
        return self._post("/device/latest", {"deviceList": device_sns})

    def device_config(self, kind: str, device_sn: str) -> dict:
        """Read current settings: kind is battery, system or tou. Read-only."""
        if kind not in ("battery", "system", "tou"):
            raise ValueError(kind)
        return self._post(f"/config/{kind}", {"deviceSn": device_sn})

    def device_measure_points(self, device_sn: str) -> dict:
        return self._post("/device/measurePoints", {"deviceSn": device_sn})

    def device_history(self, device_sn: str, granularity: int, start_at: str,
                       end_at: str | None = None, measure_points: list[str] | None = None) -> dict:
        body = {"deviceSn": device_sn, "granularity": granularity, "startAt": start_at}
        if end_at:
            body["endAt"] = end_at
        if measure_points:
            body["measurePoints"] = measure_points
        return self._post("/device/history", body)
