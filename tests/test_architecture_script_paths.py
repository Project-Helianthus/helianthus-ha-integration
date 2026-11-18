"""Checked-in architecture names only scripts present in a standalone checkout."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ARCHITECTURE = (ROOT / "ARCHITECTURE.md").read_text(encoding="utf-8")
SCRIPT_PATH = re.compile(r"(?<![\w./-])(scripts/[A-Za-z0-9_./-]+\.py)")


def test_architecture_script_paths_exist_in_this_checkout() -> None:
    named = sorted(set(SCRIPT_PATH.findall(ARCHITECTURE)))
    assert named
    missing = [path for path in named if not (ROOT / path).is_file()]
    assert missing == []
    assert "split_coordinator_domains.py" not in ARCHITECTURE
