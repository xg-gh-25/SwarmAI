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
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# Upper bound on FTS rows fetched by search() (run_78bd708f). WITHOUT this, a broad
# multi-word query OR-joins its terms and matches tens of thousands of rows in the
# messages table (measured: 41K rows / 8.6s on a 284K-message DB), which are all
# fetchall()-ed and grouped in Python — breaching the recall subsystem's 8s
# disaster-timeout cap (RECALL DISASTER TIMEOUT). SQLite early-terminates the
# `ORDER BY fts.rank LIMIT N` sort with a top-N heap, so this returns the GLOBAL
# top-N-by-BM25-rank rows (verified: full[:N] == limited(N)). N=500 keeps the
# rank-primary top sessions identical to the unbounded result on real queries
# (verified stable across N=200..1000) while dropping the worst query 8.6s→<0.6s.
_SEARCH_ROW_LIMIT = 500


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
    # Count of matched rows CONSIDERED for ranking — capped at _SEARCH_ROW_LIMIT
    # (run_78bd708f), so for a very broad query this is the top-N-by-rank count, not
    # the true grand total. No production consumer relies on it as an exact total.
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
            # OR-join per-quoted-term so a multi-word query recalls sessions
            # matching ANY term, ranked by BM25 (fts.rank). Wrapping the whole
            # query as ONE phrase required all words verbatim+adjacent → near-0
            # recall for multi-word queries (R3, run_c730a9c0). Each term stays
            # individually quoted so an FTS5 keyword term (OR/NEAR/NOT) is a
            # phrase-literal, never an operator (injection-safe). Single term →
            # OR-of-one → identical to the old single-term behavior.
            _terms = [t for t in query.split() if t]
            if _terms:
                safe_query = " OR ".join('"' + t.replace('"', '""') + '"' for t in _terms)
            else:
                safe_query = '"' + query.replace('"', '""') + '"'

            # Step 1-2: FTS5 search joined with messages.
            # Root-1 SSOT Phase 2: the FTS insert trigger indexes ALL message
            # rows including unsent pending ones (sent=0). This raw JOIN bypasses
            # the SQLiteMessagesTable chokepoint, so it MUST filter sent != 0
            # itself — otherwise a queued-but-undelivered message phantom-injects
            # into recall context (P3). Treat NULL as sent (pre-v6 rows).
            # LIMIT is mandatory (run_78bd708f): it bounds the fetch to the global
            # top-N by fts.rank (BM25), so a broad query can't fetchall() 40K+ rows
            # and blow the recall disaster-timeout cap. It is quality-preserving ONLY
            # in combination with the rank-primary _session_relevance below — the
            # surfaced session is the one owning the best-BM25 message, which a
            # top-N-by-rank LIMIT is guaranteed to keep.
            rows = conn.execute("""
                SELECT m.rowid as msg_rowid, m.session_id, m.role, m.content,
                       m.created_at, fts.rank as fts_rank
                FROM messages_fts fts
                JOIN messages m ON m.rowid = fts.rowid
                WHERE messages_fts MATCH ?
                  AND (m.sent IS NULL OR m.sent != 0)
                ORDER BY fts.rank
                LIMIT ?
            """, (safe_query, _SEARCH_ROW_LIMIT)).fetchall()

            if not rows:
                return RecallResult(query=query, sessions=[], total_matches=0)

            # Step 3: Group by session_id. Carry each match's fts.rank so session
            # scoring can be rank-primary (the best/min rank per session decides
            # ordering, which makes the LIMIT above quality-preserving).
            session_matches: dict[str, list[dict]] = {}
            for row in rows:
                sid = row["session_id"]
                session_matches.setdefault(sid, []).append({
                    "rowid": row["msg_rowid"],
                    "role": row["role"],
                    "content": row["content"],
                    "created_at": row["created_at"],
                    "rank": row["fts_rank"],
                })

            total_matches = len(rows)

            # Step 4: Rank sessions RANK-PRIMARY (run_78bd708f).
            #
            # Primary key: the session's best (min) fts.rank — i.e. the strongest
            # BM25 match it owns. Tiebreak: match density (capped). This REPLACES the
            # old density*0.4 + recency*0.35 + richness*0.25 formula for two reasons:
            #
            #  1. Quality-preservation under LIMIT: the top session is the one owning
            #     the globally-best-ranked message, and `ORDER BY fts.rank LIMIT N`
            #     keeps exactly the top-N-by-rank rows — so that deciding message is
            #     never dropped. A density/recency-weighted score depends on ALL of a
            #     session's matched rows, which the LIMIT truncates → its top session
            #     shifts with N (measured). rank-primary top sessions are stable
            #     across N (verified on real queries).
            #  2. It fixes a latent bug: density-primary let a verbose session with
            #     many mediocre mentions outrank a session with one excellent hit
            #     (the best-BM25 message's session was not even in the old top-5).
            #
            # Recency is INTENTIONALLY dropped: it is incompatible with (1) — a
            # recency term reintroduces a full-row-set dependency that breaks LIMIT
            # preservation — and BM25 rank is already the relevance signal a topic
            # recall wants. Sessions are keyed by min-rank; near-ties in relevance
            # fall back to density, not age.

            def _session_relevance(item: tuple[str, list[dict]]) -> tuple[float, float]:
                sid, matches = item
                # fts.rank is negative (more negative = better BM25 match). Negate so
                # a LARGER key = MORE relevant, consistent with reverse=True sort.
                ranks = [m.get("rank", 0.0) for m in matches]
                best_rank = min(ranks) if ranks else 0.0
                relevance = -best_rank
                density = min(len(matches), 10) / 10.0  # tiebreak only
                return (relevance, density)

            top_sessions = sorted(
                session_matches.items(),
                key=_session_relevance,
                reverse=True,
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

        # Get all messages for this session ordered by created_at.
        # Root-1 SSOT Phase 2: exclude unsent pending rows (sent=0) from the
        # context window too — they must never surface in recall (P3). Treat
        # NULL as sent (pre-v6 rows).
        all_msgs = conn.execute("""
            SELECT rowid, role, content, created_at
            FROM messages
            WHERE session_id = ?
              AND (sent IS NULL OR sent != 0)
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

    def recall_about(self, topic: str, max_sessions: int = 3, budget_chars: int = 3000) -> str:
        """Search + format as readable text for system prompt injection.

        Returns empty string if no matches found.  Distributes the character
        budget across sessions, prioritizing user and assistant messages that
        contain the search terms.  Truncates individual messages at sentence
        boundaries when possible to preserve readability.
        """
        result = self.search(topic, max_sessions=max_sessions)
        if not result.sessions:
            return ""

        topic_lower = topic.lower()
        # Build a word-boundary regex for more precise topic matching.
        # "kubernetes" should not match "mykubernetescluster".
        try:
            _topic_pattern = re.compile(
                r"\b" + re.escape(topic_lower) + r"\b", re.IGNORECASE
            )
        except re.error:
            _topic_pattern = None  # Fallback to substring if regex fails

        per_session = max(budget_chars // max(len(result.sessions), 1), 400)

        lines: list[str] = [f'## Session Recall: "{topic}"', ""]

        def _topic_match(content: str) -> bool:
            """Check if content contains topic as a whole word."""
            if _topic_pattern is not None:
                return bool(_topic_pattern.search(content))
            return topic_lower in content.lower()

        for sess in result.sessions:
            lines.append(f"### Session {sess.session_id} ({sess.date}, {sess.match_count} matches)")
            chars_used = 0
            # Prefer messages that actually contain the topic terms (word boundary)
            ranked = sorted(
                sess.key_messages,
                key=lambda m: (_topic_match(m.get("content", "")), len(m.get("content", ""))),
                reverse=True,
            )
            for msg in ranked:
                if chars_used >= per_session:
                    break
                role = msg.get("role", "unknown").capitalize()
                content = msg.get("content", "")
                remaining = per_session - chars_used
                if len(content) > remaining:
                    content = self._truncate_at_sentence(content, remaining)
                lines.append(f"- {role}: {content}")
                chars_used += len(content)
            lines.append("")

        return "\n".join(lines)

    @staticmethod
    def _truncate_at_sentence(text: str, max_chars: int) -> str:
        """Truncate text at the nearest sentence boundary within max_chars."""
        if len(text) <= max_chars:
            return text
        # Find last sentence-ending punctuation before max_chars
        truncated = text[:max_chars]
        for end_marker in (". ", ".\n", "! ", "? "):
            pos = truncated.rfind(end_marker)
            if pos > max_chars // 3:  # Don't cut too early
                return truncated[: pos + 1]
        # No good sentence boundary — cut at last space
        space_pos = truncated.rfind(" ")
        if space_pos > max_chars // 3:
            return truncated[:space_pos] + "…"
        return truncated + "…"
