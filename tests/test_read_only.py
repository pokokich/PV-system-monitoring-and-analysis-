import re
import unittest
from pathlib import Path

from collector.deye_client import READ_ONLY_ENDPOINTS, ForbiddenEndpointError, check_endpoint

ROOT = Path(__file__).resolve().parents[1]


class ReadOnlyTests(unittest.TestCase):
    def test_control_endpoints_rejected(self):
        for path in ("/order/sys/workMode/update", "/order/battery/parameter/update",
                     "/order/sys/tou/update", "/strategy/dynamicControl", "/device/register",
                     "/config/update", "/config/battery/update"):
            with self.assertRaises(ForbiddenEndpointError):
                check_endpoint(path)

    def test_allowlist_has_no_write_paths(self):
        for path in READ_ONLY_ENDPOINTS:
            self.assertNotRegex(path, r"order|strategy|update|control|register|switch|set")

    def test_code_never_mentions_control_paths(self):
        pattern = re.compile(r"[\"']/(order|strategy)/")
        for py in (ROOT / "collector").rglob("*.py"):
            self.assertIsNone(pattern.search(py.read_text(encoding="utf-8")), py)

    def test_reads_allowed(self):
        check_endpoint("/station/latest")
        check_endpoint("/device/history")
        check_endpoint("/config/battery")


if __name__ == "__main__":
    unittest.main()
