"""SQLite storage for the Cognitive Distillation Flow.

- ``ingestion_db_path`` resolves the per-user store.
- ``connect_rw`` creates and validates the WAL database.
- ``connect_ro`` opens an existing database without creating it.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from config import get_app_data_dir

from .api import DB_FILENAME, PRAGMAS, SCHEMA_DDL, SCHEMA_VERSION
from .api_v2 import SCHEMA_V2_DDL, SCHEMA_V2_VERSION


_USAGE_SCHEMA_DDL = """
CREATE TABLE IF NOT EXISTS unit_usage (
  anchor TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('recall_injected','cited_as_evidence','read_by_agent')),
  session_id TEXT NOT NULL,
  at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_unit_usage_anchor_at ON unit_usage(anchor, at);
"""


# v6: Bedrock usage gets its own record (llm_usage.py), written at each call. The
# daily-stats llm_calls/tokens columns never held usage (one counted judged units, the
# other stayed 0), so they go. ingestion_eval_log keeps each unit's outcome for 180 days
# after the 30-day cleanup (eval_log.py, design §14.2).
_V6_DDL = """
CREATE TABLE IF NOT EXISTS ingestion_llm_usage (
  utc_day TEXT NOT NULL,
  source TEXT NOT NULL,
  calls INTEGER NOT NULL DEFAULT 0,
  input_tokens INTEGER NOT NULL DEFAULT 0,
  output_tokens INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (utc_day, source)
);
CREATE TABLE IF NOT EXISTS ingestion_eval_log (
  unit_id TEXT PRIMARY KEY,
  at TEXT NOT NULL,
  source TEXT NOT NULL,
  state TEXT NOT NULL,
  reason TEXT NOT NULL,
  record TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_eval_log_at ON ingestion_eval_log(at);
ALTER TABLE ingestion_daily_stats DROP COLUMN llm_calls;
ALTER TABLE ingestion_daily_stats DROP COLUMN tokens;
"""


def _attempts_rebuild() -> str:
    """v7 widens extraction_attempts.source to S7. SQLite cannot alter a CHECK, so the
    table is rebuilt from the one definition in api_v2 (same columns, same order)."""
    import re as _re
    create = _re.search(r"CREATE TABLE IF NOT EXISTS extraction_attempts \(.*?\n\);",
                        SCHEMA_V2_DDL, _re.S).group(0)
    return (create.replace("IF NOT EXISTS extraction_attempts (", "extraction_attempts_v7 (") + "\n"
            "INSERT INTO extraction_attempts_v7 SELECT * FROM extraction_attempts;\n"
            "DROP TABLE extraction_attempts;\n"
            "ALTER TABLE extraction_attempts_v7 RENAME TO extraction_attempts;\n"
            "CREATE INDEX IF NOT EXISTS ix_attempts_started ON extraction_attempts(started_at);\n")

# v7: S2 extraction jobs (design §12 r16.13). Its own step: v6 drops columns, so a
# database already at v6 cannot re-run _V6_DDL to pick up a new table.
_V7_DDL = """
CREATE TABLE IF NOT EXISTS ingestion_s2_jobs (
  session_id TEXT PRIMARY KEY,
  reason TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('queued','running','done','failed')),
  attempts INTEGER NOT NULL DEFAULT 0,
  again INTEGER NOT NULL DEFAULT 0,
  requested_at TEXT NOT NULL,
  next_attempt_at TEXT,
  started_at TEXT,
  finished_at TEXT,
  error TEXT,
  sdk_session_id TEXT,   -- for the review's resume; NULL -> S2 main pass instead
  reviewed_to TEXT,      -- watermark: the last message the review covered
  again_after TEXT       -- not-before for the extra run a request during a run asks for
);
CREATE INDEX IF NOT EXISTS ix_s2_jobs_state ON ingestion_s2_jobs(state, requested_at);
""" + _attempts_rebuild()


_V2_MIGRATION_DDL = """
ALTER TABLE ingestion_units RENAME TO ingestion_units_v1;
ALTER TABLE ingestion_inbox RENAME TO ingestion_inbox_v1;
ALTER TABLE ingestion_writes RENAME TO ingestion_writes_v1;
ALTER TABLE ingestion_relations RENAME TO ingestion_relations_v1;
DROP INDEX IF EXISTS ix_units_cand;
DROP INDEX IF EXISTS ix_units_state;
DROP INDEX IF EXISTS ix_inbox_state_lane;
DROP INDEX IF EXISTS ix_inbox_created;
DROP INDEX IF EXISTS ix_writes_step;

CREATE TABLE ingestion_inbox (
  id TEXT PRIMARY KEY,
  idem_key TEXT NOT NULL UNIQUE,
  source TEXT NOT NULL,
  source_ref TEXT NOT NULL,
  lane TEXT NOT NULL CHECK (lane IN ('immediate','session','run','scheduled','maintenance')),
  payload TEXT NOT NULL,
  payload_schema TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  envelope TEXT NOT NULL,
  project_snapshot TEXT NOT NULL,
  project_candidates TEXT NOT NULL DEFAULT '[]',
  state TEXT NOT NULL DEFAULT 'queued'
    CHECK (state IN ('queued','in_progress','partial_pending','done')),
  owner_epoch INTEGER,
  versions TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  unit_kind TEXT,
  source_v2 TEXT,
  extraction_pass TEXT,
  evidence_root_ids TEXT,
  project_hint TEXT,
  dedup_key TEXT
);
CREATE INDEX ix_inbox_state_lane ON ingestion_inbox(state, lane, created_at);
CREATE INDEX ix_inbox_created ON ingestion_inbox(created_at);
CREATE INDEX ix_inbox_dedup_key ON ingestion_inbox(dedup_key);

CREATE TABLE ingestion_units (
  unit_id TEXT PRIMARY KEY,
  candidate_id TEXT NOT NULL REFERENCES ingestion_inbox(id) ON DELETE CASCADE,
  claim_group TEXT NOT NULL,
  decision TEXT,
  destination TEXT,
  project TEXT,
  action TEXT,
  reason TEXT,
  state TEXT NOT NULL DEFAULT 'queued' CHECK (state IN
    ('queued','merged','judged','writing','written','reinforced','rejected','deferred','failed_poison','expired_unprocessed')),
  merged_into TEXT,
  attempts INTEGER NOT NULL DEFAULT 0,
  next_attempt_at TEXT,
  error_class TEXT,
  error_fingerprint TEXT,
  owner_epoch INTEGER,
  updated_at TEXT NOT NULL,
  defer_reason TEXT,
  rerouted_from TEXT
);
CREATE INDEX ix_units_cand ON ingestion_units(candidate_id);
CREATE INDEX ix_units_state ON ingestion_units(state, next_attempt_at);

CREATE TABLE ingestion_writes (
  op_id TEXT PRIMARY KEY,
  op_kind TEXT NOT NULL CHECK (op_kind IN ('ingestion_unit','user_edit','user_tree_move','user_redact')),
  unit_id TEXT,
  target_path TEXT NOT NULL,
  before_hash TEXT,
  intended_bytes BLOB,
  after_hash TEXT,
  archive_path TEXT,
  archive_hash TEXT,
  anchors_present TEXT NOT NULL DEFAULT '[]',
  anchors_absent TEXT NOT NULL DEFAULT '[]',
  relations TEXT NOT NULL DEFAULT '[]',
  baseline_patch TEXT,
  tree_manifest TEXT,
  step TEXT NOT NULL DEFAULT 'planned' CHECK (step IN
    ('planned','archive_applied','target_applied','relations_applied','baseline_applied','done','abandoned')),
  step_owner TEXT,
  owner_epoch INTEGER,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX ix_writes_step ON ingestion_writes(step);

CREATE TABLE ingestion_relations (
  subject_anchor TEXT NOT NULL,
  predicate TEXT NOT NULL CHECK (predicate IN ('supersedes','derived_from')),
  object_anchor TEXT NOT NULL,
  created TEXT NOT NULL,
  inactive INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (subject_anchor, predicate, object_anchor)
);

INSERT INTO ingestion_inbox(
  id, idem_key, source, source_ref, lane, payload, payload_schema, content_hash,
  envelope, project_snapshot, project_candidates, state, owner_epoch, versions, created_at
) SELECT id, idem_key, source, source_ref, lane, payload, payload_schema, content_hash,
         envelope, project_snapshot, project_candidates, state, owner_epoch, versions, created_at
  FROM ingestion_inbox_v1;
INSERT INTO ingestion_units(
  unit_id, candidate_id, claim_group, decision, destination, project, action, reason,
  state, merged_into, attempts, next_attempt_at, error_class, error_fingerprint,
  owner_epoch, updated_at
) SELECT unit_id, candidate_id, claim_group, decision, destination, project, action, reason,
         state, merged_into, attempts, next_attempt_at, error_class, error_fingerprint,
         owner_epoch, updated_at
  FROM ingestion_units_v1;
INSERT INTO ingestion_writes SELECT * FROM ingestion_writes_v1;
INSERT INTO ingestion_relations SELECT * FROM ingestion_relations_v1;

DROP TABLE ingestion_units_v1;
DROP TABLE ingestion_writes_v1;
DROP TABLE ingestion_relations_v1;
DROP TABLE ingestion_inbox_v1;
"""


def ingestion_db_path() -> Path:
    """Return the CDF database path, honoring ``SWARM_DATA_DIR``."""
    return get_app_data_dir() / DB_FILENAME


def _pragma_value(conn: sqlite3.Connection, name: str):
    row = conn.execute(f"PRAGMA {name}").fetchone()
    return row[0] if row else None


def _assert_pragmas(conn: sqlite3.Connection) -> None:
    expected = {"auto_vacuum": 2, "journal_mode": "wal", "synchronous": 1}
    for name, value in expected.items():
        actual = _pragma_value(conn, name)
        if isinstance(actual, str):
            actual = actual.lower()
        if actual != value:
            raise sqlite3.DatabaseError(f"PRAGMA {name} expected {value!r}, got {actual!r}")


def _create(path: Path, busy_timeout_ms: int) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    existed = path.exists()
    conn = sqlite3.connect(path, timeout=busy_timeout_ms / 1000, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute(f"PRAGMA busy_timeout={int(busy_timeout_ms)}")
        if not existed or path.stat().st_size == 0:
            for name, value in PRAGMAS:
                conn.execute(f"PRAGMA {name}={value}")
                _assert_one_pragma(conn, name)
            script = (
                f"BEGIN IMMEDIATE;\n{SCHEMA_DDL}\n{_V2_MIGRATION_DDL}\n{SCHEMA_V2_DDL}\n"
                f"{_USAGE_SCHEMA_DDL}\n{_V6_DDL}\n{_V7_DDL}\n"
                f"PRAGMA user_version={SCHEMA_V2_VERSION};\nCOMMIT;"
            )
            try:
                conn.executescript(script)
            except BaseException:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
        else:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            version = _pragma_value(conn, "user_version")
            if version == SCHEMA_VERSION:
                script = (
                    f"BEGIN IMMEDIATE;\n{_V2_MIGRATION_DDL}\n{SCHEMA_V2_DDL}\n"
                    f"{_USAGE_SCHEMA_DDL}\n{_V6_DDL}\n{_V7_DDL}\n"
                    f"PRAGMA user_version={SCHEMA_V2_VERSION};\nCOMMIT;"
                )
                conn.executescript(script)
            elif version in {3, 4, 5} and SCHEMA_V2_VERSION == 7:
                conn.executescript(
                    f"BEGIN IMMEDIATE;\n{SCHEMA_V2_DDL}\n{_USAGE_SCHEMA_DDL}\n{_V6_DDL}\n{_V7_DDL}\n"
                    f"PRAGMA user_version={SCHEMA_V2_VERSION};\nCOMMIT;"
                )
            elif version == 6 and SCHEMA_V2_VERSION == 7:
                conn.executescript(
                    f"BEGIN IMMEDIATE;\n{_V7_DDL}\n"
                    f"PRAGMA user_version={SCHEMA_V2_VERSION};\nCOMMIT;"
                )
        _assert_pragmas(conn)
        version = _pragma_value(conn, "user_version")
        if version != SCHEMA_V2_VERSION:
            raise sqlite3.DatabaseError(f"unsupported ingestion schema {version}")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn
    except BaseException:
        conn.close()
        if not existed:
            _remove_database_files(path)
        raise


def _assert_one_pragma(conn: sqlite3.Connection, name: str) -> None:
    expected = {"auto_vacuum": 2, "journal_mode": "wal", "synchronous": 1}[name]
    actual = _pragma_value(conn, name)
    if isinstance(actual, str):
        actual = actual.lower()
    if actual != expected:
        raise sqlite3.DatabaseError(f"PRAGMA {name} did not take effect")


def _remove_database_files(path: Path) -> None:
    for candidate in (path, Path(str(path) + "-wal"), Path(str(path) + "-shm")):
        candidate.unlink(missing_ok=True)


def _is_corruption(exc: BaseException) -> bool:
    from core.data_safety import is_corruption_error
    return is_corruption_error(exc)


def _isolate_corrupt(path: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    isolated = path.with_name(f"{path.name}.corrupt-{stamp}")
    path.rename(isolated)
    for suffix in ("-wal", "-shm"):
        sidecar = Path(str(path) + suffix)
        if sidecar.exists():
            sidecar.rename(Path(str(isolated) + suffix))
    return isolated


def connect_rw(*, busy_timeout_ms: int = 5000) -> sqlite3.Connection:
    """Open the writable CDF store, creating or recovering it when needed."""
    path = ingestion_db_path()
    try:
        return _create(path, busy_timeout_ms)
    except sqlite3.DatabaseError as exc:
        if not path.exists() or not _is_corruption(exc):
            raise
        isolated = _isolate_corrupt(path)
        conn = _create(path, busy_timeout_ms)
        now = datetime.now(timezone.utc).isoformat()
        detail = json.dumps({"isolated_path": str(isolated), "reason": str(exc)}, ensure_ascii=False)
        conn.execute(
            "INSERT INTO ingestion_events(at, kind, detail) VALUES (?, 'restored_orphan', ?)",
            (now, detail),
        )
        return conn


def connect_ro() -> sqlite3.Connection:
    """Open the existing CDF store read-only; never create a database file."""
    path = ingestion_db_path()
    if not path.is_file():
        raise FileNotFoundError(path)
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    if _pragma_value(conn, "query_only") != 1:
        conn.close()
        raise sqlite3.DatabaseError("query_only did not take effect")
    return conn
