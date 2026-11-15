"""term_map — a Brain's Chinese-to-English term map, applied to queries only.

A Brain written in English shares no word with a question asked in Chinese, so
keyword recall (BM25 / FTS5, no vectors by design) cannot connect them: the CJK
bigrams of the question appear nowhere in the entries. A Brain may therefore keep a
deterministic map from its Chinese problem words to its English solution words, and
every Brain recall door expands a query that contains Chinese with the mapped English
terms before scoring it. Nothing is written to stored entries, no model is called,
and a Brain without a map scores exactly as before.

WHERE THE MAP LIVES
-------------------
In the Brain's ``CONTEXT.md`` (its glossary, the ubiquitous-language file), from two
sources, both read every time the file changes:

- a ``## Term map`` section, one bullet per line::

      ## Term map
      - 会话, 对话 → session
      - 派发 → dispatch
      - 超时 → timeout, time out

  The left side is one or more Chinese terms (separated by ``,`` ``，`` ``、`` or
  ``/``), the right side one or more English terms (separated by ``,`` ``，`` or
  ``;``). The arrow may be ``→``, ``->``, ``=>`` or ``=``.
- the glossary itself: a term heading that names a term in both scripts, such as
  ``**Session（会话）**`` or ``**会话 / Session**``, maps its Chinese name(s) to its
  English name.

``CONTEXT.md`` is used because it already travels with a Brain to a Hive and is not
part of the Brain's unit table: a new top-level file is not on brain_sync's
allowlist, and an older Hive refuses a package member it does not accept.

MATCHING
--------
Only keys with at least two CJK characters count: a Latin key, or a one-character
key, matched as a substring false-fires (``库`` inside ``数据库``, ``code`` inside
``barcode``). Chinese has no word spaces, so a key matches as a substring of the
query; keys are tried longest first and a matched span is not matched again by a
shorter key inside it, so ``不一致`` → inconsistent does not also fire ``一致`` →
consistent. An English term already in the query is not added again, which makes
``expand_query`` idempotent: two doors applying it to the same query is harmless.
"""
from __future__ import annotations

import logging
import re
import threading
from pathlib import Path
from typing import Optional

from core.cjk_index import _CJK_CLASS, _CJK_RE

logger = logging.getLogger(__name__)

TERM_MAP_FILE = "CONTEXT.md"
TERM_MAP_HEADING = "term map"          # a "## Term map" heading, any case, any suffix
MIN_KEY_CJK_CHARS = 2
MAX_FILE_BYTES = 512 * 1024            # a glossary is small; a huge file is not read on the recall path

_ARROW_RE = re.compile(r"\s*(?:→|->|=>|=)\s*")
_KEY_SPLIT_RE = re.compile(r"[,，、/]")
_VALUE_SPLIT_RE = re.compile(r"[,，;；]")
_CJK_RUN_RE = re.compile(f"[{_CJK_CLASS}]+")
_GLOSSARY_RE = re.compile(r"^\*\*(?P<term>[^*]+)\*\*\s*$")
_MARKUP_RE = re.compile(r"[`*_]")
_LATIN_WORD_RE = re.compile(r"[A-Za-z0-9][\w\-.]*")
_WORD_RE = re.compile(r"[\w\-]+", re.UNICODE)

TermMap = dict[str, tuple[str, ...]]

_CACHE: dict[str, tuple[tuple[int, ...], TermMap]] = {}
_CACHE_LOCK = threading.Lock()


def _english(text: str) -> str:
    """*text* as an English term: markup removed, no CJK, single-spaced."""
    text = _MARKUP_RE.sub(" ", text)
    if _CJK_RE.search(text):
        return ""
    return " ".join(text.split())


def _add(out: dict[str, list[str]], keys: list[str], values: list[str]) -> None:
    for key in keys:
        key = "".join(key.split())
        if len(_CJK_RE.findall(key)) < MIN_KEY_CJK_CHARS:
            continue
        have = out.setdefault(key, [])
        for v in values:
            if v and v not in have:
                have.append(v)


def _map_line(line: str) -> tuple[list[str], list[str]]:
    """(Chinese keys, English values) of one ``- 中文 → english`` line, or ([], [])."""
    body = line.strip()
    if not body.startswith(("- ", "* ")):
        return [], []
    parts = _ARROW_RE.split(body[2:].strip(), maxsplit=1)
    if len(parts) != 2:
        return [], []
    keys = [k.strip() for k in _KEY_SPLIT_RE.split(_MARKUP_RE.sub("", parts[0]))]
    values = [_english(v) for v in _VALUE_SPLIT_RE.split(parts[1])]
    return [k for k in keys if k], [v for v in values if v]


def _glossary_pair(line: str) -> tuple[list[str], list[str]]:
    """(Chinese names, [English name]) of a glossary heading naming a term in both
    scripts (``**Session（会话）**``), or ([], [])."""
    m = _GLOSSARY_RE.match(line.strip())
    if not m:
        return [], []
    term = m.group("term")
    keys = _CJK_RUN_RE.findall(term)
    if not keys:
        return [], []
    rest = _CJK_RUN_RE.sub(" ", term)
    english = " ".join(_LATIN_WORD_RE.findall(rest))
    return (keys, [english]) if english else ([], [])


def parse_term_map(text: str) -> TermMap:
    """The term map *text* (a ``CONTEXT.md``) holds: its ``## Term map`` lines and its
    two-script glossary headings. Fenced code is skipped. Deterministic, never raises
    on content."""
    out: dict[str, list[str]] = {}
    in_map = False
    fence = ""
    for line in (text or "").splitlines():
        mark = line.lstrip()[:3]
        if mark in ("```", "~~~") and (not fence or fence == mark):
            fence = "" if fence else mark
            continue
        if fence:
            continue
        if line.startswith("## "):
            in_map = line[3:].strip().lower().startswith(TERM_MAP_HEADING)
            continue
        keys, values = _map_line(line) if in_map else _glossary_pair(line)
        if keys and values:
            _add(out, keys, values)
    return {k: tuple(v) for k, v in out.items()}


def load_term_map(brain_dir: Optional[Path]) -> TermMap:
    """The term map of the Brain at *brain_dir* (``{}`` when it has none). Cached per
    file and re-read when its size, mtime, ctime or inode changes, so a recall turn
    costs one ``stat`` while the file is unchanged. Never raises."""
    if brain_dir is None:
        return {}
    path = Path(brain_dir) / TERM_MAP_FILE
    try:
        st = path.stat()
    except OSError:
        return {}
    if st.st_size > MAX_FILE_BYTES or not path.is_file():
        return {}
    # ctime and inode too: an edit of the same size whose mtime is put back (a copy
    # that keeps times, an os.replace install) still changes one of them
    stamp = (st.st_mtime_ns, st.st_size, st.st_ctime_ns, st.st_ino)
    key = str(path)
    with _CACHE_LOCK:
        hit = _CACHE.get(key)
    if hit is not None and hit[0] == stamp:
        return hit[1]
    try:
        mapping = parse_term_map(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        logger.warning("term map: %s unreadable", path)
        return {}
    with _CACHE_LOCK:
        _CACHE[key] = (stamp, mapping)
    return mapping


def _reset_cache() -> None:
    """Forget every cached map (test hook)."""
    with _CACHE_LOCK:
        _CACHE.clear()


def matched_keys(query: str, mapping: TermMap) -> list[str]:
    """The keys of *mapping* found in *query*, longest first; a matched span is not
    matched again by a shorter key inside it. ``[]`` when the query holds no CJK."""
    if not query or not mapping or not _CJK_RE.search(query):
        return []
    rest = query
    found: list[str] = []
    for key in sorted(mapping, key=lambda k: (-len(k), k)):
        if key in rest:
            found.append(key)
            rest = rest.replace(key, " ")
    return found


def mapped_terms(query: str, mapping: TermMap) -> list[str]:
    """The English terms *mapping* adds to *query*: those of every key it matches
    (``matched_keys``), leaving out a term whose words are all in the query already.
    ``[]`` when the query holds no CJK or nothing maps."""
    added: list[str] = []
    present = {w.lower() for w in _WORD_RE.findall(query or "")}
    for key in matched_keys(query, mapping):
        for term in mapping[key]:
            words = [w.lower() for w in _WORD_RE.findall(term)]
            if words and not all(w in present for w in words):
                added.append(term)
                present.update(words)
    return added


def expand_query(query: str, mapping: TermMap) -> str:
    """*query* followed by its ``mapped_terms``, or *query* unchanged when none. A second
    expansion changes nothing: every term it would add is in the query already."""
    added = mapped_terms(query, mapping)
    return f"{query} {' '.join(added)}" if added else query


def mentions(text: str, term: str) -> bool:
    """True when *text* holds the English *term* as whole words, ignoring case
    (``time out`` is not found in ``output of a time-based job``)."""
    words = [re.escape(w) for w in _WORD_RE.findall(term.lower())]
    if not words:
        return False
    # ASCII boundaries: a CJK character next to the word ("会话session") is a boundary
    pattern = r"(?<![A-Za-z0-9_\-])" + r"[\s\-_]+".join(words) + r"(?![A-Za-z0-9_\-])"
    return re.search(pattern, text.lower()) is not None


def brain_matches(brain_dir: Optional[Path], query: str) -> TermMap:
    """The part of the term map of the Brain at *brain_dir* that *query* matches
    (``matched_keys`` with their English terms), ``{}`` when none. Never raises: a map
    that cannot be used matches nothing."""
    try:
        mapping = load_term_map(brain_dir)
        return {k: mapping[k] for k in matched_keys(query, mapping)}
    except Exception:  # noqa: BLE001 -- a recall query is never lost to its expansion
        logger.warning("term map: expansion failed for %s", brain_dir, exc_info=True)
        return {}


def expand_for_brain(brain_dir: Optional[Path], query: str) -> str:
    """*query* expanded with the term map of the Brain at *brain_dir* (unchanged when
    it has none or nothing maps). Never raises."""
    return expand_query(query, brain_matches(brain_dir, query))
