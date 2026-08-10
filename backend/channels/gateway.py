"""Channel gateway -- manages adapter lifecycle and routes inbound messages to agents.

This is a singleton that runs within the FastAPI process.  It is responsible for:

* Starting / stopping channel adapters as asyncio tasks.
* Routing every :class:`InboundMessage` from an adapter to the correct agent
  via ``session_registry.session_router.run_conversation``, accumulating the reply, and sending
  the :class:`OutboundMessage` back through the adapter.
* Maintaining a mapping between external conversations and internal sessions.
* Simple per-sender rate limiting and access-control checks.

Streaming logic (reaction controller, debounce, stall timers, legacy flusher)
lives in :mod:`channels.streaming` — extracted for independent testability.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re as _re
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Optional
from uuid import uuid4

from channels.base import (
    InboundMessage,
    OutboundMessage,
    PermissionTier,
    SenderIdentity,
)
from channels.registry import get_adapter_class, load_adapters
from channels.streaming import (
    EMOJI_ACK,
    EMOJI_DONE,
    EMOJI_ERROR,
    EMOJI_THINKING,
    StreamContext,
    cleanup_stream,
    end_thinking_phase,
    handle_tool_use,
    legacy_periodic,
    reset_stall_timers,
    schedule_native_flush,
    set_reaction,
    set_reaction_final,
)
from channels.heartbeat import (
    HeartbeatManager,
    estimate_complexity,
    pick_ack,
)
from channels.message_queue import (
    ChannelMessageQueue,
    QueuedMessage,
)
from channels.egress_redactor import redact_text
from channels.response_formatter import HumanResponseFormatter
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

    def evict_stale(self, max_idle_seconds: float = 300.0) -> int:
        """Remove entries for senders idle longer than *max_idle_seconds*.

        Returns the number of senders evicted.  Called periodically to
        prevent unbounded growth of the ``_windows`` dict (G8).
        """
        if not self._windows:
            return 0
        now = time.time()
        cutoff = now - max_idle_seconds
        stale = [sid for sid, ts_list in self._windows.items()
                 if not ts_list or ts_list[-1] < cutoff]
        for sid in stale:
            del self._windows[sid]
        return len(stale)


def _parse_json_list(value) -> list:
    """Parse a JSON-encoded list from a DB field.

    Handles: actual list, JSON string, None, empty string, bad JSON.
    Returns a plain Python list — never raises.  Used everywhere
    ``allowed_senders`` or ``blocked_senders`` are read (G7).
    """
    if isinstance(value, list):
        return value
    if not value:
        return []
    if isinstance(value, str):
        import json
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except (json.JSONDecodeError, TypeError):
            return []
    return []


def _is_authorized_tier(sender_identity: Optional[SenderIdentity]) -> bool:
    """True iff the sender is OWNER or TRUSTED (allowlisted).

    THE single source of the observe-mode authorization gate (run_84cb2ea3),
    used by BOTH the write gate (A: only authorized messages are recorded) and
    the read gate (B: only authorized history is injected).  FAIL-CLOSED: a
    missing identity, or any tier that is not explicitly OWNER/TRUSTED (i.e.
    PUBLIC or an unknown future tier), returns False — an unauthorized message
    is never stored and never injected (anti-poisoning).
    """
    if sender_identity is None:
        return False
    return sender_identity.permission_tier in (
        PermissionTier.OWNER,
        PermissionTier.TRUSTED,
    )


def _resolve_reply_thread_ts(
    external_thread_id: Optional[str],
    external_message_id: Optional[str],
    is_group: bool,
) -> Optional[str]:
    """THE single source of "which thread does the bot reply into" (run_45187d49).

    * An explicit inbound ``external_thread_id`` always wins — reply in that thread.
    * Otherwise, in a GROUP channel (channel/group/mpim), root a new thread under
      the user's own message (``external_message_id``) so the reply lands in a
      thread instead of the channel main stream, and so ``thread_follow`` can
      re-engage the user's next in-thread message (whose thread_ts will equal this
      value). Matches the existing intent of adapters/slack.py start_stream (which
      already does ``external_thread_id or inbound_ts``).
    * In a DM (im, ``is_group`` False) there is no thread to root — return the raw
      value (None → top-level), leaving 1:1 DM behavior unchanged.

    Used for BOTH the outbound reply target AND session identity / thread_follow
    lookup, so the reply target and the session key can never drift (Gate-1).
    """
    if external_thread_id:
        return external_thread_id
    if is_group:
        return external_message_id
    return external_thread_id


# Hard cap on live owner-approval prompts per channel (Gate-2 RANK-3 anti-DoS):
# bounds channel_config['pending_approvals'] so a PUBLIC flood of distinct
# sender_ids can't grow it unboundedly. Dead entries are reaped before the check.
_MAX_PENDING_APPROVALS = 200

_AUTH = {PermissionTier.OWNER.value, PermissionTier.TRUSTED.value}
"""THE single source of the authorized ``sender_tier`` string VALUES (run_84cb2ea3).

B's fail-closed read gate (`_recent_authorized_history`) admits a stored record
only if its ``sender_tier`` is in this set.  Kept module-level so the write
source (`_sender_metadata`) and the read gate cannot drift on what counts as
authorized.  (Mirror of `_is_authorized_tier`, which gates on the enum; this
gates on the persisted string value.)
"""


def _sender_metadata(
    sender_identity: Optional[SenderIdentity], resolved_name: Optional[str]
) -> dict:
    """THE single source for the sender fields stamped on every recorded
    channel message (run_84cb2ea3).

    Used by BOTH write sites — the observe-record write (A) and the reply-path
    inbound write — so the ``sender_tier`` that B's fail-closed read gate keys
    on is produced in exactly ONE place.  If these two writes drift, B silently
    excludes the affected turns → a partial, misleading history; single-source
    prevents that divergence (R25).  FAIL-CLOSED: no identity → ``"unknown"``
    tier, which B's read gate excludes.
    """
    return {
        "sender_tier": (
            sender_identity.permission_tier.value
            if sender_identity else "unknown"
        ),
        "sender_display_name": resolved_name,
    }


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
        # Pre-warmed session ID (MeshClaw pattern): eliminates ~4s cold-start
        # latency on the owner's first message after daemon restart.
        self._prewarmed_session_id: Optional[str] = None
        self._prewarm_task: Optional[asyncio.Task] = None
        # Message counter for lazy rate-limiter eviction (G8/PE6)
        self._msg_counter: int = 0
        # Per-conversation message queues for human mode (Slack channels).
        # Key: (channel_id, external_chat_id) — same as conv_locks.
        self._message_queues: dict[tuple[str, str], ChannelMessageQueue] = {}
        # Per-channel message counter for status reporting
        self._channel_msg_counts: dict[str, int] = {}
        # Per-channel start time (Unix timestamp) for uptime calculation
        self._channel_start_times: dict[str, float] = {}

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

        # Pre-warm one IDLE session for the channel owner's first message.
        # Fire-and-forget — never blocks startup.
        if channels:
            self._prewarm_task = asyncio.create_task(
                self._prewarm_owner_session(channels[0])
            )

    async def shutdown(self) -> None:
        """Gracefully stop every running channel and cancel pending retries."""
        logger.info("ChannelGateway shutting down")
        # Set Slack bot presence to "away" before stopping adapters
        await self._set_all_slack_presence("away")
        self._shutting_down = True

        # Cancel pre-warm task if still running
        if self._prewarm_task and not self._prewarm_task.done():
            self._prewarm_task.cancel()
            try:
                await self._prewarm_task
            except (asyncio.CancelledError, Exception):
                pass
        self._prewarm_task = None
        self._prewarmed_session_id = None

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
        self._conv_locks.clear()
        self._channel_msg_counts.clear()
        self._channel_start_times.clear()
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
        allowed = _parse_json_list(channel_config.get("allowed_senders"))
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
        allowed = _parse_json_list(channel_config.get("allowed_senders"))

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
    # Allowlist mutation — the SINGLE approval-path writer
    # ------------------------------------------------------------------

    async def add_trusted_sender(
        self, channel_id: str, sender_id: str, *, actor: str = "slack_approval"
    ) -> bool:
        """Append *sender_id* to a channel's ``allowed_senders`` as TRUSTED.

        The single writer for the Slack-approval path (Gate-1 SSA). It enforces
        the load-bearing invariants the tier model depends on:

        * **Owner invariant** — ``allowed_senders[0]`` is the owner (see
          ``_resolve_sender_identity``). This method is **append-only** and NEVER
          reorders, so index 0 can never be displaced. It refuses to run on an
          empty allowlist (no owner to preserve → fail closed).
        * **Idempotent** — a sender already present (owner or trusted) is a no-op,
          so a double-click / replay cannot corrupt the list.
        * **Cache coherence** — after the DB write it re-gets the row and refreshes
          ``_channel_cache`` (mirrors the P0-bootstrap write at ~1055), so the new
          TRUSTED tier takes effect on the sender's NEXT message rather than
          staying latent behind a stale cache.

        Returns True if the sender was newly added, False on no-op (already present,
        empty allowlist, or missing channel). Audited via the structured logger
        (workspace_audit_log's CHECK constraint forbids entity_type='channel', so a
        log marker is the correct durable audit surface here).
        """
        channel = self._channel_cache.get(channel_id) or await db.channels.get(channel_id)
        if not channel:
            logger.warning("add_trusted_sender: channel %s not found", channel_id)
            return False

        allowed = _parse_json_list(channel.get("allowed_senders"))
        if not allowed or not allowed[0]:
            # No valid owner to preserve — refuse (fail closed). Approval only
            # makes sense once a channel has a NON-EMPTY owner at index 0 (a blank
            # owner is the degenerate config Gate-2 RANK-1 flagged).
            logger.warning(
                "add_trusted_sender: refusing on empty/blank-owner allowlist "
                "channel=%s", channel_id,
            )
            return False
        if sender_id in allowed:
            logger.info(
                "add_trusted_sender: %s already allowed on channel=%s — no-op",
                sender_id, channel_id,
            )
            return False

        new_allowed = allowed + [sender_id]  # append-only; index 0 immovable
        await db.channels.update(channel_id, {"allowed_senders": new_allowed})
        # Refresh cache so the tier change is not latent (skeptic C2).
        refreshed = await db.channels.get(channel_id)
        if refreshed is not None:
            self._channel_cache[channel_id] = refreshed
        else:
            self._channel_cache.pop(channel_id, None)
        logger.info(
            "channel_gateway.audit.allowlist_add channel=%s sender=%s actor=%s "
            "tier=trusted owner_preserved=%s",
            channel_id, sender_id, actor, allowed[0],
        )
        return True

    async def _maybe_prompt_owner_approval(
        self, channel: dict, channel_id: str, msg: "InboundMessage"
    ) -> bool:
        """DM the owner ONE Allow/Deny card for an unapproved group-channel sender.

        Dedup: one prompt per (channel, sender). The 'prompted' set is persisted in
        ``channel_config['pending_approvals']`` (survives gateway restart), so a
        chatty stranger triggers exactly one owner DM, not one per message.

        This NEVER grants access — it only surfaces the decision. The owner's
        button click (resolved in the adapter → ``add_trusted_sender``) is the sole
        grant path. Returns True if a prompt was sent this call, False on dedup /
        no owner / no adapter.
        """
        from channels import slack_approval as _sa

        allowed = _parse_json_list(channel.get("allowed_senders"))
        if not allowed:
            return False
        owner_id = allowed[0]
        sender_id = msg.external_sender_id

        pending = channel.get("pending_approvals")
        if isinstance(pending, str):
            try:
                pending = json.loads(pending)
            except (json.JSONDecodeError, TypeError):
                pending = {}
        if not isinstance(pending, dict):
            pending = {}

        existing = pending.get(sender_id)
        if _sa.pending_is_actionable(existing):
            return False  # already have a live prompt out for this sender — dedup

        # Bound the pending set (Gate-2 RANK-3): a PUBLIC flood of distinct
        # sender_ids must not grow channel_config unboundedly. First drop any
        # dead (resolved/expired) entries; if still at cap, refuse to add a new
        # prompt (the flood is denied, existing legit prompts are untouched).
        pending = {
            sid: e for sid, e in pending.items()
            if _sa.pending_is_actionable(e) or sid == sender_id
        }
        if len(pending) >= _MAX_PENDING_APPROVALS and sender_id not in pending:
            logger.warning(
                "owner-approval: pending cap (%d) reached on channel=%s — "
                "refusing new prompt for %s", _MAX_PENDING_APPROVALS, channel_id, sender_id,
            )
            return False

        adapter = self._adapters.get(channel_id)
        send_blocks = getattr(adapter, "send_blocks_to_user", None) if adapter else None
        if send_blocks is None:
            return False  # adapter can't deliver an interactive card — skip quietly

        pending_id = uuid4().hex
        pending[sender_id] = {
            "pending_id": pending_id,
            "status": "pending",
            "created_at": time.time(),
            "chat_id": msg.external_chat_id,
        }
        await db.channels.update(channel_id, {"pending_approvals": pending})
        refreshed = await db.channels.get(channel_id)
        if refreshed is not None:
            self._channel_cache[channel_id] = refreshed

        blocks = _sa.build_approval_blocks(
            sender_id=sender_id,
            sender_display_name=msg.sender_display_name or sender_id,
            pending_id=pending_id,
            channel_label=msg.external_chat_id,
        )
        await send_blocks(
            owner_id, blocks,
            f"{msg.sender_display_name or sender_id} wants to talk to me — approve?",
        )
        logger.info(
            "channel_gateway.audit.approval_prompt channel=%s sender=%s owner=%s "
            "pending_id=%s", channel_id, sender_id, owner_id, pending_id,
        )
        return True

    async def resolve_approval(
        self, channel_id: str, action_id: str, value: str, clicker_id: str
    ) -> None:
        """Resolve an owner Allow/Deny button click (adapter → here).

        Fail-closed on EVERY branch:
        * unknown action_id → ignore.
        * clicker is not the owner (``allowed_senders[0]``) → deny + audit, NEVER
          mutate. (A non-owner cannot escalate themselves.)
        * pending missing / already resolved / expired → no-op (state-based replay
          guard — a double-click or stale card grants nothing).
        Only an owner Allow on a live pending calls ``add_trusted_sender``.
        """
        from channels import slack_approval as _sa

        if action_id not in _sa._KNOWN_ACTIONS:
            return
        pending_id, sender_id = _sa.parse_action_value(value)
        if not pending_id or not sender_id:
            return

        channel = self._channel_cache.get(channel_id) or await db.channels.get(channel_id)
        if not channel:
            return

        if not _sa.is_owner_click(channel, clicker_id):
            logger.warning(
                "channel_gateway.audit.approval_denied_nonowner channel=%s "
                "clicker=%s sender=%s — ignored", channel_id, clicker_id, sender_id,
            )
            return

        pending = channel.get("pending_approvals")
        if isinstance(pending, str):
            try:
                pending = json.loads(pending)
            except (json.JSONDecodeError, TypeError):
                pending = {}
        if not isinstance(pending, dict):
            pending = {}
        entry = pending.get(sender_id)
        # Replay guard: the pending must exist, be unresolved, unexpired, AND match
        # the pending_id in the clicked button (a stale card carries an old id).
        if not _sa.pending_is_actionable(entry) or entry.get("pending_id") != pending_id:
            logger.info(
                "channel_gateway.approval_stale channel=%s sender=%s — no-op",
                channel_id, sender_id,
            )
            return

        approve = action_id == _sa.ACTION_ALLOW
        entry["status"] = "approved" if approve else "denied"
        pending[sender_id] = entry
        await db.channels.update(channel_id, {"pending_approvals": pending})
        refreshed = await db.channels.get(channel_id)
        if refreshed is not None:
            self._channel_cache[channel_id] = refreshed

        if approve:
            await self.add_trusted_sender(channel_id, sender_id, actor=f"owner:{clicker_id}")
        else:
            logger.info(
                "channel_gateway.audit.approval_denied channel=%s sender=%s "
                "owner=%s", channel_id, sender_id, clicker_id,
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
        except Exception as exc:  # noqa: BLE001
            # Degrade-OBSERVABLE. False means "slot free", so this concurrency guard
            # fails OPEN: a failure admits a second channel session and two users get
            # served at once, which is exactly what the check exists to prevent.
            logger.warning("channel slot-busy check failed, treating slot as FREE: %s",
                           exc)
            return False

    # ------------------------------------------------------------------
    # Slack presence (daemon lifecycle)
    # ------------------------------------------------------------------

    async def _set_all_slack_presence(self, presence: str) -> None:
        """Set presence on all running Slack adapters.

        Best-effort — failures are logged but don't block startup/shutdown.
        """
        for adapter in list(self._adapters.values()):
            if hasattr(adapter, "set_presence") and adapter.channel_type == "slack":
                try:
                    await adapter.set_presence(presence)
                    logger.info("Slack presence set to '%s' for channel %s", presence, adapter.channel_id)
                except Exception:
                    logger.debug("Failed to set Slack presence for channel %s", adapter.channel_id)

    # ------------------------------------------------------------------
    # Session pre-warming (MeshClaw pattern)
    # ------------------------------------------------------------------

    async def _prewarm_owner_session(self, channel: dict) -> None:
        """Pre-warm one IDLE subprocess for instant first-message response.

        Spawns a CLI subprocess (with full system prompt) during startup so
        the owner's first Slack message after daemon restart doesn't suffer
        ~4s cold-start latency.

        Best-effort: any failure is logged and silently ignored.  The first
        message will fall back to the normal cold-start path.
        """
        try:
            agent_id = channel.get("agent_id")
            if not agent_id:
                return

            router = session_registry.session_router
            if router is None:
                return

            # Build owner channel_context so the pre-warmed subprocess gets
            # Channel Security rules (sender identity, Slack formatting).
            # Without this, the first message lacks permission tier context.
            channel_config = channel.get("config", {})
            if isinstance(channel_config, str):
                import json as _json
                try:
                    channel_config = _json.loads(channel_config)
                except (ValueError, TypeError):
                    channel_config = {}

            allowed = _parse_json_list(channel_config.get("allowed_senders"))
            owner_id = allowed[0] if allowed else None
            channel_context = {
                "channel_type": channel.get("channel_type", ""),
                "channel_id": channel.get("id", ""),
                "is_group": False,
                "is_owner": True,
                **({"sender_identity": SenderIdentity(
                    external_id=owner_id or "owner",
                    display_name="Owner",
                    permission_tier=PermissionTier.OWNER,
                    is_owner=True,
                ).to_dict()} if owner_id else {}),
            }
            # Per-channel model override: must be included at prewarm time
            # so the subprocess spawns with the correct model (SDK locks model
            # per-session — can't change after spawn).
            _prewarm_model = channel_config.get("model")
            if _prewarm_model:
                channel_context["model"] = _prewarm_model

            temp_id = await router.prewarm_channel_session(
                agent_id, channel_context=channel_context,
            )
            if temp_id:
                self._prewarmed_session_id = temp_id
                logger.info(
                    "channel_gateway.prewarm_ready session_id=%s", temp_id,
                )
            else:
                logger.info("channel_gateway.prewarm_skipped (spawn failed or blocked)")
        except Exception as exc:
            logger.warning("channel_gateway.prewarm_failed: %s", exc)

    async def _try_adopt_prewarmed(self, session_id: str) -> bool:
        """Adopt the pre-warmed unit for a real session. Returns True on success."""
        prewarm_id = self._prewarmed_session_id
        if not prewarm_id:
            return False

        router = session_registry.session_router
        if router is None:
            return False

        adopted = await router.adopt_prewarmed_unit(prewarm_id, session_id)
        # Always clear — if rejection means unit died/evicted, retrying is
        # noise. Let the next message take the normal cold-start path.
        self._prewarmed_session_id = None
        if adopted:
            logger.info(
                "channel_gateway.prewarm_adopted %s → %s",
                prewarm_id, session_id,
            )
        else:
            logger.info(
                "channel_gateway.prewarm_rejected %s (unit not IDLE), cleared",
                prewarm_id,
            )
        return adopted

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
        adapter.set_on_approval(self.resolve_approval)

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
        self._channel_start_times[channel_id] = time.time()
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
        self._channel_start_times.pop(channel_id, None)

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
        self._channel_start_times.pop(channel_id, None)

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

        # Compute uptime from start time (if running)
        start_ts = self._channel_start_times.get(channel_id)
        uptime = round(time.time() - start_ts, 1) if start_ts else None

        return {
            "channel_id": channel_id,
            "status": status,
            "uptime_seconds": uptime,
            "messages_processed": self._channel_msg_counts.get(channel_id, 0),
            "active_sessions": active_sessions,
            "error_message": error_message,
        }

    # ------------------------------------------------------------------
    # L1 activation gate (should-I-reply)
    # ------------------------------------------------------------------

    async def _should_reply(
        self,
        channel: dict,
        msg: InboundMessage,
        chat_type: str,
        is_owner: bool,
    ) -> bool:
        """Decide whether the bot should reply to *msg* (L1 activation).

        Orthogonal to ``_check_access`` (authorization). This governs the
        SHOULD-I-REPLY decision per the channel's ``activation`` mode:

        * ``off``     — never reply (except the owner, who can always drive it,
                        e.g. to re-enable the channel).
        * ``mention`` — reply only when @-mentioned, OR (when ``thread_follow``
                        is on, the default) when this thread already has an
                        active session (the bot was engaged earlier in it).
        * ``review``  — reply (visible-text suppression is a downstream concern;
                        for Run 1 review behaves like ``always`` at this gate —
                        the no-visible-text rendering is a follow-up).
        * ``always``  — reply to every (authorized) message.

        Defaults: group channels (channel/group/mpim) → ``mention``; DMs (im)
        → ``always`` (a 1:1 DM is inherently addressed to the bot; requiring a
        mention there would silence it — F-regression the plan guards against).

        Fail-closed: an unknown/unrecognised mode falls back to ``mention``
        (the safest gating), never to ``always``.
        """
        is_group = chat_type in ("group", "channel", "mpim")
        default_mode = "mention" if is_group else "always"
        mode = (channel.get("activation") or default_mode)
        if not isinstance(mode, str):
            mode = default_mode
        mode = mode.strip().lower()

        # DMs are always addressed to the bot — never mention-gate them.
        if not is_group:
            return mode != "off" or is_owner

        if mode == "off":
            # Owner can still drive an off channel (e.g. a re-enable command).
            return is_owner
        if mode == "always" or mode == "review":
            return True
        if mode == "mention":
            if msg.metadata.get("is_mention"):
                return True
            # thread_follow (default on): once engaged in a thread, keep
            # following without a re-@. "Engaged" = the bot has ACTUALLY REPLIED
            # in this thread, i.e. a channel_session row exists AND its
            # message_count > 0.  message_count is bumped +2 only on a
            # successful reply (see _handle_conversation ~1806), so count==0
            # means "row exists but bot never replied" — a failed first attempt
            # OR an OBSERVE-only record (run_84cb2ea3).  Requiring count>0 is the
            # root-fix that (a) stops a stale count==0 row from wrongly following
            # and (b) lets observe-mode attach history to the SAME thread row
            # without silently flipping the thread into auto-reply.
            thread_follow = channel.get("thread_follow", True)
            if thread_follow and msg.external_thread_id:
                try:
                    existing = await db.channel_sessions.find_by_external(
                        channel.get("id") or channel.get("channel_id"),
                        msg.external_chat_id,
                        msg.external_thread_id,
                    )
                    if existing and (existing.get("message_count", 0) or 0) > 0:
                        return True
                except Exception as e:
                    # Fail-closed: if we can't confirm an engaged thread, do
                    # NOT reply to a non-mention (privacy/noise safe default).
                    logger.warning(
                        "channel_gateway._should_reply thread_follow lookup "
                        "failed (%s: %s) — treating as not-engaged",
                        type(e).__name__, e,
                    )
            return False
        # Unknown mode → fail-closed to mention gating (not always).
        return bool(msg.metadata.get("is_mention"))

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

        # Track per-channel message count for status reporting
        self._channel_msg_counts[channel_id] = self._channel_msg_counts.get(channel_id, 0) + 1

        # G8: Lazily evict stale rate-limiter entries (~every 100 messages)
        self._msg_counter += 1
        if self._msg_counter % 100 == 0:
            evicted = self._rate_limiter.evict_stale()
            if evicted:
                logger.debug("Rate limiter: evicted %d stale sender(s)", evicted)

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
        #
        # P0 BOOTSTRAP: If allowed_senders is empty, the first DM sender is
        # auto-promoted to owner. This handles new channel setup where the
        # UI only collects tokens and doesn't set allowed_senders. Without
        # this, the owner who just set up Slack gets locked out by their own
        # "secure default" empty allowlist.
        chat_type = msg.metadata.get("chat_type", "im")
        is_dm = chat_type == "im"
        # Single source (run_45187d49) of the thread the bot replies into: an
        # explicit inbound thread, else (group only) root a thread under the user's
        # message so replies/notices land in a thread, not the channel main stream.
        # DM → None (top-level, unchanged). Reused for session identity below so the
        # reply target and thread_follow key never drift.
        reply_thread_ts = _resolve_reply_thread_ts(
            msg.external_thread_id, msg.external_message_id, not is_dm and chat_type in ("group", "channel", "mpim")
        )

        if is_dm:
            allowed = _parse_json_list(channel.get("allowed_senders"))
            if not allowed:
                # Bootstrap: first DM sender becomes owner
                new_allowed = [msg.external_sender_id]
                await db.channels.update(channel_id, {
                    "allowed_senders": new_allowed,
                })
                # Refresh cache
                channel = await db.channels.get(channel_id)
                self._channel_cache[channel_id] = channel
                logger.info(
                    "Channel %s: bootstrap — first sender %s auto-added as "
                    "owner (allowed_senders was empty)",
                    channel_id, msg.external_sender_id,
                )

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
                        text="Hi! I'm a personal AI assistant. "
                             "DM access is limited to approved contacts. "
                             "Please reach out to the owner if you'd like access, "
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

        # 3.4. Slack-native owner approval (run_6038cd2c) -------------------------
        # If a PUBLIC (unapproved) sender speaks in a group channel that HAS an
        # owner, DM the owner ONE Allow/Deny card so they can add this teammate as
        # TRUSTED from inside Slack. Fail-closed + non-blocking: this NEVER grants
        # access on its own (only the owner's button click, via add_trusted_sender,
        # does) and the message continues down the normal path (the activation gate
        # below still decides reply/observe — a PUBLIC sender won't be recorded).
        # Dedup: one prompt per (channel, sender) via a persisted 'prompted' flag.
        if (
            not is_dm
            and sender_identity.permission_tier == PermissionTier.PUBLIC
            and _parse_json_list(channel.get("allowed_senders"))  # has an owner
        ):
            try:
                await self._maybe_prompt_owner_approval(channel, channel_id, msg)
            except Exception:
                logger.debug("owner-approval prompt failed (non-fatal)", exc_info=True)

        # 3.5. L1 activation gate (SHOULD-I-REPLY) --------------------------------
        # Orthogonal to access_mode (MAY-YOU-TALK, handled above). Decides whether
        # the bot replies at all, per the channel's activation mode. Group channels
        # default to `mention` (quiet unless @-mentioned or already engaged in the
        # thread); DMs (im) default to `always`. See _should_reply. run_4c5ad9c5.
        if not await self._should_reply(channel, msg, chat_type, is_owner):
            logger.info(
                "channel_gateway.activation_skip channel=%s chat_type=%s "
                "mode=%s is_mention=%s — no reply",
                channel_id, chat_type,
                (channel.get("activation") or ("always" if chat_type == "im" else "mention")),
                bool(msg.metadata.get("is_mention")),
            )
            # OBSERVE MODE (A) — run_84cb2ea3: the bot is NOT replying, but if the
            # sender is authorized (OWNER/TRUSTED) we still RECORD the message so
            # the bot has group context the next time it IS engaged (B), and so a
            # future conversation->DDD step has authorized raw material.
            # FAIL-CLOSED: a PUBLIC / unauthorized sender's message is NEVER
            # written (anti-poisoning — same philosophy as L3 assembly-exclusion:
            # never-stored > stored-but-guarded).  This path does NOT bump
            # message_count (so thread_follow stays not-engaged) and NEVER spawns
            # an agent (returns here, before run_conversation).
            if _is_authorized_tier(sender_identity):
                await self._observe_record(msg, channel, channel_id, agent_id,
                                           sender_identity)
            return

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
                        external_thread_id=reply_thread_ts,
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
                    external_thread_id=reply_thread_ts,
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

        # ── Human Mode queue: merge/redirect if agent is busy ──
        is_slack = channel.get("channel_type") == "slack"
        if is_slack:
            if conv_key not in self._message_queues:
                self._message_queues[conv_key] = ChannelMessageQueue(
                    session_id=f"{channel_id}:{msg.external_chat_id}",
                )
            queue = self._message_queues[conv_key]
            result = await queue.enqueue(QueuedMessage(
                text=msg.text or "",
                external_message_id=msg.external_message_id,
                external_sender_id=msg.external_sender_id,
                timestamp=time.time(),
            ))
            if result == "merged":
                # Message merged as supplement — don't process, just return
                return
            if result == "redirect":
                # Current processing will be cancelled via queue.cancelled flag.
                # The new request is in the queue and will be picked up after
                # the current task exits. Don't start a new task here.
                return
            # result == "queued" — proceed to handle normally

        async with self._conv_locks[conv_key]:
            return await self._handle_conversation(
                msg=msg,
                channel=channel,
                channel_id=channel_id,
                agent_id=agent_id,
                is_owner=is_owner,
                sender_identity=sender_identity,
                reply_thread_ts=reply_thread_ts,
            )

    # ------------------------------------------------------------------
    # Observe mode (A) — record without replying
    # ------------------------------------------------------------------

    async def _observe_record(
        self,
        msg: InboundMessage,
        channel: dict,
        channel_id: str,
        agent_id: str,
        sender_identity: Optional[SenderIdentity],
    ) -> None:
        """Record an authorized group message the bot chose NOT to reply to.

        OBSERVE MODE (A) — run_84cb2ea3.  Gives the bot group context for the
        next time it IS engaged (B) without replying now.  The CALLER has
        already gated on ``_is_authorized_tier`` (A write gate) — this method
        only runs for OWNER/TRUSTED senders.

        Invariants (all enforced by construction):
          * NEVER bumps ``message_count`` — reuses ``_resolve_session`` which
            creates the row at count=0; only a real reply bumps it (~1806).
            So thread_follow (which now requires count>0) stays not-engaged:
            observation NEVER flips a thread into auto-reply.
          * NEVER spawns an agent — no ``run_conversation`` call; returns after
            the DB write.
          * Shares the single UNIQUE(channel,chat,thread) channel_session row
            (no separate observe row).
          * Fail-soft: any error is logged, never raised (observation must not
            break the inbound path).
        """
        try:
            resolved_name = (
                sender_identity.display_name if sender_identity else None
            ) or msg.sender_display_name
            _sid, channel_session_id, _is_new, _prior = await self._resolve_session(
                channel_id=channel_id,
                agent_id=agent_id,
                external_chat_id=msg.external_chat_id,
                external_sender_id=msg.external_sender_id,
                external_thread_id=msg.external_thread_id,
                sender_display_name=resolved_name,
            )
            await db.channel_messages.put({
                "id": str(uuid4()),
                "channel_session_id": channel_session_id,
                "direction": "inbound",
                "external_message_id": msg.external_message_id,
                "content": msg.text or "[Attachment message]",
                "content_type": msg.metadata.get("message_type", "text"),
                "metadata": {
                    **msg.metadata,
                    "observed": True,  # recorded without a reply
                    **_sender_metadata(sender_identity, resolved_name),
                    "attachment_count": len(msg.attachments),
                },
                "status": "observed",
            })
            logger.info(
                "channel_gateway.observe_record channel=%s thread=%s tier=%s "
                "— recorded (no reply)",
                channel_id, msg.external_thread_id,
                sender_identity.permission_tier.value if sender_identity else "?",
            )
        except Exception:
            logger.exception(
                "channel_gateway.observe_record failed (non-fatal) channel=%s",
                channel_id,
            )

    # Max recent authorized messages injected on engagement (B).
    _OBSERVE_INJECT_MAX = 20

    async def _recent_authorized_history(
        self, channel_session_id: str
    ) -> list[dict]:
        """Return recent AUTHORIZED inbound history for injection (B).

        OBSERVE MODE (B) — run_84cb2ea3.  Reads the thread's recorded messages
        (``list_by_session`` → chronological ASC) and returns the last
        ``_OBSERVE_INJECT_MAX``, each as ``{sender, text, ts}``.

        READ GATE (defense-in-depth): a record is included ONLY if its stored
        ``sender_tier`` is owner/trusted.  Even though the write gate (A)
        already refuses to store PUBLIC messages, the read gate re-checks so a
        single source (``_is_authorized_tier`` semantics) governs both ends and
        a stray/legacy PUBLIC row can never leak into a prompt.  A record with
        no tier metadata is treated as unknown → EXCLUDED (fail-closed).
        """
        try:
            rows = await db.channel_messages.list_by_session(channel_session_id)
        except Exception:
            logger.exception("observe-inject: list_by_session failed")
            return []
        out: list[dict] = []
        for r in rows:
            if r.get("direction") != "inbound":
                continue
            meta = r.get("metadata") or {}
            if isinstance(meta, str):
                try:
                    meta = json.loads(meta)
                except (json.JSONDecodeError, TypeError):
                    meta = {}
            # Fail-closed read gate: only explicit owner/trusted survive.
            if meta.get("sender_tier") not in _AUTH:
                continue
            out.append({
                "sender": meta.get("sender_display_name") or "unknown",
                "text": r.get("content") or "",
                "ts": r.get("created_at"),
            })
        # Keep the newest _OBSERVE_INJECT_MAX (list is ASC → tail), preserve order.
        return out[-self._OBSERVE_INJECT_MAX:]

    async def _build_history_preamble(self, channel_session_id: str) -> str:
        """Render recent authorized history as a prompt preamble (B).

        Wraps ``_recent_authorized_history`` into a human-readable block that is
        PREPENDED to the user message (the real consumer). Returns "" when there
        is no authorized history (nothing to inject). The tier filter lives in
        ``_recent_authorized_history`` — this method is pure formatting.
        """
        recent = await self._recent_authorized_history(channel_session_id)
        if not recent:
            return ""
        lines = [f"- {h['sender']}: {h['text']}" for h in recent if h.get("text")]
        if not lines:
            return ""
        return (
            "[Recent channel discussion before this message — context only, "
            "authorized participants]\n" + "\n".join(lines)
        )

    # ------------------------------------------------------------------
    # Conversation handler
    # ------------------------------------------------------------------

    async def _handle_conversation(
        self,
        msg: InboundMessage,
        channel: dict,
        channel_id: str,
        agent_id: str,
        is_owner: bool = False,
        sender_identity: Optional[SenderIdentity] = None,
        reply_thread_ts: Optional[str] = None,
    ) -> None:
        """Inner handler — runs under per-conversation lock.

        ``reply_thread_ts`` (run_45187d49) is the SINGLE-SOURCE thread the bot
        replies into, computed ONCE by the caller (handle_inbound_message) via
        _resolve_reply_thread_ts and passed down — never recomputed here, so the
        reply target and the session key cannot drift on a chat_type default
        mismatch (Gate-2 BUG#1). Group + no inbound thread → user's message ts;
        DM → None (top-level).
        """
        try:
            # Use sender_identity.display_name (gateway-resolved, with
            # fallback chain) rather than msg.sender_display_name (raw
            # from adapter, which may be the unresolved user ID).
            resolved_name = (
                sender_identity.display_name if sender_identity else None
            ) or msg.sender_display_name
            session_id, channel_session_id, is_new_session, prior_session_id = (
                await self._resolve_session(
                    channel_id=channel_id,
                    agent_id=agent_id,
                    external_chat_id=msg.external_chat_id,
                    external_sender_id=msg.external_sender_id,
                    external_thread_id=reply_thread_ts,
                    sender_display_name=resolved_name,
                )
            )
        except Exception:
            logger.exception(f"Failed to resolve session for channel {channel_id}")
            return

        # ── Pre-warm adoption: if the owner's session is new (cold)
        # and we have a pre-warmed IDLE subprocess, adopt it to skip
        # the ~4s CLI spawn latency.
        if is_new_session and is_owner and self._prewarmed_session_id:
            await self._try_adopt_prewarmed(session_id)

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
                    # sender_tier + display_name (single source: _sender_metadata)
                    # so observe-inject (B, run_84cb2ea3) sees REAL reply-path
                    # turns, not only observe-only rows. Without sender_tier,
                    # B's fail-closed read gate excludes every reply-path
                    # message → a partial, misleading history.
                    **_sender_metadata(sender_identity, resolved_name),
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

        chat_type = msg.metadata.get("chat_type", "")
        is_group = chat_type in ("group", "channel", "mpim")
        # reply_thread_ts already computed at method entry (single source).

        channel_context = {
            "channel_type": channel.get("channel_type", ""),
            "channel_id": channel_id,
            "chat_id": msg.external_chat_id,
            "reply_to_message_id": msg.external_message_id,
            "is_group": is_group,
            "is_owner": is_owner,
            **({"sender_identity": sender_identity.to_dict()} if sender_identity else {}),
            **({"prior_session_id": prior_session_id} if prior_session_id else {}),
        }
        # Per-channel model override (stored in config.model)
        _channel_model = channel_config.get("model")
        if _channel_model:
            channel_context["model"] = _channel_model

        channel_type_str = channel.get("channel_type", "")
        if channel_type_str == "slack":
            channel_context["bot_token"] = channel_config.get("bot_token", "")
            channel_context["app_token"] = channel_config.get("app_token", "")

        final_text = await self._prepare_message_text(
            msg, agent_id, sender_identity,
        )

        # OBSERVE MODE (B) — run_84cb2ea3: PREPEND recent AUTHORIZED group
        # history to the user message so an @-engaged bot has the context of the
        # discussion that preceded the mention. This feeds the REAL consumer
        # (user_message=final_text → run_conversation), not a dead context key.
        # Group channels only (DMs carry their own session history). The read
        # gate (_recent_authorized_history) re-applies the tier filter
        # (defense-in-depth), so a PUBLIC/untiered record can never be injected.
        if is_group:
            try:
                history_preamble = await self._build_history_preamble(
                    channel_session_id
                )
                if history_preamble:
                    final_text = f"{history_preamble}\n\n{final_text}"
            except Exception:
                logger.exception(
                    "channel_gateway observe-inject failed (non-fatal) "
                    "channel=%s", channel_id,
                )


        # Build streaming context — all mutable state in a dataclass
        adapter = self._adapters.get(channel_id)
        use_native = (
            adapter is not None
            and adapter.supports_native_streaming
        )

        # ── Human Mode: Slack channels suppress all streaming/emoji ──
        # Instead of streaming tokens + tool emojis, we collect the
        # complete response and post it as a single message (like a
        # colleague replying on WeChat, not a terminal).
        human_mode = channel.get("channel_type") == "slack"

        ctx = StreamContext(
            adapter=adapter,
            external_chat_id=msg.external_chat_id,
            inbound_ts=msg.external_message_id,
            sender_user_id=msg.external_sender_id,
            # In human mode, disable ALL streaming — no reactions, no
            # progressive updates, no tool emojis.  The event loop still
            # runs but streaming guards (if ctx.streaming) skip everything.
            streaming=False if human_mode else (adapter is not None and adapter.supports_streaming),
            stream_thread_ts=reply_thread_ts or msg.external_message_id,
            native_streaming=False if human_mode else use_native,
        )

        # ── Human Mode: post ack message via heartbeat ──
        heartbeat_task: Optional[asyncio.Task] = None
        heartbeat_mgr: Optional[HeartbeatManager] = None
        if human_mode and adapter:
            complexity = estimate_complexity(final_text)
            ack_text = pick_ack(complexity)

            # Build adapter-specific callables for heartbeat
            _chat_id = msg.external_chat_id
            _thread_ts = reply_thread_ts

            async def _post_ack(channel: str, text: str) -> Optional[str]:
                """Post a plain text ack message to Slack, return ts."""
                # Use OutboundMessage for custom ack text (not the generic
                # "Thinking..." typing indicator).
                out = OutboundMessage(
                    channel_id=channel_id,
                    external_chat_id=channel,
                    external_thread_id=_thread_ts,
                    text=text,
                )
                return await adapter.send_message(out)

            async def _update_ack(channel: str, ts: str, text: str) -> None:
                """Update ack message text in-place (not final, no Block Kit)."""
                await adapter.update_message(
                    external_chat_id=channel,
                    message_id=ts,
                    text=text,
                    is_final=False,
                )

            async def _delete_ack(channel: str, ts: str) -> None:
                """Delete the ack message via Slack API."""
                if hasattr(adapter, '_slack_client') and adapter._slack_client:
                    loop = asyncio.get_running_loop()
                    await loop.run_in_executor(
                        None,
                        lambda: adapter._slack_client.chat_delete(
                            channel=channel, ts=ts,
                        ),
                    )

            heartbeat_mgr = HeartbeatManager(
                post_fn=_post_ack,
                update_fn=_update_ack,
                delete_fn=_delete_ack,
                channel=_chat_id,
                thread_ts=_thread_ts,
            )
            await heartbeat_mgr.post_ack(ack_text)
            heartbeat_task = asyncio.create_task(heartbeat_mgr.run())

        # Ack immediately — user sees 👀 before any processing (legacy mode only)
        if ctx.streaming:
            set_reaction(ctx, EMOJI_ACK, immediate=True)

        # Start streaming — prefer native (chat.startStream, zero rate limit)
        # over legacy (chat.postMessage + chat.update, ~50/min rate limit).
        if ctx.streaming and ctx.native_streaming:
            try:
                ctx.streaming_msg_id = await adapter.start_stream(
                    external_chat_id=ctx.external_chat_id,
                    external_thread_id=reply_thread_ts,
                    text=":bee: _Thinking..._",
                    recipient_user_id=msg.external_sender_id,
                    inbound_ts=msg.external_message_id,
                )
                if not ctx.streaming_msg_id:
                    # start_stream failed — fall back to legacy
                    ctx.native_streaming = False
                    logger.info(
                        "Native streaming start failed for channel %s — "
                        "falling back to legacy",
                        channel_id,
                    )
                else:
                    logger.info(
                        "Native streaming started for channel %s "
                        "(stream_ts=%s, thread_ts=%s)",
                        channel_id,
                        ctx.streaming_msg_id,
                        reply_thread_ts or msg.external_message_id,
                    )
            except Exception:
                ctx.native_streaming = False
                logger.warning(
                    "Native streaming start failed for channel %s, "
                    "using legacy",
                    channel_id, exc_info=True,
                )

        # Legacy path: postMessage → progressive chat.update
        if ctx.streaming and not ctx.native_streaming:
            try:
                ctx.streaming_msg_id = await adapter.send_typing_indicator(
                    external_chat_id=ctx.external_chat_id,
                    external_thread_id=reply_thread_ts,
                )
            except Exception:
                logger.exception("Failed to send typing indicator")
                ctx.streaming = False

        # Start legacy periodic flusher (only for legacy path — native
        # streaming flushes per-token via append_stream, no batching needed)
        if ctx.streaming and ctx.streaming_msg_id and not ctx.native_streaming:
            ctx.flush_task = asyncio.create_task(legacy_periodic(ctx))

        reply_text = ""
        error_occurred = False
        # ── Human Mode: mark queue as processing + drain any early supplements ──
        conv_key = (channel_id, msg.external_chat_id)
        queue: Optional[ChannelMessageQueue] = self._message_queues.get(conv_key)
        if human_mode and queue:
            queue.processing = True
            # Drain supplements that arrived between enqueue and here (rapid-fire)
            supplements = queue.drain_supplements()
            if supplements:
                final_text += f"\n\n{supplements}"

        _ttft_start = time.monotonic()
        _ttft_logged = False
        try:
            resume_sid = None if is_new_session else session_id
            async for event in session_registry.session_router.run_conversation(
                agent_id=agent_id,
                user_message=final_text,
                session_id=resume_sid,
                enable_skills=enable_skills,
                enable_mcp=enable_mcp,
                channel_context=channel_context,
            ):
                event_type = event.get("type", "")

                # ── Thinking streaming (native path only) ─────
                # Stream thinking tokens in italic so the user sees
                # activity immediately instead of a blank wait.
                # Thinking content is ephemeral — stop_stream replaces
                # the entire message with the final Block Kit reply.
                # NOTE: Legacy streaming (chat.update) skips thinking
                # — only native (append_stream) supports it. Future
                # adapters should check ctx.native_streaming.
                if event_type == "thinking_start":
                    if ctx.streaming and ctx.streaming_msg_id and ctx.native_streaming:
                        ctx.in_thinking = True
                        if not ctx.thinking_set:
                            ctx.thinking_set = True
                            set_reaction(ctx, EMOJI_THINKING)
                        reset_stall_timers(ctx)
                        # NOTE: the "💭 _" opener is lazily written on the FIRST
                        # non-empty thinking_delta (see thinking_delta handler),
                        # NOT here. Opus 4.8 over Bedrock emits thinking_start +
                        # an empty (signature-only) block with zero deltas — writing
                        # the opener here would leave a ghost "💭" widget with no
                        # content. end_thinking_phase() guards on thinking_content_sent.
                    continue

                if event_type == "thinking_delta":
                    if not _ttft_logged:
                        _ttft_ms = (time.monotonic() - _ttft_start) * 1000
                        logger.info(
                            "channel_gateway.ttft session_id=%s model=%s ttft_ms=%.0f",
                            session_id, _channel_model or "default", _ttft_ms,
                        )
                        _ttft_logged = True
                    thinking_text = event.get("thinking", "")
                    if thinking_text and ctx.in_thinking and ctx.native_streaming and ctx.streaming_msg_id:
                        # Lazy-open the thinking widget on first real content, so an
                        # empty Opus 4.8 thinking block never renders a ghost "💭".
                        if not ctx.thinking_content_sent:
                            try:
                                await adapter.append_stream(
                                    ctx.external_chat_id,
                                    ctx.streaming_msg_id,
                                    "💭 _",
                                )
                                ctx.thinking_content_sent = True
                            except Exception:
                                pass
                        ctx.native_pending_buf.append(thinking_text)
                        schedule_native_flush(ctx)
                        reset_stall_timers(ctx)
                    continue

                if event_type == "text_start":
                    if ctx.in_thinking:
                        await end_thinking_phase(ctx)
                    continue

                # ── Text streaming ────────────────────────────
                if event_type == "text_delta":
                    if not _ttft_logged:
                        _ttft_ms = (time.monotonic() - _ttft_start) * 1000
                        logger.info(
                            "channel_gateway.ttft session_id=%s model=%s ttft_ms=%.0f",
                            session_id, _channel_model or "default", _ttft_ms,
                        )
                        _ttft_logged = True
                    delta_text = event.get("text", "")
                    if delta_text:
                        ctx.stream_buf.append(delta_text)
                        if ctx.streaming and not ctx.thinking_set:
                            ctx.thinking_set = True
                            set_reaction(ctx, EMOJI_THINKING)
                        # Native streaming: buffer tokens and flush every
                        # 150ms via schedule_native_flush.  Zero rate limit
                        # but per-token is too chatty — batch a few tokens.
                        if ctx.native_streaming and ctx.streaming_msg_id:
                            ctx.native_pending_buf.append(delta_text)
                            schedule_native_flush(ctx)
                    continue

                if event_type == "tool_use" and ctx.streaming:
                    await handle_tool_use(ctx, event.get("name", ""))
                    continue

                if event_type == "tool_result" and ctx.streaming:
                    set_reaction(ctx, EMOJI_THINKING)
                    continue

                if event_type == "ask_user_question":
                    questions = event.get("questions", [])
                    ask_tool_use_id = event.get("toolUseId")
                    # Build a proper answers dict keyed on the EXACT question text
                    # (the shape the CLI's AskUserQuestion call() and the
                    # ask_question_gate hook's updatedInput.answers expect — same
                    # as the desktop frontend's finalAnswers[q.question]). Channels
                    # are unattended, so auto-pick the first option per question
                    # (falling back to a generic default). A JSON-encoded dict is
                    # REQUIRED: continue_with_answer does json.loads(answer) and an
                    # un-parseable free-text string would degrade to empty answers.
                    answers: dict[str, str] = {}
                    for q in questions:
                        if not isinstance(q, dict):
                            continue
                        q_text = q.get("question", "")
                        if not q_text:
                            continue
                        opts = q.get("options", [])
                        first_label = None
                        if opts and isinstance(opts[0], dict):
                            first_label = opts[0].get("label")
                        answers[q_text] = first_label or "Proceed with default"
                    answer_text = json.dumps(answers) if answers else ""
                    logger.info(
                        "Channel %s: auto-answering AskUserQuestion "
                        "(session=%s, questions=%d, answers=%d)",
                        channel_id, session_id, len(questions), len(answers),
                    )
                    try:
                        async for follow_event in (
                            session_registry.session_router.continue_with_answer(
                                session_id, answer_text,
                                tool_use_id=ask_tool_use_id,
                            )
                        ):
                            fe_type = follow_event.get("type", "")
                            # Thinking streaming in follow-up (mirrors main loop)
                            if fe_type == "thinking_start":
                                if ctx.streaming and ctx.streaming_msg_id and ctx.native_streaming:
                                    ctx.in_thinking = True
                                    if not ctx.thinking_set:
                                        ctx.thinking_set = True
                                        set_reaction(ctx, EMOJI_THINKING)
                                    reset_stall_timers(ctx)
                                    # Opener lazily written on first non-empty delta
                                    # (see below) — empty Opus 4.8 thinking → no ghost.
                                continue
                            if fe_type == "thinking_delta":
                                thinking_text = follow_event.get("thinking", "")
                                if thinking_text and ctx.in_thinking and ctx.native_streaming and ctx.streaming_msg_id:
                                    if not ctx.thinking_content_sent:
                                        try:
                                            await adapter.append_stream(
                                                ctx.external_chat_id,
                                                ctx.streaming_msg_id,
                                                "💭 _",
                                            )
                                            ctx.thinking_content_sent = True
                                        except Exception:
                                            pass
                                    ctx.native_pending_buf.append(thinking_text)
                                    schedule_native_flush(ctx)
                                    reset_stall_timers(ctx)
                                continue
                            if fe_type == "text_start":
                                if ctx.in_thinking:
                                    await end_thinking_phase(ctx)
                                continue
                            if fe_type == "text_delta" and ctx.streaming:
                                delta = follow_event.get("text", "")
                                if delta:
                                    ctx.stream_buf.append(delta)
                                    if ctx.native_streaming and ctx.streaming_msg_id:
                                        ctx.native_pending_buf.append(delta)
                                        schedule_native_flush(ctx)
                            elif fe_type == "assistant":
                                for blk in follow_event.get("content", []):
                                    if isinstance(blk, dict) and blk.get("type") == "text":
                                        t = blk.get("text", "")
                                        if t:
                                            reply_text = t
                            elif fe_type == "tool_use" and ctx.streaming:
                                await handle_tool_use(
                                    ctx, follow_event.get("name", ""),
                                )
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
                    current_text = ""
                    for block in event.get("content", []):
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
                    stop_reason = event.get("stop_reason", "")
                    cost = event.get("total_cost_usd")
                    duration = event.get("duration_ms")
                    logger.info(
                        "Agent conversation complete for channel %s "
                        "session %s (subtype=%s, stop_reason=%s, "
                        "cost=$%s, duration=%sms)",
                        channel_id, session_id, subtype,
                        stop_reason, cost, duration,
                    )
                    # Detect truncation: model hit output token limit
                    if stop_reason == "max_output_tokens":
                        logger.warning(
                            "Channel %s: response TRUNCATED "
                            "(stop_reason=max_output_tokens, session=%s)",
                            channel_id, session_id,
                        )
                        # Append visible indicator so user knows
                        ctx.stream_buf.append(
                            "\n\n---\n:warning: *Response truncated "
                            "(output limit reached). "
                            "Send \"continue\" to get the rest.*"
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
                    error_msg_text = event.get("error") or event.get("message") or "Unknown error"
                    logger.error(
                        f"Agent error on channel {channel_id}: {error_msg_text}"
                    )
                    if not reply_text:
                        reply_text = "Sorry, I hit an error processing that. Please try again."
                    error_occurred = True

        except Exception:
            logger.exception(f"Exception running agent conversation on channel {channel_id}")
            reply_text = "Sorry, something unexpected happened. Please try again."
            error_occurred = True
        finally:
            await cleanup_stream(ctx)
            # Cancel heartbeat if running
            if heartbeat_task and not heartbeat_task.done():
                heartbeat_task.cancel()
                try:
                    await heartbeat_task
                except (asyncio.CancelledError, Exception):
                    pass
            # Release queue processing state.
            # IMPORTANT: Any supplements that arrived DURING SDK processing
            # couldn't be injected mid-stream. Re-queue them as the next
            # message so they get processed. Without this, they're silently
            # dropped when processing=False clears _pending_supplements.
            if human_mode and queue:
                late_supplements = queue._pending_supplements.copy()
                queue.processing = False  # clears _pending_supplements
                if late_supplements and not queue.cancelled:
                    combined = " ".join(late_supplements)
                    await queue._queue.put(QueuedMessage(
                        text=combined,
                        external_message_id=None,
                        external_sender_id=msg.external_sender_id,
                        timestamp=time.time(),
                    ))
                    logger.info(
                        "Re-queued %d late supplement(s) for session %s",
                        len(late_supplements), session_id,
                    )

        if ctx.stream_flushed:
            reply_text = ctx.stream_flushed
        if not reply_text:
            reply_text = "(No response generated)"

        # Egress redaction (G1): redact the settled reply ONCE here so every
        # finalize path is covered — native stop_stream, legacy update_message
        # (both take reply_text directly, bypassing OutboundMessage), and the
        # OutboundMessage send paths (which re-redact idempotently). This is the
        # streaming-finalize counterpart to OutboundMessage.__post_init__.
        reply_text = redact_text(reply_text)

        # ── Final status reaction ───────────────────────────────────
        if ctx.streaming:
            await set_reaction_final(
                ctx, EMOJI_DONE if not error_occurred else EMOJI_ERROR,
            )

        # 6. Send outbound reply --------------------------------------------------
        external_message_id: Optional[str] = None

        # ── Human Mode: delete ack, post complete response as segments ──
        if human_mode and adapter and heartbeat_mgr:
            # Check if cancelled (user sent "stop" during processing)
            if queue and queue.cancelled:
                await heartbeat_mgr.update_final("好的，停了。有什么新问题随时说。")
                logger.info("Channel %s: response cancelled by user", channel_id)
                # Skip response posting — go to message logging
                external_message_id = heartbeat_mgr.ack_ts
                # Jump past all response posting logic
                reply_text = "(Cancelled by user)"
            else:
                # Delete the ack message
                await heartbeat_mgr.delete_ack()

                # Format and post response as human-like segments
                formatter = HumanResponseFormatter()
                segments = formatter.format(reply_text)
                for i, segment in enumerate(segments):
                    outbound = OutboundMessage(
                        channel_id=channel_id,
                        external_chat_id=msg.external_chat_id,
                        external_thread_id=reply_thread_ts,
                        reply_to_message_id=msg.external_message_id if i == 0 else None,
                        text=segment,
                    )
                    try:
                        seg_id = await adapter.send_message(outbound)
                        if i == 0:
                            external_message_id = seg_id
                    except Exception:
                        logger.exception(
                            "Failed to send human-mode segment %d on channel %s",
                            i, channel_id,
                        )
                    # Human-like pacing between segments
                    if i < len(segments) - 1:
                        await asyncio.sleep(1.0)

        elif ctx.streaming and ctx.streaming_msg_id and ctx.native_streaming:
            # Native streaming: finalize via stop_stream.  The adapter
            # is responsible for converting text → Block Kit internally.
            try:
                await adapter.stop_stream(
                    external_chat_id=ctx.external_chat_id,
                    stream_ts=ctx.streaming_msg_id,
                    text=reply_text,
                )
                external_message_id = ctx.streaming_msg_id
                logger.info(
                    "Native stream finalized for channel %s "
                    "(stream_ts=%s, reply_len=%d)",
                    channel_id, ctx.streaming_msg_id, len(reply_text),
                )
            except Exception:
                logger.exception("Failed to stop native stream; falling back")
                ctx.streaming = False
                ctx.native_streaming = False
        elif ctx.streaming and ctx.streaming_msg_id:
            # Legacy streaming: finalize via chat.update with Block Kit
            try:
                await adapter.update_message(
                    external_chat_id=ctx.external_chat_id,
                    message_id=ctx.streaming_msg_id,
                    text=reply_text,
                    is_final=True,
                )
                external_message_id = ctx.streaming_msg_id
            except Exception:
                logger.exception("Failed to send final streaming update; falling back")
                ctx.streaming = False

        if not human_mode and (not ctx.streaming or not ctx.streaming_msg_id):
            outbound = OutboundMessage(
                channel_id=channel_id,
                external_chat_id=msg.external_chat_id,
                external_thread_id=reply_thread_ts,
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

        OFF-LOOP (run_a1f4c2d8): every filesystem step (mkdir, the collision
        ``exists()`` probe loop, and the ``write_bytes`` of the whole attachment)
        runs inside the sync ``_stage()`` helper, dispatched via
        ``asyncio.to_thread``. Previously they executed DIRECTLY in this
        ``async def`` body, so a large Slack attachment blocked the event loop —
        stalling every other request and every chat tab's SSE stream for the
        duration of the write. The sanctioned shape is exactly this: a plain sync
        helper does the blocking work, the async caller awaits it in a thread
        (see tests/test_router_async_blocking.py, which now also scans channels/).
        """
        try:
            ws_root = Path(initialization_manager.get_cached_workspace_path())
            if sender_identity and not sender_identity.is_owner:
                # Sender-scoped directory — matches file_access_handler sandbox
                base_dir = ws_root / "channel_files" / sender_identity.external_id
            else:
                base_dir = ws_root / "channel_files" / agent_id
            safe_name = _sanitize_filename(file_name)

            def _stage() -> Path:
                """All blocking filesystem work, off the event loop.

                Kept as ONE helper (not three awaits) so the collision probe and
                the write stay in the same thread hop — splitting them would widen
                the TOCTOU window between "this name is free" and "claim it", and
                cost 3 context switches instead of 1.
                """
                base_dir.mkdir(parents=True, exist_ok=True)
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
                return target

            target = await asyncio.to_thread(_stage)
            logger.info("Staged file '%s' to %s", file_name, target)
            return str(target)
        except Exception:
            logger.exception("Failed to stage file '%s' for agent %s", file_name, agent_id)
            return None

    # ------------------------------------------------------------------
    # Thread history injection
    # ------------------------------------------------------------------


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
                # G2: Clean up conversation lock for the rotated session.
                # The old key is stale — a new lock will be created on next use.
                self._conv_locks.pop((channel_id, external_chat_id), None)
                # G3: Kill the old SessionUnit subprocess to prevent zombie leak.
                # Without this, old units remain alive with is_channel_session=True
                # and TTL-immune — accumulating as resource leaks.
                # Fire-and-forget: kill() can block up to 13s (SIGKILL + wrapper
                # exit), which would hold the conv_lock and block incoming messages.
                try:
                    from core.session_registry import session_router
                    if session_router:
                        asyncio.create_task(
                            session_router.kill_rotated_channel_session(old_session_id)
                        )
                except Exception as exc:
                    logger.debug(
                        "Failed to schedule kill for rotated session %s: %s",
                        old_session_id, exc,
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
            allowed = _parse_json_list(channel_config.get("allowed_senders"))
            # Empty allowlist => no one is allowed (secure default)
            return sender_id in allowed

        if access_mode == "blocklist":
            blocked = _parse_json_list(channel_config.get("blocked_senders"))
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
