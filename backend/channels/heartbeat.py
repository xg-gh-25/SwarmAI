"""Heartbeat manager for channel ack messages.

Updates the ack message in-place with human-like status progression.
The ack is posted immediately; phases update it at intervals to signal
"still working" without exposing any tool/process internals.

Key exports:
    HeartbeatManager — manages ack lifecycle (post, update, delete)
    estimate_complexity — classify request weight for ack template selection
    reply_language — the ack/heartbeat language for the triggering message
    pick_ack / stop_reply — ack texts in that language
"""
from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Callable type aliases for adapter operations
# ---------------------------------------------------------------------------

# These are passed at construction so HeartbeatManager doesn't depend on
# any specific adapter interface. The gateway constructs lambdas/partials
# that match the adapter's actual methods.
from typing import Callable, Awaitable

PostFn = Callable[[str, str], Awaitable[Optional[str]]]  # (channel, text) -> ts
UpdateFn = Callable[[str, str, str], Awaitable[None]]    # (channel, ts, text) -> None
DeleteFn = Callable[[str, str], Awaitable[None]]         # (channel, ts) -> None


# ---------------------------------------------------------------------------
# Ack templates (human-like, varied), per reply language
# ---------------------------------------------------------------------------

# Reply languages with templates. Any other language gets English.
DEFAULT_LANGUAGE = "en"

ACK_TEMPLATES = {
    "zh": {
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
    },
    "en": {
        "quick": [
            "Looking",
            "Let me check",
            "One sec",
            "On it",
        ],
        "medium": [
            "Good question, let me sort it out",
            "Need to check this, half a minute",
            "Got it, taking a look",
            "Let me think",
        ],
        "heavy": [
            "This is a big one, give me a minute or two",
            "Quite a lot here, I'll pull it together",
            "Let me look closely, one moment",
            "A bit involved, checking a few places",
        ],
    },
}

# Heartbeat progression (seconds, messages), per reply language
HEARTBEAT_PHASES = {
    "zh": [
        (15, ["还在查，快好了", "还在看", "快了"]),
        (30, ["内容比较多，再等一下", "还在整理", "稍等下，马上好"]),
        (60, ["确实复杂，还在整理", "比预想的多一些", "再给我一会儿"]),
        (90, ["快了快了，马上出结果", "最后整理一下", "差不多了"]),
    ],
    "en": [
        (15, ["Still checking, almost there", "Still looking", "Nearly done"]),
        (30, ["Quite a lot here, a bit longer", "Still pulling it together", "Hang on, nearly ready"]),
        (60, ["It's involved, still working on it", "More than I expected", "Give me a little longer"]),
        (90, ["Almost there", "Final touches", "Nearly done"]),
    ],
}

# The ack's final text after the user stops the request
STOP_REPLIES = {
    "zh": "好的，停了。有什么新问题随时说。",
    "en": "OK, stopped. Ask me anything new whenever you like.",
}


def reply_language(text: str) -> str:
    """The ack/heartbeat language for a triggering message: "zh" or "en".

    Chinese when the message classifier (core.runtime_hooks) says Chinese, or
    when it stays silent on a message that is too short to classify but holds
    Han characters and no Kana or Hangul ("在吗"). Everything else is English,
    the only other language with templates.
    """
    try:
        from core.runtime_hooks import _classify_message_language, _script_counts
        lang = _classify_message_language(text or "")
        if lang == "zh":
            return "zh"
        if lang is None:
            counts = _script_counts(text or "")
            if counts["han"] and not counts["kana"] and not counts["hangul"]:
                return "zh"
    except Exception:
        logger.debug("reply_language: classifier failed", exc_info=True)
    return DEFAULT_LANGUAGE


def stop_reply(language: str) -> str:
    """The final ack text after the user stops the request."""
    return STOP_REPLIES.get(language, STOP_REPLIES[DEFAULT_LANGUAGE])


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


def pick_ack(complexity: str, language: str = DEFAULT_LANGUAGE) -> str:
    """Pick a random ack template for the given complexity and reply language
    (see reply_language); a language without templates gets English."""
    by_level = ACK_TEMPLATES.get(language, ACK_TEMPLATES[DEFAULT_LANGUAGE])
    templates = by_level.get(complexity, by_level["medium"])
    return random.choice(templates)


# ---------------------------------------------------------------------------
# HeartbeatManager
# ---------------------------------------------------------------------------

@dataclass
class HeartbeatManager:
    """Manages the ack message lifecycle: post → heartbeat updates → cleanup.

    Takes callable functions for post/update/delete so it doesn't depend
    on any specific adapter interface. The gateway passes adapter-specific
    lambdas at construction time.

    Usage:
        hb = HeartbeatManager(
            post_fn=lambda ch, txt: adapter.post_raw(ch, txt),
            update_fn=lambda ch, ts, txt: adapter.update_raw(ch, ts, txt),
            delete_fn=lambda ch, ts: adapter.delete_raw(ch, ts),
            channel="C123",
            language=reply_language(user_text),
        )
        await hb.post_ack(pick_ack(estimate_complexity(user_text), hb.language))
        task = asyncio.create_task(hb.run())
        ...
        task.cancel()
        await hb.delete_ack()
    """
    post_fn: PostFn
    update_fn: UpdateFn
    delete_fn: DeleteFn
    channel: str
    thread_ts: Optional[str] = None
    language: str = DEFAULT_LANGUAGE  # reply language for heartbeat text
    ack_ts: Optional[str] = None
    _start_time: float = field(default_factory=time.monotonic)
    _phase_idx: int = 0

    async def post_ack(self, text: str) -> Optional[str]:
        """Post the initial ack message. Returns its timestamp."""
        try:
            self.ack_ts = await self.post_fn(self.channel, text)
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

            phases = HEARTBEAT_PHASES.get(self.language, HEARTBEAT_PHASES[DEFAULT_LANGUAGE])
            if self._phase_idx >= len(phases):
                # All phases exhausted — just keep running silently
                await asyncio.sleep(30)
                continue

            threshold, messages = phases[self._phase_idx]
            # Add ±3s jitter to feel natural
            jittered = threshold + random.uniform(-3, 3)
            if elapsed >= jittered:
                text = random.choice(messages)
                try:
                    await self.update_fn(self.channel, self.ack_ts, text)
                except Exception:
                    logger.debug("Failed to update heartbeat", exc_info=True)
                self._phase_idx += 1

    async def delete_ack(self) -> None:
        """Delete the ack message (called when final response is posted)."""
        if not self.ack_ts:
            return
        try:
            await self.delete_fn(self.channel, self.ack_ts)
        except Exception:
            logger.debug("Failed to delete ack message", exc_info=True)
        self.ack_ts = None

    async def update_final(self, text: str) -> None:
        """Update ack to a final status (e.g., after /stop)."""
        if not self.ack_ts:
            return
        try:
            await self.update_fn(self.channel, self.ack_ts, text)
        except Exception:
            logger.debug("Failed to update ack to final", exc_info=True)
