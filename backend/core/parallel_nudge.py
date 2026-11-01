"""parallel_nudge — after each round of tool results, remind the model to batch.

Why this exists
---------------
In coding loops (bash + editor harnesses) the model often issues ONE tool call per
step even when the next calls do not depend on each other, so every extra read costs
a whole model round trip. Measured on one install's main sessions over 7 days: 3% of
steps carried more than one call, and a third of all steps were a single read that
directly followed another single read. A one-line parallel instruction already sits in
the system prompt, and it does not hold over a long loop.

Anthropic's own prompting guide describes this pattern and its fix: append a
one-sentence nudge after each round of tool results, rather than stating it once at
the top ("Batch independent tool calls in agent loops",
https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-fable-5-1).
The guide writes it for Claude Fable 5.1; here it is applied to every model, which is
why its effect is to be measured (calls per step, share of consecutive single reads),
not assumed.

The second sentence handles a known Claude Code CLI behaviour: when one Bash call in
a parallel batch exits non-zero (a grep with no match exits 1), the CLI cancels the
other calls in that batch (anthropics/claude-code#63576, open). Batching reads is safe
only if a no-match search does not fail the batch.

Mechanism
---------
A PostToolUse hook on every tool returns the nudge as additionalContext, which the CLI
places after the tool result. When the model issues several calls in one step, each
call's PostToolUse fires; only the first within ``_SAME_ROUND_SECONDS`` injects, so a
batched step usually carries one copy. The window is a cost saver, not a correctness
boundary: a batch whose calls finish further apart gets a second copy, and a following
step that returns within the window goes without one while the earlier copy is still in
context. Neither changes what the model can do.

Inject-only: never denies, never blocks, returns {} on any error.
"""

from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

NUDGE = (
    "First privately list what you need next; then request every item that doesn't "
    "depend on another's result in this one response. In a batch, one Bash call that "
    "exits non-zero cancels the others, so end a batched search with `|| true`."
)

# Calls of one parallel step finish close together; the next step needs a full model
# round trip (seconds) first. See the module docstring for what the window is not.
_SAME_ROUND_SECONDS = 2.0
_STATE_KEY = "_parallel_nudge_last"


def create_parallel_nudge_hook(session_context: "dict[str, Any]"):
    """Factory for the PostToolUse hook (all tools). Never denies."""

    async def parallel_nudge(input_data: Any, tool_use_id: Any, context: Any) -> dict:
        try:
            now = time.monotonic()
            last = session_context.get(_STATE_KEY)
            if isinstance(last, (int, float)) and 0 <= now - last < _SAME_ROUND_SECONDS:
                return {}
            session_context[_STATE_KEY] = now
            return {"hookSpecificOutput": {"hookEventName": "PostToolUse",
                                           "additionalContext": NUDGE}}
        except Exception:  # noqa: BLE001 — inject-only: an error means no nudge
            logger.debug("parallel_nudge failed open", exc_info=True)
            return {}

    return parallel_nudge
