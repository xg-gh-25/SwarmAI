"""Declared overrides: a decision context says which entry its new unit replaces (design
docs/design/2026-10-30-brain-driven.md §2.1).

Only the context that made the new decision may retire the old one: the after-session
review (S7, from the user's own words) or a pipeline run's report (S3, from its own
change). The judge's "contradicts" never does: it pairs a newer observation with an entry
on the same subject, and about one in four such pairs is not a contradiction at all.

A declaration is ``"<8-character id> | <first line>"`` (contracts_v2.OverrideRef). It stands
only if, deterministically:
- the unit cites no external evidence (external content never retires an entry), and its
  project is the one its source fixed
  (the session's own project for S7, the run's for S3; an S3 run must have landed);
- the id names exactly one live entry of the unit's project DDD whose first line matches
  the declared one (so a wrong id cannot retire another entry); or one CDF entry of MEMORY,
  EVOLUTION or the General Brain, which belong to no project: then the new unit must go
  to that same file over its floor, or (General Brain) to that same document
  (``_global_refusal``); a target resting on the user's words needs a unit resting on them
  and the isolated user check;
- that entry was written by CDF (it carries a ``cand:`` marker): CDF never retires what a
  person wrote;
- the new unit was said after the entry: the latest ``observed_at`` of their evidence
  (an unknown time is compared by the entry's own date, by day). A unit said earlier than
  the entry it would replace is stale itself: it is not written (``older_than_existing``).
  A unit said the same day keeps the entry and is written beside it.

``check`` runs while the unit is judged and returns the anchors to retire; ``apply`` retires
them (``governed_edit.withdraw``, status ``overridden``) after the unit is written, once per
(unit, anchor), recorded in ``ingestion_events``; a retirement a concurrent edit made stale
is retried by the daily maintenance (``apply_declared``).

An event (S7 only) declares overrides of its own session's earlier events and gaps, held
by ``check_session_own``; ``apply`` retires the gaps that check approved as ``closed``
(``override_closes``), anything else as ``overridden``. Like ``check``, it trusts the
unit's cited evidence (``cited_external``); what the session read but did not cite is
not looked at, and what it retires is bounded to this session's own CDF entries.

Public: ``Checked``, ``check``, ``check_session_own``, ``apply``, ``apply_declared``,
``APPLIED_EVENT``.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

APPLIED_EVENT = "override_applied"
_DATE = re.compile(r"\b(20\d\d-\d\d-\d\d)\b")
_CAND = re.compile(r"cand:(cand-[0-9a-f]+)\.unit")
MIN_HEAD = 12     # normalized characters of a declared first line that must match


@dataclass
class Checked:
    targets: list[str] = field(default_factory=list)       # CDF anchors to retire
    refused: list[dict] = field(default_factory=list)      # {ref, reason}
    older: bool = False                                     # the unit is older than a target
    closes: list[str] = field(default_factory=list)        # targets retired as closed (gaps)
    # (anchor, entry text) of every-turn / General Brain targets of a unit resting on the
    # user's words, or that rest on them: retired only when the isolated user check finds
    # the user's words replace them (pipeline_v2)
    ask_user: list[tuple[str, str]] = field(default_factory=list)


def _target_basis(conn: sqlite3.Connection, cand: str) -> str:
    """The basis the target entry was written with (unit_basis); unknown counts as the
    strongest, ``user``: an entry is never retired on a guess about what it rested on."""
    try:
        row = conn.execute("SELECT basis FROM unit_basis WHERE unit_id=?", (f"{cand}.unit",)).fetchone()
    except sqlite3.Error:
        row = None
    return str(row[0]) if row and row[0] else "user"


def _global_refusal(kind: str, entry, *, store: str | None, path: str | None,
                    basis: str | None, admitted_by: str | None) -> str | None:
    """Why a unit may not retire this every-turn or General Brain *entry*, or None.

    An every-turn entry leaves only for a unit going to the same file that itself passed
    that file's floor. A General Brain entry leaves for a unit going to its own document
    (the same subject, as a DDD entry leaves for its project) or for one going up into
    MEMORY / EVOLUTION over that floor."""
    up = basis in _EVERY_TURN_BASIS and admitted_by != "repeat"
    if kind == "every_turn":
        if store != EVERY_TURN_TARGETS.get(entry.path):
            return "every_turn_floor"
        return None if up else "every_turn_basis"
    if store == "COMMON_DDD" and path == entry.path:
        return None
    if store in ("MEMORY", "EVOLUTION"):
        return None if up else "every_turn_basis"
    return "every_turn_floor"


def _said(conn: sqlite3.Connection, root_ids) -> str | None:
    ids = [str(r) for r in root_ids or ()]
    if not ids:
        return None
    marks = ",".join("?" * len(ids))
    row = conn.execute(f"SELECT MAX(observed_at) FROM evidence_roots WHERE root_id IN ({marks})",
                       ids).fetchone()
    return str(row[0]) if row and row[0] else None


def _entry_said(conn: sqlite3.Connection, cand: str) -> str | None:
    row = conn.execute("SELECT evidence_root_ids FROM ingestion_inbox WHERE id=?", (cand,)).fetchone()
    if not row:
        return None
    try:
        return _said(conn, json.loads(row[0] or "[]"))
    except (TypeError, ValueError):
        return None


def _first_line(text: str) -> str:
    from .withdrawn import normalize
    lines = [line for line in str(text).strip().splitlines() if line.strip()]
    return normalize(lines[0]) if lines else ""


def _cdf_anchor(workspace: Path, path: str, cand: str) -> str | None:
    from .units import parse_units
    target = Path(path)
    try:
        units = parse_units(target, root=workspace)
    except (OSError, ValueError):
        return None
    unit = next((u for u in units if u.identity == f"cand:{cand}.unit"), None)
    return unit.anchor if unit is not None else None


# The files read in every turn whose CDF entries a declaration may retire, and the General
# Brain (``relations_v2.general_brain_files``). STEERING / USER / TOOLS are the user's own.
EVERY_TURN_TARGETS = {".context/MEMORY.md": "MEMORY", ".context/EVOLUTION.md": "EVOLUTION"}
# What a unit must rest on to take an entry out of the files read in every turn: the same
# floor as writing one there (design 2026-10-30-cdf-agent-decides §2 K1). A correction let
# into EVOLUTION as a repeat (``admitted_by="repeat"``) rests on neither.
_EVERY_TURN_BASIS = frozenset({"user", "measured"})


def _global_entries(workspace: Path) -> list:
    """``(entry, kind)`` of the every-turn files and the General Brain."""
    from .relations_v2 import _entries_of, _general_entries
    out = []
    for rel in EVERY_TURN_TARGETS:
        path = workspace / rel
        if path.is_file():
            out += [(e, "every_turn") for e in _entries_of(path, workspace)]
    return out + [(e, "general") for e in _general_entries(workspace)]


def _said_later(conn: sqlite3.Connection, out: Checked, ref: str, entry_text: str,
                cand: str, new_said: str | None) -> bool:
    """Whether the new unit was said after the entry (module doc); records the refusal."""
    old_said = _entry_said(conn, cand)
    if new_said and old_said:
        if new_said < old_said:
            out.older = True
            out.refused.append({"ref": ref, "reason": "older_than_existing"})
            return False
        if new_said == old_said:
            out.refused.append({"ref": ref, "reason": "not_later"})
            return False
        return True
    dates = _DATE.findall(entry_text)
    if new_said and dates:
        if new_said[:10] < max(dates):
            out.older = True
            out.refused.append({"ref": ref, "reason": "older_than_existing"})
            return False
        if new_said[:10] == max(dates):
            out.refused.append({"ref": ref, "reason": "not_later"})
            return False
    return True


def check(conn: sqlite3.Connection, *, workspace: Path, project: str | None, source: str,
          declared, new_root_ids, cited_external: bool = True,
          home: str | None = None, destination_store: str | None = None,
          basis: str | None = None, admitted_by: str | None = None,
          destination_path: str | None = None) -> Checked:
    """The anchors a unit's declared overrides may retire (see module doc).
    *cited_external*: the unit's own evidence includes external content (a web page, a
    repo file, a peer message): such a unit never retires anything. A unit that rests on
    the agent's own inference may: the agent that made the change in that conversation is
    the one who knows what it made untrue (design 2026-10-30-cdf-agent-decides), and what
    it retires is CDF's own entry, kept in the vault and recorded. *home*: the project the source fixed (the session's own project for
    S7, the run's for S3); a declaration about any other project is refused.

    A declaration naming an entry of MEMORY, EVOLUTION or the General Brain belongs to no
    project: it needs no project or home, but ``_global_refusal`` (*destination_store* and
    *destination_path*: where the new unit goes; *basis*: what it rests on, ``Facts.basis``;
    *admitted_by*: "repeat" for a correction let into EVOLUTION as a repeat), the marker
    and said-later rules, and, for a target that rests on the user's words, a unit that
    does too plus the isolated user check (``Checked.ask_user``; design
    2026-11-02-knowledge-reaches-judgment ②: without this the every-turn files were only
    ever added to). A target the floor refuses that the project's DDD also names is
    resolved there."""
    from core import addressing
    from .contracts_v2 import OVERRIDE_RE, OVERRIDE_SOURCES
    from .withdrawn import normalize
    out = Checked()
    declared = list(declared or ())
    if not declared:
        return out
    if source not in OVERRIDE_SOURCES:
        out.refused = [{"ref": d, "reason": "source"} for d in declared]
        return out
    if cited_external:
        out.refused = [{"ref": d, "reason": "cited_external"} for d in declared]
        return out
    new_said = _said(conn, new_root_ids)
    glob = None
    local: list = []
    floor_refused: dict[str, str] = {}      # ref -> why the global path refused it
    for ref in declared:
        m = OVERRIDE_RE.match(str(ref))
        if not m:
            out.refused.append({"ref": ref, "reason": "malformed"})
            continue
        uid, head = m.group(1), normalize(m.group(2))
        if len(head) < MIN_HEAD:
            out.refused.append({"ref": ref, "reason": "head_too_short"})
            continue
        if glob is None:
            glob = _global_entries(Path(workspace))
        hits = [(e, kind) for e, kind in glob if e.id == uid and normalize(e.head).startswith(head)]
        if not hits:
            local.append((ref, uid, head))
            continue
        if len({e.anchor for e, _ in hits}) != 1:
            out.refused.append({"ref": ref, "reason": "ambiguous"})
            continue
        entry, kind = hits[0]
        why = _global_refusal(kind, entry, store=destination_store, path=destination_path,
                              basis=basis, admitted_by=admitted_by)
        if why is not None:
            if project:
                local.append((ref, uid, head))      # the same text may be a DDD entry too
                floor_refused[ref] = why
            else:
                out.refused.append({"ref": ref, "reason": why})
            continue
        cand = _CAND.search(entry.text)
        if cand is None:
            out.refused.append({"ref": ref, "reason": "human_written"})
            continue
        rests_on_user = _target_basis(conn, cand.group(1)) == "user"
        if rests_on_user and basis != "user":
            out.refused.append({"ref": ref, "reason": "weaker_than_target"})
            continue
        if not _said_later(conn, out, ref, entry.text, cand.group(1), new_said):
            continue
        if entry.anchor not in out.targets:
            out.targets.append(entry.anchor)
            if rests_on_user or basis == "user":
                # a unit resting on a user quote is asked whether those words replace the
                # entry, whatever the entry rests on: injected text can pair a real,
                # unrelated quote with an override
                out.ask_user.append((entry.anchor, entry.text))
    if not local:
        return out
    if not project:
        out.refused += [{"ref": ref, "reason": "no_project"} for ref, _u, _h in local]
        return out
    if not home or home != project:
        out.refused += [{"ref": ref, "reason": "outside_home_project"} for ref, _u, _h in local]
        return out
    for ref, uid, head in local:
        units = [u for u in addressing.matches(project, uid) if u.store == "doc"
                 and _first_line(u.text).startswith(head)]
        if len(units) != 1:
            out.refused.append({"ref": ref, "reason": floor_refused.get(ref)
                                or ("not_found" if not units else "ambiguous")})
            continue
        unit = units[0]
        cand = _CAND.search(unit.text)
        if cand is None:
            out.refused.append({"ref": ref, "reason": "human_written"})
            continue
        anchor = _cdf_anchor(workspace, unit.path, cand.group(1))
        if anchor is None:
            out.refused.append({"ref": ref, "reason": "not_found"})
            continue
        if not _said_later(conn, out, ref, unit.text, cand.group(1), new_said):
            continue
        if anchor not in out.targets:
            out.targets.append(anchor)
    return out


def is_open_gap(entry) -> bool:
    """An entry of IMPROVEMENT "What to Watch For" with a ``gap kind:``, not ticked done."""
    from .gaps import SECTION, _PREFIX
    prefix = _PREFIX.match(entry.text.lstrip())
    return (entry.path.endswith("IMPROVEMENT.md") and entry.section == SECTION
            and "gap kind:" in entry.text and bool(prefix) and prefix.group(1).lower() != "x")


def check_session_own(conn: sqlite3.Connection, *, workspace: Path, project: str | None,
                      source: str, declared, session_ref: str | None,
                      cited_external: bool = True) -> Checked:
    """The anchors an event's declared overrides may retire: an event that this same
    session's earlier review wrote (design 2026-10-31-extraction-at-task-end E3), and an
    open CDF-written gap of the project, whichever session opened it (it is closed).

    The later review of a session completes what an earlier one recorded half done
    ("root cause found" -> "fixed"), so it is the context that may replace it: by id and
    first line, a live CDF-written TIMELINE event or IMPROVEMENT gap whose candidate's
    ``source_ref`` is this session. Another session's event, a person's entry, a
    cognition entry, a gap already closed, or a declaration from any source but S7
    retires nothing. A later
    pass is later by construction (its entry is not written yet), so no time is compared.
    """
    from . import relations_v2
    out = Checked()
    declared = list(declared or ())
    if not declared:
        return out
    if source != "S7" or not session_ref:
        out.refused = [{"ref": d, "reason": "source"} for d in declared]
        return out
    if cited_external:
        out.refused = [{"ref": d, "reason": "cited_external"} for d in declared]
        return out
    entries = relations_v2.timeline_entries(workspace) + relations_v2.pool(workspace, project)
    for ref in declared:
        entry, why = relations_v2.resolve(ref, entries)
        if entry is None:
            out.refused.append({"ref": ref, "reason": why})
            continue
        cand = _CAND.search(entry.text)
        if cand is None:
            out.refused.append({"ref": ref, "reason": "human_written"})
            continue
        row = conn.execute("SELECT source_ref, unit_kind, COALESCE(source_v2, source) "
                           "FROM ingestion_inbox WHERE id=?", (cand.group(1),)).fetchone()
        if row is not None and row[1] == "gap" and is_open_gap(entry):
            # a gap closes when an event says it is done, whichever session opened it:
            # the work that finishes a gap is usually done in a later conversation. Only
            # an open, CDF-written gap of this project (the pool holds no other's)
            if entry.anchor not in out.targets:
                out.targets.append(entry.anchor)
                out.closes.append(entry.anchor)
            continue
        if row is not None and row[1] == "gap":
            # ticked done, or kept outside a project's IMPROVEMENT (the General Brain)
            out.refused.append({"ref": ref, "reason": "not_an_open_gap"})
            continue
        # an event or anything else: the review's own candidate only (an S1 unit the user
        # dictated may carry the session id as its ref and is never this review's to retire)
        if row is None or row[0] != session_ref or row[2] != "S7":
            out.refused.append({"ref": ref, "reason": "other_session"})
            continue
        if row[1] not in ("event", "gap"):
            out.refused.append({"ref": ref, "reason": "kind"})
            continue
        if entry.anchor not in out.targets:
            out.targets.append(entry.anchor)
            if row[1] == "gap":
                out.closes.append(entry.anchor)
    return out


_RECORD = """INSERT INTO ingestion_events(at,kind,detail) SELECT ?,?,?
    WHERE NOT EXISTS (SELECT 1 FROM ingestion_events WHERE kind = ?
      AND json_extract(detail, '$.unit_id') = ? AND json_extract(detail, '$.anchor') = ?)"""
_LOCK = threading.Lock()


def _recorded(conn: sqlite3.Connection) -> set[tuple[str, str]]:
    rows = conn.execute("SELECT json_extract(detail,'$.unit_id'), json_extract(detail,'$.anchor') "
                        "FROM ingestion_events WHERE kind = ?", (APPLIED_EVENT,))
    return {(str(a), str(b)) for a, b in rows}


def apply(conn: sqlite3.Connection, unit_id: str | None = None) -> int:
    """Retire the entries written units declared they override; return how many were
    retired. Each (unit, anchor) result is recorded once; a stale one is retried later."""
    with _LOCK:
        return _apply(conn, unit_id)


def _apply(conn: sqlite3.Connection, unit_id: str | None) -> int:
    from .governed_edit import StaleRevision, withdraw
    sql = ("SELECT u.unit_id, u.decision, i.payload FROM ingestion_units u "
           "JOIN ingestion_inbox i ON i.id = u.candidate_id WHERE u.state = 'written' "
           "AND u.decision LIKE '%\"overrides\"%'" + (" AND u.unit_id = ?" if unit_id else ""))
    rows = conn.execute(sql, (unit_id,) if unit_id else ()).fetchall()
    done = _recorded(conn) if rows else set()
    retired = 0
    for uid, decision, payload in rows:
        try:
            vector = json.loads(decision or "{}").get("version_vector", {})
            targets = vector.get("overrides") or []
            closes = set(vector.get("override_closes") or [])
            title = str(json.loads(payload or "{}").get("title") or "")
        except (TypeError, ValueError):
            continue
        for anchor in targets:
            if (str(uid), anchor) in done:
                continue
            # a gap its own session finished later (check_session_own) leaves as closed,
            # history a recurrence may repeat; anything else as overridden
            gap = anchor in closes
            try:
                withdraw(anchor, (f"closed by: {title}" if gap else f"overridden by: {title}")[:500]
                         or "overridden", actor="cdf:override",
                         status="closed" if gap else "overridden")
                result = "withdrawn"
            except StaleRevision:
                logger.info("CDF override of %s by %s is stale; retried later", anchor, uid)
                continue
            except Exception as exc:  # noqa: BLE001 -- recorded, never retried forever
                logger.warning("CDF override of %s by %s refused", anchor, uid, exc_info=True)
                result = f"error:{type(exc).__name__}"
            retired += result == "withdrawn"
            detail = json.dumps({"unit_id": uid, "anchor": anchor, "result": result},
                                ensure_ascii=False, sort_keys=True)
            conn.execute(_RECORD, (datetime.now(timezone.utc).isoformat(), APPLIED_EVENT, detail,
                                   APPLIED_EVENT, uid, anchor))
            conn.commit()
    return retired


def apply_declared(conn: sqlite3.Connection) -> int:
    """The daily maintenance pass: every written unit's declared overrides not yet applied."""
    return apply(conn)
