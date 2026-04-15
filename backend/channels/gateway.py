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
    # Auth failure circuit breaker: stop retrying after N consecutive auth
    # failures — these won't self-heal, require human re-auth.
    _AUTH_FAILURE_CIRCUIT_BREAK = 3

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
        # Per-channel consecutive auth failure counter (circuit breaker)
        self._auth_failure_counts: dict[str, int] = {}
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
        """Check if another channel session is actively STREAMING.

        Used to send a "busy" notice to new users before they enter the
        conversation queue.  Best-effort — races are acceptable since
        this is a UX hint, not a correctness guarantee.

        IMPORTANT: Only STREAMING counts as busy.  IDLE sessions are
        just prior conversations sitting in memory — they are NOT
        occupying the slot.  Checking ``is_alive`` (which includes IDLE)
        would cause false "busy" notices every time a prior session
        exists, even when no one is actively being helped.
        """
        try:
            from core.session_unit import SessionState

            router = session_registry.session_router
            if router is None:
                return False
            # Only STREAMING means another user is actively being helped
            return any(
                u.state == SessionState.STREAMING and u.is_channel_session
                for u in router._units.values()
            )
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
            # background thread and returns immediately (e.g. Slack),
            # runtime failures are reported via the on_error callback
            # instead, which invokes _handle_adapter_error.  The two
            # paths do not overlap for the same failure.
            try:
                await adp.start()
            except asyncio.CancelledError:
                logger.info(f"Adapter task for channel {cid} cancelled")
            except Exception as exc:
                # If the adapter was already removed by stop_channel() or
                # shutdown, this crash is a side-effect of cancellation —
                # do not update DB or schedule retry.
                if cid not in self._adapters or self._shutting_down:
                    return
                error_msg = str(exc)
                # Route through _handle_adapter_error for unified auth
                # detection and circuit-breaker logic
                if "AUTH_ERROR" in error_msg:
                    await self._handle_adapter_error(cid, error_msg)
                else:
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
        """Stop and re-start a channel.

        Also resets the auth circuit breaker — an explicit restart means
        the user has likely re-authenticated.
        """
        self._auth_failure_counts.pop(channel_id, None)
        await self.stop_channel(channel_id)
        await self.start_channel(channel_id)

    # ------------------------------------------------------------------
    # Adapter error callback
    # ------------------------------------------------------------------

    async def _handle_adapter_error(self, channel_id: str, error_message: str) -> None:
        """Handle a runtime error reported by an adapter (e.g. WS crash).

        Called from the adapter's error callback.  Cleans up references,
        updates DB status to ``'failed'``, and schedules an automatic retry.

        Auth errors (prefixed with ``AUTH_ERROR:``) are tracked separately
        and circuit-break after ``_AUTH_FAILURE_CIRCUIT_BREAK`` consecutive
        failures — these require human re-authentication, retrying is futile.
        """
        if self._shutting_down or channel_id not in self._adapters:
            return

        is_auth = error_message.startswith("AUTH_ERROR:")

        if is_auth:
            count = self._auth_failure_counts.get(channel_id, 0) + 1
            self._auth_failure_counts[channel_id] = count
            logger.error(
                f"AUTH failure #{count} for channel {channel_id}: {error_message}"
            )
        else:
            # Non-auth error resets the auth failure counter
            self._auth_failure_counts.pop(channel_id, None)
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

        # Circuit breaker: stop retrying after N consecutive auth failures
        if is_auth and self._auth_failure_counts.get(channel_id, 0) >= self._AUTH_FAILURE_CIRCUIT_BREAK:
            logger.error(
                f"Channel {channel_id}: AUTH circuit breaker tripped after "
                f"{self._AUTH_FAILURE_CIRCUIT_BREAK} consecutive auth failures — "
                f"stopping retries. Re-authenticate tokens to resume."
            )
            await db.channels.update(channel_id, {
                "status": "auth_error",
                "error_message": (
                    f"Authentication failed {self._AUTH_FAILURE_CIRCUIT_BREAK} times. "
                    f"Re-authenticate Slack tokens to resume. Last error: {error_message}"
                ),
            })
            return  # Do NOT schedule retry

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
        auth error (``validate_config`` returns AUTH_ERROR),
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

                # Check auth failure circuit breaker before attempting
                auth_count = self._auth_failure_counts.get(channel_id, 0)
                if auth_count >= self._AUTH_FAILURE_CIRCUIT_BREAK:
                    logger.error(
                        f"Channel {channel_id}: auth circuit breaker active "
                        f"({auth_count} failures) — stopping retries"
                    )
                    await db.channels.update(channel_id, {
                        "status": "auth_error",
                        "error_message": (
                            f"Authentication failed {auth_count} times. "
                            f"Re-authenticate Slack tokens to resume."
                        ),
                    })
                    break

                try:
                    await self.start_channel(channel_id)
                    # Success — reset auth failure counter
                    self._auth_failure_counts.pop(channel_id, None)
                    logger.info(f"Channel {channel_id} reconnected on retry #{attempt}")
                    break  # success
                except ValueError as ve:
                    error_str = str(ve)
                    if "AUTH_ERROR" in error_str:
                        # Auth failure during validate_config — circuit-break
                        count = self._auth_failure_counts.get(channel_id, 0) + 1
                        self._auth_failure_counts[channel_id] = count
                        logger.error(
                            f"Channel {channel_id}: auth error on retry #{attempt} "
                            f"(consecutive={count}): {error_str}"
                        )
                        if count >= self._AUTH_FAILURE_CIRCUIT_BREAK:
                            await db.channels.update(channel_id, {
                                "status": "auth_error",
                                "error_message": error_str,
                            })
                            break
                        # Shorter delay for auth retries (token might refresh)
                        delay = min(60.0, delay * self._RETRY_BACKOFF_FACTOR)
                    else:
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
            # Use sender_identity.display_name (gateway-resolved, with
            # fallback chain) rather than msg.sender_display_name (raw
            # from adapter, which may be the unresolved user ID).
            resolved_name = (
                sender_identity.display_name if sender_identity else None
            ) or msg.sender_display_name
            session_id, channel_session_id, _is_new, prior_session_id = (
                await self._resolve_session(
                    channel_id=channel_id,
                    agent_id=agent_id,
                    external_chat_id=msg.external_chat_id,
                    external_sender_id=msg.external_sender_id,
                    external_thread_id=msg.external_thread_id,
                    sender_display_name=resolved_name,
                )
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
        # ``chat_type`` in msg.metadata (e.g. Slack: "p2p" / "group",
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
            # Prior session for conversation continuity after TTL rotation.
            # The old session's messages are injected into the new session's
            # system prompt so the agent knows what was discussed before.
            **({"prior_session_id": prior_session_id} if prior_session_id else {}),
        }
        # Inject platform-specific credential keys for channel MCP tools
        channel_type = channel.get("channel_type", "")
        if channel_type == "slack":
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
        _sender_user_id = msg.external_sender_id  # for DM native streaming

        # ── Status Reaction Controller ────────────────────────────────
        # Serialized + debounced + stall timers.  Inspired by OpenClaw.
        _EMOJI_ACK = "eyes"                # 👀 received
        _EMOJI_THINKING = "thinking_face"  # 🤔 processing
        _EMOJI_TOOL = "fire"               # 🔥 tool use
        _EMOJI_CODING = "male-technologist"  # 👨‍💻 coding
        _EMOJI_WEB = "zap"                 # ⚡ web
        _EMOJI_DONE = "white_check_mark"   # ✅ done
        _EMOJI_ERROR = "x"                 # ❌ error
        _EMOJI_STALL_SOFT = "hourglass_flowing_sand"  # ⏳ 10s no activity
        _EMOJI_STALL_HARD = "warning"      # ⚠️ 30s no activity

        _DEBOUNCE_S = 0.7     # intermediate state debounce
        _STALL_SOFT_S = 20.0  # soft stall warning (agent tool calls often 10-30s)
        _STALL_HARD_S = 60.0  # hard stall warning

        _CODING_TOKENS = {"bash", "read", "write", "edit", "glob", "grep", "notebookedit"}
        _WEB_TOKENS = {"webfetch", "web_search", "web_fetch", "browser", "tavily"}

        _current_reaction: Optional[str] = None
        _debounce_handle: Optional[asyncio.TimerHandle] = None
        _stall_soft_handle: Optional[asyncio.TimerHandle] = None
        _stall_hard_handle: Optional[asyncio.TimerHandle] = None
        _reaction_finished = False

        def _cancel_debounce() -> None:
            nonlocal _debounce_handle
            if _debounce_handle:
                _debounce_handle.cancel()
                _debounce_handle = None

        def _reset_stall_timers() -> None:
            nonlocal _stall_soft_handle, _stall_hard_handle
            eloop = asyncio.get_event_loop()
            if _stall_soft_handle:
                _stall_soft_handle.cancel()
            if _stall_hard_handle:
                _stall_hard_handle.cancel()
            _stall_soft_handle = eloop.call_later(
                _STALL_SOFT_S,
                lambda: _apply_reaction_now(_EMOJI_STALL_SOFT, skip_stall_reset=True),
            )
            _stall_hard_handle = eloop.call_later(
                _STALL_HARD_S,
                lambda: _apply_reaction_now(_EMOJI_STALL_HARD, skip_stall_reset=True),
            )

        def _clear_all_timers() -> None:
            nonlocal _debounce_handle, _stall_soft_handle, _stall_hard_handle
            for h in (_debounce_handle, _stall_soft_handle, _stall_hard_handle):
                if h:
                    h.cancel()
            _debounce_handle = _stall_soft_handle = _stall_hard_handle = None

        async def _do_set_reaction(emoji: str) -> None:
            nonlocal _current_reaction
            if not adapter or not inbound_ts or _current_reaction == emoji:
                return
            old = _current_reaction
            if old:
                try:
                    await adapter.remove_reaction(msg.external_chat_id, inbound_ts, old)
                except Exception:
                    pass
            try:
                await adapter.add_reaction(msg.external_chat_id, inbound_ts, emoji)
                _current_reaction = emoji
            except Exception:
                pass

        def _apply_reaction_now(emoji: str, *, skip_stall_reset: bool = False) -> None:
            if _reaction_finished:
                return
            _cancel_debounce()
            asyncio.ensure_future(_do_set_reaction(emoji))
            if not skip_stall_reset:
                _reset_stall_timers()

        def _set_reaction(emoji: str, *, immediate: bool = False) -> None:
            nonlocal _debounce_handle
            if _reaction_finished:
                return
            if immediate:
                _apply_reaction_now(emoji)
                return
            _cancel_debounce()
            _debounce_handle = asyncio.get_event_loop().call_later(
                _DEBOUNCE_S, lambda: _apply_reaction_now(emoji),
            )
            _reset_stall_timers()

        async def _set_reaction_final(emoji: str) -> None:
            nonlocal _reaction_finished
            _reaction_finished = True
            _clear_all_timers()
            await _do_set_reaction(emoji)

        def _resolve_tool_emoji(tool_name: str) -> str:
            lower = tool_name.lower()
            if any(t in lower for t in _WEB_TOKENS):
                return _EMOJI_WEB
            if any(t in lower for t in _CODING_TOKENS):
                return _EMOJI_CODING
            return _EMOJI_TOOL

        # Ack immediately — user sees 👀 before any processing
        if streaming:
            _set_reaction(_EMOJI_ACK, immediate=True)

        # ── Start streaming ─────────────────────────────────────────
        # For DMs: use inbound message ts as thread_ts so stream is a reply.
        # For threads: use the existing thread_ts.
        _stream_thread_ts = msg.external_thread_id or msg.external_message_id

        if native_streaming:
            try:
                streaming_msg_id = await adapter.start_stream(
                    external_chat_id=msg.external_chat_id,
                    external_thread_id=_stream_thread_ts,
                    recipient_user_id=_sender_user_id,
                )
                if not streaming_msg_id:
                    native_streaming = False
            except Exception:
                logger.exception("Failed to start native stream; falling back")
                native_streaming = False

        if streaming and not native_streaming:
            try:
                streaming_msg_id = await adapter.send_typing_indicator(
                    external_chat_id=msg.external_chat_id,
                    external_thread_id=msg.external_thread_id,
                )
            except Exception:
                logger.exception("Failed to send typing indicator")
                streaming = False

        # ── Smart Stream Flusher ──────────────────────────────────────
        # Native: appendStream (no rate limit) — demand-driven flush with
        #   throttle window. First token flushes immediately, subsequent
        #   flushes respect min interval and wait for in-flight completion.
        # Legacy: chat.update (~50/min) — periodic 1.2s background flush.
        _LEGACY_FLUSH_S = 1.2
        _NATIVE_THROTTLE_S = 0.15  # min interval between appendStream calls
        _stream_buf: list[str] = []
        _stream_flushed = ""  # legacy accumulated text
        _stream_done = asyncio.Event()

        # ── Native flusher state ──
        _native_in_flight: Optional[asyncio.Task] = None
        _native_last_flush = 0.0
        _native_timer: Optional[asyncio.TimerHandle] = None

        async def _native_do_flush() -> None:
            nonlocal _native_last_flush, _native_in_flight
            if not _stream_buf or not streaming_msg_id:
                _native_in_flight = None
                return
            chunk = "".join(_stream_buf)
            _stream_buf.clear()
            try:
                await adapter.append_stream(msg.external_chat_id, streaming_msg_id, chunk)
            except Exception:
                pass
            _native_last_flush = time.monotonic()
            _native_in_flight = None
            # Tokens may have arrived during API call — schedule another
            if _stream_buf and not _stream_done.is_set():
                _native_schedule()

        def _native_schedule() -> None:
            nonlocal _native_timer, _native_in_flight
            if _native_in_flight or _native_timer:
                return
            elapsed = time.monotonic() - _native_last_flush
            delay = max(0.0, _NATIVE_THROTTLE_S - elapsed)
            if delay <= 0:
                _native_in_flight = asyncio.ensure_future(_native_do_flush())
            else:
                _native_timer = asyncio.get_event_loop().call_later(delay, _native_fire)

        def _native_fire() -> None:
            nonlocal _native_timer, _native_in_flight
            _native_timer = None
            if not _stream_done.is_set() and _stream_buf:
                _native_in_flight = asyncio.ensure_future(_native_do_flush())

        # ── Legacy flusher ──
        _flush_lock = asyncio.Lock()

        async def _legacy_flush() -> None:
            nonlocal _stream_flushed
            if not streaming or not streaming_msg_id or not _stream_buf:
                return
            async with _flush_lock:
                if not _stream_buf:
                    return
                _stream_flushed += "".join(_stream_buf)
                _stream_buf.clear()
                try:
                    # Append ✍️ indicator so user knows more is coming
                    await adapter.update_message(
                        external_chat_id=msg.external_chat_id,
                        message_id=streaming_msg_id,
                        text=_stream_flushed + " ✍️",
                    )
                except Exception:
                    logger.warning("Legacy flush: chat.update failed (rate limit?)")

        async def _legacy_periodic() -> None:
            while not _stream_done.is_set():
                await asyncio.sleep(_LEGACY_FLUSH_S)
                await _legacy_flush()

        _flush_task: Optional[asyncio.Task] = None
        if streaming and streaming_msg_id and not native_streaming:
            _flush_task = asyncio.create_task(_legacy_periodic())

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
                # ALWAYS capture deltas into _stream_buf (authoritative
                # multi-turn text source).  Only gate the Slack display
                # on `streaming` — a failed typing indicator must not
                # cause content loss in the final reply.
                if event_type == "text_delta":
                    delta_text = event.get("text", "")
                    if delta_text:
                        _stream_buf.append(delta_text)
                        if streaming:
                            if not _thinking_set:
                                _thinking_set = True
                                _set_reaction(_EMOJI_THINKING)
                            if native_streaming:
                                _native_schedule()
                    continue

                # ── Tool activity ──────────────────────────────────────
                if event_type == "tool_use" and streaming:
                    tool_name = event.get("name", "")
                    _set_reaction(_resolve_tool_emoji(tool_name))

                    if native_streaming and streaming_msg_id:
                        # Flush pending text, then append tool indicator
                        if _stream_buf:
                            chunk = "".join(_stream_buf)
                            _stream_buf.clear()
                            try:
                                await adapter.append_stream(
                                    msg.external_chat_id, streaming_msg_id, chunk,
                                )
                            except Exception:
                                pass
                        try:
                            await adapter.append_stream(
                                msg.external_chat_id, streaming_msg_id,
                                f"\n\n_Using tool: {tool_name}..._",
                            )
                        except Exception:
                            pass
                    elif streaming_msg_id:
                        await _legacy_flush()
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
                    _set_reaction(_EMOJI_THINKING)
                    continue

                # ── AskUserQuestion: auto-answer for channel sessions ─
                # Channel sessions are headless — no human to answer
                # interactive prompts.  Auto-answer questions so the agent
                # can continue.  The session goes WAITING_INPUT when this
                # event fires; we call continue_with_answer to resume.
                if event_type == "ask_user_question":
                    questions = event.get("questions", [])
                    auto_answer = "; ".join(
                        q.get("question", "yes") if isinstance(q, dict) else str(q)
                        for q in questions
                    ) if questions else "yes"
                    # Build a reasonable auto-response
                    answer_text = (
                        f"[Auto-answered by channel gateway] "
                        f"Proceeding with default: {auto_answer}"
                    )
                    logger.info(
                        "Channel %s: auto-answering AskUserQuestion "
                        "(session=%s, questions=%d)",
                        channel_id, session_id, len(questions),
                    )
                    try:
                        async for follow_event in (
                            session_registry.session_router.continue_with_answer(
                                session_id, answer_text,
                            )
                        ):
                            fe_type = follow_event.get("type", "")
                            if fe_type == "text_delta" and streaming:
                                delta = follow_event.get("text", "")
                                if delta:
                                    _stream_buf.append(delta)
                                    if native_streaming:
                                        _native_schedule()
                            elif fe_type == "assistant":
                                for blk in follow_event.get("content", []):
                                    if isinstance(blk, dict) and blk.get("type") == "text":
                                        t = blk.get("text", "")
                                        if t:
                                            reply_text = t
                            elif fe_type == "tool_use" and streaming:
                                _set_reaction(_resolve_tool_emoji(
                                    follow_event.get("name", ""),
                                ))
                            elif fe_type == "result":
                                sub = follow_event.get("subtype", "")
                                if sub and "error" in sub:
                                    error_occurred = True
                    except Exception:
                        logger.exception(
                            "Failed to auto-answer AskUserQuestion on channel %s",
                            channel_id,
                        )
                    continue

                if event_type == "assistant":
                    # Extract text from this turn's content blocks.
                    # reply_text tracks the LAST turn's text (fallback
                    # when _stream_flushed is empty — single-turn only).
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
            # Cancel native timer
            if _native_timer:
                _native_timer.cancel()
            # Wait for in-flight native flush
            if _native_in_flight and not _native_in_flight.done():
                try:
                    await _native_in_flight
                except Exception:
                    pass
            # Cancel legacy flusher
            if _flush_task is not None:
                _flush_task.cancel()
                try:
                    await _flush_task
                except asyncio.CancelledError:
                    pass

            # Final drain: flush remaining buffered tokens into _stream_flushed
            if _stream_buf:
                _stream_flushed += "".join(_stream_buf)
                _stream_buf.clear()

        # For multi-turn agentic responses, _stream_flushed accumulates ALL
        # text_delta tokens across all turns — the correct source for final msg.
        if _stream_flushed:
            reply_text = _stream_flushed
        if not reply_text:
            reply_text = "(No response generated)"

        # ── Final status reaction ───────────────────────────────────
        if streaming:
            await _set_reaction_final(_EMOJI_DONE if not error_occurred else _EMOJI_ERROR)

        # 6. Send outbound reply --------------------------------------------------
        external_message_id: Optional[str] = None

        if native_streaming and streaming_msg_id:
            # Flush any remaining tokens via appendStream
            if _stream_buf:
                final_chunk = "".join(_stream_buf)
                _stream_buf.clear()
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
                    recipient_user_id=_sender_user_id,
                )
                external_message_id = streaming_msg_id
            except Exception:
                logger.exception("Failed to stop native stream; falling back to update")
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

    # Channel session idle TTL: after this duration of inactivity, the next
    # message starts a fresh session with cold resume context injection
    # instead of resuming the stale CLI session.
    # Aligned with lifecycle_manager.TTL_SECONDS (12h) for consistency.
    # Does NOT affect chat tabs — only channel_sessions resolved here.
    _CHANNEL_SESSION_IDLE_TTL_S = 12 * 60 * 60  # 12 hours (was 2h, aligned 2026-04-02)

    async def _resolve_session(
        self,
        channel_id: str,
        agent_id: str,
        external_chat_id: str,
        external_sender_id: str,
        external_thread_id: Optional[str],
        sender_display_name: Optional[str],
    ) -> tuple[str, str, bool, Optional[str]]:
        """Resolve an external conversation to an internal session.

        Each conversation is scoped to ``(channel_id, external_chat_id,
        thread_id)`` — no cross-channel session sharing.  Swarm Brain
        (unified knowledge across channels) is provided by the shared
        context files (MEMORY.md, KNOWLEDGE.md, etc.) in the system
        prompt, not by sharing raw CLI sessions.

        **Idle TTL**: If the existing channel_session has been idle for
        longer than ``_CHANNEL_SESSION_IDLE_TTL_S``, a new session is
        created.  This prevents multi-hour context accumulation from
        degrading response quality (compaction erasing details).  The
        old session's messages remain in DB for cold resume context
        injection — ``prior_session_id`` carries them forward.

        Returns:
            (session_id, channel_session_id, is_new, prior_session_id)
            ``prior_session_id`` is non-None only on TTL rotation —
            the caller should inject the old session's conversation
            history into the new session's context.
        """
        # 1. Try to find an existing channel_session by exact external IDs
        existing = await db.channel_sessions.find_by_external(
            channel_id=channel_id,
            external_chat_id=external_chat_id,
            external_thread_id=external_thread_id,
        )

        if existing:
            # Check idle TTL — if stale, rotate to a new session
            last_msg = existing.get("last_message_at")
            if last_msg and self._is_session_stale(last_msg):
                logger.info(
                    "Channel session %s idle > %ds — rotating to fresh session "
                    "(old session_id=%s, external_chat=%s)",
                    existing["id"],
                    self._CHANNEL_SESSION_IDLE_TTL_S,
                    existing["session_id"],
                    external_chat_id,
                )
                # Create a new internal session, then UPDATE the existing
                # channel_session row in-place.  This is atomic — no gap
                # between delete and create that could hit UNIQUE constraint
                # violations if the delete fails.
                new_session_id = str(uuid4())
                title = f"Channel: {sender_display_name or external_sender_id}"
                await session_manager.store_session(
                    session_id=new_session_id,
                    agent_id=agent_id,
                    title=title,
                )
                await db.channel_sessions.update(existing["id"], {
                    "session_id": new_session_id,
                    "last_message_at": datetime.now().isoformat(),
                    "message_count": 0,
                })
                old_session_id = existing["session_id"]
                logger.info(
                    "Rotated channel_session %s → new session %s "
                    "(prior=%s) for external chat %s on channel %s",
                    existing["id"],
                    new_session_id,
                    old_session_id,
                    external_chat_id,
                    channel_id,
                )
                return new_session_id, existing["id"], True, old_session_id
            else:
                is_new = (existing.get("message_count", 0) or 0) == 0
                logger.debug(
                    "Resolved existing session %s for external chat %s "
                    "(is_new=%s)",
                    existing["session_id"],
                    external_chat_id,
                    is_new,
                )
                return existing["session_id"], existing["id"], is_new, None

        # 2. Create a new internal session (per-channel, no cross-channel sharing)
        user_key = external_sender_id

        session_id = str(uuid4())
        title = f"Channel: {sender_display_name or external_sender_id}"
        await session_manager.store_session(
            session_id=session_id,
            agent_id=agent_id,
            title=title,
        )

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
            "Created new session %s (channel_session %s) "
            "for external chat %s on channel %s",
            session_id,
            channel_session_id,
            external_chat_id,
            channel_id,
        )
        return session_id, channel_session_id, True, None

    def _is_session_stale(self, last_message_at: str) -> bool:
        """Check if a channel session has been idle beyond the TTL."""
        try:
            last_dt = datetime.fromisoformat(last_message_at)
            idle_seconds = (datetime.now() - last_dt).total_seconds()
            return idle_seconds > self._CHANNEL_SESSION_IDLE_TTL_S
        except (ValueError, TypeError):
            return False

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
