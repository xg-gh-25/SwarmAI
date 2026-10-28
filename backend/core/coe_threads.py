"""Which COEs are open: rendered from the COE Registry plus the not-yet-distilled signals.

A COE's state (open / resolved) is live working state, so nothing writes it into
MEMORY.md's Open Threads any more (CDF design: only CDF writes governed files, and
written text is never edited in place). The state is computed on read from two
inputs, each covering its own days, with no overlap and no guess:

1. **Baseline: MEMORY.md ``## COE Registry``.** Each registry entry carries its status
   (``🔍 Investigating`` / ``✅ Resolved``), folded by distillation from every
   DailyActivity day it has marked ``distilled: true``. Entries with neither status
   (hand-written post-mortems) are records, not live problems, and are skipped.
2. **Delta: the DailyActivity days not marked distilled.** Their JSONL sidecar records
   carry ``coe_signal`` (``candidate`` | ``resolution``) and ``coe_topic``. Folded in
   date order, file order within a day: a candidate opens (or counts on) a COE, a
   resolution closes it, a later candidate re-opens it.

After the CDF cutover distillation never runs, so every new day is in the delta and
the registry stays as the frozen baseline. Matching is by ``coe_key`` only (the key the
registry uses), never by shared words. Nothing here writes a file.

Readers: the session briefing (open COEs are its P0 items, and a COE-keyed thread in
MEMORY's Open Threads follows this state), and the DailyActivity summariser (open COE
titles, so a later report of the same problem reuses its title and key).

Key public symbols: ``coe_key``, ``COEState``, ``coe_states``, ``open_coes``,
``open_coe_titles``.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

_SIGNALS = ("candidate", "resolution")
_OPEN_MARK = "🔍 Investigating"
_RESOLVED_MARK = "✅ Resolved"
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def coe_key(topic: str) -> str:
    """Record key of a COE topic: its title as format_memory_entry stores it
    (``strip().strip("*").rstrip(".").strip()``) with inner ``**`` dropped (an Open
    Threads title is written without them), casefolded. The one definition."""
    return topic.strip().strip("*").rstrip(".").strip().replace("**", "").casefold()


@dataclass
class COEState:
    """State of one COE key."""

    key: str
    title: str
    open: bool = False
    first: str = ""          # earliest known date ("" when the baseline has none)
    last: str = ""           # date of the latest known signal
    reports: int = 0         # candidate signals counted while open


def _registry_baseline(memory_text: str) -> dict[str, COEState]:
    """COE Registry entries that carry a live status, keyed by ``coe_key``."""
    from core.ddd_entry_lifecycle import parse_entries

    m = re.search(r"^## COE Registry[ \t]*$", memory_text, re.M)
    if not m:
        return {}
    body = memory_text[m.end():]
    nxt = re.search(r"^## (?!#)", body, re.M)
    if nxt:
        body = body[:nxt.start()]
    # parse_entries numbers lines from 0 (the synthetic header is line 0), so the
    # entry's lead is combined line ``line_number``.
    combined = "## COE Registry" + body
    lines = combined.split("\n")
    out: dict[str, COEState] = {}
    for e in parse_entries(combined):
        lead = lines[e.line_number] if 0 <= e.line_number < len(lines) else ""
        if _OPEN_MARK in lead:
            is_open = True
        elif _RESOLVED_MARK in lead:
            is_open = False
        else:
            continue
        dates = _DATE_RE.findall(lead.split("Sessions:", 1)[-1]) if "Sessions:" in lead else []
        key = coe_key(e.title)
        if key:
            out[key] = COEState(key=key, title=e.title.strip("*").strip(), open=is_open,
                                first=min(dates) if dates else "",
                                last=max(dates) if dates else "",
                                reports=len(set(dates)) if is_open else 0)
    return out


def _day_file(knowledge: Path, name: str) -> Path | None:
    """The day's .md wherever retention left it: DailyActivity/, then the archive
    (context_health and distillation move it to Archives/; older layouts used
    Archives/DailyActivity/)."""
    for folder in ("DailyActivity", "Archives", "Archives/DailyActivity"):
        path = knowledge / folder / name
        if path.is_file():
            return path
    return None


def _undistilled_sidecars(workspace: Path) -> list[Path]:
    """JSONL sidecars of DailyActivity days whose .md is not marked distilled, by date.

    The .md (live or archived) carries the ``distilled`` flag. A sidecar whose .md is
    gone everywhere is skipped: archives are deleted after 365 days, so a COE whose
    last signal is older than that is no longer shown (stated limit, not a guess).
    """
    from core.daily_activity_writer import parse_frontmatter

    knowledge = workspace / "Knowledge"
    out = []
    for jsonl in sorted((knowledge / "DailyActivity").glob("*.jsonl"), key=lambda p: p.name):
        md = _day_file(knowledge, jsonl.with_suffix(".md").name)
        if md is None:
            continue
        try:
            fm, _ = parse_frontmatter(md.read_text(encoding="utf-8"))
        except OSError:
            continue
        if not (fm or {}).get("distilled"):
            out.append(jsonl)
    return out


def coe_states(workspace: str | Path) -> dict[str, COEState]:
    """Registry baseline with every undistilled day's COE signals folded on top."""
    from core.daily_activity_writer import read_jsonl_sidecar

    ws = Path(workspace)
    memory = ws / ".context" / "MEMORY.md"
    try:
        states = _registry_baseline(memory.read_text(encoding="utf-8"))
    except FileNotFoundError:
        states = {}
    for path in _undistilled_sidecars(ws):
        day = path.stem[:10]
        for rec in read_jsonl_sidecar(path):
            signal = str(rec.get("coe_signal") or "").strip()
            topic = str(rec.get("coe_topic") or "").strip()
            key = coe_key(topic) if topic else ""
            if signal not in _SIGNALS or not key:
                continue
            st = states.get(key)
            if st is None:
                st = states[key] = COEState(key=key, title=topic.strip("*").strip())
            if signal == "candidate":
                if not st.open:
                    st.open, st.first, st.reports = True, st.first or day, 0
                st.reports += 1
            else:
                st.open = False
            st.last = day
    return states


def open_coes(workspace: str | Path) -> list[COEState]:
    """Open COEs, latest signal first. Never raises: an error returns []."""
    try:
        states = coe_states(workspace)
    except Exception:  # noqa: BLE001 — the briefing must never fail on this
        logger.warning("coe_threads: COE state unavailable", exc_info=True)
        return []
    return sorted((s for s in states.values() if s.open),
                  key=lambda s: (s.last, s.key), reverse=True)


def open_coe_titles(workspace: str | Path) -> list[str]:
    """Titles of the open COEs (for the summariser to reuse verbatim)."""
    return [s.title for s in open_coes(workspace)]
