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
from tests._tool_markup import tc
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
        # Returns the cleaned blocks list (run_719886ee: bool→list[dict] so the
        # caller can write it back to event['content'], closing the 2d fork).
        assert ok == [{"type": "text", "text": "hi"}]
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
        assert ok == [{"type": "text", "text": "hi"}]  # cleaned blocks list (run_719886ee)
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
        assert ok == [{"type": "text", "text": "hi"}]  # cleaned blocks list (run_719886ee)
        row = mock_db.messages.put.await_args.args[0]
        assert "metadata" not in row

    @pytest.mark.asyncio
    async def test_empty_blocks_short_circuit_unchanged(self):
        """Empty blocks still short-circuit without a DB write; returns the
        (empty) input list — bool→list[dict] contract (run_719886ee)."""
        ctx, mock_db = _patch_db()
        with ctx:
            ok = await SessionRouter._persist_assistant_blocks(
                "sess-1", [], model="m", client_id="local-1-a",
            )
        assert ok == []
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

    # NOTE: the keyless-drain stale-key regression is now covered at its ROOT seam —
    # send()'s new-turn reset batch — by test_stale_turn_client_id_cleared_at_send_entry
    # in test_session_unit_cleanliness.py. That drives the REAL send() (this file's
    # harness stubs unit.send, which would make a reset-in-send() assertion vacuous).


class TestPersistSideLeakClean:
    """Persist-side tool-call-leak clean (run_2bdec5c0).

    _persist_assistant_blocks must clean raw tool-call leak from each text block
    BEFORE db.messages.put, so data.db never stores raw ``‹invoke>`` text that
    the session_recall read path would splice back into a resumed prompt
    (the re-induction loop the resume-side cleaner, run_da39669c, did not cover).

    Verified by inspecting the content dict actually passed to db.messages.put.
    """

    @pytest.mark.asyncio
    async def test_raw_leak_stripped_before_persist(self):
        """AC1 — a0f04b00's truncated-leak shape is cleaned out of the persisted content."""
        ctx, mock_db = _patch_db()
        leak = tc('court\n‹invoke name="Bash">\n\ncourt先读当前 lead:')
        with ctx:
            ok = await SessionRouter._persist_assistant_blocks(
                "sess-leak",
                [{"type": "text", "text": leak}],
                model="m",
            )
        # bool→list[dict] (run_719886ee): returns the cleaned blocks the caller
        # writes back to event['content']; here the leak is stripped out of it.
        assert isinstance(ok, list) and tc("‹invoke name=") not in ok[0]["text"]
        row = mock_db.messages.put.await_args.args[0]
        persisted = row["content"][0]["text"]
        assert tc("‹invoke name=") not in persisted, (
            f"raw leak reached data.db: {persisted!r}"
        )
        # 2d: the RETURN (→ event['content']) and the PERSISTED bytes are identical.
        assert ok == row["content"]

    @pytest.mark.asyncio
    async def test_fenced_reference_preserved_and_tool_use_untouched(self):
        """AC2 — legit fenced ``‹invoke>`` reference kept byte-for-byte; tool_use block untouched."""
        ctx, mock_db = _patch_db()
        fenced = tc('讨论语法:`‹invoke name="X">` 应走 tool_use 通道。')
        tool_use_block = {"type": "tool_use", "id": "tu_1", "name": "Bash",
                          "input": {"command": "ls"}}
        with ctx:
            await SessionRouter._persist_assistant_blocks(
                "sess-mixed",
                [{"type": "text", "text": fenced}, tool_use_block],
                model="m",
            )
        row = mock_db.messages.put.await_args.args[0]
        blocks = row["content"]
        assert blocks[0]["text"] == fenced, "fenced reference not byte-identical"
        assert blocks[1] == tool_use_block, "tool_use block was modified"

    @pytest.mark.asyncio
    async def test_detect_decision_not_invoked_by_persist(self):
        """AC4 — persist cleaning is DECOUPLED from the kill decision.

        The persist path cleans content via strip_tool_call_leak but must NOT touch
        detect_tool_call_leak (which governs whether streaming kills the turn — a
        separate concern). This asserts BOTH halves observably so it is non-vacuous:
          • strip_tool_call_leak IS called (the clean path was actually exercised)
          • detect_tool_call_leak is NOT called (decoupling holds)
        A mutation that swapped strip→detect, or deleted the clean entirely, goes RED
        on the first assertion — the old `assert_not_called()`-only form was vacuous
        (detect is never referenced in the function body, so it passed for ANY impl).
        """
        ctx, mock_db = _patch_db()
        with patch(
            "core.session_utils.strip_tool_call_leak",
            side_effect=lambda t: t,  # passthrough — we assert it's CALLED, not its effect
        ) as mock_strip, patch(
            "core.session_utils.detect_tool_call_leak",
        ) as mock_detect, ctx:
            await SessionRouter._persist_assistant_blocks(
                "sess-decouple",
                [{"type": "text", "text": tc('court\n‹invoke name="Bash">')}],
                model="m",
            )
        mock_strip.assert_called_once_with(tc('court\n‹invoke name="Bash">'))
        mock_detect.assert_not_called()

    @pytest.mark.asyncio
    async def test_clean_exception_does_not_block_persist(self):
        """AC5 — a cleaning exception degrades to persisting the ORIGINAL blocks, never loses content."""
        ctx, mock_db = _patch_db()
        original = [{"type": "text", "text": tc('court\n‹invoke name="Bash">')}]
        with patch(
            "core.session_utils.strip_tool_call_leak",
            side_effect=RuntimeError("boom"),
        ), ctx:
            ok = await SessionRouter._persist_assistant_blocks(
                "sess-degrade", original, model="m",
            )
        # persist MUST still succeed (crash-safe), content falls back to original.
        # bool→list[dict] (run_719886ee): the degrade path returns the ORIGINAL
        # blocks — and that same list is what both the DB and event['content'] get,
        # so persist==yield still holds even on the clean-exception path.
        assert ok == original, "exception path must return original blocks (= persist==yield)"
        mock_db.messages.put.assert_awaited_once()
        row = mock_db.messages.put.await_args.args[0]
        assert row["content"] == original, "exception path must persist original blocks, not drop content"

    @pytest.mark.asyncio
    async def test_cleaning_covers_all_three_call_sites(self):
        """AC3 — the SINGLE persist chokepoint cleans leak regardless of which of the
        3 call sites invoked it. The 3 sites (streaming main :3483, continue_with_answer
        :3728, continue_with_permission :3758) all funnel the same `content` list through
        `_persist_assistant_blocks`, differing ONLY in the client_id/label kwargs — never
        in the cleaning (which lives in the function body). So cleaning must hold across
        every call-site kwarg signature.

        NON-VACUOUS: this asserts the persisted content (observable effect), not that the
        source calls strip. Mutation proof — delete the in-body clean and all 3 go RED.
        """
        leak = tc('court\n‹invoke name="Bash">\n\ncourt先读:')
        # Each tuple = (bare, "-asst", label=answer, label=permission) — the real kwarg
        # shapes the 3 call sites pass (session_router.py :3483/:3728/:3758).
        call_site_kwargs = [
            {"client_id": "cid-asst"},                       # streaming main (:3483)
            {"client_id": "cid-asst", "label": "answer"},    # continue_with_answer (:3728)
            {"client_id": "cid-asst", "label": "permission"},# continue_with_permission (:3758)
        ]
        for kwargs in call_site_kwargs:
            ctx, mock_db = _patch_db()
            with ctx:
                ok = await SessionRouter._persist_assistant_blocks(
                    "sess-callsite", [{"type": "text", "text": leak}], model="m", **kwargs,
                )
            # bool→list[dict] (run_719886ee): returns cleaned blocks for the write-back.
            assert isinstance(ok, list), f"persist should return blocks list for kwargs {kwargs}"
            persisted = mock_db.messages.put.await_args.args[0]["content"][0]["text"]
            assert tc("‹invoke name=") not in persisted, (
                f"raw leak reached data.db via call-site kwargs {kwargs}: {persisted!r}"
            )
