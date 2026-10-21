"""One-off discovery run: checks access and saves what the account exposes.

Usage (from the repo root, with .env filled in):
    python3 -m collector.probe

Writes JSON files to ./data/probe/ (gitignored). Read-only: no settings are changed.
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

from .deye_client import DeyeApiError, DeyeClient, DeyeConfig, load_env

OUT = Path("data/probe")
# /device/history with granularity 1 accepts at most 5 measure points per call
FRAME_POINTS = ["TotalSolarPower", "DCPowerPV1", "DCPowerPV2", "BatteryPower", "SOC"]


def save(name: str, payload: dict) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{name}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  saved {OUT / (name + '.json')}")


def main() -> None:
    load_env()
    client = DeyeClient(DeyeConfig.from_env())

    print("1. Token and account")
    save("account_info", client.account_info())

    print("2. Stations")
    stations = client.station_list()
    save("station_list", stations)
    station_ids = [s["id"] for s in stations.get("stationList", [])]
    if not station_ids:
        print("  no stations found; check the account")
        return

    print("3. Devices")
    devices = client.station_devices(station_ids)
    save("station_devices", devices)
    device_sns = [d["deviceSn"] for d in devices.get("deviceListItems", []) if d.get("deviceSn")]

    yesterday = (date.today() - timedelta(days=1)).isoformat()
    month_start = (date.today() - timedelta(days=1)).replace(day=1).isoformat()
    for sid in station_ids:
        print(f"4. Station {sid}: latest, yesterday's 5-min frame, daily and monthly totals")
        save(f"station_{sid}_latest", client.station_latest(sid))
        # granularity 1 (frame) needs endAt as well, otherwise Deye answers "invalid param"
        save(f"station_{sid}_frame_{yesterday}", client.station_history(sid, 1, yesterday, yesterday))
        save(f"station_{sid}_daily_2025-12", client.station_history(sid, 2, "2025-12-01", "2025-12-31"))
        save(f"station_{sid}_monthly", client.station_history(sid, 3, "2025-12", date.today().strftime("%Y-%m")))

    if device_sns:
        print("5. Devices: latest values and measure points")
        save("device_latest", client.device_latest(device_sns[:10]))
        for sn in device_sns:
            # the data logger (COLLECTOR) answers "device not supported"; keep going
            for name, call in (
                ("measure_points", lambda: client.device_measure_points(sn)),
                (f"frame_{yesterday}", lambda: client.device_history(sn, 1, yesterday, yesterday, FRAME_POINTS)),
                (f"daily_{month_start}", lambda: client.device_history(sn, 2, month_start, yesterday)),
            ):
                try:
                    save(f"device_{sn}_{name}", call())
                except DeyeApiError as exc:
                    print(f"  {name} for {sn} not available: {exc}")

    print("Done. Send me the data/probe folder (or paste the measure points) so I can map the fields.")


if __name__ == "__main__":
    main()
