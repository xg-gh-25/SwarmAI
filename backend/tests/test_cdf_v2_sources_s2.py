"""CDF v2 S2 session-source tests.

A paged transcript fixture contains user, assistant, and tool records. All v2 API
functions are monkeypatched so tests assert only the source boundary.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from core.ingestion import api_v2
from core.ingestion.sources import s2_session


class FakeLLM:
    def __init__(self, replies):
        self.replies = list(replies)
        self.prompts = []

    def extract(self, prompt):
        self.prompts.append(prompt)
        return SimpleNamespace(value=self.replies.pop(0), raw="raw")


def _cognition(evidence, what="Prefer a single source of truth for persistent facts."):
    return {"kind": "cognition", "type": "guideline", "scope": "user",
            "user_kind": "preference", "title": "Single source of truth",
            "what": what, "applicability": "When persistent facts are designed.",
            "evidence": evidence}


@pytest.mark.asyncio
async def test_s2_two_passes_order_read_sets_and_s1_exclusion(monkeypatch):
    transcript = [
        {"id": "m1", "role": "user", "content": "Do not patch symptoms."},
        {"id": "m2", "role": "assistant", "content": "I will redesign it."},
        {"id": "m3", "role": "tool", "content": "external output"},
    ]
    monkeypatch.setattr(s2_session, "_transcript_sync", lambda sid: transcript)
    rows = [
        {"event_id": "u1", "kind": "user_message", "ref": "m0"},
        {"event_id": "u2", "kind": "user_message", "ref": "m1"},
        {"event_id": "t1", "kind": "quote", "ref": "m1#quote-0"},
    ]
    monkeypatch.setattr(s2_session, "_input_rows", lambda sid: rows)
    monkeypatch.setattr(s2_session, "_claimed_by_s1", lambda sid: {"u1"})
    monkeypatch.setattr(s2_session, "_root_ids", lambda ids: tuple("root:" + i for i in ids))
    monkeypatch.setattr("core.session_inputs.user_authored_text",
                        lambda event: "Do not patch symptoms." if event == "u2" else None,
                        raising=False)
    shown = []
    monkeypatch.setattr(s2_session, "window_roots",
                        lambda sid, chunk: shown.append(chunk) or [(), ("m2-agent",), ("m3-tool",)])
    calls, attempts = [], []
    monkeypatch.setattr(api_v2, "enqueue_unit", lambda *a, **k:
                        calls.append((a, k)) or SimpleNamespace(status="queued"))
    monkeypatch.setattr(api_v2, "record_extraction_attempt", lambda *a, **k:
                        attempts.append((a, k)) or "a")
    llm = FakeLLM([
        [_cognition(["event:u2"])],
        [_cognition(["event:u2", "event:t1"])],
    ])
    result = await s2_session.extract_session("session-1", llm=llm)
    assert list(result) == ["user_utterance", "main"]
    assert [call[1]["extraction_pass"] for call in calls] == ["user_utterance", "main"]
    assert calls[0][1]["read_root_ids"] == ("root:u2",)
    # the main pass reads the user roots plus the window's own agent and tool content
    assert calls[1][1]["read_root_ids"] == ("root:u2", "root:t1", "root:m2-agent", "root:m3-tool")
    assert shown == [transcript]
    # every item shows the anchors it can be cited by; the allow-list is exactly those
    main_prompt = llm.prompts[1]
    for item, anchors in (("m1", ["event:u2", "event:t1"]), ("m2", ["event:m2-agent"]),
                          ("m3", ["event:m3-tool"])):
        assert f'"id": "{item}"' in main_prompt and json.dumps(anchors) in main_prompt
    allowed = json.loads(main_prompt.split("Allowed evidence: ", 1)[1].split("\n", 1)[0])
    assert allowed == ["event:u2", "event:t1", "event:m2-agent", "event:m3-tool"]
    assert all("u1" not in str(call) for call in calls)
    assert len(attempts) == 2


async def _async(value):
    return value


@pytest.mark.asyncio
async def test_s2_records_both_empty_passes(monkeypatch):
    monkeypatch.setattr(s2_session, "_transcript_sync", lambda sid: [])
    monkeypatch.setattr(s2_session, "_input_rows", lambda sid: [])
    monkeypatch.setattr(s2_session, "_claimed_by_s1", lambda sid: set())
    attempts = []
    monkeypatch.setattr(api_v2, "record_extraction_attempt", lambda *a, **k:
                        attempts.append((a, k)) or "a")
    result = await s2_session.extract_session("session-1", llm=FakeLLM([]))
    assert result == {"user_utterance": [], "main": []}
    assert [row[1]["status"] for row in attempts] == ["no_unit", "no_unit"]


@pytest.mark.asyncio
async def test_user_pass_drops_not_self_contained(monkeypatch):
    monkeypatch.setattr(s2_session, "_transcript_sync", lambda sid: [])
    rows = [{"event_id": "u2", "kind": "user_message"}]
    monkeypatch.setattr(s2_session, "_input_rows", lambda sid: rows)
    monkeypatch.setattr(s2_session, "_claimed_by_s1", lambda sid: set())
    monkeypatch.setattr(s2_session, "_root_ids", lambda ids: tuple(ids))
    monkeypatch.setattr("core.session_inputs.user_authored_text", lambda event: "按 A", raising=False)
    enqueued, attempts = [], []
    monkeypatch.setattr(api_v2, "enqueue_unit", lambda *a, **k: enqueued.append(1))
    monkeypatch.setattr(api_v2, "record_extraction_attempt", lambda *a, **k:
                        attempts.append((a, k)) or "a")
    llm = FakeLLM([[_cognition(["event:u2"], "按 A")]])
    await s2_session.extract_session("session-1", llm=llm)
    assert enqueued == []
    assert attempts[0][1]["invalid_units"][0]["reason"] == "not_self_contained"


def test_read_roots_fail_closed_when_an_event_has_no_bound_root(tmp_path, monkeypatch):
    """§9 anti selective citation: an unbound event, a missing data.db or a read error
    keeps the raw id in the read set (provenance then marks the unit tainted); the read
    set never shrinks silently."""
    import sqlite3
    from core import session_inputs

    missing = tmp_path / "absent.db"
    monkeypatch.setattr(session_inputs, "_db_path", lambda: missing)
    assert s2_session._root_ids(("e1", "e2")) == ("e1", "e2")

    db = tmp_path / "data.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE session_inputs(event_id TEXT, root_id TEXT)")
        conn.execute("INSERT INTO session_inputs VALUES('e1','root-1'),('e2',NULL)")
    monkeypatch.setattr(session_inputs, "_db_path", lambda: db)
    assert s2_session._root_ids(("e1", "e2", "e3")) == ("root-1", "e2", "e3")


def test_main_pass_windows_are_capped_and_spread():
    """One session cut into 273 windows cost about $100; at most MAX_MAIN_WINDOWS are
    extracted, evenly spaced with the first and last (2026-10-29-cdf-launch §3 P1)."""
    from core.ingestion.sources import s2_session
    picked = s2_session.spread(list(range(273)), s2_session.MAX_MAIN_WINDOWS)
    indexes = [i for i, _ in picked]
    assert len(picked) == s2_session.MAX_MAIN_WINDOWS
    assert indexes[0] == 1 and indexes[-1] == 273 and indexes == sorted(set(indexes))
    assert s2_session.spread(list(range(3)), 8) == [(1, 0), (2, 1), (3, 2)]


def test_main_pass_extracts_only_the_capped_windows(monkeypatch):
    from core.ingestion.sources import common, s2_session
    calls = []
    monkeypatch.setattr(s2_session, "windows", lambda *a, **k: [[{"id": f"m{i}"}] for i in range(20)])
    monkeypatch.setattr(s2_session, "window_roots", lambda sid, chunk: [])
    monkeypatch.setattr(s2_session, "_with_anchors", lambda chunk, own, ue: (chunk, ()))
    monkeypatch.setattr(s2_session, "_root_ids", lambda ids: ())
    monkeypatch.setattr(common, "extract_and_enqueue",
                        lambda *a, **k: calls.append(k["evidence_range"]) or [])
    s2_session._main_pass("s", [{"id": "m0"}], [], set(), None, None)
    assert len(calls) == s2_session.MAX_MAIN_WINDOWS
    assert all(c["windows"] == 20 and c["windows_skipped"] == 12 for c in calls)
