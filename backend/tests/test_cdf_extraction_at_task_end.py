"""Extraction at task end (docs/design/2026-10-31-extraction-at-task-end.md).

- the idle review waits 60 quiet minutes, pushed back by activity but never past
  MAX_WAIT after the first request of the wait (E1);
- a later review pass is shown what this session's earlier passes wrote, TIMELINE
  events included, marked as its own; an event is compared with events (E2);
- an event may retire an event or gap its own session wrote earlier, nothing else; a
  gap so retired is closed, an event overridden (E3);
- an automated overridden withdrawal reaches TIMELINE, CDF-written entries only (E3);
- the review prompt no longer says the conversation is over (E4).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from tests.test_cdf_v2_e2e import cdf_env  # noqa: F401  (fixture)

_EV_OLD = ("### 2026-10-31 Root cause found for the summary bug\n"
           "- what: The summary was cut at its cap.\n- impact: Rows lost.\n"
           "<!-- cand:cand-aaaaaaaaaaaaaaaaaaaaaaaa.unit | event_key:k1 | source:S7 | event_kind:event -->\n")
_EV_OTHER = ("### 2026-10-31 Release notes published for version 2.4\n"
             "- what: Notes went out.\n- impact: Users know.\n"
             "<!-- cand:cand-bbbbbbbbbbbbbbbbbbbbbbbb.unit | event_key:k2 | source:S7 | event_kind:event -->\n")
_EV_HUMAN = "### 2026-10-31 Kickoff meeting held with the platform team\n- what: Met.\n- impact: Plan.\n"


def _timeline(cdf_env, *events):
    path = cdf_env["workspace"] / ".context" / "TIMELINE.md"
    path.write_text("# Timeline\n\n## 2026-10\n\n" + "\n".join(events), encoding="utf-8")
    return path


def _cand(conn, cid, session, kind="event"):
    conn.execute("INSERT INTO ingestion_inbox(id,idem_key,source,source_ref,lane,payload,payload_schema,"
                 "content_hash,envelope,project_snapshot,created_at,source_v2,extraction_pass,unit_kind) "
                 "VALUES (?,?,'S7',?,'session','{}','v','h','{}','{}','2026-10-31T00:00:00','S7','main',?)",
                 (cid, cid, session, kind))
    conn.execute("INSERT INTO ingestion_units(unit_id,candidate_id,claim_group,state,action,reason,"
                 "updated_at) VALUES (?,?,'{}','written','add','new','2026-10-31T00:00:00')",
                 (cid + ".unit", cid))
    conn.commit()


def _ref(cdf_env, title):
    from core.ingestion import relations_v2
    entry = next(e for e in relations_v2.timeline_entries(cdf_env["workspace"]) if title in e.text)
    return entry.shown(), entry.anchor


# E1 ── timing

def test_the_review_waits_an_hour_and_is_capped_at_max_wait(cdf_env, monkeypatch):
    from core.ingestion import s2_jobs
    from core.ingestion.api import connect_rw
    monkeypatch.setattr(s2_jobs, "_WAKER", None)
    assert s2_jobs.REVIEW_DELAY >= timedelta(minutes=55)
    conn = connect_rw()
    try:
        s2_jobs.request("s1", "session_end", delay=s2_jobs.REVIEW_DELAY)
        first = conn.execute("SELECT requested_at FROM ingestion_s2_jobs").fetchone()[0]
        # activity long after: the not-before may not run past MAX_WAIT after the first ask
        s2_jobs.request("s1", "session_end", delay=timedelta(hours=10))
        nb = conn.execute("SELECT next_attempt_at FROM ingestion_s2_jobs").fetchone()[0]
    finally:
        conn.close()
    assert datetime.fromisoformat(nb) <= datetime.fromisoformat(first) + s2_jobs.MAX_WAIT


def test_a_rerun_after_again_starts_a_new_wait(cdf_env, monkeypatch):
    from core.ingestion import s2_jobs
    from core.ingestion.api import connect_rw
    monkeypatch.setattr(s2_jobs, "_WAKER", None)
    conn = connect_rw()
    try:
        s2_jobs.request("s1", "save_button")
        conn.execute("UPDATE ingestion_s2_jobs SET requested_at='2020-01-01T00:00:00+00:00'")
        conn.commit()
        s2_jobs.claim(conn)
        s2_jobs.request("s1", "session_end", delay=s2_jobs.REVIEW_DELAY)   # sets again
        assert s2_jobs.finish(conn, "s1") == "queued"
        req = conn.execute("SELECT requested_at FROM ingestion_s2_jobs").fetchone()[0]
    finally:
        conn.close()
    assert datetime.fromisoformat(req) > datetime.now(timezone.utc) - timedelta(minutes=1)


def test_the_prompt_no_longer_says_the_conversation_is_over():
    from core.ingestion.sources import s7_review
    prompt = s7_review.build_prompt(("cognition", "event", "gap"), {}, "", "record")
    assert "is over for now" not in prompt
    assert "quiet for an hour" in prompt and "not pushed" in prompt


# E2 ── a pass sees its session's earlier entries

def test_earlier_units_include_this_sessions_timeline_events(cdf_env):
    from core.ingestion.api import connect_rw
    from core.ingestion.sources import s7_review
    _timeline(cdf_env, _EV_OLD, _EV_OTHER)
    conn = connect_rw()
    try:
        _cand(conn, "cand-aaaaaaaaaaaaaaaaaaaaaaaa", "sess-1")
        _cand(conn, "cand-bbbbbbbbbbbbbbbbbbbbbbbb", "sess-2")
    finally:
        conn.close()
    got = s7_review._earlier_units("sess-1", cdf_env["workspace"], "P")
    assert [e.head for e in got] == ["### 2026-10-31 Root cause found for the summary bug"]
    shown, anchor = _ref(cdf_env, "Root cause")
    text = s7_review.relation_prompt([type("U", (), {"kind": "event", "title": "Summary bug fixed",
                                                      "what": "x"})()], [got], {anchor})
    assert f"{shown}" in text and "(this session)" in text


def test_an_event_is_compared_with_events(cdf_env):
    from core.ingestion.sources import s7_review
    from core.ingestion.contracts_v2 import EventUnit
    _timeline(cdf_env, _EV_OLD)
    unit = EventUnit(when="2026-10-31", title="Summary cap fixed", what="The summary cap bug is fixed",
                     impact="rows whole", evidence=["event:input:x"], scope="project:P")
    (shown,) = s7_review._candidates([unit], cdf_env["workspace"], "P", [])
    assert any("Root cause found" in e.text for e in shown)


# E3 ── an event retires its own session's earlier event or gap, nothing else

def _own(conn, cdf_env, declared, session="sess-1", source="S7", cited_external=False):
    from core.ingestion import overrides
    return overrides.check_session_own(conn, workspace=cdf_env["workspace"], project="P",
                                       source=source, declared=declared, session_ref=session,
                                       cited_external=cited_external)


def test_an_event_may_retire_only_its_own_sessions_cdf_events(cdf_env):
    from core.ingestion.api import connect_rw
    _timeline(cdf_env, _EV_OLD, _EV_OTHER, _EV_HUMAN)
    conn = connect_rw()
    try:
        _cand(conn, "cand-aaaaaaaaaaaaaaaaaaaaaaaa", "sess-1")
        _cand(conn, "cand-bbbbbbbbbbbbbbbbbbbbbbbb", "sess-2")
        own_ref, own_anchor = _ref(cdf_env, "Root cause")
        other_ref, _ = _ref(cdf_env, "Release notes")
        human_ref, _ = _ref(cdf_env, "Kickoff")
        ok = _own(conn, cdf_env, [own_ref])
        refused = _own(conn, cdf_env, [other_ref, human_ref])
        s3 = _own(conn, cdf_env, [own_ref], source="S3")
        external = _own(conn, cdf_env, [own_ref], cited_external=True)
    finally:
        conn.close()
    assert ok.targets == [own_anchor]
    assert [r["reason"] for r in refused.refused] == ["other_session", "human_written"]
    assert s3.targets == [] and external.targets == []


def test_a_retired_gap_is_closed_and_a_retired_event_overridden(cdf_env):
    from core.ingestion import overrides, withdrawn
    from core.ingestion.api import connect_rw
    from core.ingestion.units import parse_units
    tl = _timeline(cdf_env, _EV_OLD)
    imp = cdf_env["ddd"] / "IMPROVEMENT.md"
    imp.write_text("# IMPROVEMENT\n\n## What to Watch For\n\n"
                   "- [guideline] **Summary fix not done yet** the cap. gap kind: follow_up "
                   "<!-- cand:cand-cccccccccccccccccccccccc.unit -->\n"
                   "- [guideline] **Another open gap a person wrote** stays.\n", encoding="utf-8")
    ws = cdf_env["workspace"]
    ev_anchor = parse_units(tl, root=ws)[0].anchor
    gap_anchor = next(u.anchor for u in parse_units(imp, root=ws) if "not done yet" in u.text)
    conn = connect_rw()
    try:
        _cand(conn, "cand-aaaaaaaaaaaaaaaaaaaaaaaa", "sess-1")
        _cand(conn, "cand-cccccccccccccccccccccccc", "sess-1", kind="gap")
        _cand(conn, "cand-dddddddddddddddddddddddd", "sess-1")
        conn.execute("UPDATE ingestion_units SET decision=? WHERE unit_id='cand-dddddddddddddddddddddddd.unit'",
                     (json.dumps({"version_vector": {"overrides": [ev_anchor, gap_anchor],
                                                      "override_closes": [gap_anchor]}}),))
        conn.execute("UPDATE ingestion_inbox SET payload=? WHERE id='cand-dddddddddddddddddddddddd'",
                     (json.dumps({"title": "Summary bug fixed"}),))
        conn.commit()
        retired = overrides.apply(conn, "cand-dddddddddddddddddddddddd.unit")
        results = [json.loads(r[0]) for r in conn.execute(
            "SELECT detail FROM ingestion_events WHERE kind='override_applied'")]
        assert retired == 2, results
        statuses = sorted(r.status for r in withdrawn.records(conn))
    finally:
        conn.close()
    assert "Root cause found" not in tl.read_text(encoding="utf-8")
    assert "not done yet" not in imp.read_text(encoding="utf-8")
    assert statuses == ["closed", "overridden"]


def test_an_automated_withdrawal_reaches_timeline_only_for_cdf_entries(cdf_env):
    from core.ingestion import governed_edit
    from core.ingestion.api import connect_rw
    from core.ingestion.units import parse_units
    connect_rw().close()
    tl = _timeline(cdf_env, _EV_HUMAN, _EV_OLD)
    units = parse_units(tl, root=cdf_env["workspace"])
    human = next(u.anchor for u in units if "Kickoff" in u.text)
    cdf = next(u.anchor for u in units if "Root cause" in u.text)
    with pytest.raises(PermissionError):
        governed_edit.withdraw(human, "x", actor="cdf:override", status="overridden")
    with pytest.raises(PermissionError):
        governed_edit.withdraw(cdf, "x", actor="cdf:override", status="closed")
    governed_edit.withdraw(cdf, "replaced", actor="cdf:override", status="overridden")
    text = tl.read_text(encoding="utf-8")
    assert "Kickoff" in text and "Root cause" not in text


def _event(**over):
    value = {"kind": "event", "when": "2026-10-31", "title": "Summary cap bug fixed and rows repaired",
             "what": "The summary answer is no longer cut; the history was rebuilt.",
             "impact": "Rows say what was done.", "evidence": ["event:e1"], "scope": "project:P"}
    value.update(over)
    return value


def test_the_pipeline_drops_an_event_same_as_a_recorded_one(cdf_env):
    from core.ingestion.api import connect_rw
    from core.ingestion.pipeline_v2 import run_unit_v2
    from tests.test_cdf_refactor import _row
    connect_rw().close()
    _timeline(cdf_env, _EV_OLD)
    ref, _ = _ref(cdf_env, "Root cause")
    decision = run_unit_v2(_row(_event(same=ref), source="S7", tainted=False, session_project="P"),
                           cdf_env["workspace"])
    assert (decision.decision, decision.reason) == ("reject", "already_exists")


def test_the_pipeline_records_an_events_own_session_override(cdf_env):
    from core.ingestion.api import connect_rw
    from core.ingestion.pipeline_v2 import run_unit_v2
    from tests.test_cdf_refactor import _row
    _timeline(cdf_env, _EV_OLD, _EV_OTHER)
    conn = connect_rw()
    try:
        _cand(conn, "cand-aaaaaaaaaaaaaaaaaaaaaaaa", "sess-1")
        _cand(conn, "cand-bbbbbbbbbbbbbbbbbbbbbbbb", "sess-2")
    finally:
        conn.close()
    own, own_anchor = _ref(cdf_env, "Root cause")
    other, _ = _ref(cdf_env, "Release notes")
    decision = run_unit_v2(_row(_event(overrides=[own, other]), source="S7", tainted=False,
                                session_project="P", source_ref="sess-1"), cdf_env["workspace"])
    assert decision.decision == "write"
    assert decision.version_vector.get("overrides") == [own_anchor]
    assert [r["reason"] for r in decision.version_vector["override_refused"]] == ["other_session"]


def test_a_gap_status_never_leaks_to_a_target_not_named_closed(cdf_env):
    """Only the targets the check recorded as closes are withdrawn closed; a decision with
    no override_closes retires everything as overridden."""
    from core.ingestion import overrides, withdrawn
    from core.ingestion.api import connect_rw
    from core.ingestion.units import parse_units
    tl = _timeline(cdf_env, _EV_OLD)
    ev_anchor = parse_units(tl, root=cdf_env["workspace"])[0].anchor
    conn = connect_rw()
    try:
        _cand(conn, "cand-aaaaaaaaaaaaaaaaaaaaaaaa", "sess-1")
        _cand(conn, "cand-dddddddddddddddddddddddd", "sess-1")
        conn.execute("UPDATE ingestion_units SET decision=? WHERE unit_id='cand-dddddddddddddddddddddddd.unit'",
                     (json.dumps({"version_vector": {"overrides": [ev_anchor]}}),))
        conn.execute("UPDATE ingestion_inbox SET payload=? WHERE id='cand-dddddddddddddddddddddddd'",
                     (json.dumps({"title": "Summary bug fixed"}),))
        conn.commit()
        assert overrides.apply(conn, "cand-dddddddddddddddddddddddd.unit") == 1
        statuses = [r.status for r in withdrawn.records(conn)]
    finally:
        conn.close()
    assert statuses == ["overridden"]


def test_a_non_review_row_with_the_same_session_ref_owns_nothing(cdf_env):
    """S1 rows share the session id as source_ref: ownership needs the row to be S7 too."""
    from core.ingestion.api import connect_rw
    _timeline(cdf_env, _EV_OLD)
    conn = connect_rw()
    try:
        _cand(conn, "cand-aaaaaaaaaaaaaaaaaaaaaaaa", "sess-1")
        conn.execute("UPDATE ingestion_inbox SET source='S1', source_v2='S1'")
        conn.commit()
        ref, _ = _ref(cdf_env, "Root cause")
        got = _own(conn, cdf_env, [ref])
    finally:
        conn.close()
    assert got.targets == [] and [r["reason"] for r in got.refused] == ["other_session"]


def test_an_events_same_never_matches_a_gap(cdf_env):
    """An event's `same` is compared with recorded events only: naming a gap is no match."""
    from core.ingestion.api import connect_rw
    from core.ingestion.pipeline_v2 import run_unit_v2
    from tests.test_cdf_refactor import _row
    connect_rw().close()
    _timeline(cdf_env, _EV_OTHER)
    imp = cdf_env["ddd"] / "IMPROVEMENT.md"
    imp.write_text("# IMPROVEMENT\n\n## What to Watch For\n\n"
                   "- [guideline] **Summary fix not done yet** the cap. gap kind: follow_up "
                   "<!-- cand:cand-cccccccccccccccccccccccc.unit -->\n"
                   "- [guideline] **Another open gap a person wrote** stays.\n", encoding="utf-8")
    from core.ingestion import relations_v2
    gap = next(e for e in relations_v2.pool(cdf_env["workspace"], "P") if "not done yet" in e.text)
    decision = run_unit_v2(_row(_event(same=gap.shown()), source="S7", tainted=False, session_project="P"),
                           cdf_env["workspace"])
    assert decision.reason != "already_exists"


def test_a_cap_already_past_does_not_pull_the_review_into_the_past(cdf_env, monkeypatch):
    """A job whose first request is older than MAX_WAIT is not scheduled before now."""
    from core.ingestion import s2_jobs
    from core.ingestion.api import connect_rw
    monkeypatch.setattr(s2_jobs, "_WAKER", None)
    conn = connect_rw()
    try:
        s2_jobs.request("s1", "session_end", delay=s2_jobs.REVIEW_DELAY)
        conn.execute("UPDATE ingestion_s2_jobs SET requested_at='2020-01-01T00:00:00+00:00'")
        conn.commit()
        s2_jobs.request("s1", "session_end", delay=s2_jobs.REVIEW_DELAY)
        nb = conn.execute("SELECT next_attempt_at FROM ingestion_s2_jobs").fetchone()[0]
    finally:
        conn.close()
    assert datetime.fromisoformat(nb) > datetime.now(timezone.utc) + timedelta(minutes=50)


# ── a gap closes when an event says it is done, whichever session opened it ────────

_GAPS = ("# IMPROVEMENT\n\n## What to Watch For\n\n"
         "- [guideline] **Swarm Run design doc not yet written** the doc. gap kind: follow_up "
         "<!-- cand:cand-eeeeeeeeeeeeeeeeeeeeeeee.unit -->\n"
         "- [x] **An earlier gap already closed** done. gap kind: debt "
         "<!-- cand:cand-ffffffffffffffffffffffff.unit -->\n"
         "- [guideline] **A gap a person wrote** stays. gap kind: risk\n"
         "\n## What Failed\n\n"
         "- [pitfall] **A lesson another session wrote** never an event's to retire. "
         "<!-- cand:cand-999999999999999999999999.unit -->\n")


def _pool_ref(cdf_env, title):
    from core.ingestion import relations_v2
    entry = next(e for e in relations_v2.pool(cdf_env["workspace"], "P") if title in e.text)
    return entry.shown(), entry.anchor


def test_an_event_closes_an_open_gap_another_session_opened(cdf_env):
    from core.ingestion.api import connect_rw
    _timeline(cdf_env, _EV_OTHER)
    (cdf_env["ddd"] / "IMPROVEMENT.md").write_text(_GAPS, encoding="utf-8")
    conn = connect_rw()
    try:
        _cand(conn, "cand-eeeeeeeeeeeeeeeeeeeeeeee", "sess-2", kind="gap")
        _cand(conn, "cand-ffffffffffffffffffffffff", "sess-2", kind="gap")
        _cand(conn, "cand-999999999999999999999999", "sess-2", kind="cognition")
        _cand(conn, "cand-bbbbbbbbbbbbbbbbbbbbbbbb", "sess-2")
        gap_ref, gap_anchor = _pool_ref(cdf_env, "design doc not yet written")
        closed_ref, _ = _pool_ref(cdf_env, "already closed")
        person_ref, _ = _pool_ref(cdf_env, "A gap a person wrote")
        lesson_ref, _ = _pool_ref(cdf_env, "A lesson another session wrote")
        event_ref, _ = _ref(cdf_env, "Release notes")
        ok = _own(conn, cdf_env, [gap_ref])
        refused = _own(conn, cdf_env, [closed_ref, person_ref, lesson_ref, event_ref])
        external = _own(conn, cdf_env, [gap_ref], cited_external=True)
    finally:
        conn.close()
    assert ok.targets == [gap_anchor] and ok.closes == [gap_anchor]
    assert [r["reason"] for r in refused.refused] == ["closed", "human_written", "other_session",
                                                      "other_session"]
    assert external.targets == []


def test_an_event_is_shown_the_open_gaps_it_may_close(cdf_env):
    from core.ingestion.sources import s7_review
    from core.ingestion.contracts_v2 import EventUnit
    _timeline(cdf_env, _EV_OLD)
    (cdf_env["ddd"] / "IMPROVEMENT.md").write_text(_GAPS, encoding="utf-8")
    unit = EventUnit(when="2026-10-31", title="Swarm Run design doc written",
                     what="The Swarm Run design doc landed", impact="design settled",
                     evidence=["event:input:x"], scope="project:P")
    (shown,) = s7_review._candidates([unit], cdf_env["workspace"], "P", [])
    gaps = [e for e in shown if "gap kind:" in e.text]
    assert [e.head for e in gaps] == [
        "- [guideline] **Swarm Run design doc not yet written** the doc. gap kind: follow_up"]
    text = s7_review.relation_prompt([unit], [shown])
    line = next(l for l in text.splitlines() if l.startswith(f"- {gaps[0].shown()}"))
    assert line.endswith("(open gap)"), line
