"""Tests for StreamingOrchestrator._build_file_change_events (Cycle 3, run_e626e121).

The unified backend file-change emit: raw written path(s) → enriched SSE event(s)
{path, absolutePath, relevance, operation}, resolved once (cached), bookkeeping
dropped, unresolvable fails-open to the raw path. Mocks the resolver boundary so
the test is hermetic (no real workspace walk).
"""
import asyncio
from types import SimpleNamespace

import pytest

import core.streaming_orchestrator as so


def _orch():
    # _build_file_change_events only touches `self` for nothing — a bare instance
    # via __new__ avoids the full SessionUnit wiring.
    return so.StreamingOrchestrator.__new__(so.StreamingOrchestrator)


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def test_deliverable_event_carries_resolved_absolute(monkeypatch):
    monkeypatch.setattr(
        "routers.workspace_api.resolve_path_to_physical",
        lambda raw, ws: {"relative": "Projects/SwarmAI/report.html",
                         "absolute": "/Users/gawan/repo/report.html"},
    )
    orch = _orch()
    cache: dict = {}
    events = _run(orch._build_file_change_events(["report.html"], cache))
    assert len(events) == 1
    ev = events[0]
    assert ev["type"] == "file_changed"
    assert ev["path"] == "Projects/SwarmAI/report.html"       # ws-relative display
    assert ev["absolutePath"] == "/Users/gawan/repo/report.html"  # physical (copy)
    assert ev["relevance"] == "deliverable"
    assert ev["operation"] == "written"


def test_bookkeeping_path_dropped(monkeypatch):
    # A .artifacts write must never surface — classifier drops it BEFORE resolution.
    called = {"n": 0}

    def _resolver(raw, ws):
        called["n"] += 1
        return {"relative": raw, "absolute": "/abs/" + raw}

    monkeypatch.setattr("routers.workspace_api.resolve_path_to_physical", _resolver)
    orch = _orch()
    events = _run(orch._build_file_change_events(
        ["Projects/SwarmAI/.artifacts/x.json"], {}))
    assert events == []
    assert called["n"] == 0  # bookkeeping never even pays resolution (perf)


def test_unresolvable_fails_open_to_raw(monkeypatch):
    monkeypatch.setattr("routers.workspace_api.resolve_path_to_physical",
                        lambda raw, ws: None)
    orch = _orch()
    events = _run(orch._build_file_change_events(["ghost.html"], {}))
    assert len(events) == 1
    # fail-open: still emits, absolutePath == raw (link/highlight degrade, don't vanish)
    assert events[0]["path"] == "ghost.html"
    assert events[0]["absolutePath"] == "ghost.html"
    assert events[0]["relevance"] == "deliverable"


def test_resolution_cached_per_turn(monkeypatch):
    calls = {"n": 0}

    def _resolver(raw, ws):
        calls["n"] += 1
        return {"relative": raw, "absolute": "/abs/" + raw}

    monkeypatch.setattr("routers.workspace_api.resolve_path_to_physical", _resolver)
    orch = _orch()
    cache: dict = {}
    _run(orch._build_file_change_events(["a.html"], cache))
    _run(orch._build_file_change_events(["a.html"], cache))  # same path, same turn
    assert calls["n"] == 1  # resolved once, second hit served from cache


def test_multiple_targets_from_one_bash(monkeypatch):
    monkeypatch.setattr("routers.workspace_api.resolve_path_to_physical",
                        lambda raw, ws: {"relative": raw, "absolute": "/abs/" + raw})
    orch = _orch()
    events = _run(orch._build_file_change_events(["a.md", "b.md"], {}))
    assert [e["path"] for e in events] == ["a.md", "b.md"]
    assert all(e["relevance"] == "deliverable" for e in events)
