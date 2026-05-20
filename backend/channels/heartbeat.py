"""Heartbeat manager for channel ack messages.

Updates the ack message in-place with human-like status progression.
The ack is posted immediately; phases update it at intervals to signal
"still working" without exposing any tool/process internals.

Key exports:
    HeartbeatManager — manages ack lifecycle (post, update, delete)
    estimate_complexity — classify request weight for ack template selection
"""
from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass, field
from typing import Optional, Protocol

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Adapter protocol (duck-typed — works with any adapter that has these methods)
# ---------------------------------------------------------------------------

class MessageSender(Protocol):
    """Minimal interface for sending/updating/deleting messages."""

    async def send_message_raw(
        self, channel: str, text: str, thread_ts: Optional[str] = None,
    ) -> Optional[str]:
        """Post a message, return its timestamp/ID."""
        ...

    async def update_message_raw(
        self, channel: str, ts: str, text: str,
    ) -> None:
        """Update an existing message in-place."""
        ...

    async def delete_message_raw(
        self, channel: str, ts: str,
    ) -> None:
        """Delete a message."""
        ...


# ---------------------------------------------------------------------------
# Ack templates (human-like, varied)
# ---------------------------------------------------------------------------

ACK_TEMPLATES = {
    "quick": [
        "看一下",
        "我查查",
        "让我翻一下",
        "稍等",
    ],
    "medium": [
        "好问题，让我理一下",
        "这个我得查下，半分钟",
        "收到，看看",
        "让我想想",
    ],
    "heavy": [
        "这个比较多，给我一两分钟",
        "内容不少，我整理下再回你",
        "让我仔细看看，稍等",
        "有点复杂，需要查几个地方",
    ],
}

# Heartbeat progression (seconds, message)
HEARTBEAT_PHASES = [
    (15, ["还在查，快好了", "还在看", "快了"]),
    (30, ["内容比较多，再等一下", "还在整理", "稍等下，马上好"]),
    (60, ["确实复杂，还在整理", "比预想的多一些", "再给我一会儿"]),
    (90, ["快了快了，马上出结果", "最后整理一下", "差不多了"]),
]

# Heavy keywords that signal a complex request
HEAVY_KEYWORDS = [
    "dive deep", "深入", "分析", "research", "全面",
    "详细", "investigate", "comprehensive", "所有",
]


# ---------------------------------------------------------------------------
# Complexity estimation
# ---------------------------------------------------------------------------

def estimate_complexity(text: str) -> str:
    """Estimate request complexity: 'quick', 'medium', or 'heavy'.

    Based on message content weight and keywords.
    CJK-aware: counts CJK chars as 2 (they carry more information density).
    """
    lower = text.lower()

    # Heavy: explicit research keywords
    if any(kw in lower for kw in HEAVY_KEYWORDS):
        return "heavy"

    # Content weight: CJK chars count as 2
    weight = sum(2 if ord(c) > 0x2E80 else 1 for c in text)

    # Quick: short question
    if weight < 50 and ("?" in text or "？" in text):
        return "quick"
    if weight < 30:
        return "quick"

    return "medium"


def pick_ack(complexity: str) -> str:
    """Pick a random ack template for the given complexity."""
    templates = ACK_TEMPLATES.get(complexity, ACK_TEMPLATES["medium"])
    return random.choice(templates)


# ---------------------------------------------------------------------------
# HeartbeatManager
# ---------------------------------------------------------------------------

@dataclass
class HeartbeatManager:
    """Manages the ack message lifecycle: post → heartbeat updates → cleanup.

    Usage:
        hb = HeartbeatManager(sender=adapter, channel="C123", thread_ts="...")
        ack_ts = await hb.post_ack("看一下")
        task = asyncio.create_task(hb.run())
        ...
        task.cancel()
        await hb.delete_ack()  # or hb.update_final("好的，停了。")
    """
    sender: MessageSender
    channel: str
    thread_ts: Optional[str] = None
    ack_ts: Optional[str] = None
    _start_time: float = field(default_factory=time.monotonic)
    _phase_idx: int = 0

    async def post_ack(self, text: str) -> Optional[str]:
        """Post the initial ack message. Returns its timestamp."""
        try:
            self.ack_ts = await self.sender.send_message_raw(
                self.channel, text, self.thread_ts,
            )
            self._start_time = time.monotonic()
            return self.ack_ts
        except Exception:
            logger.warning("Failed to post ack message", exc_info=True)
            return None

    async def run(self) -> None:
        """Update ack message at intervals. Runs until cancelled."""
        if not self.ack_ts:
            return

        while True:
            await asyncio.sleep(5)  # check every 5s
            elapsed = time.monotonic() - self._start_time

            if self._phase_idx >= len(HEARTBEAT_PHASES):
                # All phases exhausted — just keep running silently
                await asyncio.sleep(30)
                continue

            threshold, messages = HEARTBEAT_PHASES[self._phase_idx]
            # Add ±3s jitter to feel natural
            jittered = threshold + random.uniform(-3, 3)
            if elapsed >= jittered:
                text = random.choice(messages)
                try:
                    await self.sender.update_message_raw(
                        self.channel, self.ack_ts, text,
                    )
                except Exception:
                    logger.debug("Failed to update heartbeat", exc_info=True)
                self._phase_idx += 1

    async def delete_ack(self) -> None:
        """Delete the ack message (called when final response is posted)."""
        if not self.ack_ts:
            return
        try:
            await self.sender.delete_message_raw(self.channel, self.ack_ts)
        except Exception:
            logger.debug("Failed to delete ack message", exc_info=True)
        self.ack_ts = None

    async def update_final(self, text: str) -> None:
        """Update ack to a final status (e.g., after /stop)."""
        if not self.ack_ts:
            return
        try:
            await self.sender.update_message_raw(
                self.channel, self.ack_ts, text,
            )
        except Exception:
            logger.debug("Failed to update ack to final", exc_info=True)
