import tempfile
import unittest
from pathlib import Path

from collector import invoices
from collector.db import connect

# Layout of a BGN invoice split at a price change (numbers from a real invoice, no personal data)
SPLIT_BGN = """
 ФАКТУРА Копие
 № 0000000001 / 16.07.2024
 Консумирана електрическа енергия от 08.06.2024 до 30.06.2024
 Електромер № 00000000 Показания ново Показания старо Разлика Корекция кВтч
 Дневна 27 606 27 309 297 0 297
 Нощна 19 996 19 698 298 0 298
 Консумирана електрическа енергия от 01.07.2024 до 05.07.2024
 Дневна 27 675 27 606 69 0 69
 Нощна 20 079 19 996 83 0 83
 Тарифа Количество Единична цена Сума(лв.)
 Активна енергия Дневна 297 0,14875 44,18
 Активна енергия Дневна 69 0,16210 11,18
 Активна енергия Нощна 298 0,05997 17,87
 Активна енергия Нощна 83 0,07104 5,90
 Акциз (0,00 лв./МВтч) за акцизен период 07.2024 747 0,00000 0,00
 Достъп до разпределителната мрежа 595 0,00754 4,49
 Достъп до разпределителната мрежа 152 0,00770 1,17
 Достъп до електропреносната мрежа 595 0,00102 0,61
 Достъп до електропреносната мрежа 152 0,00085 0,13
 Пренос през разпределителната мрежа НН 595 0,04232 25,18
 Пренос през разпределителната мрежа НН 152 0,03803 5,78
 Пренос през електропреносната мрежа 595 0,01521 9,05
 Пренос през електропреносната мрежа 152 0,01270 1,93
 Данъчна основа 127,47
 ДДС 20% 25,49
 Обща стойност на сделката 152,96
"""


class InvoiceTests(unittest.TestCase):
    def test_parse_split_invoice(self):
        inv = invoices.parse_text(SPLIT_BGN)
        self.assertEqual((inv.number, inv.issued, inv.currency), ("0000000001", "2024-07-16", "BGN"))
        self.assertEqual(len(inv.periods), 2)
        self.assertEqual(sum(r["kwh"] for r in inv.readings), 747)
        self.assertEqual(inv.readings[0]["old"], 27309)
        self.assertAlmostEqual(sum(ln["amount"] for ln in inv.lines), inv.tax_base, places=2)

    def test_store_converts_to_ccy_and_derives_tariffs(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = connect(Path(tmp) / "t.sqlite")
            invoices.store(db, invoices.parse_text(SPLIT_BGN), "x.pdf")
            total = db.execute("SELECT total_ccy FROM grid_invoice").fetchone()[0]
            self.assertAlmostEqual(total, 152.96 / 1.95583, places=4)
            self.assertEqual(invoices.derive_tariffs(db), 2)
            # a hand-entered invoice has no price lines and does not change the tariffs
            db.execute("INSERT INTO grid_invoice (invoice_no, issued, period_from, period_to, day_kwh, night_kwh) "
                       "VALUES ('ръчно', '2024-08-10', '2024-07-06', '2024-08-05', 1, 1)")
            self.assertEqual(invoices.derive_tariffs(db), 2)
            day = db.execute("SELECT price_per_kwh FROM tariff WHERE zone = 'day' AND valid_from = '2024-07-01'"
                             ).fetchone()[0]
            self.assertAlmostEqual(day, (0.16210 + 0.00770 + 0.00085 + 0.03803 + 0.01270) / 1.95583, places=4)
            db.close()

class SeedTests(unittest.TestCase):
    def test_seed_file_imports_idempotently_and_skips_overlaps(self):
        import json
        import tempfile
        from collector.db import connect
        from collector.invoices import import_paths
        def inv(no, issued, a, b, kwh_day, kwh_night, total):
            return {"number": no, "issued": issued, "currency": "EUR", "periods": [[a, b]],
                    "readings": [{"period": [a, b], "zone": "day", "old": 0, "new": kwh_day, "kwh": kwh_day},
                                 {"period": [a, b], "zone": "night", "old": 0, "new": kwh_night, "kwh": kwh_night}],
                    "lines": [{"kind": "energy", "zone": "day", "qty": kwh_day, "price": 0.1, "amount": kwh_day * 0.1},
                              {"kind": "energy", "zone": "night", "qty": kwh_night, "price": 0.05, "amount": kwh_night * 0.05}],
                    "meter": "1", "tax_base": total / 1.2, "vat": total - total / 1.2, "total": total, "source": f"{no}.pdf"}
        # made-up invoices; the middle one overlaps the hand-typed July 2025 invoice
        items = [inv("T1", "2025-07-15", "2025-06-08", "2025-07-07", 300, 200, 50.0),
                 inv("T2", "2025-08-14", "2025-07-08", "2025-08-05", 310, 210, 52.0),
                 inv("T3", "2025-09-15", "2025-08-06", "2025-09-05", 320, 220, 54.0)]
        with tempfile.TemporaryDirectory() as tmp:
            seed = Path(tmp) / "invoices.json"
            seed.write_text(json.dumps(items), encoding="utf-8")
            db = connect(Path(tmp) / "pv.sqlite")
            db.execute("INSERT INTO grid_invoice (invoice_no, issued, period_from, period_to, total_ccy, source_file) "
                       "VALUES ('manual-1', '2025-08-10', '2025-07-08', '2025-08-05', 100, 'manual')")
            first = import_paths(db, [str(seed)])
            again = import_paths(db, [str(seed)])
            self.assertEqual(len(first), len(items) - 1)      # the hand-typed July 2025 one stays
            self.assertEqual(first, again)
            n = db.execute("SELECT count(*) FROM grid_invoice").fetchone()[0]
            self.assertEqual(n, len(items))
            db.close()
