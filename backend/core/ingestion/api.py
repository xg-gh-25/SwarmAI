"""CDF stage B shared contract: the one surface every stage-B workstream codes against.

Design: docs/design/2026-10-26-swarm-cognitive-distillation-flow.md (r15.3) §3-§13.
This file is the SINGLE place the cross-workstream interfaces live. Each name
below is owned (implemented) by exactly one workstream; the others import it.

- SCHEMA_DDL / SCHEMA_VERSION / PRAGMAS: ingestion.db layout (§4). Owner: ENGINE.
- ingestion_db_path(), connect_ro(), connect_rw(): DB access. Owner: ENGINE.
- EnqueueResult: the outcome of handing a unit to CDF (api_v2.enqueue_unit, §4.1, §5). Owner: ENGINE.
- wake(): nudge the owner worker (in-process event or loopback POST). Owner: ENGINE.
- Materializer protocol + get_materializer(): the ONLY filesystem write API for
  governed paths (§3.2). Owner: WRITER.
- is_governed(path): governed-path classifier used at runtime. Owner: WRITER.
- revision_of(), apply_user_edit(), apply_user_tree_move(), StaleRevision:
  governed_edit (§3.2, §13). Owner: WRITER.
- record_session_input(): provenance entry point in data.db (§9). Owner: SOURCES.
- Flow read API (flow_overview / list_items / item_detail / daily_stats) is
  owned by FLOW and reads the SCHEMA below only through connect_ro().

Each function below DELEGATES to the owning module (store.py / wake.py
/ materializer.py / governed_edit.py / core/session_inputs.py). Owners implement
those modules and never edit this file; signatures here are frozen. A change
needs all owners' agreement.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

SCHEMA_VERSION = 1
DB_FILENAME = "ingestion.db"

# Set OUTSIDE any transaction, read back and asserted, before SCHEMA_DDL (§4 建库).
PRAGMAS = (("auto_vacuum", "INCREMENTAL"), ("journal_mode", "WAL"), ("synchronous", "NORMAL"))

SCHEMA_DDL = """
CREATE TABLE IF NOT EXISTS ingestion_inbox (
  id TEXT PRIMARY KEY,
  idem_key TEXT NOT NULL UNIQUE,
  source TEXT NOT NULL,
  source_ref TEXT NOT NULL,
  lane TEXT NOT NULL CHECK (lane IN ('immediate','session','run','scheduled','maintenance')),
  payload TEXT NOT NULL,              -- JSON, one contracts_v2 source unit
  payload_schema TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  envelope TEXT NOT NULL,             -- JSON, the unit's resolved evidence (provenance)
  project_snapshot TEXT NOT NULL,     -- JSON
  project_candidates TEXT NOT NULL DEFAULT '[]',
  state TEXT NOT NULL DEFAULT 'queued'
    CHECK (state IN ('queued','in_progress','partial_pending','done')),
  owner_epoch INTEGER,
  versions TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL            -- UTC ISO-8601
);
CREATE INDEX IF NOT EXISTS ix_inbox_state_lane ON ingestion_inbox(state, lane, created_at);
CREATE INDEX IF NOT EXISTS ix_inbox_created ON ingestion_inbox(created_at);

CREATE TABLE IF NOT EXISTS ingestion_units (
  unit_id TEXT PRIMARY KEY,           -- candidate_id + '.' + stable slot
  candidate_id TEXT NOT NULL REFERENCES ingestion_inbox(id) ON DELETE CASCADE,
  claim_group TEXT NOT NULL,          -- JSON, normalize.ClaimGroup
  decision TEXT,                      -- JSON, contracts.UnitDecision (all card answers)
  destination TEXT,                   -- JSON, contracts.Destination
  project TEXT,
  action TEXT,
  reason TEXT,
  state TEXT NOT NULL DEFAULT 'queued' CHECK (state IN
    ('queued','merged','judged','writing','written','rejected','deferred','failed_poison','expired_unprocessed')),
  merged_into TEXT,
  attempts INTEGER NOT NULL DEFAULT 0,
  next_attempt_at TEXT,
  error_class TEXT,
  error_fingerprint TEXT,
  owner_epoch INTEGER,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_units_cand ON ingestion_units(candidate_id);
CREATE INDEX IF NOT EXISTS ix_units_state ON ingestion_units(state, next_attempt_at);

CREATE TABLE IF NOT EXISTS ingestion_writes (
  op_id TEXT PRIMARY KEY,
  op_kind TEXT NOT NULL CHECK (op_kind IN ('ingestion_unit','user_edit','user_tree_move','user_redact')),
  unit_id TEXT,                       -- NULL for user ops
  target_path TEXT NOT NULL,          -- workspace-relative
  before_hash TEXT,
  intended_bytes BLOB,
  after_hash TEXT,
  archive_path TEXT,
  archive_hash TEXT,
  anchors_present TEXT NOT NULL DEFAULT '[]',
  anchors_absent TEXT NOT NULL DEFAULT '[]',
  relations TEXT NOT NULL DEFAULT '[]',
  baseline_patch TEXT,
  tree_manifest TEXT,                 -- user_tree_move only
  step TEXT NOT NULL DEFAULT 'planned' CHECK (step IN
    ('planned','archive_applied','target_applied','relations_applied','baseline_applied','done','abandoned')),
  step_owner TEXT,
  owner_epoch INTEGER,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_writes_step ON ingestion_writes(step);

CREATE TABLE IF NOT EXISTS ingestion_relations (
  subject_anchor TEXT NOT NULL,
  predicate TEXT NOT NULL CHECK (predicate = 'supersedes'),
  object_anchor TEXT NOT NULL,
  created TEXT NOT NULL,
  inactive INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (subject_anchor, predicate, object_anchor)
);

CREATE TABLE IF NOT EXISTS ingestion_quota (
  unit_id TEXT PRIMARY KEY,
  store TEXT NOT NULL,
  project TEXT,
  utc_day TEXT NOT NULL,
  bytes INTEGER NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('reserved','committed','released')),
  expires_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ingestion_lanes (
  lane TEXT PRIMARY KEY,
  next_due_at TEXT,
  last_started_at TEXT,
  last_completed_at TEXT
);

CREATE TABLE IF NOT EXISTS ingestion_owner (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  epoch INTEGER NOT NULL,
  pid INTEGER NOT NULL,
  started_at TEXT NOT NULL
);

-- llm_calls/tokens below are the v1 layout; schema v6 drops them (store._V6_DDL).
CREATE TABLE IF NOT EXISTS ingestion_daily_stats (
  utc_day TEXT NOT NULL, source TEXT NOT NULL, lane TEXT NOT NULL, store TEXT NOT NULL,
  state TEXT NOT NULL, reason TEXT NOT NULL, rules_digest TEXT NOT NULL,
  candidates INTEGER NOT NULL DEFAULT 0, units INTEGER NOT NULL DEFAULT 0,
  bytes_written INTEGER NOT NULL DEFAULT 0, llm_calls INTEGER NOT NULL DEFAULT 0, tokens INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (utc_day, source, lane, store, state, reason, rules_digest)
);

CREATE TABLE IF NOT EXISTS ingestion_access_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  at TEXT NOT NULL, item_id TEXT NOT NULL, actor TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ingestion_events (   -- Need-You-worthy anomalies (§10)
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  at TEXT NOT NULL,
  kind TEXT NOT NULL,                 -- breaker|backlog|id_collision|writer_unknown|judge_down|user_race|poison|restored_orphan
  detail TEXT NOT NULL,               -- JSON
  resolved INTEGER NOT NULL DEFAULT 0
);
"""

Lane = Literal["immediate", "session", "run", "scheduled", "maintenance"]

@dataclass(frozen=True)
class EnqueueResult:
    """Outcome of one enqueue call."""

    status: Literal["queued", "duplicate", "id_collision", "busy", "rejected"]
    candidate_id: str | None
    detail: str = ""


def ingestion_db_path() -> Path:
    """<app data>/ingestion.db, from config.get_app_data_dir() (honours SWARM_DATA_DIR)."""
    from .store import ingestion_db_path as _impl
    return _impl()


def connect_ro() -> sqlite3.Connection:
    """Read-only connection (uri mode=ro + PRAGMA query_only=ON). Never creates the DB."""
    from .store import connect_ro as _impl
    return _impl()


def connect_rw(*, busy_timeout_ms: int = 5000) -> sqlite3.Connection:
    """Writable connection; creates/migrates the DB on first use (§4 建库)."""
    from .store import connect_rw as _impl
    return _impl(busy_timeout_ms=busy_timeout_ms)


def wake(lane: Lane | None = None) -> None:
    """Best-effort nudge; never raises. In-process asyncio.Event, else loopback POST
    /api/ingestion/wake with the 0600 credential (§6.1)."""
    from .wake import wake as _impl
    return _impl(lane)


class StaleRevision(Exception):
    """expected_revision did not match the file on disk (HTTP 409)."""


class Materializer(Protocol):
    """The ONLY filesystem write API for governed paths. Every call carries op_id."""

    def write_bytes(self, path: Path, data: bytes, *, op_id: str) -> str: ...  # returns sha256
    def archive(self, path: Path, *, op_id: str) -> tuple[Path, str]: ...       # (archive_path, sha256)
    def unlink(self, path: Path, *, op_id: str) -> None: ...
    def move(self, src: Path, dst: Path, *, op_id: str) -> None: ...
    def copy(self, src: Path, dst: Path, *, op_id: str) -> None: ...


def get_materializer() -> Materializer:
    from .materializer import get_materializer as _impl
    return _impl()


def is_governed(path: Path) -> bool:
    """True when the resolved path is a governed corpus file (live .context/{MEMORY,EVOLUTION,
    STEERING,USER,TOOLS}.md, Projects/*/2-understanding/{PROJECT,PRODUCT,TECH,IMPROVEMENT}.md
    via ddd_paths, Knowledge/{Notes,Library,Learned,Inbox}/**)."""
    from .materializer import is_governed as _impl
    return _impl(path)


def revision_of(path: Path) -> str:
    """sha256 of current bytes ('' when absent). The editor GET returns it."""
    from .governed_edit import revision_of as _impl
    return _impl(path)


def apply_user_edit(path: Path, new_bytes: bytes | None, *, expected_revision: str,
                    actor: str = "user", moved_from: Path | None = None) -> str:
    """governed_edit: journaled user write/create/delete (new_bytes=None deletes, archive-first).
    Raises StaleRevision. Revises baseline (§13). Returns new revision. *moved_from*: the
    governed file a move copies here (the guard compares with it)."""
    from .governed_edit import apply_user_edit as _impl
    return _impl(path, new_bytes, expected_revision=expected_revision, actor=actor,
                 moved_from=moved_from)


def tree_revision_of(root: Path) -> str:
    """Manifest digest of a project tree's governed files (expected_manifest for a move)."""
    from .governed_edit import tree_revision_of as _impl
    return _impl(root)


def apply_user_tree_move(src: Path, dst: Path | None, *, expected_manifest: str,
                         actor: str = "user") -> None:
    """Journaled project rename (dst) / delete (dst=None -> trash). Raises StaleRevision."""
    from .governed_edit import apply_user_tree_move as _impl
    return _impl(src, dst, expected_manifest=expected_manifest, actor=actor)


def record_session_input(session_id: str, kind: str, trust: Literal["user", "external", "agent", "measured"],
                         content: str, *, ref: str | None = None) -> str:
    """Append one immutable input event to data.db.session_inputs; returns event_id. Owner: SOURCES."""
    from core.session_inputs import record_session_input as _impl
    return _impl(session_id, kind, trust, content, ref=ref)
