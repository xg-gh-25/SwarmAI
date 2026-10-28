"""Unified CDF corpus unit parsing and relation traversal.

- ``parse_units`` parses bullet, EVOLUTION, TIMELINE, link, gap, and document units.
- ``layer_for`` is the closed layer table.
- ``active_head`` follows only active ``supersedes`` edges.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

from core.ddd_entry_lifecycle import (
    MEMORY_SECTIONS,
    EntryMetadata,
    parse_entries,
)

_CAND = re.compile(r"\bcand:([A-Za-z0-9_.:-]+)\b")
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


def _byte_offset(text: str, char_offset: int) -> int:
    return len(text[:char_offset].encode("utf-8"))


def _unit(path: str, section: str, text: str, whole: str, start: int,
          title: str, entry_type: str | None, baseline: Mapping[str, str],
          taint: str | None = None) -> Unit:
    raw = text.encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()
    marker = _CAND.search(text)
    identity = f"cand:{marker.group(1)}" if marker else baseline.get(digest, digest)
    return Unit(path=path, section=section, byte_start=_byte_offset(whole, start),
                byte_end=_byte_offset(whole, start + len(text)), identity=identity,
                byte_hash=digest, text=text, title=title, entry_type=entry_type,
                taint=taint)


def _relative(path: Path, root: Path | None) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix() if root else path.as_posix()
    except ValueError:
        return path.as_posix()


def _section_before(content: str, offset: int) -> str:
    matches = list(_H2.finditer(content, 0, offset))
    return matches[-1].group(1).strip() if matches else ""


def _parse_evolution(content: str, rel: str, baseline: Mapping[str, str]) -> list[Unit]:
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
            out.append(_unit(rel, _section_before(content, match.start()), block, content,
                             match.start(), match.group(1).strip(), None, baseline))
    return out


def _parse_timeline(content: str, rel: str, baseline: Mapping[str, str]) -> list[Unit]:
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
            out.append(_unit(rel, _section_before(content, match.start()), block, content,
                             match.start(), match.group(2).strip(), "event", baseline))
    return out


def _parse_special_bullets(content: str, rel: str,
                           baseline: Mapping[str, str]) -> list[Unit]:
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
            out.append(_unit(rel, _section_before(content, match.start()), block, content,
                             match.start(), match.group(2).strip(), match.group(1), baseline))
    return out


def parse_units(path: Path | str, *, root: Path | None = None,
                baseline: Mapping[str, str] | None = None) -> list[Unit]:
    """Parse one read-only markdown path into stable-addressable units."""
    file_path = Path(path)
    content = file_path.read_text(encoding="utf-8")
    rel = _relative(file_path, root)
    known = baseline or {}
    if file_path.name == "EVOLUTION.md":
        return _parse_evolution(content, rel, known)
    if file_path.name == "TIMELINE.md":
        return _parse_timeline(content, rel, known)

    special = _parse_special_bullets(content, rel, known)
    special_starts = {unit.byte_start for unit in special}
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
        if _byte_offset(content, start) in special_starts:
            continue
        out.append(_unit(rel, entry.section, entry.raw_text, content, start,
                         entry.title, entry.entry_type, known, taint=entry.taint or ""))
    if out:
        return sorted(out, key=lambda unit: unit.byte_start)

    stripped = content.strip()
    if not stripped:
        return []
    start = content.find(stripped)
    title_match = re.search(r"^#\s+(.+?)\s*$", stripped, re.MULTILINE)
    title = title_match.group(1).strip() if title_match else file_path.stem
    return [_unit(rel, "", stripped, content, start, title, None, known)]


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
