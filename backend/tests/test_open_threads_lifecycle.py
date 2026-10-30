"""Open COEs: live state rendered from the COE Registry plus undistilled DailyActivity.

Design B (CDF single writer): nothing writes COE threads into MEMORY.md's Open
Threads. ``core.coe_threads`` computes which COEs are open from the registry's
status (the baseline, folded by distillation from distilled days) and the COE
signals of the days not marked distilled; the briefing renders them as P0 items,
and a COE-keyed thread already in Open Threads follows that state. A thread is the
COE's thread only when its title has the same ``coe_key``, never shared words.
Deliverables never close a thread.
"""

from __future__ import annotations

import json
from pathlib import Path

from core import coe_threads
from core.proactive_intelligence import _merge_coe_threads, _parse_open_threads

MEMORY = """# Memory

## Open Threads

### P0 — Blocking
- 🔴 **streaming freeze + overall slowness** (reported 1x: 2026-10-24)
  Status: COE candidate — auto-promoted from DailyActivity.

### P1 — Important
- 🟡 **Frontend reconcile race** — guarded, prod-verify pending.

## COE Registry
- [pitfall] **streaming freeze + overall slowness** — 🔍 Investigating. Sessions: 2026-10-24 (2026-10-24)
  <!-- ref:0 | last:2026-10-24 | decay:active | source:auto -->
- [pitfall] **chat messages showing Interrupted chip** — 🔍 Investigating. Sessions: 2026-10-15 (2026-10-15)
  <!-- ref:0 | last:none | decay:active -->
- [pitfall] **cultivation self-deletion** — ✅ Resolved. Sessions: 2026-10-25 (2026-10-25)
  <!-- ref:0 | last:none | decay:active -->
- [pitfall] **Sev-1: data.db wiped** — post-mortem written by hand, no live status.
  <!-- ref:0 | last:none | decay:active | source:manual -->

## Other
"""


def _ws(tmp_path: Path, days: dict[str, list[tuple[str, str]]] | None = None,
        distilled: tuple[str, ...] = ()) -> Path:
    (tmp_path / ".context").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".context" / "MEMORY.md").write_text(MEMORY, encoding="utf-8")
    da = tmp_path / "Knowledge" / "DailyActivity"
    da.mkdir(parents=True, exist_ok=True)
    for day, sigs in (days or {}).items():
        flag = "true" if day in distilled else "false"
        (da / f"{day}.md").write_text(f"---\ndate: {day}\ndistilled: {flag}\n---\n",
                                      encoding="utf-8")
        (da / f"{day}.jsonl").write_text("".join(
            json.dumps({"coe_signal": s, "coe_topic": t}) + "\n" for s, t in sigs),
            encoding="utf-8")
    return tmp_path


def _open(ws: Path) -> list[str]:
    return [s.title for s in coe_threads.open_coes(ws)]


def test_the_registry_status_is_the_baseline(tmp_path: Path):
    ws = _ws(tmp_path)
    assert _open(ws) == ["streaming freeze + overall slowness",
                         "chat messages showing Interrupted chip"]
    # a hand-written post-mortem with no live status is a record, not an open problem
    assert "sev-1: data.db wiped" not in coe_threads.coe_states(ws)


def test_an_undistilled_resolution_closes_and_a_candidate_opens(tmp_path: Path):
    ws = _ws(tmp_path, {"2026-10-27": [("resolution", "Streaming freeze + overall slowness."),
                                       ("candidate", "demo Hive returns 401")]})
    assert _open(ws) == ["demo Hive returns 401", "chat messages showing Interrupted chip"]


def test_distilled_days_are_already_in_the_registry_and_not_read_again(tmp_path: Path):
    ws = _ws(tmp_path, {"2026-10-24": [("resolution", "streaming freeze + overall slowness")]},
             distilled=("2026-10-24",))
    assert "streaming freeze + overall slowness" in _open(ws)


def test_signals_fold_in_date_order_and_a_later_candidate_reopens(tmp_path: Path):
    ws = _ws(tmp_path, {"2026-10-26": [("resolution", "cultivation self-deletion")],
                        "2026-10-27": [("candidate", "cultivation self-deletion"),
                                       ("candidate", "cultivation self-deletion")]})
    st = coe_threads.coe_states(ws)["cultivation self-deletion"]
    assert st.open and st.reports == 2 and st.last == "2026-10-27"


def test_a_candidate_resolved_the_same_day_is_not_open(tmp_path: Path):
    ws = _ws(tmp_path, {"2026-10-27": [("candidate", "two-layer auth out of sync"),
                                       ("resolution", "two-layer auth out of sync")]})
    assert "two-layer auth out of sync" not in _open(ws)


def test_a_topic_sharing_words_is_not_the_same_coe(tmp_path: Path):
    ws = _ws(tmp_path, {"2026-10-27": [("resolution", "streaming slowness")]})
    assert "streaming freeze + overall slowness" in _open(ws)


def test_inner_bold_markers_do_not_change_the_key():
    assert coe_threads.coe_key("fix **inner** bug.") == coe_threads.coe_key("Fix inner bug")


def test_the_briefing_threads_follow_the_coe_state(tmp_path: Path):
    ws = _ws(tmp_path, {"2026-10-27": [("resolution", "streaming freeze + overall slowness")]})
    threads = _merge_coe_threads(_parse_open_threads(MEMORY), coe_threads.coe_states(ws))
    titles = [(t["priority"], t["title"], bool(t.get("coe"))) for t in threads]
    # the resolved COE's old thread is hidden; the open COE with no thread is a P0
    # item; a thread that is no COE is untouched
    assert titles == [("P0", "chat messages showing Interrupted chip", True),
                      ("P1", "Frontend reconcile race", False)]


def test_an_archived_undistilled_day_still_counts(tmp_path: Path):
    """After the cutover nothing marks a day distilled, and retention archives an
    undistilled day after 180 days (the .md moves to Knowledge/Archives/, the sidecar
    stays): its COE signals must still count. A day whose .md is gone everywhere
    is skipped."""
    ws = _ws(tmp_path, {"2026-04-01": [("candidate", "archived problem")],
                        "2026-04-02": [("candidate", "vanished problem")]})
    da = ws / "Knowledge" / "DailyActivity"
    (ws / "Knowledge" / "Archives").mkdir()
    (da / "2026-04-01.md").rename(ws / "Knowledge" / "Archives" / "2026-04-01.md")
    (da / "2026-04-02.md").unlink()
    assert "archived problem" in _open(ws)
    assert "vanished problem" not in _open(ws)






def test_open_problem_titles_come_from_the_coe_state(tmp_path: Path):
    from hooks.daily_activity_hook import _open_problem_titles
    ws = _ws(tmp_path)
    assert _open_problem_titles(ws) == ["streaming freeze + overall slowness",
                                        "chat messages showing Interrupted chip"]
    assert _open_problem_titles(tmp_path / "missing") == []


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




# ── the summariser reuses an open COE title ────────────────────────────────────

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
                                      open_next_items=[], open_problems=["streaming freeze + overall slowness"]))
    assert '"streaming freeze + overall slowness"' in seen["prompt"]
    assert "copy its title" in seen["prompt"] and "exactly" in seen["prompt"]


def test_summarize_passes_the_open_problems_to_the_enrichment(monkeypatch):
    import asyncio
    import core.summarization as sm
    seen = {}

    async def _enrich(self, summary, messages, *, open_next_items=None, open_problems=None,
                      **_kwargs):
        seen["open_problems"] = open_problems
        return summary
    monkeypatch.setattr(sm.SummarizationPipeline, "_enrich_with_llm", _enrich)
    msgs = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"message {i}"}
            for i in range(sm.LLM_ENRICHMENT_THRESHOLD + 2)]
    asyncio.run(sm.SummarizationPipeline().summarize(msgs, open_problems=["a problem"]))
    assert seen["open_problems"] == ["a problem"]


def test_parse_delivered_skips_the_empty_section_placeholder():
    from core.proactive_learning import parse_delivered
    text = "## 10:00 | s | t\n\n**Delivered:**\n- None recorded\n\n**Outputs:**\n- None recorded\n"
    assert parse_delivered(text) == []
