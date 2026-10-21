import json
import socket
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from desktop import app


class Health(BaseHTTPRequestHandler):
    body = {"ok": True}

    def do_GET(self):
        data = json.dumps(self.body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


class InstanceTests(unittest.TestCase):
    def serve(self, body):
        Health.body = body
        server = HTTPServer(("127.0.0.1", 0), Health)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        return server.server_address[1]

    def test_another_service_on_the_port_is_not_taken_for_this_app(self):
        port = self.serve({"ok": True, "last_frame": None})
        self.assertTrue(app.in_use(port))
        self.assertFalse(app.running(port, "abc"))
        self.assertTrue(app.running(self.serve({"ok": True, "instance": "abc"}), "abc"))

    def test_free_port_skips_a_used_one(self):
        port = self.serve({})
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            spare = s.getsockname()[1]
        self.assertNotEqual(app.free_port(port, port + 50), port)
        self.assertEqual(app.free_port(spare, spare), spare)

    def test_previous_instance_file(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(app.previous(Path(d)))
            (Path(d) / "instance").write_text("8089 abc\n")
            self.assertEqual(app.previous(Path(d)), (8089, "abc"))


class EnvFileTests(unittest.TestCase):
    def test_quotes_are_removed_as_docker_does(self):
        import os
        from collector.deye_client import load_env
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / ".env"
            f.write_text('PVMON_T1="claude -p"\nPVMON_T2=a b\nPVMON_T3=\'x\'\n')
            try:
                load_env(f)
                self.assertEqual((os.environ["PVMON_T1"], os.environ["PVMON_T2"], os.environ["PVMON_T3"]), ("claude -p", "a b", "x"))
            finally:
                for k in ("PVMON_T1", "PVMON_T2", "PVMON_T3"):
                    os.environ.pop(k, None)


class CliModelTests(unittest.TestCase):
    def test_error_printed_on_stdout_is_shown(self):
        import shlex
        import sys
        from web import llm
        cmd = f"{shlex.quote(sys.executable)} -c \"print('Not logged in'); raise SystemExit(1)\""
        with self.assertRaises(llm.LlmError) as ctx:
            llm._cli({"cli_command": cmd}, "s", "p")
        self.assertIn("Not logged in", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
