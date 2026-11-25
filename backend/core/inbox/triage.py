"""Inbox triage: deterministic rules first, then one model call per batch whose only tool
is the answer tool.

Design: docs/design/2026-11-20-inbox.md §6.1-§6.2. The runner calls ``rule_lane`` for every
pulled message, builds a ``Candidate`` for each one it keeps, and hands the batch to
``triage``.

The model never decides alone. Its only tool is the answer tool ``jobs.bedrock.structured``
carries, and that call is only parsed. Afterwards these checks decide:

- every candidate id comes back exactly once (an id outside the batch is already refused
  by the schema's ``enum``), else the whole batch is rejected (``TriageRejected``), so one
  message cannot add or drop an item;
- an item the rules put in TREND (a feed) or SKIP (a bot, no-reply sender, calendar invite)
  is never put in REPLY: a feed cannot be replied to, and an automated message's own text
  must not be able to promote it into 等你回复 (``_CEILING``);
- no item ends below its ``floor``: a DM, an @mention or mail with the person in To may be
  lowered to AWARE, never to TREND or SKIP.

Within those bounds the lane is the model's judgment: an item without a floor (bulk mail, a
watched channel, an unflagged thread message) can be moved anywhere up to its ceiling,
including by an injected text in another item of the same batch that the model obeys. The
fence below is the only defence there; the impact is bounded (nothing is sent, an item is
shown in the wrong lane or counted as skipped).

Message text, titles and author names go in the user turn inside a nonce fence
(``core.inbox.prompts.ExternalFence``), never in the system prompt. A batch larger than
``MAX_BATCH`` is split into chunks, one call each, so a first pull after a long gap cannot
overflow the model's context or its output limit on every retry.

Key public symbols: ``RuleVerdict``, ``TriageContext``, ``rule_lane``, ``triage``,
``TriageRejected``.
"""
from __future__ import annotations

import functools
from collections import Counter
from dataclasses import dataclass, field
from typing import Optional

from core import executors
from core.inbox.models import Candidate, Identity, Lane, Message, SourceCategory, TriageResult
from core.inbox.prompts import TRIAGE_SYSTEM, ExternalFence, clip, with_profile
from jobs import bedrock

FEATURE = "inbox.triage"           # the usage ledger's name for these calls
MODEL_LANES = (Lane.REPLY, Lane.AWARE, Lane.TREND, Lane.SKIP)  # PROPOSED is only for submitted drafts
TEXT_LIMIT = 4000                  # characters of one candidate's text in the prompt
CONTEXT_MESSAGES = 3               # earlier messages of the same thread shown with a candidate
CONTEXT_LIMIT = 1000               # characters of each of those
MAX_BATCH = 40                     # candidates per model call (~7,000 characters each at most)
# How low a lane is; a floor is a minimum on this order.
_RANK = {Lane.SKIP: 0, Lane.TREND: 1, Lane.AWARE: 2, Lane.REPLY: 3, Lane.PROPOSED: 3}
# The highest lane the model may give an item by its rule lane (feeds and automated senders
# never reach REPLY); a rule lane not listed has no ceiling.
_CEILING = {Lane.TREND: Lane.TREND, Lane.SKIP: Lane.AWARE}


@dataclass(frozen=True)
class RuleVerdict:
    lane: Lane                 # the lane the rules suggest
    floor: Optional[Lane]      # the lowest lane the model may move it to; None = no floor


@dataclass(frozen=True)
class TriageContext:
    user_profile: str = ""                         # the person's role and, if present, key people
    examples: tuple[str, ...] = field(default=())  # recent re-lane corrections, one line each


def rule_lane(msg: Message, me: Identity, category: SourceCategory,
              quiet: frozenset[str] = frozenset()) -> Optional[RuleVerdict]:
    """Return None for the person's own message (it only closes threads), else the rule
    verdict. Direct messages, mentions and mail with the person in To get lane REPLY with
    floor AWARE: the model may lower them to AWARE, never to SKIP.

    Order matters when a message carries several flags: a feed is always TREND; a bot,
    no-reply sender or calendar invite is SKIP even when it addresses the person (it expects
    no answer); a direct message or mention is REPLY even in list mail or a watched channel
    (an explicit ask is never dropped below AWARE); bulk mail, watched channels and any other
    conversation message are AWARE with no floor, for the model to judge. A sender in
    *quiet* (the person said this sender never waits on a reply) is AWARE with no floor; the
    runner keeps it out of REPLY whatever the model answers (``runner.clamp_lane``)."""
    if me.is_me(msg.author_id):
        return None
    if category == SourceCategory.FEED:
        return RuleVerdict(Lane.TREND, None)
    if msg.is_bot or msg.is_invite:
        return RuleVerdict(Lane.SKIP, None)
    if (msg.author_id or "").strip().lower() in quiet:
        return RuleVerdict(Lane.AWARE, None)
    if msg.is_direct or msg.mentions_me:
        return RuleVerdict(Lane.REPLY, Lane.AWARE)
    return RuleVerdict(Lane.AWARE, None)  # bulk, watched, or nothing that asks the person


async def triage(candidates: list[Candidate], context: TriageContext) -> list[TriageResult]:
    """One model call for the batch. Every candidate id comes back exactly once, or the
    whole batch is rejected (raise ``TriageRejected``) and retried at the next pull.

    A batch of more than ``MAX_BATCH`` candidates is laned in chunks of that size, one call
    each, one after another; a rejected chunk rejects the whole batch. Results come back in
    candidate order. A Bedrock error (credentials, throttling) propagates unchanged; an
    answer that is cut, filtered or off-schema is a rejection."""
    ids = [c.id for c in candidates]
    if len(set(ids)) != len(ids):
        raise ValueError("candidate ids must be unique within a batch")
    out: list[TriageResult] = []
    for start in range(0, len(candidates), MAX_BATCH):
        out += await _triage_chunk(candidates[start:start + MAX_BATCH], context)
    return out


async def _triage_chunk(candidates: list[Candidate], context: TriageContext) -> list[TriageResult]:
    ids = [c.id for c in candidates]
    call = functools.partial(
        _ask, _prompt(candidates, context),
        schema=_schema(ids),
        max_tokens=512 + 200 * len(candidates),
        system=with_profile(TRIAGE_SYSTEM, context.user_profile),
    )
    try:
        answer = await executors.run_in("llm", call)  # off the event loop and its default pool
    except bedrock.StructuredOutputError as exc:
        raise TriageRejected(f"the model answer is not usable ({exc.kind})") from exc
    entries = answer.get("items") or []
    counts = Counter(e["id"] for e in entries)
    missing = [i for i in ids if counts[i] == 0]
    extra = sorted(i for i in counts if i not in set(ids))
    repeated = sorted(i for i, n in counts.items() if n > 1)
    if missing or extra or repeated:
        raise TriageRejected(f"ids not answered exactly once: missing={missing} "
                             f"extra={extra} repeated={repeated}")
    by_id = {e["id"]: e for e in entries}
    return [_result(c, by_id[c.id]) for c in candidates]


class TriageRejected(Exception):
    """The model answer did not cover every candidate id exactly once."""


def _ask(prompt: str, *, schema: dict, max_tokens: int, system: str) -> dict:
    """The one model call (blocking: run it on the 'llm' pool). Its only tool is the
    answer tool ``structured`` carries; the answer is parsed, never executed."""
    return bedrock.structured(prompt, schema=schema, model_id=bedrock.get_model_id(),
                              max_tokens=max_tokens, system=system,
                              description="Record the lane, priority, reason and action of every item.",
                              feature=FEATURE)


def _schema(ids: list[str]) -> dict:
    return {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "description": "One entry for every item id, each id exactly once.",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "enum": list(ids)},
                        "lane": {"type": "string", "enum": [l.value for l in MODEL_LANES]},
                        "priority": {"type": "integer", "enum": [0, 1, 2, 3]},
                        "reason": {"type": "string",
                                   "description": "One plain sentence in the person's language: "
                                                  "what they are asked to do."},
                        "needs_action": {"type": "boolean",
                                         "description": "True when the person must do something "
                                                        "other than write a reply (approve, renew, "
                                                        "fill in, fix, decide)."},
                        "due": {"type": "string",
                                "description": "The date the action is due by as YYYY-MM-DD when "
                                               "the message names one, else an empty string."},
                    },
                    "required": ["id", "lane", "priority", "reason"],
                },
            },
        },
        "required": ["items"],
    }


def _result(c: Candidate, entry: dict) -> TriageResult:
    lane = Lane(entry["lane"])
    ceiling = _CEILING.get(c.rule_lane)
    if ceiling is not None and _RANK[lane] > _RANK[ceiling]:
        lane = ceiling                  # feeds and automated senders never reach REPLY
    if c.floor is not None and _RANK[lane] < _RANK[c.floor]:
        lane = c.floor                  # never below the rules' floor
    reason = " ".join(str(entry.get("reason") or "").split()) or " ".join(c.message.title.split())
    due = str(entry.get("due") or "").strip()[:10]
    return TriageResult(id=c.id, lane=lane, priority=int(entry["priority"]), reason=reason,
                        reply_expected=lane is Lane.REPLY,
                        needs_action=bool(entry.get("needs_action")),  # the runner keeps it only off REPLY
                        due=due)


def _signals(m: Message) -> str:
    flags = [("direct message or To the person", m.is_direct), ("mentions the person", m.mentions_me),
             ("automated sender", m.is_bot), ("bulk or list mail", m.is_bulk),
             ("calendar invite", m.is_invite), ("watched channel or repository", m.watched)]
    return ", ".join(name for name, on in flags if on) or "none"


def _prompt(candidates: list[Candidate], context: TriageContext) -> str:
    texts: list[str] = list(context.examples)
    for c in candidates:
        m = c.message
        texts += [m.text, m.title, m.author_name, m.author_id, m.url, c.source_kind]
        for t in c.thread_context[-CONTEXT_MESSAGES:]:
            texts += [t.text, t.author_name, t.author_id]
    fence = ExternalFence.for_texts(texts)
    blocks = []
    for c in candidates:
        m = c.message
        floor = f" (floor: {c.floor.value}, never lower)" if c.floor is not None else ""
        lines = [f"<item-{fence.nonce} id=\"{c.id}\">",
                 f"channel: {c.source_kind}",
                 f"suggested lane: {c.rule_lane.value}{floor}",
                 f"signals: {_signals(m)}",
                 f"from: {m.author_name or m.author_id}",
                 f"received: {m.received_at.isoformat()}"]
        if m.title:
            lines.append(f"title: {clip(m.title, 300)}")
        earlier = c.thread_context[-CONTEXT_MESSAGES:]
        if earlier:
            lines.append("earlier in the thread (oldest first):")
            lines += [f"- {t.author_name or t.author_id}: {clip(t.text, CONTEXT_LIMIT)}" for t in earlier]
        lines += ["message:", clip(m.text, TEXT_LIMIT), f"</item-{fence.nonce}>"]
        blocks.append("\n".join(lines))
    parts = [
        f"Lane each of the {len(candidates)} items below, then answer with the answer tool: one "
        "entry per item, using each of these ids exactly once: " + ", ".join(c.id for c in candidates) + ".",
        fence.preamble(),
        f"Each item is the region from <item-{fence.nonce} id=\"…\"> to </item-{fence.nonce}>; "
        "its id is the one in that opening tag.",
    ]
    if context.examples:
        parts.append("The person's recent corrections of lanes, as examples of what they want:\n"
                     + fence.wrap("\n".join(f"- {e}" for e in context.examples)))
    parts.append("Items:\n" + fence.wrap("\n\n".join(blocks)))
    return "\n\n".join(parts)
