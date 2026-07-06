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
from typing import Any, Optional

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

    # Egress redaction (G1): rolling-buffer redactor for the append-only native
    # stream, so a credential split across two token-chunks never leaves
    # half-redacted. Lazily created on first drain (keeps the leaf import out of
    # this module's import path). See channels/egress_redactor.py.
    stream_redactor: Any = None

    # Legacy flusher
    flush_task: Optional[asyncio.Task] = None

    thinking_set: bool = False

    # Thinking streaming — shows thinking tokens in italic before the reply
    in_thinking: bool = False
    thinking_content_sent: bool = False  # True once any thinking content delivered to adapter


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


def _ensure_stream_redactor(ctx: StreamContext) -> Any:
    """Lazily create the per-stream egress redactor (G1)."""
    if ctx.stream_redactor is None:
        from channels.egress_redactor import StreamRedactor
        ctx.stream_redactor = StreamRedactor()
    return ctx.stream_redactor


def drain_stream_redactor(ctx: StreamContext) -> str:
    """Release + redact the withheld tail before any STATIC content is appended.

    The native stream is append-only, so ordering matters: whenever a caller is
    about to append a static separator / tool-status line directly (bypassing
    ``native_flush_now``), it must first release the redactor's held-back token
    tail — otherwise that tail would be emitted *after* the separator, scrambling
    order. Returns the redacted tail (possibly "").
    """
    if ctx.stream_redactor is None:
        return ""
    return ctx.stream_redactor.flush()


async def native_flush_now(ctx: StreamContext) -> None:
    """Flush buffered native tokens via append_stream (egress-redacted, G1).

    Tokens pass through a rolling-buffer ``StreamRedactor`` so a credential split
    across two throttle batches (``AKIA…`` in one, the rest in the next) is never
    appended half-formed. The trailing not-yet-terminated token is withheld until
    the next batch completes it or a phase boundary flushes it.
    """
    if not ctx.native_pending_buf or not ctx.streaming_msg_id or not ctx.adapter:
        return
    chunk = "".join(ctx.native_pending_buf)
    ctx.native_pending_buf.clear()
    safe = _ensure_stream_redactor(ctx).feed(chunk)
    if not safe:
        return  # entire chunk is a still-in-flight token; hold for next batch
    try:
        await ctx.adapter.append_stream(
            ctx.external_chat_id, ctx.streaming_msg_id, safe,
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


async def end_thinking_phase(ctx: StreamContext) -> None:
    """Transition from thinking to text phase.

    Flushes any pending thinking tokens, appends a closing italic marker
    and separator, then resets the thinking state.  Called when the first
    ``text_start`` event arrives after a thinking block.
    """
    if not ctx.in_thinking:
        return
    ctx.in_thinking = False

    if not ctx.streaming_msg_id or not ctx.adapter:
        return

    # Cancel any pending native flush so we can do a clean transition
    if ctx.native_flush_handle:
        ctx.native_flush_handle.cancel()
        ctx.native_flush_handle = None

    # Flush remaining thinking tokens (egress-redacted, G1) + release any tail
    # the rolling redactor withheld, THEN close italic + separator. The redacted
    # dynamic content must precede the static separator to keep append order.
    pending = ""
    if ctx.native_pending_buf:
        pending = "".join(ctx.native_pending_buf)
        ctx.native_pending_buf.clear()
    if pending:
        pending = _ensure_stream_redactor(ctx).feed(pending)
    pending += drain_stream_redactor(ctx)

    # Only emit separator if thinking content was actually delivered to user.
    # If the opener (💭 _) failed, sending a stray _\n\n---\n\n is confusing.
    if not ctx.thinking_content_sent and not pending:
        return

    # Close the italic block and add a visual separator before the reply
    separator = "_\n\n---\n\n"
    try:
        await ctx.adapter.append_stream(
            ctx.external_chat_id,
            ctx.streaming_msg_id,
            pending + separator,
        )
    except Exception:
        logger.debug("end_thinking_phase: append_stream failed")


async def handle_tool_use(ctx: StreamContext, tool_name: str) -> None:
    """Handle a tool_use event during streaming.

    Closes any open thinking phase, sets the tool emoji reaction, and
    appends a ``_Using tool: {name}..._`` status line to the stream
    (native or legacy path).  Shared by the main event loop and the
    follow-up (continue_with_answer) loop so behaviour stays identical.
    """
    if ctx.in_thinking:
        await end_thinking_phase(ctx)

    set_reaction(ctx, resolve_tool_emoji(tool_name))

    if not ctx.streaming_msg_id:
        return

    tool_status = f"\n\n_Using tool: {tool_name}..._"

    if ctx.native_streaming:
        # Cancel pending throttle timer to prevent out-of-order delivery,
        # flush buffered tokens first, then append tool status.
        if ctx.native_flush_handle:
            ctx.native_flush_handle.cancel()
            ctx.native_flush_handle = None
        pending = ""
        if ctx.native_pending_buf:
            pending = "".join(ctx.native_pending_buf)
            ctx.native_pending_buf.clear()
        # Egress-redact dynamic tokens + release the withheld tail (G1) before
        # the static tool-status line, so append order is preserved.
        if pending:
            pending = _ensure_stream_redactor(ctx).feed(pending)
        pending += drain_stream_redactor(ctx)
        # Drain stream_buf for bookkeeping
        if ctx.stream_buf:
            ctx.stream_flushed += "".join(ctx.stream_buf)
            ctx.stream_buf.clear()
        try:
            await ctx.adapter.append_stream(
                ctx.external_chat_id,
                ctx.streaming_msg_id,
                pending + tool_status,
            )
        except Exception:
            pass
    else:
        # Legacy: flush buffer then update_message
        await legacy_flush(ctx)
        status = (
            ctx.stream_flushed + tool_status
            if ctx.stream_flushed
            else tool_status.lstrip("\n")
        )
        try:
            await ctx.adapter.update_message(
                external_chat_id=ctx.external_chat_id,
                message_id=ctx.streaming_msg_id,
                text=status,
            )
        except Exception:
            pass


async def cleanup_stream(ctx: StreamContext) -> None:
    """Clean up streaming resources (timers, tasks, buffer drain)."""
    ctx.stream_done.set()

    # Cancel native throttle timer
    if ctx.native_flush_handle:
        ctx.native_flush_handle.cancel()
        ctx.native_flush_handle = None
    # Await pending native flush task
    if ctx.native_flush_task and not ctx.native_flush_task.done():
        try:
            await ctx.native_flush_task
        except Exception:
            pass

    # IMPORTANT: Discard thinking tokens BEFORE the final native flush.
    # If we're still in thinking phase (error/timeout during thinking),
    # native_pending_buf contains thinking tokens that must NOT reach
    # stream_flushed.  Discard them now — they were already streamed
    # visually (ephemeral, stop_stream replaces everything).
    if ctx.in_thinking:
        ctx.native_pending_buf.clear()
        ctx.in_thinking = False

    # Final native flush — send any remaining text tokens so the user sees them
    # before stop_stream replaces with Block Kit. Egress-redacted (G1): route the
    # terminal buffer through the redactor AND release its withheld tail, so this
    # last append can neither leak a credential (raw append) nor silently drop the
    # tail the rolling buffer was holding. This is the stream-termination
    # counterpart to native_flush_now; it MUST redact like every other append.
    if ctx.streaming_msg_id and ctx.adapter:
        pending = ""
        if ctx.native_pending_buf:
            pending = "".join(ctx.native_pending_buf)
            ctx.native_pending_buf.clear()
        if pending:
            pending = _ensure_stream_redactor(ctx).feed(pending)
        pending += drain_stream_redactor(ctx)  # release + redact the withheld tail
        if pending:
            try:
                await ctx.adapter.append_stream(
                    ctx.external_chat_id, ctx.streaming_msg_id, pending,
                )
            except Exception:
                pass  # non-fatal — tokens still in stream_buf via drain below

    if ctx.flush_task is not None:
        ctx.flush_task.cancel()
        try:
            await ctx.flush_task
        except asyncio.CancelledError:
            pass

    # Final drain — merge remaining stream_buf into stream_flushed
    # so the final reply_text captures everything.
    # Note: native_pending_buf is already empty (cleared by thinking
    # discard or final native flush above), so only stream_buf needs draining.
    if ctx.stream_buf:
        ctx.stream_flushed += "".join(ctx.stream_buf)
        ctx.stream_buf.clear()
