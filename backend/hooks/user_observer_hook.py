"""Post-session hook that extracts user observations.

Implements the SessionLifecycleHook protocol to run after session close,
analyzing conversation messages for behavioral patterns.

Key public symbols:

- ``UserObserverHook`` — Hook that extracts and persists user observations.
"""
from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from core.session_hooks import HookContext, SessionLifecycleHook
from core.user_observer import UserObserver

logger = logging.getLogger(__name__)


class UserObserverHook:
    """Post-session hook that extracts user observations.

    Implements SessionLifecycleHook protocol.
    """

    @property
    def name(self) -> str:
        return "user-observer"

    async def execute(self, context: HookContext) -> None:
        """Extract observations from session messages.

        1. Load messages from DB for session_id
        2. Create UserObserver with .context/user_observations.jsonl path
        3. Call observe_session()
        4. Load existing observations
        5. Consolidate
        6. Save
        """
        try:
            from database import db

            # Load messages for this session
            messages_raw = await db.messages.list(
                filters={"session_id": context.session_id}
            )
            if not messages_raw:
                logger.debug("No messages for session %s, skipping observer", context.session_id)
                return

            messages = [
                {"role": m.get("role", ""), "content": m.get("content", "")}
                for m in messages_raw
            ]

            # Determine observations path
            from core.initialization_manager import initialization_manager
            ws_path = initialization_manager.get_cached_workspace_path()
            if not ws_path:
                logger.debug("No workspace path, skipping observer")
                return

            obs_path = Path(ws_path) / ".context" / "user_observations.jsonl"
            observer = UserObserver(observations_path=obs_path)

            # Extract new observations
            new_obs = observer.observe_session(messages, context.session_id)
            if not new_obs:
                return

            # Consolidate with existing
            existing = observer.load_existing()
            consolidated = observer.consolidate(new_obs, existing)

            # Save atomically: write consolidated set to temp file, then rename.
            # This prevents data loss if the process crashes mid-write.
            obs_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_fd, tmp_name = tempfile.mkstemp(
                dir=str(obs_path.parent), suffix=".tmp", prefix=".obs_"
            )
            tmp_path = Path(tmp_name)
            try:
                with open(tmp_fd, "w", encoding="utf-8") as f:
                    import json
                    from dataclasses import asdict
                    for obs in consolidated:
                        f.write(json.dumps(asdict(obs), ensure_ascii=False) + "\n")
                tmp_path.replace(obs_path)
            except Exception:
                tmp_path.unlink(missing_ok=True)
                raise

            logger.info(
                "UserObserver: %d new observations for session %s (%d total)",
                len(new_obs), context.session_id, len(consolidated),
            )

        except Exception as exc:
            logger.error("UserObserverHook failed: %s", exc, exc_info=True)
