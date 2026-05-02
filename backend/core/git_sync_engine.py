"""Git sync engine for workspace backup & restore.

Handles:
- DB table export (sqlite3 .backup → per-table .dump → gzip)
- DB table import (gunzip → sqlite3 pipe)
- Git operations (add, commit, push) with timeouts
- Config file snapshot/restore

All subprocess calls use shell=False and 30s timeout.
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


class GitSyncEngine:
    """Git operations + DB export/import for workspace backup."""

    def __init__(self, workspace_dir: Path):
        self.workspace_dir = workspace_dir

    # -- DB Export ----------------------------------------------------------

    async def export_db_tables(
        self,
        db_path: Path,
        export_dir: Path,
        tables: list[str],
    ) -> int:
        """Export DB tables to gzipped SQL dumps. Returns count exported.

        Uses sqlite3 .backup for WAL-safe snapshot, then .dump per table.
        """
        export_dir.mkdir(parents=True, exist_ok=True)

        # WAL-safe snapshot to temp file
        snap_path = export_dir / "_snapshot.db"
        try:
            src = sqlite3.connect(str(db_path))
            dst = sqlite3.connect(str(snap_path))
            src.backup(dst)
            src.close()
            dst.close()
        except Exception as e:
            logger.warning("DB snapshot failed: %s", e)
            return 0

        exported = 0
        snap = sqlite3.connect(str(snap_path))
        for table in tables:
            try:
                gz_path = export_dir / f"{table}.sql.gz"
                # Get table DDL + data
                rows = list(snap.iterdump())
                # Filter to only this table's statements
                table_sql = []
                for row in rows:
                    if table in row:
                        table_sql.append(row)

                if not table_sql:
                    # Fallback: dump specific table
                    cursor = snap.execute(
                        f"SELECT sql FROM sqlite_master WHERE name=?", (table,)
                    )
                    ddl = cursor.fetchone()
                    if ddl and ddl[0]:
                        table_sql.append(ddl[0] + ";")
                        cursor = snap.execute(f"SELECT * FROM {table}")
                        cols = [d[0] for d in cursor.description]
                        for data_row in cursor:
                            values = ", ".join(
                                f"'{str(v).replace(chr(39), chr(39)+chr(39))}'"
                                if v is not None else "NULL"
                                for v in data_row
                            )
                            table_sql.append(
                                f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({values});"
                            )

                if table_sql:
                    with gzip.open(gz_path, "wt") as f:
                        f.write("\n".join(table_sql) + "\n")
                    exported += 1
                    logger.debug("Exported %s (%d statements)", table, len(table_sql))
            except Exception as e:
                logger.warning("Failed to export table %s: %s", table, e)

        snap.close()
        snap_path.unlink(missing_ok=True)
        return exported

    # -- DB Import ----------------------------------------------------------

    async def import_db_tables(
        self,
        db_path: Path,
        export_dir: Path,
    ) -> int:
        """Import gzipped SQL dumps into a DB. Returns count imported."""
        imported = 0
        conn = sqlite3.connect(str(db_path))

        for gz_file in sorted(export_dir.glob("*.sql.gz")):
            try:
                with gzip.open(gz_file, "rt") as f:
                    sql = f.read()
                conn.executescript(sql)
                imported += 1
                logger.debug("Imported %s", gz_file.name)
            except Exception as e:
                logger.warning("Failed to import %s: %s", gz_file.name, e)

        conn.close()
        return imported

    # -- Git Operations -----------------------------------------------------

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

    def has_changes(self) -> bool:
        """Check if there are uncommitted changes."""
        try:
            result = _run_git(["status", "--porcelain"], self.workspace_dir)
            return bool(result.stdout.strip())
        except (subprocess.TimeoutExpired, Exception):
            return False
