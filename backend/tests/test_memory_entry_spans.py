"""Acceptance tests: MEMORY.md / EVOLUTION.md are changed whole entries at a time.

Design: docs/design/2026-10-25-memory-write-entry-dedup.md (v4), §6.

Each test names the symptom (S1-S8) or invariant it pins. Tests marked RED were
written before the fix and fail on main; tests marked PIN pass on main and guard
behaviour the fix must keep. All work on synthetic files under tmp_path; nothing
reads the live workspace.

Invariant: every write, sweep or removal adds, replaces or removes whole entry
spans (lead bullet + continuation + metadata + indented tail, or a whole ``###``
block); duplicates are judged only by the sweep, by one signature.
"""
from __future__ import annotations

import asyncio
import fcntl
import re
import threading
import time
from datetime import date
from pathlib import Path

import pytest

from core.ddd_entry_lifecycle import format_memory_entry, parse_entries

TODAY = date(2026, 10, 25)
META_RE = re.compile(r"^\s*<!-- ref:\d+ \|")


def _entry(etype: str, title: str, body: str, d: str, run_id: str | None = None) -> str:
    lead, meta = format_memory_entry(etype, title, body, d, run_id)
    return f"{lead}\n{meta}"




def _memory(tmp_path: Path, body_by_section: dict[str, str]) -> Path:
    ctx = tmp_path / ".context"
    ctx.mkdir(parents=True, exist_ok=True)
    parts = ["# MEMORY", ""]
    for sec, body in body_by_section.items():
        parts += [f"## {sec}", body, ""]
    p = ctx / "MEMORY.md"
    p.write_text("\n".join(parts), encoding="utf-8")
    return p


# ── Writes land whole (S1, S2, S4) ─────────────────────────────────────────

EVOLUTION_BASE = """# EVOLUTION

## Corrections Captured

### C057 | 2026-10-20
- **Correction**: older correction text
- **Status**: active

## Competence Learned

### K003 | 2026-10-20
- **Competence**: older competence text
- **Status**: active
"""


def _evolution(tmp_path: Path, text: str = EVOLUTION_BASE) -> Path:
    ctx = tmp_path / ".context"
    ctx.mkdir(parents=True, exist_ok=True)
    p = ctx / "EVOLUTION.md"
    p.write_text(text, encoding="utf-8")
    return p


def _id_blocks(text: str, prefix: str) -> dict[str, list[str]]:
    """Map each ``### <prefix>NNN`` header to the non-blank lines of its block."""
    blocks: dict[str, list[str]] = {}
    cur = None
    for ln in text.splitlines():
        m = re.match(rf"^### ({prefix}\d+)\b", ln)
        if m:
            cur = m.group(1)
            blocks[cur] = []
        elif ln.startswith("#"):
            cur = None
        elif cur and ln.strip():
            blocks[cur].append(ln)
    return blocks










# ── Sweeps remove whole spans (S5, S7) and judge duplicates by one signature ─

def _sweep(path: Path) -> str:
    from core.ddd_entry_lifecycle import reclaim_duplicate_entries
    reclaim_duplicate_entries(
        path.read_text(encoding="utf-8"), TODAY, path.parent,
        archive_name="MEMORY-archive-test.md", source_path=path
    )
    return path.read_text(encoding="utf-8")


def _with_tail(etype, title, body, d, run_id=None, detail=None) -> str:
    """Distillation shape: lead, metadata, then Detail + valid_from tail."""
    detail = detail or f"DailyActivity/{d}.md"
    return (f"{_entry(etype, title, body, d, run_id)}\n"
            f"  Detail: {detail}.\n  <!-- valid_from:{d} -->")










@pytest.mark.parametrize("etype,sections", [
    ("decision", ("Decisions", "Principles")),          # PIN (protected on main)
    ("guideline", ("Guidelines", "Standing Preferences")),  # RED (main groups file-wide)
])
def test_same_entry_in_two_sections_is_kept(tmp_path, etype, sections):
    """Section-scoped: a deliberate cross-section repeat is not a duplicate."""
    e = _entry(etype, "Cross reference", "identical body text here", "2026-10-24")
    mem = _memory(tmp_path, {sections[0]: e, sections[1]: e})
    assert _sweep(mem).count("Cross reference") == 2


EVOLUTION_WITH_BLOCKS = """# EVOLUTION

## Corrections Captured

### C001 | 2026-10-24
- **Correction**: same correction body
- **Status**: active

### C002 | 2026-10-25
- **Correction**: same correction body
- **Status**: active

### CLASS — test-isolation failures
- **Pattern**: shared fixture mutated
- **Pattern**: shared fixture mutated

### DATA-POINT 2026-10-25
- **Seen**: same data point
- **Seen**: same data point

## Competence Learned

- **[2026-10-25]** Auto-optimized skill foo: tightened trigger wording
- **[2026-10-25]** Auto-optimized skill foo: tightened trigger wording
"""


def _h3_spans(text: str) -> list[str]:
    out, cur = [], None
    for ln in text.splitlines():
        if ln.startswith("### "):
            if cur is not None:
                out.append("\n".join(cur).rstrip())
            cur = [ln]
        elif ln.startswith("## "):
            if cur is not None:
                out.append("\n".join(cur).rstrip())
            cur = None
        elif cur is not None:
            cur.append(ln)
    if cur is not None:
        out.append("\n".join(cur).rstrip())
    return out






# ── Writers emit canonical entries (S6) ────────────────────────────────────

# ── COE Registry is a keyed record (S3) ─────────────────────────────────────

def _coe_entries(text: str) -> list:
    return [e for e in parse_entries(text) if e.section == "COE Registry"]


def _coe_line(text: str, title: str) -> str:
    return next(ln for ln in text.splitlines() if f"**{title}**" in ln)


# ── Scheduled removal removes spans (S8) ────────────────────────────────────

# ── C/K ids are allocated under the file lock ──────────────────────────────



# ── Pins: what must not change ─────────────────────────────────────────────

def test_pin_parse_entries_default_reads_bullets_inside_h3():
    """PIN. Default parse_entries on a DDD doc with ### E001 headers is unchanged."""
    doc = "## Notes\n\n### E001 | 2026-10-01\n- **Name**: first item\n- **What**: detail line\n"
    titles = [e.title for e in parse_entries(doc)]
    assert titles == ["Name", "What"]




# ── Code-review findings (2026-10-25): one reading of an entry everywhere ───

def test_inject_metadata_does_not_cross_a_heading():
    """A metadata-free entry followed by ### gets its metadata right after it."""
    from core.ddd_entry_lifecycle import inject_entry_metadata
    doc = "## S\n- [guideline] **A** — body (2026-01-01)\n### Next\nparagraph\n- **Field**: value\n"
    entries = parse_entries(doc)
    out = inject_entry_metadata(doc, entries).splitlines()
    assert out[1].startswith("- [guideline] **A**")
    assert out[2].lstrip().startswith("<!-- ref:")
    assert out[3:6] == ["### Next", "paragraph", "- **Field**: value"]








