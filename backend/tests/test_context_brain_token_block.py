"""Tests for the C&M Global Brain overlay's context token-block builder.

The overlay's Context tab + overview rail need a LIVE, calibrated view of the
assembled context files: total prompt tokens, per-file token size, composition %,
ownership (system/user/agent/auto), priority, and lock (P0-P2 non-truncatable).

`build_context_token_block(context_dir)` is the single source of that data. It
joins ContextDirectoryLoader.CONTEXT_FILES (the SoT for filename/priority/
section_name/truncatable) with the CANONICAL ContextDirectoryLoader.estimate_tokens
(the same estimator prompt assembly uses — NOT byte-size, which diverges ~2.2x on
CJK) and the real 133K/172K budget thresholds.

Each row's `tokens` is what the file puts in the prompt (the loader's own
render_section), `disk_tokens` the whole file: TIMELINE.md, for one, injects only its
month summaries. The total, the warning line, the share and the health tag use the
injected tokens.

Ownership is derived by an explicit filename→owner map (Gate-1 finding: ContextFileSpec
has NO owner field — only user_customized; the 4-way owner category system/user/
agent/auto is documented in KNOWLEDGE.md but not on the dataclass, so the map lives
here, NOT as a dataclass mutation).
"""
from datetime import date, timedelta
from pathlib import Path

import pytest

from core.context_brain import build_context_token_block
from core.context_directory_loader import ContextDirectoryLoader


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
    assert block["warning_threshold"] == 133_000
    assert block["emergency_threshold"] == 172_000
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


# --------------------------------------------------------------------------
# Selective-injection honesty (run_5f040023) — disk vs injected
# --------------------------------------------------------------------------

@pytest.fixture
def big_memory_context_dir(tmp_path: Path) -> Path:
    """A .context dir whose MEMORY.md is LARGE (>30K tokens). There is no selective
    mode anymore (2026-08-14) — a large MEMORY.md is still FULL-injected; this
    fixture just exercises the large-file path of the token-block builder."""
    # A large-MEMORY target (the former selective threshold, now just "big").
    BIG_TOKEN_TARGET = 30_000
    ctx = tmp_path / ".context"
    ctx.mkdir()
    (ctx / "SWARMAI.md").write_text("core identity " * 50, encoding="utf-8")
    big = ["## Memory Index\n\nindex line\n", "## Open Threads\n\nalways-load thread\n"]
    # Build well past the target — estimator-confirmed, no char-ratio guessing.
    i = 0
    while ContextDirectoryLoader.estimate_tokens("\n".join(big)) < BIG_TOKEN_TARGET * 1.3:
        big.append(f"## Section{i}\n\n" + ("removable body content here " * 40) + "\n")
        i += 1
    (ctx / "MEMORY.md").write_text("\n".join(big), encoding="utf-8")
    return ctx


def test_no_selective_fields_full_injection_arch(fake_context_dir: Path):
    """The always-inert has_selective / injected_floor per-row fields and the
    injected_estimate block field (run_8f852625) stay removed: the injected size is
    the row's `tokens`, the disk size its `disk_tokens`. Teeth: re-adding any of the
    old fields makes this RED."""
    block = build_context_token_block(fake_context_dir)
    assert "injected_estimate" not in block, "injected_estimate removed (full-injection)"
    for row in block["per_file"]:
        assert "has_selective" not in row, "has_selective removed (no selective mode)"
        assert "injected_floor" not in row, "injected_floor removed (no selective mode)"


def test_big_memory_is_full_injected_no_selective_fields(big_memory_context_dir: Path):
    """Even a large MEMORY.md is injected whole — no selective fields on its row, and
    its injected tokens are its disk tokens (less the stripped title line)."""
    block = build_context_token_block(big_memory_context_dir)
    mem = next(r for r in block["per_file"] if r["name"] == "MEMORY.md")
    assert "has_selective" not in mem
    assert "injected_floor" not in mem
    assert mem["tokens"] == pytest.approx(mem["disk_tokens"], rel=0.01)


def test_total_tokens_is_the_sole_size_headline(big_memory_context_dir: Path):
    """total_tokens (the injected load) is the ONE size headline; disk_tokens is the
    secondary figure, and there is no separate injected_estimate."""
    block = build_context_token_block(big_memory_context_dir)
    assert isinstance(block["total_tokens"], int) and block["total_tokens"] > 0
    assert "injected_estimate" not in block


def test_total_tokens_is_injected_and_disk_is_kept(fake_context_dir: Path):
    """total_tokens is the INJECTED sum; the disk sum is kept beside it, never
    dropped (both per row and in total)."""
    block = build_context_token_block(fake_context_dir)
    assert block["total_tokens"] == sum(f["tokens"] for f in block["per_file"])
    assert block["disk_tokens"] == sum(f["disk_tokens"] for f in block["per_file"])
    for row in block["per_file"]:
        path = fake_context_dir / row["name"]
        assert row["disk_tokens"] == ContextDirectoryLoader.estimate_tokens(
            path.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------
# Injected, not disk — what each file really puts in the prompt
# --------------------------------------------------------------------------

def _previous_month(today: date) -> str:
    return (today.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")


def _write_big_timeline(ctx: Path, events: int) -> str:
    """A TIMELINE.md in the CDF writer's block format: ONE month summary for the
    previous month (what select_timeline injects) and *events* events (which it
    does not). Returns the summary as the prompt shows it."""
    month = _previous_month(date.today())
    summary_shown = (f"### {month}-28 month summary\n- what: the month in short\n"
                     "- outcome: summarised")
    summary = (summary_shown + "\n<!-- cand:summary-1 | event_key:ks | source:S0 | "
               "event_kind:month_summary -->")
    blocks = [
        f"### {month}-{(i % 27) + 1:02d} event number {i}\n"
        f"- what: {'a long event body with many words ' * 12}\n- outcome: done\n"
        f"<!-- cand:event-{i} | event_key:k{i} | source:S2 | event_kind:event -->"
        for i in range(events)
    ]
    (ctx / "TIMELINE.md").write_text(
        f"# Timeline\n\n## {month}\n{summary}\n\n" + "\n\n".join(blocks) + "\n",
        encoding="utf-8",
    )
    return summary_shown


def test_timeline_injected_is_the_summary_disk_is_the_file(tmp_path: Path):
    """A TIMELINE with many events and one month summary: its injected tokens are
    about the summary (plus the section header and the one line counting events),
    its disk tokens the whole file, and the block total uses the injected figure."""
    ctx = tmp_path / ".context"
    ctx.mkdir()
    (ctx / "SWARMAI.md").write_text("core identity " * 50, encoding="utf-8")
    summary_shown = _write_big_timeline(ctx, events=300)

    block = build_context_token_block(ctx)
    row = next(r for r in block["per_file"] if r["name"] == "TIMELINE.md")
    est = ContextDirectoryLoader.estimate_tokens
    disk = est((ctx / "TIMELINE.md").read_text(encoding="utf-8"))

    assert row["disk_tokens"] == disk
    assert disk > 20_000                       # the events make the file big
    summary = est(summary_shown)
    # summary + "## Timeline" header + "(300 events are in …)" line: a few dozen tokens
    assert summary <= row["tokens"] <= summary + 40
    assert row["tokens"] < disk / 100
    # the total and the share are on the injected figure
    assert block["total_tokens"] == sum(r["tokens"] for r in block["per_file"])
    assert block["total_tokens"] < disk
    assert row["pct"] == round(row["tokens"] / block["total_tokens"] * 100, 1)
    assert row["health"] == "fresh"


def test_over_budget_does_not_fire_when_only_disk_is_over(tmp_path: Path):
    """A TIMELINE far over the warning line ON DISK, whose injected form is small:
    over_budget stays False and the file is not tagged oversized."""
    ctx = tmp_path / ".context"
    ctx.mkdir()
    (ctx / "SWARMAI.md").write_text("core identity " * 50, encoding="utf-8")
    _write_big_timeline(ctx, events=2500)

    block = build_context_token_block(ctx)
    row = next(r for r in block["per_file"] if r["name"] == "TIMELINE.md")
    assert block["disk_tokens"] > block["warning_threshold"]
    assert block["total_tokens"] < block["warning_threshold"]
    assert block["over_budget"] is False
    assert row["health"] not in {"oversized", "growing"}


def test_over_budget_fires_on_injected_tokens(tmp_path: Path):
    """The warning still fires when what is injected is over the line (MEMORY.md is
    injected whole)."""
    ctx = tmp_path / ".context"
    ctx.mkdir()
    words = 70_000  # ~2.2 tokens per word → ~154K tokens
    (ctx / "MEMORY.md").write_text("## Principles\n" + "memoryword " * words, encoding="utf-8")
    block = build_context_token_block(ctx)
    assert block["total_tokens"] > block["warning_threshold"]
    assert block["over_budget"] is True
    mem = next(r for r in block["per_file"] if r["name"] == "MEMORY.md")
    assert mem["health"] == "oversized"


def test_unfilled_template_injects_nothing(tmp_path: Path):
    """An unfilled user template (headers only) is skipped by assembly: 0 injected
    tokens, its disk size still reported."""
    ctx = tmp_path / ".context"
    ctx.mkdir()
    (ctx / "TOOLS.md").write_text("## Device Names\n\n## SSH Hosts\n", encoding="utf-8")
    block = build_context_token_block(ctx)
    row = next(r for r in block["per_file"] if r["name"] == "TOOLS.md")
    assert row["tokens"] == 0
    assert row["disk_tokens"] > 0
    assert block["total_tokens"] == 0


def test_total_matches_what_assembly_carries(tmp_path: Path):
    """The overlay's total is the assembled prompt's context-file load: the sum of
    the sections assembly joins (the "\\n\\n" separators carry no words), each
    counted as the budget check counts it."""
    ctx = tmp_path / ".context"
    ctx.mkdir()
    (ctx / "SWARMAI.md").write_text("# SwarmAI\n<!-- private note -->\ncore identity " * 30,
                                    encoding="utf-8")
    (ctx / "AGENT.md").write_text("agent rules " * 200, encoding="utf-8")
    (ctx / "MEMORY.md").write_text("## Principles\n- one principle\n" * 40, encoding="utf-8")
    (ctx / "TOOLS.md").write_text("## Device Names\n\n## SSH Hosts\n", encoding="utf-8")
    _write_big_timeline(ctx, events=50)

    block = build_context_token_block(ctx)
    assembled = ContextDirectoryLoader(ctx)._assemble_from_sources(1_000_000)
    whole = ContextDirectoryLoader.estimate_tokens(assembled)
    # the estimator rounds down per text, so per-section sums may differ by < 1 each
    assert abs(block["total_tokens"] - whole) <= len(block["per_file"])
    assert block["total_tokens"] < block["disk_tokens"]


def test_health_counts_present_for_lifecycle_files_none_for_prose(tmp_path: Path):
    """run_2816ab1c: per_file carries health_counts for the 3 entry-structured
    files, None for prose files."""
    ctx = tmp_path / ".context"
    ctx.mkdir()
    (ctx / "SWARMAI.md").write_text("core identity " * 50, encoding="utf-8")
    # MEMORY.md with a REAL entry carrying metadata → counts populated.
    (ctx / "MEMORY.md").write_text(
        "## Guidelines\n"
        "- [guideline] **A real entry** — a lesson (2026-01-01)\n"
        "  <!-- ref:2 | last:2026-01-01 | decay:active -->\n",
        encoding="utf-8",
    )
    block = build_context_token_block(ctx)
    rows = {r["name"]: r for r in block["per_file"]}
    assert "health_counts" in rows["MEMORY.md"]
    hc = rows["MEMORY.md"]["health_counts"]
    assert hc is not None
    assert set(hc) == {"entries", "duplicate"}
    assert hc["entries"] == 1
    assert hc["duplicate"] == 0
    # Prose file → no entries → None (not a fake zero).
    assert rows["SWARMAI.md"]["health_counts"] is None
