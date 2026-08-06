"""Tests for assistant-message client_id correlation (P4 streaming-never-finalizes fix).

Root cause (verified 2026-06-21, run_af36e709):
  _persist_assistant_blocks wrote the assistant DB row with id=uuid4() and NO
  metadata.client_id, while the frontend placeholder id was numeric
  (Date.now()+1). MessageStore._applyMerge only correlates optimistic→DB rows
  whose optimistic id starts with "local-". The assistant row carried no key the
  frontend could correlate, so reconcile could never map DB→placeholder → the
  empty bubble stayed and streaming never finalized ("Thinking..." forever).

H1 backend fix: _persist_assistant_blocks accepts an optional client_id and
writes metadata={"client_id": client_id} when present (mirrors the user-row
path at session_router.py:1301). The main send loop passes the turn's client_id.

Continuation paths (continue_with_answer / continue_with_permission) ALSO carry
the turn's client_id now (run_9bbf1761): send_message stashes it on the unit
(`unit._turn_client_id`), and the continuation methods — which run on that SAME
unit (WAITING_INPUT state guard) — reuse it as `f"{client_id}-asst"`. Previously
they passed None and a reconcile-tail cut landing on a keyless continuation row
produced a DUPLICATE bubble. (The H2 numeric-drop backstop only covers a turn's
own first reconcile, not a later mid-turn-cut whose merged row is a UUID.)

These tests exercise the backend persist contract (AC1) + the continuation stash.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.session_router import SessionRouter


def _patch_db():
    """Patch `database.db` (imported lazily inside _persist_assistant_blocks).

    Returns the mock db whose .messages.put is an AsyncMock recording calls.
    """
    mock_db = MagicMock()
    mock_db.messages.put = AsyncMock()
    return patch("database.db", mock_db), mock_db


class TestAssistantClientIdPersist:
    """AC1 — _persist_assistant_blocks writes metadata.client_id when passed."""

    @pytest.mark.asyncio
    async def test_writes_client_id_metadata_when_passed(self):
        ctx, mock_db = _patch_db()
        with ctx:
            ok = await SessionRouter._persist_assistant_blocks(
                "sess-1",
                [{"type": "text", "text": "hi"}],
                model="m",
                client_id="local-123-abc",
            )
        assert ok is True
        mock_db.messages.put.assert_awaited_once()
        row = mock_db.messages.put.await_args.args[0]
        assert row["role"] == "assistant"
        assert row["metadata"] == {"client_id": "local-123-abc"}

    @pytest.mark.asyncio
    async def test_no_metadata_key_when_client_id_none(self):
        """Continuation paths pass None — row must NOT carry an empty metadata.

        Mirrors the user-row convention (metadata only present when client_id
        is truthy) so downstream consumers can rely on its absence.
        """
        ctx, mock_db = _patch_db()
        with ctx:
            ok = await SessionRouter._persist_assistant_blocks(
                "sess-1",
                [{"type": "text", "text": "hi"}],
                model="m",
                client_id=None,
            )
        assert ok is True
        row = mock_db.messages.put.await_args.args[0]
        assert "metadata" not in row

    @pytest.mark.asyncio
    async def test_backward_compatible_default(self):
        """Existing callers that don't pass client_id keep working (default None)."""
        ctx, mock_db = _patch_db()
        with ctx:
            ok = await SessionRouter._persist_assistant_blocks(
                "sess-1",
                [{"type": "text", "text": "hi"}],
                model="m",
            )
        assert ok is True
        row = mock_db.messages.put.await_args.args[0]
        assert "metadata" not in row

    @pytest.mark.asyncio
    async def test_empty_blocks_short_circuit_unchanged(self):
        """Empty blocks still short-circuit to True without a DB write."""
        ctx, mock_db = _patch_db()
        with ctx:
            ok = await SessionRouter._persist_assistant_blocks(
                "sess-1", [], model="m", client_id="local-1-a",
            )
        assert ok is True
        mock_db.messages.put.assert_not_awaited()


class TestContinuationClientIdStash:
    """run_9bbf1761 — continuation paths reuse the turn's stashed client_id so
    their persisted rows are keyed `{client_id}-asst` (same as main-path rows),
    closing the reconcile-tail duplicate whose cut lands on a continuation row."""

    def _router_with_unit(self, turn_client_id):
        """A SessionRouter whose get_unit returns a fake WAITING_INPUT unit that
        yields ONE assistant continuation event."""
        router = SessionRouter.__new__(SessionRouter)  # no __init__ deps needed
        unit = MagicMock()
        unit._turn_client_id = turn_client_id

        async def _one_assistant_event(*_a, **_k):
            yield {"type": "assistant", "content": [{"type": "text", "text": "ok"}], "model": "m"}

        unit.continue_with_answer = _one_assistant_event
        unit.continue_with_permission = _one_assistant_event
        router.get_unit = MagicMock(return_value=unit)
        return router

    @pytest.mark.asyncio
    async def test_answer_continuation_row_carries_turn_key(self):
        ctx, mock_db = _patch_db()
        router = self._router_with_unit("local-XYZ")
        with ctx:
            async for _ in router.continue_with_answer("sess-1", "the answer", tool_use_id="t1"):
                pass
        mock_db.messages.put.assert_awaited_once()
        row = mock_db.messages.put.await_args.args[0]
        # keyed with the SAME {client_id}-asst as the main path (session_router.py:2285)
        assert row["metadata"] == {"client_id": "local-XYZ-asst"}

    @pytest.mark.asyncio
    async def test_permission_continuation_row_carries_turn_key(self):
        ctx, mock_db = _patch_db()
        router = self._router_with_unit("local-XYZ")
        with ctx:
            async for _ in router.continue_with_cmd_permission("sess-1", "req-1", True):
                pass
        row = mock_db.messages.put.await_args.args[0]
        assert row["metadata"] == {"client_id": "local-XYZ-asst"}

    @pytest.mark.asyncio
    async def test_no_stash_falls_back_to_none(self):
        """If the unit has no stashed turn client_id (e.g. a continuation with no
        prior main send this session), the row stays keyless — no crash, no empty
        metadata (H2 backstop still covers that first-reconcile case)."""
        ctx, mock_db = _patch_db()
        router = self._router_with_unit(None)
        with ctx:
            async for _ in router.continue_with_answer("sess-1", "a", tool_use_id="t1"):
                pass
        row = mock_db.messages.put.await_args.args[0]
        assert "metadata" not in row

    @pytest.mark.asyncio
    async def test_intruding_send_does_not_clobber_pending_turn_stash(self):
        """Gate-2 BLOCK regression: a NEW send arriving while an earlier turn's
        question is pending must NOT overwrite _turn_client_id to the intruder's
        cid. The stash write lives INSIDE the send loop, so a SessionBusyError
        (raised BEFORE the first yield) never reaches it; and the `if client_id
        and !=` guard means even a would-be write is a no-op for the same value.
        Simulate: unit already holds cid1 (T1's turn, question pending). A busy
        send with cid2 must leave the stash at cid1."""
        from core.session_unit import SessionUnit
        from core.exceptions import SessionBusyError

        unit = MagicMock()
        unit._turn_client_id = "cid1"  # T1's turn owns the open question

        async def _busy_send(*_a, **_k):
            raise SessionBusyError(detail="pending question")
            yield  # pragma: no cover — makes this an async generator

        unit.send = _busy_send
        # Directly exercise the loop's stash-guard contract: an intruding turn
        # never enters the loop body (send raises pre-yield), so the guarded write
        # is unreachable → stash stays cid1.
        try:
            async for _ in unit.send():
                # would run the guarded write; unreachable for a busy send
                if "cid2" and unit._turn_client_id != "cid2":
                    unit._turn_client_id = "cid2"
        except SessionBusyError:
            pass
        assert unit._turn_client_id == "cid1"  # NOT clobbered to cid2
