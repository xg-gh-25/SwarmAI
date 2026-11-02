"""Unified CDF corpus unit parsing and relation traversal.

- ``parse_units`` parses bullet, EVOLUTION, TIMELINE, link, gap, and document units.
- ``layer_for`` is the closed layer table.
- ``active_head`` follows only active ``supersedes`` edges.
"""
from __future__ import annotations

import bisect
import hashlib
import os
import re
import threading
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

from core.ddd_entry_lifecycle import (
    MEMORY_SECTIONS,
    EntryMetadata,
    parse_entries,
)

# The writer's identity marker: a comment that begins with ``cand:<id>`` (it may carry
# more fields, ``<!-- cand:<id> | project_hint:"…" -->``). Entries cite other entries in
# their text ("Kept: cand:cand-….unit"), so a bare ``cand:`` anywhere is not an identity.
_MARKER = re.compile(r"<!--\s*cand:([A-Za-z0-9_.:-]+?)\s*(?:\|[^>]*?)?-->")


def cand_marker(text: str) -> str | None:
    """The id the entry's own marker comment names (the last one), or None."""
    found = _MARKER.findall(text or "")
    return found[-1] if found else None


def cand_id(text: str) -> str | None:
    """The CDF candidate id (``cand-<hex>``) of the entry's own marker, or None."""
    marker = cand_marker(text)
    m = re.fullmatch(r"(cand-[0-9a-f]+)\.unit", marker or "")
    return m.group(1) if m else None
_H2 = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
_H3 = re.compile(r"^###\s+([CKO]\d+|[^\n]+?)\s*$", re.MULTILINE)


@dataclass(frozen=True)
class Unit:
    """One addressable corpus unit; offsets are locations, never identity."""

    path: str
    section: str
    byte_start: int
    byte_end: int
    identity: str
    byte_hash: str
    text: str
    title: str
    entry_type: str | None = None
    # A lifecycle entry's taint as ``EntryMetadata.taint`` parsed it from the entry's
    # own metadata comment ("external" or ""); None for a unit that is no lifecycle
    # entry (EVOLUTION / TIMELINE blocks, link and gap bullets, whole documents).
    taint: str | None = None

    @property
    def anchor(self) -> str:
        return f"{self.path}#{self.section}#{self.identity}"


@dataclass(frozen=True)
class Supersedes:
    """One active or inactive supersedes relation."""

    subject_anchor: str
    object_anchor: str
    predicate: str = "supersedes"
    inactive: bool = False


class _Offsets:
    """Byte offsets of character positions in one text, computed incrementally.

    Encoding the whole prefix for every position made a parse quadratic: about one
    second for a 1 MB DDD file, on the chat's injection, recall and Read paths too
    (2026-10-29-cdf-chat-coexistence.md §1). Positions arrive in increasing order, so
    only the slice since the previous one is encoded; a position before the previous
    one starts again from 0 (always correct). One instance per parse, never shared
    between threads."""

    def __init__(self, text: str) -> None:
        self.text = text
        self._char = 0
        self._byte = 0

    def __call__(self, char: int) -> int:
        if char < self._char:
            self._char, self._byte = 0, 0
        self._byte += len(self.text[self._char:char].encode("utf-8"))
        self._char = char
        return self._byte


class _Sections:
    """The ``## `` heading in force at a position: one scan, then a binary search
    (scanning the prefix again for every unit was quadratic as well)."""

    def __init__(self, text: str) -> None:
        matches = list(_H2.finditer(text))
        self._starts = [m.start() for m in matches]
        self._names = [m.group(1).strip() for m in matches]

    def before(self, offset: int) -> str:
        index = bisect.bisect_left(self._starts, offset)   # headings starting before offset
        return self._names[index - 1] if index else ""


def _unit(path: str, section: str, text: str, offsets: _Offsets, start: int,
          title: str, entry_type: str | None, baseline: Mapping[str, str],
          taint: str | None = None) -> Unit:
    raw = text.encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()
    marker = cand_marker(text)
    identity = f"cand:{marker}" if marker else baseline.get(digest, digest)
    byte_start = offsets(start)
    return Unit(path=path, section=section, byte_start=byte_start,
                byte_end=byte_start + len(raw), identity=identity,
                byte_hash=digest, text=text, title=title, entry_type=entry_type,
                taint=taint)


def _relative(path: Path, root: Path | None) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix() if root else path.as_posix()
    except ValueError:
        return path.as_posix()


def _parse_evolution(content: str, rel: str, baseline: Mapping[str, str]) -> list[Unit]:
    offsets, sections = _Offsets(content), _Sections(content)
    matches = list(_H3.finditer(content))
    out: list[Unit] = []
    for index, match in enumerate(matches):
        boundaries = [len(content)]
        if index + 1 < len(matches):
            boundaries.append(matches[index + 1].start())
        next_section = _H2.search(content, match.end())
        if next_section:
            boundaries.append(next_section.start())
        end = min(boundaries)
        block = content[match.start():end].rstrip()
        if block:
            out.append(_unit(rel, sections.before(match.start()), block, offsets,
                             match.start(), match.group(1).strip(), None, baseline))
    return out


def _parse_timeline(content: str, rel: str, baseline: Mapping[str, str]) -> list[Unit]:
    offsets, sections = _Offsets(content), _Sections(content)
    matches = list(re.finditer(r"^###\s+(\S+)\s+(.+?)\s*$", content, re.MULTILINE))
    out: list[Unit] = []
    for index, match in enumerate(matches):
        boundaries = [len(content)]
        if index + 1 < len(matches):
            boundaries.append(matches[index + 1].start())
        next_h2 = _H2.search(content, match.end())
        if next_h2:
            boundaries.append(next_h2.start())
        end = min(boundaries)
        block = content[match.start():end].rstrip()
        if block:
            out.append(_unit(rel, sections.before(match.start()), block, offsets,
                             match.start(), match.group(2).strip(), "event", baseline))
    return out


def _parse_special_bullets(content: str, rel: str,
                           baseline: Mapping[str, str]) -> list[Unit]:
    offsets, sections = _Offsets(content), _Sections(content)
    matches = list(re.finditer(
        r"^- \[(link|gap:[^\]]+)\] \*\*(.+?)\*\*.*$", content, re.MULTILINE))
    out: list[Unit] = []
    for index, match in enumerate(matches):
        boundaries = [len(content)]
        if index + 1 < len(matches):
            boundaries.append(matches[index + 1].start())
        for pattern in (_H2, _H3):
            following = pattern.search(content, match.end())
            if following:
                boundaries.append(following.start())
        end = min(boundaries)
        block = content[match.start():end].rstrip()
        if block:
            out.append(_unit(rel, sections.before(match.start()), block, offsets,
                             match.start(), match.group(2).strip(), match.group(1), baseline))
    return out


_CACHE_SIZE = 64
_cache: "OrderedDict[tuple, tuple[Unit, ...]]" = OrderedDict()
_cache_lock = threading.Lock()


def parse_units(path: Path | str, *, root: Path | None = None,
                baseline: Mapping[str, str] | None = None) -> list[Unit]:
    """Parse one read-only markdown path into stable-addressable units.

    Cached by (path, root, inode, mtime, ctime, size) when no baseline is given: card 3 parsed
    every DDD document again for every unit it judged, and chat's injection, recall
    and Read paths parse the same files. A changed file has a new key. The caller
    gets its own list; ``Unit`` is frozen."""
    file_path = Path(path)
    key = None
    if not baseline:
        try:
            # inode, device and ctime too: a copy that keeps size and mtime (copy2,
            # rsync -t, os.utime) must not return the old offsets, which the writers
            # cut the file at. The path as given: Unit.path and the parser follow it.
            stat = os.stat(file_path)
            key = (str(file_path), str(root) if root else None, str(file_path.resolve()),
                   stat.st_ino, stat.st_dev, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size)
        except OSError:
            key = None
        if key is not None:
            with _cache_lock:
                hit = _cache.get(key)
                if hit is not None:
                    _cache.move_to_end(key)
                    return list(hit)
    parsed = _parse_units(file_path, root, baseline or {})
    if key is not None:
        with _cache_lock:
            _cache[key] = tuple(parsed)
            while len(_cache) > _CACHE_SIZE:
                _cache.popitem(last=False)
    return parsed


def _parse_units(file_path: Path, root: Path | None, known: Mapping[str, str]) -> list[Unit]:
    content = file_path.read_text(encoding="utf-8")
    rel = _relative(file_path, root)
    if file_path.name == "EVOLUTION.md":
        return _parse_evolution(content, rel, known)
    if file_path.name == "TIMELINE.md":
        return _parse_timeline(content, rel, known)

    special = _parse_special_bullets(content, rel, known)
    special_starts = {unit.byte_start for unit in special}
    offsets = _Offsets(content)
    entries = parse_entries(content, skip_h3_spans=False)
    out: list[Unit] = list(special)
    cursor = 0
    for entry in entries:
        start = content.find(entry.raw_text, cursor)
        if start < 0:
            start = content.find(entry.raw_text)
        if start < 0:
            continue
        cursor = start + len(entry.raw_text)
        if offsets(start) in special_starts:
            continue
        out.append(_unit(rel, entry.section, entry.raw_text, offsets, start,
                         entry.title, entry.entry_type, known, taint=entry.taint or ""))
    if out:
        return sorted(out, key=lambda unit: unit.byte_start)

    stripped = content.strip()
    if not stripped:
        return []
    start = content.find(stripped)
    title_match = re.search(r"^#\s+(.+?)\s*$", stripped, re.MULTILINE)
    title = title_match.group(1).strip() if title_match else file_path.stem
    return [_unit(rel, "", stripped, _Offsets(content), start, title, None, known)]


def layer_for(target_section: str, entry_type: str) -> str:
    """Return the one valid layer for a section/type combination."""
    memory = MEMORY_SECTIONS.get(target_section)
    if memory and memory["type"] == entry_type:
        return str(memory["layer"])
    if target_section in {"Corrections Captured", "Competence Learned"}:
        if entry_type in {"correction", "guideline", "process", "model", "principle", "decision", "pitfall"}:
            return "meta-cognitive"
    ddd_sections = {
        "Recent Decisions", "Vision", "What Makes It Different", "Strategic Priorities",
        "Success Criteria", "Non-Goals", "Architecture", "Key Subsystems",
        "Conventions", "Runtime Traps", "What Worked", "What Failed", "Open Gaps",
        "What to Watch For", "Open Items",
    }
    if target_section in ddd_sections and (entry_type in {
        "guideline", "pitfall", "decision", "model", "process", "principle", "correction"
    } or entry_type.startswith("gap:")):
        return "domain"
    raise ValueError("invalid_layer_combination")


def active_head(anchor: str, relations: Sequence[Supersedes], *,
                existing_anchors: set[str] | None = None) -> str | None:
    """Follow active supersedes edges to one head; cycles or missing endpoints fail closed."""
    active = [edge for edge in relations if edge.predicate == "supersedes" and not edge.inactive]
    incoming: dict[str, list[str]] = {}
    for edge in active:
        incoming.setdefault(edge.object_anchor, []).append(edge.subject_anchor)
    current = anchor
    seen: set[str] = set()
    while True:
        if current in seen:
            return None
        seen.add(current)
        if existing_anchors is not None and current not in existing_anchors:
            return None
        replacements = sorted(set(incoming.get(current, [])))
        if not replacements:
            return current
        if len(replacements) > 1:
            return None
        current = replacements[0]
