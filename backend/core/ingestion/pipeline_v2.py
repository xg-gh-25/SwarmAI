"""CDF v2 unit pipeline, evidence reinforcement, and parked-unit requeueing.

The source unit is already validated and its trust is already derived when this module
runs. Design docs/design/2026-10-30-cdf-agent-decides.md: what a unit means (its type,
scope, destination, whether it is new or the same as an entry, what it replaces) is the
producing agent's declaration; this module only checks facts and routes:

- where it goes: the unit's own declaration (contracts_v2.route_unit), a project that
  does not exist going to the General Brain (route_general);
- what it rests on (§2): the user's words (a ``user_quote`` found verbatim in a cited user
  message), a cited measurement, or external content in its own evidence
  (``cited_external``). The every-turn files take only what this admits (``_admitted``);
  anything else goes to the project's DDD, or the General Brain;
- its declared relations (relations_v2.resolve: id and first line name one entry) and
  overrides (overrides.check);
- an exact restatement of a withdrawn entry, an event already recorded (by its key).

It never rewrites semantic fields: it routes the original values to the writer.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from . import api_v2, contracts_v2, relations_v2, withdrawn
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

logger = logging.getLogger(__name__)

_H2 = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)


@dataclass(frozen=True)
class ReinforcementResult:
    """The deterministic claim-support outcome for one duplicate/refuting hit."""

    reinforced: bool
    novel_root_ids: tuple[str, ...]


AGENT_SELF_STORES = frozenset({"EVOLUTION", "MEMORY"})
USER_CONTEXT_STORES = frozenset({"STEERING", "USER", "TOOLS"})


def _user_texts(unit: SourceUnit, row: Any) -> list[str]:
    """The user-written text of every user message *unit* cites (the session's own,
    user-trust inputs), and for S1 the message its envelope names."""
    import sqlite3
    from core import session_inputs
    ids = [a[len("event:"):] for a in unit.evidence if a.startswith("event:")]
    envelope = _json(_get(row, "envelope"), {})
    if str(_get(row, "source_v2", "")) == "S1":
        ids += [str(e) for e in envelope.get("source_event_ids") or envelope.get("event_ids") or []]
    texts: list[str] = []
    path = session_inputs._db_path()
    if ids and path.exists():
        marks = ",".join("?" * len(ids))
        try:
            with sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True, timeout=1.0) as conn:
                found = [r[0] for r in conn.execute(
                    f"SELECT event_id FROM session_inputs WHERE event_id IN ({marks}) "
                    "AND kind='user_message' AND trust='user'", ids)]
        except sqlite3.Error:
            found = []
        texts += [session_inputs.user_authored_text(event_id) or "" for event_id in found]
    if str(_get(row, "source_v2", "")) == "S1" and envelope.get("user_authored_text"):
        texts.append(str(envelope["user_authored_text"]))
    return [t for t in texts if t.strip()]


_QUOTE_NOISE = re.compile(r"[\s\"'`“”‘’「」『』《》]+")


def _quote_norm(text: str) -> str:
    """Comparable form for the verbatim check: NFKC, case-folded, without whitespace or
    quotation marks (an extractor writes 「」 for a straight quote, JSON-safe)."""
    import unicodedata
    return _QUOTE_NOISE.sub("", unicodedata.normalize("NFKC", text or "").casefold())


def _quote_found(quote: str | None, texts: list[str]) -> bool:
    """Whether *quote* appears verbatim (up to whitespace and quotation marks) in one
    of the cited user messages."""
    q = _quote_norm(quote or "")
    return len(q) >= 2 and any(q in _quote_norm(t) for t in texts)


@dataclass(frozen=True)
class Facts:
    """What a unit rests on (design §2), from provenance only."""

    user_words: bool        # a user_quote found verbatim in a cited user message
    measured: bool          # it cites a measurement and nothing external
    cited_external: bool    # its own evidence lineage holds external content
    read_external: bool     # its extractor read external content

    @property
    def basis(self) -> str:
        """The trust an entry written from the unit is registered with (K3)."""
        if self.cited_external:
            return "external"
        if self.user_words:
            return "user"
        return "measured" if self.measured else "inferred"


def _facts(unit: SourceUnit, row: Any, resolved: Any) -> Facts:
    cited = bool(getattr(resolved, "cited_external", True))
    quote = getattr(unit, "user_quote", None)
    words = bool(quote) and not cited and _quote_found(quote, _user_texts(unit, row))
    return Facts(user_words=words,
                 measured=not cited and getattr(resolved, "strength", "") == "measured",
                 cited_external=cited,
                 read_external=bool(getattr(resolved, "read_external", True)))


def _admitted(store: str, facts: Facts) -> bool:
    """§2 K1 / K2: may a unit resting on *facts* go into *store*? The user's own files
    take only the user's words; MEMORY and EVOLUTION the user's words or a measurement;
    neither anything citing external content. Every other store takes it (its basis is
    recorded)."""
    if store in USER_CONTEXT_STORES:
        return facts.user_words
    if store in AGENT_SELF_STORES:
        return facts.user_words or facts.measured
    return True


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


def _home(unit: SourceUnit, destination: Any, project: str | None, subject: str | None) -> Any:
    """Where a unit goes that its evidence keeps out of an every-turn file: the DDD of
    the project it was learned in, else the General Brain (recalled on demand, never
    injected every turn), marked with the store it was declared for."""
    scoped = unit.model_copy(update={"scope": f"project:{project}"}) if project else unit
    try:
        home = contracts_v2.route_unit(scoped, project) if project else None
    except ValueError:
        home = None
    if home is None:
        home = contracts_v2.route_general(unit, subject)
    return home.model_copy(update={"rerouted_from": destination.store})


def _event_known(root: Path, event_key: str) -> bool:
    """Whether an event with *event_key* is already in TIMELINE or on its way there."""
    try:
        if f"event_key:{event_key}" in (root / contracts_v2.TIMELINE_PATH).read_text(encoding="utf-8"):
            return True
    except OSError:
        pass
    try:
        from .store import connect_ro
        conn = connect_ro()
    except (OSError, sqlite3.Error):
        return False
    try:
        return conn.execute(
            "SELECT 1 FROM ingestion_units WHERE state IN ('judged','writing','written') "
            "AND decision LIKE ?", (f'%"event_key": "{event_key}"%',)).fetchone() is not None
    except sqlite3.Error:
        return False
    finally:
        conn.close()


def run_unit_v2(row: Any, workspace_root: Path | str) -> UnitDecision:
    """Decide one unit from its declaration and the facts of its evidence (module doc)."""
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
    # it is the project of a gap or project unit that names one that does not exist.
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
    subject = declared or scope_project
    if wants_project:
        project = _resolve_project(root, subject)
        if project is None and not declared and not _is_hive_lesson(row):
            # a project the extractor named that does not exist (a subsystem's name, the
            # workspace) is the session's own project when it has one
            project = session_home
    else:
        project = _resolve_project(root, declared)
    corpus_project = project or session_home
    try:
        if wants_project and project is None:
            # no such project: the General Brain keeps it under the subject it named (D7)
            destination = contracts_v2.route_general(unit, subject)
        else:
            destination = contracts_v2.route_unit(unit, project)
    except ValueError as exc:
        return _decision(unit_id, "reject", "reject", str(exc), vectors=vectors, card0=card0)

    facts = _facts(unit, row, resolved)
    if not _admitted(destination.store, facts):
        destination = _home(unit, destination, corpus_project, subject)
    if destination.store == "TIMELINE":
        # an event citing external content is not read every turn (writer: "Not injected")
        destination = destination.model_copy(update={"injectable": not facts.cited_external})
    done = dict(vectors=vectors, card0=card0, destination=destination)

    event_key = None
    if isinstance(unit, EventUnit):
        event_key = _event_key(unit, resolved.root_ids)
        if _event_known(root, event_key):
            return _decision(unit_id, "reject", "reject", "already_exists", **done)

    # what the user withdrew stays out: an exact restatement is refused
    withdrawn_all = _withdrawn_records()
    if withdrawn.restates(unit.model_dump(mode="json"), withdrawn_all) is not None:
        return _decision(unit_id, "reject", "reject", "withdrawn", **done)

    # the relations the producing agent declared (D1): code checks only that the id and
    # the first line name one entry; a declaration that does not stands for nothing
    entries = relations_v2.pool(root, corpus_project) + relations_v2.withdrawn_entries(withdrawn_all)
    refused: list[dict[str, str]] = []
    declared_rel: dict[str, Any] = {}
    for name in ("same", "adds", "repeats"):
        ref = getattr(unit, name, None)
        if not ref:
            continue
        entry, why = relations_v2.resolve(ref, entries)
        if entry is None:
            refused.append({"relation": name, "ref": ref, "reason": why})
        else:
            declared_rel[name] = entry
    for name in ("same", "adds"):
        entry = declared_rel.get(name)
        if entry is None or not entry.section.startswith("withdrawn:"):
            continue
        if entry.section[len("withdrawn:"):] in withdrawn.REFUSING:
            # the same as (or more of) what the user took out: it stays out
            return _decision(unit_id, "reject", "reject", "withdrawn", **done)
        # the same as a closed gap or a done item is a recurrence: written again as new
        declared_rel.pop(name)
    if "same" not in declared_rel and isinstance(unit, (CognitionUnit, GapUnit)):
        again = relations_v2.restated(unit.title, unit.what, entries, destination.relative_path)
        if again is not None:
            declared_rel["same"] = again              # the same text written again
    from .gaps import is_closed_gap
    same = declared_rel.get("same")
    if same is not None and is_closed_gap(same.anchor, same.text):
        # the same as a closed gap ([x]) is a recurrence: written again, derived from the
        # old one, never dropped as a duplicate (gap-lifecycle §2.5)
        declared_rel["adds"] = declared_rel.pop("same")
    if "same" in declared_rel:
        if isinstance(unit, CognitionUnit):
            support = reinforce_or_reject(declared_rel["same"].anchor, resolved, source_unit_id=unit_id)
            if support.reinforced:
                return _decision(unit_id, "reinforce", "reinforce", "reinforced", **done)
        return _decision(unit_id, "reject", "reject", "already_exists", **done)
    # last before writing: a unit refused or already known never asks for a section
    if not _section_exists(root, destination):
        return _decision(unit_id, "defer", "defer", "missing_section", **done)
    hints = _relation_hints(unit, str(_get(row, "source_v2", _get(row, "source", ""))))
    if "adds" in declared_rel:
        hints.append({"predicate": "derived_from", "object_anchor": declared_rel["adds"].anchor})
    # what the deciding context says this unit replaces (S7 / S3 only; brain-driven §2.1)
    # the project the source fixed, never the unit's own scope: the run's for S3, the
    # session's for S7 (a session about one project does not retire another's entries)
    home = _resolve_project(root, declared) or session_home
    checked = _check_overrides(unit, row, root, destination, corpus_project, resolved, home,
                               supported=facts.user_words or facts.measured)
    if checked is not None and checked.older:
        return _decision(unit_id, "reject", "reject", "older_than_existing", **done)
    result = _decision(unit_id, "write", "add", "adds" if "adds" in declared_rel else "new", **done)
    if checked is not None and checked.targets:
        result.version_vector["overrides"] = checked.targets
    if checked is not None and checked.refused:
        result.version_vector["override_refused"] = checked.refused
    if refused:
        result.version_vector["relation_refused"] = refused
    if "repeats" in declared_rel:
        result.version_vector["repeats"] = declared_rel["repeats"].anchor
    result.version_vector["relation_hints"] = hints
    # what the entry rests on: the trust the baseline registers it with, so a unit citing
    # it later is no stronger than it; only "external" is marked in the file
    result.version_vector["basis"] = facts.basis
    if event_key:
        result.version_vector["event_key"] = event_key
    return result
