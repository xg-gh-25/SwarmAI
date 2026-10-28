"""Pure CDF Phase A unit pipeline.

- ``run_unit`` carries every card answer and reason into ``UnitDecision``.
- Rerouting is bounded to one hop and restarts at card 2.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from .card0 import evaluate_card0
from .card2 import RoutingError, reroute_answer, route_card2
from .card3 import aggregate_hits, relation_for_hit, retrieve
from .contracts import Candidate, Card1Answer, Card3bAnswer, HitJudgment, UnitDecision
from .llm import JudgeResult
from .normalize import ClaimGroup, normalize_candidate
from .prompts import UntypedOutput, source_context
from .rules import evaluate_destination_rules
from .taint import check_taint

_QUALITY_REASONS = {
    "atomic": "not_atomic", "valuable": "no_judgment_value", "self_contained": "not_self_contained",
    "durable": "not_durable", "evidenced": "speculative", "internally_consistent": "self_contradictory",
    "concise": "not_concise",
}


def _unwrap(result: Any, vectors: list[dict[str, Any]]) -> Any:
    if isinstance(result, JudgeResult):
        vectors.append(result.vector)
        return result.value
    return result


def _version_vector(vectors: list[dict[str, Any]], corpus_digest: str = "") -> dict[str, Any]:
    vector = dict(vectors[-1]) if vectors else {}
    hashes = [item.get("prompt_hash", "") for item in vectors]
    vector["prompt_hashes"] = hashes
    vector["prompt_hash"] = hashlib.sha256("".join(hashes).encode("ascii")).hexdigest() if hashes else ""
    vector["corpus_digest"] = corpus_digest
    return vector


def _decision(unit_id: str, decision: str, action: str, reason: str, *,
              vectors: list[dict[str, Any]], corpus_digest: str = "", **cards: Any) -> UnitDecision:
    return UnitDecision(unit_id=unit_id, decision=decision, action=action, reason=reason,
                        version_vector=_version_vector(vectors, corpus_digest), **cards)


def _quality_failure(answer: Card1Answer) -> str | None:
    for field, reason in _QUALITY_REASONS.items():
        if not getattr(answer.quality, field):
            if field == "valuable" and answer.content_form == "reference":
                return "no_reference_value"
            return reason
    if answer.shape == "WorldCrossGlobal" and not answer.global_relevance:
        return "not_global"
    return None


def _section_exists(root: Path, relative_path: str, section: str | None) -> bool:
    target = root / relative_path
    if not target.is_file():
        return False
    if section is None:
        return True
    text = target.read_text(encoding="utf-8")
    normalized = " ".join(section.split())
    headings = {" ".join(match.group(1).split()) for match in re.finditer(r"^##\s+(.+?)\s*$", text, re.MULTILINE)}
    return normalized in headings


def _rule_facts(candidate: Candidate, group: ClaimGroup, root: Path, destination: Any) -> dict[str, Any]:
    supplied = candidate.project_snapshot.get("rule_facts", {})
    facts = dict(supplied) if isinstance(supplied, dict) else {}
    facts.update({
        "why": facts.get("why", group.why),
        "how": facts.get("how", group.how),
        "evidence": candidate.envelope.evidence,
        "project_count": max(1, len(group.project_candidates)),
        "target_path": root / destination.relative_path,
        "live_context_root": root / ".context",
        "templates_root": root / "templates",
        "source": candidate.source,
        "title": group.title,
        "tainted": candidate.envelope.tainted,
    })
    if candidate.payload.kind == "backflow":
        facts.update(message_id=candidate.payload.message_id,
                     frontmatter={"source": candidate.source, "ingestion_candidate": candidate.id,
                                  "ontology_types": [], **({"taint": "external"} if candidate.envelope.tainted else {})})
    return facts


def _effective_unit_valid(answer: Card3bAnswer, candidate: Card1Answer) -> bool:
    candidate_claims = set(getattr(candidate, "claims", []))
    for claim in answer.claims:
        if claim.disposition in {"preserve", "refine"}:
            if not claim.output_sentence or claim.output_sentence not in answer.effective_unit:
                return False
        elif claim.disposition == "refute":
            if not claim.candidate_claim or claim.candidate_claim not in candidate_claims:
                return False
    return _quality_failure(answer.card1) is None


def run_unit(
    candidate: Candidate,
    corpus: Path | str,
    judge: Any,
    *,
    group: ClaimGroup | None = None,
    existing_fingerprints: set[str] | None = None,
    exclude_origin: bool = False,
) -> UnitDecision:
    """Run one claim-group through CDF steps 1-9 without writing any file."""
    root = Path(corpus)
    groups = normalize_candidate(candidate)
    selected = group or (groups[0] if len(groups) == 1 else None)
    if selected is None:
        raise ValueError("run_unit requires exactly one group or an explicit group")
    unit_id = f"{candidate.id}.{selected.slot}"
    if selected.merged_into:
        return _decision(unit_id, "merged", "merged", "merged", vectors=[])

    vectors: list[dict[str, Any]] = []
    card0 = evaluate_card0(selected, existing_fingerprints=existing_fingerprints or set())
    if not card0.passed:
        return _decision(unit_id, "reject", "reject", card0.reason, vectors=vectors, card0=card0)
    try:
        context = source_context(candidate.payload.kind, candidate.envelope.evidence)
        card1 = _unwrap(judge.card1(selected.text, selected.project_candidates, context), vectors)
    except UntypedOutput:
        return _decision(unit_id, "reject", "reject", "untyped", vectors=vectors, card0=card0)
    except Exception as exc:
        return _decision(unit_id, "defer", "defer", f"judge_unavailable:{type(exc).__name__}", vectors=vectors, card0=card0)
    failure = _quality_failure(card1)
    if failure:
        return _decision(unit_id, "reject", "reject", failure, vectors=vectors, card0=card0, card1=card1)
    card0 = evaluate_card0(selected, content_form=card1.content_form,
                           existing_fingerprints=existing_fingerprints or set())
    if not card0.passed:
        return _decision(unit_id, "reject", "reject", card0.reason, vectors=vectors, card0=card0, card1=card1)
    taint_reason = check_taint(card1, candidate.envelope.tainted)
    if taint_reason:
        return _decision(unit_id, "reject", "reject", taint_reason, vectors=vectors, card0=card0, card1=card1)

    current = card1
    reroutes = 0
    while True:
        try:
            destination = route_card2(current, selected.project_candidates)
        except RoutingError as exc:
            reason = str(exc)
            decision = "defer" if reason == "ambiguous_project" else "reject"
            return _decision(unit_id, decision, decision, reason, vectors=vectors,
                             card0=card0, card1=current, reroutes=reroutes)
        if destination.section and not _section_exists(root, destination.relative_path, destination.section):
            return _decision(unit_id, "defer", "defer", "missing_section", vectors=vectors,
                             card0=card0, card1=current, card2=destination, destination=destination,
                             reroutes=reroutes)
        facts = _rule_facts(candidate, selected, root, destination)
        rules = evaluate_destination_rules(current, destination, selected.text, **facts)
        failures = [rule for rule in rules if not rule.passed]
        reroute_to = current.reroute_to or next((rule.reroute_to for rule in failures if rule.reroute_to), None)
        hard_failures = [rule for rule in failures if not rule.reroute_to]
        if reroute_to and not hard_failures and reroutes == 0:
            try:
                current = reroute_answer(current, reroute_to,
                                         project=selected.project_candidates[0] if len(selected.project_candidates) == 1 else None)
            except RoutingError:
                return _decision(unit_id, "reject", "reject", "destination_mismatch", vectors=vectors,
                                 card0=card0, card1=current, card2=destination, rules=rules,
                                 destination=destination, reroutes=reroutes)
            reroutes += 1
            continue
        if reroute_to and reroutes >= 1:
            return _decision(unit_id, "reject", "reject", "destination_mismatch", vectors=vectors,
                             card0=card0, card1=current, card2=destination, rules=rules,
                             destination=destination, reroutes=reroutes)
        if failures:
            return _decision(unit_id, "reject", "reject", failures[0].reason, vectors=vectors,
                             card0=card0, card1=current, card2=destination, rules=rules,
                             destination=destination, reroutes=reroutes)
        break

    origin_anchor = None
    if exclude_origin and candidate.origin:
        origin_anchor = f"{candidate.origin.get('path', '')}#{candidate.origin.get('anchor', '')}"
    retrieval = retrieve(selected, root, destination, exclude_anchor=origin_anchor)
    if retrieval.status in {"UNAVAILABLE", "STALE"}:
        return _decision(unit_id, "defer", "defer", retrieval.status.lower(), vectors=vectors,
                         corpus_digest=retrieval.corpus_digest, card0=card0, card1=current,
                         card2=destination, rules=rules, card3=retrieval, destination=destination,
                         reroutes=reroutes)
    if retrieval.status == "OK_EMPTY":
        return _decision(unit_id, "write", "add", "new", vectors=vectors,
                         corpus_digest=retrieval.corpus_digest, card0=card0, card1=current,
                         card2=destination, rules=rules, card3=retrieval, destination=destination,
                         reroutes=reroutes)
    hit_payload = [hit.model_dump() | {"candidate_evidence": candidate.envelope.evidence,
                                     "candidate_observed_at": candidate.envelope.observed_at}
                   for hit in retrieval.hits]
    try:
        judgments: list[HitJudgment] = _unwrap(judge.card3(selected.text, hit_payload), vectors)
    except UntypedOutput:
        return _decision(unit_id, "reject", "reject", "untyped", vectors=vectors,
                         corpus_digest=retrieval.corpus_digest, card0=card0, card1=current,
                         card2=destination, rules=rules, card3=retrieval, destination=destination,
                         reroutes=reroutes)
    except Exception as exc:
        return _decision(unit_id, "defer", "defer", f"judge_unavailable:{type(exc).__name__}", vectors=vectors,
                         corpus_digest=retrieval.corpus_digest, card0=card0, card1=current,
                         card2=destination, rules=rules, card3=retrieval, destination=destination,
                         reroutes=reroutes)
    if {item.anchor for item in judgments} != {hit.anchor for hit in retrieval.hits} or len(judgments) != len(retrieval.hits):
        return _decision(unit_id, "reject", "reject", "untyped", vectors=vectors,
                         corpus_digest=retrieval.corpus_digest, card0=card0, card1=current,
                         card2=destination, rules=rules, card3=retrieval, destination=destination,
                         hit_judgments=judgments, reroutes=reroutes)
    aggregate = aggregate_hits(relation_for_hit(item) for item in judgments)
    if aggregate.action == "reject":
        return _decision(unit_id, "reject", "reject", aggregate.reason, vectors=vectors,
                         corpus_digest=retrieval.corpus_digest, card0=card0, card1=current,
                         card2=destination, rules=rules, card3=retrieval, destination=destination,
                         hit_judgments=judgments, reroutes=reroutes)
    card3b = None
    if aggregate.action in {"replace", "refine"}:
        target = next(hit for hit in retrieval.hits if hit.anchor == aggregate.target_anchor)
        if target.baseline:
            try:
                card3b = _unwrap(judge.card3b(target.text, selected.text), vectors)
            except UntypedOutput:
                return _decision(unit_id, "reject", "reject", "untyped", vectors=vectors,
                                 corpus_digest=retrieval.corpus_digest, card0=card0, card1=current,
                                 card2=destination, rules=rules, card3=retrieval, destination=destination,
                                 hit_judgments=judgments, reroutes=reroutes)
            except Exception as exc:
                return _decision(unit_id, "defer", "defer", f"judge_unavailable:{type(exc).__name__}", vectors=vectors,
                                 corpus_digest=retrieval.corpus_digest, card0=card0, card1=current,
                                 card2=destination, rules=rules, card3=retrieval, destination=destination,
                                 hit_judgments=judgments, reroutes=reroutes)
            if not _effective_unit_valid(card3b, current):
                return _decision(unit_id, "reject", "reject", "conflict_unresolved", vectors=vectors,
                                 corpus_digest=retrieval.corpus_digest, card0=card0, card1=current,
                                 card2=destination, rules=rules, card3=retrieval, destination=destination,
                                 hit_judgments=judgments, card3b=card3b, reroutes=reroutes)
    return _decision(unit_id, "write", aggregate.action, aggregate.reason, vectors=vectors,
                     corpus_digest=retrieval.corpus_digest, card0=card0, card1=current,
                     card2=destination, rules=rules, card3=retrieval, destination=destination,
                     hit_judgments=judgments, card3b=card3b, reroutes=reroutes)
