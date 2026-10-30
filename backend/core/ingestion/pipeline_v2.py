"""CDF v2 unit pipeline, evidence reinforcement, and parked-unit requeueing.

The source unit is already validated and its trust is already derived when this module
runs.  This module never rewrites semantic fields: it only joins them for judging and
routes the original values to the writer.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from . import api_v2, contracts_v2, withdrawn
from .card3 import retrieve
from .contracts import Card0Answer, UnitDecision
from .contracts_v2 import (
    PARKED_DEFER_REASONS,
    snapshot_project,
    CognitionUnit,
    DestinationV2,
    EventUnit,
    GapUnit,
    ReferenceUnit,
    SOURCE_UNIT,
    SourceUnit,
    semantic_urls_have_credentials,
)
from .llm import JudgeResult
from .normalize import ClaimGroup
from .prompts import UntypedOutput

logger = logging.getLogger(__name__)

_H2 = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)


@dataclass(frozen=True)
class ReinforcementResult:
    """The deterministic claim-support outcome for one duplicate/refuting hit."""

    reinforced: bool
    novel_root_ids: tuple[str, ...]


AGENT_SELF_STORES = frozenset({"EVOLUTION", "MEMORY"})
USER_CONTEXT_STORES = frozenset({"STEERING", "USER", "TOOLS"})


def _volatile(text: str) -> bool:
    """The DDD D4 rule (rules/ddd.py) for every cognition unit: a count or size that
    drifts, unless the text says which version it describes."""
    from .rules.ddd import _VERSIONED, _VOLATILE
    return bool(_VOLATILE.search(text)) and not _VERSIONED.search(text)


def _grounding(anchors: list[str]) -> str:
    """The evidence the agent's own cognition may rest on (session review §9): the
    user-written text of cited user messages, and ``measured <path> = <value>`` for cited
    measured results. Empty when the unit cites neither."""
    import sqlite3
    from core import session_inputs
    from core.ingestion.sources import s7_review
    ids = [a[len("event:"):] for a in anchors if a.startswith("event:")]
    path = session_inputs._db_path()
    if not ids or not path.exists():
        return ""
    marks = ",".join("?" * len(ids))
    texts: list[str] = []
    try:
        with sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True, timeout=1.0) as conn:
            rows = conn.execute(
                f"SELECT event_id, kind, ref FROM session_inputs WHERE event_id IN ({marks}) "
                f"AND ((kind='user_message' AND trust='user') OR "
                f"(kind='measured_field' AND trust='measured'))", ids).fetchall()
            for event_id, kind, ref in rows:
                if kind == "user_message":
                    texts.append(session_inputs.user_authored_text(event_id) or "")
                    continue
                found = conn.execute("SELECT id, content FROM messages WHERE id=?",
                                     (s7_review.message_id_of(ref),)).fetchone()
                if found is None:
                    continue
                content = found[1]
                try:
                    content = json.loads(content) if isinstance(content, str) and content[:1] in "[{" else content
                except ValueError:
                    pass
                value = s7_review.measured_from_ref(ref, {"id": found[0], "content": content})
                if value:
                    texts.append(f"measured {value[0]} = {json.dumps(value[1])}")
    except sqlite3.Error:
        return ""
    return "\n\n".join(t for t in texts if t.strip())

def _get(row: Any, key: str, default: Any = None) -> Any:
    if isinstance(row, Mapping):
        return row.get(key, default)
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return getattr(row, key, default)


def _json(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return default
    return value


def _unwrap(value: Any, vectors: list[dict[str, Any]]) -> Any:
    if isinstance(value, JudgeResult):
        vectors.append(value.vector)
        return value.value
    return value


def _unit_text(unit: SourceUnit) -> str:
    """Join complete semantic fields without clipping or rewriting their contents."""
    if isinstance(unit, CognitionUnit):
        fields = (unit.title, unit.what, unit.how, unit.why, unit.applicability,
                  unit.rejected_alternatives)
    elif isinstance(unit, EventUnit):
        fields = (unit.title, unit.what, unit.impact, unit.outcome, unit.follow_up)
    elif isinstance(unit, GapUnit):
        fields = (unit.title, unit.what, unit.why, unit.proposed_next)
    else:
        fields = (unit.title, unit.summary, unit.body, unit.source_url)
    return "\n".join(value for value in fields if value is not None)


def _group(unit: SourceUnit, projects: Iterable[str]) -> ClaimGroup:
    why = getattr(unit, "why", None)
    how = getattr(unit, "how", None)
    return ClaimGroup(
        slot="unit",
        text=_unit_text(unit),
        title=unit.title,
        why=why,
        how=how,
        evidence=json.dumps(unit.evidence, ensure_ascii=False),
        type_hint=getattr(unit, "type", unit.kind),
        project_candidates=tuple(dict.fromkeys(projects)),
    )


def _resolved(row: Any) -> api_v2.ResolvedEvidence:
    envelope = _json(_get(row, "envelope"), {})
    roots = tuple(_json(_get(row, "evidence_root_ids"), envelope.get("root_ids", [])))
    lineage = tuple(envelope.get("lineage_root_ids", roots))
    # an envelope from before cdf-fixing-spec C1 holds one "tainted" (the read set's);
    # what the unit itself cites is then read from its lineage's current trust
    read_external = bool(envelope.get("read_external", envelope.get("tainted", False)))
    if "cited_external" in envelope:
        cited_external = bool(envelope["cited_external"])
    else:
        from .provenance import lineage_external
        cited_external = lineage_external(lineage)
    return api_v2.ResolvedEvidence(
        root_ids=roots,
        lineage_root_ids=lineage,
        strength=envelope.get("strength", envelope.get("evidence", "inferred")),
        read_external=read_external,
        cited_external=cited_external,
        unresolved=tuple(envelope.get("unresolved", ())),
    )


def _decision(unit_id: str, decision: str, action: str, reason: str, *,
              vectors: list[dict[str, Any]], **values: Any) -> UnitDecision:
    hashes = [str(vector.get("prompt_hash", "")) for vector in vectors]
    vector = dict(vectors[-1]) if vectors else {}
    vector["prompt_hashes"] = hashes
    if hashes:
        vector["prompt_hash"] = hashlib.sha256("".join(hashes).encode("ascii")).hexdigest()
    # model_construct permits the v2 card-1 variants while retaining the frozen
    # UnitDecision return type and serialization used by the worker.
    return UnitDecision.model_construct(
        unit_id=unit_id,
        decision=decision,
        action=action,
        reason=reason,
        version_vector=vector,
        destination=values.get("destination"),
        card0=values.get("card0"),
        card1=values.get("card1"),
        card2=values.get("card2"),
        rules=values.get("rules", []),
        card3=values.get("card3"),
        hit_judgments=values.get("hit_judgments", []),
        card3b=values.get("card3b"),
        reroutes=values.get("reroutes", 0),
    )


def _card0(unit: SourceUnit, row: Any) -> Card0Answer:
    """Apply v2 card 0; noisy checks use only envelope/non-semantic data."""
    envelope = json.dumps(_json(_get(row, "envelope"), {}), ensure_ascii=False)
    if re.fullmatch(r"\s*(?:session|run)[-_ ]?id\s*[:=].*", envelope, re.I):
        return Card0Answer(passed=False, reason="noisy")
    text = _unit_text(unit)
    if not text.strip():
        return Card0Answer(passed=False, reason="noisy")
    if not isinstance(unit, ReferenceUnit) and ("<!--" in text or "-->" in text):
        # an HTML comment marker inside an entry ends or opens the metadata comment of
        # the line it lands on; the prompt form then cuts the text after it
        return Card0Answer(passed=False, reason="unrenderable")
    if isinstance(unit, ReferenceUnit) and len(text.encode("utf-8")) > 16_384:
        return Card0Answer(passed=False, reason="too_large_incomplete")
    if isinstance(unit, ReferenceUnit) and semantic_urls_have_credentials(unit.summary, unit.body):
        return Card0Answer(passed=False, reason="credentialed_url")
    return Card0Answer(passed=True)


def _ddd_exists(root: Path, project: str | None) -> bool:
    if not project:
        return False
    try:
        from core.ddd_paths import ddd_path
        from core.project_registry import DDD_CANONICAL_DOCS
        return any(ddd_path(root / "Projects" / project, name).is_file()
                   for name in DDD_CANONICAL_DOCS)
    except (OSError, ValueError):
        return False


def _section_exists(root: Path, destination: DestinationV2) -> bool:
    if destination.store in {"KNOWLEDGE", "COMMON_DDD", "TIMELINE"}:
        return True
    target = root / destination.relative_path
    if not target.is_file():
        return False
    if destination.section is None:
        return True
    headings = {" ".join(match.group(1).split())
                for match in _H2.finditer(target.read_text(encoding="utf-8"))}
    return " ".join(destination.section.split()) in headings


def _is_hive_lesson(row: Any) -> bool:
    from .contracts_v2 import HIVE_REF_PREFIX
    return (str(_get(row, "source_v2", _get(row, "source", ""))) == "S6"
            and str(_get(row, "source_ref", "")).startswith(HIVE_REF_PREFIX))


def _event_key(unit: EventUnit, roots: tuple[str, ...]) -> str:
    day = unit.when[:10]
    title = " ".join(unit.title.casefold().split())
    material = json.dumps([sorted(roots), day, title], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _root_rows(conn: sqlite3.Connection) -> dict[str, tuple[str, tuple[str, ...], str]]:
    rows = conn.execute("SELECT root_id,locator,parent_root_ids,trust FROM evidence_roots").fetchall()
    return {str(row[0]): (str(row[1]), tuple(_json(row[2], [])), str(row[3])) for row in rows}


def _descends_from_target(root_id: str, target_anchor: str,
                          roots: dict[str, tuple[str, tuple[str, ...], str]]) -> bool:
    targets = {item for item, (locator, _, _) in roots.items() if locator == target_anchor}
    stack = [root_id]
    seen: set[str] = set()
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        if current in targets:
            return True
        row = roots.get(current)
        if row:
            stack.extend(row[1])
    return False


def reinforce_or_reject(target_anchor: str, resolved: api_v2.ResolvedEvidence, *,
                         source_unit_id: str = "", polarity: str = "support",
                         conn: sqlite3.Connection | None = None) -> ReinforcementResult:
    """Persist only independent novel roots for a qualified Cognition duplicate/refute."""
    owned = conn is None
    if conn is None:
        from .store import connect_rw
        conn = connect_rw()
    try:
        existing = {str(row[0]) for row in conn.execute(
            "SELECT root_id FROM claim_support WHERE claim_anchor=? AND inactive=0",
            (target_anchor,),
        )}
        roots = _root_rows(conn)
        novel = tuple(sorted(root_id for root_id in set(resolved.root_ids)
                             if root_id not in existing
                             and not _descends_from_target(root_id, target_anchor, roots)))
        now = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()
        weight = {"user": 1.0, "measured": 0.8, "inferred": 0.5, "external": 0.3}
        for root_id in novel:
            trust = roots.get(root_id, ("", (), "external"))[2]
            conn.execute(
                """INSERT INTO claim_support(claim_anchor,root_id,source_unit_id,weight,polarity,created_at)
                   VALUES(?,?,?,?,?,?) ON CONFLICT(claim_anchor,root_id) DO NOTHING""",
                (target_anchor, root_id, source_unit_id, weight.get(trust, 0.3), polarity, now),
            )
        conn.commit()
        return ReinforcementResult(bool(novel), novel)
    finally:
        if owned:
            conn.close()


# A "supersedes" edge only links two entries: nothing is hidden for it (an entry another one
# replaces leaves the file by a declared override, overrides.py). A unit from a model may name
# a related entry, recorded as derived_from: measured on 2026-10-30, 10 of the 16 supersedes
# edges from model judgement paired the wrong entries. No source may declare one since the
# drift-fix path (the only S4 producer of one) was removed.
_SUPERSEDES_SOURCES: frozenset[str] = frozenset()


def _check_overrides(unit, row, root, destination, corpus_project, resolved, home=None,
                     supported=False):
    """``overrides.check`` for a cognition unit that declares overrides, else None. An
    unreadable store refuses every declaration (logged): nothing is retired on a guess.
    *supported*: the unit is shown by the user's words or a measurement (run_unit_v2)."""
    declared = getattr(unit, "overrides", None)
    if not isinstance(unit, CognitionUnit) or not declared:
        return None
    from . import overrides
    from .api import connect_ro
    source = str(_get(row, "source_v2", _get(row, "source", "")))
    project = getattr(destination, "project", None) or corpus_project
    try:
        conn = connect_ro()
    except Exception:  # noqa: BLE001
        logger.warning("cdf: overrides not checked (store unreadable)", exc_info=True)
        return overrides.Checked(refused=[{"ref": d, "reason": "store_unreadable"} for d in declared])
    try:
        return overrides.check(conn, workspace=Path(root), project=project, source=source,
                               declared=declared, new_root_ids=getattr(resolved, "root_ids", ()),
                               cited_external=bool(getattr(resolved, "cited_external", True)),
                               supported=supported, home=home)
    finally:
        conn.close()


def _withdrawn_records() -> list:
    """The completed withdrawals (``withdrawn.records``); none when the store cannot be read,
    logged (the unit is then judged without them, as before this check existed)."""
    from .api import connect_ro
    try:
        conn = connect_ro()
    except Exception:  # noqa: BLE001 -- judged without them, said in the log
        logger.warning("cdf: withdrawn records unreadable; judging without them", exc_info=True)
        return []
    try:
        return withdrawn.records(conn)
    except Exception:  # noqa: BLE001
        logger.warning("cdf: withdrawn records unreadable; judging without them", exc_info=True)
        return []
    finally:
        conn.close()


def _relation_hints(unit: SourceUnit, source: str) -> list[dict[str, str]]:
    hint = unit.relation_hint or {}
    kind, target = hint.get("kind"), hint.get("target")
    if kind not in {"supersedes", "derived_from"} or not target:
        return []
    if kind == "supersedes" and source not in _SUPERSEDES_SOURCES:
        kind = "derived_from"
    return [{"predicate": kind, "object_anchor": str(target)}]


def _now_iso() -> str:
    """The ISO time every other writer stamps: CURRENT_TIMESTAMP ('2026-10-29 11:09')
    sorts before every ISO time of the same day, so comparisons call it hours old."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def requeue_parked(reason: str, key: str) -> int:
    """Requeue matching parked rows without consuming an attempt."""
    if reason not in PARKED_DEFER_REASONS:
        return 0
    from .store import connect_rw
    conn = connect_rw()
    try:
        if reason == "unknown_domain":
            where = "json_extract(claim_group,'$.domain')=?"
            params: tuple[Any, ...] = (key,)
        else:
            try:
                awaited = json.loads(key)
                relative_path = str(awaited["relative_path"])
                section = str(awaited["section"])
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                return 0
            where = ("json_extract(destination,'$.relative_path')=? AND "
                     "json_extract(destination,'$.section')=?")
            params = (relative_path, section)
        ids = [str(row[0]) for row in conn.execute(
            f"SELECT unit_id FROM ingestion_units WHERE state='deferred' "
            f"AND defer_reason=? AND {where}",
            (reason, *params),
        )]
        if not ids:
            return 0
        marks = ",".join("?" for _ in ids)
        cursor = conn.execute(
            f"""UPDATE ingestion_units SET state='queued',defer_reason=NULL,reason=NULL,
                       next_attempt_at=NULL,updated_at=?
                WHERE unit_id IN ({marks})""",
            (_now_iso(), *ids),
        )
        conn.execute(
            f"""UPDATE ingestion_events SET resolved=1
                WHERE kind='missing_section'
                  AND json_extract(detail,'$.unit_id') IN ({marks})""",
            ids,
        )
        conn.commit()
        return cursor.rowcount
    finally:
        conn.close()


def requeue_units(reason: str) -> int:
    """Give units poisoned or deferred with *reason* (``error:OSError``, ``unknown_domain``)
    a fresh start once its cause is fixed: queued with no attempts, the candidate pending
    again, their poison events resolved. Only exact-reason matches; returns how many
    units were requeued. A unit whose cause is still there fails or parks again."""
    from .store import connect_rw
    conn = connect_rw()
    try:
        rows = conn.execute(
            "SELECT unit_id,candidate_id FROM ingestion_units WHERE state IN ('failed_poison','deferred') "
            "AND reason=?",
            (reason,)).fetchall()
        if not rows:
            return 0
        ids = [str(row[0]) for row in rows]
        candidates = sorted({str(row[1]) for row in rows})
        marks = ",".join("?" for _ in ids)
        cursor = conn.execute(
            f"""UPDATE ingestion_units SET state='queued',reason=NULL,defer_reason=NULL,attempts=0,
                       next_attempt_at=NULL,error_class=NULL,error_fingerprint=NULL,
                       updated_at=?
                WHERE unit_id IN ({marks}) AND state IN ('failed_poison','deferred')""",
            (_now_iso(), *ids))
        conn.execute(
            f"""UPDATE ingestion_events SET resolved=1 WHERE kind='poison' AND resolved=0
                  AND json_extract(detail,'$.unit_id') IN ({marks})""", ids)
        count = cursor.rowcount
        # recomputed from the units by the one aggregation the worker uses (a candidate
        # can be done or still in_progress, and the worker selects neither); it commits
        from .worker import reaggregate_candidates
        reaggregate_candidates(conn, candidates)
        return count
    finally:
        conn.close()


def requeue_missing_sections(workspace_root: Path | str, target_path: Path | str | None = None) -> int:
    """Requeue parked units whose exact target heading now exists."""
    from .store import connect_rw

    root = Path(workspace_root).resolve(strict=False)
    selected = Path(target_path).resolve(strict=False) if target_path is not None else None
    conn = connect_rw()
    try:
        rows = conn.execute(
            """SELECT destination FROM ingestion_units
               WHERE state='deferred' AND defer_reason='missing_section'
               ORDER BY unit_id"""
        ).fetchall()
    finally:
        conn.close()
    keys: list[str] = []
    for row in rows:
        value = _json(row[0], {})
        try:
            destination = DestinationV2.model_validate(value)
        except Exception as exc:
            logger.warning("CDF parked unit has an unreadable destination: %s", exc)
            continue
        path = (root / destination.relative_path).resolve(strict=False)
        if selected is not None and path != selected:
            continue
        if _section_exists(root, destination):
            keys.append(json.dumps({
                "relative_path": destination.relative_path,
                "section": destination.section or "",
            }, sort_keys=True))
    return sum(requeue_parked("missing_section", key) for key in dict.fromkeys(keys))


# ── refactor 2026-10-29: whoever holds the context decides ────────────────────
# Design 2026-10-29-cdf-refactor. The destination is the extractor's declaration
# (contracts_v2.route_unit); trust is the unit's own cited evidence; one verification
# call (judge.verify) answers what a context-free checker can; a hit in another file is
# linked, never rewritten or hidden.

def _project_key(name: str) -> str:
    return re.sub(r"[\s_\-]+", "", name).casefold()


def _resolve_project(root: Path, name: str | None) -> str | None:
    """The existing project *name* refers to (case, spaces, - and _ ignored), or None:
    an invented name ("Hive Tab Pipeline") never becomes a new file."""
    if not name:
        return None
    projects = root / "Projects"
    if not projects.is_dir():
        return None
    key = _project_key(name)
    # the folder's own spelling: a case-insensitive file system also opens "widget"
    matches = [folder.name for folder in sorted(projects.iterdir())
               if folder.is_dir() and _project_key(folder.name) == key]
    exact = [found for found in matches if found == name]
    for found in exact + matches:
        if _ddd_exists(root, found):
            return found
    return None


def _grounding_kinds(grounding: str) -> tuple[bool, bool]:
    """(has the user's words, has a measured result) in a _grounding() text."""
    parts = [part for part in grounding.split("\n\n") if part.strip()]
    measured = any(part.startswith("measured ") for part in parts)
    return any(not part.startswith("measured ") for part in parts), measured


def _not_grounded(store: str, resolved: Any, grounding: str, grounded: bool | None) -> bool:
    """The trust rule (§3.2): may a unit with this evidence reach *store*?

    With the session's text in hand (a cited user message or measured result) the
    verification decides (*grounded*; ``None`` before it ran: only the deterministic
    part is checked, so a unit that can never pass costs no model call). A unit citing
    none keeps a measured strength derived from its own roots (an S5 metric) when no
    external content reached it. The user's own files take only a unit whose extractor
    read no external content (S1, the user's-words pass) with words the verification read:
    which input shaped a reviewed unit is not knowable, so one that read external content
    never gets there, whatever a model says (§9). Both read ``read_external``, the
    extractor's whole read set (cdf-fixing-spec C1)."""
    if grounding:
        words, measured = _grounding_kinds(grounding) if grounded is not False else (False, False)
    else:
        words, measured = False, not resolved.read_external and resolved.strength == "measured"
    if store in USER_CONTEXT_STORES:
        return resolved.read_external or resolved.strength != "user_explicit" or not words
    if store in AGENT_SELF_STORES:
        return not (words or measured)
    return False


def unit_basis(resolved: Any, supported: bool, grounding: str, grounded: bool | None) -> str:
    """The trust an entry written from this unit carries as evidence (a ``RootTrust``):
    external when it cites external content; user when the verification read the user's
    words; measured when shown by a measurement; otherwise inferred."""
    if getattr(resolved, "cited_external", True):
        return "external"
    if not supported:
        return "inferred"
    words = bool(grounding) and grounded is not False and _grounding_kinds(grounding)[0]
    return "user" if words else "measured"


def _project_home(unit: SourceUnit, destination: Any, project: str | None) -> Any:
    """Where an agent or world lesson goes when nothing it cites shows it to be true (the
    agent's own reading of tool output, cdf-refactor §3.2): the DDD of the project it was
    learned in, marked as rerouted (its basis is inferred: the baseline registers it so,
    and it is recalled there on demand, never injected every turn). None when there is no such project, and for the
    user's own files, which take only the user's words."""
    if not (isinstance(unit, CognitionUnit) and destination.store in AGENT_SELF_STORES and project):
        return None
    home = contracts_v2.route_unit(unit.model_copy(update={"scope": f"project:{project}"}), project)
    return home.model_copy(update={"rerouted_from": destination.store})


def run_unit_v2(row: Any, workspace_root: Path | str, judge: Any) -> UnitDecision:
    """Judge one unit: route it by its declaration, verify it once, decide."""
    root = Path(workspace_root)
    unit = SOURCE_UNIT.validate_python(_json(_get(row, "payload"), {}))
    unit_id = str(_get(row, "unit_id") or f"{_get(row, 'id', 'unit')}.unit")
    resolved = _resolved(row)
    vectors: list[dict[str, Any]] = []
    card0 = _card0(unit, row)
    if not card0.passed:
        return _decision(unit_id, "reject", "reject", card0.reason, vectors=vectors, card0=card0)

    # the project: one a source fixed (an S3 run's Brain, a Hive lesson, S1's explicit
    # project; the inbox's project_hint) wins for a project unit or gap; otherwise the
    # unit's own scope=project:X. The project the session worked in is not a declaration:
    # it names the DDD searched for duplicates, and the project of a gap that names none.
    snapshot = _json(_get(row, "project_snapshot"), {})
    declared = _get(row, "project_hint") or snapshot_project(snapshot)
    session_project = snapshot.get("session_project") if isinstance(snapshot, dict) else None
    scope = getattr(unit, "scope", None) or ""
    scope_project = scope[len("project:"):].strip() if scope.startswith("project:") else None
    if _is_hive_lesson(row) and scope_project and (
            not declared or _resolve_project(root, scope_project) != _resolve_project(root, declared)):
        # a Hive lesson lands only in the Brain its run used (none for the General Brain)
        return _decision(unit_id, "reject", "reject", "project_outside_brain", vectors=vectors, card0=card0)
    # only a gap and a project lesson need a project to have a destination; an event or a
    # reference goes where its kind says, whatever its scope names
    wants_project = isinstance(unit, GapUnit) or (isinstance(unit, CognitionUnit) and bool(scope_project))
    session_home = _resolve_project(root, session_project)
    if wants_project:
        project = _resolve_project(root, declared or scope_project)
        if project is None and not declared and not _is_hive_lesson(row):
            # a project the extractor named that does not exist (a subsystem's name, the
            # workspace) is the session's own project when it has one
            project = session_home
        if project is None:
            return _decision(unit_id, "reject", "reject", "unknown_project", vectors=vectors, card0=card0)
    else:
        project = _resolve_project(root, declared)
    corpus_project = project or session_home
    try:
        destination = contracts_v2.route_unit(unit, project)
    except ValueError as exc:
        return _decision(unit_id, "reject", "reject", str(exc), vectors=vectors, card0=card0)
    if isinstance(unit, CognitionUnit) and _volatile(_unit_text(unit)):
        # D4 / R30#4: a drifting number (line N, N files, N MB) is a snapshot, not cognition
        return _decision(unit_id, "reject", "reject", "volatile", vectors=vectors,
                         card0=card0, destination=destination)
    grounding = _grounding(list(unit.evidence))
    if not grounding and str(_get(row, "source_v2", "")) == "S1":
        envelope = _json(_get(row, "envelope"), {})
        event_ids = envelope.get("source_event_ids") or envelope.get("event_ids") or []
        if event_ids:
            from core.session_inputs import user_authored_text
            grounding = user_authored_text(str(event_ids[-1])) or ""
        grounding = grounding or str(envelope.get("user_authored_text", ""))
    if _not_grounded(destination.store, resolved, grounding, None):
        destination = _project_home(unit, destination, corpus_project)
        if destination is None:
            return _decision(unit_id, "reject", "reject", "not_grounded", vectors=vectors, card0=card0)

    group = _group(unit, (project,) if project else ())
    event_key = None
    if isinstance(unit, EventUnit):
        event_key = _event_key(unit, resolved.root_ids)
        group = ClaimGroup(**{**group.__dict__, "evidence": event_key})
    retrieval = retrieve(group, root, destination, project=corpus_project)
    if retrieval.status in {"UNAVAILABLE", "STALE"}:
        return _decision(unit_id, "defer", "defer", retrieval.status.lower(), vectors=vectors,
                         card0=card0, destination=destination, card3=retrieval)
    if event_key and any(f"event_key:{event_key}" in hit.text for hit in retrieval.hits):
        return _decision(unit_id, "reject", "reject", "already_exists", vectors=vectors,
                         card0=card0, destination=destination, card3=retrieval)

    # what the user withdrew stays out: an exact restatement is refused here, a near one is
    # shown to the judge next to the unit's hits (design 2026-10-30-brain-driven §2.2)
    unit_json = unit.model_dump(mode="json")
    withdrawn_all = _withdrawn_records()
    restated = withdrawn.restates(unit_json, withdrawn_all)
    if restated is not None:
        return _decision(unit_id, "reject", "reject", "withdrawn", vectors=vectors,
                         card0=card0, destination=destination, card3=retrieval)
    near = {r.anchor: r for r in withdrawn.nearest(unit_json, withdrawn_all)}
    hits = [{"anchor": hit.anchor, "text": hit.text} for hit in retrieval.hits]
    hits += [{"anchor": anchor, "text": r.text} for anchor, r in near.items()]
    try:
        verdict = contracts_v2.VerifyAnswer.model_validate(
            _unwrap(judge.verify(unit_json, grounding, hits), vectors))
    except UntypedOutput:
        return _decision(unit_id, "reject", "reject", "untyped", vectors=vectors,
                         card0=card0, destination=destination, card3=retrieval)
    except Exception as exc:
        return _decision(unit_id, "defer", "defer", f"judge_unavailable:{type(exc).__name__}",
                         vectors=vectors, card0=card0, destination=destination, card3=retrieval)
    vectors.append({"verify": verdict.model_dump(mode="json")})
    done = dict(vectors=vectors, card0=card0, destination=destination, card3=retrieval)
    if isinstance(unit, EventUnit) and not verdict.significant:
        return _decision(unit_id, "reject", "reject", "not_significant", **done)
    if not verdict.self_contained:
        return _decision(unit_id, "reject", "reject", "not_self_contained", **done)
    if not verdict.durable:
        return _decision(unit_id, "reject", "reject", "not_durable", **done)

    # an answer naming an unsupported claim is not grounded, whatever its flag says
    grounded = verdict.grounded and not verdict.ungrounded
    if _not_grounded(destination.store, resolved, grounding, grounded):
        destination = _project_home(unit, destination, corpus_project)
        if destination is None:
            return _decision(unit_id, "reject", "reject", "not_grounded", **done)
        done["destination"] = destination
    # verified against the cited words or measurement, or (citing none) measured by
    # provenance with no external content reaching it, as _not_grounded allows
    supported = grounded if grounding else not resolved.read_external and resolved.strength == "measured"
    if destination.store == "TIMELINE":
        # an event citing external content is not read every turn (writer: "Not injected")
        destination = destination.model_copy(update={"injectable": not resolved.cited_external})
        done["destination"] = destination

    from .prompts import verify_hit_ids
    shown = verify_hit_ids(hits)
    known = {hit.anchor for hit in retrieval.hits}
    from .gaps import is_closed_gap
    closed = {hit.anchor for hit in retrieval.hits if is_closed_gap(hit.anchor, hit.text)}
    # a "same" that names claims the hit lacks is an "adds": dropping it as a duplicate would
    # lose those claims (design 2026-10-30-brain-driven §5.2: 17 of 43 "same" verdicts did)
    verdicts = [(shown.get(item.anchor, item.anchor),
                 "adds" if item.relation == "same" and item.new_only else item.relation)
                for item in verdict.hits]
    # a "same" or "adds" against a withdrawn entry restates what the user took out: refused.
    # Against a closed gap or a done item it is a recurrence, written again as new.
    if any(near[a].status in withdrawn.REFUSING and r in {"same", "adds"}
           for a, r in verdicts if a in near):
        return _decision(unit_id, "reject", "reject", "withdrawn", **done)
    relations = [(a, r) for a, r in verdicts if a in known]
    # the same as a closed gap ([x]) is a recurrence: written again,
    # derived from the old one, never dropped as a duplicate (gap-lifecycle §2.5)
    relations = [(anchor, "adds" if relation == "same" and anchor in closed else relation)
                 for anchor, relation in relations]
    same = [anchor for anchor, relation in relations if relation == "same"]
    if same:
        if isinstance(unit, CognitionUnit):
            support = reinforce_or_reject(same[0], resolved, source_unit_id=unit_id)
            if support.reinforced:
                return _decision(unit_id, "reinforce", "reinforce", "reinforced", **done)
        return _decision(unit_id, "reject", "reject", "already_exists", **done)
    # last before writing: a unit refused or already known never asks for a section
    if not _section_exists(root, destination):
        return _decision(unit_id, "defer", "defer", "missing_section", **done)
    hints = _relation_hints(unit, str(_get(row, "source_v2", _get(row, "source", ""))))
    # the judge's "contradicts" links the entries, it never retires one: it pairs a newer
    # observation with an entry on the same subject, not a correction with what it corrects
    for anchor, relation in relations:
        if relation in {"contradicts", "adds"}:
            hints.append({"predicate": "derived_from", "object_anchor": anchor})
    reason = next((r for _a, r in relations if r in {"contradicts", "adds"}), "new")
    # what the deciding context says this unit replaces (S7 / S3 only; brain-driven §2.1)
    # the project the source fixed, never the unit's own scope: the run's for S3, the
    # session's for S7 (a session about one project does not retire another's entries)
    home = _resolve_project(root, declared) or session_home
    checked = _check_overrides(unit, row, root, destination, corpus_project, resolved, home,
                               supported=supported)
    if checked is not None and checked.older:
        return _decision(unit_id, "reject", "reject", "older_than_existing", **done)
    result = _decision(unit_id, "write", "add", reason, **done)
    if checked is not None and checked.targets:
        result.version_vector["overrides"] = checked.targets
    if checked is not None and checked.refused:
        result.version_vector["override_refused"] = checked.refused
    result.version_vector["relation_hints"] = hints
    # what the entry rests on: the trust the baseline registers it with, so a unit citing
    # it later is no stronger than it (cdf-fixing-spec C1); only "external" is marked
    result.version_vector["basis"] = unit_basis(resolved, supported, grounding, grounded)
    if event_key:
        result.version_vector["event_key"] = event_key
    return result
