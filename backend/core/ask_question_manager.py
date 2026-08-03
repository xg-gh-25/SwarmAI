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
from typing import Any, Union

logger = logging.getLogger(__name__)

# Distinct sentinel for an un-answered timeout. Kept separate from an (possibly
# empty) answers dict so the caller can emit a visible "question expired" outcome
# rather than silently injecting empty answers. Mirrors PermissionManager's
# "timeout" string sentinel.
TIMEOUT_SENTINEL = "timeout"

# How long a blocked AskUserQuestion hook waits for the user's answer before
# giving up. 4 hours — a human may leave a question open across a meal, a
# meeting, or a nap. The old value (300s, copied from permission_manager) was
# far too short: it resolved before the user returned, reviving the original
# "agent proceeds without the answer" bug. On expiry the hook DENIES the tool
# (question expired → agent re-asks), never injecting a fabricated empty answer.
# Upper bound is still the session's 12h TTL; 4h leaves ample headroom while
# not pinning a blocked subprocess for the session's entire lifetime.
ASK_ANSWER_TIMEOUT_SECONDS = 14400


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
        """True iff a waiter event exists for this id (a hook is — or is about to
        be — blocked on the answer).

        The respawn-immune liveness signal. The event is registered either by
        ``register_waiter`` (synchronously, just before the question is surfaced)
        or on entry to ``wait_for_answer``, and popped in ``wait_for_answer``'s
        ``finally`` (answer, timeout, OR cancellation when the subprocess is
        killed). If the surface→await span throws before ``wait_for_answer`` is
        entered, the caller MUST ``discard_waiter`` to reap the event — otherwise
        this returns True with no coroutine blocked, defeating the orchestrator's
        stale-item drop-guard (→ "answer into the void"). Mirrors
        PermissionManager.has_live_waiter.
        """
        return tool_use_id in self._answer_events

    async def wait_for_answer(
        self, tool_use_id: str, timeout: int = ASK_ANSWER_TIMEOUT_SECONDS
    ) -> Union[dict[str, Any], str]:
        """Wait for the user's answers to an AskUserQuestion.

        Args:
            tool_use_id: The AskUserQuestion tool_use block id (== SDK block.id).
            timeout: Seconds to wait (default 4 hours — see
                ASK_ANSWER_TIMEOUT_SECONDS). A human may step away (meal, meeting,
                nap); the subprocess stays alive in WAITING_INPUT (protected from
                eviction) until the answer arrives OR the timeout fires. On
                timeout the caller DENIES the tool (question expired), never
                injecting a fabricated empty answer.

        Returns:
            The answers dict on success, or ``TIMEOUT_SENTINEL`` ("timeout") if no
            answer arrived in time. The sentinel is DISTINCT from an empty answers
            dict so the caller can emit a visible expiry outcome rather than
            silently injecting empty answers.
        """
        # Reuse a pre-registered event if register_waiter() was called before the
        # question was surfaced (F3) — otherwise create one now. Creating here
        # only on first await opened a window: the hook surfaced the question
        # (enqueue) BEFORE this coroutine ran, so a fast non-human set_answer
        # arriving in between found no waiter and dropped. register_waiter closes
        # that window; this reuse makes wait_for_answer idempotent with it.
        event = self._answer_events.get(tool_use_id)
        if event is None:
            event = asyncio.Event()
            self._answer_events[tool_use_id] = event
        # An answer may have ALREADY arrived between register_waiter and this
        # await — set_answer would have set the event; wait returns immediately.

        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
            return self._answer_results.get(tool_use_id, {})
        except asyncio.TimeoutError:
            return TIMEOUT_SENTINEL
        finally:
            self._answer_events.pop(tool_use_id, None)
            self._answer_results.pop(tool_use_id, None)

    def register_waiter(self, tool_use_id: str) -> None:
        """Synchronously register a waiter BEFORE the question is surfaced (F3).

        The ask_question_gate hook surfaces the question (enqueue) and only then
        awaits wait_for_answer — which previously created the event on its first
        line. A fast non-human auto-answer (channel gateway) arriving in that
        window found no live waiter and was dropped by set_answer. Calling this
        before surfacing guarantees the waiter exists, so set_answer always has a
        target. Idempotent: re-registering reuses the existing event."""
        if tool_use_id not in self._answer_events:
            self._answer_events[tool_use_id] = asyncio.Event()

    def discard_waiter(self, tool_use_id: str) -> None:
        """Reap a registered waiter when wait_for_answer will NEVER be entered
        (the surface→await window threw or was cancelled). Without this, a
        register_waiter() whose wait_for_answer never runs leaks the Event in
        _answer_events forever — and a leaked entry makes has_live_waiter() return
        True with no coroutine blocked, defeating the orchestrator's stale-item
        drop-guard (→ ghost question / answer-into-void). wait_for_answer's own
        finally already covers the case where it WAS entered; this covers the gap
        before it. Idempotent."""
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
