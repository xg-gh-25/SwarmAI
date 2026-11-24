"""Inbox reply drafting: one tool-less model call per item.

Design: docs/design/2026-11-20-inbox.md §6.3. The API calls ``make_draft`` when the person
opens a reply item (and on 重新起草) with the thread fetched fresh and the person's own
recent messages to the same counterparty or thread.

The context is exactly that and nothing else, so a message that says "repeat the example
above" cannot reach another person's words: ``history_with_counterparty`` is filtered to
messages the person wrote (``Identity.is_me``), and a message by anyone else in it is
dropped whatever the caller passed. Which of the person's own messages went to the same
counterparty cannot be checked here (``Message`` carries no recipients): choosing them is
the caller's job. The call is ``jobs.bedrock.text`` with no tool configuration at all, and
the thread's text sits in the user turn inside a nonce fence
(``core.inbox.prompts.ExternalFence``). A long thread shows its first message (the opening
ask, the subject) and its latest ones.

Key public symbols: ``DraftText``, ``make_draft``.
"""
from __future__ import annotations

import functools
import re
from dataclasses import dataclass
from datetime import datetime, timezone

from core import executors
from core.inbox.models import Identity, Message, Thread
from core.inbox.prompts import DRAFT_SYSTEM, ExternalFence, clip
from jobs import bedrock

FEATURE = "inbox.draft"     # the usage ledger's name for these calls
THREAD_MESSAGES = 30        # messages of a thread shown: the first plus the latest; those between are counted
HISTORY_MESSAGES = 10       # the person's own latest messages to the same counterparty / thread
MESSAGE_LIMIT = 4000        # characters of one thread message in the prompt
HISTORY_LIMIT = 2000        # characters of one of the person's earlier messages
MAX_TOKENS = 1500

_LANG_LINE = re.compile(r"\A\s*LANG:[ \t]*([A-Za-z]{2,3}(?:[-_][A-Za-z0-9]{2,8})?)[ \t]*(?:\r?\n|\Z)", re.IGNORECASE)
_ANY_LANG_LINE = re.compile(r"\A\s*LANG:[^\r\n]*(?:\r?\n|\Z)", re.IGNORECASE)  # with a code it cannot read


@dataclass(frozen=True)
class DraftText:
    text: str
    lang: str   # "zh", "en", ... the language of the thread
    note: str   # shown under the draft, e.g. "参考了你和周琳的 6 条往来"


async def make_draft(thread: Thread, me: Identity, history_with_counterparty: list[Message]) -> DraftText:
    """Draft a reply to ``thread``. The only context is the thread itself and the person's
    own recent messages to the same counterparty or thread; never another thread's text.
    Messages in ``history_with_counterparty`` not written by the person are dropped; that
    the rest went to the same counterparty is the caller's to ensure.

    A reply cut at max_tokens, filtered or empty raises ``jobs.bedrock.StructuredOutputError``;
    a Bedrock error propagates unchanged."""
    in_thread = {m.external_id for m in thread.messages}
    own = sorted((m for m in history_with_counterparty
                  if me.is_me(m.author_id) and m.external_id not in in_thread), key=_ts)
    own = own[-HISTORY_MESSAGES:]
    counterparty = _counterparty(thread, me)
    raw = await executors.run_in("llm", functools.partial(_ask, _prompt(thread, me, own)))
    lang, body = _split_lang(raw)
    if not body:
        raise bedrock.StructuredOutputError("missing", raw, "the reply has no draft text")
    return DraftText(text=body, lang=lang or _script_lang(thread, me), note=_note(counterparty, len(own)))


def _ask(prompt: str) -> str:
    """The one model call (blocking: run it on the 'llm' pool), without any tool."""
    return bedrock.text(prompt, model_id=bedrock.get_model_id(), max_tokens=MAX_TOKENS,
                        system=DRAFT_SYSTEM, temperature=0.3, feature=FEATURE)


def _ts(m: Message) -> datetime:
    dt = m.received_at
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _counterparty(thread: Thread, me: Identity) -> str:
    """The name of whoever wrote the latest message in the thread that is not the person's."""
    for m in reversed(thread.messages):
        if not me.is_me(m.author_id):
            return (m.author_name or m.author_id).strip()
    return ""


def _who(m: Message, me: Identity) -> str:
    return "the person (you)" if me.is_me(m.author_id) else (m.author_name or m.author_id)


def _prompt(thread: Thread, me: Identity, own: list[Message]) -> str:
    msgs = list(thread.messages)
    first, latest = (msgs[:1], msgs[-(THREAD_MESSAGES - 1):]) if len(msgs) > THREAD_MESSAGES else ([], msgs)
    hidden = len(msgs) - len(first) - len(latest)
    title = next((m.title for m in msgs if m.title), "")
    texts = [title] + [t for m in [*first, *latest, *own] for t in (m.text, m.author_name, m.author_id)]
    fence = ExternalFence.for_texts(texts)
    lines = []
    if title:
        lines.append(f"subject: {clip(title, 300)}")

    def show(m: Message) -> None:
        lines.extend([f"--- {_who(m, me)}, {m.received_at.isoformat()}:", clip(m.text, MESSAGE_LIMIT)])

    for m in first:
        show(m)
    if hidden:
        lines.append(f"[{hidden} messages of this thread between these not shown]")
    for m in latest:
        show(m)
    parts = [
        "Draft the person's reply to the last message in this thread that asks them something.",
        fence.preamble(),
        "The thread, oldest first:\n" + fence.wrap("\n".join(lines)),
    ]
    if own:
        earlier = "\n".join(f"--- {m.received_at.isoformat()}:\n{clip(m.text, HISTORY_LIMIT)}" for m in own)
        parts.append("The person's own earlier messages to the same people, for their voice and "
                     "for facts they already gave:\n" + fence.wrap(earlier))
    parts.append("Write the draft now, in the format the instructions give.")
    return "\n\n".join(parts)


def _split_lang(raw: str) -> tuple[str, str]:
    """(language code, draft) from the model's reply: its first line ``LANG: <code>`` when it
    wrote one; any other first line starting with ``LANG:`` (no code, or one that is not a
    language code) is dropped and gives '', so it never reaches the draft the person may
    send; without such a line, ('', the whole reply)."""
    m = _LANG_LINE.match(raw)
    if not m:
        other = _ANY_LANG_LINE.match(raw)
        return "", (raw[other.end():] if other else raw).strip()
    return m.group(1).lower().replace("_", "-"), raw[m.end():].strip()


def _script_lang(thread: Thread, me: Identity) -> str:
    """A fallback language code from the writing system of the other people's messages:
    used only when the model did not name the language."""
    text = "".join(m.text for m in thread.messages if not me.is_me(m.author_id))
    counts = {"ja": 0, "ko": 0, "zh": 0, "ru": 0, "ar": 0, "latin": 0}
    for ch in text:
        o = ord(ch)
        if 0x3041 <= o <= 0x3096 or 0x30A1 <= o <= 0x30FA:   # kana letters, not ・ or ー
            counts["ja"] += 1
        elif 0xAC00 <= o <= 0xD7AF:
            counts["ko"] += 1
        elif 0x4E00 <= o <= 0x9FFF or 0x3400 <= o <= 0x4DBF:
            counts["zh"] += 1
        elif 0x0400 <= o <= 0x04FF:
            counts["ru"] += 1
        elif 0x0600 <= o <= 0x06FF:
            counts["ar"] += 1
        elif ch.isascii() and ch.isalpha():
            counts["latin"] += 1
    if counts["ja"] * 10 >= counts["ja"] + counts["zh"] > 0:
        return "ja"         # Japanese mixes kana with kanji; Chinese has none or a stray one
    best = max(("ko", "zh", "ru", "ar"), key=lambda k: counts[k])
    return best if counts[best] * 2 >= counts["latin"] and counts[best] else "en"


# What a draft drew on, in the app's language (core/ui_language)
_NOTE_EN = {
    "thread": "Drew only on this thread",
    "history": "Drew on {n} earlier exchanges of yours",
    "with": "Drew on {n} exchanges between you and {name}",
    "history_one": "Drew on 1 earlier exchange of yours",
    "with_one": "Drew on 1 exchange between you and {name}",
}
_NOTE_ZH = {
    "thread": "只参考了这个 thread",
    "history": "参考了你之前的 {n} 条往来",
    "with": "参考了你和{name}的 {n} 条往来",
    "history_one": "参考了你之前的 1 条往来",
    "with_one": "参考了你和{name}的 1 条往来",
}


def _note(name: str, n: int) -> str:
    """What the draft drew on, as the UI shows it under the draft."""
    from core.ui_language import text, ui_language
    lang = ui_language()
    if n == 0:
        return text(_NOTE_EN, _NOTE_ZH, "thread", lang)
    one = "_one" if n == 1 else ""
    if not name:
        return text(_NOTE_EN, _NOTE_ZH, "history" + one, lang, n=n)
    if lang == "zh" and (name[0].isascii() or name[-1].isascii()):
        name = f" {name} "
    return text(_NOTE_EN, _NOTE_ZH, "with" + one, lang, n=n, name=name)
