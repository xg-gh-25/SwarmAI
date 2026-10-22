"""Lint Pipeline v1 stage cards against protocol symbols.

Methodology: scan only the nine v1 cards; every named check and command must exist.
"""
from __future__ import annotations

import re
from pathlib import Path

from core.run_protocol.checks import CHECKS

ROOT = Path(__file__).resolve().parents[1] / "skills" / "s_autonomous-pipeline" / "stages"
EXPECTED = {"evaluate", "think", "plan", "build", "goal_cycle", "review", "test", "deliver", "reflect"}


def test_v1_cards_are_complete_small_and_reference_real_checks():
    cards = {name: ROOT / f"{name}.md" for name in EXPECTED}
    for name, path in cards.items():
        text = path.read_text(encoding="utf-8")
        assert len(text) < 2000, name
        for check_id in re.findall(r"\b(?:RQ|DS|BX|RV|DC|FX|TS|DV|LG|QX|MD|RF)-\d+[a-z]?\b", text):
            assert check_id in CHECKS, (name, check_id)
        assert "pipeline_v1.py" in text or name in {"build"}, name


def test_cards_never_mention_deleted_reviewers_or_legacy_commands():
    text = "\n".join((ROOT / f"{name}.md").read_text(encoding="utf-8")
                     for name in EXPECTED)
    for deleted in ("gate1-skeptic", "meta-review", "fixspec-skeptic", "root-cause",
                    "run-update --stage-json", "advance --run-id", "probe:", " -- sh"):
        assert deleted not in text
