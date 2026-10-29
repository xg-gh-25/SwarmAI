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

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from . import api_v2, contracts_v2
from .card0 import content_fingerprint
from .card3 import aggregate_hits, relation_for_hit, retrieve
from .contracts import Card0Answer, Card1Answer, Card3bAnswer, HitJudgment, UnitDecision
from .contracts_v2 import (
    CARD1_V2_ADAPTER,
    PARKED_DEFER_REASONS,
    PER_TURN_STORES,
    CognitionUnit,
    DestinationV2,
    EntailmentVerdict,
    EventAnswer,
    EventUnit,
    GapAnswer,
    GapUnit,
    ReferenceAnswerV2,
    ReferenceUnit,
    SOURCE_UNIT,
    SourceUnit,
    expand_projects,
    known_domains,
    route_for_v2,
    semantic_urls_have_credentials,
)
from .llm import JudgeResult
from .normalize import ClaimGroup
from .prompts import UntypedOutput

logger = logging.getLogger(__name__)

_QUALITY_REASONS = {
    "atomic": "not_atomic",
    "valuable": "no_judgment_value",
    "self_contained": "not_self_contained",
    "durable": "not_durable",
    "evidenced": "speculative",
    "internally_consistent": "self_contradictory",
    "concise": "not_concise",
}
_SENTENCE = re.compile(r"(?<=[.!?。！？])(?:\s+|(?=[^.!?。！？]))|\n+")
_H2 = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)


class ClaimEntailment(BaseModel):
    """A second, typed card-3b check for one independently parsed old claim."""

    model_config = ConfigDict(extra="forbid")
    claim_id: str
    entailed: bool


_CLAIM_ENTAILMENTS = TypeAdapter(list[ClaimEntailment])


@dataclass(frozen=True)
class ReinforcementResult:
    """The deterministic claim-support outcome for one duplicate/refuting hit."""

    reinforced: bool
    novel_root_ids: tuple[str, ...]



# Session review §9: S7 cognition about the agent itself that may reach EVOLUTION/MEMORY
# when the user's own words entail it.
AGENT_SELF_TYPES = frozenset({"correction", "pitfall", "guideline", "process", "model"})
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
    return api_v2.ResolvedEvidence(
        root_ids=roots,
        lineage_root_ids=lineage,
        strength=envelope.get("strength", envelope.get("evidence", "inferred")),
        tainted=bool(envelope.get("tainted", False)),
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
    if isinstance(unit, CognitionUnit) and len(text) > contracts_v2.MAX_COGNITION_CHARS:
        return Card0Answer(passed=False, reason="not_atomic")
    if not isinstance(unit, ReferenceUnit) and ("<!--" in text or "-->" in text):
        # an HTML comment marker inside an entry ends or opens the metadata comment of
        # the line it lands on; the prompt form then cuts the text after it
        return Card0Answer(passed=False, reason="unrenderable")
    if isinstance(unit, ReferenceUnit) and len(text.encode("utf-8")) > 16_384:
        return Card0Answer(passed=False, reason="too_large_incomplete")
    if isinstance(unit, ReferenceUnit) and semantic_urls_have_credentials(unit.summary, unit.body):
        return Card0Answer(passed=False, reason="credentialed_url")
    return Card0Answer(passed=True)


def _quality_failure(unit: SourceUnit, answer: Any) -> str | None:
    if isinstance(answer, EventAnswer):
        if not answer.significance:
            return "not_significant"
        if not answer.self_contained:
            return "not_self_contained"
        return None if answer.evidenced else "speculative"
    if isinstance(answer, GapAnswer):
        if not answer.actionable:
            return "not_actionable"
        if not answer.evidenced:
            return "speculative"
        return None if answer.self_contained else "not_self_contained"
    if isinstance(answer, ReferenceAnswerV2):
        if not answer.quality.self_contained:
            return "not_self_contained"
        if not answer.quality.valuable:
            return "no_reference_value"
        for field, reason in _QUALITY_REASONS.items():
            if not getattr(answer.quality, field):
                return "no_reference_value" if field == "valuable" else reason
        return None
    quality = answer.quality
    for field, reason in _QUALITY_REASONS.items():
        if not getattr(quality, field):
            return reason
    return None


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


def _existing_domains(root: Path) -> tuple[str, ...]:
    folder = root / "Knowledge" / "Learned" / "domains"
    return tuple(path.stem for path in folder.glob("*.md")) if folder.is_dir() else ()


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


def _answer_projects(answer: Any, fallback: str | None) -> list[str]:
    projects = getattr(answer, "projects", None)
    if projects:
        return [str(value) for value in projects]
    project = getattr(answer, "project", None) or fallback
    return [str(project)] if project else []


def _judge_card1(judge: Any, unit: SourceUnit, group: ClaimGroup, projects: tuple[str, ...],
                 domains: tuple[str, ...], vectors: list[dict[str, Any]]) -> Any:
    if isinstance(unit, CognitionUnit):
        context = json.dumps({
            "declared_type": unit.type,
            "declared_scope": unit.scope,
            "known_domains": sorted(known_domains(domains)),
        }, ensure_ascii=False)
        return _unwrap(judge.card1(group.text, projects, context), vectors)
    if hasattr(judge, "card1_v2"):
        return _unwrap(judge.card1_v2(unit.model_dump(mode="json"), projects, domains), vectors)
    return CARD1_V2_ADAPTER.validate_python(
        _unwrap(judge.card1(unit.model_dump(mode="json"), projects, domains), vectors)
    )


def _event_key(unit: EventUnit, roots: tuple[str, ...]) -> str:
    day = unit.when[:10]
    title = " ".join(unit.title.casefold().split())
    material = json.dumps([sorted(roots), day, title], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _split_old_claims(text: str) -> list[tuple[str, str]]:
    """Parse the old unit first, then deterministically split its complete sentences."""
    stripped = re.sub(r"<!--\s*cand:[^>]+-->", "", text)
    stripped = re.sub(r"^-\s+(?:\[[^]]+\]\s+)?\*\*[^*]+\*\*\s*", "", stripped.strip())
    sentences = [part.strip() for part in _SENTENCE.split(stripped) if part.strip()]
    return [(f"old-{index:04d}", sentence) for index, sentence in enumerate(sentences, 1)]


def _validate_card3b(judge: Any, old_text: str, candidate_text: str,
                     card3b: Card3bAnswer, vectors: list[dict[str, Any]]) -> bool:
    parsed = _split_old_claims(old_text)
    dispositions = {claim.sentence: claim for claim in card3b.claims}
    if set(dispositions) != {sentence for _, sentence in parsed}:
        return False
    required = [(claim_id, sentence) for claim_id, sentence in parsed
                if dispositions[sentence].disposition in {"preserve", "refine"}]
    if required:
        payload = [{"claim_id": claim_id, "claim": sentence} for claim_id, sentence in required]
        if hasattr(judge, "card3b_entailment"):
            raw = _unwrap(judge.card3b_entailment(payload, card3b.effective_unit), vectors)
            checks = _CLAIM_ENTAILMENTS.validate_python(raw)
        else:
            checks = [ClaimEntailment(claim_id=claim_id,
                                      entailed=sentence in card3b.effective_unit)
                      for claim_id, sentence in required]
        if {item.claim_id for item in checks} != {claim_id for claim_id, _ in required}:
            return False
        if not all(item.entailed for item in checks):
            return False
    candidate_claims = set(getattr(card3b.card1, "claims", []))
    for claim in dispositions.values():
        if claim.disposition == "refute" and (not claim.candidate_claim or
                                               claim.candidate_claim not in candidate_claims):
            return False
    return _quality_failure(SOURCE_UNIT.validate_python({
        "kind": "cognition", "type": "guideline", "scope": "world",
        "title": "effective", "what": card3b.effective_unit, "evidence": ["unit:effective"]
    }), card3b.card1) is None


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
        weight = {"user": 1.0, "measured": 0.8, "external": 0.3}
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


def _relation_hints(unit: SourceUnit) -> list[dict[str, str]]:
    hint = unit.relation_hint or {}
    kind, target = hint.get("kind"), hint.get("target")
    if kind not in {"supersedes", "derived_from"} or not target:
        return []
    return [{"predicate": kind, "object_anchor": str(target)}]


def run_unit_v2(row: Any, workspace_root: Path | str, judge: Any) -> UnitDecision:
    """Run one persisted v2 inbox row through cards 0-3 and return its audit decision."""
    root = Path(workspace_root)
    payload = _json(_get(row, "payload"), {})
    unit = SOURCE_UNIT.validate_python(payload)
    unit_id = str(_get(row, "unit_id") or f"{_get(row, 'id', 'unit')}.unit")
    resolved = _resolved(row)
    projects = tuple(_json(_get(row, "project_candidates"), []))
    project_hint = _get(row, "project_hint")
    group = _group(unit, projects)
    vectors: list[dict[str, Any]] = []
    card0 = _card0(unit, row)
    if not card0.passed:
        return _decision(unit_id, "reject", "reject", card0.reason, vectors=vectors, card0=card0)

    if str(_get(row, "source_v2", _get(row, "source", ""))) == "S1" and isinstance(unit, CognitionUnit):
        envelope = _json(_get(row, "envelope"), {})
        event_ids = envelope.get("source_event_ids") or envelope.get("event_ids") or []
        user_text = ""
        if event_ids:
            from core.session_inputs import user_authored_text
            user_text = user_authored_text(str(event_ids[-1])) or ""
        if not user_text:
            user_text = str(envelope.get("user_authored_text", ""))
        try:
            verdict = _unwrap(judge.entailment(user_text, unit.what, unit.how, unit.why), vectors)
            verdict = EntailmentVerdict.model_validate(verdict)
        except Exception as exc:
            logger.warning("CDF S1 entailment unavailable: %s", type(exc).__name__)
            return _decision(unit_id, "defer", "defer", "entailment_unavailable",
                             vectors=vectors, card0=card0)
        if not verdict.entailed:
            resolved = api_v2.ResolvedEvidence(
                root_ids=tuple(_json(_get(row, "envelope"), {}).get("context_root_ids", resolved.root_ids)),
                lineage_root_ids=tuple(_json(_get(row, "envelope"), {}).get("context_lineage_root_ids", resolved.lineage_root_ids)),
                strength="inferred",
                tainted=True,
            )

    tainted_lineage = None    # set when the S7 grounding verdict lifts the taint
    if (resolved.tainted and str(_get(row, "source_v2", _get(row, "source", ""))) == "S7"
            and isinstance(unit, CognitionUnit) and unit.scope == "agent"
            and unit.type in AGENT_SELF_TYPES):
        grounding = _grounding(unit.evidence)
        if grounding:
            try:
                verdict = _unwrap(judge.entailment(grounding, unit.what, unit.how, unit.why), vectors)
                verdict = EntailmentVerdict.model_validate(verdict)
            except Exception as exc:
                logger.warning("CDF S7 agent-self entailment unavailable: %s", type(exc).__name__)
                return _decision(unit_id, "defer", "defer", "entailment_unavailable",
                                 vectors=vectors, card0=card0)
            if verdict.entailed:
                # The agent's own cognition, backed by what the user said or a measured
                # result (session review §9): routed as untainted, so it can reach
                # EVOLUTION and MEMORY -- and only those (checked after routing): the
                # verdict is a model's, so it never opens STEERING/USER/TOOLS.
                tainted_lineage = resolved
                resolved = api_v2.ResolvedEvidence(
                    root_ids=resolved.root_ids, lineage_root_ids=resolved.lineage_root_ids,
                    strength=resolved.strength, tainted=False, unresolved=resolved.unresolved)
                vectors.append({"agent_self_grounded": True, "entailment": verdict.reason})

    domains = _existing_domains(root)
    try:
        card1 = _judge_card1(judge, unit, group, projects, domains, vectors)
    except UntypedOutput:
        return _decision(unit_id, "reject", "reject", "untyped", vectors=vectors, card0=card0)
    except Exception as exc:
        return _decision(unit_id, "defer", "defer", f"judge_unavailable:{type(exc).__name__}",
                         vectors=vectors, card0=card0)
    failure = _quality_failure(unit, card1)
    if failure:
        return _decision(unit_id, "reject", "reject", failure, vectors=vectors,
                         card0=card0, card1=card1)

    answer_projects = _answer_projects(card1, project_hint)
    if _is_hive_lesson(row) and any(p != project_hint for p in answer_projects):
        # a Hive's lesson lands only in the Brain its run used (or in no project for
        # the General Brain): the judge's own project choice cannot move it elsewhere
        return _decision(unit_id, "reject", "reject", "project_outside_brain", vectors=vectors,
                         card0=card0, card1=card1)
    if len(answer_projects) > 1:
        expansions = expand_projects(unit_id, answer_projects)
        # The worker expands these into one row/write each. Keeping the complete expansion
        # in the audit result makes the fan-out deterministic and restartable.
        vector = {"project_expansions": expansions}
        vectors.append(vector)
    selected_project = answer_projects[0] if len(answer_projects) == 1 else project_hint
    domain = getattr(card1, "domain", None) or _json(_get(row, "envelope"), {}).get("domain")
    try:
        destination = route_for_v2(
            unit.kind, card1,
            ddd_exists=_ddd_exists(root, selected_project),
            project_hint=selected_project,
            tainted=resolved.tainted,
            domain=domain,
            existing_domains=domains,
            source_url=getattr(unit, "source_url", None),
        )
    except ValueError as exc:
        reason = str(exc)
        decision = "defer" if reason in {"unknown_domain", "ambiguous_project"} else "reject"
        return _decision(unit_id, decision, decision, reason, vectors=vectors,
                         card0=card0, card1=card1)
    if tainted_lineage is not None and destination.store not in AGENT_SELF_STORES:
        # the S7 grounding verdict lifted the taint for EVOLUTION / MEMORY only
        resolved = tainted_lineage
        try:
            destination = route_for_v2(
                unit.kind, card1, ddd_exists=_ddd_exists(root, selected_project),
                project_hint=selected_project, tainted=True, domain=domain,
                existing_domains=domains, source_url=getattr(unit, "source_url", None))
        except ValueError as exc:
            return _decision(unit_id, "reject", "reject", str(exc), vectors=vectors,
                             card0=card0, card1=card1)
    if destination.store in USER_CONTEXT_STORES and resolved.strength != "user_explicit":
        # C2: STEERING / USER / TOOLS hold what the user said, never an inference
        return _decision(unit_id, "reject", "reject", "insufficient_evidence", vectors=vectors,
                         card0=card0, card1=card1, card2=destination, destination=destination)
    if isinstance(unit, CognitionUnit) and _volatile(_unit_text(unit)):
        # D4 / R30#4: a drifting number (line N, N files, N MB) is a snapshot, not cognition
        return _decision(unit_id, "reject", "reject", "volatile", vectors=vectors,
                         card0=card0, card1=card1, card2=destination, destination=destination)
    if resolved.tainted and destination.store in PER_TURN_STORES and destination.injectable:
        return _decision(unit_id, "reject", "reject", "trust_floor", vectors=vectors,
                         card0=card0, card1=card1, card2=destination,
                         destination=destination)
    if not _section_exists(root, destination):
        return _decision(unit_id, "defer", "defer", "missing_section", vectors=vectors,
                         card0=card0, card1=card1, card2=destination,
                         destination=destination)

    if isinstance(unit, EventUnit):
        event_key = _event_key(unit, resolved.root_ids)
        group = ClaimGroup(**{**group.__dict__, "evidence": event_key})
    retrieval = retrieve(group, root, destination)
    if retrieval.status in {"UNAVAILABLE", "STALE"}:
        return _decision(unit_id, "defer", "defer", retrieval.status.lower(), vectors=vectors,
                         card0=card0, card1=card1, card2=destination,
                         destination=destination, card3=retrieval)
    if retrieval.status == "OK_EMPTY":
        result = _decision(unit_id, "write", "add", "new", vectors=vectors,
                           card0=card0, card1=card1, card2=destination,
                           destination=destination, card3=retrieval)
        result.version_vector["relation_hints"] = _relation_hints(unit)
        if isinstance(unit, EventUnit):
            result.version_vector["event_key"] = _event_key(unit, resolved.root_ids)
        return result

    if isinstance(unit, EventUnit):
        event_key = _event_key(unit, resolved.root_ids)
        if any(f"event_key:{event_key}" in hit.text for hit in retrieval.hits):
            return _decision(unit_id, "reject", "reject", "already_exists", vectors=vectors,
                             card0=card0, card1=card1, card2=destination,
                             destination=destination, card3=retrieval)
    hit_payload = [hit.model_dump() | {
        "candidate_evidence": resolved.strength,
        "candidate_observed_at": _json(_get(row, "envelope"), {}).get("observed_at"),
    } for hit in retrieval.hits]
    try:
        judgments = _unwrap(judge.card3(group.text, hit_payload), vectors)
        judgments = [HitJudgment.model_validate(item) for item in judgments]
    except UntypedOutput:
        return _decision(unit_id, "reject", "reject", "untyped", vectors=vectors,
                         card0=card0, card1=card1, card2=destination,
                         destination=destination, card3=retrieval)
    except Exception as exc:
        return _decision(unit_id, "defer", "defer", f"judge_unavailable:{type(exc).__name__}",
                         vectors=vectors, card0=card0, card1=card1, card2=destination,
                         destination=destination, card3=retrieval)
    if len(judgments) != len(retrieval.hits) or {item.anchor for item in judgments} != {
        item.anchor for item in retrieval.hits
    }:
        return _decision(unit_id, "reject", "reject", "untyped", vectors=vectors,
                         card0=card0, card1=card1, card2=destination,
                         destination=destination, card3=retrieval,
                         hit_judgments=judgments)

    relations = [relation_for_hit(item) for item in judgments]
    aggregate = aggregate_hits(relations)
    duplicate_target = aggregate.target_anchor if aggregate.reason == "already_exists" else None
    if duplicate_target and isinstance(unit, CognitionUnit):
        support = reinforce_or_reject(duplicate_target, resolved, source_unit_id=unit_id)
        if support.reinforced:
            return _decision(unit_id, "reinforce", "reinforce", "reinforced", vectors=vectors,
                             card0=card0, card1=card1, card2=destination,
                             destination=destination, card3=retrieval,
                             hit_judgments=judgments)
    # Refuting roots are evidence even when normal replacement is blocked.  They do not
    # replace relation semantics; card 3 continues below.
    for judgment in judgments:
        if any(claim.verdict == "refutes" for claim in judgment.claim_verdicts):
            reinforce_or_reject(judgment.anchor, resolved, source_unit_id=unit_id,
                                polarity="refute")

    if aggregate.action == "reject":
        return _decision(unit_id, "reject", "reject", aggregate.reason, vectors=vectors,
                         card0=card0, card1=card1, card2=destination,
                         destination=destination, card3=retrieval,
                         hit_judgments=judgments)
    card3b = None
    if aggregate.action in {"replace", "refine"}:
        target = next(hit for hit in retrieval.hits if hit.anchor == aggregate.target_anchor)
        if target.baseline:
            try:
                card3b = _unwrap(judge.card3b(target.text, group.text), vectors)
                card3b = Card3bAnswer.model_validate(card3b)
            except UntypedOutput:
                return _decision(unit_id, "reject", "reject", "untyped", vectors=vectors,
                                 card0=card0, card1=card1, card2=destination,
                                 destination=destination, card3=retrieval,
                                 hit_judgments=judgments)
            except Exception as exc:
                return _decision(unit_id, "defer", "defer", f"judge_unavailable:{type(exc).__name__}",
                                 vectors=vectors, card0=card0, card1=card1, card2=destination,
                                 destination=destination, card3=retrieval,
                                 hit_judgments=judgments)
            effective_ok = _validate_card3b(judge, target.text, group.text, card3b, vectors)
            if effective_ok and destination.store in {"DDD", "COMMON_DDD"}:
                effective_ok = all(f"{field}:" in card3b.effective_unit
                                   for field in ("what", "how", "why"))
            if effective_ok:
                try:
                    effective_destination = route_for_v2(
                        "cognition", card3b.card1,
                        ddd_exists=_ddd_exists(root, selected_project),
                        project_hint=selected_project, tainted=resolved.tainted,
                        domain=domain, existing_domains=domains,
                    )
                    effective_ok = (effective_destination.relative_path == destination.relative_path
                                    and effective_destination.section == destination.section)
                except ValueError:
                    effective_ok = False
            if not effective_ok:
                return _decision(unit_id, "reject", "reject", "conflict_unresolved",
                                 vectors=vectors, card0=card0, card1=card1,
                                 card2=destination, destination=destination, card3=retrieval,
                                 hit_judgments=judgments, card3b=card3b)
    result = _decision(unit_id, "write", aggregate.action, aggregate.reason, vectors=vectors,
                       card0=card0, card1=card1, card2=destination,
                       destination=destination, card3=retrieval,
                       hit_judgments=judgments, card3b=card3b)
    result.version_vector["relation_hints"] = _relation_hints(unit)
    return result


def check_entailment(user_text: str, *, what: str, how: str | None,
                     why: str | None) -> EntailmentVerdict | None:
    """Run the isolated S1 entailment judge with no ambient session context."""
    try:
        from .llm import BedrockJudge
        from .llm_usage import attribute
        with attribute("S1"):
            return BedrockJudge().entailment(user_text, what, how, why).value
    except Exception as exc:
        logger.warning("CDF isolated entailment failed: %s", type(exc).__name__)
        return None


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
                       next_attempt_at=NULL,updated_at=CURRENT_TIMESTAMP
                WHERE unit_id IN ({marks})""",
            ids,
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
