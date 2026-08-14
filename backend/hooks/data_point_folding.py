"""Data-point family folding for EVOLUTION.md's `## Corrections Captured`.

The `## Corrections Captured` section is a NARRATIVE region: each `### CLASS ...`
family accumulates prose sub-bullets (RECURRENCE / CONTAINMENT DATA-POINT records)
append-only. The existing `evolution_maintenance_hook` lifecycle (deprecate/prune)
only reaches STRUCTURED `### Exxx | date` entries with Status/Usage fields — it is
structurally blind to this narrative region, so it grows unbounded (the landfill).

This module gives the narrative region the same self-pruning ability, but
type-aware so it NEVER eats load-bearing judgment:

- Only sub-bullets whose lead marker is a FOLDABLE type are candidates:
  ``RECURRENCE DATA-POINT`` / ``CONTAINMENT DATA POINT`` / ``CONTAINMENT DATA-POINT``.
- PROTECTED lead markers are never folded: ``METHOD FIX``, ``CONTAINMENT NOTE``,
  ``VALIDATING EVIDENCE``, ``REGRESSION``, ``POSITIVE FOLLOW-ON`` — plus the
  ``### CLASS`` header body itself and any ``META-CORRECTION`` header.
- Per family, when foldable candidates exceed ``CAP`` (default 4), keep:
  anchor (first) + capstone (explicit TEXT marker, NOT last/date — Gate-1 F2)
  + the most-recent-2 by date. Everything else is moved to the archive file and
  replaced by ONE summary line preserving each folded point's run-id + date.
- Idempotent (Gate-1 F3): a ``<!-- folded ... -->`` marker records which run-ids
  were archived; a re-run folds only NEW foldable candidates that push the family
  back over CAP, never re-folding the kept set.
- Fail-safe (Gate-1 F4): pure function; on ANY parse ambiguity for a family it
  leaves that family UNTOUCHED (returns it verbatim). No backup copy is written
  (git history + the forward-append archive ARE the recovery paths — STEERING #2
  bans a disaster-recovery-copy reflex).

Public API:
- ``fold_corrections_section(content, cap=4)`` -> ``FoldResult`` — pure, no IO.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# ── Section + family boundaries ────────────────────────────────────────────
_CORRECTIONS_HEADER = "## Corrections Captured"

# A CLASS/family header inside the Corrections section.
_FAMILY_HEADER_RE = re.compile(r"^### .+$", re.MULTILINE)

# ── Sub-bullet typing ──────────────────────────────────────────────────────
# A sub-bullet starts at line-start with `- **` OR `  - **` (indent varies —
# real data mixes top-level and 2-space-indented data-points, so we key on the
# TYPE MARKER text, never on indentation).
_BULLET_RE = re.compile(r"^([ \t]*)- \*\*(.+?)(?:\*\*|:)", re.MULTILINE)

# Foldable lead markers (case-insensitive, matched at the start of the bold lead).
_FOLDABLE_MARKERS = (
    "RECURRENCE DATA-POINT",
    "CONTAINMENT DATA POINT",
    "CONTAINMENT DATA-POINT",
    "DATA-POINT",  # bare "DATA-POINT —" family records
)

# Protected lead markers — NEVER folded even if numerous.
_PROTECTED_MARKERS = (
    "METHOD FIX",
    "CONTAINMENT NOTE",
    "VALIDATING EVIDENCE",
    "REGRESSION",
    "POSITIVE FOLLOW-ON",
)

# Explicit capstone text marker (Gate-1 F2): the family's closing lesson is
# labelled in prose, e.g. "the CAPSTONE that finally CLOSED the class".
_CAPSTONE_RE = re.compile(r"CAPSTONE", re.IGNORECASE)

# run-id + date extraction for the summary line (preserve traceability).
_RUNID_RE = re.compile(r"run_[0-9a-f]{6,}")
_DATE_RE = re.compile(r"(20\d{2}-\d{2}-\d{2})|(\b\d{2}-\d{2}\b)")

# Idempotency marker written in place of the folded set.
_FOLD_MARKER_RE = re.compile(r"<!-- folded archived=([^>]*) -->")

_SENTINEL_OLD = "0000-00-00"  # NO-DATE sorts oldest (Gate-1 F2 determinism)


@dataclass
class _Bullet:
    """One parsed sub-bullet within a family."""
    text: str            # full verbatim block (bullet + any continuation lines)
    lead: str            # the bold lead marker text
    is_foldable: bool
    is_protected: bool
    is_capstone: bool
    date_key: str        # normalized YYYY-MM-DD or sentinel for sort
    owner_run_id: str | None  # THIS bullet's own run-id (from its lead), for
                              # the idempotency marker — NOT ids merely cited in
                              # the prose (which would poison the archived-set).
    run_ids: tuple[str, ...]  # ALL run-ids in the block (display/traceability only)


@dataclass
class FoldResult:
    """Outcome of a fold pass (pure — caller decides whether to write)."""
    new_content: str
    archived_blocks: list[str] = field(default_factory=list)
    families_folded: int = 0
    bullets_archived: int = 0
    changed: bool = False


def _normalize_date(raw: str) -> str:
    """Normalize a date token to YYYY-MM-DD for deterministic sort.

    A bare MM-DD (e.g. '07-10') is assumed current-era; without a year it can't
    beat a full date, so it's given a low-but-nonzero year sentinel that still
    orders within itself. NO date at all → oldest sentinel (never picked as
    'recent', never picked as capstone unless it carries the text marker)."""
    m = _DATE_RE.search(raw)
    if not m:
        return _SENTINEL_OLD
    if m.group(1):            # full YYYY-MM-DD
        return m.group(1)
    # bare MM-DD → synthetic old-ish year so full dates always win, but they
    # still sort consistently among themselves.
    return "0001-" + m.group(2)


def _classify_lead(lead: str) -> tuple[bool, bool]:
    """Return (is_foldable, is_protected) for a bold lead marker."""
    up = lead.upper()
    is_protected = any(up.startswith(p) for p in _PROTECTED_MARKERS)
    if is_protected:
        return (False, True)
    is_foldable = any(up.startswith(f) for f in _FOLDABLE_MARKERS)
    return (is_foldable, False)


def _split_family_bullets(family_body: str) -> list[_Bullet]:
    """Split a family's body into sub-bullet blocks (verbatim, incl. continuations)."""
    matches = list(_BULLET_RE.finditer(family_body))
    bullets: list[_Bullet] = []
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(family_body)
        block = family_body[start:end]
        lead = m.group(2).strip()
        is_foldable, is_protected = _classify_lead(lead)
        # owner run-id = the FIRST run-id in the bullet's LEAD LINE only (the
        # `- **RECURRENCE DATA-POINT (date, run_xxx, ...)` header). Run-ids that
        # appear only later in the prose are CROSS-REFERENCES to other bullets
        # and must not be treated as this bullet's identity (Gate-2 MED #3).
        lead_line = block.split("\n", 1)[0]
        lead_ids = _RUNID_RE.findall(lead_line)
        all_ids = tuple(dict.fromkeys(_RUNID_RE.findall(block)))
        bullets.append(_Bullet(
            text=block,
            lead=lead,
            is_foldable=is_foldable,
            is_protected=is_protected,
            is_capstone=bool(_CAPSTONE_RE.search(block)),
            date_key=_normalize_date(block[:200]),
            owner_run_id=(lead_ids[0] if lead_ids else None),
            run_ids=all_ids,
        ))
    return bullets


def _already_folded_ids(family_body: str) -> set[str]:
    """run-ids already recorded as archived by a prior fold (idempotency)."""
    ids: set[str] = set()
    for m in _FOLD_MARKER_RE.finditer(family_body):
        ids.update(_RUNID_RE.findall(m.group(1)))
    return ids


def _summary_line(folded: list[_Bullet]) -> str:
    """One line preserving traceability of every folded point (run-id + date).

    The visible refs show each folded point's OWN run-id (falling back to a
    cited id only for display). The machine marker `archived=` records ONLY
    owner run-ids — cross-referenced ids must never enter it, or a later live
    bullet keyed to a cited id would be wrongly treated as already-archived
    (Gate-2 MED #3)."""
    refs = []
    for b in folded:
        rid = b.owner_run_id or (b.run_ids[0] if b.run_ids else "no-run-id")
        date = b.date_key if not b.date_key.startswith(("0000", "0001")) else "?"
        refs.append(f"{rid}({date})")
    archived_ids = " ".join(b.owner_run_id for b in folded if b.owner_run_id)
    return (
        f"  - **[{len(folded)} earlier data-points folded to archive]** — "
        f"traceability preserved (full text in the .context/EVOLUTION-archive-*.md "
        f"monthly shards): "
        f"{', '.join(refs)}. "
        f"<!-- folded archived={archived_ids} -->\n"
    )


def _fold_one_family(header: str, body: str, cap: int,
                     archived_out: list[str]) -> tuple[str, int]:
    """Fold a single family's body. Returns (new_body, n_archived).

    Fail-safe: on any structural ambiguity, returns body unchanged (0 archived)."""
    try:
        bullets = _split_family_bullets(body)
    except Exception:
        return body, 0  # fail-safe: never eat data on parse trouble

    already = _already_folded_ids(body)

    # Work in ABSOLUTE indices into `bullets` throughout (Gate-2 CRITICAL #1):
    # never key the keep/fold sets on bullet TEXT — two byte-identical bullets
    # would collide and a KEPT one could be silently dropped + never archived.
    # foldable_idx = positions in `bullets` that are foldable AND not already
    # archived in a prior pass (idempotency, keyed on the bullet's OWN run-id).
    foldable_idx = [
        i for i, b in enumerate(bullets)
        if b.is_foldable and not (b.owner_run_id and b.owner_run_id in already)
    ]
    if len(foldable_idx) <= cap:
        return body, 0  # nothing to do (idempotent no-op once at/below cap)

    # Choose which foldable bullets to KEEP (as absolute indices):
    #   anchor (first foldable) + capstone (text-marker) + most-recent by date.
    keep: set[int] = set()
    keep.add(foldable_idx[0])                                  # anchor

    capstone_idxs = [i for i in foldable_idx if bullets[i].is_capstone]
    if capstone_idxs:
        # newest capstone by date, tie-broken by reading order (later wins)
        cap_i = max(capstone_idxs, key=lambda i: (bullets[i].date_key, i))
        keep.add(cap_i)

    by_recent = sorted(
        foldable_idx, key=lambda i: (bullets[i].date_key, i), reverse=True,
    )
    for i in by_recent:
        if len(keep) >= cap:
            break
        keep.add(i)

    fold_idx = [i for i in foldable_idx if i not in keep]
    if not fold_idx:
        return body, 0

    folded = [bullets[i] for i in fold_idx]
    fold_idx_set = set(fold_idx)
    for b in folded:
        archived_out.append(f"### (folded from: {header.strip()})\n{b.text}")

    # Rebuild by INDEX: emit every bullet except folded ones; drop the whole
    # folded set and insert ONE summary line at the FIRST folded position.
    new_parts: list[str] = []
    summary_inserted = False
    first_bullet_start = body.find(bullets[0].text) if bullets else -1
    if first_bullet_start > 0:
        new_parts.append(body[:first_bullet_start])  # preamble before bullet 0

    for i, b in enumerate(bullets):
        if i in fold_idx_set:
            if not summary_inserted:
                new_parts.append(_summary_line(folded))
                summary_inserted = True
            # else: drop (represented by the single summary line)
        else:
            new_parts.append(b.text)

    return "".join(new_parts), len(folded)


def fold_corrections_section(content: str, cap: int = 4) -> FoldResult:
    """Fold the `## Corrections Captured` section. Pure — no IO.

    Returns a FoldResult with the rewritten content + archived blocks. If the
    section is absent or nothing exceeds cap, returns changed=False and the
    original content verbatim (fail-safe)."""
    sec_start = content.find(_CORRECTIONS_HEADER)
    if sec_start == -1:
        return FoldResult(new_content=content)

    # Section spans until the next top-level `## ` header (or EOF).
    body_start = sec_start + len(_CORRECTIONS_HEADER)
    next_sec = re.search(r"^## ", content[body_start:], re.MULTILINE)
    sec_end = body_start + next_sec.start() if next_sec else len(content)
    section = content[body_start:sec_end]

    # Split the section into families by `### ` headers.
    fam_headers = list(_FAMILY_HEADER_RE.finditer(section))
    if not fam_headers:
        return FoldResult(new_content=content)

    archived: list[str] = []
    families_folded = 0
    total_archived = 0
    rebuilt: list[str] = [section[:fam_headers[0].start()]]  # preamble

    for i, hm in enumerate(fam_headers):
        h_start = hm.start()
        h_line_end = section.find("\n", h_start)
        h_line_end = h_line_end + 1 if h_line_end != -1 else len(section)
        fam_end = fam_headers[i + 1].start() if i + 1 < len(fam_headers) else len(section)
        header_line = section[h_start:h_line_end]
        fam_body = section[h_line_end:fam_end]

        new_body, n = _fold_one_family(header_line, fam_body, cap, archived)
        rebuilt.append(header_line)
        rebuilt.append(new_body)
        if n:
            families_folded += 1
            total_archived += n

    if total_archived == 0:
        return FoldResult(new_content=content)  # no change

    new_section = "".join(rebuilt)
    new_content = content[:body_start] + new_section + content[sec_end:]
    return FoldResult(
        new_content=new_content,
        archived_blocks=archived,
        families_folded=families_folded,
        bullets_archived=total_archived,
        changed=True,
    )
