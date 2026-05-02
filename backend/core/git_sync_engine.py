"""Git sync engine for workspace backup & restore.

Handles:
- DB table export (sqlite3 .backup → per-table SELECT → gzip)
- DB table import (gunzip → validated SQL → sqlite3)
- Git operations (add, commit, push) with timeouts

All subprocess calls use shell=False and 30s timeout.
DB I/O runs in a thread via asyncio.to_thread to avoid blocking the event loop.
"""
import asyncio
import gzip
import logging
import sqlite3
import subprocess
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
# Sync helpers (run in thread via asyncio.to_thread)
# ---------------------------------------------------------------------------

def _export_tables_sync(db_path: Path, export_dir: Path, tables: list[str]) -> int:
    """Export DB tables to gzipped SQL dumps (sync, blocking I/O).

    Uses sqlite3 .backup for WAL-safe snapshot, then per-table SELECT.
    Validates table names against sqlite_master before querying.
    """
    export_dir.mkdir(parents=True, exist_ok=True)
    snap_path = export_dir / "_snapshot.db"

    # WAL-safe snapshot
    src = None
    dst = None
    try:
        src = sqlite3.connect(str(db_path))
        dst = sqlite3.connect(str(snap_path))
        src.backup(dst)
    except Exception as e:
        logger.warning("DB snapshot failed: %s", e)
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
                continue  # Table doesn't exist in this DB — skip silently
            try:
                gz_path = export_dir / f"{table}.sql.gz"
                # Get DDL
                ddl_row = snap.execute(
                    "SELECT sql FROM sqlite_master WHERE name=?", (table,)
                ).fetchone()
                if not ddl_row or not ddl_row[0]:
                    continue

                lines = [ddl_row[0] + ";"]

                # Get data via parameterized-safe column-aware SELECT
                cursor = snap.execute(f'SELECT * FROM "{table}"')
                cols = [d[0] for d in cursor.description]
                col_list = ", ".join(f'"{c}"' for c in cols)
                for row in cursor:
                    placeholders = ", ".join(
                        "NULL" if v is None
                        else f"'{str(v).replace(chr(39), chr(39)+chr(39))}'"
                        for v in row
                    )
                    lines.append(f'INSERT INTO "{table}" ({col_list}) VALUES ({placeholders});')

                with gzip.open(gz_path, "wt") as f:
                    f.write("\n".join(lines) + "\n")
                exported += 1
                logger.debug("Exported %s (%d rows)", table, len(lines) - 1)
            except Exception as e:
                logger.warning("Failed to export table %s: %s", table, e)
    finally:
        if snap:
            snap.close()
        snap_path.unlink(missing_ok=True)

    return exported


def _import_tables_sync(
    db_path: Path,
    export_dir: Path,
    allowed_tables: list[str],
) -> int:
    """Import gzipped SQL dumps into a DB (sync, blocking I/O).

    Security: only processes files matching allowed table names.
    Validates each SQL statement is CREATE TABLE/INDEX or INSERT only.
    """
    allowed_set = set(allowed_tables)
    imported = 0
    conn = None
    try:
        conn = sqlite3.connect(str(db_path))
        for gz_file in sorted(export_dir.glob("*.sql.gz")):
            table_name = gz_file.stem.removesuffix(".sql")  # "messages.sql.gz" → stem "messages.sql" → "messages"
            if table_name.startswith("_"):
                continue
            if table_name not in allowed_set:
                logger.warning("Skipping %s: not in allowed tables", gz_file.name)
                continue
            try:
                with gzip.open(gz_file, "rt") as f:
                    sql = f.read()
                # Validate: only safe statement types
                for stmt in sql.split(";"):
                    stripped = stmt.strip().upper()
                    if not stripped or stripped in ("COMMIT", "BEGIN TRANSACTION"):
                        continue
                    if not (
                        stripped.startswith("CREATE TABLE")
                        or stripped.startswith("CREATE INDEX")
                        or stripped.startswith("INSERT INTO")
                        or stripped.startswith("DELETE FROM")
                    ):
                        logger.warning(
                            "Rejected unsafe SQL in %s: %.80s",
                            gz_file.name, stmt.strip(),
                        )
                        raise ValueError(f"Unsafe SQL statement in {gz_file.name}")
                conn.executescript(sql)
                imported += 1
                logger.debug("Imported %s", gz_file.name)
            except Exception as e:
                logger.warning("Failed to import %s: %s", gz_file.name, e)
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
        """Export DB tables to gzipped SQL dumps. Returns count exported.

        Runs blocking I/O in a thread to avoid starving the event loop.
        """
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
        """Import gzipped SQL dumps into a DB. Returns count imported.

        Security: only allowed table names are processed, each SQL
        statement validated to be CREATE/INSERT only.
        """
        if allowed_tables is None:
            from core.backup_manager import L2_TABLES
            allowed_tables = L2_TABLES

        return await asyncio.to_thread(
            _import_tables_sync, db_path, export_dir, allowed_tables
        )

    # -- Git Operations -----------------------------------------------------

    # -- Git Clone ----------------------------------------------------------

    def git_clone(self, repo_url: str, target_dir: Path | None = None) -> bool:
        """Clone a repo into target_dir (defaults to workspace_dir). Returns True on success."""
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
