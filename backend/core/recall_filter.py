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
   (``NO_INJECT_PREFIXES``); a past session older than ``SESSION_FRESH_DAYS``
   must match ``SESSION_STALE_MIN_TERMS`` query terms.

Nothing is removed from any corpus; this only decides which hits of this
query are worth the model's attention.

Public:
- ``query_terms(query)``
- ``filter_recall(result, prompt_text="", followup=False)`` -> BucketedRecall
- ``in_prompt_text(text, prompt_norm)``
- ``DOMAIN_CHAR_CAPS``, ``FOLLOWUP_CHAR_CAPS``, ``FOLLOWUP_SLOTS``, ``LIBRARY_TOP``, ``ARCHIVE_SLOTS``, ``NO_INJECT_PREFIXES``
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# Max characters each domain may contribute to the injected block, and the max
# characters of any single hit. ~6.5K characters total, about 2-2.5K tokens.
DOMAIN_CHAR_CAPS: dict[str, int] = {
    "context_files": 1500,
    "ddd": 2000,
    "library": 2800,
    "session": 1800,
    "codeintel": 500,
    "memory_archive": 1200,   # the library leg's archive slots (D4), own budget
}
HIT_CHAR_CAP = 900

# Follow-up recall (a later message on a new topic) gets a smaller block: the
# whole session shares one budget (session_router._RECALL_SESSION_CHARS), and
# the first hit of a leg carries most of its value (library rank 1 useful 62%,
# rank 2 37%, rank 3+ under 30%; past sessions 37% overall). Halving each
# follow-up roughly doubles how many a session gets (design 2026-10-26 §9).
FOLLOWUP_CHAR_CAPS: dict[str, int] = {
    "context_files": 700,
    "ddd": 1000,
    "library": 1400,
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


def _truncate(text: str, cap: int) -> str:
    if len(text) <= cap:
        return text
    cut = text[:cap]
    nl = cut.rfind("\n")
    if nl > cap * 0.6:
        cut = cut[:nl]
    return cut.rstrip() + " …"


def _split_sections(content: str) -> list[str]:
    parts = re.split(r"\n(?=## )", content.strip())
    return [p for p in parts if p.strip()]


def _hit_text(domain: str, hit: dict) -> str:
    if domain == "session":
        return hit.get("text") or ""
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


def filter_recall(result: Any, prompt_text: str = "", followup: bool = False) -> Any:
    """Return a copy of ``result`` holding only relevant, new, budgeted hits.

    ``followup``: a later message's recall; uses ``FOLLOWUP_CHAR_CAPS`` and
    ``FOLLOWUP_SLOTS`` instead of the first-message sizes."""
    caps = FOLLOWUP_CHAR_CAPS if followup else DOMAIN_CHAR_CAPS
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
            sections = [s for s in _split_sections(content)
                        if _keep(domain, s, terms, prompt_norm, code_terms)]
            if sections:
                names = [s.splitlines()[0].lstrip("# ").strip() for s in sections]
                kept = [{"section": n} for n in names]
                kept[0]["content"] = "\n\n".join(sections)
        else:
            kept = [h for h in hits if isinstance(h, dict)
                    and _keep(domain, _hit_text(domain, h), terms, prompt_norm, code_terms)]
            if domain == "library":
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


def _session_fresh_or_strong(hit: dict, terms: list[str]) -> bool:
    """A past session recent enough, or old but matching enough terms. A hit with
    no date keeps the old behaviour (kept): its age is unknown, not old."""
    day = str(hit.get("date") or "")[:10]
    if not day:
        return True
    try:
        from datetime import date
        age = (date.fromisoformat(_today()) - date.fromisoformat(day)).days
    except ValueError:
        return True
    if age <= SESSION_FRESH_DAYS:
        return True
    return _coverage(_hit_text("session", hit), terms) >= SESSION_STALE_MIN_TERMS


def _budget(domain: str, hits: list[dict], caps: dict[str, int] = DOMAIN_CHAR_CAPS) -> list[dict]:
    if domain == "library":
        # Archive slots carry their own budget, so ordinary hits ranked above
        # them cannot use it up (D4); original order is kept.
        tagged = [{**h, "_i": i} for i, h in enumerate(hits)]
        is_arch = [str(h.get("source") or "").startswith(ARCHIVE_PREFIX) for h in tagged]
        kept = (_budget_one("library", [h for h, a in zip(tagged, is_arch) if not a], caps)
                + _budget_one("memory_archive", [h for h, a in zip(tagged, is_arch) if a], caps))
        kept.sort(key=lambda h: h["_i"])
        return [{k: v for k, v in h.items() if k != "_i"} for h in kept]
    return _budget_one(domain, hits, caps)


def _budget_one(domain: str, hits: list[dict],
                caps: dict[str, int] = DOMAIN_CHAR_CAPS) -> list[dict]:
    cap = caps.get(domain, 1500)
    used = 0
    out: list[dict] = []
    for h in hits:
        key = "text" if domain == "session" else "content"
        text = h.get(key)
        if isinstance(text, str) and text:
            per_hit = cap if domain == "context_files" else HIT_CHAR_CAP
            text = _truncate(text, min(per_hit, cap - used))
            if len(text) < 60 and used:
                break
            h = {**h, key: text}
            used += len(text)
        else:
            used += 80
        out.append(h)
        if used >= cap:
            break
    return out
