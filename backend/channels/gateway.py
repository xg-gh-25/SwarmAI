"""Channel gateway -- manages adapter lifecycle and routes inbound messages to agents.

This is a singleton that runs within the FastAPI process.  It is responsible for:

* Starting / stopping channel adapters as asyncio tasks.
* Routing every :class:`InboundMessage` from an adapter to the correct agent
  via ``session_registry.session_router.run_conversation``, accumulating the reply, and sending
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

from channels.base import (
    ChannelAdapter,
    InboundMessage,
    OutboundMessage,
    PermissionTier,
    SenderIdentity,
)
from channels.registry import get_adapter_class, load_adapters
from core import session_registry
from core.session_manager import session_manager
from core.initialization_manager import initialization_manager
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

    # Retry configuration
    _RETRY_BASE_DELAY = 5.0       # seconds
    _RETRY_MAX_DELAY = 300.0      # 5 minutes cap
    _RETRY_BACKOFF_FACTOR = 2.0
    _RETRY_MAX_ATTEMPTS = 20      # ~1.5 hours at max backoff

    def __init__(self) -> None:
        # channel_id -> running ChannelAdapter instance
        self._adapters: dict[str, ChannelAdapter] = {}
        # channel_id -> asyncio.Task running the adapter's ``start()``
        self._tasks: dict[str, asyncio.Task] = {}
        # channel_id -> asyncio.Task running the retry loop
        self._retry_tasks: dict[str, asyncio.Task] = {}
        # Per-sender rate limiter (shared across all channels)
        self._rate_limiter = _TokenBucketRateLimiter()
        # In-memory cache of channel configs keyed by channel_id
        self._channel_cache: dict[str, dict] = {}
        # Flag to prevent retries during shutdown
        self._shutting_down = False
        # Startup lifecycle state for the system status endpoint.
        # Valid values: "not_started", "starting", "started", "failed"
        self._startup_state: str = "not_started"
        # Per-conversation lock prevents two rapid messages from the same
        # external conversation from racing through _resolve_session +
        # run_conversation simultaneously.  Key: (channel_id, external_chat_id).
        self._conv_locks: dict[tuple[str, str], asyncio.Lock] = {}

    @property
    def startup_state(self) -> str:
        """Current startup lifecycle state.

        Returns one of ``"not_started"``, ``"starting"``, ``"started"``,
        or ``"failed"``.  Read by the system status endpoint to report
        channel gateway readiness.
        """
        return self._startup_state

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def startup(self) -> None:
        """Called once during FastAPI lifespan startup.

        Loads adapter modules via the registry, then auto-starts every
        channel found in the database (regardless of previous status).
        Channels that fail to start will be retried automatically.
        """
        logger.info("ChannelGateway starting up")
        self._shutting_down = False
        load_adapters()

        channels = await db.channels.list()
        logger.info(f"Found {len(channels)} channel(s), auto-starting all")

        for ch in channels:
            try:
                await self.start_channel(ch["id"])
            except ValueError:
                # Config / adapter errors — permanent, do not retry
                logger.error(
                    f"Channel {ch['id']} ({ch.get('name')}) has a "
                    f"configuration error — will not retry"
                )
            except Exception:
                logger.exception(
                    f"Failed to start channel {ch['id']} ({ch.get('name')}) "
                    f"during startup — will retry automatically"
                )
                self._schedule_retry(ch["id"])

        # Set Slack bot presence to "auto" (online) on startup
        await self._set_all_slack_presence("auto")

    async def shutdown(self) -> None:
        """Gracefully stop every running channel and cancel pending retries."""
        logger.info("ChannelGateway shutting down")
        # Set Slack bot presence to "away" before stopping adapters
        await self._set_all_slack_presence("away")
        self._shutting_down = True

        # Cancel all pending retry tasks first
        for channel_id, task in list(self._retry_tasks.items()):
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        self._retry_tasks.clear()

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
    # Owner detection
    # ------------------------------------------------------------------

    @staticmethod
    def _is_owner(channel_config: dict, sender_id: str) -> bool:
        """Check if sender is the channel owner (first allowed_sender).

        The owner gets priority: no rate limit, no queue wait, bypasses
        the channel slot (uses chat pool instead).
        """
        allowed = channel_config.get("allowed_senders", "[]")
        if isinstance(allowed, str):
            import json
            try:
                allowed = json.loads(allowed)
            except (json.JSONDecodeError, TypeError):
                return False
        return bool(allowed) and sender_id == allowed[0]

    # ------------------------------------------------------------------
    # Sender identity resolution
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_sender_identity(
        channel_config: dict,
        sender_id: str,
        sender_display_name: Optional[str],
    ) -> SenderIdentity:
        """Resolve a sender's identity and permission tier.

        The permission model has three tiers:

        * **OWNER** — The first entry in ``allowed_senders``.  Full access
          to everything: files, system commands, external actions, private data.
        * **TRUSTED** — Other entries in ``allowed_senders``.  Can ask
          questions and get knowledge-based help.  Cannot access files,
          system commands, or trigger external actions.
        * **PUBLIC** — Anyone not in ``allowed_senders`` (only reachable in
          group channels, since DMs from non-allowed senders are rejected
          earlier).  Public knowledge only.

        This is the **single enforcement point** for sender authorization.
        The agent receives the tier in ``channel_context`` and must respect it.
        """
        allowed = channel_config.get("allowed_senders", "[]")
        if isinstance(allowed, str):
            import json
            try:
                allowed = json.loads(allowed)
            except (json.JSONDecodeError, TypeError):
                allowed = []

        is_owner = bool(allowed) and sender_id == allowed[0]

        if is_owner:
            tier = PermissionTier.OWNER
        elif sender_id in allowed:
            tier = PermissionTier.TRUSTED
        else:
            tier = PermissionTier.PUBLIC

        return SenderIdentity(
            external_id=sender_id,
            display_name=sender_display_name or sender_id,
            permission_tier=tier,
            is_owner=is_owner,
        )

    # ------------------------------------------------------------------
    # Channel slot awareness (queue notifications)
    # ------------------------------------------------------------------

    def _is_channel_slot_busy(self) -> bool:
        """Check if the channel slot is currently occupied (STREAMING).

        Used to send a "busy" notice to new users before they enter the
        conversation queue.  Best-effort — races are acceptable since
        this is a UX hint, not a correctness guarantee.
        """
        try:
            router = session_registry.session_router
            if router is None:
                return False
            # Count alive channel sessions — if >= 1, slot is busy
            count = sum(
                1 for u in router._units.values()
                if u.is_alive and u.is_channel_session
            )
            return count >= 1
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Slack presence (daemon lifecycle)
    # ------------------------------------------------------------------

    async def _set_all_slack_presence(self, presence: str) -> None:
        """Set presence on all running Slack adapters.

        Best-effort — failures are logged but don't block startup/shutdown.
        """
        for adapter in self._adapters.values():
            if hasattr(adapter, "set_presence") and adapter.channel_type == "slack":
                try:
                    await adapter.set_presence(presence)
                    logger.info("Slack presence set to '%s' for channel %s", presence, adapter.channel_id)
                except Exception:
                    logger.debug("Failed to set Slack presence for channel %s", adapter.channel_id)

    # ------------------------------------------------------------------
    # Channel start / stop / restart
    # ------------------------------------------------------------------

    async def start_channel(self, channel_id: str) -> None:
        """Load a channel from DB, instantiate its adapter, and start it.

        Updates the channel status to ``'active'`` on success, ``'error'``
        for permanent configuration problems (bad credentials, missing
        adapter), or ``'failed'`` for runtime crashes (retriable).
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
        adapter.set_on_error(self._handle_adapter_error)

        # Validate config before attempting to start
        is_valid, validation_error = await adapter.validate_config()
        if not is_valid:
            error_msg = f"Invalid config for channel {channel_id}: {validation_error}"
            logger.error(error_msg)
            await db.channels.update(channel_id, {"status": "error", "error_message": error_msg})
            raise ValueError(error_msg)

        # Cache the channel record
        self._channel_cache[channel_id] = channel

        # Cancel any pending retry for this channel since we're starting fresh
        retry_task = self._retry_tasks.pop(channel_id, None)
        if retry_task and not retry_task.done():
            retry_task.cancel()
            try:
                await retry_task
            except (asyncio.CancelledError, Exception):
                pass

        # Wrap adapter.start() in an asyncio task so it can run concurrently
        async def _run_adapter(cid: str, adp: ChannelAdapter) -> None:
            # NOTE: This handler catches exceptions from blocking start()
            # implementations.  For adapters whose start() spawns a
            # background thread and returns immediately (e.g. Feishu),
            # runtime failures are reported via the on_error callback
            # instead, which invokes _handle_adapter_error.  The two
            # paths do not overlap for the same failure.
            try:
                await adp.start()
            except asyncio.CancelledError:
                logger.info(f"Adapter task for channel {cid} cancelled")
            except Exception:
                # If the adapter was already removed by stop_channel() or
                # shutdown, this crash is a side-effect of cancellation —
                # do not update DB or schedule retry.
                if cid not in self._adapters or self._shutting_down:
                    return
                logger.exception(f"Adapter for channel {cid} crashed")
                await db.channels.update(cid, {
                    "status": "failed",
                    "error_message": "Adapter crashed unexpectedly",
                })
                # Clean up references
                self._adapters.pop(cid, None)
                self._tasks.pop(cid, None)
                self._channel_cache.pop(cid, None)
                # Schedule automatic retry
                self._schedule_retry(cid)

        task = asyncio.create_task(_run_adapter(channel_id, adapter))
        self._adapters[channel_id] = adapter
        self._tasks[channel_id] = task

        await db.channels.update(channel_id, {"status": "active", "error_message": None})
        logger.info(f"Channel {channel_id} ({channel.get('name')}) started successfully")

    async def stop_channel(self, channel_id: str) -> None:
        """Stop a running channel adapter and update DB status to ``'inactive'``.

        Also cancels any pending retry — an explicit stop means the user
        does not want the channel running.
        """
        # Cancel pending retry
        retry_task = self._retry_tasks.pop(channel_id, None)
        if retry_task and not retry_task.done():
            retry_task.cancel()

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
    # Adapter error callback
    # ------------------------------------------------------------------

    async def _handle_adapter_error(self, channel_id: str, error_message: str) -> None:
        """Handle a runtime error reported by an adapter (e.g. WS crash).

        Called from the adapter's error callback.  Cleans up references,
        updates DB status to ``'failed'``, and schedules an automatic retry.
        """
        if self._shutting_down or channel_id not in self._adapters:
            return

        logger.error(f"Adapter error callback for channel {channel_id}: {error_message}")

        adapter = self._adapters.pop(channel_id, None)
        self._tasks.pop(channel_id, None)
        self._channel_cache.pop(channel_id, None)

        # Best-effort cleanup: the adapter likely already crashed, so
        # stop() may be a partial no-op.  The try/except ensures any
        # secondary errors during teardown don't prevent DB update.
        if adapter is not None:
            try:
                await adapter.stop()
            except Exception:
                logger.exception(f"Error stopping adapter during error handling for channel {channel_id}")

        await db.channels.update(channel_id, {
            "status": "failed",
            "error_message": error_message,
        })

        self._schedule_retry(channel_id)

    # ------------------------------------------------------------------
    # Auto-retry
    # ------------------------------------------------------------------

    def _schedule_retry(self, channel_id: str) -> None:
        """Schedule an automatic reconnection attempt for a failed channel."""
        if self._shutting_down:
            return
        if channel_id in self._retry_tasks and not self._retry_tasks[channel_id].done():
            return  # retry already scheduled
        task = asyncio.create_task(self._retry_loop(channel_id))
        self._retry_tasks[channel_id] = task

    async def _retry_loop(self, channel_id: str) -> None:
        """Retry starting a channel with exponential backoff.

        Stops on: success, permanent config error (``ValueError``),
        max attempts reached, shutdown, or explicit stop/start by user.
        """
        delay = self._RETRY_BASE_DELAY
        attempt = 0
        try:
            while not self._shutting_down:
                attempt += 1
                if attempt > self._RETRY_MAX_ATTEMPTS:
                    logger.error(
                        f"Channel {channel_id}: max retries ({self._RETRY_MAX_ATTEMPTS}) "
                        f"exhausted — giving up"
                    )
                    await db.channels.update(channel_id, {
                        "status": "error",
                        "error_message": f"Failed to connect after {self._RETRY_MAX_ATTEMPTS} retries",
                    })
                    break

                logger.info(
                    f"Retry #{attempt} for channel {channel_id} in {delay:.0f}s"
                )
                await asyncio.sleep(delay)

                if self._shutting_down:
                    break
                # If channel was started successfully by another path, stop retrying
                if channel_id in self._adapters:
                    logger.info(f"Channel {channel_id} is already running, stopping retry")
                    break

                try:
                    await self.start_channel(channel_id)
                    logger.info(f"Channel {channel_id} reconnected on retry #{attempt}")
                    break  # success
                except ValueError:
                    # Permanent config / adapter error — no point retrying
                    logger.error(
                        f"Channel {channel_id}: permanent error on retry "
                        f"#{attempt} — stopping retries"
                    )
                    break
                except Exception:
                    logger.warning(
                        f"Retry #{attempt} failed for channel {channel_id}, "
                        f"next attempt in {min(delay * self._RETRY_BACKOFF_FACTOR, self._RETRY_MAX_DELAY):.0f}s"
                    )
                    delay = min(delay * self._RETRY_BACKOFF_FACTOR, self._RETRY_MAX_DELAY)
        finally:
            self._retry_tasks.pop(channel_id, None)

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
        # Channels/groups: open to everyone (Swarm is a team participant).
        # DMs: allowlist only. Non-allowlisted DMs get a polite decline.
        chat_type = msg.metadata.get("chat_type", "im")
        is_dm = chat_type == "im"

        if is_dm and not self._check_access(channel, msg.external_sender_id):
            logger.info(
                f"DM access denied for {msg.external_sender_id} "
                f"on channel {channel_id} — sending polite decline"
            )
            adapter = self._adapters.get(channel_id)
            if adapter:
                try:
                    await adapter.send_message(OutboundMessage(
                        channel_id=channel_id,
                        external_chat_id=msg.external_chat_id,
                        text="Hi! I'm XG's AI assistant. "
                             "DM access is limited to approved contacts. "
                             "Please reach out to XG if you'd like access, "
                             "or @mention me in a channel — I'm happy to help there!",
                    ))
                except Exception:
                    pass
            return

        # 3. Sender identity + permission tier -----------------------------------
        # Resolves WHO is talking and WHAT they can do.  This is the single
        # source of truth — the agent receives this in channel_context.
        sender_identity = self._resolve_sender_identity(
            channel, msg.external_sender_id, msg.sender_display_name,
        )
        is_owner = sender_identity.is_owner

        # 4. Rate limiting (owner exempt) -----------------------------------------
        rate_limit = channel.get("rate_limit_per_minute", 10)
        if not is_owner and not self._rate_limiter.is_allowed(msg.external_sender_id, rate_limit):
            logger.warning(
                f"Rate limit exceeded for sender {msg.external_sender_id} "
                f"on channel {channel_id}"
            )
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

        # 5. Queue awareness: if another channel conversation is actively
        # streaming, send an immediate "busy" notice so the user isn't
        # left staring at silence.  Owner skips this — always prioritized.
        adapter = self._adapters.get(channel_id)
        if adapter and not is_owner and self._is_channel_slot_busy():
            try:
                await adapter.send_message(OutboundMessage(
                    channel_id=channel_id,
                    external_chat_id=msg.external_chat_id,
                    external_thread_id=msg.external_thread_id,
                    reply_to_message_id=msg.external_message_id,
                    text="Hi! I'm currently helping someone else. "
                         "I'll get to your question as soon as I'm done "
                         "— usually within a minute or two. :hourglass_flowing_sand:",
                ))
            except Exception:
                logger.debug("Failed to send busy notice")

        # 5. Resolve / create internal session ------------------------------------
        # Per-conversation lock: prevents two rapid messages from the same
        # external chat from racing into _resolve_session + run_conversation.
        conv_key = (channel_id, msg.external_chat_id)
        if conv_key not in self._conv_locks:
            self._conv_locks[conv_key] = asyncio.Lock()

        async with self._conv_locks[conv_key]:
            return await self._handle_conversation(
                msg=msg,
                channel=channel,
                channel_id=channel_id,
                agent_id=agent_id,
                is_owner=is_owner,
                sender_identity=sender_identity,
            )

    async def _handle_conversation(
        self,
        msg: InboundMessage,
        channel: dict,
        channel_id: str,
        agent_id: str,
        is_owner: bool = False,
        sender_identity: Optional[SenderIdentity] = None,
    ) -> None:
        """Inner handler — runs under per-conversation lock."""
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

        # Determine if this is a group conversation.  Adapters set
        # ``chat_type`` in msg.metadata (e.g. Feishu: "p2p" / "group",
        # Slack: "channel" / "im").  We normalize to a boolean here so
        # downstream code (context loader) can exclude personal files
        # like MEMORY.md and USER.md from group prompts.
        chat_type = msg.metadata.get("chat_type", "")
        is_group = chat_type in ("group", "channel", "mpim")

        channel_context = {
            "channel_type": channel.get("channel_type", ""),
            "channel_id": channel_id,
            "chat_id": msg.external_chat_id,
            "reply_to_message_id": msg.external_message_id,
            "is_group": is_group,
            "is_owner": is_owner,
            # Sender identity — the agent MUST use this to determine who
            # is talking and what they're allowed to do.
            **({"sender_identity": sender_identity.to_dict()} if sender_identity else {}),
        }
        # Inject platform-specific credential keys for channel MCP tools
        channel_type = channel.get("channel_type", "")
        if channel_type == "feishu":
            channel_context["app_id"] = channel_config.get("app_id", "")
            channel_context["app_secret"] = channel_config.get("app_secret", "")
        elif channel_type == "slack":
            channel_context["bot_token"] = channel_config.get("bot_token", "")
            channel_context["app_token"] = channel_config.get("app_token", "")

        # Prepare message text (stages attachments to workspace if present).
        # Non-owner attachments go to sender-scoped directory.
        final_text = await self._prepare_message_text(
            msg, agent_id, sender_identity,
        )

        # 5a. Streaming setup ------------------------------------------------
        adapter = self._adapters.get(channel_id)
        streaming = adapter is not None and adapter.supports_streaming
        native_streaming = streaming and adapter.supports_native_streaming
        streaming_msg_id: Optional[str] = None
        inbound_ts = msg.external_message_id  # for reactions

        # ── Status reactions: immediate emoji feedback ──────────────
        # Inspired by OpenClaw's status-reactions system.
        # React to the USER's message (not our reply) so they see
        # instant acknowledgment.
        _EMOJI_ACK = "eyes"           # 👀 received
        _EMOJI_THINKING = "thinking_face"  # 🤔 processing
        _EMOJI_TOOL = "fire"          # 🔥 tool use
        _EMOJI_DONE = "white_check_mark"  # ✅ done
        _EMOJI_ERROR = "x"            # ❌ error
        _current_reaction: Optional[str] = None

        async def _set_reaction(emoji: str) -> None:
            """Set status reaction on the inbound message (swap previous)."""
            nonlocal _current_reaction
            if not adapter or not inbound_ts:
                return
            # Remove previous reaction
            if _current_reaction and _current_reaction != emoji:
                try:
                    await adapter.remove_reaction(
                        msg.external_chat_id, inbound_ts, _current_reaction,
                    )
                except Exception:
                    pass
            # Add new reaction
            try:
                await adapter.add_reaction(
                    msg.external_chat_id, inbound_ts, emoji,
                )
                _current_reaction = emoji
            except Exception:
                pass

        # Ack immediately — user sees 👀 before any processing
        if streaming:
            await _set_reaction(_EMOJI_ACK)

        # ── Start streaming ─────────────────────────────────────────
        if native_streaming:
            # Native Slack streaming: chat.startStream (no rate limit)
            try:
                streaming_msg_id = await adapter.start_stream(
                    external_chat_id=msg.external_chat_id,
                    external_thread_id=msg.external_thread_id,
                )
                if not streaming_msg_id:
                    native_streaming = False
            except Exception:
                logger.exception("Failed to start native stream; falling back")
                native_streaming = False

        if streaming and not native_streaming:
            # Fallback: legacy chat.update streaming
            try:
                streaming_msg_id = await adapter.send_typing_indicator(
                    external_chat_id=msg.external_chat_id,
                    external_thread_id=msg.external_thread_id,
                )
            except Exception:
                logger.exception("Failed to send typing indicator")
                streaming = False

        # ── Streaming state ─────────────────────────────────────────
        # For native streaming: tokens go directly via appendStream (no buffer).
        # For legacy streaming: buffer + periodic flush (chat.update rate limited).
        _STREAM_FLUSH_INTERVAL = 1.2  # legacy only
        _stream_buf: list[str] = []
        _stream_flushed = ""
        _flush_lock = asyncio.Lock()
        _stream_done = asyncio.Event()

        async def _do_flush() -> None:
            """Drain buffer and push to adapter (legacy path only)."""
            nonlocal _stream_flushed
            if not streaming or not streaming_msg_id or not _stream_buf:
                return
            async with _flush_lock:
                if not _stream_buf:
                    return
                _stream_flushed = _stream_flushed + "".join(_stream_buf)
                _stream_buf.clear()
                try:
                    await adapter.update_message(
                        external_chat_id=msg.external_chat_id,
                        message_id=streaming_msg_id,
                        text=_stream_flushed,
                    )
                except Exception as exc:
                    logger.warning("Stream update failed: %s", exc)

        async def _periodic_flusher() -> None:
            """Background task: flush buffered tokens (legacy path only)."""
            while not _stream_done.is_set():
                await asyncio.sleep(_STREAM_FLUSH_INTERVAL)
                await _do_flush()

        _flush_task: Optional[asyncio.Task] = None
        if streaming and streaming_msg_id and not native_streaming:
            _flush_task = asyncio.create_task(_periodic_flusher())

        # ── Batch buffer for native streaming (reduces API calls) ───
        # Accumulate tokens and flush every ~200ms (no rate limit, but
        # batching avoids per-token API overhead).
        _NATIVE_BATCH_INTERVAL = 0.2
        _native_buf: list[str] = []

        async def _native_batch_flusher() -> None:
            """Background task: batch-flush tokens via appendStream."""
            while not _stream_done.is_set():
                await asyncio.sleep(_NATIVE_BATCH_INTERVAL)
                if _native_buf and streaming_msg_id:
                    chunk = "".join(_native_buf)
                    _native_buf.clear()
                    try:
                        await adapter.append_stream(
                            msg.external_chat_id, streaming_msg_id, chunk,
                        )
                    except Exception:
                        pass

        _native_flush_task: Optional[asyncio.Task] = None
        if native_streaming and streaming_msg_id:
            _native_flush_task = asyncio.create_task(_native_batch_flusher())

        reply_text = ""
        error_occurred = False
        _thinking_set = False
        try:
            resume_sid = None if _is_new else session_id
            async for event in session_registry.session_router.run_conversation(
                agent_id=agent_id,
                user_message=final_text,
                session_id=resume_sid,
                enable_skills=enable_skills,
                enable_mcp=enable_mcp,
                channel_context=channel_context,
            ):
                event_type = event.get("type", "")

                # ── Streaming: token-by-token text deltas ──────────────
                if event_type == "text_delta" and streaming:
                    delta_text = event.get("text", "")
                    if delta_text:
                        # Switch reaction from 👀 to 🤔 on first text
                        if not _thinking_set:
                            _thinking_set = True
                            await _set_reaction(_EMOJI_THINKING)

                        if native_streaming:
                            _native_buf.append(delta_text)
                        else:
                            _stream_buf.append(delta_text)
                    continue

                # ── Tool activity ──────────────────────────────────────
                if event_type == "tool_use" and streaming:
                    tool_name = event.get("name", "")
                    # Status reaction: 🔥
                    await _set_reaction(_EMOJI_TOOL)

                    if native_streaming and streaming_msg_id:
                        # Flush pending text, then append tool indicator
                        if _native_buf:
                            chunk = "".join(_native_buf)
                            _native_buf.clear()
                            await adapter.append_stream(
                                msg.external_chat_id, streaming_msg_id, chunk,
                            )
                        await adapter.append_stream(
                            msg.external_chat_id, streaming_msg_id,
                            f"\n\n_Using tool: {tool_name}..._",
                        )
                    elif streaming_msg_id:
                        await _do_flush()
                        status = _stream_flushed + f"\n\n_Using tool: {tool_name}..._" if _stream_flushed else f"_Using tool: {tool_name}..._"
                        try:
                            await adapter.update_message(
                                external_chat_id=msg.external_chat_id,
                                message_id=streaming_msg_id,
                                text=status,
                            )
                        except Exception:
                            pass
                    continue

                if event_type == "tool_result" and streaming:
                    # Back to thinking
                    await _set_reaction(_EMOJI_THINKING)
                    continue

                if event_type == "assistant":
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
                    new_sid = event.get("sessionId")
                    if new_sid and new_sid != session_id:
                        session_id = new_sid
                        try:
                            await db.channel_sessions.update(
                                channel_session_id,
                                {"session_id": session_id},
                            )
                        except Exception:
                            logger.exception("Failed to update channel_session with new session_id")

                elif event_type == "result":
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
                            reply_text = f"Sorry, something went wrong: {error_detail}"
                        error_occurred = True

                elif event_type == "error":
                    error_msg = event.get("error") or event.get("message") or "Unknown error"
                    logger.error(
                        f"Agent error on channel {channel_id}: {error_msg}"
                    )
                    if not reply_text:
                        reply_text = "Sorry, I hit an error processing that. Please try again."
                    error_occurred = True

        except Exception:
            logger.exception(f"Exception running agent conversation on channel {channel_id}")
            reply_text = "Sorry, something unexpected happened. Please try again."
            error_occurred = True
        finally:
            # Stop flusher tasks
            _stream_done.set()
            for task in (_flush_task, _native_flush_task):
                if task is not None:
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass

        if not reply_text:
            reply_text = "(No response generated)"

        # ── Final status reaction ───────────────────────────────────
        if streaming:
            await _set_reaction(_EMOJI_DONE if not error_occurred else _EMOJI_ERROR)

        # 6. Send outbound reply --------------------------------------------------
        external_message_id: Optional[str] = None

        if native_streaming and streaming_msg_id:
            # Flush any remaining tokens in native buffer
            if _native_buf:
                final_chunk = "".join(_native_buf)
                _native_buf.clear()
                try:
                    await adapter.append_stream(
                        msg.external_chat_id, streaming_msg_id, final_chunk,
                    )
                except Exception:
                    pass
            # Stop the stream — message becomes a normal Slack message
            try:
                await adapter.stop_stream(
                    external_chat_id=msg.external_chat_id,
                    stream_ts=streaming_msg_id,
                )
                external_message_id = streaming_msg_id
            except Exception:
                logger.exception("Failed to stop native stream; falling back to update")
                # Fall through to legacy final update
                native_streaming = False

        if not native_streaming and streaming and streaming_msg_id:
            try:
                await adapter.update_message(
                    external_chat_id=msg.external_chat_id,
                    message_id=streaming_msg_id,
                    text=reply_text,
                    is_final=True,
                )
                external_message_id = streaming_msg_id
            except Exception:
                logger.exception("Failed to send final streaming update; falling back")
                streaming = False

        if not streaming or not streaming_msg_id:
            outbound = OutboundMessage(
                channel_id=channel_id,
                external_chat_id=msg.external_chat_id,
                external_thread_id=msg.external_thread_id,
                reply_to_message_id=msg.external_message_id,
                text=reply_text,
            )
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

    async def _prepare_message_text(
        self,
        msg: InboundMessage,
        agent_id: str,
        sender_identity: Optional[SenderIdentity] = None,
    ) -> str:
        """Build the final message text, staging any attachments to the agent workspace.

        If no attachments are present, returns ``msg.text`` unchanged.
        Otherwise stages each file and appends path info to the text.

        Non-owner attachments are staged to a sender-scoped directory
        (``channel_files/<sender_id>/``) that matches the file access
        sandbox enforced by ``prompt_builder.py``.
        """
        if not msg.attachments:
            return msg.text

        staged_lines: list[str] = []
        for attachment in msg.attachments:
            file_name = attachment.get("file_name", "attachment")
            file_bytes = attachment.get("file_bytes", b"")
            if not file_bytes:
                continue
            path = await self._stage_file_to_workspace(
                agent_id, file_name, file_bytes, sender_identity,
            )
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
        self,
        agent_id: str,
        file_name: str,
        file_bytes: bytes,
        sender_identity: Optional[SenderIdentity] = None,
    ) -> Optional[str]:
        """Write a file into the agent's workspace ``channel_files/`` directory.

        For non-owner senders, files go to ``channel_files/<sender_id>/``
        which matches the sandboxed file access directory.  Owner files
        go to ``channel_files/<agent_id>/`` (legacy behavior).

        Returns the absolute file path on success, or None on failure.
        """
        try:
            ws_root = Path(initialization_manager.get_cached_workspace_path())
            if sender_identity and not sender_identity.is_owner:
                # Sender-scoped directory — matches file_access_handler sandbox
                base_dir = ws_root / "channel_files" / sender_identity.external_id
            else:
                base_dir = ws_root / "channel_files" / agent_id
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

    async def _resolve_user_key(
        self, platform: str, external_sender_id: str,
    ) -> str:
        """Map a platform-specific sender ID to a unified user_key.

        Looks up ``channel_user_identities`` in DB.  If no mapping exists,
        falls back to using the raw ``external_sender_id`` as the user_key
        (per-channel isolation for unmapped users).
        """
        try:
            user_key = await db.channel_user_identities.resolve_user_key(
                platform=platform, external_sender_id=external_sender_id,
            )
            if user_key:
                return user_key
        except Exception:
            logger.debug(
                "channel_user_identities lookup failed for %s/%s, using fallback",
                platform, external_sender_id,
            )
        return external_sender_id

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

        For threaded messages (external_thread_id is set), sessions are
        scoped per (channel_id, external_chat_id, thread_id) — no
        cross-channel sharing.

        For top-level messages (no thread), sessions are shared across
        channels for the same user_key, enabling cross-channel conversation
        continuity (L2: Swarm's Brain Model).

        Returns:
            (session_id, channel_session_id, is_new)
        """
        # 1. Try to find an existing channel_session by exact external IDs
        existing = await db.channel_sessions.find_by_external(
            channel_id=channel_id,
            external_chat_id=external_chat_id,
            external_thread_id=external_thread_id,
        )

        if existing:
            is_new = (existing.get("message_count", 0) or 0) == 0
            logger.debug(
                f"Resolved existing session {existing['session_id']} "
                f"for external chat {external_chat_id} (is_new={is_new})"
            )
            return existing["session_id"], existing["id"], is_new

        # 2. For top-level (non-threaded) messages, check cross-channel sharing
        if not external_thread_id:
            channel_record = self._channel_cache.get(channel_id)
            if not channel_record:
                channel_record = await db.channels.get(channel_id)
            platform = (channel_record or {}).get("channel_type", "unknown")
            user_key = await self._resolve_user_key(platform, external_sender_id)

            # Look for any existing non-threaded session for this user_key
            try:
                cross = await db.channel_sessions.find_by_user_key(
                    user_key=user_key, exclude_threaded=True,
                )
                if cross:
                    # Reuse the session_id from the other channel
                    session_id = cross["session_id"]
                    channel_session_id = str(uuid4())
                    await db.channel_sessions.put({
                        "id": channel_session_id,
                        "channel_id": channel_id,
                        "external_chat_id": external_chat_id,
                        "external_sender_id": external_sender_id,
                        "external_thread_id": None,
                        "session_id": session_id,
                        "agent_id": agent_id,
                        "sender_display_name": sender_display_name,
                        "user_key": user_key,
                        "last_message_at": datetime.now().isoformat(),
                        "message_count": 0,
                    })
                    is_new = (cross.get("message_count", 0) or 0) == 0
                    logger.info(
                        f"Cross-channel session sharing: reusing session {session_id} "
                        f"for user_key={user_key} from channel {cross.get('channel_id')} "
                        f"→ channel {channel_id}"
                    )
                    return session_id, channel_session_id, is_new
            except Exception:
                logger.debug("find_by_user_key not available, falling back to per-channel")
        else:
            user_key = external_sender_id

        # 3. Create a new internal session
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
            "user_key": user_key,
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
