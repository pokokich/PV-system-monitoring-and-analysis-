import gzip
import json
import os
import sqlite3
import tempfile
import time
import unittest
import urllib.error
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

from collector import backup, guard, notify
from collector.db import connect, set_state


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = connect(self.root / "pv.sqlite")
        self.db.execute("INSERT INTO station (id, name) VALUES (1, 'test')")
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_backup_is_a_consistent_gzipped_copy(self):
        path = backup.make_backup(self.db, self.root / "b", date(2026, 10, 20))
        self.assertEqual(path.name, "pv-2026-10-20.sqlite.gz")
        raw = self.root / "restored.sqlite"
        raw.write_bytes(gzip.decompress(path.read_bytes()))
        copy = sqlite3.connect(raw)
        self.assertEqual(copy.execute("SELECT name FROM station").fetchone()[0], "test")
        copy.close()

    def test_rotation_keeps_days_sundays_and_months(self):
        d0 = date(2026, 10, 20)
        days = [d0 - timedelta(days=i) for i in range(400)]
        keep = backup.to_keep(days)
        self.assertTrue(set(days[:7]) <= keep)
        self.assertTrue(set([d for d in days if d.weekday() == 6][:8]) <= keep)
        self.assertTrue(set([d for d in days if d.day == 1][:12]) <= keep)
        self.assertLessEqual(len(keep), 7 + 8 + 12)
        folder = self.root / "r"
        folder.mkdir()
        for d in days[:30]:
            (folder / f"pv-{d}.sqlite.gz").write_bytes(b"x")
        (folder / "notes.txt").write_text("stays")
        backup.rotate(folder)
        left = sorted(p.name for p in folder.iterdir())
        self.assertIn("notes.txt", left)
        self.assertIn("pv-2026-10-20.sqlite.gz", left)
        self.assertNotIn("pv-2026-09-22.sqlite.gz", left)   # a Monday, older than 7 days
        self.assertIn("pv-2026-10-01.sqlite.gz", left)       # first of the month

    def test_copy_before_migration(self):
        for v in range(5):
            backup.pre_migration_copy(self.db, 9 + v)
        left = sorted(p.name for p in self.root.glob("pv.pre-*.sqlite"))
        self.assertEqual([n[:11] for n in left], ["pv.pre-011-", "pv.pre-012-", "pv.pre-013-"])


class GuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        env = {"DB_PATH": str(root / "pv.sqlite"), "BACKUP_PATH": str(root / "b"), "TZ": "Europe/Sofia"}
        for k in ("TELEGRAM_BOT_TOKEN", "SMTP_HOST", "NTFY_URL", "WEB_HEALTH_URL"):
            env[k] = ""
        self.env = mock.patch.dict(os.environ, env)
        self.env.start()
        self.g = guard.Guard()
        self.g.conn.execute("INSERT INTO station (id, name) VALUES (1, 'test')")
        self.g.conn.commit()

    def tearDown(self):
        self.g.conn.close()
        self.env.stop()
        self.tmp.cleanup()

    def frame(self, ts):
        self.g.conn.execute("INSERT INTO station_frame (station_id, ts, source) VALUES (1, ?, 'power')", (int(ts),))
        self.g.conn.commit()

    def codes(self):
        return {p["code"] for p in guard.assess(self.g.conn)}

    def test_stale_data_and_backup(self):
        self.frame(time.time() - 3 * 3600)
        self.assertEqual(self.codes(), {"data_stale", "backup_missing"})
        self.frame(time.time() - 60)
        self.assertEqual(self.codes(), {"backup_missing"})
        self.assertTrue(self.g.do_backup())
        self.assertEqual(self.codes(), set())
        set_state(self.g.conn, "backup_last_ts", str(time.time() - 40 * 3600))
        self.assertEqual(self.codes(), {"backup_old"})

    def test_alert_on_change_only(self):
        sent = []
        with mock.patch.object(notify, "send", lambda t, x: sent.append(t) or {"test": "ok"}):
            self.frame(time.time() - 3 * 3600)
            self.g.check()          # backup is made, data is stale -> alert
            self.g.check()          # same problem -> no second alert
            self.frame(time.time() - 60)
            self.g.check()          # all fine again -> one "fine" message
            self.g.check()
        self.assertEqual(sent, ["PV система: проблем", "PV система: всичко е наред отново"])
        status = json.loads(self.g.conn.execute("SELECT value FROM collector_state WHERE key = 'guard_status'").fetchone()[0])
        self.assertEqual(status["problems"], [])
        self.assertIsNone(guard.guard_heartbeat_problem(self.g.conn))

    def test_site_starting_up_is_not_down(self):
        self.g.web_url = "http://web:8088/healthz"
        calls = []

        def urlopen(url, timeout):
            calls.append(url)
            if len(calls) < 3:
                raise OSError("connection refused")
            return mock.MagicMock()
        with mock.patch("urllib.request.urlopen", urlopen), mock.patch("time.sleep"):
            self.assertIsNone(self.g.web_problem())
            calls.clear()
            with mock.patch("urllib.request.urlopen", side_effect=OSError("refused")):
                self.assertEqual(self.g.web_problem()["code"], "web_down")

    def test_tunnel_check(self):
        self.assertIsNone(self.g.tunnel_problem())   # not configured -> no check
        self.g.tunnel_url = "https://pv.example.com"
        self.g.tunnel_ready_url = "http://tunnel:2000/ready"

        def http_error(code):
            return urllib.error.HTTPError("https://pv.example.com", code, "x", {}, None)
        ok = mock.MagicMock()
        with mock.patch("time.sleep"), mock.patch("urllib.request.urlopen", return_value=ok):
            with mock.patch("urllib.request.OpenerDirector.open", side_effect=http_error(302)):
                self.assertIsNone(self.g.tunnel_problem())   # redirect to the Access login
            with mock.patch("urllib.request.OpenerDirector.open", side_effect=http_error(530)):
                p = self.g.tunnel_problem()
                self.assertEqual(p["code"], "tunnel_down")
                self.assertIn("530", p["text"])
        with mock.patch("time.sleep"), mock.patch("urllib.request.urlopen", side_effect=OSError("refused")):
            self.assertIn("не е свързан", self.g.tunnel_problem()["text"])

    def test_channels_only_when_configured(self):
        self.assertEqual(notify.configured(), [])
        with mock.patch.dict(os.environ, {"NTFY_URL": "https://ntfy.example/topic"}):
            self.assertEqual(notify.configured(), ["ntfy"])
            with mock.patch("urllib.request.urlopen", side_effect=OSError("offline")):
                self.assertEqual(notify.send("t", "x"), {"ntfy": "offline"})


if __name__ == "__main__":
    unittest.main()
