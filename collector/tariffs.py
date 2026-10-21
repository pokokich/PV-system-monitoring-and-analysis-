"""Tariff zones (local time), shared by the checks and the dashboard.

The night (low-rate) hours come from the setting `tariff_night_windows`: a list of
{"months": [from, to], "start": "HH:MM", "end": "HH:MM"} (months may wrap, 11..3 = November
to March; start after end = over midnight). An empty list means a single-rate tariff: every
hour is "day". Zone boundaries are whole local hours.
"""
from __future__ import annotations

from datetime import datetime


def _in_months(month: int, months: list[int]) -> bool:
    a, b = months
    return a <= month <= b if a <= b else month >= a or month <= b


def _hour(hhmm: str) -> int:
    return int(str(hhmm).split(":")[0]) % 24


def zone(local: datetime, windows: list[dict] | None = None) -> str:
    """'night' if the local hour falls in a night window of its month, else 'day'."""
    for w in windows or []:
        if not _in_months(local.month, w.get("months") or [1, 12]):
            continue
        start, end = _hour(w.get("start", "0")), _hour(w.get("end", "0"))
        if start == end:
            continue
        inside = start <= local.hour < end if start < end else local.hour >= start or local.hour < end
        if inside:
            return "night"
    return "day"


# Presets the setup offers; owners can enter any other hours.
PRESETS = {
    "single": [],
    "night_22_06": [{"months": [1, 12], "start": "22:00", "end": "06:00"}],
    "night_23_07": [{"months": [1, 12], "start": "23:00", "end": "07:00"}],
    # Bulgaria (Electrohold, EVN, Energo-Pro): 23-07 April-October, 22-06 November-March
    "bg_seasonal": [{"months": [4, 10], "start": "23:00", "end": "07:00"},
                    {"months": [11, 3], "start": "22:00", "end": "06:00"}],
}
