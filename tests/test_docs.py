import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_builder():
    spec = importlib.util.spec_from_file_location("build_llms_full", ROOT / "scripts" / "build_llms_full.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class LlmsTxt(unittest.TestCase):
    def test_llms_txt_shape(self):
        text = (ROOT / "llms.txt").read_text(encoding="utf-8")
        self.assertTrue(text.startswith("# PV Monitor\n"))
        self.assertIn("\n> ", text)
        self.assertIn("\n## Docs\n", text)

    def test_llms_full_is_current(self):
        current = (ROOT / "llms-full.txt").read_text(encoding="utf-8")
        self.assertEqual(current, load_builder().build(),
                         "llms-full.txt is out of date: run python scripts/build_llms_full.py")


if __name__ == "__main__":
    unittest.main()
