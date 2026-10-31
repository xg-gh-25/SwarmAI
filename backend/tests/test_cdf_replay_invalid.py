"""scripts/cdf_replay_invalid.py (design 2026-10-31-memory-chain-audit M3).

Questions:
- is a bare evidence id given its prefix only when it is a registered input of the same
  session, and a unit with any evidence that names nothing left out?
- is a unit whose claim the code no longer makes true left out?
- is the workspace name as a scope corrected to the project, and is the read set every
  input the session had before the review finished (never smaller)?
- are events, gaps and references never put back?
"""
from __future__ import annotations

import json
import sqlite3

from scripts import cdf_replay_invalid as replay

S = "sess-1"


def _stores(units):
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE extraction_attempts(attempt_id, source, evidence_range, invalid_units, "
                 "units_invalid, started_at, finished_at)")
    conn.execute("INSERT INTO extraction_attempts VALUES('a1','S7',?,?,?, '2026-10-30T01', '2026-10-30T02')",
                 (json.dumps({"session_id": S}), json.dumps([{"raw": u, "reason": "x"} for u in units]), len(units)))
    inputs = sqlite3.connect(":memory:")
    inputs.execute("CREATE TABLE session_inputs(event_id, session_id, root_id, created_at)")
    for event, session, root, at in (("input:" + "a" * 32, S, "r1", "2026-10-30T00"),
                                     ("input:" + "b" * 32, "other", "r2", "2026-10-30T00"),
                                     ("input:" + "c" * 32, S, "r3", "2026-10-30T03")):   # after the review
        inputs.execute("INSERT INTO session_inputs VALUES(?,?,?,?)", (event, session, root, at))
    return conn, inputs


def _unit(title, evidence, **kw):
    return {"kind": "cognition", "type": "pitfall", "scope": kw.pop("scope", "project:SwarmAI"),
            "title": title, "what": "w", "evidence": evidence, **kw}


def test_only_a_registered_input_of_the_session_is_repaired_and_kept():
    conn, inputs = _stores([
        _unit("Bare and registered", ["a" * 32]),
        _unit("Bare but another session's", ["b" * 32]),
        _unit("A sentence as evidence", ["Commit d03f56c35 is labeled reviewed"]),
        _unit("Already prefixed", ["event:input:" + "a" * 32]),
    ])
    found = {f["title"]: f for f in replay.candidates(conn, inputs)}
    assert found["Bare and registered"]["unit"]["evidence"] == ["event:input:" + "a" * 32]
    assert found["Bare and registered"]["keep"] and found["Already prefixed"]["keep"]
    assert not found["Bare but another session's"]["keep"]
    assert found["Bare but another session's"]["unit"]["evidence"] == ["b" * 32]     # never rewritten
    assert not found["A sentence as evidence"]["keep"]


def test_no_longer_true_and_other_kinds_are_left_out():
    conn, inputs = _stores([
        _unit("Redacted tombstone retains its cand: identity and will appear", ["a" * 32]),
        {"kind": "gap", "gap_kind": "follow_up", "scope": "project:SwarmAI", "title": "A gap", "evidence": ["a" * 32]},
    ])
    found = replay.candidates(conn, inputs)
    assert [f["title"] for f in found] == ["Redacted tombstone retains its cand: identity and will appear"]
    assert not found[0]["keep"]


def test_the_scope_and_the_read_set():
    conn, inputs = _stores([_unit("Workspace as scope", ["a" * 32], scope="project:SwarmWS")])
    (f,) = replay.candidates(conn, inputs)
    assert f["unit"]["scope"] == "project:SwarmAI"
    assert f["read"] == ("r1",)                    # this session's inputs before the review finished
