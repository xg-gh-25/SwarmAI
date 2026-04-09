"""Session Recall — FTS5-based full-text search across session messages.

Enables searching past conversations by topic and returning contextual
message windows around matches. Uses SQLite FTS5 for efficient full-text
indexing with automatic sync via triggers.

The FTS5 virtual table and sync triggers are created by the DB migration
in ``database/sqlite.py``.  This module only *verifies* the table exists
at init time and performs read-only searches.  Connections are opened with
WAL mode and busy_timeout to match the main DB layer's settings.

Key public symbols:

- ``SessionRecall``  — Search + recall engine.
- ``SessionMatch``   — Per-session match result.
- ``RecallResult``   — Overall search result.
"""
from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class SessionMatch:
    session_id: str
    date: str           # from created_at
    match_count: int
    key_messages: list[dict] = field(default_factory=list)  # [{role, content, created_at}]


@dataclass
class RecallResult:
    query: str
    sessions: list[SessionMatch] = field(default_factory=list)
    total_matches: int = 0


class SessionRecall:
    """FTS5-based session search and recall engine."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._verify_fts()

    def _open_conn(self) -> sqlite3.Connection:
        """Open a connection with WAL mode and busy_timeout matching the main DB layer."""
        conn = sqlite3.connect(str(self._db_path))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _verify_fts(self) -> None:
        """Verify that the FTS5 virtual table exists (created by DB migration).

        Does NOT recreate the table or triggers — that is the responsibility
        of ``database/sqlite.py``.  Logs a warning if the table is missing
        so callers know search will return empty results.
        """
        conn = self._open_conn()
        try:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='messages_fts'"
            ).fetchone()
            if row is None:
                logger.warning(
                    "messages_fts table not found — session recall search "
                    "will return empty results until the DB migration runs"
                )
        except Exception as exc:
            logger.error("Failed to verify FTS5 table: %s", exc)
        finally:
            conn.close()

    def search(self, query: str, max_sessions: int = 3) -> RecallResult:
        """Search messages using FTS5 and return grouped results.

        1. FTS5 search for matching rowids
        2. Join with messages table for session_id, created_at, content, role
        3. Group by session_id, count matches per session
        4. Take top max_sessions by match count
        5. For each session: load +/-10 messages around each match
        """
        conn = self._open_conn()
        conn.row_factory = sqlite3.Row
        try:
            # Escape FTS5 special characters by quoting the query
            safe_query = '"' + query.replace('"', '""') + '"'

            # Step 1-2: FTS5 search joined with messages
            rows = conn.execute("""
                SELECT m.rowid as msg_rowid, m.session_id, m.role, m.content, m.created_at
                FROM messages_fts fts
                JOIN messages m ON m.rowid = fts.rowid
                WHERE messages_fts MATCH ?
                ORDER BY fts.rank
            """, (safe_query,)).fetchall()

            if not rows:
                return RecallResult(query=query, sessions=[], total_matches=0)

            # Step 3: Group by session_id
            session_matches: dict[str, list[dict]] = {}
            for row in rows:
                sid = row["session_id"]
                session_matches.setdefault(sid, []).append({
                    "rowid": row["msg_rowid"],
                    "role": row["role"],
                    "content": row["content"],
                    "created_at": row["created_at"],
                })

            total_matches = len(rows)

            # Step 4: Top sessions by match count
            top_sessions = sorted(
                session_matches.items(),
                key=lambda x: -len(x[1]),
            )[:max_sessions]

            # Step 5: Load context window around each match
            results: list[SessionMatch] = []
            for session_id, matches in top_sessions:
                # Get the date from the first match
                match_date = matches[0]["created_at"][:10] if matches[0]["created_at"] else ""

                # Collect rowids of matches for context window
                match_rowids = [m["rowid"] for m in matches]

                # Load context: ±10 messages around each match rowid
                key_messages = self._load_context_window(conn, session_id, match_rowids)

                results.append(SessionMatch(
                    session_id=session_id,
                    date=match_date,
                    match_count=len(matches),
                    key_messages=key_messages,
                ))

            return RecallResult(
                query=query,
                sessions=results,
                total_matches=total_matches,
            )
        except Exception as exc:
            logger.error("Session recall search failed: %s", exc)
            return RecallResult(query=query, sessions=[], total_matches=0)
        finally:
            conn.close()

    def _load_context_window(
        self,
        conn: sqlite3.Connection,
        session_id: str,
        match_rowids: list[int],
        window: int = 10,
    ) -> list[dict]:
        """Load ±window messages around each match rowid within a session."""
        if not match_rowids:
            return []

        # Get all messages for this session ordered by created_at
        all_msgs = conn.execute("""
            SELECT rowid, role, content, created_at
            FROM messages
            WHERE session_id = ?
            ORDER BY created_at
        """, (session_id,)).fetchall()

        # Build index map: rowid -> position
        rowid_to_pos = {row["rowid"]: i for i, row in enumerate(all_msgs)}

        # Collect positions that should be included
        include_positions: set[int] = set()
        for rid in match_rowids:
            pos = rowid_to_pos.get(rid)
            if pos is not None:
                start = max(0, pos - window)
                end = min(len(all_msgs), pos + window + 1)
                include_positions.update(range(start, end))

        # Build result in order
        result: list[dict] = []
        for i in sorted(include_positions):
            row = all_msgs[i]
            result.append({
                "role": row["role"],
                "content": row["content"],
                "created_at": row["created_at"],
            })
        return result

    def recall_about(self, topic: str, max_sessions: int = 3) -> str:
        """Search + format as readable text for system prompt injection.

        Returns empty string if no matches found.
        """
        result = self.search(topic, max_sessions=max_sessions)
        if not result.sessions:
            return ""

        lines: list[str] = [f'## Session Recall: "{topic}"', ""]

        for sess in result.sessions:
            lines.append(f"### Session {sess.session_id} ({sess.date}, {sess.match_count} matches)")
            for msg in sess.key_messages[:10]:  # Limit display
                role = msg.get("role", "unknown").capitalize()
                content = msg.get("content", "")[:120]
                lines.append(f"- {role}: \"{content}\"")
            lines.append("")

        return "\n".join(lines)
