"""Final session-lifecycle kick for CDF.

- ``IngestionSessionKickHook`` wakes the session lane after all extractors finish.
"""
from __future__ import annotations

from core.session_hooks import HookContext


class IngestionSessionKickHook:
    """Wake CDF after the complete session hook chain has enqueued its candidates."""

    @property
    def name(self) -> str:
        return "ingestion_session_kick"

    async def execute(self, context: HookContext) -> None:
        """Mark this session's enqueue window complete and wake its lane."""
        del context
        from core.ingestion.api import wake
        wake("session")
