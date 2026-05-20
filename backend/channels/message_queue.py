"""FIFO message queue with merge semantics for channel sessions.

When the agent is processing a request, new messages from the same user
are merged as supplementary context rather than queued as separate requests.
Redirect signals ("算了", "不用了", "换个问题") cancel the current request
and start a new one.

Key exports:
    ChannelMessageQueue — per-session queue managing message flow
    is_redirect — detect cancellation/redirect signals in user text
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Redirect detection
# ---------------------------------------------------------------------------

# Keywords that signal the user is cancelling/changing direction,
# not supplementing the current request.
REDIRECT_KEYWORDS = [
    "算了", "不用了", "换个问题", "别查了", "别看了",
    "stop", "never mind", "forget it", "cancel",
]

# Compiled as alternation for O(1) check
_REDIRECT_RE = re.compile(
    "|".join(re.escape(k) for k in REDIRECT_KEYWORDS),
    re.IGNORECASE,
)


def is_redirect(text: str) -> bool:
    """Return True if text contains a redirect/cancellation signal."""
    return bool(_REDIRECT_RE.search(text))


def extract_post_redirect(text: str) -> str:
    """Extract the new request after a redirect keyword.

    E.g. "算了，看下 CI" → "看下 CI"
    Returns the full text if no clear split point.
    """
    match = _REDIRECT_RE.search(text)
    if not match:
        return text
    remainder = text[match.end():].strip()
    # Strip common connectors
    remainder = re.sub(r'^[，,、\s]+', '', remainder)
    return remainder if remainder else text


# ---------------------------------------------------------------------------
# Message dataclass
# ---------------------------------------------------------------------------

@dataclass
class QueuedMessage:
    """A message waiting to be processed."""
    text: str
    external_message_id: Optional[str] = None
    external_sender_id: Optional[str] = None
    timestamp: float = 0.0


# ---------------------------------------------------------------------------
# ChannelMessageQueue
# ---------------------------------------------------------------------------

@dataclass
class ChannelMessageQueue:
    """Per-session message queue with merge-on-busy semantics.

    Behavior:
    - If not processing: message goes to queue for immediate pickup
    - If processing + message is redirect: cancel current, queue new
    - If processing + message is supplement: merge into pending supplements
    """
    session_id: str
    _queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    _processing: bool = False
    _pending_supplements: list[str] = field(default_factory=list)
    _cancel_event: asyncio.Event = field(default_factory=asyncio.Event)

    @property
    def processing(self) -> bool:
        return self._processing

    @processing.setter
    def processing(self, value: bool) -> None:
        self._processing = value
        if not value:
            self._pending_supplements.clear()
            self._cancel_event.clear()

    @property
    def cancelled(self) -> bool:
        return self._cancel_event.is_set()

    def request_cancel(self) -> None:
        """Signal cancellation of the current processing task."""
        self._cancel_event.set()

    async def enqueue(self, msg: QueuedMessage) -> str:
        """Add a message to the queue.

        Returns:
            'queued' — message will be processed after current completes
            'merged' — message merged as supplement to current request
            'redirect' — current request cancelled, new request queued
        """
        if not self._processing:
            await self._queue.put(msg)
            return "queued"

        # Processing is active — decide merge vs redirect
        if is_redirect(msg.text):
            # Cancel current, queue the new request
            new_text = extract_post_redirect(msg.text)
            self._cancel_event.set()
            await self._queue.put(QueuedMessage(
                text=new_text,
                external_message_id=msg.external_message_id,
                external_sender_id=msg.external_sender_id,
                timestamp=msg.timestamp,
            ))
            logger.info(
                "Redirect detected in session %s: cancelling current, "
                "queuing new request",
                self.session_id,
            )
            return "redirect"

        # Merge as supplement
        self._pending_supplements.append(msg.text)
        logger.info(
            "Merged supplement in session %s: %d pending",
            self.session_id, len(self._pending_supplements),
        )
        return "merged"

    def drain_supplements(self) -> Optional[str]:
        """Drain pending supplements into a single merged string.

        Returns None if no supplements pending.
        """
        if not self._pending_supplements:
            return None
        merged = "\n\n".join(
            f"[追加] {s}" for s in self._pending_supplements
        )
        self._pending_supplements.clear()
        return merged

    async def get(self) -> QueuedMessage:
        """Get next message from queue (blocks until available)."""
        return await self._queue.get()

    def empty(self) -> bool:
        """Check if queue has no pending messages."""
        return self._queue.empty()

    def qsize(self) -> int:
        """Number of messages waiting in queue."""
        return self._queue.qsize()
