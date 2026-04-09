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

            # Close the loop: surface USER.md suggestions when patterns emerge
            self._maybe_suggest_updates(observer, consolidated, ws_path)

        except Exception as exc:
            logger.error("UserObserverHook failed: %s", exc, exc_info=True)

    @staticmethod
    def _maybe_suggest_updates(
        observer: UserObserver,
        consolidated: list,
        ws_path: str,
    ) -> None:
        """Check for enough observations to suggest USER.md updates.

        Writes suggestions to .context/user_suggestions.md for the agent
        to surface during the next session's proactive briefing.
        """
        try:
            suggestions = observer.suggest_user_md_updates(consolidated)
            if not suggestions:
                return

            suggestions_path = Path(ws_path) / ".context" / "user_suggestions.md"
            # Write (overwrite) — only latest suggestions matter
            content = "## Suggested USER.md Updates\n\n"
            content += "_Based on observed patterns across sessions:_\n\n"
            for s in suggestions:
                content += f"- {s}\n"
            content += (
                "\n_Review and apply manually, or ask Swarm to update USER.md._\n"
            )
            suggestions_path.write_text(content, encoding="utf-8")
            logger.info(
                "UserObserver: wrote %d USER.md suggestions to %s",
                len(suggestions), suggestions_path,
            )
        except Exception as exc:
            logger.debug("Failed to write user suggestions: %s", exc)
