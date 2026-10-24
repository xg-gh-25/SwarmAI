"""Need You items are written for the USER (design welcome-screen-v2.md, "Needs you rows").

What is tested, per source, with the REAL collectors and faked inputs:
  - `title` says what is needed of the user, `summary` says which thing it is;
    neither carries internal jargon (CLASS_x, L1, circuit-broken, [decision]).
  - `dispatch.message` is in the user's voice: the ask first, readable facts
    after, and the ids an agent needs on ONE closing "References:" line.
  - the ids/full text an agent needs are not lost (proposal id, run id, owner
    session, job id, the full note text).
"""
from __future__ import annotations

from types import SimpleNamespace
from pathlib import Path

from core import attention_authority as aa

JARGON = ("CLASS_", "L1 rule", "CODE GATE", "Circuit-broken", "[decision]", "circuit")


def _plain(it):
    for word in JARGON:
        assert word not in it.title, (word, it.title)
        assert word not in it.summary, (word, it.summary)
    msg = it.dispatch["message"]
    body, _, refs = msg.rpartition("\n\nReferences: ")
    assert body and refs and "\n" not in refs, msg  # one closing References line
    return body, refs


def _projects(monkeypatch, names):
    import routers.ddd_brain as db
    monkeypatch.setattr(db, "_list_project_dirs", lambda: [Path(f"/x/{n}") for n in names])


def test_cultivation_note_is_shown_without_its_tag_and_keeps_full_text(monkeypatch):
    import core.ddd_cultivation as dc
    _projects(monkeypatch, ["SwarmAI"])
    long_note = "Stamp a cached derived value with the identity it was measured under. " * 10
    monkeypatch.setattr(dc, "read_pending_proposals", lambda ws, p: [SimpleNamespace(
        proposal_id="proposal_a8c9", target_doc="PROJECT.md", content=f"[decision] {long_note}")])
    it = aa._collect_cultivation(Path("/ws"))[0]
    body, refs = _plain(it)
    assert it.title == "Approve a note SwarmAI wants to add to its memory"
    assert it.summary.startswith("Stamp a cached derived value") and len(it.summary) <= 160
    assert "the SwarmAI brain (PROJECT.md, decisions)" in body
    assert long_note.split()[-1] in body          # full note, not the 80-char title
    assert refs == "proposal proposal_a8c9"


def test_user_stopped_run_says_you_stopped_it_and_keeps_ids(monkeypatch):
    import routers.pipelines as rp
    monkeypatch.setattr(rp, "_to_response", lambda raw: SimpleNamespace(
        id=raw["id"], status=SimpleNamespace(value="paused"), pause_kind="stopped",
        requirement=raw["requirement"]))
    items = aa._collect_paused_runs([{"id": "run_811d", "_project": "SwarmAI",
                                      "session_id": "086356df", "reason": "user_stop",
                                      "requirement": "As an agent, I need a rule for destructive steps"}])
    assert len(items) == 1
    it = items[0]
    body, refs = _plain(it)
    assert it.title == "Resume or drop a pipeline run you stopped"
    assert it.summary == "As an agent, I need a rule for destructive steps"
    assert "What it was for: As an agent, I need a rule for destructive steps" in body
    assert "Reason recorded: user_stop" in body
    assert refs == "run run_811d · its chat session 086356df"


def test_circuit_broken_job_reads_as_a_paused_job(monkeypatch):
    import jobs.scheduler as sch
    js = SimpleNamespace(consecutive_failures=7, consecutive_auth_failures=0,
                         last_error='{"stop_reason":"tool_use"}')
    monkeypatch.setattr(sch, "load_state", lambda: SimpleNamespace(jobs={"gh-evening": js}))
    monkeypatch.setattr(sch, "load_jobs", lambda: [SimpleNamespace(id="gh-evening", name="GitHub Evening Scan")])
    it = aa._collect_jobs()[0]
    body, refs = _plain(it)
    assert it.title == "A scheduled job keeps failing and was paused"
    assert it.summary == "GitHub Evening Scan · failed 7 runs in a row"
    assert body.startswith("The scheduled job \u201cGitHub Evening Scan\u201d failed 7 runs in a row")
    assert "Last recorded error (raw):" in body
    assert refs == "job gh-evening"


def test_escalation_lists_options_and_pick(monkeypatch):
    import core.escalation as esc_mod
    _projects(monkeypatch, ["SwarmAI"])
    esc = SimpleNamespace(id="esc_1", level=int(esc_mod.Level.BLOCK), title="Which DB to use?",
                          situation="Two stores disagree.", recommendation="Keep SQLite",
                          options=[SimpleNamespace(label="SQLite", is_recommendation=True),
                                   SimpleNamespace(label="Postgres", is_recommendation=False)])
    monkeypatch.setattr(esc_mod, "get_open_escalations", lambda ws, p: [esc])
    it = aa._collect_escalations(Path("/ws"))[0]
    body, refs = _plain(it)
    assert it.title == "Answer a question SwarmAI is stuck on"
    assert it.summary == "Which DB to use?"
    assert "Options: SQLite (recommended); Postgres" in body
    assert "SwarmAI's pick: Keep SQLite" in body
    assert refs == "escalation esc_1"


def test_prompt_drops_empty_facts_and_refs():
    msg = aa._prompt("Do it.", [("A", ""), ("B", "b")], ["", "run r"])
    assert msg == "Do it.\n\nB: b\n\nReferences: run r"
