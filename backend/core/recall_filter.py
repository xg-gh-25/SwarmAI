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

Nothing is removed from any corpus; this only decides which hits of this
query are worth the model's attention.

Public:
- ``query_terms(query)``
- ``filter_recall(result, prompt_text="")`` -> BucketedRecall
- ``DOMAIN_CHAR_CAPS``
"""

from __future__ import annotations

import re
from typing import Any

# Max characters each domain may contribute to the injected block, and the max
# characters of any single hit. ~6.5K characters total, about 2-2.5K tokens.
DOMAIN_CHAR_CAPS: dict[str, int] = {
    "context_files": 1500,
    "ddd": 2000,
    "library": 2800,
    "session": 1800,
    "codeintel": 500,
}
HIT_CHAR_CAP = 900

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
    body = _norm("\n".join(lines[1:]) if len(lines) > 1 else text)
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
        return " ".join(str(hit.get(k) or "") for k in ("id", "name", "file_path"))
    return " ".join(str(hit.get(k) or "") for k in ("heading", "section", "content"))


def _keep(domain: str, text: str, terms: list[str], prompt_norm: str,
          code_terms: list[str]) -> bool:
    if domain == "codeintel":
        return _coverage(text, code_terms) >= 1
    if _coverage(text, terms) < _required(terms):
        return False
    return not _in_prompt(text, prompt_norm)


def filter_recall(result: Any, prompt_text: str = "") -> Any:
    """Return a copy of ``result`` holding only relevant, new, budgeted hits."""
    from core.recall_multi import BucketedRecall

    raw_terms = (result.query or "").split()
    terms = query_terms(result.query)
    code_terms = [t.lower() for t in raw_terms if is_code_like(t)]
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
        out.buckets[domain] = _budget(domain, kept)
        if hits and not out.buckets[domain]:
            out.hit_layers[domain] = "filtered"
    return out


def _budget(domain: str, hits: list[dict]) -> list[dict]:
    cap = DOMAIN_CHAR_CAPS.get(domain, 1500)
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
