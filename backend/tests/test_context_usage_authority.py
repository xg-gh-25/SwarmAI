"""Context usage has ONE authority: the backend snapshot.

What is tested:

* ``core.context_usage`` — the snapshot builder and the CLI-transcript reader
  (main-chain rows only, zero-usage synthetic rows skipped, a later
  ``compact_boundary`` wins with its ``postTokens``, a boundary without sizes
  means "unknown", not the stale pre-compact value).
* ``SessionUnit`` — the snapshot is stamped with the ``sdk_session_id`` it was
  measured under and is invisible once that identity changes or is cleared;
  per-call recording dedupes on the token value; ``compact()`` records the
  boundary it drains (or clears the snapshot when it sees none).
* The orchestrator read loop — a snapshot is yielded after each main-agent
  API call whose size changed (never for a sub-agent), and on compact_boundary,
  with no transcript I/O on that path.
* ``GET /api/chat/sessions/{id}/context-usage`` — live snapshot, transcript
  derivation (unit id first, persisted id only without a unit), and
  ``source: none``.
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from claude_agent_sdk import AssistantMessage, SystemMessage, TextBlock

from core import context_usage
from core.session_unit import SessionState, SessionUnit
from core.streaming_orchestrator import StreamingOrchestrator

SDK_ID = "11111111-2222-3333-4444-555555555555"


# ── fixtures / helpers ──────────────────────────────────────────────────


def _unit(sdk_id: str | None = SDK_ID) -> SessionUnit:
    unit = SessionUnit(session_id="ctx-usage", agent_id="default")
    unit.state = SessionState.STREAMING
    unit._model_name = "claude-opus-5-5"
    unit._sdk_session_id = sdk_id
    unit._hook_session_context = {}
    return unit


def _asst(tokens: int, *, model: str = "claude-opus-5-5", sidechain: bool = False,
          ts: str = "2026-10-17T10:00:00.000Z") -> dict:
    return {
        "type": "assistant", "isSidechain": sidechain, "timestamp": ts,
        "message": {"model": model, "content": [{"type": "text", "text": "x"}],
                    "usage": {"input_tokens": 2, "cache_read_input_tokens": tokens - 2,
                              "cache_creation_input_tokens": 0, "output_tokens": 5}},
    }


def _boundary(post: int | None, ts: str = "2026-10-17T11:00:00.000Z") -> dict:
    md = {"trigger": "auto", "preTokens": 900_000}
    if post is not None:
        md["postTokens"] = post
    return {"type": "system", "subtype": "compact_boundary", "isSidechain": False,
            "timestamp": ts, "compactMetadata": md}


def _write(tmp_path: Path, rows: list[dict], *, sdk_id: str = SDK_ID) -> Path:
    proj = tmp_path / "projects" / "-ws"
    proj.mkdir(parents=True, exist_ok=True)
    path = proj / f"{sdk_id}.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return path


class _Script:
    def __init__(self, msgs):
        self._msgs = list(msgs)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._msgs:
            return self._msgs.pop(0)
        raise StopAsyncIteration


def _sys(subtype, **data):
    return SystemMessage(subtype=subtype, data={"type": "system", "subtype": subtype, **data})


async def _drain(unit: SessionUnit, msgs) -> list[dict]:
    from core import permission_manager as _pm_mod

    unit._streaming_start_time = time.time()
    unit._last_event_time = time.time()
    # A scripted stream ends without a ResultMessage; marking content emitted
    # keeps the read loop's zombie detector (an instant, empty stream) from
    # raising — so ANY exception here is a real failure of the code under test.
    unit._content_emitted = True
    orch = StreamingOrchestrator(unit)
    unit._client = MagicMock()
    unit._client.receive_response = lambda: _Script(msgs)
    events: list[dict] = []
    with patch.object(_pm_mod.permission_manager, "get_session_queue",
                      return_value=asyncio.Queue()):
        async for ev in orch._read_formatted_response():
            events.append(ev)
    return events


def _ctx(events):
    return [e for e in events if e.get("type") == "context_warning"]


# ── context_usage module ────────────────────────────────────────────────


class TestBuildSnapshot:
    def test_snapshot_carries_backend_level_and_provenance(self):
        snap = context_usage.build_snapshot(
            900_000, "claude-opus-5-5", source="live", basis="last_call", measured_at=5.0)
        assert snap["type"] == "context_warning"
        assert (snap["pct"], snap["level"], snap["tokensEst"]) == (90, "critical", 900_000)
        assert (snap["source"], snap["basis"], snap["measuredAt"]) == ("live", "last_call", 5.0)
        assert snap["window"] == 1_000_000

    def test_no_snapshot_for_non_positive_tokens(self):
        assert context_usage.build_snapshot(0, None, source="live", basis="last_call") is None

    def test_none_snapshot_has_no_pct(self):
        snap = context_usage.none_snapshot(measured_at=7.0)
        assert snap["source"] == "none" and snap["pct"] is None and snap["measuredAt"] == 7.0


class TestTranscriptReader:
    def test_last_main_chain_row_wins_sidechain_and_zero_rows_skipped(self, tmp_path):
        zero = _asst(10)
        zero["message"]["usage"] = {"input_tokens": 0, "cache_read_input_tokens": 0}
        path = _write(tmp_path, [_asst(300_000), _asst(400_000, ts="2026-10-17T10:05:00Z"),
                                 _asst(20_000, sidechain=True), zero])
        got = context_usage.read_transcript_usage(path)
        assert got.tokens == 400_000 and got.basis == "last_call"
        assert got.model == "claude-opus-5-5"
        assert got.measured_at == pytest.approx(
            context_usage._parse_ts("2026-10-17T10:05:00Z"))

    def test_later_compact_boundary_uses_post_tokens_and_keeps_model(self, tmp_path):
        zero = _asst(10)
        zero["message"]["usage"] = {"input_tokens": 0}
        path = _write(tmp_path, [_asst(800_000, model="claude-sonnet-4-6"),
                                 _boundary(41_000), zero])
        got = context_usage.read_transcript_usage(path)
        assert (got.tokens, got.basis, got.model) == (41_000, "post_compact", "claude-sonnet-4-6")

    def test_assistant_after_boundary_wins(self, tmp_path):
        path = _write(tmp_path, [_asst(800_000), _boundary(41_000), _asst(60_000)])
        got = context_usage.read_transcript_usage(path)
        assert (got.tokens, got.basis) == (60_000, "last_call")

    def test_boundary_without_sizes_is_unknown_not_stale(self, tmp_path):
        path = _write(tmp_path, [_asst(800_000), _boundary(None)])
        got = context_usage.read_transcript_usage(path)
        assert got.tokens is None and got.basis == "post_compact"

    def test_reads_backwards_across_chunks_and_ignores_garbage(self, tmp_path):
        filler = {"type": "user", "message": {"content": "y" * 5000}}
        rows = [_asst(123_456)] + [filler] * 200
        path = _write(tmp_path, rows)
        with path.open("a") as fh:
            fh.write("{not json\n")
        got = context_usage.read_transcript_usage(path, chunk_size=4096)
        assert got.tokens == 123_456

    def test_result_is_cached_by_file_state(self, tmp_path):
        path = _write(tmp_path, [_asst(300_000)])
        with patch.object(context_usage, "_scan_transcript_usage",
                          wraps=context_usage._scan_transcript_usage) as scan:
            assert context_usage.read_transcript_usage(path).tokens == 300_000
            assert context_usage.read_transcript_usage(path).tokens == 300_000
            assert scan.call_count == 1          # unchanged file: served from cache
            with path.open("a") as fh:           # the CLI appends a newer request
                fh.write(json.dumps(_asst(310_000)) + "\n")
            assert context_usage.read_transcript_usage(path).tokens == 310_000
            assert scan.call_count == 2          # changed file: re-read, never stale

    def test_empty_transcript_returns_none(self, tmp_path):
        assert context_usage.read_transcript_usage(_write(tmp_path, [])) is None

    def test_snapshot_from_transcript(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
        _write(tmp_path, [_asst(250_000)])
        snap = context_usage.snapshot_from_transcript(SDK_ID, None)
        assert (snap["source"], snap["pct"], snap["basis"]) == ("transcript", 25, "last_call")

    def test_snapshot_from_transcript_none_cases(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
        assert context_usage.snapshot_from_transcript(None, None)["source"] == "none"
        assert context_usage.snapshot_from_transcript(SDK_ID, None)["source"] == "none"  # no file
        assert context_usage.snapshot_from_transcript("../../etc", None)["source"] == "none"


# ── SessionUnit snapshot ownership ──────────────────────────────────────


class TestUnitSnapshot:
    def test_record_dedupes_on_token_value(self):
        unit = _unit()
        assert unit._record_context_usage(300_000, basis="last_call") is not None
        assert unit._record_context_usage(300_000, basis="last_call") is None
        assert unit._record_context_usage(301_000, basis="last_call")["tokensEst"] == 301_000
        assert unit._record_context_usage(301_000, basis="last_call", force=True) is not None

    def test_snapshot_invisible_after_identity_change(self):
        unit = _unit()
        unit._record_context_usage(300_000, basis="last_call")
        assert unit.context_usage_snapshot()["tokensEst"] == 300_000
        unit._sdk_session_id = None
        assert unit.context_usage_snapshot() is None
        unit._sdk_session_id = "99999999-0000-0000-0000-000000000000"
        assert unit.context_usage_snapshot() is None

    def test_snapshot_recorded_without_identity_is_never_served(self):
        unit = _unit(sdk_id=None)
        assert unit._record_context_usage(300_000, basis="last_call") is not None
        assert unit.context_usage_snapshot() is None

    def test_compact_boundary_with_and_without_sizes(self):
        unit = _unit()
        unit._record_context_usage(800_000, basis="last_call")
        evt = unit._record_compact_boundary({"post_tokens": 41_000})
        assert (evt["tokensEst"], evt["basis"]) == (41_000, "post_compact")
        evt = unit._record_compact_boundary({})
        assert evt["source"] == "none" and evt["pct"] is None
        # No size reported: the live value is dropped so the read endpoint
        # derives the truth from the transcript instead.
        assert unit.context_usage_snapshot() is None

    def test_turn_end_reemits_current_snapshot(self):
        unit = _unit()
        unit.state = SessionState.IDLE
        unit._last_call_usage = {"input_tokens": 10, "cache_read_input_tokens": 499_990}
        evs = unit._emit_post_stream_metadata({"input_tokens": 0}, num_turns=3)
        w = _ctx(evs)
        assert len(w) == 1 and w[0]["pct"] == 50 and w[0]["source"] == "live"
        assert unit.context_usage_snapshot()["tokensEst"] == 500_000

    def test_turn_end_average_fallback_is_labelled(self):
        unit = _unit()
        unit.state = SessionState.IDLE
        evs = unit._emit_post_stream_metadata(
            {"input_tokens": 0, "cache_read_input_tokens": 600_000}, num_turns=3)
        assert _ctx(evs)[0]["basis"] == "turn_average"

    @pytest.mark.asyncio
    async def test_manual_compact_records_drained_boundary(self):
        unit = _unit()
        unit.state = SessionState.IDLE
        unit._record_context_usage(800_000, basis="last_call")
        client = MagicMock()

        async def _q(**_kw):
            return None

        client.query = _q
        client.receive_response = lambda: _Script([
            _sys("compact_boundary", compact_metadata={"pre_tokens": 800_000,
                                                       "post_tokens": 30_000})])
        unit._client = client
        assert (await unit.compact())["success"] is True
        assert unit.context_usage_snapshot()["tokensEst"] == 30_000

    def test_turn_end_keeps_this_turns_compaction(self):
        """A compaction with no request after it (e.g. /compact typed in chat):
        turn end must not overwrite post_tokens with the summed average."""
        unit = _unit()
        unit.state = SessionState.IDLE
        unit._context_recorded_this_turn = False
        unit._record_compact_boundary({"post_tokens": 41_000})
        evs = unit._emit_post_stream_metadata(
            {"input_tokens": 0, "cache_read_input_tokens": 2_700_000}, num_turns=3)
        assert _ctx(evs)[0]["tokensEst"] == 41_000
        assert unit.context_usage_snapshot()["basis"] == "post_compact"

    def test_request_then_compaction_then_turn_end_keeps_post_compact(self):
        """A request, then a compaction, then the turn ends with no further
        request: the pre-compaction last-call usage must not win at turn end."""
        unit = _unit()
        unit.state = SessionState.IDLE
        unit._last_call_usage = {"input_tokens": 2, "cache_read_input_tokens": 899_998}
        unit._record_context_usage(900_000, basis="last_call")
        unit._record_compact_boundary({"post_tokens": 41_000})
        # The turn's ResultMessage still sums the pre-compaction request.
        evs = unit._emit_post_stream_metadata(
            {"input_tokens": 2, "cache_read_input_tokens": 899_998}, num_turns=2)
        assert _ctx(evs)[0]["tokensEst"] == 41_000
        assert unit.context_usage_snapshot()["basis"] == "post_compact"

    @pytest.mark.asyncio
    async def test_manual_compact_cancelled_by_timeout_drops_live_value(self):
        """Proactive callers wrap compact() in wait_for: a timeout cancels it with
        CancelledError, which `except Exception` does not see."""
        unit = _unit()
        unit.state = SessionState.IDLE
        unit._record_context_usage(800_000, basis="last_call")
        client = MagicMock()

        async def _q(**_kw):
            return None

        async def _hang():
            await asyncio.sleep(10)
            yield  # pragma: no cover

        client.query = _q
        client.receive_response = _hang
        unit._client = client
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(unit.compact(), timeout=0.05)
        assert unit.context_usage_snapshot() is None

    @pytest.mark.asyncio
    async def test_manual_compact_aborted_drain_drops_live_value(self):
        unit = _unit()
        unit.state = SessionState.IDLE
        unit._record_context_usage(800_000, basis="last_call")
        client = MagicMock()

        async def _q(**_kw):
            return None

        async def _boom():
            raise RuntimeError("killed mid-drain")
            yield  # pragma: no cover

        client.query = _q
        client.receive_response = _boom
        unit._client = client
        assert (await unit.compact())["success"] is False
        assert unit.context_usage_snapshot() is None

    @pytest.mark.asyncio
    async def test_manual_compact_without_boundary_clears_stale_value(self):
        unit = _unit()
        unit.state = SessionState.IDLE
        unit._record_context_usage(800_000, basis="last_call")
        client = MagicMock()

        async def _q(**_kw):
            return None

        client.query = _q
        client.receive_response = lambda: _Script([])
        unit._client = client
        assert (await unit.compact())["success"] is True
        assert unit.context_usage_snapshot() is None  # transcript is truth now


# ── Orchestrator read loop ──────────────────────────────────────────────


class TestReadLoopEmission:
    @pytest.mark.asyncio
    async def test_emits_after_each_main_agent_call_not_sub_agent(self):
        unit = _unit(sdk_id=None)
        call1 = AssistantMessage(content=[TextBlock(text="a")], model="m",
                                 usage={"input_tokens": 5, "cache_read_input_tokens": 300_000})
        call1b = AssistantMessage(content=[TextBlock(text="b")], model="m",
                                  usage={"input_tokens": 5, "cache_read_input_tokens": 300_000})
        sub = AssistantMessage(content=[TextBlock(text="s")], model="m",
                               parent_tool_use_id="tu-1",
                               usage={"input_tokens": 5, "cache_read_input_tokens": 20_000})
        call2 = AssistantMessage(content=[TextBlock(text="c")], model="m",
                                 usage={"input_tokens": 5, "cache_read_input_tokens": 350_000})
        no_io = AssertionError("no transcript I/O on the stream")
        with patch.object(context_usage, "read_transcript_usage", side_effect=no_io), \
             patch("core.leak_transcript.find_transcript", side_effect=no_io), \
             patch.object(context_usage, "snapshot_from_transcript", side_effect=no_io):
            events = await _drain(unit, [_sys("init", session_id=SDK_ID),
                                         call1, call1b, sub, call2])
        toks = [e["tokensEst"] for e in _ctx(events)]
        assert toks == [300_005, 350_005]
        assert all(e["source"] == "live" for e in _ctx(events))
        assert unit.context_usage_snapshot()["tokensEst"] == 350_005

    @pytest.mark.asyncio
    async def test_compact_boundary_yields_post_compact_snapshot(self):
        unit = _unit()
        events = await _drain(unit, [
            _sys("compact_boundary", compact_metadata={"trigger": "auto",
                                                       "pre_tokens": 752_000,
                                                       "post_tokens": 41_000})])
        kinds = [e["type"] for e in events]
        assert kinds.index("context_compacted") < kinds.index("context_warning")
        assert _ctx(events)[0]["tokensEst"] == 41_000


# ── Read endpoint ───────────────────────────────────────────────────────


class _Router:
    def __init__(self, unit=None, persisted=None):
        self._unit = unit
        self._persisted = persisted or {}

    def get_unit(self, sid):
        return self._unit

    def persisted_sdk_session_id(self, sid):
        return self._persisted.get(sid)


async def _get(router, sid="ctx-usage"):
    from routers.chat import get_context_usage_endpoint

    with patch("routers.chat._get_router", return_value=router):
        return await get_context_usage_endpoint(sid)


class TestEndpoint:
    @pytest.mark.asyncio
    async def test_live_snapshot_served(self):
        unit = _unit()
        unit._record_context_usage(420_000, basis="last_call")
        snap = await _get(_Router(unit))
        assert (snap["source"], snap["tokensEst"]) == ("live", 420_000)

    @pytest.mark.asyncio
    async def test_unit_without_live_data_derives_from_its_transcript(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
        _write(tmp_path, [_asst(270_000)])
        snap = await _get(_Router(_unit()))
        assert (snap["source"], snap["pct"]) == ("transcript", 27)

    @pytest.mark.asyncio
    async def test_no_unit_uses_persisted_id(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
        _write(tmp_path, [_asst(270_000)])
        snap = await _get(_Router(None, {"ctx-usage": SDK_ID}))
        assert snap["source"] == "transcript"

    @pytest.mark.asyncio
    async def test_cleared_unit_identity_beats_persisted_id(self, tmp_path, monkeypatch):
        """After refresh the unit has no sdk id: the next spawn rebuilds context,
        so a persisted id must NOT resurrect the pre-refresh transcript."""
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
        _write(tmp_path, [_asst(270_000)])
        snap = await _get(_Router(_unit(sdk_id=None), {"ctx-usage": SDK_ID}))
        assert snap["source"] == "none" and snap["pct"] is None

    @pytest.mark.asyncio
    async def test_live_value_recorded_during_the_read_wins(self, tmp_path, monkeypatch):
        """A per-call snapshot recorded while the transcript is read must be
        returned instead of the (older) derived answer."""
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
        unit = _unit()
        real = context_usage.snapshot_from_transcript

        def _read_then_live(*args):
            out = real(*args)
            unit._record_context_usage(333_000, basis="last_call")
            return out

        with patch.object(context_usage, "snapshot_from_transcript", side_effect=_read_then_live):
            snap = await _get(_Router(unit))
        assert (snap["source"], snap["tokensEst"]) == ("live", 333_000)

    @pytest.mark.asyncio
    async def test_identity_change_during_the_read_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
        _write(tmp_path, [_asst(270_000)])
        unit = _unit()
        real = context_usage.snapshot_from_transcript

        def _read_then_clear(*args):
            out = real(*args)
            unit._sdk_session_id = None  # e.g. a give-up cleared the identity
            return out

        with patch.object(context_usage, "snapshot_from_transcript", side_effect=_read_then_clear):
            snap = await _get(_Router(unit))
        assert snap["source"] == "none"

    @pytest.mark.asyncio
    async def test_derived_answer_is_stamped_at_request_time(self, tmp_path, monkeypatch):
        """A derived answer must beat a "none" pushed moments before the request
        (its transcript row is older than that push)."""
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
        _write(tmp_path, [_asst(270_000, ts="2020-01-01T00:00:00Z")])
        before = time.time()
        snap = await _get(_Router(_unit()))
        assert snap["source"] == "transcript" and snap["measuredAt"] >= before

    @pytest.mark.asyncio
    async def test_none_answer_is_stamped_at_request_time(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
        before = time.time()
        snap = await _get(_Router(_unit(sdk_id=None)))
        assert snap["source"] == "none"
        assert before <= snap["measuredAt"] <= time.time()

    @pytest.mark.asyncio
    async def test_unknown_session_is_none(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
        snap = await _get(_Router(None))
        assert snap["source"] == "none"
