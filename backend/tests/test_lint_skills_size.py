"""Tests for the pipeline stage-doc SIZE pass in ``scripts/lint_skills.py``.

What is tested: ``size_limit_for`` (which limit a path gets) and ``lint_sizes``
(the ratchet). Methodology: a fake repo root under ``tmp_path`` carrying a
miniature ``backend/skills/s_autonomous-pipeline`` tree, with an explicit
pending-split table passed in, so every rule is driven on real files.

Invariants:
  * an unlisted file must meet its real limit (card 5K, INSTRUCTIONS 10K,
    any non-history stages subdir 15K); ``stages/history/`` is unlimited;
  * a listed file may shrink but never grow past its ceiling;
  * a listed file that fits its real limit, or no longer exists, is a stale
    entry and itself a finding;
  * the shipped repo is green today and its table holds only oversize files.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS = _REPO_ROOT / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import lint_skills  # noqa: E402

_SKILL = "backend/skills/s_autonomous-pipeline"


def _write(root: Path, rel: str, n: int, ch: str = "x") -> str:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(ch * n, encoding="utf-8")
    return rel


@pytest.mark.parametrize("rel,limit", [
    (f"{_SKILL}/stages/build.md", 5_000),
    (f"{_SKILL}/INSTRUCTIONS.md", 10_000),
    (f"{_SKILL}/stages/ref/build/greenfield.md", 15_000),
    (f"{_SKILL}/stages/ref/topic.md", 15_000),
    (f"{_SKILL}/stages/specialists/x.md", 15_000),
    (f"{_SKILL}/stages/history/build.md", None),
    (f"{_SKILL}/stages/history/old/deep.md", None),
    (f"{_SKILL}/REVIEW_PATTERNS.md", None),
    (f"{_SKILL}/SKILL.md", None),
    (f"{_SKILL}/stages/build.txt", None),
    ("backend/skills/s_other/stages/build.md", None),
])
def test_size_limit_for_each_location(rel, limit):
    assert lint_skills.size_limit_for(rel) == limit


@pytest.mark.parametrize("rel,limit", [
    (f"{_SKILL}/stages/build.md", 5_000),
    (f"{_SKILL}/INSTRUCTIONS.md", 10_000),
    (f"{_SKILL}/stages/ref/build/greenfield.md", 15_000),
    (f"{_SKILL}/stages/specialists/x.md", 15_000),
])
def test_unlisted_file_must_meet_the_real_limit(tmp_path, rel, limit):
    _write(tmp_path, rel, limit)
    assert lint_skills.lint_sizes(tmp_path, pending={}) == [], "at the limit is fine"
    _write(tmp_path, rel, limit + 1)
    findings = lint_skills.lint_sizes(tmp_path, pending={})
    assert len(findings) == 1 and rel in findings[0] and "exceeds" in findings[0]


def test_history_tree_is_unlimited(tmp_path):
    _write(tmp_path, f"{_SKILL}/stages/history/deliver.md", 200_000)
    assert lint_skills.lint_sizes(tmp_path, pending={}) == []


def test_size_is_counted_in_characters_not_bytes(tmp_path):
    # 5,000 three-byte characters = 15,000 bytes, still exactly at the limit.
    _write(tmp_path, f"{_SKILL}/stages/think.md", 5_000, ch="字")
    assert lint_skills.lint_sizes(tmp_path, pending={}) == []


def test_listed_file_may_sit_at_or_under_its_ceiling(tmp_path):
    rel = _write(tmp_path, f"{_SKILL}/stages/deliver.md", 70_000)
    assert lint_skills.lint_sizes(tmp_path, pending={rel: 70_000}) == []
    _write(tmp_path, rel, 60_000)  # shrinking is always allowed
    assert lint_skills.lint_sizes(tmp_path, pending={rel: 70_000}) == []


def test_listed_file_may_not_grow_past_its_ceiling(tmp_path):
    rel = _write(tmp_path, f"{_SKILL}/INSTRUCTIONS.md", 50_001)
    findings = lint_skills.lint_sizes(tmp_path, pending={rel: 50_000})
    assert len(findings) == 1 and "ceiling" in findings[0] and rel in findings[0]


def test_listed_file_that_fits_the_real_limit_is_a_stale_entry(tmp_path):
    rel = _write(tmp_path, f"{_SKILL}/stages/review.md", 4_000)
    findings = lint_skills.lint_sizes(tmp_path, pending={rel: 56_000})
    assert len(findings) == 1 and "stale" in findings[0]


def test_listed_file_that_no_longer_exists_is_a_stale_entry(tmp_path):
    _write(tmp_path, f"{_SKILL}/stages/build.md", 10)
    gone = f"{_SKILL}/stages/evaluate.md"
    findings = lint_skills.lint_sizes(tmp_path, pending={gone: 56_000})
    assert len(findings) == 1 and gone in findings[0] and "stale" in findings[0]


def test_listed_history_file_is_a_stale_entry(tmp_path):
    rel = _write(tmp_path, f"{_SKILL}/stages/history/build.md", 90_000)
    findings = lint_skills.lint_sizes(tmp_path, pending={rel: 90_000})
    assert len(findings) == 1 and "stale" in findings[0]


def test_absent_pipeline_skill_is_a_clean_no_op(tmp_path):
    assert lint_skills.lint_sizes(tmp_path, pending={}) == []


def test_the_shipped_stage_docs_pass_the_ratchet():
    assert lint_skills.lint_sizes() == []


def test_every_pending_entry_is_size_governed_and_really_oversize():
    """The table may only hold files that are governed AND over their limit —
    anything else is the stale state the lint itself rejects."""
    for rel, ceiling in lint_skills._SIZE_PENDING_SPLIT.items():
        limit = lint_skills.size_limit_for(rel)
        assert limit is not None, f"{rel} is not size-governed"
        assert ceiling > limit, f"{rel}: ceiling {ceiling} does not exceed limit {limit}"


def test_main_runs_the_size_pass(monkeypatch, capsys):
    monkeypatch.setattr(lint_skills, "lint_sizes", lambda: ["fake: too big"])
    assert lint_skills.main() == 1
    assert "fake: too big" in capsys.readouterr().out
