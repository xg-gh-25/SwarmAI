"""CJK segmentation for SQLite FTS5 — the single expansion authority.

WHY THIS MODULE EXISTS
----------------------
SQLite FTS5 ships **no tokenizer that can segment a 2-character Chinese word**.
Measured against every built-in (``unicode61``, ``ascii``, ``porter``,
``trigram``, and the two option-bearing variants): a 2-char CJK term matches
**0** rows. ``unicode61`` (the default, and what all of SwarmAI's indexes used)
splits on whitespace/punctuation, so a Chinese run indexes as ONE token and is
findable only by an exact whole-run match. ``trigram`` — the tempting built-in
"fix" — needs 3 characters minimum, so it cannot see 2-char words at all, which
are the most common Chinese word length; switching to it raised aggregate recall
from 12.3% to 13.8% while taking completely-unfindable terms from 4/50 to 33/50.
A custom tokenizer needs the FTS5 **C API**, unreachable from Python's
``sqlite3``.

So the segmentation is applied in Python, to the text being indexed AND to the
query. Overlapping character bigrams make partial CJK matching work the way
whitespace does for English: 配置文件 → 配置 / 置文 / 文件, so a search for 配置
hits a document containing 配置文件.

THE TWO RULES THAT ARE EASY TO GET WRONG
----------------------------------------
1. **Split a mixed CJK+ASCII token BEFORE expanding.** ``用goal-pipeline跑`` is a
   single ``\\w+`` token. "Contains CJK → bigram the whole thing" yields
   ``['用g','go','oa','al','l-','-p',...]`` and destroys the embedded English
   word — measured as a real drop in English recall. Split into maximal CJK and
   non-CJK runs first; expand only the CJK runs, pass the ASCII through verbatim.

2. **Expand both sides.** An expanded index queried with a raw 4-char term
   returns 0 (the index holds bigrams, the query asks for one long token). An
   index-only change deploys cleanly and does nothing.

WHERE THE EXPANDED TEXT LIVES
-----------------------------
Callers store the output in a persisted ``*_seg`` column and point the FTS index
at that column, rather than writing expanded text into an index whose
external-content source is the raw column. That distinction is load-bearing, not
stylistic: FTS5 re-derives tokens from the content source on ``'rebuild'``, on
``DELETE FROM <fts>``, and inside the raw-``old.*`` triggers. If the source is
the raw column, every one of those paths subtracts tokens that were never
inserted — verified to leave stale postings behind and to fail
``INSERT INTO <fts>(<fts>, rank) VALUES('integrity-check', 1)`` with "database
disk image is malformed". Indexing a ``*_seg`` column makes all three paths
re-derive the *already expanded* text, so they are correct by construction.

Deliberately NOT used here: a SQL UDF or a generated column. Both require the
function to be registered on every connection that writes the table, and a
connection without it fails the whole statement ("unknown function") — an
availability risk on an irreplaceable message store.
"""

from __future__ import annotations

import re

# The scripts that need segmentation, as ONE character class used by all three
# regexes below. They MUST stay in sync: if the "is there CJK here" test and the
# "split into runs" test disagree about a character, a token containing it takes
# the CJK branch and then fails to split, which silently reproduces the
# mixed-token shredding this module exists to prevent.
#
# Included, and why each earns its place:
#   U+3040–30FF  Hiragana + Katakana — Japanese has no spaces either
#   U+31F0–31FF  Katakana phonetic extensions
#   U+FF66–FF9F  half-width Katakana
#   U+3400–4DBF  CJK Extension A
#   U+4E00–9FFF  CJK Unified Ideographs (the common case)
#   U+F900–FAFF  CJK Compatibility Ideographs
#   U+AC00–D7AF  Hangul syllables — Korean is written without word spaces too
#   U+1100–11FF  Hangul Jamo
#   U+20000+     Extension B and beyond (rare, but a 2-char word there was
#                completely unfindable before)
# Verified before this widening: expand_cjk("설정파일") returned the whole string
# as ONE token, so a 2-character Korean word was as unfindable as a Chinese one.
_CJK_CLASS = (
    "\u3040-\u30ff\u31f0-\u31ff\uff66-\uff9f"      # kana
    "\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff"      # ideographs
    "\u1100-\u11ff\uac00-\ud7af"                    # hangul
    "\U00020000-\U0003ffff"                          # ext B+
)

# ``memory_index._CJK_RE`` delegates its segmentation here so the codebase has
# ONE CJK policy rather than two that can disagree.
_CJK_RE = re.compile(f"[{_CJK_CLASS}]")

# A word-ish token: letters/digits/underscore (Unicode-aware, so CJK is
# included) plus the hyphen, which keeps compound identifiers like
# ``goal-pipeline`` intact.
_TOKEN_RE = re.compile(r"[\w\-]+", re.UNICODE)

# Splits a token into maximal CJK runs and maximal non-CJK runs. This is what
# makes the expansion split-aware (rule 1 above). Built from the SAME class as
# _CJK_RE so the two cannot drift.
_RUN_RE = re.compile(f"[{_CJK_CLASS}]+|[^{_CJK_CLASS}]+")


def _expand_token(token: str, cap: int | None = None) -> list[str]:
    """Expand ONE token, preserving any embedded non-CJK text verbatim.

    ``cap`` bounds how many characters of a single CJK RUN are expanded (query
    side); ``None`` means no bound (index side). An ASCII run is never truncated —
    it contributes one token regardless of length.
    """
    if not _CJK_RE.search(token):
        # Strip connector chars here too, exactly as the embedded-ASCII branch
        # below does. Without this the two sides disagree: `expand_cjk('___')`
        # stored the token `___`, which FTS5's tokenizer then discards, while
        # `expand_cjk_query('___')` asked for the phrase `"___"` — text stored but
        # unfindable. Also made `___` and `___配置` handle the same prefix
        # differently, which is the asymmetry class this module exists to avoid.
        stripped = token.strip("-_")
        return [stripped] if stripped else []

    out: list[str] = []
    for run in _RUN_RE.findall(token):
        if _CJK_RE.match(run):
            if len(run) == 1:
                out.append(run)  # a lone CJK char has no bigram
            else:
                # Bound the bigram count per run (query side only — see
                # _MAX_CJK_RUN_CHARS). Index side passes cap=None so stored text
                # is never truncated.
                span = run if cap is None else run[:cap]
                out.extend(span[i:i + 2] for i in range(len(span) - 1))
        else:
            # Embedded ASCII (e.g. the "goal-pipeline" of "用goal-pipeline跑").
            # Strip connector chars that would otherwise become lone tokens.
            stripped = run.strip("-_")
            if stripped:
                out.append(stripped)
    return out


def expand_cjk(text: str) -> str:
    """Return ``text`` with CJK runs expanded into overlapping bigrams.

    ASCII is preserved verbatim, including ASCII embedded inside an otherwise
    CJK token. The result is a single-space-joined token string suitable for
    storing in a ``*_seg`` column and indexing with FTS5's default tokenizer.

    Idempotent: ``expand_cjk(expand_cjk(x)) == expand_cjk(x)``. That matters
    because the migration that populates ``*_seg`` columns may be re-run, and a
    non-idempotent expansion would drift the index on every pass.
    """
    out: list[str] = []
    for token in _TOKEN_RE.findall(text or ""):
        out.extend(_expand_token(token))
    return " ".join(out)


# Longest CJK RUN we expand on the query side. A CJK run has no spaces, so a
# pasted paragraph arrives as ONE term and each character adds a bigram
# AND-clause — measured: a 20K-character paste produced a 180 KB MATCH expression
# taking ~2.8s on a small index, and the session search runs per debounced
# keystroke. 24 characters is far beyond any real CJK search intent while
# bounding one term to ~23 clauses.
#
# ⚠️ Applies to the CJK RUN, never to the whole term. Truncating the term itself
# silently breaks ASCII search: `axolotl-evolution` is 17 characters, and a
# 16-char term cap turned it into `axolotl-evolutio`, which is a prefix of no
# indexed token and therefore matches NOTHING. An ASCII token contributes exactly
# ONE clause regardless of length, so it needs no cap at all — only the
# per-character bigram expansion does. (Caught by an existing recall test after
# the first, term-level version of this cap shipped.)
#
# Index-side expansion is deliberately NOT capped — truncating stored text would
# lose content.
_MAX_CJK_RUN_CHARS = 24


def expand_cjk_terms(text: str) -> list[str]:
    """Per-term expansion for building a query, preserving term boundaries.

    Returns one list of index-side tokens per whitespace-separated input term.
    A query builder needs this shape rather than ``expand_cjk``'s flat string:
    the bigrams of ONE term must be AND-ed together (they all belong to the same
    word), while separate terms are OR-ed. Flattening first would lose the
    boundary and turn 配置 文件 into an undifferentiated bag.
    """
    groups: list[list[str]] = []
    for raw_term in (text or "").split():
        tokens: list[str] = []
        for token in _TOKEN_RE.findall(raw_term):
            tokens.extend(_expand_token(token, cap=_MAX_CJK_RUN_CHARS))
        if tokens:
            groups.append(tokens)
    return groups


def expand_cjk_query(text: str) -> str:
    """Build an FTS5 MATCH expression from user text, CJK-aware.

    Shape: ``("配置" AND "置文" AND "文件") OR ("语义" AND "义搜" AND "搜索")`` —
    the bigrams of one term AND-ed (all belong to that word), terms OR-ed (any
    matching term is relevant, which is the existing convention in every caller;
    FTS5's rank still boosts documents matching more terms).

    Every token is quote-wrapped, which preserves the injection-safety contract
    the existing query builders rely on: an FTS5 keyword (``OR``/``NEAR``/
    ``NOT``) or special character arriving in user text becomes a phrase
    literal, never an operator. Returns ``""`` when nothing survives
    tokenization, so callers can short-circuit instead of issuing a MATCH that
    FTS5 would reject.

    NOTE ON PRECISION: AND-of-bigrams is adjacency-agnostic — a document holding
    all bigrams of a term out of order still matches. That is an acceptable
    trade for a ranked recall layer (measured: most sampled terms matched ground
    truth exactly, the worst over-returned by ~25%). A caller needing exactness
    should post-filter its candidate rows with ``LIKE``.

    Each CJK RUN is bounded by ``_MAX_CJK_RUN_CHARS`` — a space-free CJK paste
    would otherwise become one enormous AND-chain (measured: 20K chars -> 180 KB
    expression, ~2.8s) and the session search runs per debounced keystroke. ASCII
    tokens are never truncated: one token is one clause however long it is.
    """
    clauses: list[str] = []
    for tokens in expand_cjk_terms(text):
        quoted = ['"' + t.replace('"', '""') + '"' for t in tokens]
        if len(quoted) == 1:
            clauses.append(quoted[0])
        else:
            clauses.append("(" + " AND ".join(quoted) + ")")
    return " OR ".join(clauses)
