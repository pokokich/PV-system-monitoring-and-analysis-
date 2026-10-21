"""Import Electrohold household invoices (PDF) into grid_invoice tables.

    python3 -m collector.main import-invoices <folder or file> [...]
    python3 -m collector.main import-invoices seed/electrohold-invoices.json   # all invoices, already parsed

Needs `pdftotext` (poppler-utils). Amounts in BGN (invoices before 2026) are converted
to EUR at the fixed rate, so the database holds EUR only. Read from local files only.
"""
from __future__ import annotations

import json
import re
import sqlite3
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

BGN_PER_EUR = 1.95583
NUM = r"-?\d{1,3}(?: \d{3})*(?:,\d+)?"
NETWORK = {
    "Достъп до разпределителната мрежа": "distribution_access",
    "Достъп до електропреносната мрежа": "transmission_access",
    "Пренос през разпределителната мрежа НН": "distribution_lv",
    "Пренос през електропреносната мрежа": "transmission",
}


def _num(s: str) -> float:
    return float(s.replace(" ", "").replace(",", "."))


def _date(s: str) -> str:
    return datetime.strptime(s, "%d.%m.%Y").date().isoformat()


@dataclass
class Invoice:
    number: str
    issued: str
    currency: str
    periods: list[tuple[str, str]] = field(default_factory=list)   # reading sub-periods
    readings: list[dict] = field(default_factory=list)
    lines: list[dict] = field(default_factory=list)
    meter: str | None = None
    tax_base: float | None = None
    vat: float | None = None
    total: float | None = None

    def eur(self, value: float | None) -> float | None:
        if value is None:
            return None
        return value / BGN_PER_EUR if self.currency == "BGN" else value


def parse_text(text: str) -> Invoice:
    m = re.search(r"№\s*(\d{6,})\s*/\s*(\d\d\.\d\d\.\d{4})", text)
    if not m:
        raise ValueError("invoice number not found")
    currency = "EUR" if re.search(r"Сума\s*\(евро\)|\(евро/кВтч\)", text) else "BGN"
    inv = Invoice(number=m.group(1), issued=_date(m.group(2)), currency=currency)
    if mm := re.search(r"Електромер №\s*(\d+)", text):
        inv.meter = mm.group(1)

    period = None
    for raw in text.splitlines():
        line = re.sub(r"\s+", " ", raw).strip()
        if pm := re.match(r"Консумирана електрическа енергия от (\S+) до (\S+?)(?: г\.)?$", line):
            period = (_date(pm.group(1)), _date(pm.group(2)))
            inv.periods.append(period)
            continue
        if rm := re.match(rf"(Дневна|Нощна) ({NUM}) ({NUM}) ({NUM}) ({NUM}) ({NUM})$", line):
            a, b = _num(rm.group(2)), _num(rm.group(3))
            inv.readings.append({"period": period, "zone": "day" if rm.group(1) == "Дневна" else "night",
                                 "old": min(a, b), "new": max(a, b), "kwh": _num(rm.group(6))})
            continue
        if em := re.match(rf"Активна енергия (Дневна|Нощна) ({NUM}) ({NUM}) ({NUM})$", line):
            inv.lines.append({"kind": "energy", "zone": "day" if em.group(1) == "Дневна" else "night",
                              "qty": _num(em.group(2)), "price": _num(em.group(3)), "amount": _num(em.group(4))})
            continue
        if xm := re.match(rf"Акциз .* ({NUM}) ({NUM}) ({NUM})$", line):
            inv.lines.append({"kind": "excise", "zone": None, "qty": _num(xm.group(1)),
                              "price": _num(xm.group(2)), "amount": _num(xm.group(3))})
            continue
        for name, code in NETWORK.items():
            if line.startswith(name + " "):
                nm = re.match(rf"{re.escape(name)} ({NUM}) ({NUM}) ({NUM})$", line)
                if nm:
                    inv.lines.append({"kind": code, "zone": None, "qty": _num(nm.group(1)),
                                      "price": _num(nm.group(2)), "amount": _num(nm.group(3))})
        if tm := re.match(rf"Данъчна основа ({NUM})$", line):
            inv.tax_base = _num(tm.group(1))
        elif vm := re.match(rf"ДДС 20% ({NUM})$", line):
            inv.vat = _num(vm.group(1))
        elif om := re.match(rf"Обща стойност на сделката ({NUM})$", line):
            inv.total = _num(om.group(1))
    if not inv.readings or inv.total is None:
        raise ValueError(f"{inv.number}: readings or total not found")
    return inv


def pdf_text(path: Path) -> str:
    return subprocess.run(["pdftotext", "-layout", str(path), "-"], check=True,
                          capture_output=True, text=True).stdout


def store(conn: sqlite3.Connection, inv: Invoice, source: str) -> None:
    day = sum(r["kwh"] for r in inv.readings if r["zone"] == "day")
    night = sum(r["kwh"] for r in inv.readings if r["zone"] == "night")
    energy = sum(ln["amount"] for ln in inv.lines if ln["kind"] == "energy")
    network = sum(ln["amount"] for ln in inv.lines if ln["kind"] in NETWORK.values())
    conn.execute("DELETE FROM grid_invoice_reading WHERE invoice_no = ?", (inv.number,))
    conn.execute("DELETE FROM grid_invoice_line WHERE invoice_no = ?", (inv.number,))
    conn.execute(
        "INSERT OR REPLACE INTO grid_invoice VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (inv.number, inv.issued, inv.periods[0][0], inv.periods[-1][1], inv.meter, day, night,
         inv.eur(energy), inv.eur(network), inv.eur(inv.tax_base), inv.eur(inv.vat), inv.eur(inv.total), source),
    )
    conn.executemany(
        "INSERT INTO grid_invoice_reading VALUES (?, ?, ?, ?, ?, ?, ?)",
        [(inv.number, r["period"][0], r["period"][1], r["zone"], r["old"], r["new"], r["kwh"]) for r in inv.readings],
    )
    conn.executemany(
        "INSERT INTO grid_invoice_line (invoice_no, kind, zone, qty_kwh, unit_price_ccy, amount_ccy) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [(inv.number, ln["kind"], ln["zone"], ln["qty"], inv.eur(ln["price"]), inv.eur(ln["amount"]))
         for ln in inv.lines],
    )


def from_dict(d: dict) -> Invoice:
    """An invoice saved with dataclasses.asdict (seed/electrohold-invoices.json)."""
    keys = Invoice.__dataclass_fields__
    inv = Invoice(**{k: v for k, v in d.items() if k in keys})
    inv.periods = [tuple(x) for x in inv.periods]
    for r in inv.readings:
        r["period"] = tuple(r["period"])
    return inv


def _overlapping(conn: sqlite3.Connection, inv: Invoice) -> str | None:
    """Another invoice (e.g. typed in by hand) already covering part of this period."""
    row = conn.execute("SELECT invoice_no FROM grid_invoice WHERE invoice_no != ? AND period_from <= ? AND period_to >= ?",
                       (inv.number, inv.periods[-1][1], inv.periods[0][0])).fetchone()
    return row[0] if row else None


def import_paths(conn: sqlite3.Connection, paths: list[str]) -> list[str]:
    """PDFs (folders are searched) or JSON files with parsed invoices; idempotent by invoice number.
    An invoice whose period overlaps a different one already in the database is skipped."""
    done = []
    files = sorted({f for p in paths for f in (
        [*Path(p).rglob("*.pdf"), *Path(p).rglob("*.json")] if Path(p).is_dir() else [Path(p)])})
    for f in files:
        try:
            if f.suffix.lower() == ".json":
                items = [(from_dict(d), d.get("source") or f.name) for d in json.loads(f.read_text(encoding="utf-8"))]
            else:
                items = [(parse_text(pdf_text(f)), f.name)]
        except (ValueError, KeyError, TypeError, subprocess.CalledProcessError) as exc:
            print(f"  skipped {f.name}: {exc}")
            continue
        for inv, source in items:
            other = _overlapping(conn, inv)
            if other:
                print(f"  skipped {inv.number}: its period overlaps invoice {other} already in the database")
                continue
            store(conn, inv, source)
            done.append(inv.number)
            print(f"  {inv.number} {inv.periods[0][0]}..{inv.periods[-1][1]}  {inv.total:.2f} {inv.currency}")
    conn.commit()
    return done


WINDOWS = {
    "day": '{"summer": {"months": [4, 10], "hours": [["07:00", "23:00"]]}, '
           '"winter": {"months": [11, 3], "hours": [["06:00", "22:00"]]}}',
    "night": '{"summer": {"months": [4, 10], "hours": [["23:00", "07:00"]]}, '
             '"winter": {"months": [11, 3], "hours": [["22:00", "06:00"]]}}',
}


def derive_tariffs(conn: sqlite3.Connection) -> int:
    """Rebuild the Electrohold rows of `tariff` from the imported invoices.

    Only invoices imported from PDF carry prices (hand-entered ones from the dashboard
    have no lines and are skipped). Each reading sub-period gets the prices printed for it (an invoice is split at a
    price change, with lines in sub-period order); consecutive equal prices merge.
    """
    periods = []
    for inv in conn.execute("SELECT invoice_no FROM grid_invoice WHERE invoice_no IN "
                            "(SELECT invoice_no FROM grid_invoice_line WHERE kind = 'energy') ORDER BY period_from"):
        no = inv[0]
        subs = [tuple(r) for r in conn.execute(
            "SELECT DISTINCT period_from, period_to FROM grid_invoice_reading WHERE invoice_no = ? ORDER BY period_from",
            (no,))]
        lines = conn.execute("SELECT kind, zone, unit_price_ccy FROM grid_invoice_line WHERE invoice_no = ? ORDER BY id",
                             (no,)).fetchall()

        def nth(kind, zone, i):
            prices = [ln[2] for ln in lines if ln[0] == kind and (zone is None or ln[1] == zone)]
            return prices[min(i, len(prices) - 1)] if prices else 0.0

        for i, (p_from, p_to) in enumerate(subs):
            fees = {k: round(nth(k, None, i), 5) for k in NETWORK.values()}
            periods.append((p_from, round(nth("energy", "day", i), 5), round(nth("energy", "night", i), 5),
                            round(nth("excise", None, i), 5), fees))

    groups = []
    for p_from, day, night, excise, fees in periods:
        key = (day, night, excise, tuple(sorted(fees.items())))
        if groups and groups[-1]["key"] == key:
            continue
        groups.append({"key": key, "from": p_from, "day": day, "night": night, "excise": excise, "fees": fees})
    conn.execute("DELETE FROM tariff WHERE note LIKE 'Електрохолд%'")
    for i, g in enumerate(groups):
        valid_to = groups[i + 1]["from"] if i + 1 < len(groups) else None
        network = sum(g["fees"].values())
        for zone in ("day", "night"):
            fees = {"energy": g[zone], "excise": g["excise"], **g["fees"]}
            conn.execute(
                "INSERT INTO tariff (valid_from, valid_to, zone, price_per_kwh, vat_included, window_json, fees_json, note) "
                "VALUES (?, ?, ?, ?, 0, ?, ?, ?)",
                (g["from"], valid_to, zone, round(g[zone] + g["excise"] + network, 5), WINDOWS[zone],
                 json.dumps(fees), "Електрохолд, по фактурите"),
            )
    conn.commit()
    return len(groups)
