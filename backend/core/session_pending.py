"""Single owner of the server-side pending-message contract (Root-1 SSOT, Phase 1).

A user message that arrives while a session is NOT idle (STREAMING / WAITING_INPUT
/ COLD-spawning / DEAD-cleanup) must not be lost and must not be replayed as
phantom context on cold resume. This module persists such messages into the
existing ``messages`` table with ``sent=0`` and a per-session monotonic
``pending_seq``, then exposes the primitives a later drain worker (Phase 2) uses
to deliver them.

Design contract: ``.kiro/specs/session-state-source-of-truth/design.md``
(Data Models + Low-Level Design + F4/F6/F10 resolutions).

Three-phase row lifecycle (F4 — never lose a claimed-but-undelivered message):

    | phase   | sent | claimed_at | meaning                              |
    |---------|------|------------|--------------------------------------|
    | pending | 0    | NULL       | queued, awaiting drain               |
    | claimed | 0    | <ts>       | a drain took it, send() in flight    |
    | sent    | 1    | (cleared)  | confirmed delivered to the subprocess|

Concurrency (F6): ``pending_seq`` is assigned ``MAX(pending_seq)+1`` inside a
per-session :class:`asyncio.Lock` (keyed by ``session_id``), created independently
of the SessionUnit so it is valid on the persist-before-slot path (which can run
before the unit's own lock exists). Cross-session persists do not contend.

FIFO-coalesce (product decision): pending messages are persisted individually but
:func:`combine_pending` merges the whole claimed set into ONE turn (text joined
``\\n\\n`` latest-last; multimodal content-block lists concatenated). The drain
worker claims the whole set atomically and marks it sent together, so P5
(single in-flight turn) and P4 (exactly-once) hold trivially.

**Phase 1 scope:** these are pure persistence primitives. NOTHING in this module
is wired into the send/read path yet — the drain worker, the Option-B disconnect
change, and the truthful SESSION_BUSY all land in Phase 2. The only Phase-1
consumer is :func:`reopen_dangling_claims`, called once at daemon startup.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime
from uuid import uuid4

import aiosqlite

logger = logging.getLogger(__name__)

# TTL for pending rows — mirrors SQLiteMessagesTable.TTL_SECONDS (90 days) so an
# abandoned pending message (session never drained, never explicitly deleted) is
# eventually TTL-reaped instead of leaking forever. The TTL sweeper is taught to
# skip sent=0 rows (see SQLiteMessagesTable.cleanup_expired) so a slow-but-live
# drain is never deleted mid-flight — only rows whose 90-day window fully elapses
# are reaped, by which point the session is unambiguously dead.
_PENDING_TTL_SECONDS = 90 * 24 * 60 * 60

# Transient SQLite errors worth retrying (WAL checkpoint collision, concurrent
# writers). Mirrors SQLiteTable.put's retry contract — the whole point of this
# module is to NEVER lose a pending message, so a bare "database is locked" must
# not drop it.
_RETRY_DELAYS = (0.05, 0.2, 0.5)  # exponential-ish, total < 1s


def _is_transient_db_error(exc: Exception) -> bool:
    s = str(exc).lower()
    return "database is locked" in s or "busy" in s


async def _with_retry(coro_factory):
    """Run an async DB operation with retry on transient lock/busy errors.

    ``coro_factory`` is a zero-arg callable returning a fresh coroutine each
    attempt (a coroutine can only be awaited once). Non-transient errors and the
    final attempt propagate immediately.
    """
    last_error: Exception | None = None
    for attempt in range(len(_RETRY_DELAYS) + 1):
        try:
            return await coro_factory()
        except Exception as exc:  # noqa: BLE001 — re-raised below if not transient
            last_error = exc
            if not _is_transient_db_error(exc) or attempt >= len(_RETRY_DELAYS):
                raise
            await asyncio.sleep(_RETRY_DELAYS[attempt])
    raise last_error  # pragma: no cover - loop always returns or raises

# Per-session locks guarding pending_seq assignment (F6). Keyed by session_id.
# Module-level so it survives across calls; cleared only in tests.
_SEQ_LOCKS: dict[str, asyncio.Lock] = {}

# Test seam: when set, overrides the DB path resolution (tests point at a tmp DB).
_db_path_override: str | None = None


@dataclass(frozen=True)
class PendingMessage:
    """A user message persisted with sent=0, awaiting server-side drain.

    Invariants:
      - Belongs to exactly one session_id.
      - pending_seq is unique and monotonic within session_id (the coalesce key).
      - Excluded from cold-resume context injection (sent=0 filter, Phase 2).
      - Included in exactly one coalesced drain (the whole unsent set flips
        sent 0→1 atomically at drain time).
    """

    id: str
    session_id: str
    pending_seq: int
    user_message: str | None  # text path
    content: list[dict] | None  # multimodal blocks path
    created_at: str


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_db_path() -> str:
    """Resolve the SQLite path the messages table is backed by.

    Reuses the db singleton's messages-table path so this module writes to the
    SAME store (single store keeps durability + cold-resume filtering in one
    place — design Data Models). A test seam (``_db_path_override``) lets tests
    point at a tmp DB without touching the global singleton's wiring.
    """
    if _db_path_override is not None:
        return _db_path_override
    from database import db
    return str(db.messages.db_path)


def _pooled(db_path: str, readonly: bool = False):
    """Borrow a pooled connection for db_path (run_7e8a2030).

    These pending-message ops share the messages DB, so they must share the same
    connection pool as SQLiteTable — otherwise they'd keep spawning one aiosqlite
    worker-thread per call (the executor-starvation source this fix removes). Same
    ``async with _pooled(db_path, readonly=...) as conn`` shape as the raw
    ``aiosqlite.connect`` it replaces; PRAGMA busy_timeout is already set once at
    pool-connection creation, so the per-call ``PRAGMA busy_timeout`` lines are
    now redundant (left in place — harmless, and removing them is out of scope).
    readonly=True only for pure SELECT paths; any read-modify-write uses the
    serialized write connection (default).
    """
    from database.sqlite import _get_pool
    return _get_pool(db_path).borrow(readonly=readonly)


def _get_seq_lock(session_id: str) -> asyncio.Lock:
    """Return the per-session lock guarding pending_seq assignment + lifecycle
    transitions for one session.

    ``setdefault`` is atomic under asyncio's single-threaded scheduling (no
    ``await`` between read and write) AND self-documents the invariant: this
    function MUST remain await-free, else two coroutines for the same new
    session could hold divergent locks and break seq monotonicity.

    NOTE (bounded growth): one Lock accrues per session_id and is pruned only by
    :func:`forget_session` (called from session teardown in Phase 2) or tests.
    Until Phase 2 wires teardown, growth is ~1 small Lock per session — bounded
    by session churn, not unbounded data accumulation.
    """
    return _SEQ_LOCKS.setdefault(session_id, asyncio.Lock())


def forget_session(session_id: str) -> None:
    """Drop the per-session seq lock when a session is finalized (Phase 2
    teardown hook). Idempotent. Prevents _SEQ_LOCKS from growing unbounded over
    the daemon's lifetime."""
    _SEQ_LOCKS.pop(session_id, None)


def _row_to_pending(row: aiosqlite.Row | tuple) -> PendingMessage:
    """Build a PendingMessage from a (id, session_id, pending_seq, content,
    created_at) row. ``content`` is the stored JSON for the message payload."""
    msg_id, session_id, pending_seq, content_json, created_at = row
    user_message: str | None = None
    content: list[dict] | None = None
    try:
        parsed = json.loads(content_json) if content_json else None
    except (json.JSONDecodeError, TypeError):
        parsed = None
    # Stored payload is either a text string or a list of content blocks.
    if isinstance(parsed, str):
        user_message = parsed
    elif isinstance(parsed, list) and parsed:
        # A single [{"type":"text","text":...}] block is the text path's storage
        # form; surface it as text when it's EXACTLY that, else as content blocks.
        # Guard every element access: a legacy/seed row stores '[]' (empty list,
        # handled by the `and parsed` above) and a malformed row could store a
        # non-dict first element — neither may crash peek/claim.
        first = parsed[0]
        if (
            len(parsed) == 1
            and isinstance(first, dict)
            and first.get("type") == "text"
            and set(first) <= {"type", "text"}
        ):
            user_message = first.get("text")
        else:
            content = parsed
    # parsed is None (malformed/empty) or [] → both user_message and content stay
    # None; the row is a degenerate pending message (persist_pending rejects
    # fully-empty input, so this only arises from corrupt/legacy data).
    return PendingMessage(
        id=msg_id,
        session_id=session_id,
        pending_seq=pending_seq,
        user_message=user_message,
        content=content,
        created_at=created_at,
    )


def _payload_json(user_message: str | None, content: list[dict] | None) -> str:
    """Serialize the message payload for the messages.content column.

    Text path stores ``[{"type":"text","text":...}]`` (matches the existing
    session_router persist shape), multimodal stores the block list verbatim.
    """
    if content is not None:
        return json.dumps(content)
    return json.dumps([{"type": "text", "text": user_message or ""}])


# ---------------------------------------------------------------------------
# Public API — persistence primitives (Phase 1)
# ---------------------------------------------------------------------------

async def persist_pending(
    session_id: str,
    *,
    user_message: str | None,
    content: list[dict] | None,
    agent_id: str,
    client_id: str | None = None,
) -> PendingMessage:
    """Persist an arriving user message with sent=0 and a fresh monotonic
    pending_seq, under the per-session seq lock (F6).

    Exactly one of ``user_message`` / ``content`` should be non-empty. No
    subprocess interaction — pure persistence.

    ``client_id`` is the frontend's optimistic-message id. It MUST be threaded
    into ``metadata.client_id`` exactly as the live send path does
    (session_router persist-before-slot, ~line 1086) — otherwise when Phase 2
    drains this row the frontend cannot dedup its optimistic bubble against the
    persisted message and the user sees a duplicate. Stored in the existing
    ``metadata`` JSON column (no schema change).
    """
    if not user_message and not content:
        raise ValueError("persist_pending requires user_message or content")

    db_path = _get_db_path()
    now = datetime.now().isoformat()
    expires_at = int(time.time()) + _PENDING_TTL_SECONDS
    msg_id = str(uuid4())
    payload = _payload_json(user_message, content)
    metadata_json = json.dumps({"client_id": client_id}) if client_id else "{}"

    async def _do() -> int:
        # SELECT MAX+INSERT held inside the per-session lock for monotonic
        # pending_seq (F6). Retry wraps the whole thing so a transient
        # "database is locked" never loses the message.
        async with _get_seq_lock(session_id):
            async with _pooled(db_path, readonly=True) as conn:
                await conn.execute("PRAGMA busy_timeout=5000")
                cursor = await conn.execute(
                    "SELECT COALESCE(MAX(pending_seq), 0) FROM messages "
                    "WHERE session_id = ?",
                    (session_id,),
                )
                max_seq = (await cursor.fetchone())[0]
                next_seq = max_seq + 1
                await conn.execute(
                    "INSERT INTO messages "
                    "(id, session_id, role, content, model, metadata, "
                    " sent, pending_seq, claimed_at, expires_at, "
                    " created_at, updated_at) "
                    "VALUES (?, ?, 'user', ?, NULL, ?, 0, ?, NULL, ?, ?, ?)",
                    (msg_id, session_id, payload, metadata_json, next_seq,
                     expires_at, now, now),
                )
                await conn.commit()
                return next_seq

    next_seq = await _with_retry(_do)
    return _row_to_pending((msg_id, session_id, next_seq, payload, now))


async def mark_pending_by_id(session_id: str, message_id: str) -> int | None:
    """Convert an already-persisted live row (sent=1) into a pending row (sent=0)
    with a fresh monotonic pending_seq, preserving its id and metadata.

    This is the Phase-2 L2 entry: the send() path persists the arriving user
    message *before* slot acquisition (with its ``client_id`` in metadata). When
    the slot check yields SESSION_BUSY / QUEUE_TIMEOUT, that row must NOT be
    deleted — it becomes a pending message owned by the drain worker. Flipping the
    existing row (rather than delete + re-:func:`persist_pending`) preserves:
      - the row id (stable across the conversion),
      - ``metadata.client_id`` (frontend optimistic-bubble dedup, R1),
      - a single FTS-trigger insertion (no duplicate index entry).

    Returns the assigned ``pending_seq``, or ``None`` if no matching row exists
    (defensive — e.g. the pre-slot persist failed). Idempotent-ish: a row already
    sent=0 keeps its existing seq (we never reassign a pending row).
    """
    db_path = _get_db_path()
    now = datetime.now().isoformat()

    async def _do() -> int | None:
        # MAX(pending_seq)+1 + UPDATE held under the per-session lock so the seq
        # stays monotonic against concurrent persist_pending on the same session.
        async with _get_seq_lock(session_id):
            async with _pooled(db_path, readonly=True) as conn:
                await conn.execute("PRAGMA busy_timeout=5000")
                # Already pending? keep its seq (don't reassign / don't clobber).
                cursor = await conn.execute(
                    "SELECT sent, pending_seq FROM messages "
                    "WHERE id = ? AND session_id = ?",
                    (message_id, session_id),
                )
                existing = await cursor.fetchone()
                if existing is None:
                    return None
                if existing[0] == 0:
                    return existing[1]  # already pending — no-op, return its seq

                cursor = await conn.execute(
                    "SELECT COALESCE(MAX(pending_seq), 0) FROM messages "
                    "WHERE session_id = ?",
                    (session_id,),
                )
                next_seq = (await cursor.fetchone())[0] + 1
                await conn.execute(
                    "UPDATE messages SET sent = 0, pending_seq = ?, claimed_at = NULL, "
                    "updated_at = ? WHERE id = ? AND session_id = ?",
                    (next_seq, now, message_id, session_id),
                )
                await conn.commit()
                return next_seq

    return await _with_retry(_do)


async def peek_pending_batch(session_id: str) -> list[PendingMessage]:
    """Return ALL unsent rows for the session, ordered by pending_seq (FIFO).

    Read-only. Empty list if none. This is the coalesce input set.
    """
    db_path = _get_db_path()

    async def _do() -> list:
        async with _pooled(db_path, readonly=True) as conn:
            await conn.execute("PRAGMA busy_timeout=5000")
            cursor = await conn.execute(
                "SELECT id, session_id, pending_seq, content, created_at "
                "FROM messages WHERE session_id = ? AND sent = 0 "
                "ORDER BY pending_seq ASC",
                (session_id,),
            )
            return await cursor.fetchall()

    rows = await _with_retry(_do)
    return [_row_to_pending(r) for r in rows]


async def claim_pending_batch(session_id: str) -> list[PendingMessage]:
    """Atomically CLAIM the whole current unsent+unclaimed set (set claimed_at,
    keep sent=0) and return them FIFO-ordered. Does NOT flip sent (F4).

    Exactly-once (P4): the claim runs under the per-session seq lock and only
    takes rows that are unsent AND unclaimed at call time. A concurrent claim
    that loses the race sees an empty set; a row that arrives mid-claim is left
    for the next drain. The claimed→sent flip happens later via
    :func:`mark_sent_batch`, only after delivery is confirmed.
    """
    db_path = _get_db_path()
    now = datetime.now().isoformat()

    async def _do() -> list:
        async with _get_seq_lock(session_id):
            async with _pooled(db_path, readonly=True) as conn:
                await conn.execute("PRAGMA busy_timeout=5000")
                cursor = await conn.execute(
                    "SELECT id, session_id, pending_seq, content, created_at "
                    "FROM messages WHERE session_id = ? AND sent = 0 "
                    "AND claimed_at IS NULL ORDER BY pending_seq ASC",
                    (session_id,),
                )
                rows = await cursor.fetchall()
                if not rows:
                    return []
                await conn.execute(
                    "UPDATE messages SET claimed_at = ? "
                    "WHERE session_id = ? AND sent = 0 AND claimed_at IS NULL",
                    (now, session_id),
                )
                await conn.commit()
                return rows

    rows = await _with_retry(_do)
    return [_row_to_pending(r) for r in rows]


async def mark_sent_batch(session_id: str, pending_seqs: list[int]) -> None:
    """Flip a CLAIMED set claimed(sent=0)→sent(sent=1). Called ONLY after the
    coalesced turn is confirmed delivered to the subprocess (F4).

    Guarded by ``claimed_at IS NOT NULL`` so a stale/reopened seq (rolled back to
    pending by a crash + reopen) becomes a no-op rather than silently marking an
    undelivered message as sent. Held under the per-session lock so it serializes
    with persist/claim/rollback on the same session's lifecycle columns.
    """
    if not pending_seqs:
        return
    db_path = _get_db_path()
    placeholders = ",".join("?" for _ in pending_seqs)

    async def _do() -> None:
        async with _get_seq_lock(session_id):
            async with _pooled(db_path) as conn:
                await conn.execute("PRAGMA busy_timeout=5000")
                await conn.execute(
                    f"UPDATE messages SET sent = 1, claimed_at = NULL "
                    f"WHERE session_id = ? AND sent = 0 AND claimed_at IS NOT NULL "
                    f"AND pending_seq IN ({placeholders})",
                    (session_id, *pending_seqs),
                )
                await conn.commit()

    await _with_retry(_do)


async def rollback_claim_batch(session_id: str, pending_seqs: list[int]) -> None:
    """Revert a claimed set back to pending (claimed_at=NULL, sent stays 0).

    Called when the coalesced send() fails (QUEUE_TIMEOUT / budget / spawn) so
    no message is lost and the whole set re-coalesces on the next IDLE (F4).
    """
    if not pending_seqs:
        return
    db_path = _get_db_path()
    placeholders = ",".join("?" for _ in pending_seqs)

    async def _do() -> None:
        async with _get_seq_lock(session_id):
            async with _pooled(db_path) as conn:
                await conn.execute("PRAGMA busy_timeout=5000")
                await conn.execute(
                    f"UPDATE messages SET claimed_at = NULL "
                    f"WHERE session_id = ? AND sent = 0 "
                    f"AND pending_seq IN ({placeholders})",
                    (session_id, *pending_seqs),
                )
                await conn.commit()

    await _with_retry(_do)


def combine_pending(
    rows: list[PendingMessage],
) -> tuple[str | None, list[dict] | None]:
    """Merge claimed rows (already FIFO-ordered) into ONE turn payload.

    - Text-only rows: concatenated with ``\\n\\n``, latest last (a correction
      reads as the most salient instruction).
    - Any multimodal row present: ALL rows are flattened into a single
      content-block list in arrival order (text rows become text blocks), so the
      image/document blocks are preserved alongside the text.

    Returns ``(user_message, content)`` for ``send()``: exactly one is non-None
    (content wins when any row carries blocks).
    """
    if not rows:
        return None, None

    has_multimodal = any(r.content is not None for r in rows)
    if not has_multimodal:
        text = "\n\n".join(r.user_message or "" for r in rows)
        return text, None

    blocks: list[dict] = []
    for r in rows:
        if r.content is not None:
            blocks.extend(r.content)
        elif r.user_message:
            blocks.append({"type": "text", "text": r.user_message})
    return None, blocks


def count_pending(session_id: str) -> int:
    """Number of sent=0 rows for the session (feeds the read API's
    ``pending_count`` in Phase 2). Synchronous: a quick indexed COUNT."""
    import sqlite3

    db_path = _get_db_path()
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA busy_timeout=5000")
        cursor = conn.execute(
            "SELECT COUNT(*) FROM messages WHERE session_id = ? AND sent = 0",
            (session_id,),
        )
        return cursor.fetchone()[0]
    finally:
        conn.close()


async def reopen_dangling_claims(session_id: str | None = None) -> int:
    """Startup crash-window reconciler (F4/F10): reopen rows left in the
    ``claimed`` phase (claimed_at set, sent=0) by a crash back to ``pending``
    (claimed_at=NULL). Returns the number of rows reopened.

    Runs once at daemon startup before serving. Idempotent. Does NOT touch
    already-sent rows (sent=1) — those were delivered.

    When ``session_id`` is None, reopens dangling claims across all sessions
    (the startup case). Pass a session_id to scope to one session (tests).
    """
    db_path = _get_db_path()
    async with _pooled(db_path) as conn:
        await conn.execute("PRAGMA busy_timeout=5000")
        if session_id is None:
            cursor = await conn.execute(
                "UPDATE messages SET claimed_at = NULL "
                "WHERE sent = 0 AND claimed_at IS NOT NULL"
            )
        else:
            cursor = await conn.execute(
                "UPDATE messages SET claimed_at = NULL "
                "WHERE session_id = ? AND sent = 0 AND claimed_at IS NOT NULL",
                (session_id,),
            )
        reopened = cursor.rowcount
        await conn.commit()
    if reopened:
        logger.info(
            "session_pending.reopen_dangling_claims: reopened %d dangling "
            "claimed row(s) to pending", reopened,
        )
    return reopened
