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
  anything else is recalled on demand (``_home``): about the agent or the world, from the
  General Brain, read in every project; otherwise from the project's DDD, or the General
  Brain. For the user's own files
  (STEERING / USER / TOOLS) the quote must also say what the unit claims: the one model
  call here, ``user_check``, shown only the user's messages and the claim;
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
from dataclasses import dataclass, replace
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
    # only text read back (hash-checked) from session_inputs counts: nothing a payload or
    # an envelope carries is the user's words
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
        fields = (unit.title, unit.what, unit.impact, unit.outcome)
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


def _with_relations(decision: UnitDecision, declared: dict[str, Any],
                    refused: list[dict[str, str]]) -> UnitDecision:
    """*decision* carrying the relations the agent declared: ``repeats`` (the anchor of the
    earlier correction it names) and the declarations that named no entry."""
    if refused:
        decision.version_vector["relation_refused"] = refused
    if "repeats" in declared:
        decision.version_vector["repeats"] = declared["repeats"].anchor
    return decision


def _same_entry(decision: UnitDecision, entry: Any) -> UnitDecision:
    """*decision* naming the entry the unit is the same as: a reinforcement strengthens
    that entry, which may sit in another file than the unit's own destination (the
    Cognition flow view shows where it really landed)."""
    decision.version_vector["same_anchor"] = entry.anchor
    decision.version_vector["same_path"] = entry.path
    return decision


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
    """Apply v2 card 0: facts only (an empty unit, a URL carrying credentials); what a
    unit says is never matched against a pattern (design 2026-10-30-cdf-agent-decides I6)."""
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
    from .writer import product_sections
    if destination.section in product_sections(destination.store):
        return True                  # the writer adds a product-defined section itself
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
                     basis=None, admitted_by=None):
    """``overrides.check`` for a cognition unit that declares overrides,
    ``overrides.check_session_own`` for an event, else None. An unreadable store refuses
    every declaration (logged): nothing is retired on a guess."""
    declared = getattr(unit, "overrides", None)
    if not isinstance(unit, (CognitionUnit, EventUnit)) or not declared:
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
        if isinstance(unit, EventUnit):
            return overrides.check_session_own(
                conn, workspace=Path(root), project=project, source=source, declared=declared,
                session_ref=str(_get(row, "source_ref", "") or "") or None,
                cited_external=bool(getattr(resolved, "cited_external", True)))
        return overrides.check(conn, workspace=Path(root), project=project, source=source,
                               declared=declared, new_root_ids=getattr(resolved, "root_ids", ()),
                               cited_external=bool(getattr(resolved, "cited_external", True)),
                               home=home, destination_store=getattr(destination, "store", None),
                               basis=basis, admitted_by=admitted_by,
                               destination_path=getattr(destination, "relative_path", None))
    finally:
        conn.close()


def _ask_user_about_targets(checked, unit, row) -> None:
    """Keep a target that rests on the user's words only when the isolated user check finds
    that what the user said replaces it (decision D, extended to overrides): the unit's own
    claim being the user's words says nothing about whether they are about that entry, and
    injected text can pair a real, unrelated quote with an override."""
    from . import user_check as _check
    texts = _user_texts(unit, row)
    for anchor, text in checked.ask_user:
        old = " ".join(re.sub(r"<!--.*?-->", " ", text, flags=re.S).split())
        claim = (f"{_check.claim_of(unit)}\n\nThis replaces the earlier entry, which no longer "
                 f"holds:\n{old}")
        verdict = _check.grounds(claim, texts)
        if not verdict.holds and anchor in checked.targets:
            checked.targets.remove(anchor)
            checked.refused.append({"ref": anchor, "reason": "user_check", "detail": verdict.reason})


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
        try:
            awaited = json.loads(key)
            relative_path = str(awaited["relative_path"])
            section = str(awaited["section"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return 0
        where = ("json_extract(destination,'$.relative_path')=? AND "
                 "json_extract(destination,'$.section')=?")
        params: tuple[Any, ...] = (relative_path, section)
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
    """Give units poisoned or deferred with *reason* (``error:OSError``)
    a fresh start once its cause is fixed: queued with no attempts, the candidate pending
    again, their poison events resolved. Only exact-reason matches; returns how many
    units were requeued. A unit whose cause is still there fails or parks again."""
    from .store import connect_rw
    conn = connect_rw()
    try:
        return _requeue(conn, conn.execute(
            "SELECT unit_id,candidate_id FROM ingestion_units WHERE state IN ('failed_poison','deferred') "
            "AND reason=?",
            (reason,)).fetchall())
    finally:
        conn.close()


def requeue_error_poisons(conn) -> int:
    """At startup, give every unit an exception poisoned (it carries the error's
    fingerprint) a fresh start: the binary may now hold the fix, and without this the
    unit stays lost until someone names its reason to ``requeue_units``. A cause that is
    still there poisons it again after its few quick retries; that is the whole cost."""
    return _requeue(conn, conn.execute(
        "SELECT unit_id,candidate_id FROM ingestion_units WHERE state='failed_poison' "
        "AND error_fingerprint IS NOT NULL").fetchall())


def _requeue(conn, rows) -> int:
    """Put *rows* (unit_id, candidate_id) back to queued with no attempts, resolve their
    poison events and recompute their candidates."""
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


# a unit about the agent itself or about the world is read in every project, not one: the
# General Brain is recalled in every session, a project's DDD only when that project is
# the active one (live finding 2026-10-31: 118 such units sat in SwarmAI's DDD, unseen in
# every session about another project)
GENERAL_SUBJECTS = {"agent": "Agent self-knowledge", "world": "General knowledge"}


def _home(unit: SourceUnit, destination: Any, project: str | None, subject: str | None,
          *, cited_external: bool = True) -> Any:
    """Where a unit goes that its evidence keeps out of an every-turn file: about the agent
    or the world, the General Brain under GENERAL_SUBJECTS, unless it cites external
    content (that stays in the project it came in through, not read in every project);
    otherwise the DDD of the project it was learned in, else the General Brain (recalled on
    demand, never injected every turn). Marked with the store it was declared for."""
    general = (None if cited_external
               else GENERAL_SUBJECTS.get(str(getattr(unit, "scope", "") or "")))
    if general:
        return contracts_v2.route_general(unit, general).model_copy(
            update={"rerouted_from": destination.store})
    scoped = unit.model_copy(update={"scope": f"project:{project}"}) if project else unit
    try:
        home = contracts_v2.route_unit(scoped, project) if project else None
    except ValueError:
        home = None
    if home is None:
        home = contracts_v2.route_general(unit, subject)
    return home.model_copy(update={"rerouted_from": destination.store})


def _repeat_opens(unit: Any, declared_rel: dict[str, Any], declared: Any, destination: Any,
                  facts: Facts, row: Any) -> bool:
    """Whether a correction about the agent that its evidence kept out of EVOLUTION goes
    there anyway: it declares it repeats a recorded correction (one of
    ``relations_v2.corrections``) that another conversation recorded, so the mistake has
    now been made in two, and it cites nothing external. A first occurrence stays in the
    General Brain; a repeat of this conversation's own entry is no second occurrence."""
    from . import relations_v2
    earlier = declared_rel.get("repeats")
    if not (isinstance(unit, CognitionUnit) and unit.type == "correction" and earlier is not None
            and declared.store == "EVOLUTION" and destination.store != "EVOLUTION"
            and not facts.cited_external and relations_v2.corrections([earlier])):
        return False
    cand = re.search(r"cand:(cand-[0-9a-f]+)\.unit", earlier.text)
    if cand is None:
        return True                       # a correction written before CDF or by a person
    session = _get(row, "source_ref")
    try:
        from .store import connect_ro
        conn = connect_ro()
    except (OSError, sqlite3.Error):
        return False                      # unknown whose it is: stays in the General Brain
    try:
        found = conn.execute("SELECT source_ref FROM ingestion_inbox WHERE id=?",
                             (cand.group(1),)).fetchone()
    except sqlite3.Error:
        return False
    finally:
        conn.close()
    return not (found and session and found[0] == session)


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
        # the project the producing agent named, or none: a name that is no project goes
        # to the General Brain under that name (D7). The session's project is not
        # guessed for it — the agent was shown the projects that exist (s7_review) and
        # names one of them when that is where the unit belongs.
        project = _resolve_project(root, subject)
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
    user_check = None
    if destination.store in USER_CONTEXT_STORES | AGENT_SELF_STORES and facts.user_words:
        # the quote is in the user's message; whether it says what the unit claims is
        # asked of a model that sees only the user's messages and the claim (decision D,
        # user_check): injected text in the session cannot reach it. The user's own files
        # need the words to state it; MEMORY and EVOLUTION need the claim to come from
        # them (an agent's lesson may generalise the correction). Not so: not the user's
        # words, so the unit goes where its other facts admit it (a measurement), else
        # to the project's DDD like any other.
        from . import user_check as _check
        ask = _check.says if destination.store in USER_CONTEXT_STORES else _check.grounds
        verdict = ask(_check.claim_of(unit), _user_texts(unit, row))
        user_check = {"holds": verdict.holds, "reason": verdict.reason, "version": _check.CHECK_VERSION}
        if not verdict.holds:
            facts = replace(facts, user_words=False)
    declared_destination = destination
    if not _admitted(destination.store, facts):
        destination = _home(unit, destination, corpus_project, subject,
                            cited_external=facts.cited_external)
    if destination.store == "TIMELINE":
        # an event citing external content is not read every turn (writer: "Not injected")
        destination = destination.model_copy(update={"injectable": not facts.cited_external})
    done = dict(vectors=vectors, card0=card0, destination=destination)

    event_key = None
    if isinstance(unit, EventUnit):
        event_key = _event_key(unit, resolved.root_ids)
        if _event_known(root, event_key):
            return _decision(unit_id, "reject", "reject", "already_exists", **done)

    # the entries the review was shown come from the session's project; a unit scoped to
    # another project declares against that one's too: both are in force
    entries = relations_v2.pool(root, corpus_project)
    if session_home and session_home != corpus_project:
        known = {e.anchor for e in entries}       # the every-turn files are in both pools
        entries += [e for e in relations_v2.pool(root, session_home) if e.anchor not in known]

    # what the user withdrew stays out: an exact restatement is refused. One exception: every
    # record it restates was overridden (never withdrawn by the user) and an entry in force
    # in that same file says it too: the override wrote the whole current statement and
    # carried the old text forward, so the unit is the same as that entry
    withdrawn_all = _withdrawn_records()
    carried = None
    restated_by = withdrawn.restating(unit.model_dump(mode="json"), withdrawn_all)
    if restated_by:
        if not isinstance(unit, EventUnit):
            carried = withdrawn.carried_by(unit.model_dump(mode="json"), restated_by, entries)
        if carried is None:
            return _decision(unit_id, "reject", "reject", "withdrawn", **done)

    # the relations the producing agent declared (D1): code checks only that the id and
    # the first line name one entry; a declaration that does not stands for nothing
    entries += relations_v2.withdrawn_entries(withdrawn_all)
    if isinstance(unit, EventUnit):
        # an event is the same as an event only (extraction-at-task-end E2): an open gap
        # it finishes is retired by its overrides, never taken for the same thing
        entries = relations_v2.timeline_entries(root) + [
            e for e in entries if e.path == relations_v2.TIMELINE_FILE]
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
    if carried is not None:
        declared_rel["same"] = carried
    admitted_by = None
    if _repeat_opens(unit, declared_rel, declared_destination, destination, facts, row):
        # the same mistake recorded in two conversations is a measurement: the repeat
        # goes where the correction was declared for, read in every session
        destination = declared_destination
        done["destination"] = destination
        admitted_by = "repeat"
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
        # what was declared is kept on every outcome: a correction said again (same as the
        # earlier one) is exactly the repeat the repeat rate counts (queries.corrections)
        if isinstance(unit, CognitionUnit):
            support = reinforce_or_reject(declared_rel["same"].anchor, resolved, source_unit_id=unit_id)
            if support.reinforced:
                return _same_entry(_with_relations(_decision(unit_id, "reinforce", "reinforce", "reinforced", **done),
                                                   declared_rel, refused), declared_rel["same"])
        return _same_entry(_with_relations(_decision(unit_id, "reject", "reject", "already_exists", **done),
                                           declared_rel, refused), declared_rel["same"])
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
                               basis=facts.basis, admitted_by=admitted_by)
    if checked is not None and checked.ask_user:
        _ask_user_about_targets(checked, unit, row)
    if checked is not None and checked.older:
        return _decision(unit_id, "reject", "reject", "older_than_existing", **done)
    result = _decision(unit_id, "write", "add", "adds" if "adds" in declared_rel else "new", **done)
    if checked is not None and checked.targets:
        result.version_vector["overrides"] = checked.targets
        if checked.closes:
            result.version_vector["override_closes"] = checked.closes
    if checked is not None and checked.refused:
        result.version_vector["override_refused"] = checked.refused
    _with_relations(result, declared_rel, refused)
    if user_check is not None:
        result.version_vector["user_check"] = user_check
    result.version_vector["relation_hints"] = hints
    # what the entry rests on: the trust the baseline registers it with, so a unit citing
    # it later is no stronger than it; only "external" is marked in the file
    result.version_vector["basis"] = facts.basis
    if admitted_by:
        result.version_vector["admitted_by"] = admitted_by
    if event_key:
        result.version_vector["event_key"] = event_key
    return result
