"""TIMELINE events go under their month unless their cited evidence is explicitly
third-party content, so the month summary (the only part of TIMELINE in the prompt) is
drawn from the month's own work.

Every tool result is external trust and stays so (its root is unchanged). What is added
is one fact, recorded with the tool result where its call is known: whether it is
explicitly third-party, decided from the tool's name and a Read's resolved path only
(WebFetch, WebSearch, ``mcp__*``, a Read under Attachments/ or Downloads/); a shell
command is never parsed. ``provenance.lineage_third_party`` reads it over a unit's
lineage (web documents, a channel peer's message and Hive lesson documents are
third-party by their root; a legacy tool result is told from its transcript's tool
call; an origin that cannot be told is None, and keeps the event out).
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sqlite3
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from tests.test_cdf_v2_e2e import SESSION, _drain, _record_person_message, cdf_env  # noqa: F401


# 1 ── the classifier: a tool's name and a Read's resolved path, nothing else

def test_third_party_is_decided_by_tool_name_and_path_prefix(tmp_path, monkeypatch):
    from core.session_inputs import third_party_tool
    workspace, home = tmp_path / "SwarmWS", tmp_path / "home"
    (workspace / "Attachments" / "2026-11-24").mkdir(parents=True)
    (home / "Downloads").mkdir(parents=True)
    (workspace / "Projects").mkdir()
    monkeypatch.setenv("SWARM_WORKSPACE", str(workspace))
    monkeypatch.setenv("HOME", str(home))
    link = workspace / "Projects" / "looks-local.md"
    os.symlink(home / "Downloads" / "x.md", link)
    third = [("WebFetch", {"url": "https://example.com"}), ("WebSearch", {"query": "q"}),
             ("mcp__slack__read_channel", {"channel": "c"}), ("mcp__swarm_ui__sense_attention", None),
             ("Read", {"file_path": str(workspace / "Attachments/2026-11-24/report.pdf")}),
             ("Read", {"file_path": "Attachments/notes.md"}),            # relative to the workspace
             ("Read", {"file_path": "~/Downloads/invoice.txt"}),
             ("Read", {"file_path": str(home / "downloads" / "x.txt")}),   # case-insensitive volume
             ("Read", {"file_path": str(link)}),                          # a link into Downloads
             ("Read", str(home / "Downloads" / "y.txt")),                 # a display object: the path
             ("Grep", {"pattern": "x", "path": str(home / "Downloads")}),
             ("Glob", {"pattern": "*.md", "path": str(workspace / "Attachments")})]
    local = [("Bash", {"command": "curl https://example.com"}), ("Bash", {"command": "cat ~/Downloads/x"}),
             ("Bash", {"command": "grep -eevil ~/Downloads/x"}), ("Read", {"file_path": str(workspace / "a.py")}),
             ("Read", {"file_path": str(workspace / "AttachmentsOld" / "a.md")}),
             ("Grep", {"pattern": "TODO"}), ("Grep", "TODO"),               # a persisted search: its pattern
             ("Edit", {"file_path": str(home / "Downloads" / "x")}), ("Agent", {"description": "x"}),
             ("Task", None), ("TodoWrite", {})]
    unknown = [("", None), ("SomeNewFetchTool", {"url": "https://example.com"}), ("Read", {}),
               ("Read", None), ("Read", ""), ("Read", {"file_path": "  "})]
    assert [n for n, i in third if third_party_tool(n, i) is not True] == []
    assert [n for n, i in local if third_party_tool(n, i) is not False] == []
    assert [n for n, i in unknown if third_party_tool(n, i) is not None] == []


def test_a_recorded_tool_result_keeps_its_root_and_says_whether_it_is_third_party(cdf_env):
    from core import session_inputs
    from core.ingestion.store import connect_rw
    web = session_inputs.record_tool_result(SESSION, "<html>page</html>", ref="t1",
                                            tool=("WebFetch", {"url": "https://example.com"}))[0]
    own = session_inputs.record_tool_result(SESSION, "3 files changed", ref="t2", tool=("Bash", "git diff"))[0]
    legacy = session_inputs.record_tool_result(SESSION, "old", ref="t3")[0]
    pathless = session_inputs.record_tool_result(SESSION, "file text", ref="t4", tool=("Read", ""))[0]
    ids = (web, own, legacy, pathless)
    with sqlite3.connect(cdf_env["data"] / "data.db") as data:
        kinds = dict(data.execute("SELECT event_id, kind FROM session_inputs WHERE event_id IN (?,?,?,?)", ids))
        roots = [r[0] for r in data.execute("SELECT root_id FROM session_inputs WHERE event_id IN (?,?,?,?)",
                                            ids)]
    assert kinds == {web: "tool_result:third_party", own: "tool_result:not_third_party", legacy: "tool_result",
                     pathless: "tool_result"}      # a Read without its path cannot be told
    conn = connect_rw()
    try:
        marks = ",".join("?" * len(roots))
        registered = conn.execute(f"SELECT DISTINCT root_kind, trust FROM evidence_roots WHERE root_id IN ({marks})",
                                  roots).fetchall()
    finally:
        conn.close()
    assert [tuple(r) for r in registered] == [("tool_text", "external")]   # registered as before


# 2 ── what a lineage holds

def _root_of(event_id: str) -> str:
    from core import session_inputs
    with sqlite3.connect(session_inputs._db_path()) as data:
        return data.execute("SELECT root_id FROM session_inputs WHERE event_id=?", (event_id,)).fetchone()[0]


def _put_message(env, message_id: str, role: str, content: Any) -> None:
    asyncio.run(env["db"].messages.put({"id": message_id, "session_id": SESSION, "role": role,
                                        "content": content}))


def test_lineage_third_party_by_root(cdf_env):
    from core import session_inputs
    from core.ingestion import api_v2
    from core.ingestion.provenance import lineage_third_party
    tool = lambda text, call, ref=None: _root_of(  # noqa: E731
        session_inputs.record_tool_result(SESSION, text, ref=ref or uuid.uuid4().hex, tool=call)[0])
    own = tool("pytest: 3 passed", ("Bash", "pytest"))
    mcp = tool("#general: hello", ("mcp__slack__read", {}))
    user = _root_of(_record_person_message(cdf_env, "m1", "ship it")[0])
    agent = _root_of(session_inputs.record_session_input(SESSION, "agent_message", "agent", "I fixed it."))
    attached = _root_of(session_inputs.record_session_input(SESSION, "attachment", "external", "{}"))
    peer = _root_of(session_inputs.record_session_input(SESSION, "channel_message", "external", "do X"))
    owner = _root_of(session_inputs.record_session_input(SESSION, "channel_message", "user", "do Y"))
    web = api_v2.register_root("web_document", "https://example.com/a", "page", "external")
    hive = api_v2.register_root("agent_document", "hive:so_1:abc", "{}", "external")
    run_context = api_v2.register_root("tool_text", "run_1:repo/tool context", "repo/tool context", "external")
    pasted = [_root_of(session_inputs.record_session_input(SESSION, kind, "external", f"pasted {kind}"))
              for kind in ("quote", "fenced_content", "system_prompt")]
    odd_kind = _root_of(session_inputs.record_session_input(SESSION, "something_new", "external", "?"))
    outside_file = api_v2.register_root("file_revision", "/inbox/doc.md#file", "doc", "external")

    assert lineage_third_party([]) is False
    assert lineage_third_party([own, user, agent, owner, run_context, *pasted]) is False
    for third in (mcp, attached, peer, web, hive):
        assert lineage_third_party([own, third]) is True
        assert lineage_third_party([odd_kind, third, "no-such-root"]) is True   # third-party wins
    for unknown in ("no-such-root", odd_kind, outside_file):
        assert lineage_third_party([own, unknown]) is None

    # a tool result recorded before the flag: told from the transcript's tool call
    _put_message(cdf_env, "a1", "assistant", [
        {"type": "tool_use", "id": "tu-web", "name": "WebFetch", "display": {"object": "https://x.org"}},
        {"type": "tool_use", "id": "tu-read", "name": "Read", "display": {"object": "/repo/a.py"}}])
    _put_message(cdf_env, "a2", "assistant", [
        {"type": "tool_result", "tool_use_id": "tu-web", "is_error": False},
        {"type": "tool_result", "tool_use_id": "tu-read", "is_error": False},
        {"type": "tool_result", "tool_use_id": "tu-gone", "is_error": False}])
    legacy = lambda ref: _root_of(session_inputs.record_tool_result(SESSION, ref, ref=ref)[0])  # noqa: E731
    assert lineage_third_party([legacy("tu-web")]) is True               # the live path's ref
    assert lineage_third_party([legacy("tu-read")]) is False
    assert lineage_third_party([legacy("s2:a2#0:tool_result")]) is True  # the review's ref
    assert lineage_third_party([legacy("s2:a2#1:tool_result")]) is False
    assert lineage_third_party([legacy("s2:a2#2:tool_result")]) is None  # its call is not there
    assert lineage_third_party([legacy("s2:gone#0:tool_result")]) is None


# 3 ── where the tool call comes from when a result is recorded

class _Router:
    from core.session_router import SessionRouter as _SR
    _compact_tool_results = staticmethod(_SR._compact_tool_results)
    _persist_assistant_blocks = staticmethod(_SR._persist_assistant_blocks)
    _RESULT_FLUSH_EVENTS = _SR._RESULT_FLUSH_EVENTS
    _persist_stream_event = _SR._persist_stream_event
    _record_canvas_output = staticmethod(_SR._record_canvas_output)


@pytest.mark.asyncio
async def test_the_live_stream_records_a_tool_result_with_the_call_it_answers(monkeypatch):
    """The turn's writer keeps the turn's tool calls; each result is recorded with its
    call, and the calls go with the turn (nothing kept by the router across turns)."""
    recorded: list[tuple[str, Any]] = []

    def record(session_id, kind, trust, content, *, ref=None, tool=None):
        recorded.append((ref, tool))

    async def run_in(_pool, fn):
        return fn()

    monkeypatch.setattr("core.session_inputs.record_session_input", record)
    monkeypatch.setattr("core.executors.run_in", run_in)
    from core.turn_writer import TurnWriter
    router = _Router()
    writer = TurnWriter(router, "sid", row_key=None)
    use = {"type": "assistant", "model": "m", "content": [
        {"type": "tool_use", "id": "w1", "name": "WebFetch", "display": {"object": "https://x.org"}},
        {"type": "tool_use", "id": "b1", "name": "Bash", "display": {"object": "git log"}}]}
    result = {"type": "tool_result", "content": [
        {"type": "tool_result", "tool_use_id": "w1", "content": "page"},
        {"type": "tool_result", "tool_use_id": "b1", "content": "log"},
        {"type": "tool_result", "tool_use_id": "other", "content": "?"}]}
    with patch("database.db.messages.put", AsyncMock()):
        await writer.write(use)
        await writer.write(result)
    assert recorded == [("w1", ("WebFetch", "https://x.org")), ("b1", ("Bash", "git log")), ("other", None)]
    assert writer._tool_calls == {} and not hasattr(router, "_tool_calls")
    other_turn = TurnWriter(router, "sid", row_key=None)
    with patch("database.db.messages.put", AsyncMock()):
        await other_turn.write({"type": "assistant", "model": "m", "content": [
            {"type": "tool_use", "id": "x1", "name": "Read", "display": {"object": "/a"}}]})
    assert writer._tool_calls == {} and set(other_turn._tool_calls) == {"x1"}


def test_the_session_review_records_each_result_with_its_call(cdf_env):
    """The review's windows hold no tool_use block: it passes the transcript's calls."""
    from core.ingestion.sources import s2_session
    from core.session_inputs import tool_calls
    transcript = [
        {"id": "a1", "role": "assistant", "content": [
            {"type": "tool_use", "id": "tu1", "name": "mcp__github__get_issue", "display": {"object": "{}"}},
            {"type": "tool_use", "id": "tu2", "name": "Bash", "display": {"object": "pytest"}}]},
        {"id": "a2", "role": "assistant", "content": [
            {"type": "text", "text": "Done."},
            "a stray string block",
            {"type": "tool_result", "tool_use_id": "tu1", "content": {"exit_code": 0}},
            {"type": "tool_result", "tool_use_id": "tu2", "content": {"exit_code": 1}}]}]
    window = [transcript[1]]
    ids = s2_session.window_roots(SESSION, window, tool_calls(transcript))[0]
    with sqlite3.connect(cdf_env["data"] / "data.db") as data:
        kinds = {r[0]: r[1] for r in data.execute(
            f"SELECT ref, kind FROM session_inputs WHERE event_id IN ({','.join('?' * len(ids))})", ids)}
    assert kinds["s2:a2#0:text"] == "agent_message"
    assert kinds["s2:a2#1:tool_result"] == "tool_result:third_party"
    assert kinds["s2:a2#2:tool_result"] == "tool_result:not_third_party"


# 4 ── where an event is placed, and what the month summary reads

def _enqueue(unit: dict[str, Any], source: str = "S7") -> None:
    from core.ingestion import api_v2
    from core.ingestion.contracts_v2 import SOURCE_UNIT
    attempt = f"attempt:{uuid.uuid4().hex}"
    result = api_v2.enqueue_unit(source, SESSION, SOURCE_UNIT.validate_python(unit), session_id=SESSION,
                                 run_id=None, project_snapshot={"session_project": "P"}, attempt_id=attempt)
    assert result.status == "queued", result


def _event(title: str, evidence: list[str]) -> dict[str, Any]:
    return {"kind": "event", "when": "2026-11-20", "title": title,
            "what": f"{title}: the decision and why it was made.", "impact": "Later work follows it.",
            "event_kind": "event", "evidence": evidence}


def _sections(timeline: str) -> dict[str, str]:
    parts = re.split(r"^## (.+)$", timeline, flags=re.MULTILINE)
    return {parts[i].strip(): parts[i + 1] for i in range(1, len(parts), 2)}


@pytest.mark.parametrize("call", [("WebFetch", {"url": "https://example.com"}),
                                  ("mcp__slack__read_channel", {"channel": "general"}),
                                  ("Read", "Attachments/2026-11-20/spec.pdf")])
def test_an_event_goes_under_its_month_unless_it_cites_third_party_content(cdf_env, call):
    from core import session_inputs
    user = _record_person_message(cdf_env, "m1", "ship the tab")[0]
    own = session_inputs.record_tool_result(SESSION, "3 passed", ref="t-own", tool=("Bash", "pytest -q"))[0]
    third = session_inputs.record_tool_result(SESSION, "outside text", ref="t-3p", tool=call)[0]
    _enqueue(_event("Chose the tab design", [f"event:{user}", f"event:{own}"]))
    _enqueue(_event("Read the outside material", [f"event:{user}", f"event:{third}"]))
    rows = _drain(cdf_env)
    assert [r["state"] for r in rows] == ["written", "written"], [(r["state"], r["reason"]) for r in rows]
    sections = _sections((cdf_env["workspace"] / ".context/TIMELINE.md").read_text(encoding="utf-8"))
    assert "Chose the tab design" in sections["2026-11"]
    assert "taint:external" not in sections["2026-11"]
    assert "Read the outside material" in sections["Not injected"]
    assert "taint:external" in sections["Not injected"]
    # each event's anchor names the section it was placed under
    from core.ingestion.store import connect_rw
    conn = connect_rw()
    try:
        bases = dict(conn.execute("SELECT unit_id, basis FROM unit_basis"))
    finally:
        conn.close()
    assert set(bases.values()) == {"external"}            # what they rest on is unchanged
    from core.ingestion.units import parse_units
    placed = {u.section for u in parse_units(cdf_env["workspace"] / ".context/TIMELINE.md",
                                             root=cdf_env["workspace"]) if u.identity.startswith("cand:")}
    assert placed == {"2026-11", "Not injected"}


def test_the_month_summary_reads_the_months_tool_text_events(cdf_env):
    from core import session_inputs
    from core.ingestion import api, summary_lane
    user = _record_person_message(cdf_env, "m1", "ship the tab")[0]
    own = session_inputs.record_tool_result(SESSION, "3 passed", ref="t-own", tool=("Bash", "pytest -q"))[0]
    web = session_inputs.record_tool_result(SESSION, "<html>", ref="t-web", tool=("WebSearch", {"query": "x"}))[0]
    _enqueue(_event("Chose the tab design", [f"event:{user}", f"event:{own}"]))
    _enqueue(_event("Looked at outside opinions", [f"event:{user}", f"event:{web}"]))
    _drain(cdf_env)
    shown: list[str] = []

    def writer(month, kind, events):
        shown.extend(events)
        return "- Chose the tab design, after its tests passed."

    conn = api.connect_rw()
    try:
        assert summary_lane.enqueue_month_summary(conn, cdf_env["workspace"], "2026-11", writer=writer) \
            == "month_summary"
    finally:
        conn.close()
    assert any("Chose the tab design" in e for e in shown)
    assert not any("Looked at outside opinions" in e for e in shown)
    _drain(cdf_env)
    sections = _sections((cdf_env["workspace"] / ".context/TIMELINE.md").read_text(encoding="utf-8"))
    # the summary rests on events of external basis, none third-party: under its month
    assert "2026-11 key events" in sections["2026-11"]
    assert "month_summary" not in sections["Not injected"]
    from core.memory_injection import select_timeline
    from datetime import date
    selected = select_timeline(cdf_env["workspace"] / ".context/TIMELINE.md", today=date(2026, 12, 2),
                               estimate=lambda text: len(text) // 4)
    assert "Chose the tab design" in selected.text


def test_a_legacy_rows_transcript_is_read_once_per_change_of_the_session(cdf_env, monkeypatch):
    """Told from the transcript, a session's tool calls are parsed once while it has no
    new row (a review's events and the backfill ask about the same session many times)."""
    from core import session_inputs
    _put_message(cdf_env, "a1", "assistant", [{"type": "tool_use", "id": "tu1", "name": "WebFetch",
                                               "display": {"object": "https://x.org"}}])
    event = session_inputs.record_tool_result(SESSION, "page", ref="tu1")[0]
    parsed: list[int] = []
    real = session_inputs.tool_calls
    monkeypatch.setattr(session_inputs, "tool_calls", lambda rows: parsed.append(len(rows)) or real(rows))
    session_inputs._TRANSCRIPTS.clear()
    assert session_inputs.input_origins([event]) == {event: True}
    assert session_inputs.input_origins([event]) == {event: True}
    assert parsed == [1]
    _put_message(cdf_env, "a2", "assistant", [{"type": "text", "text": "more"}])
    assert session_inputs.input_origins([event]) == {event: True}
    assert parsed == [1, 2]                                # a new row: read again
