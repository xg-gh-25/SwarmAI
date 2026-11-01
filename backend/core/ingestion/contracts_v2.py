"""CDF unit contract v2 (design r16.5 §5.3): the ONE source for unit kinds, ontology,
routing, trust floors and source ownership.

Every source submits one ``SourceUnit`` (Cognition | Event | Gap | Reference).
``route_unit``, the DB CHECK values and the design-doc tables are generated from or
validated against this module; nothing re-declares them.

Key public symbols:
- ``TYPES`` / ``TYPE_TO_LAYER`` / ``layer_of`` — the seven cognition types; layer is
  derived from type only (source never supplies it).
- ``CognitionUnit`` / ``EventUnit`` / ``GapUnit`` / ``ReferenceUnit`` / ``SourceUnit`` /
  ``SOURCE_UNIT`` — what a source submits; ``SEMANTIC_FIELDS`` lists the fields that
  must never be truncated (first principle).
- ``REFERENCE_FOLDERS``, ``slugify``, ``link_file_for``, ``DestinationV2``,
  ``route_unit`` — the destination is a pure function of the unit's own kind, scope,
  type and topic (design 2026-10-29-cdf-refactor §3.1); there is no reroute.
- ``URL_CREDENTIAL_PARAMS`` / ``sanitize_source_url`` / ``semantic_urls_have_credentials``
  — §5.12 credential handling (before secret scan).
- ``S1_MAX_UNITS_PER_MESSAGE``, ``ExtractionPass``, ``TRUSTED_PASS_ROOT_KINDS`` — §9.1.
- ``DeferReason`` / ``PARKED_DEFER_REASONS`` — deferrals that consume no retry.
- ``expand_projects`` — §7 D6 deterministic multi-project fan-out.
- ``SOURCES_V2`` — S1..S6 lane and owned evidence-root kinds (§5.2).
- ``EvidenceRootKind`` / ``RootTrust`` / ``ROOT_TRUST_ORDER`` — §9 provenance tiers.
- ``UnitOutcome`` — terminal/non-terminal unit states incl. ``reinforced``.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator


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
# A gap is a weak point of the project that stays until fixed. Work a session leaves
# for later is not a gap: the session's activity row says where it stopped, and a ToDo
# is written by the person or the agent in the session (design
# 2026-10-31-continue-lookback-todo D2).
GapKind = Literal["debt", "risk", "deferred_finding", "process_issue"]


# What _valid_scope accepts, in words: shown to the extractor (extract_llm.unit_schema),
# which cannot see this validator. Change both together.
# how long a cognition unit should be, stated to the extractors (extract_llm.UNIT_SIZE_RULE)
# as a writing rule; nothing refuses a unit for its length (design 2026-10-29-cdf-refactor
# §3.5: card 0 counted characters and dropped the most complete units)
MAX_COGNITION_CHARS = 600
# The reach is part of the definition (design 2026-10-31-memory-chain-audit M1): agent and
# world are read in every session (EVOLUTION, MEMORY), so reliability alone must not put a
# fact there; knowledge that matters only when one tool, library, API or subject comes up
# is recalled when it does (a project's DDD, or the General Brain under that subject).
SCOPE_RULE = ('one of "user", "agent", "world", or "project:<project name>". "agent" and '
              '"world" are read in every future session: only what changes how you judge in '
              'most tasks, whatever the project (principles, decisions, models, corrections, '
              'broad lessons). Knowledge that matters only when one tool, library, API or '
              'subject comes up, however reliable, is "project:<that project or subject>"')
TOPIC_RULE = ('for scope=project:X only, which project document it belongs in: "decision" (a '
              'project decision), "product" (what the product is for, its users, non-goals), '
              '"architecture" (how it is built, conventions, runtime traps), "lesson" (what '
              'worked or failed while building it)')
SKILL_RULE = ('the skill this unit is about, when it is about how one skill behaved: its '
              'folder name as invoked (e.g. "s_pdf"); empty otherwise. A correction that names '
              'a skill counts against that skill\'s success (skill health)')
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


# a gap's id is its candidate id (the ``<!-- cand:<id>.unit -->`` marker the writer puts
# on it); a run sees at most GAP_LIST_TOP open gaps and may close only those
GAP_ID_RE = re.compile(r"cand-[0-9a-f]{8,64}")
GAP_LIST_TOP = 20
# the entries one unit may declare it makes untrue: a change that removes a mechanism makes
# every entry about it untrue, and each declaration is checked on its own (overrides.check)
OVERRIDES_TOP = 10


class _Unit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    evidence: list[EvidenceAnchor] = Field(min_length=1)
    relation_hint: dict | None = None  # {"kind": supersedes|derived_from, "target": anchor}

    @model_validator(mode="after")
    def _scope_ok(self):
        if not _valid_scope(getattr(self, "scope", None)):
            raise ValueError("invalid scope")
        return self


# An existing entry a decision context says this unit replaces (design 2026-10-30-
# brain-driven §2.1): "<8-character id> | <its first line>", the id as ``knowledge find`` /
# ``get`` printed it, the first line so a wrong id cannot retire another entry. A flat
# string: the structured-output schema carries no nested objects (extract_llm._flat).
OverrideRef = Annotated[str, Field(pattern=r"^[0-9a-f]{8} \| \S.{6,298}$")]
OVERRIDE_RE = re.compile(r"^([0-9a-f]{8}) \| (.+)$", re.S)


# the sources that may declare an override: the after-session review (the user's own
# words) and a pipeline run's report (its own change). Any other source's are ignored.
OVERRIDE_SOURCES = frozenset({"S7", "S3"})


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
    # which project document a scope=project:X unit belongs in (DDD_MATRIX); the
    # extractor, who has the context, says it (design 2026-10-29-cdf-refactor §3.1)
    topic: Literal["decision", "product", "architecture", "lesson"] | None = None
    # entries this unit replaces, declared by the context that made the new decision
    overrides: list[OverrideRef] = Field(default_factory=list, max_length=OVERRIDES_TOP)
    # the user's own words this unit rests on, copied verbatim from a cited user message
    # (design 2026-10-30-cdf-agent-decides §2): the only thing that counts as "the user
    # said it" for the files injected every turn; checked as a substring by code
    user_quote: str | None = Field(default=None, max_length=2000)
    # what the producing agent declared about existing entries (§3 D1), each
    # "<8-character id> | <first line>": the same claim (``same``), the same subject
    # with something new (``adds``), and for a correction the earlier correction of the
    # same mistake (``repeats``). Code only checks that id and first line name one entry.
    same: OverrideRef | None = None
    adds: OverrideRef | None = None
    repeats: OverrideRef | None = None
    # the skill the unit is about (SKILL_RULE), named by the agent that saw it used; a
    # correction naming one is what skill health counts (queries.corrections)
    skill: str | None = Field(default=None, max_length=80)

    @model_validator(mode="after")
    def _conditional(self):
        if self.repeats and self.type != "correction":
            raise ValueError("repeats only for type=correction")
        if self.same and self.adds:
            raise ValueError("same and adds exclude each other")
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
    event_kind: Literal["event", "month_summary", "month_supplement"] = "event"
    # the open gaps of the run's project this event fixed, by id (a pipeline run's
    # REFLECT report only; design 2026-10-30-cdf-gap-lifecycle §2)
    closes: list[str] | None = Field(default=None, max_length=GAP_LIST_TOP)
    # declared in the review's relation step (design 2026-10-31-extraction-at-task-end
    # E3): the event already recorded (``same``: not written again), and the events or
    # gaps of this same session this one completes or restates (``overrides``: retired,
    # a gap as closed). overrides.check holds them to this session's own entries.
    same: OverrideRef | None = None
    overrides: list[OverrideRef] = Field(default_factory=list, max_length=OVERRIDES_TOP)

    @model_validator(mode="before")
    @classmethod
    def _no_follow_up(cls, data):
        """A payload stored, or sent by a Hive, before follow_up was removed (design
        2026-10-31-continue-lookback-todo D2) still carries the key (null when unset):
        it is dropped, so the event stays valid. Open work is not a CDF unit."""
        if isinstance(data, dict) and "follow_up" in data:
            data = {k: v for k, v in data.items() if k != "follow_up"}
        return data

    @model_validator(mode="after")
    def _impact_or_outcome(self):
        if not (self.impact or self.outcome):
            raise ValueError("event needs impact or outcome")
        bad = [gap for gap in self.closes or () if not GAP_ID_RE.fullmatch(gap)]
        if bad:
            raise ValueError(f"closes names no gap id: {bad}")
        return self


class GapUnit(_Unit):
    """A weak point of the project still open: debt, a risk, a deferred finding, a
    process issue (§5.5 S3 part 5). Never reinforced."""

    kind: Literal["gap"] = "gap"
    gap_kind: GapKind
    scope: Scope
    title: str = Field(min_length=1)
    what: str = Field(min_length=1)
    why: str | None = None
    proposed_next: str | None = None
    same: OverrideRef | None = None      # see CognitionUnit
    adds: OverrideRef | None = None


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
    "event": ("title", "what", "impact", "outcome"),
    "gap": ("title", "what", "why", "proposed_next"),
    "reference": ("title", "summary", "body", "source_url"),
}

# ── card 1 v2 answers (re-check) ──────────────────────────────────────────────
# TIMELINE is injected every turn, so an event must change how the work stands. Stated
# by both extraction prompts (extract_llm, s7_review) and the card 1 event check.
EVENT_RULE = ("An event is recorded only when it changes how the work stands: a deliverable "
              "the user asked for landed, a decision or change of direction (and why), a "
              "milestone, an incident (and how it was resolved), real loss or rework, "
              "worded as it stands at the end. Routine operations are not events whether "
              "they succeed or fail: a build, a restart, a deploy, an install, a test run, "
              "checking a result, dispatching the next step of a plan. An event is never a "
              "status (not pushed, not built) and never work still to do: open work is a "
              "ToDo, written by the session's own activity summary.\n")
def event_date_rule() -> str:
    """The date an event falls back to: with none in the source the model invented one
    (2025-01-01T00:00:00), which filed the event under a month of its own."""
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).date().isoformat()
    return (f"Date an event (when) from the source; when the source shows no date, use "
            f"today, {today}.\n")


# ── routing (§5.10, §5.12, §9.1) ──────────────────────────────────────────────
# Reference kinds write only to Learned / Library / Notes (r16.3). Reports/ and Designs/
# are user deliverables, not governed.
REFERENCE_FOLDERS: dict[str, str] = {
    "learned": "Knowledge/Learned", "spec": "Knowledge/Library", "link": "Knowledge/Library/Links",
    "analysis": "Knowledge/Notes", "design": "Knowledge/Notes", "note": "Knowledge/Notes",
}
TIMELINE_PATH = ".context/TIMELINE.md"
# COMMON_DDD is a General Brain document (Knowledge/Library/<subject>.md, sectioned like a
# DDD): the home of a unit whose project does not exist (route_general). rerouted_from
# marks a unit its evidence kept out of the every-turn files (pipeline_v2._admitted).
StoreV2 = Literal["KNOWLEDGE", "COMMON_DDD", "STEERING", "USER", "TOOLS", "EVOLUTION", "DDD", "MEMORY", "TIMELINE"]


class DestinationV2(BaseModel):
    model_config = ConfigDict(extra="forbid")
    store: StoreV2
    relative_path: str
    section: str | None = None
    project: str | None = None
    injectable: bool = True  # False => recall-only
    rerouted_from: str | None = None  # pre-refactor decisions only (see StoreV2)


def slugify(text: str) -> str:
    """Deterministic slug (§5.10): NFKC, lower, non-alphanumeric -> '-', collapse."""
    import re
    import unicodedata
    norm = unicodedata.normalize("NFKC", text).lower()
    return re.sub(r"-{2,}", "-", re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "-", norm)).strip("-")


def link_file_for(source_url: str) -> str:
    """Links are filed by canonical host (deterministic, §5.12)."""
    from urllib.parse import urlsplit
    host = (urlsplit(source_url).hostname or "").lower().rstrip(".")
    if not host:
        raise ValueError("link needs a host")
    return f"{REFERENCE_FOLDERS['link']}/{host}.md"


def snapshot_project(snapshot: object) -> str | None:
    """The project a source declared for its unit in ``project_snapshot``:
    ``explicit_project`` (S1, Hive lessons) or ``project`` (S3, S4, S6)."""
    if not isinstance(snapshot, dict):
        return None
    for key in ("explicit_project", "project"):
        value = snapshot.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def project_candidates(unit: object, snapshot: object) -> list[str]:
    """The projects the judge may choose for *unit*. A project the source declared
    (``snapshot_project``) is the one candidate, and the router falls back to it when the
    judge gives none. The project a session worked in (``session_project``) is only
    offered, and only to a unit its extractor scoped to a project: offered to an agent,
    user or world unit, the judge moved it into that project (A/B on 54 live units,
    2026-10-29), where sessions of other projects cannot reach it."""
    declared = snapshot_project(snapshot)
    if declared:
        return [declared]
    session = snapshot.get("session_project") if isinstance(snapshot, dict) else None
    scope = getattr(unit, "scope", None) or ""
    if isinstance(session, str) and session.strip() and scope.startswith("project:"):
        return [session.strip()]
    return []


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
# inferred: not external content, and not shown by the user's words or a measurement (an
# entry CDF wrote from the agent's own reading); a unit citing it is at most inferred
# and is not external because of it (cdf-fixing-spec C1)
RootTrust = Literal["user", "measured", "inferred", "external"]
ROOT_TRUST_ORDER: dict[str, int] = {"external": 0, "inferred": 1, "measured": 2, "user": 3}
EvidenceRootKind = Literal[
    "user_message", "human_answer", "measured_field", "corpus_unit", "agent_document",
    "run_finding", "run_validation", "commit", "file_revision", "tool_text", "web_document",
    "metric_window", "channel_message",
]

# ── sources (§5.2) ────────────────────────────────────────────────────────────
SourceId = Literal["S0", "S1", "S2", "S3", "S4", "S5", "S6", "S7"]  # S0 = system summaries (summary lane)
SOURCES_V2: dict[str, dict] = {
    "S0": {"name": "system_summary", "lane": "summary"},  # TIMELINE month summaries only
    "S1": {"name": "user_directive", "lane": "immediate"},
    "S2": {"name": "session", "lane": "session"},
    "S3": {"name": "run", "lane": "run"},
    "S4": {"name": "code_change", "lane": "scheduled"},
    "S5": {"name": "metrics", "lane": "scheduled"},
    "S6": {"name": "external", "lane": "scheduled"},
    # S7: the after-session review in a fork of the same session (2026-10-29-cdf-session-review.md)
    "S7": {"name": "session_review", "lane": "session"},
}

# A Hive run's lessons (core/hive_lessons): S6 units whose source_ref starts with this.
# Their project is fixed by the host (project_hint = the run's Brain, or none for the
# General Brain); the judge may not move them to another project (pipeline_v2).
HIVE_REF_PREFIX = "hive:"

# ── trusted passes, S1, entailment (§9.1, §9.2) ───────────────────────────────
ExtractionPass = Literal["main", "user_utterance", "human_answer", "s1"]
# A unit from a trusted pass is accepted only if EVERY evidence root is of these kinds and
# owned by the unit's session (user_message) or run (human_answer).
TRUSTED_PASS_ROOT_KINDS: dict[str, frozenset[str]] = {
    "user_utterance": frozenset({"user_message"}),
    "human_answer": frozenset({"human_answer"}),
}
S1_MAX_UNITS_PER_MESSAGE = 3


# ── deferrals (§5.10, §9.2, §7) ───────────────────────────────────────────────
DeferReason = Literal["ambiguous_project", "missing_section", "entailment_unavailable", "writer_busy", "lane_backoff"]
# Parked: consumes no retry, never becomes failed_poison, excluded from the 24h deferred
# age gate; re-queued when the awaited thing appears; purged at 30 days (§4.4).
PARKED_DEFER_REASONS: frozenset[str] = frozenset({"missing_section"})


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


# ── routing: a pure function of what the extractor declared (cdf-refactor §3.1) ──

USER_KIND_FILE = {"preference": "STEERING", "profile": "USER", "environment": "TOOLS"}
# a project unit that did not say which document (an S3 run report has no topic):
# what was learned while building it is a lesson, a decision a decision, a model or
# principle of how it is built its architecture
_DEFAULT_TOPIC = {"pitfall": "lesson", "correction": "lesson", "guideline": "lesson",
                  "process": "lesson", "decision": "decision", "model": "architecture",
                  "principle": "architecture"}


GENERAL_BRAIN_DIR = "Knowledge/Library"


def route_general(unit: "SourceUnit", subject: str | None) -> DestinationV2:
    """The General Brain home (design 2026-10-30-cdf-agent-decides §3 D7) of a project
    unit or gap whose project does not exist, and of a unit its evidence keeps out of the
    every-turn files when it has no project: one Library document per named *subject*,
    sectioned like a DDD document. Recalled with the Library, never injected."""
    from .contracts import DDD_MATRIX
    rel = f"{GENERAL_BRAIN_DIR}/{slugify(subject or '') or 'general'}.md"
    if isinstance(unit, GapUnit):
        section = "Open Gaps"
    else:
        topic = getattr(unit, "topic", None) or _DEFAULT_TOPIC[unit.type]
        section = DDD_MATRIX[topic][unit.type][1]
    return DestinationV2(store="COMMON_DDD", relative_path=rel, section=section,
                         project=subject or None, injectable=False)


def route_unit(unit: "SourceUnit", project: str | None) -> DestinationV2:
    """Where *unit* goes, from its own declaration and the resolved *project* (the
    existing project its scope or its source named). Raises ``ValueError(reason)``."""
    from core.ddd_entry_lifecycle import MEMORY_TYPE_TO_SECTION
    from .contracts import DDD_MATRIX
    if isinstance(unit, EventUnit):
        return DestinationV2(store="TIMELINE", relative_path=TIMELINE_PATH)
    if isinstance(unit, ReferenceUnit):
        if unit.reference_kind == "link":
            if not unit.source_url:
                raise ValueError("link needs source_url")
            rel = link_file_for(unit.source_url)
        else:
            rel = REFERENCE_FOLDERS[unit.reference_kind] + "/"
        return DestinationV2(store="KNOWLEDGE", relative_path=rel, project=project, injectable=False)
    from core.ddd_paths import ddd_new_path
    if isinstance(unit, GapUnit):
        if not project:
            raise ValueError("gap_without_project")
        rel = ddd_new_path(Path("Projects") / project, "IMPROVEMENT.md").as_posix()
        return DestinationV2(store="DDD", relative_path=rel, section="What to Watch For",
                             project=project, injectable=False)
    scope = unit.scope
    if scope == "user":
        store = USER_KIND_FILE[unit.user_kind or "preference"]
        return DestinationV2(store=store, relative_path=f".context/{store}.md")
    if scope == "agent":
        section = "Corrections Captured" if unit.type == "correction" else "Competence Learned"
        return DestinationV2(store="EVOLUTION", relative_path=".context/EVOLUTION.md", section=section)
    if scope == "world":
        return DestinationV2(store="MEMORY", relative_path=".context/MEMORY.md",
                             section=MEMORY_TYPE_TO_SECTION[unit.type])
    if not project:
        raise ValueError("unknown_project")
    topic = unit.topic or _DEFAULT_TOPIC[unit.type]
    file_name, section = DDD_MATRIX[topic][unit.type]
    rel = ddd_new_path(Path("Projects") / project, file_name).as_posix()
    return DestinationV2(store="DDD", relative_path=rel, section=section, project=project,
                         injectable=False)
