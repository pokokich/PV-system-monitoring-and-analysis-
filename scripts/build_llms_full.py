"""Build llms-full.txt (llmstxt.org) from llms.txt and the English docs.

Run after changing README.md, AGENT_SETUP.md, docs/public-access.md or llms.txt:
    python scripts/build_llms_full.py
tests/test_docs.py fails when the committed file is out of date.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCES = ("README.md", "AGENT_SETUP.md", "docs/public-access.md")
REPO = "https://github.com/pokokich/PV-system-monitoring-and-analysis-/blob/main/"


def build() -> str:
    head = (ROOT / "llms.txt").read_text(encoding="utf-8").split("\n## ", 1)[0].rstrip()
    parts = [head, "This file contains the full English documentation of PV Monitor, generated from the files below."]
    for name in SOURCES:
        text = (ROOT / name).read_text(encoding="utf-8").strip()
        parts.append(f"---\n\nSource: {REPO}{name}\n\n{text}")
    return "\n\n".join(parts) + "\n"


if __name__ == "__main__":
    (ROOT / "llms-full.txt").write_text(build(), encoding="utf-8")
