"""E2E tests for ContextHealthHook MEMORY.md/KNOWLEDGE.md decay lifecycle.

Drives the REAL _run_memory_lifecycle / _run_knowledge_lifecycle (no mock of the
function under change — GUI32 prompt-source=answer-source) against a temp
workspace, verifying the full decay→archive→strip→reindex loop closes:

  - AC1/AC2: an OLD (created > dormant threshold), unreferenced operational entry
    is transitioned to dormant, PERSISTED, then archived+stripped in the same run.
  - AC3: an OLD but usage-cited entry (via .memory-usage.json → ref bridge) is
    NOT stripped (B-protection).
  - AC4: an evergreen (COE Registry) entry is never decayed nor stripped.
  - AC5: the Memory Index is rebuilt AFTER the strip so no index ID points at a
    stripped entry (same-session consistency).
  - AC6: _run_knowledge_lifecycle also persists decay transitions.
  - AC7: assess_decay's default (no dormant_days) behavior is unchanged (60d),
    so ddd_orchestrator IMPROVEMENT.md semantics are preserved.

Mutation contract (verified manually in BUILD):
  - revert the apply-loop  → AC1 (dormant persist) goes RED.
  - revert the post-strip reindex → AC5 (index-consistency) goes RED.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from core.ddd_entry_lifecycle import assess_decay, parse_entries
from hooks.context_health_hook import ContextHealthHook


def _old_iso(days: int) -> str:
    return (date.today() - timedelta(days=days)).isoformat()


def _mem_doc() -> str:
    """A MEMORY.md body with 4 probe entries + an index block.

    - GUI900 old-unused (created 120d ago, ref:0) → must decay+strip
    - GUI901 old-cited  (created 120d ago, ref:0 on disk, but usage-cited) → keep
    - COE900 evergreen  (COE Registry, created 200d ago) → immune
    - GUI902 fresh      (created today) → keep (grace)
    """
    old = _old_iso(120)
    older = _old_iso(200)
    today = date.today().isoformat()
    return f"""<!-- MEMORY_INDEX_START -->
## Memory Index
- [GUI900] old unused probe entry
- [GUI901] old cited probe entry
- [COE900] evergreen coe probe
- [GUI902] fresh probe entry
<!-- MEMORY_INDEX_END -->

## Guidelines

_Operational lessons._

- [guideline] **old unused probe entry** — an old operational lesson nobody cited. ({old}, run_probe900)
  <!-- ref:0 | last:{old} | decay:active -->
- [guideline] **old cited probe entry** — an old operational lesson that IS still cited. ({old}, run_probe901)
  <!-- ref:0 | last:{old} | decay:active -->
- [guideline] **fresh probe entry** — a brand new lesson within grace. ({today}, run_probe902)
  <!-- ref:0 | last:{today} | decay:active -->

## COE Registry

_Post-mortems. Never decays._

- [pitfall] **evergreen coe probe** — a permanent post-mortem. ({older})
  <!-- ref:0 | last:{older} | decay:active -->
"""


@pytest.fixture
def ws(tmp_path):
    ctx = tmp_path / ".context"
    ctx.mkdir()
    (ctx / "MEMORY.md").write_text(_mem_doc(), encoding="utf-8")
    return tmp_path


@pytest.fixture
def hook():
    return ContextHealthHook()


def _titles(section_entries):
    return {e.title for e in section_entries}


class TestMemoryLifecycleClosesLoop:
    def test_old_unused_entry_is_stripped(self, hook, ws):
        """AC1+AC2: old unused operational entry decays → archived+stripped."""
        hook._run_memory_lifecycle(ws)
        content = (ws / ".context" / "MEMORY.md").read_text(encoding="utf-8")
        entries = parse_entries(content)
        titles = {e.title for e in entries}
        # The old unused entry must be GONE from the body (stripped).
        assert "old unused probe entry" not in titles, (
            "old-unused entry should have been decayed+stripped, still present"
        )

    def test_old_unused_entry_archived(self, hook, ws):
        """AC2: stripped entry is preserved in the archive (reversible)."""
        hook._run_memory_lifecycle(ws)
        archive = ws / ".context" / "MEMORY-archive.md"
        assert archive.exists(), "archive file should be created"
        assert "old unused probe entry" in archive.read_text(encoding="utf-8")

    def test_no_double_archive(self, hook, ws):
        """Adversarial (Gate-2): reclaim is the SINGLE archive authority. An
        entry must appear EXACTLY ONCE in the archive — not twice from a
        redundant apply-loop archive_entries + reclaim archive. Regression guard
        for the double-archive the active_entries filter was wrongly meant to
        prevent (inject_entry_metadata never strips, so a separate pre-archive
        would be re-archived by reclaim)."""
        hook._run_memory_lifecycle(ws)
        archive = (ws / ".context" / "MEMORY-archive.md").read_text(encoding="utf-8")
        # The stripped entry's title must occur exactly once in the archive.
        assert archive.count("old unused probe entry") == 1, (
            f"double-archive: 'old unused probe entry' appears "
            f"{archive.count('old unused probe entry')} times in archive (want 1)"
        )

    def test_evergreen_never_stripped(self, hook, ws):
        """AC4: COE Registry entry is immune."""
        hook._run_memory_lifecycle(ws)
        content = (ws / ".context" / "MEMORY.md").read_text(encoding="utf-8")
        assert "evergreen coe probe" in content

    def test_fresh_entry_kept(self, hook, ws):
        """AC4: entry within grace is kept."""
        hook._run_memory_lifecycle(ws)
        content = (ws / ".context" / "MEMORY.md").read_text(encoding="utf-8")
        assert "fresh probe entry" in content

    def test_old_cited_entry_protected(self, hook, ws):
        """AC3: an old entry with real usage citation is NOT stripped."""
        import json
        # Cite GUI901 heavily via the usage bridge.
        (ws / ".context" / ".memory-usage.json").write_text(
            json.dumps({"GUI901": 50}), encoding="utf-8"
        )
        hook._run_memory_lifecycle(ws)
        content = (ws / ".context" / "MEMORY.md").read_text(encoding="utf-8")
        assert "old cited probe entry" in content, (
            "old-but-cited entry should be protected from strip by ref bridge"
        )

    def test_index_rebuilt_no_orphan_ids(self, hook, ws):
        """AC5: after strip, the Memory Index has no ID pointing at a stripped entry."""
        hook._run_memory_lifecycle(ws)
        content = (ws / ".context" / "MEMORY.md").read_text(encoding="utf-8")
        # index block
        start = content.find("MEMORY_INDEX_START")
        end = content.find("MEMORY_INDEX_END")
        assert start != -1 and end != -1, "index markers must survive"
        index_block = content[start:end]
        # The stripped entry's index line must be gone too (same-session rebuild).
        assert "old unused probe entry" not in index_block, (
            "index still references a stripped entry — reindex did not run after strip"
        )


class TestAssessDecayDefaultUnchanged:
    def test_default_dormant_threshold_is_not_45(self):
        """AC7: assess_decay with no dormant_days keeps the 60d default —
        proves the MEMORY 45d path did not leak into IMPROVEMENT/KNOWLEDGE."""
        from core.ddd_entry_lifecycle import EntryMetadata

        today = date(2026, 7, 13)
        e = EntryMetadata(
            entry_type="guideline",
            title="probe",
            section="Guidelines",
            raw_text="- [guideline] **probe** — x.",
            ref_count=0,
            last_referenced=today - timedelta(days=50),
            created_date=today - timedelta(days=50),
            decay_state="active",
        )
        # 50d old: dormant at 45d, NOT dormant at 60d default.
        at_default = assess_decay([e], today)
        assert at_default == [], "50d entry must NOT be dormant at the 60d default"
        e2 = EntryMetadata(
            entry_type="guideline", title="probe", section="Guidelines",
            raw_text="- [guideline] **probe** — x.", ref_count=0,
            last_referenced=today - timedelta(days=50),
            created_date=today - timedelta(days=50), decay_state="active",
        )
        at_45 = assess_decay([e2], today, dormant_days=45)
        assert len(at_45) == 1, "50d entry MUST be dormant at the 45d MEMORY threshold"


class TestEpisodicWarStoryGate:
    """Step-6 MEMORY admission gate (run_117bcdf4): a REFLECT lesson that OPENS by
    narrating a single run-event is HELD BACK from the MEMORY hot path; a genuine
    semantic rule (even one citing a run as trailing attribution) is ADMITTED.

    Mutation contract: remove the Step-6 clause in _admit_lesson_to_memory →
    test_warstory_lessons_held_back goes RED (all 8 would re-ADMIT).
    """

    # Real archived war-stories (the 92-entry decay-archive source, 2026-07-28).
    WARSTORIES = [
        "Gate-2 caught a case-sensitivity false-drift: comparing a git SHA against a verbatim-stored anchor fires a false positive",
        "Gate-1 blocked a layer-2 that was head-position bias in disguise",
        "5th consecutive C042 catch this session: Gate-1 fresh-context skeptic BLOCKED a NEW wrapper function",
        "GUI122 RECURRED (mine, this run): I used git checkout to revert a mutation-test change",
        "Gate-2 又抓到 CLASS-A test-theater: 我自写的 cross-turn-bleed green test 是 vacuous",
        "M3 skeptic caught 2 CLASS-B framing errors pre-code",
        "adversarial gate corrected my root-cause NARRATIVE not just found bugs",
        "Gate-0 M3 skeptic flipped a WRONG-FRAME frontend-primary to backend-primary",
    ]
    # War-story shapes with a NON-opener actor position — verb-before-actor (passive)
    # and leading run-id / session deixis. The first cut (opener-anchored) MISSED
    # these (Gate-2 adversarial HIGH, run_117bcdf4); they must HOLD-BACK.
    WARSTORIES_NONOPENER = [
        "In run_x, Gate-2 caught a case-sensitivity false-drift",
        "Caught by Gate-2: a vacuous cross-turn test",
        "This session's 3rd catch: Gate-1 blocked a wrapper",
    ]
    # Genuine semantic rules — must be ADMITTED (run-id cited as attribution ≠ episodic).
    # The trailing block (RULES ABOUT THE GATING SYSTEM) are the Gate-2 adversarial
    # CRITICAL false-positives (run_117bcdf4): a gate ACTOR but NO run-event verb → a
    # reusable rule, not a war-story. A bare-topic detector wrongly dropped them;
    # keeping them here is the anti-test-theater guard (they exercise the FP branch).
    SEMANTIC = [
        "A fix that ADDS a liveness/verification check can REINTRODUCE the very false-positive it targets: make unknown a distinct safe state",
        "A new enum value on a widely-consumed type needs a GREP of every consumer, not just the primary one",
        "write→read mismatch is a recurring bug class — close BOTH halves in one change",
        "fail-closed has a silent-death twin — always pair it with a positive counter",
        "护栏的 verified 字段必须 isinstance(bool) 显式判类型,LLM 常把 bool 序列化成字符串绕过 is True",
        "Gate-2 must always run before merge; a fresh-context reviewer is the only defense against author bias",
        "Gate-1 blocks are cheaper than production incidents: invest in the skeptic pass",
        "adversarial gate design should assume the author is overconfident and wrong",
        "M3 skeptic passes must precede any code write in the pipeline",
    ]

    def test_detector_flags_warstories(self):
        from hooks.context_health_hook import _is_episodic_warstory
        for l in self.WARSTORIES + self.WARSTORIES_NONOPENER:
            assert _is_episodic_warstory(l), f"war-story not flagged: {l[:60]}"

    def test_detector_passes_semantic(self):
        from hooks.context_health_hook import _is_episodic_warstory
        for l in self.SEMANTIC:
            assert not _is_episodic_warstory(l), f"semantic wrongly flagged: {l[:60]}"

    def test_warstory_lessons_held_back(self, hook):
        # Full admission gate: qualified run, but war-story body → HOLD-BACK at Step 6.
        for l in self.WARSTORIES + self.WARSTORIES_NONOPENER:
            admit, reason, _ = hook._admit_lesson_to_memory(l, run_qualified=True)
            assert not admit, f"war-story ADMITTED: {l[:60]}"
            assert "episodic" in reason, f"wrong reject reason ({reason}): {l[:50]}"

    def test_semantic_lessons_admitted(self, hook):
        # Genuine rules from a qualified run must still ADMIT (no over-block).
        for l in self.SEMANTIC:
            admit, reason, _ = hook._admit_lesson_to_memory(l, run_qualified=True)
            assert admit, f"semantic HELD-BACK ({reason}): {l[:60]}"
