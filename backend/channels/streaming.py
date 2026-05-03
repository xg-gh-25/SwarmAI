"""Streaming context and helpers for the channel gateway.

Extracted from ``ChannelGateway`` (G6 follow-up) so streaming logic —
reaction controller, debounce, stall timers, legacy flusher — can be
reasoned about and tested independently of the 1,800-line gateway.

All functions operate on a :class:`StreamContext` dataclass; none need
access to the full gateway or adapter beyond what's already on ``ctx``.
"""
from __future__ import annotations

import asyncio
import dataclasses
import logging
from typing import Optional

from channels.base import ChannelAdapter

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Reaction emoji constants
# ---------------------------------------------------------------------------

EMOJI_ACK = "eyes"
EMOJI_THINKING = "thinking_face"
EMOJI_TOOL = "fire"
EMOJI_CODING = "male-technologist"
EMOJI_WEB = "zap"
EMOJI_DONE = "white_check_mark"
EMOJI_ERROR = "x"
EMOJI_STALL_SOFT = "hourglass_flowing_sand"
EMOJI_STALL_HARD = "warning"

# ---------------------------------------------------------------------------
# Timing constants
# ---------------------------------------------------------------------------

DEBOUNCE_S = 0.7
STALL_SOFT_S = 20.0
STALL_HARD_S = 60.0
LEGACY_FLUSH_S = 1.2
NATIVE_THROTTLE_S = 0.15  # batch tokens for 150ms before append_stream

# ---------------------------------------------------------------------------
# Tool classification
# ---------------------------------------------------------------------------

CODING_TOKENS = frozenset({"bash", "read", "write", "edit", "glob", "grep", "notebookedit"})
WEB_TOKENS = frozenset({"webfetch", "web_search", "web_fetch", "browser", "tavily"})


def resolve_tool_emoji(tool_name: str) -> str:
    """Map a tool name to a status emoji."""
    lower = tool_name.lower()
    if any(t in lower for t in WEB_TOKENS):
        return EMOJI_WEB
    if any(t in lower for t in CODING_TOKENS):
        return EMOJI_CODING
    return EMOJI_TOOL


# ---------------------------------------------------------------------------
# StreamContext — mutable state for a single streaming conversation
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class StreamContext:
    """Mutable state for a single streaming conversation.

    Holds the adapter reference, buffer, reaction state, and timer handles.
    All streaming helper functions operate on this dataclass.
    """
    adapter: Optional[ChannelAdapter]
    external_chat_id: str
    inbound_ts: Optional[str]       # for reactions
    sender_user_id: str             # for DM context
    streaming: bool = False
    native_streaming: bool = False   # True = chat.startStream/appendStream path
    streaming_msg_id: Optional[str] = None
    stream_thread_ts: Optional[str] = None

    # Buffer + state
    stream_buf: list[str] = dataclasses.field(default_factory=list)
    flush_lock: asyncio.Lock = dataclasses.field(default_factory=asyncio.Lock)
    stream_flushed: str = ""
    stream_done: asyncio.Event = dataclasses.field(default_factory=asyncio.Event)

    # Reaction state
    current_reaction: Optional[str] = None
    reaction_finished: bool = False
    debounce_handle: Optional[asyncio.TimerHandle] = None
    stall_soft_handle: Optional[asyncio.TimerHandle] = None
    stall_hard_handle: Optional[asyncio.TimerHandle] = None

    # Native streaming throttle
    native_pending_buf: list[str] = dataclasses.field(default_factory=list)
    native_flush_handle: Optional[asyncio.TimerHandle] = None
    native_flush_task: Optional[asyncio.Task] = None

    # Legacy flusher
    flush_task: Optional[asyncio.Task] = None

    thinking_set: bool = False


# ---------------------------------------------------------------------------
# Reaction helpers
# ---------------------------------------------------------------------------

async def do_set_reaction(ctx: StreamContext, emoji: str) -> None:
    """Apply a single reaction, removing the previous one."""
    if not ctx.adapter or not ctx.inbound_ts or ctx.current_reaction == emoji:
        return
    old = ctx.current_reaction
    if old:
        try:
            await ctx.adapter.remove_reaction(ctx.external_chat_id, ctx.inbound_ts, old)
        except Exception:
            pass
    try:
        await ctx.adapter.add_reaction(ctx.external_chat_id, ctx.inbound_ts, emoji)
        ctx.current_reaction = emoji
    except Exception:
        pass


def apply_reaction_now(ctx: StreamContext, emoji: str, *, skip_stall_reset: bool = False) -> None:
    """Apply a reaction immediately (cancel pending debounce)."""
    if ctx.reaction_finished:
        return
    if ctx.debounce_handle:
        ctx.debounce_handle.cancel()
        ctx.debounce_handle = None
    asyncio.ensure_future(do_set_reaction(ctx, emoji))
    if not skip_stall_reset:
        reset_stall_timers(ctx)


def set_reaction(ctx: StreamContext, emoji: str, *, immediate: bool = False) -> None:
    """Set a reaction with optional debounce."""
    if ctx.reaction_finished:
        return
    if immediate:
        apply_reaction_now(ctx, emoji)
        return
    if ctx.debounce_handle:
        ctx.debounce_handle.cancel()
        ctx.debounce_handle = None
    ctx.debounce_handle = asyncio.get_event_loop().call_later(
        DEBOUNCE_S, lambda: apply_reaction_now(ctx, emoji),
    )
    reset_stall_timers(ctx)


async def set_reaction_final(ctx: StreamContext, emoji: str) -> None:
    """Set the final reaction and cancel all timers."""
    ctx.reaction_finished = True
    clear_all_timers(ctx)
    await do_set_reaction(ctx, emoji)


# ---------------------------------------------------------------------------
# Timer management
# ---------------------------------------------------------------------------

def reset_stall_timers(ctx: StreamContext) -> None:
    """Reset soft/hard stall timers."""
    eloop = asyncio.get_event_loop()
    if ctx.stall_soft_handle:
        ctx.stall_soft_handle.cancel()
    if ctx.stall_hard_handle:
        ctx.stall_hard_handle.cancel()
    ctx.stall_soft_handle = eloop.call_later(
        STALL_SOFT_S,
        lambda: apply_reaction_now(ctx, EMOJI_STALL_SOFT, skip_stall_reset=True),
    )
    ctx.stall_hard_handle = eloop.call_later(
        STALL_HARD_S,
        lambda: apply_reaction_now(ctx, EMOJI_STALL_HARD, skip_stall_reset=True),
    )


def clear_all_timers(ctx: StreamContext) -> None:
    """Cancel all pending timers."""
    for h in (ctx.debounce_handle, ctx.stall_soft_handle, ctx.stall_hard_handle):
        if h:
            h.cancel()
    ctx.debounce_handle = ctx.stall_soft_handle = ctx.stall_hard_handle = None


# ---------------------------------------------------------------------------
# Legacy streaming (chat.update path)
# ---------------------------------------------------------------------------

async def legacy_flush(ctx: StreamContext) -> None:
    """Flush buffered tokens via legacy chat.update."""
    if not ctx.streaming or not ctx.streaming_msg_id or not ctx.stream_buf:
        return
    async with ctx.flush_lock:
        if not ctx.stream_buf:
            return  # another flush drained it
        ctx.stream_flushed += "".join(ctx.stream_buf)
        ctx.stream_buf.clear()
    try:
        await ctx.adapter.update_message(
            external_chat_id=ctx.external_chat_id,
            message_id=ctx.streaming_msg_id,
            text=ctx.stream_flushed + " ✍️",
        )
    except Exception:
        logger.warning("Legacy flush: chat.update failed (rate limit?)")


async def legacy_periodic(ctx: StreamContext) -> None:
    """Periodic legacy flusher loop."""
    while not ctx.stream_done.is_set():
        await asyncio.sleep(LEGACY_FLUSH_S)
        await legacy_flush(ctx)


async def native_flush_now(ctx: StreamContext) -> None:
    """Flush buffered native tokens via append_stream."""
    if not ctx.native_pending_buf or not ctx.streaming_msg_id or not ctx.adapter:
        return
    chunk = "".join(ctx.native_pending_buf)
    ctx.native_pending_buf.clear()
    try:
        await ctx.adapter.append_stream(
            ctx.external_chat_id, ctx.streaming_msg_id, chunk,
        )
    except Exception:
        logger.debug("native append_stream failed for %s", ctx.streaming_msg_id)


def schedule_native_flush(ctx: StreamContext) -> None:
    """Schedule a native flush after NATIVE_THROTTLE_S.

    Batches multiple text_delta tokens into a single append_stream call.
    If a flush is already scheduled, this is a no-op.
    """
    if ctx.native_flush_handle is not None:
        return  # already scheduled
    if ctx.stream_done.is_set():
        return

    def _fire() -> None:
        ctx.native_flush_handle = None
        ctx.native_flush_task = asyncio.ensure_future(native_flush_now(ctx))

    ctx.native_flush_handle = asyncio.get_event_loop().call_later(
        NATIVE_THROTTLE_S, _fire,
    )


async def cleanup_stream(ctx: StreamContext) -> None:
    """Clean up streaming resources (timers, tasks, buffer drain)."""
    ctx.stream_done.set()

    # Cancel native throttle timer and do a final append_stream so the
    # user sees the last tokens before stop_stream replaces with Block Kit.
    if ctx.native_flush_handle:
        ctx.native_flush_handle.cancel()
        ctx.native_flush_handle = None
    # Await pending native flush task
    if ctx.native_flush_task and not ctx.native_flush_task.done():
        try:
            await ctx.native_flush_task
        except Exception:
            pass
    # Final native flush — send any remaining buffered tokens
    if ctx.native_pending_buf and ctx.streaming_msg_id and ctx.adapter:
        chunk = "".join(ctx.native_pending_buf)
        ctx.native_pending_buf.clear()
        try:
            await ctx.adapter.append_stream(
                ctx.external_chat_id, ctx.streaming_msg_id, chunk,
            )
        except Exception:
            pass  # non-fatal — tokens still in stream_buf via drain below

    if ctx.flush_task is not None:
        ctx.flush_task.cancel()
        try:
            await ctx.flush_task
        except asyncio.CancelledError:
            pass

    # Final drain — merge both native and legacy buffers into stream_flushed
    # so the final reply_text captures everything.
    if ctx.native_pending_buf:
        ctx.stream_buf.extend(ctx.native_pending_buf)
        ctx.native_pending_buf.clear()
    if ctx.stream_buf:
        ctx.stream_flushed += "".join(ctx.stream_buf)
        ctx.stream_buf.clear()
