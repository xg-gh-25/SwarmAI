"""Relevance gate, prompt dedupe and size budget for first-message recall.

Keyword recall ranks; it never says "nothing relevant". Every FTS leg returns
its top hits for any query that shares one term with the corpus, so before
this module each leg's best hits were injected for every message -- a bare
"continue" injected 3.4K tokens, the median injection was ~12K characters, and
the MEMORY leg re-injected sections already in the prompt in full. This module
is applied between ``recall_all`` and rendering:

1. Relevance: a hit must contain about a third of the distinct query terms,
   and never fewer than two. A one-term query recalls only when that term is
   code-like (``session_router``, ``pre-tool-use``, ``SessionUnit``) -- a lone
   common word ("commit", "review") matches half the corpus. Code symbols are
   kept only when they match a code-like term. Term coverage is scale-free,
   unlike raw BM25 scores, which are not comparable across the legs.
2. Not already in context: a hit whose text is already in the system prompt
   (live MEMORY, the injected DailyActivity days, any context file) is dropped
   -- recall exists to bring in what the prompt does not carry.
3. Budget: per-domain character caps, so one leg cannot crowd out the rest.
4. Rank and age (design 2026-10-26 D1, measured on replayed traffic, where term
   coverage did NOT separate useful from noise hits): the library leg keeps its
   first ``LIBRARY_TOP`` relevant hits plus up to ``ARCHIVE_SLOTS`` MEMORY /
   EVOLUTION archive hits with their own budget, and never fetched feeds
   (``NO_INJECT_PREFIXES``); a past session or a DailyActivity day older than
   ``SESSION_FRESH_DAYS`` must match ``SESSION_STALE_MIN_TERMS`` query terms.

Nothing is removed from any corpus; this only decides which hits of this
query are worth the model's attention.

Public:
- ``extract_query_keywords(message)`` -- the search terms of a message (chat and pipeline)
- ``take_whole(units, cap, size)`` / ``not_shown_note(items, ...)`` / ``unit_title(text)``
  / ``split_sections(text)``
  -- the one rule of every read path: a unit is shown whole or named, never cut
- ``query_terms(query)``
- ``filter_recall(result, prompt_text="", followup=False, caps=None, slots=None)`` -> BucketedRecall
- ``in_prompt_text(text, prompt_norm)``
- ``DOMAIN_CHAR_CAPS``, ``FOLLOWUP_CHAR_CAPS``, ``FOLLOWUP_SLOTS``, ``LIBRARY_TOP``, ``ARCHIVE_SLOTS``, ``NO_INJECT_PREFIXES``
"""

from __future__ import annotations

import logging
import re
from typing import Any, Callable

logger = logging.getLogger(__name__)

# Max characters each domain may contribute to the injected block. A hit is shown
# whole or named (``take_whole``), so the library cap fits a p90 Knowledge Library
# section (3.9K characters; p50 0.8K, p99 11K, measured over Knowledge/Library) --
# a longer one is listed by its location, never cut.
DOMAIN_CHAR_CAPS: dict[str, int] = {
    "context_files": 1500,
    "ddd": 2000,
    "library": 4500,
    "session": 1800,
    "codeintel": 500,
    "memory_archive": 1200,   # the library leg's archive slots (D4), own budget
}

# Follow-up recall (a later message on a new topic) gets a smaller block: the
# whole session shares one budget (session_router._RECALL_SESSION_CHARS), and
# the first hit of a leg carries most of its value (library rank 1 useful 62%,
# rank 2 37%, rank 3+ under 30%; past sessions 37% overall). Halving each
# follow-up roughly doubles how many a session gets (design 2026-10-26 §9).
FOLLOWUP_CHAR_CAPS: dict[str, int] = {
    "context_files": 700,
    "ddd": 1000,
    "library": 2400,
    "session": 900,
    "codeintel": 300,
    "memory_archive": 600,
}
FOLLOWUP_SLOTS: dict[str, int] = {"library": 2, "memory_archive": 1, "session": 1}

# Precision rules measured on replayed real traffic (design
# docs/design/2026-10-26-recall-precision-and-coverage.md §2.3, D1). The term
# coverage above does NOT separate useful from noise hits (both ~0.4-0.5); rank
# and age do. Library: rank 1 was useful 62% of the time, rank 5+ 15%.
LIBRARY_TOP = 3
# MEMORY / EVOLUTION archives are the only recall route to old memory, so they
# get their own slots instead of competing for LIBRARY_TOP (D4).
ARCHIVE_PREFIX = ".context/Archives/"
ARCHIVE_SLOTS = 2
# Fetched feeds and the briefs built from them: 0 useful in the sample. Still in
# the index for explicit Library search; just never auto-injected.
NO_INJECT_PREFIXES = ("Signals/", "Archives/Signals/", "DailyBriefs/")
# Past sessions: <=1 day useful 60%, <=7 days 44%, older 17% (noise 52%). An
# older session must match at least SESSION_STALE_MIN_TERMS query terms.
SESSION_FRESH_DAYS = 7
SESSION_STALE_MIN_TERMS = 3
# DailyActivity follows the same age rule (design 2026-10-26 §10): useful 83%
# within 2 days, 42% at 3-7 days, 13% at 8-30 days, 9% older. Its date is the
# file name (``DailyActivity/YYYY-MM-DD.md``).
_DAILY_RE = re.compile(r"(?:^|/)DailyActivity/(\d{4}-\d{2}-\d{2})")


_STOP_WORDS: frozenset[str] = frozenset({
    "the", "this", "that", "with", "from", "what", "when", "where",
    "which", "about", "into", "than", "then", "them", "they", "been",
    "being", "have", "has", "had", "does", "did", "doing", "done",
    "will", "would", "could", "should", "shall", "might",
    "can", "may", "also", "just", "more", "most", "some", "any", "please",
    "help", "tell", "want", "need", "know", "like", "look", "show", "check",
    "all", "each", "every", "both", "few", "many", "much", "such",
    "very", "too", "quite", "rather", "only", "even", "still",
    "how", "why", "who", "you", "its", "our", "your", "their", "his", "her",
    "and", "but", "for", "nor", "not", "yet", "are", "was", "were",
    "let", "got", "get", "put", "see", "say", "said", "make", "made",
    # conversational turn-control words: alone they say nothing about a topic
    "continue", "okay", "yes", "now", "again", "here", "there", "thing",
})

# Chinese function words and conversational filler. A CJK run is cut at every
# occurrence (longest first), and the remaining pieces of 2+ characters are the
# content words. Without this a whole clause ("的看下我们现在的") became ONE
# search term whose every bigram had to match -- it matched nothing, while
# filler-only messages matched everything that shared a filler.
_CJK_STOP_WORDS: tuple[str, ...] = tuple(sorted({
    "我们", "你们", "他们", "咱们", "自己", "现在", "目前", "刚才", "之前", "以后",
    "一下", "看下", "看看", "看一下", "查下", "查一下", "帮我", "给我", "你给我",
    "什么", "怎么", "怎么样", "为什么", "为啥", "有没有", "是不是", "能不能",
    "要不要", "可以", "可能", "应该", "需要", "这个", "那个", "这些", "那些",
    "这样", "那样", "一个", "一些", "还有", "然后", "因为", "所以", "如果",
    "但是", "不过", "而且", "或者", "已经", "就是", "还是", "只是", "其实",
    "东西", "情况", "问题", "事情", "地方", "时候", "一点", "有点", "一样",
    "正好", "继续", "好好", "一次性", "到底", "真的", "直接", "马上", "确定",
    "上次", "感觉", "总是", "似的", "好像", "明白", "一直", "所有", "整个",
    "我", "你", "他", "她", "它", "的", "了", "吗", "呢", "吧", "啊", "呀",
    "嘛", "哦", "噢", "在", "是", "有", "和", "跟", "与", "把", "被", "给",
    "让", "去", "来", "下", "也", "都", "就", "还", "又", "再", "很", "太",
    "不", "没", "要", "会", "能", "好", "对", "说", "做", "搞", "帮", "请",
    "这", "那", "哪", "谁", "个", "们", "着", "过", "得", "地", "从", "向",
}, key=len, reverse=True))
_CJK_STOP_RE = re.compile("|".join(re.escape(w) for w in _CJK_STOP_WORDS))
_CJK_RUN_RE = re.compile(r"[\u3040-\u30ff\u4e00-\u9fff\u3400-\u4dbf\uac00-\ud7af]+")
# Longest CJK content piece kept as one term; longer pieces are split, because
# a term's bigrams are AND-ed and a long piece rarely recurs verbatim.
_CJK_MAX_TERM = 4


def _cjk_terms(text: str) -> list[str]:
    """Content words from the CJK runs of ``text``, in order, de-duplicated."""
    out: list[str] = []
    for run in _CJK_RUN_RE.findall(text):
        for piece in _CJK_STOP_RE.split(run):
            if len(piece) < 2:
                continue
            for i in range(0, len(piece), _CJK_MAX_TERM):
                part = piece[i:i + _CJK_MAX_TERM]
                if len(part) >= 2 and part not in out:
                    out.append(part)
    return out


def extract_query_keywords(message: str) -> str:
    """Extract searchable keywords from user message.  Pure NLP, no LLM.

    Returns a space-separated string of up to 18 terms suitable for
    FTS5+BM25 keyword recall (the vector leg was removed 2026-08-14).
    Returns empty string for messages too short to produce meaningful recall.
    """
    if not message or len(message.strip()) < 3:
        return ""

    text = message.strip()

    # Strip common conversational filler
    text = re.sub(
        r"^(hey|hi|hello|please|can you|could you|help me|help|swarm)\s+",
        "", text, flags=re.IGNORECASE,
    )

    if not text:
        return ""

    # Strip URLs and file paths before word extraction
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"(?:^|\s)[~/]\S+", " ", text)

    # Hyphenated compounds first — preserve "session-router", "pre-tool-use"
    # as single terms. ASCII-only classes throughout: Python's \w also matches
    # CJK, so "看下recall机制" used to come out as one "English word".
    compounds = re.findall(
        r"(?<![A-Za-z0-9_])([A-Za-z_][A-Za-z0-9_]+(?:-[A-Za-z][A-Za-z0-9_]+)+)", text)
    # Strip matched compounds from text to avoid double-counting
    text_stripped = text
    for c in compounds:
        text_stripped = text_stripped.replace(c, " ")

    # English words: substantive terms (>2 chars, not stop words), de-duplicated.
    words: list[str] = []
    for w in re.findall(r"(?<![A-Za-z0-9_])([A-Za-z_][A-Za-z0-9_]{2,})(?![A-Za-z0-9_])",
                        text_stripped):
        if w.lower() not in _STOP_WORDS and w not in words:
            words.append(w)

    # CJK + Kana + Hangul: content words only (function words cut out)
    cjk = _cjk_terms(text)

    combined = compounds[:3] + words[:10] + cjk[:8]
    return " ".join(combined) if combined else ""


def _today() -> str:
    """Today as YYYY-MM-DD (the replay tool substitutes the message's date)."""
    from datetime import date
    return date.today().isoformat()

_WS_RE = re.compile(r"\s+")


def query_terms(query: str) -> list[str]:
    out: list[str] = []
    for t in (query or "").lower().split():
        if t and t not in out:
            out.append(t)
    return out


_CODE_LIKE_RE = re.compile(r"[_.\-/]|[a-z][A-Z]")


def is_code_like(term: str) -> bool:
    return bool(_CODE_LIKE_RE.search(term))


def code_like_terms(terms: list[str]) -> list[str]:
    """The lower-cased code-like terms, minus project names: ``SwarmAI`` /
    ``SecDLC`` look like CamelCase identifiers but name a project (the DDD leg's
    job), and matched every symbol containing the name (design 2026-10-26)."""
    try:
        from core.recall_multi import list_project_names
        projects = {p.lower() for p in list_project_names()}
    except Exception as exc:  # noqa: BLE001 -- no workspace: nothing to exclude
        logger.debug("code_like_terms: project names unavailable: %s", exc)
        projects = set()
    return [t.lower() for t in terms if is_code_like(t) and t.lower() not in projects]


def _required(terms: list[str]) -> int:
    return max(2, round(len(terms) * 0.3)) if len(terms) > 1 else 1


def _coverage(text: str, terms: list[str]) -> int:
    low = text.lower()
    return sum(1 for t in terms if t in low)


def _norm(text: str) -> str:
    return _WS_RE.sub(" ", text).strip()


def _in_prompt(text: str, prompt_norm: str) -> bool:
    """True when ``text`` is (essentially) already present in the prompt.

    Two 80-character probes, from the start and the middle of the body, must
    both occur; the header line is skipped because recall formats its own.
    """
    if not prompt_norm:
        return False
    lines = text.strip().splitlines()
    body = "\n".join(lines[1:]) if len(lines) > 1 else text
    return in_prompt_text(body, prompt_norm)


def in_prompt_text(text: str, prompt_norm: str) -> bool:
    """True when ``text`` (whole, no header skipped) is essentially present in
    the whitespace-normalized ``prompt_norm``: two 80-character probes, from its
    start and its middle, must both occur. Text under 40 characters counts as
    absent (too short to tell)."""
    if not prompt_norm:
        return False
    body = _norm(text)
    if len(body) < 40:
        return False
    probes = [body[:80], body[len(body) // 2:len(body) // 2 + 80]]
    return all(p in prompt_norm for p in probes)


# ── Whole units (CDF first principle) ─────────────────────────────────────────
# Design 2026-10-26-swarm-cognitive-distillation-flow §5: injection and recall take or
# leave a unit WHOLE -- an entry, a section, a session message, a DailyActivity block,
# a lesson -- and every unit left out is named so the reader can fetch it. Nothing on
# the read path cuts a unit, and every reader uses these two functions, so there is
# one rule (run-knowledge §3.0) instead of one per caller.
TITLE_MAX = 200
NOTE_ITEMS = 5      # units a "not shown" line names one by one; the rest are counted
_BOLD_TITLE_RE = re.compile(r"^-\s*(?:\[\w+\]\s*)?\*\*(.+?)\*\*")
_HEADING_RE = re.compile(r"^#{1,6}\s+(.+)")


def take_whole(units: list, cap: int, size: Callable[[Any], int] = len) -> tuple[list, list]:
    """*units* in rank order, split into (shown, omitted): each unit is shown whole while
    it fits what is left of *cap*; the first one that does not fit ends the walk, and it
    and every unit after it are omitted. A lower-ranked unit never takes the place of a
    higher one, and no unit is ever shown in part."""
    shown: list = []
    omitted: list = []
    used = 0
    for u in units:
        n = size(u)
        if omitted or used + n > cap:
            omitted.append(u)
            continue
        shown.append(u)
        used += n
    return shown, omitted


def unit_title(text: str) -> str:
    """A unit's own title, whole: its bold entry title (``- [type] **title** ...``) or
    its heading line; "" when it has none or the title is longer than ``TITLE_MAX``.
    A locator for a unit that is not shown, never a cut-down copy of its text."""
    first = str(text or "").lstrip()
    m = _BOLD_TITLE_RE.match(first) or _HEADING_RE.match(first.split("\n", 1)[0])
    title = m.group(1).strip() if m else ""
    return title if len(title) <= TITLE_MAX else ""


def not_shown_note(items: list[tuple[str, str, int]], noun: str = "entry",
                   nouns: str = "entries", where: str = "",
                   max_items: "int | None" = None) -> str:
    """The one line naming units left out whole: how many, and for each its location,
    its own title when it has one and its length, so a person or an agent knows the
    unit exists and where to read it. *items*: ``(location, title, length)``, in rank
    order; with *max_items*, the first ones are listed and the rest counted. A
    location or title from untrusted text must not be passed here outside its fence."""
    n = len(items)
    what = noun if n == 1 else nouns
    head = (f"{where}: " if where else "") + (
        f"{n} more matching {what} not shown here, so that each one shown stays whole. "
        f"Read {'it' if n == 1 else 'them'} " + (f"in {where}:" if where else "at:"))
    lines = [head]
    listed = items if max_items is None else items[:max_items]
    for loc, title, length in listed:
        lines.append(f"- {loc}" + (f": {title}" if title else "") + f" ({length} characters)")
    if len(listed) < n:
        lines.append(f"- and {n - len(listed)} more")
    return "\n".join(lines)


def _pointer(domain: str, hit: dict) -> str:
    """Where the whole text of *hit* is: the locator of a hit that is not shown."""
    if domain == "session":
        return f"session {hit.get('session_id') or ''}".strip()
    if domain == "context_files":
        return "MEMORY.md" + (f"#{hit['section']}" if hit.get("section") else "")
    src = str(hit.get("doc") or hit.get("source") or "")
    sec = str(hit.get("section") or hit.get("heading") or "")
    return f"{src}#{sec}" if src and sec and not sec.startswith("(") else src


def split_sections(content: str) -> list[str]:
    """*content* split before each ``## `` heading line, never inside a fenced code
    block (a ``## `` line in a fence is code, and splitting there would show half a
    block). The text before the first heading is the first part."""
    parts: list[list[str]] = [[]]
    fence = ""
    for line in content.strip().split("\n"):
        mark = line.lstrip()[:3]
        if mark in ("```", "~~~"):
            fence = "" if fence == mark else (fence or mark)
        if not fence and line.startswith("## ") and any(x.strip() for x in parts[-1]):
            parts.append([])
        parts[-1].append(line)
    return [p for p in ("\n".join(x) for x in parts) if p.strip()]



def _hit_text(domain: str, hit: dict) -> str:
    if domain == "session":
        # every matched message, whole (``matched``), so a session is judged by what
        # it said even when a message is too long to show in the budget
        return hit.get("matched") or hit.get("text") or ""
    if domain == "codeintel":
        # The symbol name plus its FILE's stem: a term naming a file
        # (``session_router``) names that file's code, but a directory term
        # (``s_autonomous-pipeline``) named every symbol under the tree.
        stem = str(hit.get("file_path") or "").rsplit("/", 1)[-1].rsplit(".", 1)[0]
        return f"{hit.get('name') or ''} {stem}"
    return " ".join(str(hit.get(k) or "") for k in ("heading", "section", "content"))


def _keep(domain: str, text: str, terms: list[str], prompt_norm: str,
          code_terms: list[str]) -> bool:
    if domain == "codeintel":
        # ``text`` is the symbol name + file stem (``_hit_text``): matching the
        # whole path (``s_autonomous-pipeline`` -> every symbol in that skill's
        # scripts) returned _Parser / main / GATES; 75% of code hits were noise.
        return _coverage(text, code_terms) >= 1
    if _coverage(text, terms) < _required(terms):
        return False
    return not _in_prompt(text, prompt_norm)


def filter_recall(result: Any, prompt_text: str = "", followup: bool = False,
                  caps: "dict[str, int] | None" = None,
                  slots: "dict[str, int] | None" = None) -> Any:
    """Return a copy of ``result`` holding only relevant, new, budgeted hits.

    ``followup``: a later message's recall; uses ``FOLLOWUP_CHAR_CAPS`` and
    ``FOLLOWUP_SLOTS`` instead of the first-message sizes. ``caps`` / ``slots``: a
    caller with its own budget (a pipeline run's knowledge.md) passes them; the
    relevance rules are the same."""
    if caps is None:
        caps = FOLLOWUP_CHAR_CAPS if followup else DOMAIN_CHAR_CAPS
    if slots is None:
        slots = FOLLOWUP_SLOTS if followup else {}
    from core.recall_multi import BucketedRecall

    raw_terms = (result.query or "").split()
    terms = query_terms(result.query)
    code_terms = code_like_terms(raw_terms)
    prompt_norm = _norm(prompt_text) if prompt_text else ""
    out = BucketedRecall(query=result.query, hit_layers=dict(result.hit_layers))
    single_plain = len(terms) == 1 and not code_terms
    for domain, hits in result.buckets.items():
        if single_plain:
            out.buckets[domain] = []
            if hits:
                out.hit_layers[domain] = "filtered"
            continue
        hits = hits or []
        kept: list[dict] = []
        if domain == "context_files":
            # One hit carries every matched section in `content`; gate per section.
            content = (hits[0].get("content") or "") if hits else ""
            sections = [s for s in split_sections(content)
                        if _keep(domain, s, terms, prompt_norm, code_terms)]
            if sections:
                names = [s.splitlines()[0].lstrip("# ").strip() for s in sections]
                kept = [{"section": n} for n in names]
                kept[0]["content"] = "\n\n".join(sections)
        else:
            kept = [h for h in hits if isinstance(h, dict)
                    and _keep(domain, _hit_text(domain, h), terms, prompt_norm, code_terms)]
            if domain == "library":
                kept = [h for h in kept if _daily_fresh_or_strong(h, terms)]
                kept = _library_slots(kept, slots.get("library", LIBRARY_TOP),
                                      slots.get("memory_archive", ARCHIVE_SLOTS))
            elif domain == "session":
                kept = [h for h in kept if _session_fresh_or_strong(h, terms)]
                kept = kept[:slots["session"]] if "session" in slots else kept
        out.buckets[domain] = _budget(domain, kept, caps)
        if hits and not out.buckets[domain]:
            out.hit_layers[domain] = "filtered"
    return out


def _library_slots(hits: list[dict], top: int = LIBRARY_TOP,
                   archive_slots: int = ARCHIVE_SLOTS) -> list[dict]:
    """Rank slots for the library leg: the first ``top`` ordinary hits plus up
    to ``archive_slots`` memory-archive hits, in their original order; fetched
    feeds never. Applied after the relevance gate, so ranks are among relevant hits."""
    out: list[dict] = []
    ordinary = archive = 0
    for h in hits:
        src = str(h.get("source") or "")
        if src.startswith(NO_INJECT_PREFIXES):
            continue
        if src.startswith(ARCHIVE_PREFIX):
            if archive < archive_slots:
                archive += 1
                out.append(h)
        elif ordinary < top:
            ordinary += 1
            out.append(h)
    return out


def _fresh_or_strong(day: str, text: str, terms: list[str]) -> bool:
    """Recent enough (<= SESSION_FRESH_DAYS before today), or older but matching
    SESSION_STALE_MIN_TERMS query terms. No or bad date: kept (age unknown, not old)."""
    if not day:
        return True
    try:
        from datetime import date
        age = (date.fromisoformat(_today()) - date.fromisoformat(day)).days
    except ValueError:
        return True
    if age <= SESSION_FRESH_DAYS:
        return True
    return _coverage(text, terms) >= SESSION_STALE_MIN_TERMS


def _session_fresh_or_strong(hit: dict, terms: list[str]) -> bool:
    """The age rule for a past-session hit (date = its best match's day)."""
    return _fresh_or_strong(str(hit.get("date") or "")[:10], _hit_text("session", hit), terms)


def _daily_fresh_or_strong(hit: dict, terms: list[str]) -> bool:
    """The age rule for a DailyActivity library hit; other sources pass."""
    m = _DAILY_RE.search(str(hit.get("source") or ""))
    return not m or _fresh_or_strong(m.group(1), _hit_text("library", hit), terms)


def _budget(domain: str, hits: list[dict], caps: dict[str, int] = DOMAIN_CHAR_CAPS) -> list[dict]:
    if domain == "library":
        # Archive slots carry their own budget, so ordinary hits ranked above
        # them cannot use it up (D4); original order is kept, notes last.
        tagged = [{**h, "_i": i} for i, h in enumerate(hits)]
        is_arch = [str(h.get("source") or "").startswith(ARCHIVE_PREFIX) for h in tagged]
        kept = (_budget_one("library", [h for h, a in zip(tagged, is_arch) if not a], caps)
                + _budget_one("memory_archive", [h for h, a in zip(tagged, is_arch) if a], caps))
        kept.sort(key=lambda h: h.get("_i", len(hits)))
        return [{k: v for k, v in h.items() if k != "_i"} for h in kept]
    return _budget_one(domain, hits, caps)


def _text_key(domain: str) -> str:
    return "text" if domain == "session" else "content"


def _hit_size(domain: str, hit: dict) -> int:
    text = hit.get(_text_key(domain))
    return len(text) if isinstance(text, str) and text else 80   # a pointer-only hit


def _budget_one(domain: str, hits: list[dict],
                caps: dict[str, int] = DOMAIN_CHAR_CAPS) -> list[dict]:
    """The hits that fit the domain's budget, each WHOLE, by rank (``take_whole``), and
    one ``omitted`` row naming the rest (no id, no score: it is not a hit)."""
    cap = caps.get(domain, 1500)
    if domain == "context_files":
        return _budget_sections(hits, cap)
    shown, omitted = take_whole(hits, cap, lambda h: _hit_size(domain, h))
    return shown + _omitted_rows(domain, omitted)


def _budget_sections(hits: list[dict], cap: int) -> list[dict]:
    """context_files carries every kept ``## section`` in its first hit's content:
    whole sections by rank while they fit, the rest named (``MEMORY.md#<section>``)."""
    content = (hits[0].get("content") or "") if hits else ""
    sections = split_sections(content)
    shown, omitted = take_whole(sections, cap, lambda s: len(s) + 2)
    names = [s.splitlines()[0].lstrip("# ").strip() for s in shown]
    out = [{"section": n} for n in names]
    note = ""
    if omitted:
        note = not_shown_note(
            [(f"MEMORY.md#{s.splitlines()[0].lstrip('# ').strip()}", "", len(s))
             for s in omitted], "section", "sections", max_items=NOTE_ITEMS)
    if out:
        out[0]["content"] = "\n\n".join(shown + ([note] if note else []))
    elif note:
        out = [{"section": "", "kind": "omitted", "content": note}]
    return out


def _trust(hit: dict) -> str:
    """Which block a library hit renders in: a note naming an untrusted source must sit
    inside that source's fence, so its location never reads as trusted text."""
    from core.recall_multi import is_external_source, is_repo_doc_source
    src = str(hit.get("source") or "")
    return "repo" if is_repo_doc_source(src) else ("external" if is_external_source(src) else "")


def _bind_prefix() -> str:
    from core.knowledge_store import BIND_KEY_PREFIX
    return BIND_KEY_PREFIX


def _external_prefix() -> str:
    from core.recall_multi import EXTERNAL_SOURCE_PREFIXES
    return EXTERNAL_SOURCE_PREFIXES[0]


# the neutral label of a note naming several untrusted sources: still routed to the fence
_TRUST_PREFIX = {"repo": _bind_prefix, "external": _external_prefix}


def _omitted_rows(domain: str, omitted: list[dict]) -> list[dict]:
    if not omitted:
        return []
    key = _text_key(domain)
    groups: dict[str, list[dict]] = {}
    for h in omitted:
        groups.setdefault(_trust(h) if domain in ("library", "memory_archive") else "",
                          []).append(h)
    rows = []
    for trust, group in groups.items():
        items = []
        for h in group:
            if domain == "session":
                loc = f"session {h.get('session_id') or ''} ({h.get('date') or ''})"
            elif domain == "codeintel":
                loc = str(h.get("id") or h.get("name") or "?")
            elif trust:
                loc = str(h.get("source") or "")       # untrusted: its location only
            else:
                loc = _pointer(domain, h)
            title = unit_title(h.get(key) or "") if domain == "ddd" else ""
            items.append((loc, title, _hit_size(domain, h)))
        noun, nouns = {"session": ("past session", "past sessions"),
                       "codeintel": ("symbol", "symbols")}.get(domain, ("entry", "entries"))
        row = {"kind": "omitted", key: not_shown_note(items, noun, nouns, max_items=NOTE_ITEMS)}
        if domain in ("library", "memory_archive"):
            # an untrusted note carries its sources' location, so it renders in their
            # fence; a trusted one has none
            srcs = {str(h.get("source") or "") for h in group}
            row["source"] = ("" if not trust else srcs.pop() if len(srcs) == 1
                             else _TRUST_PREFIX[trust]())
        rows.append(row)
    return rows
