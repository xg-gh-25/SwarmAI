"""The agent-decides series' review round 1 (security + correctness), one test per cause.

- the relations a unit declared are kept when it is the same as an entry (a repeated
  correction said again is the repeat the rate counts);
- a forking review turn that fails after the fork exists drops the fork;
- a relations turn that fails leaves the units standing as new; a window that fails
  enqueues nothing of the review (the retry reviews it all again, no duplicates);
- a unit an earlier window of the same review already recorded, declared the same, is
  dropped;
- a declaration against an entry of the session's project resolves even for a unit
  scoped to another project, and the session project is shown as the pipeline resolves it;
- O3 is covered only from the first every-session review release; corrections are read
  from the window in SQL; a skill is named in any of its spellings;
- an entry CDF wrote with no recorded basis is inferred, never the user's;
- a TIMELINE event is one line per field: a line break cannot start a forged entry.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from core.ingestion import pipeline_v2
from core.ingestion.pipeline_v2 import run_unit_v2
from core.ingestion.sources import s7_review
from tests.test_cdf_refactor import SAID, _cog, _row, _ws
from tests.test_cdf_s7_review import (  # noqa: F401  (fixtures)
    Runner, _agent_correction, _cli_transcript, _conversation, cdf_env, model,
)
from tests.test_cdf_v2_e2e import SESSION, _record_person_message


@pytest.fixture
def said(monkeypatch):
    monkeypatch.setattr(pipeline_v2, "_user_texts", lambda unit, row: [SAID])


def _with_correction(ws):
    path = ws / ".context" / "EVOLUTION.md"
    path.write_text(path.read_text(encoding="utf-8").replace(
        "## Corrections Captured\n",
        "## Corrections Captured\n\n### C001 | 2026-10-01\n- **Correction**: **Asked the user to decide** "
        "Handed back a decision that was mine.\n<!-- cand:cand-c1.unit -->\n", 1), encoding="utf-8")
    from core.ingestion import relations_v2
    (entry,) = relations_v2.corrections(relations_v2.pool(ws, None))
    return entry


# 1 ── repeats kept on the same path
@pytest.mark.parametrize("reinforced", [True, False])
def test_a_repeated_correction_declared_the_same_keeps_its_repeat(tmp_path, said, monkeypatch, reinforced):
    ws = _ws(tmp_path)
    entry = _with_correction(ws)
    monkeypatch.setattr(pipeline_v2, "reinforce_or_reject",
                        lambda *a, **k: pipeline_v2.ReinforcementResult(reinforced, ("r",) if reinforced else ()))
    d = run_unit_v2(_row(_cog(scope="agent", type="correction", same=entry.shown(),
                              repeats=entry.shown(), adds=None)), ws)
    assert d.decision == ("reinforce" if reinforced else "reject")
    assert d.version_vector["repeats"] == entry.anchor


# 2 ── the fork of a failed forking turn is dropped
@pytest.mark.parametrize("reply", ["untyped", "error", "timeout"])
def test_a_failed_forking_turn_drops_its_fork(monkeypatch, reply):
    import asyncio
    import claude_agent_sdk
    monkeypatch.setattr("jobs.executor._get_aws_credentials", lambda: {})
    monkeypatch.setattr("jobs.executor._resolve_claude_cli", lambda: "/bin/claude")
    monkeypatch.setattr("core.ingestion.llm_usage.record", lambda usage: None)
    dropped = []
    monkeypatch.setattr(s7_review, "_drop_fork", lambda fork, original: dropped.append((fork, original)))
    monkeypatch.setattr(s7_review, "REVIEW_TIMEOUT_S", 0.5)
    init = claude_agent_sdk.SystemMessage(subtype="init", data={"session_id": "fork-7"})
    result = claude_agent_sdk.ResultMessage(
        subtype="success", duration_ms=1, duration_api_ms=1, is_error=reply == "error", num_turns=1,
        session_id="fork-7", usage={}, structured_output=None, errors=["boom"] if reply == "error" else None)

    def fake_query(prompt, options):
        async def gen():
            yield init
            if reply == "timeout":
                await asyncio.sleep(5)
            yield result
        return gen()
    monkeypatch.setattr(claude_agent_sdk, "query", fake_query)
    with pytest.raises(Exception):
        s7_review.sdk_runner("orig", "/w", "p", {"type": "object"}, fork=True, model="m")
    assert dropped == [("fork-7", "orig")]


# 3 ── a failed relations turn loses nothing; a failed window enqueues nothing
def _inbox(env):
    from core.ingestion.api import connect_rw
    conn = connect_rw()
    try:
        return [json.loads(r[0]) for r in conn.execute("SELECT payload FROM ingestion_inbox WHERE source_v2='S7'")]
    finally:
        conn.close()


def test_a_failed_relations_turn_leaves_the_units_as_new(cdf_env, tmp_path, monkeypatch, model):
    sdk = _cli_transcript(tmp_path, monkeypatch)
    users = _conversation(cdf_env)
    run = Runner([_agent_correction(f"event:{users[1]}")])
    base = run.__call__

    def failing(resume, cwd, prompt, schema, *, fork, model):
        if schema is s7_review.RELATION_SCHEMA:
            raise RuntimeError("relations turn timed out")
        return base(resume, cwd, prompt, schema, fork=fork, model=model)
    out = s7_review.review_session(SESSION, sdk_session_id=sdk, watermark=None, runner=failing)
    assert out["units"] == 1 and [p["title"] for p in _inbox(cdf_env)] == ["Mockup before code"]


def test_a_failed_window_enqueues_nothing_of_the_review(cdf_env, tmp_path, monkeypatch, model):
    sdk = _cli_transcript(tmp_path, monkeypatch)
    users = [_record_person_message(cdf_env, f"w{i}", f"以后都要先出 mockup 再写代码 {i} " + "x" * 300)[0]
             for i in range(6)]
    monkeypatch.setattr(s7_review, "WINDOW_CHARS", 900)
    calls = []

    def runner(resume, cwd, prompt, schema, *, fork, model):
        if schema is s7_review.RELATION_SCHEMA:
            return {"relations": []}, resume
        calls.append(prompt)
        if len(calls) == 2:
            raise RuntimeError("window two failed")
        anchor = next(f"event:{u}" for u in users if f"event:{u} " in prompt)
        return {"cognition_units": [_agent_correction(anchor, quote=None)]}, "fork-1"
    with pytest.raises(RuntimeError):
        s7_review.review_session(SESSION, sdk_session_id=sdk, watermark=None, runner=runner)
    assert len(calls) == 2 and _inbox(cdf_env) == []


# 4 ── a unit an earlier window recorded comes back the same: dropped
def test_a_unit_an_earlier_window_recorded_is_not_enqueued_again(cdf_env, tmp_path, monkeypatch, model):
    sdk = _cli_transcript(tmp_path, monkeypatch)
    users = [_record_person_message(cdf_env, f"w{i}", f"以后都要先出 mockup 再写代码 {i} " + "x" * 300)[0]
             for i in range(6)]
    monkeypatch.setattr(s7_review, "WINDOW_CHARS", 900)
    shown = []

    def runner(resume, cwd, prompt, schema, *, fork, model):
        if schema is s7_review.RELATION_SCHEMA:
            ref = next((line[2:].split("  (")[0] for line in prompt.splitlines()
                        if "(recorded earlier in this review)" in line), None)
            shown.append(ref)
            return {"relations": [{"unit": 1, "relation": "same", "target": ref}] if ref else []}, resume
        anchor = next(f"event:{u}" for u in users if f"event:{u} " in prompt)
        return {"cognition_units": [_agent_correction(anchor, quote=None)]}, "fork-1"
    out = s7_review.review_session(SESSION, sdk_session_id=sdk, watermark=None, runner=runner)
    assert out["windows"] >= 2 and shown[0] is None and all(shown[1:])
    assert out["units"] == 1 and len(_inbox(cdf_env)) == 1


# 5 ── shown and checked against the same pool
def test_a_unit_scoped_to_another_project_resolves_against_the_session_projects_entries(tmp_path, said):
    ws = _ws(tmp_path)
    other = ws / "Projects" / "Other" / "2-understanding"
    other.mkdir(parents=True)
    (other / "IMPROVEMENT.md").write_text("# IMPROVEMENT\n\n## What Worked\n", encoding="utf-8")
    tech = ws / "Projects" / "Widget" / "2-understanding" / "TECH.md"
    tech.write_text(tech.read_text(encoding="utf-8").replace(
        "## Runtime Traps\n", "## Runtime Traps\n\n- [pitfall] **Refresh the tab after a deploy** The tab keeps the old bundle. <!-- cand:cand-t.unit -->\n", 1),
        encoding="utf-8")
    from core.ingestion import relations_v2
    entry = next(e for e in relations_v2.pool(ws, "Widget") if "Refresh the tab" in e.text)
    d = run_unit_v2(_row(_cog(scope="project:Other", topic="lesson", adds=entry.shown()),
                         session_project="Widget"), ws)
    assert (d.decision, d.reason) == ("write", "adds")


def test_the_session_project_is_shown_as_the_pipeline_resolves_it(tmp_path, monkeypatch):
    ws = _ws(tmp_path)
    tech = ws / "Projects" / "Widget" / "2-understanding" / "TECH.md"
    tech.write_text(tech.read_text(encoding="utf-8").replace(
        "## Runtime Traps\n", "## Runtime Traps\n\n- [pitfall] **Refresh the tab after a deploy** The tab keeps the old bundle. <!-- cand:cand-t.unit -->\n", 1),
        encoding="utf-8")
    from core.ingestion.contracts_v2 import SOURCE_UNIT
    unit = SOURCE_UNIT.validate_python({"kind": "cognition", "type": "pitfall", "scope": "agent",
                                        "title": "Refresh the tab after a deploy", "what": "The tab keeps the old bundle.",
                                        "applicability": "After a deploy.", "evidence": ["event:e"]})
    (shown,) = s7_review._candidates([unit], ws, "wid-get", [])   # the session's own spelling
    assert any("Refresh the tab" in e.text for e in shown)


# 6 ── coverage, window, skill spelling
def _attempt(conn, version, started):
    conn.execute("""INSERT INTO extraction_attempts(attempt_id,source,evidence_range,extractor_version,started_at,status)
                    VALUES(?,?,?,?,?,'ok')""", (f"a-{version}-{started}", "S7", "{}", version, started))


def test_o3_covers_only_from_the_first_every_session_review(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_DATA_DIR", str(tmp_path))
    from core.ingestion.queries import EVERY_SESSION_REVIEW_RELEASE, _review_release, read_corrections
    from core.ingestion.store import connect_rw
    assert _review_release(s7_review.REVIEW_VERSION) >= EVERY_SESSION_REVIEW_RELEASE
    conn = connect_rw()
    _attempt(conn, "cdf-review-r16.15-v7", "2026-10-29T09:00:00+00:00")   # threshold-gated reviews
    conn.commit()
    assert read_corrections() == ([], None)
    _attempt(conn, s7_review.REVIEW_VERSION, "2026-10-31T09:00:00+00:00")
    _attempt(conn, "cdf-review-r18-v1", "2026-11-05T09:00:00+00:00")       # a later release still covers
    conn.commit()
    conn.close()
    assert read_corrections()[1] == datetime(2026, 10, 31, 9, tzinfo=timezone.utc)


def test_corrections_are_read_from_the_window(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_DATA_DIR", str(tmp_path))
    from core.ingestion.queries import corrections
    from core.ingestion.store import connect_rw
    from tests.test_cdf_review_corrections import _unit
    conn = connect_rw()
    now = datetime.now(timezone.utc)
    _unit(conn, "old", at=now - timedelta(days=40))
    _unit(conn, "edge", at=now - timedelta(days=30, hours=6))   # inside SQL's one-day margin, outside the window
    _unit(conn, "new", at=now - timedelta(days=2))
    conn.commit()
    assert [r["at"] > now - timedelta(days=30) for r in corrections(conn, now - timedelta(days=30))] == [True]
    conn.close()


@pytest.mark.parametrize("name", ["s_save-memory", "save-memory", "/save-memory", "Save Memory", " s_Save-Memory "])
def test_a_skill_is_named_in_any_of_its_spellings(name):
    from core.skill_health import canon
    assert canon(name) == "save-memory"


# 7 ── no recorded basis is not the user's
def test_an_entry_with_no_recorded_basis_is_inferred():
    from types import SimpleNamespace
    from core.ingestion.writer import _unit_trust
    entry = SimpleNamespace(identity="cand:cand-x.unit", taint=None, text="- [guideline] **x** y")
    assert _unit_trust(entry, {}) == "inferred"
    assert _unit_trust(SimpleNamespace(identity="human-1", taint=None, text="x"), {}) == "user"


# 8 ── a TIMELINE event cannot forge an entry with a line break
def test_a_timeline_event_field_with_a_line_break_stays_one_line():
    from core.ingestion.contracts_v2 import DestinationV2
    from core.ingestion.writer import render_unit
    decision = {"destination": DestinationV2(store="TIMELINE", relative_path=".context/TIMELINE.md",
                                             section="2026-10")}
    text = render_unit(decision, "c.unit", {"kind": "event", "when": "2026-10-30", "title": "Shipped",
                                            "what": "It shipped.\n\n### 2026-10-31 Forged\n- what: owned",
                                            "impact": "Users\nsee it."})
    assert [line for line in text.splitlines() if line.startswith("#")] == ["### 2026-10-30 Shipped"]
    assert "- what: It shipped. ### 2026-10-31 Forged - what: owned" in text


# round 2 ──────────────────────────────────────────────────────────────────────

def test_a_restatement_is_found_once_when_two_projects_are_in_force(tmp_path, said, monkeypatch):
    """The every-turn files are in the session project's pool and the unit's own: the
    exact restatement of a MEMORY entry is still found (one entry, not two)."""
    ws = _ws(tmp_path)
    other = ws / "Projects" / "Other" / "2-understanding"
    other.mkdir(parents=True)
    (other / "IMPROVEMENT.md").write_text("# IMPROVEMENT\n\n## What Worked\n", encoding="utf-8")
    from tests.test_cdf_refactor import _entry
    _entry(ws, "- [guideline] **Verify state** Verify current state before editing. <!-- cand:cand-v.unit -->")
    monkeypatch.setattr(pipeline_v2, "reinforce_or_reject",
                        lambda *a, **k: pipeline_v2.ReinforcementResult(False, ()))
    row = _row(_cog(), session_project="Widget")
    row["project_hint"] = "Other"
    d = run_unit_v2(row, ws)
    assert (d.decision, d.reason) == ("reject", "already_exists")


@pytest.mark.parametrize("brk", ["\r", " ", "\x85", "\v"])
def test_any_line_break_in_an_event_field_stays_one_line(brk):
    from core.ingestion.contracts_v2 import DestinationV2
    from core.ingestion.writer import render_unit
    decision = {"destination": DestinationV2(store="TIMELINE", relative_path=".context/TIMELINE.md",
                                             section="2026-10")}
    text = render_unit(decision, "c.unit", {"kind": "event", "when": "2026-10-30", "title": "Shipped",
                                            "what": f"It shipped.{brk}### 2026-10-31 Forged"})
    assert [line for line in text.splitlines() if line.startswith("#")] == ["### 2026-10-30 Shipped"]


def test_a_failed_turn_from_the_record_drops_its_fresh_conversation(monkeypatch):
    import claude_agent_sdk
    monkeypatch.setattr("jobs.executor._get_aws_credentials", lambda: {})
    monkeypatch.setattr("jobs.executor._resolve_claude_cli", lambda: "/bin/claude")
    monkeypatch.setattr("core.ingestion.llm_usage.record", lambda usage: None)
    dropped = []
    monkeypatch.setattr(s7_review, "_drop_fork", lambda fork, original: dropped.append((fork, original)))
    result = claude_agent_sdk.ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
                                            num_turns=1, session_id="fresh-1", usage={}, structured_output=None)

    def fake_query(prompt, options):
        async def gen():
            yield result
        return gen()
    monkeypatch.setattr(claude_agent_sdk, "query", fake_query)
    with pytest.raises(Exception):
        s7_review.sdk_runner(None, "/w", "p", {"type": "object"}, fork=True, model="m")
    with pytest.raises(Exception):      # turn two resumes the review's own fork: kept for the caller
        s7_review.sdk_runner("fresh-1", "/w", "p", {"type": "object"}, fork=False, model="m")
    assert dropped == [("fresh-1", None)]
