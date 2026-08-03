"""Tests for the C&M Global Brain overlay's context token-block builder.

The overlay's Context tab + overview rail need a LIVE, calibrated view of the
assembled context files: total prompt tokens, per-file token size, composition %,
ownership (system/user/agent/auto), priority, and lock (P0-P2 non-truncatable).

`build_context_token_block(context_dir)` is the single source of that data. It
joins ContextDirectoryLoader.CONTEXT_FILES (the SoT for filename/priority/
section_name/truncatable) with the CANONICAL ContextDirectoryLoader.estimate_tokens
(the same estimator prompt assembly uses — NOT byte-size, which diverges ~2.2x on
CJK) and the real 91K/130K budget thresholds.

Ownership is derived by an explicit filename→owner map (Gate-1 finding: ContextFileSpec
has NO owner field — only user_customized; the 4-way owner category system/user/
agent/auto is documented in KNOWLEDGE.md but not on the dataclass, so the map lives
here, NOT as a dataclass mutation).
"""
from pathlib import Path

import pytest

from core.context_brain import build_context_token_block


@pytest.fixture
def fake_context_dir(tmp_path: Path) -> Path:
    """A .context dir with a few real-named context files of known content."""
    ctx = tmp_path / ".context"
    ctx.mkdir()
    # ASCII content — deterministic token estimate
    (ctx / "SWARMAI.md").write_text("core identity " * 50, encoding="utf-8")
    (ctx / "AGENT.md").write_text("agent rules " * 200, encoding="utf-8")
    (ctx / "USER.md").write_text("user profile " * 30, encoding="utf-8")
    (ctx / "MEMORY.md").write_text("memory entry " * 400, encoding="utf-8")
    return ctx


def test_token_block_has_total_and_budget(fake_context_dir: Path):
    block = build_context_token_block(fake_context_dir)
    assert block["total_tokens"] > 0
    assert block["budget"] > 0
    assert block["warning_threshold"] == 91_000
    assert block["emergency_threshold"] == 130_000
    # total must equal the sum of per-file tokens (no double count, no drop)
    assert block["total_tokens"] == sum(f["tokens"] for f in block["per_file"])


def test_per_file_rows_have_required_shape(fake_context_dir: Path):
    block = build_context_token_block(fake_context_dir)
    assert len(block["per_file"]) == 4  # only the 4 files that exist on disk
    for row in block["per_file"]:
        assert set(row) >= {"name", "tokens", "pct", "owner", "priority", "locked"}
        assert row["tokens"] > 0
        assert 0 <= row["pct"] <= 100
        assert row["owner"] in {"system", "user", "agent", "auto"}


def test_ownership_and_lock_are_correct(fake_context_dir: Path):
    block = build_context_token_block(fake_context_dir)
    by_name = {row["name"]: row for row in block["per_file"]}
    # SWARMAI = P0 system, LOCKED (non-truncatable)
    assert by_name["SWARMAI.md"]["owner"] == "system"
    assert by_name["SWARMAI.md"]["priority"] == 0
    assert by_name["SWARMAI.md"]["locked"] is True
    # AGENT = P3 system, NOT locked (truncatable)
    assert by_name["AGENT.md"]["owner"] == "system"
    assert by_name["AGENT.md"]["locked"] is False
    # USER = P4 user-owned
    assert by_name["USER.md"]["owner"] == "user"
    # MEMORY = P7 agent-owned
    assert by_name["MEMORY.md"]["owner"] == "agent"


def test_rows_sorted_by_priority(fake_context_dir: Path):
    block = build_context_token_block(fake_context_dir)
    priorities = [row["priority"] for row in block["per_file"]]
    assert priorities == sorted(priorities)


def test_composition_pct_sums_to_about_100(fake_context_dir: Path):
    block = build_context_token_block(fake_context_dir)
    total_pct = sum(f["pct"] for f in block["per_file"])
    # rounding tolerance — each pct is rounded to 1 decimal
    assert 99.0 <= total_pct <= 101.0


def test_missing_context_dir_returns_empty_not_crash(tmp_path: Path):
    # A non-existent dir must yield an empty-but-valid block, never raise
    block = build_context_token_block(tmp_path / "nope")
    assert block["total_tokens"] == 0
    assert block["per_file"] == []
    assert block["budget"] > 0  # thresholds still present


def test_calibrated_tokens_not_bytes(fake_context_dir: Path):
    """The estimate must be the calibrated token count, not raw byte length."""
    block = build_context_token_block(fake_context_dir)
    swarmai = next(r for r in block["per_file"] if r["name"] == "SWARMAI.md")
    raw_bytes = (fake_context_dir / "SWARMAI.md").stat().st_size
    # token estimate is materially smaller than byte count (≈ chars/3.5 for ASCII)
    assert swarmai["tokens"] < raw_bytes
