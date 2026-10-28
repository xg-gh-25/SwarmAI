"""MEMORY.md Open Threads: how a COE signal opens and closes a thread.

A COE ``candidate`` opens a P0 thread; a ``resolution`` closes the matching thread by
removing it (its history stays in the COE Registry). The thread is the same thread as
the COE when its title has the same ``coe_key`` — the key the COE Registry uses — never
because the two share some words. Deliverables never close a thread: matching titles
against deliverable lines by word overlap closed nothing right and one thing wrong on
real data, so that path is gone.
"""

from __future__ import annotations

from pathlib import Path

from hooks.distillation_hook import DistillationTriggerHook

apply = DistillationTriggerHook._apply_open_thread_updates

MEMORY = """# Memory

## Open Threads

### P0 — Blocking
- 🔴 **streaming freeze + overall slowness** (reported 1x: 2026-10-24)
  Status: COE candidate — auto-promoted from DailyActivity.
- 🔴 **chat messages showing Interrupted chip** (reported 1x: 2026-10-15)
  Status: COE candidate — auto-promoted from DailyActivity.

### P1 — Important
- 🟡 **Frontend reconcile race** — guarded, prod-verify pending.

## COE Registry
"""


def _threads(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.startswith(("- 🔴", "- 🟡", "- 🔵"))]


def test_a_resolution_with_no_thread_opens_nothing():
    out = apply(MEMORY, [("2026-10-27", "resolution", "Pipeline audit reporting 0/N findings closed")])
    assert out == MEMORY


def test_a_candidate_with_no_thread_opens_a_p0_thread():
    out = apply(MEMORY, [("2026-10-27", "candidate", "demo Hive returns 401 at app layer")])
    assert "- 🔴 **demo Hive returns 401 at app layer** (reported 1x: 2026-10-27)" in out
    p0 = out.index("### P0")
    assert p0 < out.index("demo Hive returns 401") < out.index("### P1")


def test_a_resolution_removes_its_thread_and_its_status_line():
    out = apply(MEMORY, [("2026-10-27", "resolution", "Streaming freeze + overall slowness.")])
    assert "streaming freeze" not in out
    assert "chat messages showing Interrupted chip" in out
    assert out.count("Status: COE candidate") == 1      # the other thread keeps its own
    assert "Frontend reconcile race" in out


def test_a_topic_sharing_words_with_a_thread_is_not_that_thread():
    """Word overlap ("streaming", "slowness") once made an unrelated COE bump or close
    another thread; only the same coe_key is the same thread."""
    out = apply(MEMORY, [("2026-10-27", "resolution", "streaming slowness")])
    assert "streaming freeze + overall slowness" in out
    out = apply(MEMORY, [("2026-10-27", "candidate", "streaming slowness")])
    assert "(reported 1x: 2026-10-24)" in out           # the existing thread is untouched
    assert "**streaming slowness** (reported 1x: 2026-10-27)" in out


def test_a_repeated_candidate_counts_on_its_own_thread():
    out = apply(MEMORY, [("2026-10-27", "candidate", "**Streaming freeze + overall slowness**")])
    assert "**streaming freeze + overall slowness** (reported 2x:" in out
    assert len(_threads(out)) == len(_threads(MEMORY))


def test_a_candidate_resolved_in_the_same_batch_opens_nothing():
    out = apply(MEMORY, [("2026-10-26", "candidate", "two-layer auth password out of sync"),
                         ("2026-10-27", "resolution", "two-layer auth password out of sync")])
    assert out == MEMORY


def test_deliverables_no_longer_close_threads():
    assert not hasattr(DistillationTriggerHook, "_reconcile_open_threads_from_deliverables")
    assert not hasattr(DistillationTriggerHook, "_apply_deliverable_reconciliation")


# ── one parser for the DailyActivity "Delivered" sections ──────────────────────

DA = """---
date: 2026-10-27
---
## 10:00 | aaa | first session
**Delivered:**
- shipped the open-thread fix
- wrote the HLD
**Lessons:**
- not a deliverable
## 11:00 | bbb | second session
**Delivered:**
- published to Pippin
**Next:** something
"""


def test_parse_delivered_reads_every_session_block():
    from core.proactive_learning import parse_delivered
    assert parse_delivered(DA) == ["shipped the open-thread fix", "wrote the HLD",
                                   "published to Pippin"]


def test_parse_delivered_reads_the_older_header_too():
    from core.proactive_learning import parse_delivered
    old = "## 09:00 | x | s\n**Deliverables:**\n- old one\n### Next\n- not it\n"
    assert parse_delivered(old) == ["old one"]


def test_briefing_hints_see_the_delivered_lines(tmp_path: Path):
    """The briefing's deliverables (used to drop finished continue-hints) were read with
    a header the writer no longer emits, so they were always empty."""
    from core.proactive_intelligence import _extract_recent_deliverables
    (tmp_path / "2026-10-27.md").write_text(DA, encoding="utf-8")
    assert _extract_recent_deliverables(tmp_path) == [
        "shipped the open-thread fix", "wrote the hld", "published to pippin"]


def test_effectiveness_scoring_sees_the_delivered_lines(tmp_path: Path, monkeypatch):
    import core.proactive_intelligence as pi
    seen = {}

    class _State:
        last_briefing_suggested = ["fix the open threads"]
        effectiveness: dict = {}

    monkeypatch.setattr(pi, "_load_learning_state", lambda ws: _State())
    monkeypatch.setattr(pi, "_save_learning_state", lambda ws, st: None)
    monkeypatch.setattr(pi, "_update_effectiveness",
                        lambda st, sugg, deliv: seen.setdefault("deliv", list(deliv)))
    da = tmp_path / "2026-10-27.md"
    da.write_text(DA, encoding="utf-8")
    DistillationTriggerHook._update_effectiveness_scoring([da], tmp_path)
    assert seen["deliv"] == ["shipped the open-thread fix", "wrote the HLD", "published to Pippin"]


# ── review round 1 ─────────────────────────────────────────────────────────────

def test_a_title_with_inner_bold_markers_is_found_again():
    out = apply(MEMORY, [("2026-10-26", "candidate", "fix **inner** bug")])
    assert "- 🔴 **fix inner bug** (reported 1x: 2026-10-26)" in out
    out = apply(out, [("2026-10-27", "candidate", "fix **inner** bug")])
    assert "**fix inner bug** (reported 2x: 2026-10-26, 2026-10-27)" in out
    assert apply(out, [("2026-10-28", "resolution", "fix **inner** bug")]).count("inner") == 0


def test_a_bump_adds_the_new_date():
    out = apply(MEMORY, [("2026-10-27", "candidate", "streaming freeze + overall slowness")])
    assert "(reported 2x: 2026-10-24, 2026-10-27)" in out


def test_only_the_open_threads_section_is_searched():
    text = "## Notes\n- 🔴 **a bullet elsewhere** — not a thread\n\n" + MEMORY
    assert apply(text, [("2026-10-27", "resolution", "a bullet elsewhere")]) == text
    out = apply(text, [("2026-10-27", "candidate", "a bullet elsewhere")])
    assert "**a bullet elsewhere** — not a thread" in out          # untouched
    assert "**a bullet elsewhere** (reported 1x: 2026-10-27)" in out  # its own thread


def test_a_resolution_removes_a_body_with_blank_lines_inside():
    text = MEMORY.replace(
        "- 🟡 **Frontend reconcile race** — guarded, prod-verify pending.\n",
        "- 🟡 **Frontend reconcile race** — guarded, prod-verify pending.\n"
        "  first paragraph\n\n  **second paragraph of the same thread**\n")
    out = apply(text, [("2026-10-27", "resolution", "Frontend reconcile race")])
    assert "reconcile race" not in out and "paragraph" not in out
    assert "## COE Registry" in out


def test_the_summariser_is_asked_to_reuse_an_open_problem_title(monkeypatch):
    import core.summarization as sm
    from core import executors
    seen = {}

    async def _run_in(pool, fn, prompt):
        seen["prompt"] = prompt
        return {}
    monkeypatch.setattr(executors, "run_in", _run_in)
    import asyncio
    pipe = sm.SummarizationPipeline()
    summary = sm.StructuredSummary(topics=[], decisions=[], files_modified=[],
                                   open_questions=[], session_title="t", timestamp="10:00")
    msgs = [{"role": "user", "content": "the streaming froze again"},
            {"role": "assistant", "content": "found the stall"}]
    asyncio.run(pipe._enrich_with_llm(summary, msgs,
                                      open_problems=["streaming freeze + overall slowness"]))
    assert '"streaming freeze + overall slowness"' in seen["prompt"]
    assert "copy that title exactly" in seen["prompt"]


def test_open_problem_titles_come_from_memory(tmp_path: Path):
    from hooks.daily_activity_hook import _open_problem_titles
    (tmp_path / ".context").mkdir()
    (tmp_path / ".context" / "MEMORY.md").write_text(MEMORY, encoding="utf-8")
    assert _open_problem_titles(tmp_path) == [
        "streaming freeze + overall slowness", "chat messages showing Interrupted chip",
        "Frontend reconcile race"]
    assert _open_problem_titles(tmp_path / "missing") == []


def test_summarize_passes_the_open_problems_to_the_enrichment(monkeypatch):
    import asyncio
    import core.summarization as sm
    seen = {}

    async def _enrich(self, summary, messages, *, open_problems=None):
        seen["open_problems"] = open_problems
        return summary
    monkeypatch.setattr(sm.SummarizationPipeline, "_enrich_with_llm", _enrich)
    msgs = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"message {i}"}
            for i in range(sm.LLM_ENRICHMENT_THRESHOLD + 2)]
    asyncio.run(sm.SummarizationPipeline().summarize(msgs, open_problems=["a problem"]))
    assert seen["open_problems"] == ["a problem"]
