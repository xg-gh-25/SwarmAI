"""Git sync engine for workspace backup & restore.

Handles:
- DB table export (sqlite3 .backup → per-table iterdump-style → gzip)
- DB table import (gunzip → statement-level validation → sqlite3)
- Git operations (add, commit, push) with timeouts

All subprocess calls use shell=False and 30s timeout.
DB I/O runs in a thread via asyncio.to_thread to avoid blocking the event loop.
"""
import asyncio
import gzip
import logging
import os
import sqlite3
import subprocess
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

GIT_TIMEOUT = 30  # seconds


def _run_git(args: list[str], cwd: Path, timeout: int = GIT_TIMEOUT) -> subprocess.CompletedProcess:
    """Run a git command with timeout. Returns CompletedProcess."""
    return subprocess.run(
        ["git"] + args,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout,
    )


# ---------------------------------------------------------------------------
# SQL value formatting (handles all SQLite types correctly)
# ---------------------------------------------------------------------------

def _format_sql_value(v) -> str:
    """Format a Python value as a SQL literal, handling all SQLite types.

    - None → NULL
    - int/float → unquoted numeric literal
    - bytes → X'hex' blob literal
    - str → single-quoted with internal quotes doubled
    """
    if v is None:
        return "NULL"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return repr(v)  # repr preserves precision
    if isinstance(v, bytes):
        return "X'" + v.hex() + "'"
    # str: escape single quotes by doubling, escape newlines
    s = str(v).replace("'", "''")
    return f"'{s}'"


# ---------------------------------------------------------------------------
# Sync helpers (run in thread via asyncio.to_thread)
# ---------------------------------------------------------------------------

def _export_tables_sync(db_path: Path, export_dir: Path, tables: list[str]) -> int:
    """Export DB tables to gzipped SQL dumps (sync, blocking I/O).

    Uses sqlite3 .backup for WAL-safe snapshot, then per-table SELECT
    with proper type-aware value formatting. Streams rows directly to
    gzip writer to avoid holding entire dump in memory.
    """
    export_dir.mkdir(parents=True, exist_ok=True)

    # WAL-safe snapshot to TEMP dir (not inside git workspace — B3 fix)
    snap_fd, snap_path_str = tempfile.mkstemp(suffix=".db", prefix="swarm-backup-")
    os.close(snap_fd)
    snap_path = Path(snap_path_str)

    src = None
    dst = None
    try:
        src = sqlite3.connect(str(db_path))
        dst = sqlite3.connect(str(snap_path))
        src.backup(dst)
    except Exception as e:
        logger.warning("DB snapshot failed: %s", e)
        snap_path.unlink(missing_ok=True)
        return 0
    finally:
        if dst:
            dst.close()
        if src:
            src.close()

    exported = 0
    snap = None
    try:
        snap = sqlite3.connect(str(snap_path))
        # Get valid table names from schema
        valid_tables = {
            r[0] for r in snap.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }

        for table in tables:
            if table not in valid_tables:
                continue
            tmp_gz = None
            try:
                # Write to temp file, then atomic rename (B2 fix)
                gz_path = export_dir / f"{table}.sql.gz"
                tmp_gz = gz_path.with_suffix(".gz.tmp")

                # Get DDL
                ddl_row = snap.execute(
                    "SELECT sql FROM sqlite_master WHERE name=?", (table,)
                ).fetchone()
                if not ddl_row or not ddl_row[0]:
                    continue

                row_count = 0
                with gzip.open(tmp_gz, "wt") as f:
                    # DDL
                    f.write(ddl_row[0] + ";\n")

                    # Stream rows (D2 fix: no accumulate-in-list)
                    cursor = snap.execute(f'SELECT * FROM "{table}"')
                    cols = [d[0] for d in cursor.description]
                    col_list = ", ".join(f'"{c}"' for c in cols)
                    for row in cursor:
                        values = ", ".join(_format_sql_value(v) for v in row)
                        f.write(f'INSERT INTO "{table}" ({col_list}) VALUES ({values});\n')
                        row_count += 1

                # Atomic rename (B2 fix)
                os.replace(str(tmp_gz), str(gz_path))
                tmp_gz = None  # prevent cleanup
                exported += 1
                logger.debug("Exported %s (%d rows)", table, row_count)
            except Exception as e:
                logger.warning("Failed to export table %s: %s", table, e)
                if tmp_gz and tmp_gz.exists():
                    tmp_gz.unlink(missing_ok=True)
    finally:
        if snap:
            snap.close()
        snap_path.unlink(missing_ok=True)

    return exported


# ---------------------------------------------------------------------------
# Statement-level SQL validation for import
# ---------------------------------------------------------------------------

_SAFE_PREFIXES = ("CREATE TABLE", "CREATE INDEX", "INSERT INTO")


def _split_statements(sql: str) -> list[str]:
    """Split SQL text into individual complete statements.

    Uses sqlite3.complete_statement() for proper boundary detection,
    yielding each statement separately for individual validation.
    """
    stmts: list[str] = []
    buf = ""
    for line in sql.split("\n"):
        buf += line + "\n"
        if sqlite3.complete_statement(buf):
            stmt = buf.strip().rstrip(";").strip()
            if stmt:
                stmts.append(stmt)
            buf = ""
    # Reject trailing incomplete SQL (sign of tampering)
    if buf.strip():
        raise ValueError(f"Incomplete SQL at end of buffer: {buf[:80]!r}")
    return stmts


def _validate_statement(stmt: str, filename: str) -> bool:
    """Validate a single SQL statement is safe for import.

    Returns True if the statement is a simple CREATE TABLE/INDEX or
    INSERT INTO. Rejects subqueries (CREATE TABLE AS SELECT),
    compound statements, and anything not matching safe prefixes.
    """
    upper = stmt.upper()

    # Must start with a known safe prefix
    if not any(upper.startswith(prefix) for prefix in _SAFE_PREFIXES):
        logger.warning("Rejected unsafe SQL in %s: %.80s", filename, stmt)
        return False

    # Block CREATE TABLE AS SELECT (data exfiltration via subquery)
    if upper.startswith("CREATE TABLE") and " AS " in upper:
        logger.warning("Rejected CREATE TABLE AS in %s: %.80s", filename, stmt)
        return False

    # Block INSERT INTO ... SELECT (cross-table data exfiltration)
    if upper.startswith("INSERT INTO") and " SELECT " in upper:
        logger.warning("Rejected INSERT...SELECT in %s: %.80s", filename, stmt)
        return False

    return True


def _import_tables_sync(
    db_path: Path,
    export_dir: Path,
    allowed_tables: list[str],
) -> int:
    """Import gzipped SQL dumps into a DB (sync, blocking I/O).

    Security model:
    - Only processes files matching allowed table names
    - Splits SQL into individual statements (no executescript)
    - Validates each statement independently
    - Executes statements one at a time via conn.execute()
    """
    allowed_set = set(allowed_tables)
    imported = 0
    conn = None
    try:
        conn = sqlite3.connect(str(db_path))
        # ATOMICITY (run_037a02af): the whole import is ONE transaction. A
        # VALIDATION failure (malformed/rejected file) is non-fatal — that file
        # is SKIPPED (an optional/untrusted table shouldn't abort the restore).
        # But an EXECUTE failure is all-or-nothing: we rollback EVERY table and
        # re-raise, so a mid-import interruption never leaves a partially-imported
        # DB (some tables committed, others not). Commit happens ONCE at the end.
        for gz_file in sorted(export_dir.glob("*.sql.gz")):
            table_name = gz_file.stem.removesuffix(".sql")
            if table_name.startswith("_"):
                continue
            if table_name not in allowed_set:
                logger.warning("Skipping %s: not in allowed tables", gz_file.name)
                continue
            with gzip.open(gz_file, "rt") as f:
                sql = f.read()
            # Split into individual statements for per-statement validation
            try:
                stmts = _split_statements(sql)
            except ValueError as e:
                logger.warning("Rejected %s: %s", gz_file.name, e)
                continue  # validation skip — non-fatal
            # Validate EVERY statement individually — reject entire file on any failure
            if not all(_validate_statement(s, gz_file.name) for s in stmts):
                continue  # validation skip — non-fatal
            # Execute one at a time — never executescript with untrusted SQL.
            # An execute failure here propagates → rollback ALL tables (atomic).
            for stmt in stmts:
                conn.execute(stmt)
            imported += 1
            logger.debug("Imported %s (%d statements)", gz_file.name, len(stmts))
        conn.commit()  # single commit — all validated tables land together or not at all
    except Exception as e:
        logger.error("DB import failed, rolling back ALL tables: %s", e)
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
        raise  # all-or-nothing: caller cleans up, never a partial DB
    finally:
        if conn:
            conn.close()
    return imported


# ---------------------------------------------------------------------------
# Public async API
# ---------------------------------------------------------------------------

class GitSyncEngine:
    """Git operations + DB export/import for workspace backup."""

    def __init__(self, workspace_dir: Path):
        self.workspace_dir = workspace_dir

    # -- DB Export (async wrapper) ------------------------------------------

    async def export_db_tables(
        self,
        db_path: Path,
        export_dir: Path,
        tables: list[str],
    ) -> int:
        """Export DB tables to gzipped SQL dumps. Returns count exported."""
        return await asyncio.to_thread(
            _export_tables_sync, db_path, export_dir, tables
        )

    # -- DB Import (async wrapper) ------------------------------------------

    async def import_db_tables(
        self,
        db_path: Path,
        export_dir: Path,
        allowed_tables: list[str] | None = None,
    ) -> int:
        """Import gzipped SQL dumps into a DB. Returns count imported."""
        if allowed_tables is None:
            from core.backup_manager import L2_TABLES
            allowed_tables = L2_TABLES

        return await asyncio.to_thread(
            _import_tables_sync, db_path, export_dir, allowed_tables
        )

    # -- Git Clone ----------------------------------------------------------

    def git_clone(self, repo_url: str, target_dir: Path | None = None) -> bool:
        """Clone a repo. Returns True on success."""
        target = str(target_dir or self.workspace_dir)
        try:
            result = subprocess.run(
                ["git", "clone", repo_url, target],
                capture_output=True, text=True, timeout=120,
            )
            if result.returncode == 0:
                self.workspace_dir = Path(target)
            return result.returncode == 0
        except subprocess.TimeoutExpired:
            logger.warning("git clone timed out after 120s")
            return False

    # -- Git Operations (all sync, called via to_thread from backup()) ------

    def git_add_all(self) -> bool:
        """Stage all changes. Returns True on success."""
        try:
            result = _run_git(["add", "-A"], self.workspace_dir)
            return result.returncode == 0
        except subprocess.TimeoutExpired:
            logger.warning("git add timed out")
            return False

    def git_commit(self, message: str) -> str | None:
        """Commit staged changes. Returns commit SHA or None."""
        try:
            result = _run_git(["commit", "-m", message], self.workspace_dir)
            if result.returncode == 0:
                sha_result = _run_git(
                    ["rev-parse", "--short", "HEAD"], self.workspace_dir
                )
                return sha_result.stdout.strip()
            return None
        except subprocess.TimeoutExpired:
            logger.warning("git commit timed out")
            return None

    def git_push(self) -> bool:
        """Push to origin. Returns True on success."""
        try:
            result = _run_git(["push"], self.workspace_dir)
            return result.returncode == 0
        except subprocess.TimeoutExpired:
            logger.warning("git push timed out after %ds", GIT_TIMEOUT)
            return False

    def get_remote_url(self) -> str | None:
        """Get the origin remote URL, or None."""
        try:
            result = _run_git(["remote", "get-url", "origin"], self.workspace_dir)
            return result.stdout.strip() if result.returncode == 0 else None
        except (subprocess.TimeoutExpired, Exception):
            return None
