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
path at session_router.py:1301). The main send loop passes the turn's client_id;
continuation paths (continue_with_answer / continue_with_permission) pass None
(client_id not in scope there) and rely on the H2 turn-end reconcile backstop.

These tests exercise the backend persist contract (AC1).
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
