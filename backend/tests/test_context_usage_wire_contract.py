"""Layer-4 wire contract: the REAL backend snapshot payloads equal the shared fixture.

The fixture ``desktop/src/__tests__/fixtures/context-usage-snapshots.json`` is
the contract both sides are bound to: this file asserts what the backend
actually serializes (through the real FastAPI route and real SessionUnit /
context_usage code — only the transcript directory and the router lookup are
stand-ins), and ``desktop/src/__tests__/contextUsage.wire.test.ts`` drives the
same payloads through the frontend's real apply path. A key renamed or dropped
on either side turns that side's test red.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.session_unit import SessionState, SessionUnit

FIXTURE = (Path(__file__).resolve().parents[2]
           / "desktop/src/__tests__/fixtures/context-usage-snapshots.json")
SDK_ID = "11111111-2222-3333-4444-555555555555"


@pytest.fixture(scope="module")
def contract() -> dict:
    return json.loads(FIXTURE.read_text())


def _same(actual: dict, expected: dict) -> None:
    assert set(actual) == set(expected), (sorted(actual), sorted(expected))
    for k, v in expected.items():
        if k == "measuredAt":
            assert isinstance(actual[k], float)
        else:
            assert actual[k] == v, (k, actual[k], v)


def _unit(sdk_id=SDK_ID) -> SessionUnit:
    unit = SessionUnit(session_id="wire", agent_id="default")
    unit.state = SessionState.IDLE
    unit._model_name = "claude-opus-5-5"
    unit._sdk_session_id = sdk_id
    unit._hook_session_context = {}
    return unit


class _Router:
    def __init__(self, unit=None, persisted=None):
        self._unit, self._persisted = unit, persisted or {}

    def get_unit(self, sid):
        return self._unit

    def persisted_sdk_session_id(self, sid):
        return self._persisted.get(sid)


def _http_get(router) -> dict:
    import routers.chat as rc

    app = FastAPI()
    app.include_router(rc.router, prefix="/api/chat")
    with patch("routers.chat._get_router", return_value=router):
        resp = TestClient(app).get("/api/chat/sessions/wire/context-usage")
    assert resp.status_code == 200
    return resp.json()


def test_live_snapshot_over_http(contract):
    unit = _unit()
    unit._record_context_usage(900_000, basis="last_call")
    _same(_http_get(_Router(unit)), contract["live_critical"])


def test_post_compact_snapshot_over_http(contract):
    unit = _unit()
    unit._record_context_usage(900_000, basis="last_call")
    unit._record_compact_boundary({"post_tokens": 41_000})
    _same(_http_get(_Router(unit)), contract["post_compact"])


def test_transcript_snapshot_over_http(contract, tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    proj = tmp_path / "projects" / "-ws"
    proj.mkdir(parents=True)
    row = {"type": "assistant", "isSidechain": False, "timestamp": "2026-10-17T10:00:00Z",
           "message": {"model": "claude-opus-5-5", "usage": {"input_tokens": 2,
                       "cache_read_input_tokens": 269_998, "cache_creation_input_tokens": 0}}}
    (proj / f"{SDK_ID}.jsonl").write_text(json.dumps(row) + "\n")
    _same(_http_get(_Router(None, {"wire": SDK_ID})), contract["transcript"])


def test_none_snapshot_over_http(contract):
    _same(_http_get(_Router(_unit(sdk_id=None))), contract["none"])


def test_sse_event_is_the_same_shape(contract):
    """The per-call SSE event the orchestrator yields is the snapshot itself."""
    unit = _unit()
    evt = unit._record_context_usage(900_000, basis="last_call")
    _same(json.loads(json.dumps(evt)), contract["live_critical"])
