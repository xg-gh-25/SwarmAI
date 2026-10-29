"""CDF Phase A contracts and routing authorities.

- ``Card1Answer`` is the seven-shape discriminated union.
- ``SOURCE_PAYLOAD`` validates every Phase A source payload.
- ``route_for`` is the single-row deterministic routing table.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Annotated, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from core.ddd_entry_lifecycle import MEMORY_TYPE_TO_SECTION, VALID_TYPES

CONTRACT_VERSION = "cdf-contract-r15.1-v1"
NORMALIZER_VERSION = "cdf-normalizer-v1"
RULES_VERSION = "cdf-rules-r15.1-v1"
PROMPT_VERSION = "cdf-prompts-r15.1-v3"
VALID_TYPE = Literal["guideline", "pitfall", "decision", "model", "process", "principle", "correction"]
Topic = Literal["decision", "product", "architecture", "lesson"]
ContentForm = Literal["rule", "reference"]
EvidenceStrength = Literal["inferred", "run_lesson", "review", "measured", "user_explicit"]
EVIDENCE_ORDER: dict[str, int] = {
    "inferred": 0, "run_lesson": 1, "review": 2, "measured": 3, "user_explicit": 4,
}


class StrictModel(BaseModel):
    """Forbid accidental fields in persisted or judged structures."""

    model_config = ConfigDict(extra="forbid")


class QualityAnswers(StrictModel):
    """The seven mandatory quality answers for card 1."""

    atomic: bool
    valuable: bool
    self_contained: bool
    durable: bool
    evidenced: bool
    internally_consistent: bool
    concise: bool


class RuleFields(StrictModel):
    """Fields shared by rule-shaped card 1 answers."""

    content_form: Literal["rule"] = "rule"
    entry_type: VALID_TYPE = Field(alias="type")
    applicability: str = Field(min_length=1)
    claims: list[str] = Field(min_length=1, max_length=5)
    quality: QualityAnswers
    reroute_to: str | None = None


class ReferenceFields(StrictModel):
    """Fields shared by reference-shaped card 1 answers."""

    content_form: Literal["reference"] = "reference"
    quality: QualityAnswers
    reroute_to: str | None = None


class Reference(ReferenceFields):
    """A retrievable reference document."""

    shape: Literal["Reference"]
    reference_kind: Literal["learned", "analysis", "spec"]
    project: str | None = None


class UserCross(RuleFields):
    """A cross-project fact about the user."""

    shape: Literal["UserCross"]
    user_kind: Literal["preference", "profile", "environment"]


class UserSingle(RuleFields):
    """A user rule scoped to one project."""

    shape: Literal["UserSingle"]
    project: str
    topic: Topic


class Agent(RuleFields):
    """A correction or competence fact about the agent."""

    shape: Literal["Agent"]
    agent_kind: Literal["correction", "competence"]


class ProjectSingle(RuleFields):
    """A project-owned decision or operating rule."""

    shape: Literal["ProjectSingle"]
    project: str
    topic: Topic


class WorldSingle(RuleFields):
    """Domain knowledge currently scoped to one project."""

    shape: Literal["WorldSingle"]
    project: str
    topic: Topic


class WorldCrossGlobal(RuleFields):
    """Globally relevant world knowledge."""

    shape: Literal["WorldCrossGlobal"]
    global_relevance: bool


Card1Answer: TypeAlias = Annotated[
    Reference | UserCross | UserSingle | Agent | ProjectSingle | WorldSingle | WorldCrossGlobal,
    Field(discriminator="shape"),
]
CARD1_ADAPTER = TypeAdapter(Card1Answer)


class ClaimVerdict(StrictModel):
    """One candidate claim's relation to an existing sentence."""

    verdict: Literal["agrees", "refines", "refutes", "independent"]
    existing_sentence: str | None = None


class HitJudgment(StrictModel):
    """Card 3 judgment for one retrieval hit."""

    anchor: str
    score: float = 0.0
    overlap: Literal["same", "contained", "contains", "related", "unrelated"]
    same_topic: bool
    claim_verdicts: list[ClaimVerdict]
    scope_relation: Literal["equal", "narrower", "wider", "disjoint"]
    candidate_evidence: EvidenceStrength
    existing_evidence: EvidenceStrength
    candidate_observed_at: str | None = None
    existing_observed_at: str | None = None


class ExistingClaim(StrictModel):
    """One claim extracted from a baseline unit by card 3b."""

    text: str
    sentence: str
    disposition: Literal["preserve", "refute", "refine"]
    candidate_claim: str | None = None
    output_sentence: str | None = None


class Card3bAnswer(StrictModel):
    """Effective replacement unit generated for a baseline hit."""

    claims: list[ExistingClaim]
    effective_unit: str
    card1: Card1Answer


class Destination(StrictModel):
    """A single deterministic route selected by card 2."""

    store: Literal["KNOWLEDGE", "STEERING", "USER", "TOOLS", "EVOLUTION", "DDD", "MEMORY"]
    relative_path: str
    section: str | None = None
    project: str | None = None


class Card0Answer(StrictModel):
    """Deterministic card 0 result."""

    passed: bool
    reason: str = "pass"


class RuleAnswer(StrictModel):
    """One destination-rule result."""

    rule: str
    version: str
    passed: bool
    reason: str = "pass"
    reroute_to: str | None = None


class RetrievalHit(StrictModel):
    """A typed corpus hit returned to card 3."""

    anchor: str
    text: str
    score: float
    identity: str
    byte_hash: str
    evidence: EvidenceStrength = "inferred"
    observed_at: str | None = None
    baseline: bool = True


class RetrievalResult(StrictModel):
    """Typed card 3 retrieval state."""

    status: Literal["OK_EMPTY", "OK_HITS", "UNAVAILABLE", "STALE"]
    hits: list[RetrievalHit] = Field(default_factory=list)
    corpus_digest: str = ""
    reason: str | None = None


class UnitDecision(StrictModel):
    """Complete audit result for one Phase A claim-group."""

    unit_id: str
    decision: Literal["write", "reinforce", "reject", "defer", "merged"]
    destination: Destination | None = None
    action: str
    reason: str
    card0: Card0Answer | None = None
    card1: Card1Answer | None = None
    card2: Destination | None = None
    rules: list[RuleAnswer] = Field(default_factory=list)
    card3: RetrievalResult | None = None
    hit_judgments: list[HitJudgment] = Field(default_factory=list)
    card3b: Card3bAnswer | None = None
    reroutes: int = 0
    version_vector: dict = Field(default_factory=dict)


class Envelope(StrictModel):
    """Trust-derived candidate envelope used by offline replay."""

    source_event_ids: list[str]
    evidence: EvidenceStrength
    tainted: bool
    writer: str | None = None
    observed_at: str | None = None


class RememberPayload(StrictModel):
    """Payload for explicit persistence requests."""

    kind: Literal["remember"] = "remember"
    text: str
    user_intent: str | None = None
    target_hint: str | None = None


class CorrectionPayload(StrictModel):
    """Payload for an immediate user correction."""

    kind: Literal["correction"] = "correction"
    wrong: str
    right: str
    trigger: str


class Lesson(StrictModel):
    """A distilled session lesson."""

    text: str
    type_hint: VALID_TYPE | None = None


class DistillationPayload(StrictModel):
    """Payload for session-end distillation."""

    kind: Literal["distillation"] = "distillation"
    lessons: list[Lesson]


class DailyActivityPayload(StrictModel):
    """Payload for one DailyActivity decision or correction."""

    kind: Literal["daily_activity"] = "daily_activity"
    activity_kind: Literal["decision", "correction"]
    text: str
    why: str | None = None


class BackflowPayload(StrictModel):
    """Payload for a self-contained backflow document."""

    kind: Literal["backflow"] = "backflow"
    title: str
    body: str
    message_id: str


class ExtractedPayload(StrictModel):
    """Payload for generic memory extraction."""

    kind: Literal["extracted"] = "extracted"
    text: str
    type_hint: VALID_TYPE | None = None


class EvolutionPayload(StrictModel):
    """Payload for an evolution observation."""

    kind: Literal["evolution"] = "evolution"
    skill: str
    change: str
    result: str


class ReflectLesson(StrictModel):
    """One run REFLECT lesson."""

    statement: str
    kind: VALID_TYPE
    relation_hint: str | None = None
    evidence: str


class ReflectPayload(StrictModel):
    """Payload emitted by a completed run."""

    kind: Literal["reflect"] = "reflect"
    lessons: list[ReflectLesson]
    run_id: str
    repo: str


class Proposal(StrictModel):
    """A cultivation proposal before CDF judgment."""

    doc_hint: str | None = None
    section_hint: str | None = None
    text: str
    why: str


class CultivationPayload(StrictModel):
    """Payload for signal, learn, and code-change cultivation."""

    kind: Literal["cultivation"] = "cultivation"
    proposal: Proposal


SourcePayload: TypeAlias = Annotated[
    RememberPayload | CorrectionPayload | DistillationPayload | DailyActivityPayload
    | BackflowPayload | ExtractedPayload | EvolutionPayload | ReflectPayload | CultivationPayload,
    Field(discriminator="kind"),
]
SOURCE_PAYLOAD = TypeAdapter(SourcePayload)


class Candidate(StrictModel):
    """Offline replay candidate contract."""

    id: str
    source: str
    payload: SourcePayload
    envelope: Envelope
    project_snapshot: dict = Field(default_factory=dict)
    cluster_id: str
    origin: dict[str, str] | None = None
    project_candidates: list[str] = Field(default_factory=list)


_DDD_MATRIX: dict[str, dict[str, tuple[str, str]]] = {
    "decision": {t: ("PROJECT.md", "Recent Decisions") for t in VALID_TYPES},
    "product": {
        "principle": ("PRODUCT.md", "Vision"), "model": ("PRODUCT.md", "What Makes It Different"),
        "decision": ("PRODUCT.md", "Strategic Priorities"), "guideline": ("PRODUCT.md", "Success Criteria"),
        "process": ("PRODUCT.md", "Success Criteria"), "pitfall": ("PRODUCT.md", "Non-Goals"),
        "correction": ("PRODUCT.md", "Non-Goals"),
    },
    "architecture": {
        "principle": ("TECH.md", "Architecture"), "model": ("TECH.md", "Key Subsystems"),
        "decision": ("TECH.md", "Architecture"), "guideline": ("TECH.md", "Conventions"),
        "process": ("TECH.md", "Conventions"), "pitfall": ("TECH.md", "Runtime Traps"),
        "correction": ("TECH.md", "Runtime Traps"),
    },
    "lesson": {t: ("IMPROVEMENT.md", "What Failed" if t in {"pitfall", "correction"} else "What Worked") for t in VALID_TYPES},
}
DDD_MATRIX = _DDD_MATRIX
REROUTE_ALLOWED: dict[str, frozenset[str]] = {
    "Reference": frozenset(),
    "UserCross": frozenset({"UserSingle"}),
    "UserSingle": frozenset({"UserCross", "ProjectSingle"}),
    "Agent": frozenset(),
    "ProjectSingle": frozenset({"UserSingle", "WorldSingle"}),
    "WorldSingle": frozenset({"ProjectSingle", "WorldCrossGlobal"}),
    "WorldCrossGlobal": frozenset(),
}


def _norm_heading(value: str) -> str:
    return " ".join(value.split())


def validate_ddd_matrix(template_root: Path | None = None) -> None:
    """Require every matrix heading to exist exactly after whitespace normalization.

    The one source of DDD section names is the skeleton every brain gets,
    ``backend/templates/ddd-skeleton/`` (new-brain design §5.11 C2); the SwarmAI seed
    under ``templates/swarmai-seed/`` is one project's content, not the template.
    """
    root = template_root or Path(__file__).resolve().parents[2] / "templates" / "ddd-skeleton"
    by_file: dict[str, set[str]] = {}
    for name in {cell[0] for row in DDD_MATRIX.values() for cell in row.values()}:
        text = (root / name).read_text(encoding="utf-8")
        by_file[name] = {_norm_heading(m.group(1)) for m in re.finditer(r"^##\s+(.+?)\s*$", text, re.MULTILINE)}
    missing = {(name, section) for row in DDD_MATRIX.values() for name, section in row.values()
               if _norm_heading(section) not in by_file[name]}
    if missing:
        raise ValueError(f"DDD matrix headings missing from templates: {sorted(missing)!r}")


def route_for(answer: Card1Answer) -> Destination:
    """Return exactly one destination row for a valid card 1 answer."""
    if answer.shape == "Reference":
        folder = {"learned": "Learned", "analysis": "Notes", "spec": "Library"}[answer.reference_kind]
        return Destination(store="KNOWLEDGE", relative_path=f"Knowledge/{folder}/")
    if answer.shape == "UserCross":
        file_name = {"preference": "STEERING.md", "profile": "USER.md", "environment": "TOOLS.md"}[answer.user_kind]
        return Destination(store=file_name.removesuffix(".md"), relative_path=f".context/{file_name}")
    if answer.shape == "Agent":
        section = "Corrections Captured" if answer.agent_kind == "correction" else "Competence Learned"
        return Destination(store="EVOLUTION", relative_path=".context/EVOLUTION.md", section=section)
    if answer.shape == "WorldCrossGlobal":
        if not answer.global_relevance:
            raise ValueError("not_global")
        return Destination(store="MEMORY", relative_path=".context/MEMORY.md", section=MEMORY_TYPE_TO_SECTION[answer.entry_type])
    file_name, section = DDD_MATRIX[answer.topic][answer.entry_type]
    from core.ddd_paths import ddd_new_path  # SSOT for the six-section DDD layout; no mkdir

    rel = ddd_new_path(Path("Projects") / answer.project, file_name).as_posix()
    return Destination(store="DDD", relative_path=rel, section=section, project=answer.project)


validate_ddd_matrix()


class SourceContract(StrictModel):
    """Static Phase A source registration."""

    lane: Literal["immediate", "session", "run", "scheduled"]
    payload_kind: str
    idem_algorithm: str
    target_hint: str | None = None


SOURCE_CONTRACTS: dict[str, SourceContract] = {
    "remember": SourceContract(lane="immediate", payload_kind="remember", idem_algorithm="events+schema+semantic_hash"),
    "correction": SourceContract(lane="immediate", payload_kind="correction", idem_algorithm="events+schema+semantic_hash"),
    "distillation": SourceContract(lane="session", payload_kind="distillation", idem_algorithm="events+schema+lesson_hash"),
    "daily_activity": SourceContract(lane="session", payload_kind="daily_activity", idem_algorithm="events+schema+semantic_hash"),
    "backflow": SourceContract(lane="session", payload_kind="backflow", idem_algorithm="message_id"),
    "memory_extractor": SourceContract(lane="session", payload_kind="extracted", idem_algorithm="events+schema+semantic_hash"),
    "evolution_optimizer": SourceContract(lane="session", payload_kind="evolution", idem_algorithm="events+schema+semantic_hash"),
    "reflect": SourceContract(lane="run", payload_kind="reflect", idem_algorithm="run_id+lesson_hash"),
    "cultivation": SourceContract(lane="scheduled", payload_kind="cultivation", idem_algorithm="events+schema+semantic_hash"),
}


def idem_key(source: str, payload: SourcePayload, envelope: Envelope, *, semantic_slot: str = "") -> str:
    """Return a stable source-specific idempotency key; never use array position."""
    contract = SOURCE_CONTRACTS.get(source)
    if contract is None or payload.kind != contract.payload_kind:
        raise ValueError("unsupported_schema")
    if payload.kind == "backflow":
        material = f"{source}|{CONTRACT_VERSION}|{payload.message_id}"
    elif payload.kind == "reflect":
        lesson_hash = hashlib.sha256(
            json.dumps([item.model_dump(mode="json") for item in payload.lessons], sort_keys=True).encode("utf-8")
        ).hexdigest()
        material = f"{source}|{CONTRACT_VERSION}|{payload.run_id}|{lesson_hash}|{semantic_slot}"
    else:
        event_ids = ",".join(sorted(set(envelope.source_event_ids)))
        semantic = hashlib.sha256(
            json.dumps(payload.model_dump(mode="json"), ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        material = f"{source}|{CONTRACT_VERSION}|{event_ids}|{semantic}|{semantic_slot}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()
