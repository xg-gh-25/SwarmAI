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

A steer (docs/design/2026-10-24-chat-steer.md D2) adds one more phase: a message
written into the RUNNING turn's stdin that the CLI has not read yet. It is
``sent = 0`` with ``claimed_at`` set, ``pending_seq`` NULL and
``metadata.steer = "inflight"``: not history (``sent = 0``), not the queue (every
queue reader below excludes it, a drain never claims it, a cancel answers
``claimed``). It leaves that phase exactly once: ``confirm_steer`` (the CLI read
it: history at the echo time) or ``release_steer`` (it was never read: the queue,
with a new seq).

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

#: metadata.steer of a steer written to stdin and not yet read (module docstring)
STEER_INFLIGHT = "inflight"
#: confirm_steer outcome -> metadata.steered (the bubble's label, D7)
_STEERED_LABEL = {"folded": "during", "opened": "after"}
#: a row that is not a steer in flight (malformed metadata counts as "not a steer")
_NOT_STEER_SQL = ("json_extract(CASE WHEN json_valid(metadata) THEN metadata ELSE '{}' END, "
                  "'$.steer') IS NULL")
_IS_STEER_SQL = ("json_extract(CASE WHEN json_valid(metadata) THEN metadata ELSE '{}' END, "
                 "'$.steer') = 'inflight'")

# backend/schemas/message.py TerminalContext.buffer_tail max_length: a pending
# send's terminal output, and a combined drain's, stay within it.
_TERMINAL_MAX = 16000

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
    # The send's attached terminal output ({buffer_tail, cwd}), kept in the row's
    # metadata while it is pending (mark_pending_by_id) so the drain delivers it.
    terminal_context: dict | None = None


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


def _valid_terminal(tc: object) -> dict | None:
    """*tc* as {buffer_tail, cwd} when it carries terminal output, else None."""
    if not isinstance(tc, dict) or not isinstance(tc.get("buffer_tail"), str) or not tc["buffer_tail"]:
        return None
    return {"buffer_tail": tc["buffer_tail"][-_TERMINAL_MAX:],
            "cwd": tc.get("cwd") if isinstance(tc.get("cwd"), str) else ""}


def _terminal_of(metadata_json: str | None) -> dict | None:
    """The pending row's terminal_context from its metadata, or None."""
    try:
        meta = json.loads(metadata_json) if metadata_json else None
    except (json.JSONDecodeError, TypeError):
        return None
    return _valid_terminal(meta.get("terminal_context")) if isinstance(meta, dict) else None


def _row_to_pending(row: aiosqlite.Row | tuple) -> PendingMessage:
    """Build a PendingMessage from a (id, session_id, pending_seq, content,
    created_at[, metadata]) row. ``content`` is the stored JSON for the message
    payload; ``metadata`` (when selected) carries a pending send's terminal output."""
    msg_id, session_id, pending_seq, content_json, created_at = row[:5]
    terminal_context = _terminal_of(row[5]) if len(row) > 5 else None
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
            # Re-sent to the model: restore attachment bytes from their files.
            from core.message_attachments import (
                attachments_dir, rehydrate_attachments)
            content = rehydrate_attachments(parsed, attachments_dir(_get_db_path()))
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
        terminal_context=terminal_context,
    )


def _payload_json(user_message: str | None, content: list[dict] | None) -> str:
    """Serialize the message payload for the messages.content column.

    Text path stores ``[{"type":"text","text":...}]`` (matches the existing
    session_router persist shape), multimodal stores the block list verbatim.

    Encoding is DELEGATED to ``database.sqlite.dumps_json`` — the single authority
    for DB-stored JSON — not decided here. This module is the SECOND write door
    into ``messages.content`` (it inserts via a raw ``INSERT``, bypassing
    ``SQLiteTable._serialize_value``), so an independently-chosen policy here is
    how the CJK-escape defect recurs: a bare ``json.dumps`` escapes non-ASCII, and
    ``messages_fts`` then indexes escape tokens instead of Chinese (run_a7587134).
    """
    from database.sqlite import dumps_json
    from .session_utils import strip_tool_call_leak

    # Strip raw tool-call leaks before persisting (this is the SECOND user-content
    # write door — the raw INSERT below bypasses SQLiteMessagesTable.put, so the
    # assistant path's _clean_blocks_for_persist does NOT run here). Without this a
    # user who re-feeds a prior turn's on-screen leak remnant persists a raw
    # ‹invoke> body that resume then splices back into the prompt, re-inducing the
    # leak. strip is lossless on clean text + preserves fenced/inline discussion.
    if content is not None:
        cleaned = [
            {**b, "text": strip_tool_call_leak(b["text"])}
            if isinstance(b, dict) and isinstance(b.get("text"), str)
            else b
            for b in content
        ]
        return dumps_json(cleaned)
    return dumps_json([{"type": "text", "text": strip_tool_call_leak(user_message or "")}])


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
    # Attachment bytes go to files, the row keeps a reference; the drain
    # (_row_to_pending) turns it back into base64 before re-sending.
    from core.message_attachments import attachments_dir, externalize_attachments
    if content:
        try:
            content = externalize_attachments(content, attachments_dir(db_path))
        except OSError as exc:
            logger.warning("persist_pending: attachment externalize failed, "
                           "storing inline: %s", exc)
    payload = _payload_json(user_message, content)
    # Same single-authority encoder as the payload — a client_id is ASCII today,
    # but the policy must not fork per call site (that is how door 2 drifted).
    from core.message_search_text import message_content_seg
    from database.sqlite import dumps_json
    metadata_json = dumps_json({"client_id": client_id}) if client_id else "{}"

    async def _do() -> int:
        # SELECT MAX+INSERT held inside the per-session lock for monotonic
        # pending_seq (F6). Retry wraps the whole thing so a transient
        # "database is locked" never loses the message.
        async with _get_seq_lock(session_id):
            async with _pooled(db_path) as conn:
                await conn.execute("PRAGMA busy_timeout=5000")
                cursor = await conn.execute(
                    "SELECT COALESCE(MAX(pending_seq), 0) FROM messages "
                    "WHERE session_id = ?",
                    (session_id,),
                )
                max_seq = (await cursor.fetchone())[0]
                next_seq = max_seq + 1
                # content_seg is what messages_fts actually indexes (schema
                # v10) — a 2-char Chinese word is unfindable under any FTS5
                # built-in tokenizer, so the text is bigram-segmented in Python.
                # This raw INSERT bypasses SQLiteMessagesTable.put (the usual
                # chokepoint), so it MUST populate the column itself: the trigger
                # only copies it, and a NULL here would leave the message
                # invisible to search while looking perfectly stored (R27 — the
                # second writer is the one that gets forgotten).
                await conn.execute(
                    "INSERT INTO messages "
                    "(id, session_id, role, content, content_seg, model, metadata, "
                    " sent, pending_seq, claimed_at, expires_at, "
                    " created_at, updated_at) "
                    "VALUES (?, ?, 'user', ?, ?, NULL, ?, 0, ?, NULL, ?, ?, ?)",
                    (msg_id, session_id, payload, message_content_seg(payload),
                     metadata_json, next_seq, expires_at, now, now),
                )
                await conn.commit()
                return next_seq

    next_seq = await _with_retry(_do)
    return _row_to_pending((msg_id, session_id, next_seq, payload, now))


async def mark_pending_by_id(session_id: str, message_id: str, *,
                             terminal_context: dict | None = None) -> int | None:
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

    ``terminal_context`` (the send's attached terminal output) is kept in the row's
    metadata while it is pending — it is not part of the message text, and without
    it the drain would deliver the message without the output. mark_sent_batch
    removes it again once delivered.
    """
    db_path = _get_db_path()
    now = datetime.now().isoformat()
    from database.sqlite import dumps_json
    tc = _valid_terminal(terminal_context)

    async def _do() -> int | None:
        # MAX(pending_seq)+1 + UPDATE held under the per-session lock so the seq
        # stays monotonic against concurrent persist_pending on the same session.
        async with _get_seq_lock(session_id):
            async with _pooled(db_path) as conn:
                await conn.execute("PRAGMA busy_timeout=5000")
                # Already pending? keep its seq (don't reassign / don't clobber).
                cursor = await conn.execute(
                    "SELECT sent, pending_seq, metadata FROM messages "
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
                metadata = existing[2]
                if tc:
                    try:
                        meta = json.loads(metadata) if metadata else {}
                    except (json.JSONDecodeError, TypeError):
                        meta = {}
                    metadata = dumps_json({**(meta if isinstance(meta, dict) else {}),
                                           "terminal_context": tc})
                await conn.execute(
                    "UPDATE messages SET sent = 0, pending_seq = ?, claimed_at = NULL, "
                    "metadata = ?, updated_at = ? WHERE id = ? AND session_id = ?",
                    (next_seq, metadata, now, message_id, session_id),
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
                "SELECT id, session_id, pending_seq, content, created_at, metadata "
                f"FROM messages WHERE session_id = ? AND sent = 0 AND {_NOT_STEER_SQL} "
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
            async with _pooled(db_path) as conn:
                await conn.execute("PRAGMA busy_timeout=5000")
                cursor = await conn.execute(
                    "SELECT id, session_id, pending_seq, content, created_at, metadata "
                    "FROM messages WHERE session_id = ? AND sent = 0 "
                    "AND claimed_at IS NULL ORDER BY pending_seq ASC",
                    (session_id,),
                )
                rows = await cursor.fetchall()
                if not rows:
                    return []
                # created_at becomes the claim time: the record (history, the
                # turn-end reconcile, cold resume) orders by created_at, and a
                # message queued during a turn enters the conversation when it is
                # delivered, after every row that turn writes, not when it was
                # typed (equal times keep insertion order: rowid).
                await conn.execute(
                    "UPDATE messages SET claimed_at = ?, created_at = ? "
                    "WHERE session_id = ? AND sent = 0 AND claimed_at IS NULL",
                    (now, now, session_id),
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
                # Delivered: the terminal output it carried was for the turn only.
                rows = await (await conn.execute(
                    f"SELECT id, metadata FROM messages WHERE session_id = ? AND sent = 1 "
                    f"AND pending_seq IN ({placeholders}) AND metadata LIKE '%terminal_context%'",
                    (session_id, *pending_seqs),
                )).fetchall()
                for mid, meta_json in rows:
                    try:
                        meta = json.loads(meta_json) if meta_json else {}
                    except (json.JSONDecodeError, TypeError):
                        continue
                    if isinstance(meta, dict) and meta.pop("terminal_context", None) is not None:
                        from database.sqlite import dumps_json
                        await conn.execute("UPDATE messages SET metadata = ? WHERE id = ?",
                                           (dumps_json(meta), mid))
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


def combine_terminal(rows: list[PendingMessage]) -> dict | None:
    """The terminal output of a coalesced set: every row's buffer_tail in arrival
    order (a row from another folder headed ``# cwd: …``), the newest kept within
    the schema limit; cwd is the first row's. None when no row carries any."""
    parts = [r.terminal_context for r in rows if r.terminal_context]
    if not parts:
        return None
    cwd = parts[0].get("cwd") or ""
    text = "\n\n".join(
        (f"# cwd: {p.get('cwd') or 'SwarmWS'}\n" if i and (p.get("cwd") or "") != (parts[i - 1].get("cwd") or "") else "")
        + p["buffer_tail"]
        for i, p in enumerate(parts)
    )
    if len(text) > _TERMINAL_MAX:
        text = text[-_TERMINAL_MAX:]
    return {"buffer_tail": text, "cwd": cwd}


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


async def count_pending(session_id: str) -> int:
    """Number of sent=0 rows for the session (feeds the read API's
    ``pending_count``).

    ASYNC + pooled (run_7e8a2030): was a SYNCHRONOUS ``sqlite3.connect()`` called
    un-offloaded inside the async ``/sessions/streaming-state`` endpoint (polled
    ~every 15s, once PER live session) — it blocked the daemon's single event loop
    for the connect+COUNT, a SECOND independent cause of the >3s /health stall
    (distinct from the aiosqlite-thread-starvation the pool fixed). If the DB was
    briefly locked (WAL checkpoint / git-sync snapshot) the sync connect could stall
    the loop and /health with it → the exact watchdog false-offline flap. Now a
    pooled async read borrow: no event-loop block, no per-call thread."""
    db_path = _get_db_path()
    async with _pooled(db_path, readonly=True) as conn:
        cursor = await conn.execute(
            f"SELECT COUNT(*) FROM messages WHERE session_id = ? AND sent = 0 AND {_NOT_STEER_SQL}",
            (session_id,),
        )
        row = await cursor.fetchone()
        return row[0]


#: characters of a queued message shown in its row of the queue list
_PREVIEW_CHARS = 200
#: queued messages listed (a queue is a handful; the cap bounds the read)
_LIST_MAX = 50


#: The row's preview, computed by SQLite (on the connection's thread, never the
#: event loop, and without handing the whole content to Python): the text of a
#: string content, or of its text blocks; "" for anything else or invalid JSON.
#: Bound parameters: the preview length, twice.
_PREVIEW_SQL = (
    "CASE WHEN content IS NULL OR NOT json_valid(content) THEN '' "
    "WHEN json_type(content) = 'text' THEN substr(json_extract(content, '$'), 1, ?) "
    "WHEN json_type(content) = 'array' THEN substr(COALESCE((SELECT group_concat("
    "json_extract(j.value, '$.text'), ' ') FROM json_each(content) AS j "
    "WHERE json_extract(j.value, '$.type') = 'text'), ''), 1, ?) "
    "ELSE '' END AS preview"
)


async def list_pending(session_id: str) -> list[dict]:
    """The session's queued (sent=0) messages, FIFO, as the queue list shows them:
    ``client_id``, ``id``, ``pending_seq``, ``preview``, ``created_at``, ``claimed``
    (a drain has taken it: it is being delivered and can no longer be cancelled).
    One pooled read; ``pending_count`` is its length (docs/design/chat-turn-liveness.md S3)."""
    db_path = _get_db_path()
    async with _pooled(db_path, readonly=True) as conn:
        cursor = await conn.execute(
            f"SELECT id, pending_seq, {_PREVIEW_SQL}, created_at, metadata, claimed_at "
            f"FROM messages WHERE session_id = ? AND sent = 0 AND {_NOT_STEER_SQL} "
            "ORDER BY pending_seq ASC LIMIT ?",
            (_PREVIEW_CHARS, _PREVIEW_CHARS, session_id, _LIST_MAX),
        )
        rows = await cursor.fetchall()
    out = []
    for msg_id, seq, preview, created_at, metadata_json, claimed_at in rows:
        try:
            meta = json.loads(metadata_json) if metadata_json else {}
        except (json.JSONDecodeError, TypeError):
            meta = {}
        out.append({
            "id": msg_id, "client_id": (meta or {}).get("client_id"), "pending_seq": seq,
            "preview": preview or "", "created_at": created_at,
            "claimed": claimed_at is not None,
        })
    return out


async def list_unclaimed(session_id: str) -> list[dict]:
    """The queued messages no drain has taken yet (the ones that can be cancelled)."""
    return [p for p in await list_pending(session_id) if not p["claimed"]]


async def cancel_unclaimed(session_id: str, client_id: str) -> str:
    """Cancel one queued message by its client_id: ``cancelled`` (its row is deleted
    and its send-ledger entry released, so the same client_id may be sent again),
    ``claimed`` (a drain already took it — it is being delivered), or ``missing``.

    One transaction (docs/design/chat-turn-liveness.md S1 v4 RC-D): the row's
    delete and the ledger's release commit together, so a retry of the same
    client_id never sees a receipt naming a deleted row. Under the per-session seq
    lock, so it can never race ``claim_pending_batch``: the delete only takes a row
    that is still unsent AND unclaimed."""
    db_path = _get_db_path()

    async def _do() -> str:
        async with _get_seq_lock(session_id):
            async with _pooled(db_path) as conn:
                await conn.execute("PRAGMA busy_timeout=5000")
                cursor = await conn.execute(
                    "SELECT id, claimed_at FROM messages WHERE session_id = ? AND sent = 0 "
                    "AND json_extract(metadata, '$.client_id') = ?",
                    (session_id, client_id),
                )
                row = await cursor.fetchone()
                if row is None:
                    return "missing"
                if row[1] is not None:
                    return "claimed"
                await conn.execute(
                    "DELETE FROM messages WHERE id = ? AND sent = 0 AND claimed_at IS NULL",
                    (row[0],),
                )
                # the ledger's release (core/send_requests.release), same transaction
                await conn.execute(
                    "UPDATE send_requests SET status = 'failed', user_message_id = NULL, "
                    "updated_at = ? WHERE client_id = ?",
                    (datetime.now().isoformat(), client_id),
                )
                await conn.commit()
                return "cancelled"

    return await _with_retry(_do)


async def reopen_dangling_claims(session_id: str | None = None) -> int:
    """Startup crash-window reconciler (F4/F10): reopen rows left in the
    ``claimed`` phase (claimed_at set, sent=0) by a crash back to ``pending``
    (claimed_at=NULL). Returns the number of rows reopened.

    A steer still in flight (the daemon died before its echo) goes back to the
    queue FIRST, with a new ``pending_seq`` (design chat-steer D2, E7: the CLI
    never ran it, so queueing it cannot send it twice). Clearing its claim the
    way the other rows are cleared would leave a queued row with
    ``pending_seq = NULL``: a drain would claim it and ``mark_sent_batch``
    (``pending_seq IN``) would never flip it.

    Runs once at daemon startup before serving. Idempotent. Does NOT touch
    already-sent rows (sent=1) — those were delivered.

    When ``session_id`` is None, reopens dangling claims across all sessions
    (the startup case). Pass a session_id to scope to one session (tests).
    """
    db_path = _get_db_path()
    scope, args = ("", ()) if session_id is None else (" AND session_id = ?", (session_id,))
    async with _pooled(db_path) as conn:
        await conn.execute("PRAGMA busy_timeout=5000")
        cursor = await conn.execute(
            f"SELECT id, session_id FROM messages WHERE sent = 0 AND {_IS_STEER_SQL}{scope} "
            "ORDER BY session_id, created_at, rowid", args)
        steers = await cursor.fetchall()
        now = datetime.now().isoformat()
        for msg_id, sid in steers:
            cursor = await conn.execute(
                "SELECT COALESCE(MAX(pending_seq), 0) FROM messages WHERE session_id = ?", (sid,))
            next_seq = (await cursor.fetchone())[0] + 1
            await conn.execute(
                "UPDATE messages SET claimed_at = NULL, pending_seq = ?, "
                "metadata = json_remove(metadata, '$.steer'), updated_at = ? WHERE id = ?",
                (next_seq, now, msg_id))
        cursor = await conn.execute(
            f"UPDATE messages SET claimed_at = NULL "
            f"WHERE sent = 0 AND claimed_at IS NOT NULL AND {_NOT_STEER_SQL}{scope}", args)
        reopened = cursor.rowcount + len(steers)
        await conn.commit()
    if reopened:
        logger.info(
            "session_pending.reopen_dangling_claims: reopened %d dangling "
            "claimed row(s) to pending (%d steer)", reopened, len(steers),
        )
    return reopened


# ---------------------------------------------------------------------------
# Steer rows (design docs/design/2026-10-24-chat-steer.md D2)
# ---------------------------------------------------------------------------

async def _report_row(row_id: str) -> None:
    """Hand a committed change of one row to the transcript feed (other tabs see it
    now, not at their next resync). The raw UPDATEs here only bump the row's rev
    through the trigger; the feed is pushed by the messages table."""
    try:
        from database import db
        await db.messages._report(row_id)
    except Exception:  # noqa: BLE001 — a display aid: the row itself is committed
        logger.warning("session_pending: transcript feed push failed row=%s", row_id, exc_info=True)


async def mark_steer_inflight(session_id: str, message_id: str, *,
                              terminal_context: dict | None = None) -> bool:
    """Move the send's just-written user row (sent=1, persist-before-slot) into the
    steer-in-flight phase, BEFORE the steer is written to stdin. False when the row
    is not a delivered user row of this session (missing, queued, already a steer),
    or while the session has a queued message not yet taken by a drain: a later
    message never overtakes an earlier one (frontend design 2026-10-25 B3). A
    claimed one is already the running turn's input and placed before it. Checked in the same statement,
    under the seq lock every queue writer holds, so no queue write falls between
    the check and the claim. The caller then queues it.

    ``terminal_context`` (the send's attached terminal output) is kept in the row's
    metadata like a queued message's (``mark_pending_by_id``): a steer that goes
    back to the queue is delivered with it. ``confirm_steer`` removes it."""
    db_path = _get_db_path()
    now = datetime.now().isoformat()
    from database.sqlite import dumps_json
    tc = _valid_terminal(terminal_context)

    async def _do() -> bool:
        async with _get_seq_lock(session_id):
            async with _pooled(db_path) as conn:
                await conn.execute("PRAGMA busy_timeout=5000")
                meta_sql = ("json_set(CASE WHEN json_valid(metadata) THEN metadata ELSE '{}' END, "
                            "'$.steer', ?" + (", '$.terminal_context', json(?))" if tc else ")"))
                args = (now, STEER_INFLIGHT, *((dumps_json(tc),) if tc else ()), now,
                        message_id, session_id)
                cursor = await conn.execute(
                    f"UPDATE messages SET sent = 0, claimed_at = ?, pending_seq = NULL, "
                    f"metadata = {meta_sql}, updated_at = ? "
                    f"WHERE id = ? AND session_id = ? AND role = 'user' "
                    f"AND (sent IS NULL OR sent = 1) AND {_NOT_STEER_SQL} "
                    f"AND NOT EXISTS (SELECT 1 FROM messages q WHERE q.session_id = ? "
                    f"AND q.sent = 0 AND q.claimed_at IS NULL "
                    f"AND {_NOT_STEER_SQL.replace('metadata', 'q.metadata')})",
                    (*args, session_id))
                await conn.commit()
                return cursor.rowcount == 1

    return await _with_retry(_do)


async def confirm_steer(session_id: str, message_id: str, echo_at: str, outcome: str) -> bool:
    """The CLI read the steer (its echo): the row becomes history (``sent = 1``) at
    ``echo_at`` or the time the turn's writer already gave it, whichever is later
    (``restamp_steer``), labelled ``metadata.steered`` = ``during`` (``folded``) or
    ``after`` (``opened``), and is pushed to the live feed. False, and nothing
    written or pushed, when the row is not a steer in flight."""
    label = _STEERED_LABEL.get(outcome)
    if label is None:
        raise ValueError(f"confirm_steer: unknown outcome {outcome!r}")
    db_path = _get_db_path()
    now = datetime.now().isoformat()

    async def _do() -> bool:
        async with _get_seq_lock(session_id):
            async with _pooled(db_path) as conn:
                await conn.execute("PRAGMA busy_timeout=5000")
                cursor = await conn.execute(
                    "UPDATE messages SET sent = 1, claimed_at = NULL, created_at = MAX(created_at, ?), "
                    "metadata = json_set(json_remove(metadata, '$.steer', '$.terminal_context'), "
                    "'$.steered', ?), "
                    f"updated_at = ? WHERE id = ? AND session_id = ? AND sent = 0 AND {_IS_STEER_SQL}",
                    (echo_at, label, now, message_id, session_id))
                await conn.commit()
                return cursor.rowcount == 1

    done = await _with_retry(_do)
    if done:
        await _report_row(message_id)
    return done


async def restamp_steer(session_id: str, client_id: str, at: str) -> bool:
    """The turn's writer reached the steer (its ``user_steered`` event): the row's
    time becomes ``at`` unless it is already later (frontend design 2026-10-25 B1).
    The echo time comes from the reader, which can run ahead of the writer: rows of
    the segment before the steer are inserted after it, and a reload would put
    them after the steer. The writer inserts in order, so its time is after every
    row before the steer and before every row after it. Either this or
    ``confirm_steer`` may land first; both keep the later time. Only a steer row
    (in flight or read). False when none matched."""
    db_path = _get_db_path()

    async def _do() -> bool:
        async with _get_seq_lock(session_id):
            async with _pooled(db_path) as conn:
                await conn.execute("PRAGMA busy_timeout=5000")
                cursor = await conn.execute(
                    "UPDATE messages SET created_at = MAX(created_at, ?) "
                    "WHERE session_id = ? AND role = 'user' "
                    "AND json_extract(CASE WHEN json_valid(metadata) THEN metadata ELSE '{}' END, "
                    "'$.client_id') = ? "
                    f"AND ({_IS_STEER_SQL} OR json_extract(CASE WHEN json_valid(metadata) "
                    "THEN metadata ELSE '{}' END, '$.steered') IS NOT NULL)",
                    (at, session_id, client_id))
                await conn.commit()
                return cursor.rowcount >= 1

    return await _with_retry(_do)


async def release_steer(session_id: str, message_id: str) -> int | None:
    """The steer was never read (the process went, a Stop, a write failure): the row
    goes back to the queue with a fresh ``pending_seq``, last in line, and the drain
    sends it like any queued message (its ``created_at`` becomes the claim time).
    Returns the seq, or None when the row is not a steer in flight. The caller kicks
    the drain."""
    db_path = _get_db_path()
    now = datetime.now().isoformat()

    async def _do() -> int | None:
        async with _get_seq_lock(session_id):
            async with _pooled(db_path) as conn:
                await conn.execute("PRAGMA busy_timeout=5000")
                cursor = await conn.execute(
                    f"SELECT 1 FROM messages WHERE id = ? AND session_id = ? AND sent = 0 AND {_IS_STEER_SQL}",
                    (message_id, session_id))
                if await cursor.fetchone() is None:
                    return None
                cursor = await conn.execute(
                    "SELECT COALESCE(MAX(pending_seq), 0) FROM messages WHERE session_id = ?",
                    (session_id,))
                next_seq = (await cursor.fetchone())[0] + 1
                await conn.execute(
                    "UPDATE messages SET claimed_at = NULL, pending_seq = ?, "
                    "metadata = json_remove(metadata, '$.steer'), updated_at = ? WHERE id = ?",
                    (next_seq, now, message_id))
                await conn.commit()
                return next_seq

    return await _with_retry(_do)
