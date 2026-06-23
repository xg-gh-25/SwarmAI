"""Answer state management for the AskUserQuestion human-in-the-loop flow.

Encapsulates the block/signal store for ``AskUserQuestion`` tool calls. Mirrors
``PermissionManager``'s wait/set/has_live_waiter semantics, but the result payload
is the user's *answers dict* (not an approve/deny string).

Why this exists:
    The Claude CLI's ``AskUserQuestion`` tool has
    ``checkPermissions → {behavior:"ask"}`` and ``requiresUserInteraction → true``.
    In headless/SDK mode there is no interactive UI to satisfy "ask", so the CLI
    self-resolves the tool_use with ``is_error:true, content:"Answer questions?"``
    ~19ms after emission — long before the user actually answers. The agent then
    gives up ("No answer, I'll proceed") and the real answer (arriving seconds-to-
    minutes later) lands on an already-resolved tool.

    The fix: a PreToolUse hook (``create_ask_question_gate``) intercepts the tool
    call BEFORE the CLI self-resolves it, BLOCKS on ``wait_for_answer()``, and once
    the user answers returns ``permissionDecision:"allow" + updatedInput.answers``.
    The CLI's ``call()`` then returns the real answers. This manager is the
    block/signal primitive that bridges the blocked hook and the user's answer.

Key correlation invariant:
    The waiter is keyed on the SDK ``tool_use_id`` (the AskUserQuestion block.id).
    That SAME id is:
      - delivered to the PreToolUse hook as its 2nd positional arg (SDK query.py),
      - surfaced to the frontend via the ``ask_user_question`` SSE event's
        ``toolUseId`` field,
      - passed back by ``continue_with_answer(tool_use_id=...)`` (chat.py + gateway).
    So ``wait_for_answer(tool_use_id)`` and ``set_answer(tool_use_id, answers)`` always
    agree on one id with zero extra plumbing.

State managed:
    - _answer_events: tool_use_id → asyncio.Event for signaling the answer arrived
    - _answer_results: tool_use_id → answers dict
"""

import asyncio
import logging
from typing import Any, Optional, Union

logger = logging.getLogger(__name__)

# Distinct sentinel for an un-answered timeout. Kept separate from an (possibly
# empty) answers dict so the caller can emit a visible "question expired" outcome
# rather than silently injecting empty answers. Mirrors PermissionManager's
# "timeout" string sentinel.
TIMEOUT_SENTINEL = "timeout"


class AskQuestionManager:
    """Block/signal store for AskUserQuestion answers, keyed on SDK tool_use_id.

    Provides:
    - ``wait_for_answer(tool_use_id)`` — block until the user answers (or timeout)
    - ``set_answer(tool_use_id, answers)`` — signal the blocked waiter with answers
    - ``has_live_waiter(tool_use_id)`` — respawn-immune liveness signal
    """

    def __init__(self) -> None:
        self._answer_events: dict[str, asyncio.Event] = {}
        self._answer_results: dict[str, dict[str, Any]] = {}

    def has_live_waiter(self, tool_use_id: str) -> bool:
        """True iff a ``wait_for_answer`` coroutine is currently blocked on this id.

        The respawn-immune liveness signal: the event is registered on entry to
        ``wait_for_answer`` and popped in its ``finally`` (on answer, timeout, OR
        cancellation when the subprocess is killed and the hook task is torn down).
        So this is True only while a real awaiting hook exists to receive the
        answer — surfacing a question with no live waiter would let the user
        "answer into the void" (mirrors PermissionManager.has_live_waiter).
        """
        return tool_use_id in self._answer_events

    async def wait_for_answer(
        self, tool_use_id: str, timeout: int = 300
    ) -> Union[dict[str, Any], str]:
        """Wait for the user's answers to an AskUserQuestion.

        Args:
            tool_use_id: The AskUserQuestion tool_use block id (== SDK block.id).
            timeout: Seconds to wait (default 5 minutes). Bounded so an
                un-surfaced or unanswered question does not block the subprocess
                for hours. The subprocess stays alive in WAITING_INPUT (protected
                from eviction) until the answer arrives OR the timeout fires.

        Returns:
            The answers dict on success, or ``TIMEOUT_SENTINEL`` ("timeout") if no
            answer arrived in time. The sentinel is DISTINCT from an empty answers
            dict so the caller can emit a visible expiry outcome rather than
            silently injecting empty answers.
        """
        event = asyncio.Event()
        self._answer_events[tool_use_id] = event

        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
            return self._answer_results.get(tool_use_id, {})
        except asyncio.TimeoutError:
            return TIMEOUT_SENTINEL
        finally:
            self._answer_events.pop(tool_use_id, None)
            self._answer_results.pop(tool_use_id, None)

    def set_answer(self, tool_use_id: str, answers: dict[str, Any]) -> None:
        """Record the user's answers and signal the waiting hook."""
        self._answer_results[tool_use_id] = answers
        event = self._answer_events.get(tool_use_id)
        if event is not None:
            event.set()
        else:
            # No waiter — clean up immediately to prevent a leak (mirrors
            # PermissionManager.set_permission_decision).
            self._answer_results.pop(tool_use_id, None)


# Module-level singleton
ask_question_manager = AskQuestionManager()
