"""A session's knowledge carries the project the session worked in.

S2 and S7 enqueued with no project, so a SwarmAI lesson from a SwarmAI coding session
landed in the cross-project ``Knowledge/Learned/_common`` and every gap parked as
unknown_domain (the gap judge may choose only from PROJECT_CANDIDATES, which was
always empty).

- ``s2_jobs.request`` records the project bound to the repo the session last worked
  in (the signal recall uses), while the session is still alive; a later request
  without one keeps it.
- ``run_session_job`` gives both passes and the review that project as the snapshot's
  ``explicit_project``.
- A v7 store gains the column on open.
- The judge's PROJECT_CANDIDATES holds the hint: written at enqueue, and derived from
  the hint for rows queued before.
"""
from __future__ import annotations

import json
import sqlite3

import pytest


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SWARM_WORKSPACE", str(tmp_path / "SwarmWS"))
    from core.ingestion import s2_jobs
    monkeypatch.setattr(s2_jobs, "_WAKER", None)
    from core.ingestion.store import connect_rw
    conn = connect_rw()
    yield conn
    conn.close()


def test_request_records_the_sessions_bound_project(store, monkeypatch):
    from core import repo_guidance
    from core.ingestion import s2_jobs
    # the hook keeps its state under the APP session id (hook_builder: session_context
    # ["sdk_session_id"] holds the app id); the job's sdk id is a different value
    monkeypatch.setitem(repo_guidance._STATE, "app-1", {"repos": {}, "written": set(),
                                                        "bound_project": "SwarmAI"})
    monkeypatch.setitem(repo_guidance._STATE, "sdk-2", {"repos": {}, "written": set(),
                                                        "bound_project": "Wrong"})
    s2_jobs.request("app-1", "session_end", sdk_session_id="sdk-1")
    repo_guidance._STATE.pop("app-1")
    s2_jobs.request("app-1", "save_button", sdk_session_id="sdk-1")   # state gone: kept
    s2_jobs.request("app-2", "session_end", sdk_session_id="sdk-2")   # never bound
    rows = dict(store.execute("SELECT session_id, project FROM ingestion_s2_jobs"))
    assert rows == {"app-1": "SwarmAI", "app-2": None}
    job = s2_jobs.claim(store)
    assert job["project"] in ("SwarmAI", None)
    assert {"app-1": "SwarmAI", "app-2": None}[job["session_id"]] == job["project"]


def test_the_session_job_gives_its_passes_the_project(monkeypatch):
    from core.ingestion import worker
    from core.ingestion.sources import s2_session, s7_review
    seen: list[tuple[str, object]] = []
    monkeypatch.setattr(s2_session, "extract_session_sync",
                        lambda sid, **kw: seen.append(("s2", kw.get("project_snapshot"))) or {})
    monkeypatch.setattr(s7_review, "review_session",
                        lambda sid, **kw: seen.append(("s7", kw.get("project_snapshot")))
                        or {"status": "unresumable"})
    worker.run_session_job(None, {"session_id": "app-1", "reason": "session_end",
                                  "sdk_session_id": None, "reviewed_to": None,
                                  "project": "SwarmAI"}, runner=object())
    # the session project is offered to the judge, never declared: a declared project
    # is a fallback for a unit the judge gives none (contracts_v2.project_candidates)
    assert seen and all(snap == {"session_project": "SwarmAI"} for _, snap in seen), seen
    seen.clear()
    worker.run_session_job(None, {"session_id": "app-2", "reason": "session_end",
                                  "sdk_session_id": None, "reviewed_to": None,
                                  "project": None}, runner=object())
    assert all(snap is None for _, snap in seen), seen


def test_a_v7_store_gains_the_project_column(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_DATA_DIR", str(tmp_path))
    from core.ingestion.api_v2 import SCHEMA_V2_VERSION
    from core.ingestion.store import connect_rw
    connect_rw().close()
    path = next(tmp_path.rglob("ingestion.db"))
    with sqlite3.connect(path) as conn:
        conn.execute("ALTER TABLE ingestion_s2_jobs DROP COLUMN project")
        conn.execute("INSERT INTO ingestion_s2_jobs(session_id,reason,state,requested_at) "
                     "VALUES('old','session_end','queued','2026-10-29T00:00:00+00:00')")
        conn.execute("PRAGMA user_version=7")
    conn = connect_rw()
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_V2_VERSION
        assert [tuple(r) for r in conn.execute("SELECT session_id, project FROM ingestion_s2_jobs")] == [("old", None)]
    finally:
        conn.close()


def test_the_gap_judge_is_offered_the_hint_as_its_candidate(tmp_path):
    from core.ingestion.contracts_v2 import GapAnswer
    from core.ingestion.pipeline_v2 import run_unit_v2
    offered: list[tuple] = []

    class Judge:
        def card1_v2(self, _unit, projects, _domains):
            offered.append(tuple(projects))
            return GapAnswer(shape="Gap", actionable=True, evidenced=True, self_contained=True,
                             project=projects[0] if projects else None)

    gap = {"kind": "gap", "gap_kind": "risk", "scope": "project:Widget", "title": "Missing fallback",
           "what": "The fallback is absent.", "proposed_next": "Add and validate the fallback.",
           "evidence": ["event:one"]}
    row = {"id": "c", "unit_id": "c.unit", "unit_kind": "gap", "source_v2": "S7",
           "payload": json.dumps(gap), "evidence_root_ids": json.dumps(["root-1"]),
           "envelope": json.dumps({"root_ids": ["root-1"], "lineage_root_ids": ["root-1"],
                                   "strength": "user_explicit", "tainted": False}),
           "project_candidates": "[]", "project_hint": "Widget"}
    decision = run_unit_v2(row, tmp_path, Judge())
    assert offered == [("Widget",)]
    assert decision.decision == "write" and decision.destination.project == "Widget"


def test_recall_reads_the_bound_project_under_the_app_session_id(monkeypatch):
    """Recall's signal 2 looked the hook state up by the SDK id and never found it."""
    from types import SimpleNamespace
    from core import repo_guidance, session_router
    monkeypatch.setitem(repo_guidance._STATE, "app-9", {"repos": {}, "written": set(),
                                                        "bound_project": "SwarmAI"})
    unit = SimpleNamespace(session_id="app-9", _sdk_session_id="sdk-9")
    assert session_router._session_bound_project(unit) == "SwarmAI"
    adopted = SimpleNamespace(session_id="prewarm-x", _app_session_id="app-9", _sdk_session_id="sdk-9")
    assert session_router._session_bound_project(adopted) == "SwarmAI"
