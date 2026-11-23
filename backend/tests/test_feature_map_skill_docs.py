"""The feature_map skill gives the writing standard, and s_qa replays features (R8.1, R8.2)."""
from __future__ import annotations

from pathlib import Path

SKILLS = Path(__file__).resolve().parents[1] / "skills"


def test_skill_texts_carry_the_standard():
    fm = (SKILLS / "s_feature-map" / "INSTRUCTIONS.md").read_text(encoding="utf-8")
    for must in ("toBeInViewport({ ratio: 1 })", "@start:", "waitForTimeout", "getByRole",
                 "getByTestId", "scripts.feature_map verify", "replay --affected", "--run",
                 "Never capture a stub from a live call", "does not contain"):
        assert must in fm, must
    qa = (SKILLS / "s_qa" / "INSTRUCTIONS.md").read_text(encoding="utf-8")
    assert "scripts.feature_map affected" in qa and "scripts.feature_map replay --affected" in qa
    assert "Take screenshots of each affected page" not in qa, "the eyeball-only step is gone"


def test_the_skill_states_how_to_write_ignore_and_owns():
    # R7.1: the writing standard for the two narrowing declarations and their fallbacks
    fm = (SKILLS / "s_feature-map" / "INSTRUCTIONS.md").read_text(encoding="utf-8")
    flat = " ".join(fm.split())
    for must in ("schema: 2", "**`ignore`** names files the target does not build or serve",
                 "**`owns`** names what is this feature's alone", "Never shared code",
                 "a path no feature owns replays every feature of the target",
                 "treated as not declared", "When in doubt, leave a path unowned"):
        assert must in flat, must
