"""CDF unit contract v2 (design r16.5 §5.3): the ONE source for unit kinds, ontology,
routing, trust floors and source ownership.

Every source submits one ``SourceUnit`` (Cognition | Event | Gap | Reference). Card 1
v2 answers, ``route_for_v2``, the DB CHECK values and the design-doc tables are
generated from or validated against this module; nothing re-declares them.

Key public symbols:
- ``TYPES`` / ``TYPE_TO_LAYER`` / ``layer_of`` — the seven cognition types; layer is
  derived from type only (source never supplies it).
- ``CognitionUnit`` / ``EventUnit`` / ``GapUnit`` / ``ReferenceUnit`` / ``SourceUnit`` /
  ``SOURCE_UNIT`` — what a source submits; ``SEMANTIC_FIELDS`` lists the fields that
  must never be truncated (first principle).
- ``EventAnswer`` / ``GapAnswer`` / ``ReferenceAnswerV2`` / ``Card1AnswerV2`` — card 1
  re-check answers per kind (Cognition reuses the r15 shapes from ``contracts``).
- ``REFERENCE_FOLDERS``, ``LEARNED_*`` dirs, ``LEARNED_DOMAINS``, ``slugify``,
  ``link_file_for``, ``DestinationV2``, ``route_for_v2`` — total, deterministic routing
  incl. the §9.1 reroute of tainted per-turn rules to ``Learned/_common/``.
- ``URL_CREDENTIAL_PARAMS`` / ``sanitize_source_url`` / ``semantic_urls_have_credentials``
  — §5.12 credential handling (before secret scan).
- ``S1_MAX_UNITS_PER_MESSAGE``, ``ExtractionPass``, ``TRUSTED_PASS_ROOT_KINDS``,
  ``EntailmentVerdict`` — §9.1/§9.2.
- ``DeferReason`` / ``PARKED_DEFER_REASONS`` — deferrals that consume no retry.
- ``expand_projects`` — §7 D6 deterministic multi-project fan-out.
- ``PER_TURN_STORES`` / ``trust_floor_allows`` — §9 per-destination trust floor.
- ``SOURCES_V2`` — S1..S6 lane and owned evidence-root kinds (§5.2).
- ``EvidenceRootKind`` / ``RootTrust`` / ``ROOT_TRUST_ORDER`` — §9 provenance tiers.
- ``UnitOutcome`` — terminal/non-terminal unit states incl. ``reinforced``.
"""
from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from .contracts import (
    DDD_MATRIX, MEMORY_TYPE_TO_SECTION, QualityAnswers, Card1Answer, Destination,
)

CONTRACT_V2_VERSION = "cdf-contract-r16.5-v1"
SYSTEM_APPENDIX_HEADING = "## 系统追加（自动提取）"

# ── ontology (§5.3) ───────────────────────────────────────────────────────────
CognitionType = Literal["principle", "correction", "decision", "model", "guideline", "pitfall", "process"]
TYPES: tuple[str, ...] = ("principle", "correction", "decision", "model", "guideline", "pitfall", "process")
CognitiveLayer = Literal["meta", "cognitive", "operational"]
TYPE_TO_LAYER: dict[str, str] = {
    "principle": "meta", "correction": "meta",
    "decision": "cognitive", "model": "cognitive",
    "guideline": "operational", "pitfall": "operational", "process": "operational",
}
TYPE_LABEL_ZH: dict[str, str] = {
    "principle": "原则", "correction": "纠错", "decision": "决定", "model": "心智模型",
    "guideline": "经验", "pitfall": "坑", "process": "流程",
}


def layer_of(type_: str) -> str:
    """The cognitive layer is derived from the type and nothing else."""
    return TYPE_TO_LAYER[type_]


Scope = str  # "user" | "agent" | "world" | "project:<name>"
EvidenceAnchor = Annotated[str, Field(min_length=3, pattern=r"^(event|finding|run_event|doc|commit|unit|metric|web):.+")]
ReferenceKind = Literal["learned", "analysis", "design", "spec", "note", "link"]
GapKind = Literal["debt", "risk", "deferred_finding", "process_issue", "follow_up"]


# What _valid_scope accepts, in words: shown to the extractor (extract_llm.flat_unit_schema),
# which cannot see this validator. Change both together.
SCOPE_RULE = 'one of "user", "agent", "world", or "project:<project name>"'
# The model validators below, in words, per kind (same contract: change both together).
UNIT_RULES: dict[str, tuple[str, ...]] = {
    "cognition": (
        "type=decision needs why and rejected_alternatives (write 'none' if none)",
        "type=pitfall needs its trigger condition in applicability",
        "type=process needs how",
        "scope=user needs user_kind; user_kind only for scope=user",
    ),
    "event": ("needs impact or outcome", "when is an ISO date or timestamp (at least 10 characters)"),
    "gap": (),
    "reference": ("source_url, when given, is https with a host and no credentials",
                  "reference_kind=link needs source_url"),
}


def _valid_scope(value: str | None) -> bool:
    return value is None or value in {"user", "agent", "world"} or (
        value.startswith("project:") and len(value) > len("project:"))


class _Unit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    evidence: list[EvidenceAnchor] = Field(min_length=1)
    relation_hint: dict | None = None  # {"kind": supersedes|derived_from, "target": anchor}

    @model_validator(mode="after")
    def _scope_ok(self):
        if not _valid_scope(getattr(self, "scope", None)):
            raise ValueError("invalid scope")
        return self


class CognitionUnit(_Unit):
    """A judged, reusable claim. Fields left empty are left empty: never invented."""

    kind: Literal["cognition"] = "cognition"
    type: CognitionType
    scope: Scope
    user_kind: Literal["preference", "profile", "environment"] | None = None  # scope=user only
    title: str = Field(min_length=1)
    what: str = Field(min_length=1)
    how: str | None = None
    why: str | None = None
    applicability: str | None = None
    rejected_alternatives: str | None = None

    @model_validator(mode="after")
    def _conditional(self):
        if self.type == "decision" and not (self.why and self.rejected_alternatives):
            raise ValueError("decision needs why and rejected_alternatives (write 'none' if none)")
        if self.type == "pitfall" and not self.applicability:
            raise ValueError("pitfall needs a trigger condition in applicability")
        if self.type == "process" and not self.how:
            raise ValueError("process needs how")
        if self.scope == "user" and self.user_kind is None:
            raise ValueError("scope=user needs user_kind")
        if self.scope != "user" and self.user_kind is not None:
            raise ValueError("user_kind only for scope=user")
        return self


class EventUnit(_Unit):
    """A significant event for TIMELINE (§5.6)."""

    kind: Literal["event"] = "event"
    when: str = Field(min_length=10)  # ISO-8601 date or datetime
    scope: Scope | None = None
    title: str = Field(min_length=1)
    what: str = Field(min_length=1)
    impact: str | None = None
    outcome: str | None = None
    follow_up: str | None = None
    event_kind: Literal["event", "month_summary", "month_supplement"] = "event"

    @model_validator(mode="after")
    def _impact_or_outcome(self):
        if not (self.impact or self.outcome):
            raise ValueError("event needs impact or outcome")
        return self


class GapUnit(_Unit):
    """An open improvement area / follow-up (§5.5 S3 part 5). Never reinforced."""

    kind: Literal["gap"] = "gap"
    gap_kind: GapKind
    scope: Scope
    title: str = Field(min_length=1)
    what: str = Field(min_length=1)
    why: str | None = None
    proposed_next: str | None = None


class ReferenceUnit(_Unit):
    """A self-contained document for Knowledge/ (§5.12)."""

    kind: Literal["reference"] = "reference"
    reference_kind: ReferenceKind
    scope: Scope | None = None
    title: str = Field(min_length=1)
    summary: str = Field(min_length=1)  # one complete sentence, never sliced
    body: str = Field(min_length=1)  # for kind=link: why it is worth keeping, what it covers
    ontology_types: list[CognitionType] = Field(default_factory=list)
    # External / internet provenance (§5.12 外部链接). Required for kind=link; for any other
    # kind it records where fetched content came from.
    source_url: str | None = None
    retrieved_at: str | None = None
    content_hash: str | None = None  # of what was read at retrieved_at, if fetched

    @model_validator(mode="after")
    def _link(self):
        if self.source_url is not None:
            from urllib.parse import urlsplit
            parts = urlsplit(self.source_url)
            if parts.scheme != "https" or not parts.netloc or parts.username or parts.password:
                raise ValueError("source_url must be https with a host and no credentials")
        if self.reference_kind == "link" and not self.source_url:
            raise ValueError("link needs source_url")
        return self


SourceUnit: TypeAlias = Annotated[
    CognitionUnit | EventUnit | GapUnit | ReferenceUnit, Field(discriminator="kind")]
SOURCE_UNIT = TypeAdapter(SourceUnit)

# First principle: these fields are never truncated, regex-stripped or line-folded.
SEMANTIC_FIELDS: dict[str, tuple[str, ...]] = {
    "cognition": ("title", "what", "how", "why", "applicability", "rejected_alternatives"),
    "event": ("title", "what", "impact", "outcome", "follow_up"),
    "gap": ("title", "what", "why", "proposed_next"),
    "reference": ("title", "summary", "body", "source_url"),
}

# ── card 1 v2 answers (re-check) ──────────────────────────────────────────────
Significance = Literal["direction_change", "loss_or_rework", "milestone", "key_decision", "user_asked"]


class EventAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    shape: Literal["Event"]
    significance: list[Significance]  # empty list => not significant => reject
    self_contained: bool
    evidenced: bool


class GapAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    shape: Literal["Gap"]
    actionable: bool
    evidenced: bool
    self_contained: bool
    project: str | None = None


class ReferenceAnswerV2(BaseModel):
    model_config = ConfigDict(extra="forbid")
    shape: Literal["ReferenceV2"]
    reference_kind: ReferenceKind
    quality: QualityAnswers  # self_contained + valuable-outside-origin carry the weight
    project: str | None = None


Card1AnswerV2: TypeAlias = Annotated[
    EventAnswer | GapAnswer | ReferenceAnswerV2, Field(discriminator="shape")]
CARD1_V2_ADAPTER = TypeAdapter(Card1AnswerV2)
# A CognitionUnit is re-checked with the r15 ``contracts.Card1Answer`` shapes.

# ── routing (§5.10, §5.12, §9.1) ──────────────────────────────────────────────
# Reference kinds write only to Learned / Library / Notes (r16.3). Reports/ and Designs/
# are user deliverables, not governed.
REFERENCE_FOLDERS: dict[str, str] = {
    "learned": "Knowledge/Learned", "spec": "Knowledge/Library", "link": "Knowledge/Library/Links",
    "analysis": "Knowledge/Notes", "design": "Knowledge/Notes", "note": "Knowledge/Notes",
}
# Common DDD layout (r16.5). Learned/ root keeps learned-kind Reference cards only.
LEARNED_DOMAINS_DIR = "Knowledge/Learned/domains"
LEARNED_REPOS_DIR = "Knowledge/Learned/repos"
LEARNED_COMMON_DIR = "Knowledge/Learned/_common"
COMMON_DDD_GAP_SECTION = "Open Gaps"
TIMELINE_PATH = ".context/TIMELINE.md"
# Seed list of domains; the closed list at runtime is this ∪ existing domains/*.md stems.
# Users add a domain by creating an empty file under domains/ (governed_edit), never by
# editing this tuple.
LEARNED_DOMAINS: tuple[str, ...] = ("general-engineering", "product-design", "writing", "data", "ops")

StoreV2 = Literal["KNOWLEDGE", "COMMON_DDD", "STEERING", "USER", "TOOLS", "EVOLUTION", "DDD", "MEMORY", "TIMELINE"]
PER_TURN_STORES: frozenset[str] = frozenset({"STEERING", "USER", "TOOLS", "EVOLUTION", "MEMORY", "TIMELINE"})
# §9.1 reroute target for a tainted rule whose destination is a per-turn file.
REROUTE_FILE: dict[str, str] = {
    "STEERING": f"{LEARNED_COMMON_DIR}/user.md", "USER": f"{LEARNED_COMMON_DIR}/user.md",
    "TOOLS": f"{LEARNED_COMMON_DIR}/user.md", "EVOLUTION": f"{LEARNED_COMMON_DIR}/agent.md",
    "MEMORY": f"{LEARNED_COMMON_DIR}/world.md",
}


class DestinationV2(BaseModel):
    model_config = ConfigDict(extra="forbid")
    store: StoreV2
    relative_path: str
    section: str | None = None
    project: str | None = None
    injectable: bool = True  # False => recall-only
    rerouted_from: str | None = None  # §9.1: original per-turn store of a tainted rule


def slugify(text: str) -> str:
    """Deterministic slug (§5.10): NFKC, lower, non-alphanumeric -> '-', collapse."""
    import re
    import unicodedata
    norm = unicodedata.normalize("NFKC", text).lower()
    return re.sub(r"-{2,}", "-", re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "-", norm)).strip("-")


def known_domains(existing_stems: tuple[str, ...] | list[str] = ()) -> frozenset[str]:
    """Closed domain list = seed ∪ existing domains/*.md stems (slugified). '_'-prefixed
    names are reserved and never a domain."""
    names = {slugify(n) for n in (*LEARNED_DOMAINS, *existing_stems) if n and not n.startswith("_")}
    return frozenset(n for n in names if n)


def link_file_for(source_url: str) -> str:
    """Links are filed by canonical host (deterministic, §5.12)."""
    from urllib.parse import urlsplit
    host = (urlsplit(source_url).hostname or "").lower().rstrip(".")
    if not host:
        raise ValueError("link needs a host")
    return f"{REFERENCE_FOLDERS['link']}/{host}.md"


def _no_ddd_path(project: str | None, domain: str | None, domains: frozenset[str]) -> str:
    if project:
        return f"{LEARNED_REPOS_DIR}/{slugify(project)}.md"
    if domain is not None and slugify(domain) in domains:
        return f"{LEARNED_DOMAINS_DIR}/{slugify(domain)}.md"
    raise ValueError("unknown_domain")


def route_for_v2(unit_kind: str, answer: Card1Answer | Card1AnswerV2, *,
                 ddd_exists: bool, project_hint: str | None, tainted: bool,
                 domain: str | None = None, existing_domains: tuple[str, ...] = (),
                 source_url: str | None = None) -> DestinationV2:
    """Total, deterministic routing: every legal combination maps to exactly one file and
    section. Raises ``ValueError('unknown_domain')`` (parked, §5.10) or ``ValueError`` on an
    illegal pair. A tainted rule bound for a per-turn file is rerouted, never raised (§9.1)."""
    domains = known_domains(existing_domains)
    if unit_kind == "event":
        if not isinstance(answer, EventAnswer):
            raise ValueError("event needs EventAnswer")
        return DestinationV2(store="TIMELINE", relative_path=TIMELINE_PATH, injectable=not tainted)
    if unit_kind == "reference":
        if not isinstance(answer, ReferenceAnswerV2):
            raise ValueError("reference needs ReferenceAnswerV2")
        if answer.reference_kind == "link":
            if not source_url:
                raise ValueError("link needs source_url")
            rel = link_file_for(source_url)
        else:
            rel = REFERENCE_FOLDERS[answer.reference_kind] + "/"
        return DestinationV2(store="KNOWLEDGE", relative_path=rel,
                             project=answer.project or project_hint, injectable=False)
    if unit_kind == "gap":
        if not isinstance(answer, GapAnswer):
            raise ValueError("gap needs GapAnswer")
        project = answer.project or project_hint
        if project and ddd_exists:
            from core.ddd_paths import ddd_write_path
            rel = ddd_write_path(Path("Projects") / project, "IMPROVEMENT.md").as_posix()
            return DestinationV2(store="DDD", relative_path=rel, section="What to Watch For", project=project)
        return DestinationV2(store="COMMON_DDD", relative_path=_no_ddd_path(project, domain, domains),
                             section=COMMON_DDD_GAP_SECTION, project=project, injectable=False)
    if unit_kind != "cognition":
        raise ValueError(f"unknown unit kind {unit_kind}")
    from .contracts import route_for
    if getattr(answer, "shape", None) == "WorldCrossGlobal" and not getattr(answer, "global_relevance", True):
        # r16.3: world but not global -> common DDD domain file, keeps its type.
        section = MEMORY_TYPE_TO_SECTION[answer.entry_type]  # type: ignore[union-attr]
        return DestinationV2(store="COMMON_DDD", relative_path=_no_ddd_path(None, domain, domains),
                             section=section, injectable=False)
    base: Destination = route_for(answer)  # type: ignore[arg-type]
    if base.store == "KNOWLEDGE":
        raise ValueError("cognition cannot route to a reference folder")
    if base.store == "DDD" and not ddd_exists:
        return DestinationV2(store="COMMON_DDD", relative_path=_no_ddd_path(base.project, domain, domains),
                             section=base.section, project=base.project, injectable=False)
    if tainted and base.store in REROUTE_FILE:
        return DestinationV2(store="COMMON_DDD", relative_path=REROUTE_FILE[base.store], section=base.section,
                             project=base.project, injectable=False, rerouted_from=base.store)
    return DestinationV2(store=base.store, relative_path=base.relative_path, section=base.section,
                         project=base.project, injectable=base.store in PER_TURN_STORES)


def trust_floor_allows(store: str) -> bool:
    """§9: tainted content never reaches a per-turn injected store (TIMELINE takes a
    tainted event only into its non-injected zone)."""
    return store not in PER_TURN_STORES


def card3_corpus_excludes_common(dest: DestinationV2) -> bool:
    """§9.1 r16.5: when the destination is a per-turn file, card 3 does NOT search the
    rerouted copies in Learned/_common/ (a later trusted unit must not be blocked)."""
    return dest.store in PER_TURN_STORES


# ── URL credentials (§5.12) ───────────────────────────────────────────────────
PRESIGN_PARAMS: frozenset[str] = frozenset({"x-amz-signature", "x-amz-credential", "x-amz-security-token"})
URL_CREDENTIAL_PARAMS: frozenset[str] = frozenset({
    "token", "access_token", "sig", "signature", "key", "api_key", "apikey", "auth", "password", "secret",
}) | PRESIGN_PARAMS
_TRACKING_PREFIXES = ("utm_",)


def _is_presigned(names: set[str]) -> bool:
    return bool(names & PRESIGN_PARAMS) or {"signature", "expires"} <= names


def sanitize_source_url(url: str) -> str:
    """Return the canonical ``source_url``: presigned -> ValueError('credentialed_url');
    other credential params and tracking params dropped; lower-case scheme/host; no
    fragment; no trailing '/'. Runs before secret scanning and canonicalisation."""
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
    parts = urlsplit(url)
    if parts.scheme.lower() != "https" or not parts.hostname or parts.username or parts.password:
        raise ValueError("source_url must be https with a host and no credentials")
    pairs = parse_qsl(parts.query, keep_blank_values=True)
    names = {k.lower() for k, _ in pairs}
    if _is_presigned(names):
        raise ValueError("credentialed_url")
    kept = [(k, v) for k, v in pairs
            if k.lower() not in URL_CREDENTIAL_PARAMS and not k.lower().startswith(_TRACKING_PREFIXES)]
    netloc = parts.hostname.lower() + (f":{parts.port}" if parts.port else "")
    path = parts.path.rstrip("/")
    return urlunsplit(("https", netloc, path, urlencode(kept), ""))


def semantic_urls_have_credentials(*texts: str | None) -> bool:
    """§5.12 r16.4: a URL carrying a credential param inside summary/body rejects the whole
    unit (``credentialed_url``); semantic fields are never rewritten."""
    import re
    from urllib.parse import parse_qsl, urlsplit
    for text in texts:
        for url in re.findall(r"https?://[^\s<>()\"']+", text or ""):
            names = {k.lower() for k, _ in parse_qsl(urlsplit(url).query, keep_blank_values=True)}
            if names & URL_CREDENTIAL_PARAMS or _is_presigned(names):
                return True
    return False


# ── provenance tiers (§9) ─────────────────────────────────────────────────────
RootTrust = Literal["user", "measured", "external"]
ROOT_TRUST_ORDER: dict[str, int] = {"external": 0, "measured": 1, "user": 2}
EvidenceRootKind = Literal[
    "user_message", "human_answer", "measured_field", "corpus_unit", "agent_document",
    "run_finding", "run_validation", "commit", "file_revision", "tool_text", "web_document",
    "metric_window", "channel_message",
]

# ── sources (§5.2) ────────────────────────────────────────────────────────────
SourceId = Literal["S0", "S1", "S2", "S3", "S4", "S5", "S6"]  # S0 = system summaries (summary lane)
SOURCES_V2: dict[str, dict] = {
    "S0": {"name": "system_summary", "lane": "summary"},  # TIMELINE month summaries only
    "S1": {"name": "user_directive", "lane": "immediate"},
    "S2": {"name": "session", "lane": "session"},
    "S3": {"name": "run", "lane": "run"},
    "S4": {"name": "code_change", "lane": "scheduled"},
    "S5": {"name": "metrics", "lane": "scheduled"},
    "S6": {"name": "external", "lane": "scheduled"},
}

# ── trusted passes, S1, entailment (§9.1, §9.2) ───────────────────────────────
ExtractionPass = Literal["main", "user_utterance", "human_answer", "s1"]
# A unit from a trusted pass is accepted only if EVERY evidence root is of these kinds and
# owned by the unit's session (user_message) or run (human_answer).
TRUSTED_PASS_ROOT_KINDS: dict[str, frozenset[str]] = {
    "user_utterance": frozenset({"user_message"}),
    "human_answer": frozenset({"human_answer"}),
}
S1_MAX_UNITS_PER_MESSAGE = 3


class EntailmentVerdict(BaseModel):
    """Isolated entailment judge (§9.2): sees ONLY the user message text and the unit's
    what/how/why. Not a security boundary; failure/unavailability -> DEFER."""

    model_config = ConfigDict(extra="forbid")
    entailed: bool
    reason: str = Field(min_length=1)


# ── deferrals (§5.10, §9.2, §7) ───────────────────────────────────────────────
DeferReason = Literal["ambiguous_project", "unknown_domain", "missing_section", "entailment_unavailable", "writer_busy", "lane_backoff"]
# Parked: consumes no retry, never becomes failed_poison, excluded from the 24h deferred
# age gate; re-queued when the awaited thing appears; purged at 30 days (§4.4).
PARKED_DEFER_REASONS: frozenset[str] = frozenset({"unknown_domain", "missing_section"})


def expand_projects(unit_id: str, projects: list[str]) -> list[tuple[str, str]]:
    """§7 D6: one unit about N projects becomes N units (deterministic order, stable ids),
    each its own write op, sharing the same evidence roots."""
    uniq = sorted({p for p in projects if p})
    if not uniq:
        raise ValueError("expand_projects needs at least one project")
    if len(uniq) == 1:
        return [(unit_id, uniq[0])]
    return [(f"{unit_id}.p-{slugify(p)}", p) for p in uniq]


UnitOutcome = Literal[
    "queued", "merged", "judged", "writing", "written", "reinforced", "rejected",
    "deferred", "failed_poison", "expired_unprocessed",
]
TERMINAL_OUTCOMES = frozenset({"merged", "written", "reinforced", "rejected", "failed_poison", "expired_unprocessed"})

__all__ = [n for n in dir() if not n.startswith("_")]
