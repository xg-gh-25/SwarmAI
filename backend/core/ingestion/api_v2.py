"""CDF v2 frozen cross-workstream interface (design r16.5). Workstreams implement the
modules this file delegates to and never edit this file; signatures are frozen.

Owners:
- PROVENANCE: ``provenance.py`` — ``SCHEMA_V2_DDL`` tables below, ``register_root``,
  ``resolve_evidence``, ``record_extraction_attempt``, ``enqueue_unit``.
- PIPELINE: ``pipeline_v2.py`` / ``writer`` renderers — ``run_unit_v2``, ``reinforce_or_reject``,
  ``render_unit``, parked-deferral requeue; ``knowledge_store.sync_knowledge_file``
  (per-unit chunks for multi-unit files).
- SOURCES: ``sources/s1..s6`` — extractors that call ``enqueue_unit`` /
  ``record_extraction_attempt`` only.
- MEMORY: ``daily_activity_v2.py``, ``timeline.py``, ``summary_lane.py`` — DailyActivity
  generation/injection and TIMELINE summaries.

Public symbols: ``SCHEMA_V2_DDL``, ``EvidenceRoot``, ``ResolvedEvidence``,
``ExtractionAttempt``, ``register_root``, ``resolve_evidence``,
``record_extraction_attempt``, ``enqueue_unit``, ``ingest_link_status``,
``requeue_parked``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from .api import EnqueueResult
from .contracts_v2 import (
    EvidenceRootKind, ExtractionPass, RootTrust, SourceId, SourceUnit,
)

SCHEMA_V2_VERSION = 9

# Applied after api.SCHEMA_DDL in the same migration transaction (store.py owns DDL apply).
# What each entry CDF wrote rests on (its basis), kept for as long as the entry: the
# baseline registers the entry with that trust (writer._manifest_entry), so an entry not
# shown by the user's words is never cited as the user's. Not pruned with the 30-day
# records. (store._V9_DDL adds it to an existing store.)
UNIT_BASIS_DDL = """
CREATE TABLE IF NOT EXISTS unit_basis (
  unit_id TEXT PRIMARY KEY,
  basis TEXT NOT NULL CHECK (basis IN ('user','measured','inferred','external')),
  recorded_at TEXT NOT NULL
);
"""

SCHEMA_V2_DDL = """
CREATE TABLE IF NOT EXISTS evidence_roots (
  root_id TEXT PRIMARY KEY,                 -- sha256(root_kind|canonical locator|content_hash)
  root_kind TEXT NOT NULL,
  locator TEXT NOT NULL,                    -- canonical: session/event id, run:finding, commit sha, normalized URL, unit anchor
  content_hash TEXT NOT NULL,
  trust TEXT NOT NULL CHECK (trust IN ('user','measured','inferred','external')),
  parent_root_ids TEXT NOT NULL DEFAULT '[]',
  observed_at TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_roots_locator ON evidence_roots(root_kind, locator);

CREATE TABLE IF NOT EXISTS claim_support (
  claim_anchor TEXT NOT NULL,
  root_id TEXT NOT NULL REFERENCES evidence_roots(root_id),
  source_unit_id TEXT NOT NULL,
  weight REAL NOT NULL,
  polarity TEXT NOT NULL CHECK (polarity IN ('support','refute')),
  inactive INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  PRIMARY KEY (claim_anchor, root_id)
);

CREATE TABLE IF NOT EXISTS extraction_attempts (
  attempt_id TEXT PRIMARY KEY,
  source TEXT NOT NULL CHECK (source IN ('S0','S1','S2','S3','S4','S5','S6','S7')),
  evidence_range TEXT NOT NULL,             -- JSON: {session_id, msg_from, msg_to} | {run_id} | {commits} | {window}
  extractor_version TEXT NOT NULL,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  units_emitted INTEGER NOT NULL DEFAULT 0,
  units_invalid INTEGER NOT NULL DEFAULT 0,
  splits INTEGER NOT NULL DEFAULT 0,
  invalid_units TEXT NOT NULL DEFAULT '[]', -- JSON list of {raw, reason} (rejected_at_source)
  status TEXT NOT NULL CHECK (status IN ('running','ok','no_unit','failed')),
  error TEXT
);
CREATE INDEX IF NOT EXISTS ix_attempts_started ON extraction_attempts(started_at);

CREATE TABLE IF NOT EXISTS link_status (
  url_root_id TEXT PRIMARY KEY REFERENCES evidence_roots(root_id),
  url TEXT NOT NULL,
  last_checked TEXT,
  status TEXT NOT NULL DEFAULT 'unchecked' CHECK (status IN ('unchecked','ok','dead','unreachable')),
  http_status INTEGER                        -- only 404/410/NXDOMAIN => dead (§5.12)
);

CREATE TABLE IF NOT EXISTS redactions (
  anchor TEXT PRIMARY KEY,
  redacted_at TEXT NOT NULL,
  reason TEXT NOT NULL,
  vault_path TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS injection_selection (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  file_path TEXT NOT NULL,
  file_hash TEXT NOT NULL,
  selected_anchors TEXT NOT NULL,
  score_snapshot_version TEXT NOT NULL,
  computed_at TEXT NOT NULL,
  omitted_count INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_injection_selection_file
  ON injection_selection(file_path, id DESC);
""" + UNIT_BASIS_DDL
# v2 also widens: ingestion_units.state adds 'reinforced'; ingestion_units gains
# defer_reason TEXT (parked reasons consume no retry), rerouted_from TEXT, project TEXT
# (one row per project after D6 fan-out); ingestion_relations.predicate becomes
# IN ('supersedes','derived_from'); ingestion_inbox gains unit_kind TEXT, source_v2 TEXT,
# extraction_pass TEXT, evidence_root_ids TEXT, project_hint TEXT, dedup_key TEXT
# (indexed, NOT unique). ingestion_writes.op_kind adds 'user_redact'. store.py migrates.


@dataclass(frozen=True)
class EvidenceRoot:
    root_id: str
    root_kind: EvidenceRootKind
    locator: str
    content_hash: str
    trust: RootTrust
    parent_root_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class ResolvedEvidence:
    """Server-derived provenance for one unit (§9). Callers never supply trust.

    Two separate questions (cdf-fixing-spec C1), each read where it is the right one:
    *read_external*: the extractor read external content, or a root of what it read is
    unknown (fail-closed). It guards the user's own files and the measured entrance,
    where which input shaped a unit is not knowable (anti selective-citation).
    *cited_external*: the unit's own evidence, through its parents, includes external
    content. It decides the ``taint: external`` mark, TIMELINE placement and overrides."""

    root_ids: tuple[str, ...]
    lineage_root_ids: tuple[str, ...]          # transitive closure through parent_root_ids
    strength: Literal["user_explicit", "measured", "run_lesson", "inferred"]
    read_external: bool
    cited_external: bool = False
    unresolved: tuple[str, ...] = field(default=())  # anchors that failed to resolve


@dataclass
class ExtractionAttempt:
    source: SourceId
    evidence_range: dict[str, Any]
    extractor_version: str
    attempt_id: str = ""


def register_root(kind: EvidenceRootKind, locator: str, content: bytes | str, trust: RootTrust, *,
                  parent_root_ids: tuple[str, ...] = (), observed_at: str | None = None) -> str:
    """Idempotently register an evidence root; returns root_id. Agent documents MUST pass
    the roots they read as parents; their trust is computed as the worst parent's
    (the ``trust`` argument is ignored for kind=agent_document)."""
    from .provenance import register_root as _impl
    return _impl(kind, locator, content, trust, parent_root_ids=parent_root_ids, observed_at=observed_at)


def resolve_evidence(anchors: list[str], *, session_id: str | None, run_id: str | None,
                     read_root_ids: tuple[str, ...] = ()) -> ResolvedEvidence:
    """Resolve anchors to roots, verify ownership (session/run) and hashes, close over
    parents, compute strength/taint. ``read_root_ids`` = everything the extractor read
    in this attempt: ``read_external`` covers anchors ∪ read set (anti selective-citation),
    ``cited_external`` the anchors' own lineage."""
    from .provenance import resolve_evidence as _impl
    return _impl(anchors, session_id=session_id, run_id=run_id, read_root_ids=read_root_ids)


def record_extraction_attempt(attempt: ExtractionAttempt, *, status: str, units_emitted: int = 0,
                              invalid_units: list[dict] | None = None, splits: int = 0,
                              error: str | None = None) -> str:
    """Insert/finish an extraction_attempts row; returns attempt_id. Never raises into
    the caller's hot path (logs and returns '' on DB failure)."""
    from .provenance import record_extraction_attempt as _impl
    return _impl(attempt, status=status, units_emitted=units_emitted, invalid_units=invalid_units,
                 splits=splits, error=error)


def enqueue_unit(source: SourceId, source_ref: str, unit: SourceUnit, *, session_id: str | None,
                 run_id: str | None, read_root_ids: tuple[str, ...] = (),
                 project_snapshot: dict[str, Any] | None = None, attempt_id: str = "",
                 extraction_pass: ExtractionPass = "main") -> EnqueueResult:
    """v2 enqueue (§4.1, §9, §9.1, §9.2). Steps, all server-side:

    1. validate ``unit`` (contracts_v2); for links ``sanitize_source_url`` (presigned ->
       rejected ``credentialed_url``) BEFORE secret scan; any credential URL in
       summary/body -> rejected ``credentialed_url``;
    2. resolve evidence; ``extraction_pass`` in TRUSTED_PASS_ROOT_KINDS requires every
       root to be of that kind and owned by ``session_id`` / ``run_id`` (else rejected
       ``untrusted_pass_root``);
    3. ``source == 'S1'``: evidence must be the session's LATEST user_message root
       (else rejected ``s1_stale_evidence``) and at most S1_MAX_UNITS_PER_MESSAGE units
       per that root (else rejected ``s1_unit_cap``); turns without a user message cannot
       use S1;
    4. idem_key = sha256(source, source_ref, extractor slot, semantic claim hash)
       (enqueue idempotency, may include source); dedup_key = sha256(canonical root set,
       unit kind, claim hash) (cross-source, exact text only; card 3 does the rest);
    5. insert ON CONFLICT(idem_key) DO NOTHING, wake the lane. One 1.5 s wall clock.

    Unresolvable evidence -> rejected ``missing_evidence``, counted on the attempt."""
    from .provenance import enqueue_unit as _impl
    return _impl(source, source_ref, unit, session_id=session_id, run_id=run_id,
                 read_root_ids=read_root_ids, project_snapshot=project_snapshot, attempt_id=attempt_id,
                 extraction_pass=extraction_pass)


def ingest_link_status(url_root_id: str, status: str, *, http_status: int | None = None) -> None:
    """Scheduled lane, only when the user enabled link checks (off by default). Records a
    HEAD result (no file change): 404/410/NXDOMAIN -> 'dead'; A2-blocked, 401/403/405,
    timeout -> 'unreachable'."""
    from .provenance import ingest_link_status as _impl
    _impl(url_root_id, status, http_status=http_status)


def requeue_parked(reason: str, key: str) -> int:
    """Re-queue units parked on ``reason`` once the awaited thing exists.

    ``unknown_domain`` uses the domain slug. ``missing_section`` uses a canonical
    JSON object with ``relative_path`` and ``section``. Parked units consume no retry.
    """
    from .pipeline_v2 import requeue_parked as _impl
    return _impl(reason, key)


def requeue_units(reason: str) -> int:
    """Requeue units poisoned or deferred with exactly ``reason`` after its cause is fixed
    (attempts reset, candidate pending again, poison events resolved)."""
    from .pipeline_v2 import requeue_units as _impl
    return _impl(reason)
