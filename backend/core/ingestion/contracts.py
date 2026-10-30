"""The shapes CDF's stored decisions keep, and the DDD routing matrix.

- ``UnitDecision`` (with ``Card0Answer`` and the answer types its fields name): what
  pipeline_v2 decides for a unit and the worker stores; decisions judged by the retired
  v1 cards (2026-10-29) carry the card answers, v2 ones leave them empty.
- ``DDD_MATRIX`` / ``validate_ddd_matrix``: which project document and section a project
  unit of a topic and type goes to (contracts_v2.route_unit).
- The version strings a model call records (llm.BedrockJudge.version_vector).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from core.ddd_entry_lifecycle import VALID_TYPES

CONTRACT_VERSION = "cdf-contract-r15.1-v1"
NORMALIZER_VERSION = "cdf-normalizer-v1"
RULES_VERSION = "cdf-rules-r15.1-v1"
PROMPT_VERSION = "cdf-prompts-r15.1-v4"
VALID_TYPE = Literal["guideline", "pitfall", "decision", "model", "process", "principle", "correction"]
Topic = Literal["decision", "product", "architecture", "lesson"]
ContentForm = Literal["rule", "reference"]
EvidenceStrength = Literal["inferred", "run_lesson", "review", "measured", "user_explicit"]
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


validate_ddd_matrix()
