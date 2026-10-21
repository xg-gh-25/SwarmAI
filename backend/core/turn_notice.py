"""Why a turn's last call did not finish, as a persisted ``turn_notice`` block.

The backend restarts the AI process mid-turn (a stall after sleep, a crash) or hands
the conversation to a newer message; the chat only saw a call with no result and
said "Stopped · the last call did not finish" with no reason (2026-10-20). Each such
point now yields one ``assistant`` event carrying a ``turn_notice`` block, which the
router persists like any assistant row, so the reason is there live and after a
reload. It is a display block: ``context_injector`` keeps it out of resume history.

- ``turn_notice_event(reason, cause=None)`` — the event to yield.
- ``REASONS`` — the reason codes the frontend renders.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

#: restarted  — the AI process was restarted mid-turn; the call it ran was lost.
#: superseded — a newer message resumed the conversation; this turn stopped.
#: gave_up    — historical: written by the removed disconnect auto-continue when it ran
#:              out of attempts. No longer produced (a disconnect no longer stops a
#:              turn); kept so rows already in the DB still render.
REASONS = ("restarted", "superseded", "gave_up")


def turn_notice_event(reason: str, cause: Optional[str] = None) -> dict:
    """One assistant event with a single ``turn_notice`` block."""
    block = {"type": "turn_notice", "reason": reason,
             "at": datetime.now(timezone.utc).isoformat()}
    if cause:
        block["cause"] = cause
    return {"type": "assistant", "content": [block]}
