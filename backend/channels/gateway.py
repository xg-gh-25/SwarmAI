"""Channel gateway -- manages adapter lifecycle and routes inbound messages to agents.

This is a singleton that runs within the FastAPI process.  It is responsible for:

* Starting / stopping channel adapters as asyncio tasks.
* Routing every :class:`InboundMessage` from an adapter to the correct agent
  via ``agent_manager.run_conversation``, accumulating the reply, and sending
  the :class:`OutboundMessage` back through the adapter.
* Maintaining a mapping between external conversations and internal sessions.
* Simple per-sender rate limiting and access-control checks.
"""
from __future__ import annotations

import asyncio
import logging
import re as _re
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Optional
from uuid import uuid4

from channels.base import ChannelAdapter, InboundMessage, OutboundMessage
from channels.registry import get_adapter_class, load_adapters
from core.agent_manager import agent_manager
from core.session_manager import session_manager
from core.workspace_manager import workspace_manager
from database import db

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Rate-limiter helpers
# ---------------------------------------------------------------------------

class _TokenBucketRateLimiter:
    """Very lightweight per-sender rate limiter backed by a sliding-window
    list of timestamps.  Not intended for high-throughput production use --
    good enough for a desktop application with a handful of channels.
    """

    def __init__(self):
        # sender_id -> list of Unix timestamps (most recent last)
        self._windows: dict[str, list[float]] = defaultdict(list)

    def is_allowed(self, sender_id: str, max_per_minute: int) -> bool:
        """Return True if *sender_id* is within the rate limit."""
        if max_per_minute <= 0:
            return True
        now = time.time()
        window = self._windows[sender_id]
        # Evict entries older than 60 s
        cutoff = now - 60.0
        window[:] = [ts for ts in window if ts > cutoff]
        if len(window) >= max_per_minute:
            return False
        window.append(now)
        return True

    def clear(self, sender_id: Optional[str] = None) -> None:
        if sender_id:
            self._windows.pop(sender_id, None)
        else:
            self._windows.clear()


# ---------------------------------------------------------------------------
# ChannelGateway
# ---------------------------------------------------------------------------

class ChannelGateway:
    """Singleton gateway that owns channel adapter lifecycle and message routing."""

    def __init__(self) -> None:
        # channel_id -> running ChannelAdapter instance
        self._adapters: dict[str, ChannelAdapter] = {}
        # channel_id -> asyncio.Task running the adapter's ``start()``
        self._tasks: dict[str, asyncio.Task] = {}
        # Per-sender rate limiter (shared across all channels)
        self._rate_limiter = _TokenBucketRateLimiter()
        # In-memory cache of channel configs keyed by channel_id
        self._channel_cache: dict[str, dict] = {}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def startup(self) -> None:
        """Called once during FastAPI lifespan startup.

        Loads adapter modules via the registry, then starts every channel
        whose DB status is ``'active'``.
        """
        logger.info("ChannelGateway starting up")
        load_adapters()

        channels = await db.channels.list()
        active_channels = [ch for ch in channels if ch.get("status") == "active"]
        logger.info(f"Found {len(active_channels)} active channel(s) to start")

        for ch in active_channels:
            try:
                await self.start_channel(ch["id"])
            except Exception:
                logger.exception(f"Failed to start channel {ch['id']} ({ch.get('name')}) during startup")

    async def shutdown(self) -> None:
        """Gracefully stop every running channel."""
        logger.info("ChannelGateway shutting down")
        channel_ids = list(self._adapters.keys())
        for channel_id in channel_ids:
            try:
                await self.stop_channel(channel_id)
            except Exception:
                logger.exception(f"Error stopping channel {channel_id} during shutdown")
        self._rate_limiter.clear()
        self._channel_cache.clear()
        logger.info("ChannelGateway shutdown complete")

    # ------------------------------------------------------------------
    # Channel start / stop / restart
    # ------------------------------------------------------------------

    async def start_channel(self, channel_id: str) -> None:
        """Load a channel from DB, instantiate its adapter, and start it.

        Updates the channel status to ``'active'`` on success or ``'error'``
        on failure.
        """
        if channel_id in self._adapters:
            logger.warning(f"Channel {channel_id} is already running; stopping first")
            await self.stop_channel(channel_id)

        channel = await db.channels.get(channel_id)
        if not channel:
            raise ValueError(f"Channel {channel_id} not found in database")

        channel_type = channel.get("channel_type", "")
        adapter_cls = get_adapter_class(channel_type)
        if adapter_cls is None:
            error_msg = f"No adapter registered for channel type '{channel_type}'"
            logger.error(error_msg)
            await db.channels.update(channel_id, {"status": "error", "error_message": error_msg})
            raise ValueError(error_msg)

        config = channel.get("config", {})
        if isinstance(config, str):
            import json
            try:
                config = json.loads(config)
            except json.JSONDecodeError:
                config = {}

        # Create the adapter, injecting our message handler
        adapter = adapter_cls(
            channel_id=channel_id,
            config=config,
            on_message=self.handle_inbound_message,
        )

        # Validate config before attempting to start
        is_valid, validation_error = await adapter.validate_config()
        if not is_valid:
            error_msg = f"Invalid config for channel {channel_id}: {validation_error}"
            logger.error(error_msg)
            await db.channels.update(channel_id, {"status": "error", "error_message": error_msg})
            raise ValueError(error_msg)

        # Cache the channel record
        self._channel_cache[channel_id] = channel

        # Wrap adapter.start() in an asyncio task so it can run concurrently
        async def _run_adapter(cid: str, adp: ChannelAdapter) -> None:
            try:
                await adp.start()
            except asyncio.CancelledError:
                logger.info(f"Adapter task for channel {cid} cancelled")
            except Exception:
                logger.exception(f"Adapter for channel {cid} crashed")
                await db.channels.update(cid, {
                    "status": "error",
                    "error_message": "Adapter crashed unexpectedly",
                })
                # Clean up references
                self._adapters.pop(cid, None)
                self._tasks.pop(cid, None)
                self._channel_cache.pop(cid, None)

        task = asyncio.create_task(_run_adapter(channel_id, adapter))
        self._adapters[channel_id] = adapter
        self._tasks[channel_id] = task

        await db.channels.update(channel_id, {"status": "active", "error_message": None})
        logger.info(f"Channel {channel_id} ({channel.get('name')}) started successfully")

    async def stop_channel(self, channel_id: str) -> None:
        """Stop a running channel adapter and update DB status to ``'inactive'``."""
        adapter = self._adapters.pop(channel_id, None)
        task = self._tasks.pop(channel_id, None)
        self._channel_cache.pop(channel_id, None)

        if adapter is not None:
            try:
                await adapter.stop()
            except Exception:
                logger.exception(f"Error in adapter.stop() for channel {channel_id}")

        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception(f"Error cancelling task for channel {channel_id}")

        await db.channels.update(channel_id, {"status": "inactive", "error_message": None})
        logger.info(f"Channel {channel_id} stopped")

    async def restart_channel(self, channel_id: str) -> None:
        """Stop and re-start a channel."""
        await self.stop_channel(channel_id)
        await self.start_channel(channel_id)

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    async def get_channel_status(self, channel_id: str) -> dict:
        """Return a runtime status dict for a channel.

        Returns keys that match :class:`ChannelStatusResponse`:
        ``status``, ``uptime_seconds``, ``messages_processed``,
        ``active_sessions``, ``error_message``.
        """
        is_running = channel_id in self._adapters

        # Determine status string
        if is_running:
            status = "active"
        else:
            channel = await db.channels.get(channel_id)
            status = channel.get("status", "inactive") if channel else "inactive"

        # Count active sessions from DB
        active_sessions = 0
        try:
            active_sessions = await db.channel_sessions.count_by_channel(channel_id)
        except Exception:
            pass

        # Fetch error_message from cached or DB channel record
        error_message = None
        cached = self._channel_cache.get(channel_id)
        if cached:
            error_message = cached.get("error_message")
        elif not is_running:
            ch = await db.channels.get(channel_id)
            if ch:
                error_message = ch.get("error_message")

        return {
            "channel_id": channel_id,
            "status": status,
            "uptime_seconds": None,
            "messages_processed": 0,
            "active_sessions": active_sessions,
            "error_message": error_message,
        }

    # ------------------------------------------------------------------
    # Inbound message handling (core routing logic)
    # ------------------------------------------------------------------

    async def handle_inbound_message(self, msg: InboundMessage) -> None:
        """Route an inbound message from a channel adapter to the right agent.

        Steps:
        1. Load channel config (cache-first).
        2. Access control check.
        3. Rate limiting.
        4. Resolve / create internal session.
        5. Run agent conversation and accumulate assistant reply text.
        6. Send outbound reply via adapter.
        7. Log inbound and outbound messages.
        """
        channel_id = msg.channel_id
        logger.info(
            f"Inbound message on channel {channel_id} from "
            f"{msg.sender_display_name or msg.external_sender_id}"
        )

        # 1. Load channel config -------------------------------------------------
        channel = self._channel_cache.get(channel_id)
        if not channel:
            channel = await db.channels.get(channel_id)
            if not channel:
                logger.error(f"Channel {channel_id} not found; dropping message")
                return
            self._channel_cache[channel_id] = channel

        agent_id = channel.get("agent_id")
        if not agent_id:
            logger.error(f"Channel {channel_id} has no agent_id; dropping message")
            return

        # 2. Access control -------------------------------------------------------
        if not self._check_access(channel, msg.external_sender_id):
            logger.warning(
                f"Access denied for sender {msg.external_sender_id} "
                f"on channel {channel_id}"
            )
            return

        # 3. Rate limiting --------------------------------------------------------
        rate_limit = channel.get("rate_limit_per_minute", 10)
        if not self._rate_limiter.is_allowed(msg.external_sender_id, rate_limit):
            logger.warning(
                f"Rate limit exceeded for sender {msg.external_sender_id} "
                f"on channel {channel_id}"
            )
            # Best effort: send a polite rate-limit notice back to the user
            adapter = self._adapters.get(channel_id)
            if adapter:
                try:
                    await adapter.send_message(OutboundMessage(
                        channel_id=channel_id,
                        external_chat_id=msg.external_chat_id,
                        external_thread_id=msg.external_thread_id,
                        reply_to_message_id=msg.external_message_id,
                        text="You are sending messages too quickly. Please wait a moment and try again.",
                    ))
                except Exception:
                    logger.exception("Failed to send rate-limit notice")
            return

        # 4. Resolve / create internal session ------------------------------------
        try:
            session_id, channel_session_id, _is_new = await self._resolve_session(
                channel_id=channel_id,
                agent_id=agent_id,
                external_chat_id=msg.external_chat_id,
                external_sender_id=msg.external_sender_id,
                external_thread_id=msg.external_thread_id,
                sender_display_name=msg.sender_display_name,
            )
        except Exception:
            logger.exception(f"Failed to resolve session for channel {channel_id}")
            return

        # Log inbound message to channel_messages ---------------------------------
        inbound_record_id = str(uuid4())
        try:
            await db.channel_messages.put({
                "id": inbound_record_id,
                "channel_session_id": channel_session_id,
                "direction": "inbound",
                "external_message_id": msg.external_message_id,
                "content": msg.text or "[Attachment message]",
                "content_type": msg.metadata.get("message_type", "text"),
                "metadata": {
                    **msg.metadata,
                    "attachment_count": len(msg.attachments),
                    "attachment_names": [a.get("file_name") for a in msg.attachments],
                },
                "status": "received",
            })
        except Exception:
            logger.exception("Failed to log inbound channel message")

        # 5. Run agent conversation -----------------------------------------------
        enable_skills = bool(channel.get("enable_skills", False))
        enable_mcp = bool(channel.get("enable_mcp", False))

        # Build channel context for MCP tool injection (e.g. send_file)
        channel_config = channel.get("config", {})
        if isinstance(channel_config, str):
            import json
            try:
                channel_config = json.loads(channel_config)
            except json.JSONDecodeError:
                channel_config = {}

        channel_context = {
            "channel_type": channel.get("channel_type", ""),
            "channel_id": channel_id,
            "chat_id": msg.external_chat_id,
            "reply_to_message_id": msg.external_message_id,
            # Extract only the credential keys needed by channel adapters
            "app_id": channel_config.get("app_id", ""),
            "app_secret": channel_config.get("app_secret", ""),
        }

        # Prepare message text (stages attachments to workspace if present)
        final_text = await self._prepare_message_text(msg, agent_id)

        reply_text = ""
        error_occurred = False
        try:
            # For new sessions (is_new=True, i.e. message_count==0), pass
            # session_id=None so the SDK creates a fresh session.  The SDK
            # will return its own session_id in the session_start event,
            # which we store for future resumption.
            # For existing sessions that completed at least one successful
            # exchange, the stored session_id IS the SDK session_id and
            # can be used to resume.
            resume_sid = None if _is_new else session_id
            async for event in agent_manager.run_conversation(
                agent_id=agent_id,
                user_message=final_text,
                session_id=resume_sid,
                enable_skills=enable_skills,
                enable_mcp=enable_mcp,
                channel_context=channel_context,
            ):
                event_type = event.get("type", "")

                if event_type == "assistant":
                    # Keep only the last assistant message as the reply.
                    # In multi-step conversations (assistant speaks → tool
                    # call → assistant speaks again), intermediate messages
                    # are not meaningful to send back; only the final
                    # response matters.
                    current_text = ""
                    content_blocks = event.get("content", [])
                    for block in content_blocks:
                        if isinstance(block, dict) and block.get("type") == "text":
                            text = block.get("text", "")
                            if text:
                                current_text += text
                    if current_text:
                        reply_text = current_text

                elif event_type == "session_start":
                    # If the SDK provides a new session ID, update our mapping
                    new_sid = event.get("sessionId")
                    if new_sid and new_sid != session_id:
                        session_id = new_sid
                        # Update the channel_session record to point to the
                        # SDK-assigned session ID
                        try:
                            await db.channel_sessions.update(
                                channel_session_id,
                                {"session_id": session_id},
                            )
                        except Exception:
                            logger.exception("Failed to update channel_session with new session_id")

                elif event_type == "result":
                    # Conversation finished — may include an error subtype
                    subtype = event.get("subtype", "")
                    cost = event.get("totalCostUsd")
                    duration = event.get("durationMs")
                    logger.info(
                        f"Agent conversation complete for channel {channel_id} "
                        f"session {session_id} "
                        f"(subtype={subtype}, cost=${cost}, duration={duration}ms)"
                    )
                    if subtype and "error" in subtype:
                        error_detail = event.get("error") or event.get("message") or subtype
                        logger.error(
                            f"Agent result error on channel {channel_id}: {error_detail}"
                        )
                        if not reply_text:
                            reply_text = f"Sorry, the agent encountered an error: {error_detail}"
                        error_occurred = True

                elif event_type == "error":
                    error_msg = event.get("error") or event.get("message") or "Unknown error"
                    logger.error(
                        f"Agent error on channel {channel_id}: {error_msg}"
                    )
                    if not reply_text:
                        reply_text = "Sorry, I encountered an error processing your request."
                    error_occurred = True

                # Silently consume tool_use, tool_result, ask_user_question, etc.

        except Exception:
            logger.exception(f"Exception running agent conversation on channel {channel_id}")
            reply_text = "Sorry, an unexpected error occurred. Please try again later."
            error_occurred = True

        if not reply_text:
            reply_text = "(No response generated)"

        # 6. Send outbound reply --------------------------------------------------
        outbound = OutboundMessage(
            channel_id=channel_id,
            external_chat_id=msg.external_chat_id,
            external_thread_id=msg.external_thread_id,
            reply_to_message_id=msg.external_message_id,
            text=reply_text,
        )

        external_message_id: Optional[str] = None
        adapter = self._adapters.get(channel_id)
        if adapter:
            try:
                external_message_id = await adapter.send_message(outbound)
            except Exception:
                logger.exception(f"Failed to send outbound message on channel {channel_id}")

        # 7. Log outbound message -------------------------------------------------
        try:
            await db.channel_messages.put({
                "id": str(uuid4()),
                "channel_session_id": channel_session_id,
                "direction": "outbound",
                "external_message_id": external_message_id,
                "content": reply_text,
                "content_type": "text",
                "metadata": {},
                "status": "error" if error_occurred else "sent",
            })
        except Exception:
            logger.exception("Failed to log outbound channel message")

        # Update channel_session last_message_at & message_count ----------------
        # Only increment message_count on success so that failed first
        # attempts keep message_count == 0, allowing the next attempt to
        # start a fresh SDK session instead of trying to resume.
        try:
            updates = {"last_message_at": datetime.now().isoformat()}
            if not error_occurred:
                existing_cs = await db.channel_sessions.get(channel_session_id)
                count = (existing_cs.get("message_count", 0) if existing_cs else 0) + 2
                updates["message_count"] = count
            await db.channel_sessions.update(channel_session_id, updates)
        except Exception:
            logger.exception("Failed to update channel_session counters")

    # ------------------------------------------------------------------
    # Attachment staging
    # ------------------------------------------------------------------

    async def _prepare_message_text(self, msg: InboundMessage, agent_id: str) -> str:
        """Build the final message text, staging any attachments to the agent workspace.

        If no attachments are present, returns ``msg.text`` unchanged.
        Otherwise stages each file and appends path info to the text.
        """
        if not msg.attachments:
            return msg.text

        staged_lines: list[str] = []
        for attachment in msg.attachments:
            file_name = attachment.get("file_name", "attachment")
            file_bytes = attachment.get("file_bytes", b"")
            if not file_bytes:
                continue
            path = await self._stage_file_to_workspace(agent_id, file_name, file_bytes)
            if path:
                staged_lines.append(f"[File '{file_name}' saved to: {path}]")

        if not staged_lines and not msg.text:
            return ""

        parts: list[str] = []
        if msg.text:
            parts.append(msg.text)
        if staged_lines:
            parts.append("\n".join(staged_lines))
        return "\n\n".join(parts)

    async def _stage_file_to_workspace(
        self, agent_id: str, file_name: str, file_bytes: bytes
    ) -> Optional[str]:
        """Write a file into the agent's workspace ``channel_files/`` directory.

        Returns the absolute file path on success, or None on failure.
        """
        try:
            base_dir = workspace_manager.agents_workspace / agent_id / "channel_files"
            base_dir.mkdir(parents=True, exist_ok=True)

            safe_name = _sanitize_filename(file_name)
            target = base_dir / safe_name

            # Handle filename collisions with a counter suffix
            if target.exists():
                stem = target.stem
                suffix = target.suffix
                counter = 1
                while target.exists():
                    target = base_dir / f"{stem}_{counter}{suffix}"
                    counter += 1

            target.write_bytes(file_bytes)
            logger.info("Staged file '%s' to %s", file_name, target)
            return str(target)
        except Exception:
            logger.exception("Failed to stage file '%s' for agent %s", file_name, agent_id)
            return None

    # ------------------------------------------------------------------
    # Session resolution
    # ------------------------------------------------------------------

    async def _resolve_session(
        self,
        channel_id: str,
        agent_id: str,
        external_chat_id: str,
        external_sender_id: str,
        external_thread_id: Optional[str],
        sender_display_name: Optional[str],
    ) -> tuple[str, str, bool]:
        """Resolve an external conversation to an internal session.

        Returns:
            (session_id, channel_session_id, is_new)
        """
        # Try to find an existing channel_session mapping
        existing = await db.channel_sessions.find_by_external(
            channel_id=channel_id,
            external_chat_id=external_chat_id,
            external_thread_id=external_thread_id,
        )

        if existing:
            # Only treat as resumable if at least one conversation completed
            # successfully.  message_count == 0 means the prior attempt failed
            # before the SDK assigned a real session ID, so we must start fresh.
            is_new = (existing.get("message_count", 0) or 0) == 0
            logger.debug(
                f"Resolved existing session {existing['session_id']} "
                f"for external chat {external_chat_id} (is_new={is_new})"
            )
            return existing["session_id"], existing["id"], is_new

        # Create a new internal session
        session_id = str(uuid4())
        title = f"Channel: {sender_display_name or external_sender_id}"
        await session_manager.store_session(
            session_id=session_id,
            agent_id=agent_id,
            title=title,
        )

        # Create the channel_session mapping
        channel_session_id = str(uuid4())
        await db.channel_sessions.put({
            "id": channel_session_id,
            "channel_id": channel_id,
            "external_chat_id": external_chat_id,
            "external_sender_id": external_sender_id,
            "external_thread_id": external_thread_id,
            "session_id": session_id,
            "agent_id": agent_id,
            "sender_display_name": sender_display_name,
            "last_message_at": datetime.now().isoformat(),
            "message_count": 0,
        })

        logger.info(
            f"Created new session {session_id} (channel_session {channel_session_id}) "
            f"for external chat {external_chat_id} on channel {channel_id}"
        )
        return session_id, channel_session_id, True

    # ------------------------------------------------------------------
    # Access control
    # ------------------------------------------------------------------

    @staticmethod
    def _check_access(channel_config: dict, sender_id: str) -> bool:
        """Check whether *sender_id* is permitted to use this channel.

        Access control modes (stored in ``channel_config["access_mode"]``):

        * ``"open"``      -- everyone is allowed.
        * ``"allowlist"``  -- only senders in ``allowed_senders`` are allowed.
        * ``"blocklist"``  -- everyone *except* senders in ``blocked_senders``.

        If the mode is missing or unrecognised the default is to **deny**.
        """
        access_mode = channel_config.get("access_mode", "allowlist")

        if access_mode == "open":
            return True

        if access_mode == "allowlist":
            allowed = channel_config.get("allowed_senders", [])
            if isinstance(allowed, str):
                import json
                try:
                    allowed = json.loads(allowed)
                except json.JSONDecodeError:
                    allowed = []
            # Empty allowlist => no one is allowed (secure default)
            return sender_id in allowed

        if access_mode == "blocklist":
            blocked = channel_config.get("blocked_senders", [])
            if isinstance(blocked, str):
                import json
                try:
                    blocked = json.loads(blocked)
                except json.JSONDecodeError:
                    blocked = []
            return sender_id not in blocked

        # Unknown mode -- deny by default
        logger.warning(f"Unknown access_mode '{access_mode}'; denying access")
        return False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Characters not allowed in staged filenames (path separators + shell-dangerous)
_UNSAFE_FILENAME_RE = _re.compile(r'[/\\:*?"<>|;\x00-\x1f]')


def _sanitize_filename(name: str) -> str:
    """Sanitize a filename for safe use in the workspace.

    Strips path separators and dangerous characters, collapses runs of
    underscores, and ensures a non-empty result.
    """
    # Take only the basename in case the name contains path components
    name = name.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    name = _UNSAFE_FILENAME_RE.sub("_", name)
    # Collapse consecutive underscores
    name = _re.sub(r"_+", "_", name).strip("_")
    return name or "attachment"


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

channel_gateway = ChannelGateway()
