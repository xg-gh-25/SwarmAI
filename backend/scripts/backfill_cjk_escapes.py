#!/usr/bin/env python3
"""Re-encode DB rows whose JSON columns hold ``\\uXXXX`` escapes as raw UTF-8.

BACKGROUND (run_a7587134)
-------------------------
``json.dumps`` defaults to ``ensure_ascii=True``, so every non-ASCII character in a
list/dict-shaped column was stored as a literal ``\\uXXXX`` escape. ``messages_fts``
is an **external-content** FTS5 index over ``messages.content``, so it indexed the
escape tokens and no Chinese query could match recorded history — ``MATCH '部署'``
returned 0 while ``MATCH 'u90e8'`` returned thousands.

The writer is fixed at the single ``database.sqlite.dumps_json`` authority, so NEW
rows are correct. This script converts the rows written before that fix. The two
halves are independent: escaped and raw forms ``json.loads`` to the identical
object, so a mixed corpus reads fine and this can run any time after the deploy.

WHY A SCRIPT AND NOT A SCHEMA MIGRATION
---------------------------------------
Migration v5 (``sqlite.py``) set the precedent of rewriting data autonomously at
startup — but it rewrote ``expires_at``, a DERIVED integer, inside a non-fatal
``except``. This rewrites ``content``: the irreplaceable payload itself. An
unattended, un-backed-up rewrite of a user store is exactly what STEERING #20
forbids after the data.db-wipe COE, and a swallowed exception mid-way would leave a
silently half-converted corpus. So the decision to write is HUMAN-OWNED.

SAFETY
------
- **Dry-run is the DEFAULT.** Nothing is written without ``--apply``
  (mirrors ``purge_garbage_runs.py``, the repo's convention for destructive scripts).
- **Verified snapshot backup before any write.** ``--apply`` takes a
  ``Connection.backup`` snapshot (NOT ``shutil.copy2`` — the DB is WAL with live
  writers, so file copies tear), PROVES it with ``PRAGMA integrity_check``, chmods it
  0600, and ABORTS if any of that fails. Nothing is deleted or overwritten in place
  beyond the UPDATE itself. ⚠️ The backup is a full second copy of all chat history —
  delete it once you have verified the result.
- **Writing needs an explicit token, not a boolean.** ``backfill()`` refuses unless
  ``confirm=WRITE_CONFIRMATION`` accompanies ``apply=True``, so no import, job, hook
  or future auto-repair can reach the write path by flipping a flag.
- **Mandatory FTS rebuild for trigger-less indexes.** ``transcript_chunks`` and
  ``knowledge_chunks`` have NO sync triggers (only ``messages`` does), so converting
  them without a rebuild would leave their index on the old escape tokens.
- **Per-row semantic verification.** A row is written only if the re-encoded text
  parses back to an object EQUAL to the original. A row that fails is SKIPPED and
  counted, never written — and never silently.
- **Chunked + resumable, NOT one transaction.** Writes commit every 500 rows so a
  live daemon is never lock-starved (one giant transaction would exceed its
  busy_timeout and LOSE incoming messages). A failure therefore leaves a PARTIALLY
  converted DB — which is harmless (both encodings parse identically) and fully
  resumable: re-running converts only what remains.
- **Idempotent.** A second run converts nothing.

ROW SELECTION IS SEMANTIC, NOT A PATTERN
----------------------------------------
The obvious predicate — ``content LIKE '%\\u4%' OR ... '%\\u9%'`` — covers only the
CJK plane and MISSED ~7,100 real rows carrying ``\\u3000`` (ideographic space),
``\\u2018`` (curly quotes) and emoji (Gate-1 finding). So selection is: parse the
row, re-encode with ``ensure_ascii=False``, and treat it as affected iff the result
differs AND contains non-ASCII. A cheap SQL prefilter narrows the scan; the
semantic test decides.

NO HARDCODED COUNTS
-------------------
The affected-row count drifts continuously while any unfixed writer is live (it
moved 26,775 -> 33,953 during the authoring run). Counts here are MEASURED and
reported; nothing in this script's logic compares against a frozen number (R30#4).

RUNBOOK (follow in order — a safety control the operator cannot follow is not a control)
---------------------------------------------------------------------------------------
0. **DEPLOY THE WRITER FIX FIRST.** The daemon runs a PyInstaller binary
   (``~/.swarm-ai/daemon/python-backend``); a source edit is INERT until it is
   rebuilt and restarted. Skip this and new rows keep getting escaped while you
   convert the old ones — the corpus never converges and you must re-run forever.
   Verify it is live: send one Chinese message, then
   ``sqlite3 ~/.swarm-ai/data.db "SELECT content FROM messages ORDER BY rowid DESC LIMIT 1"``
   must show real characters, not ``\\uXXXX``.
1. **Dry run** (safe, read-only, writes nothing)::

       cd backend
       .venv/bin/python scripts/backfill_cjk_escapes.py --audit-all

   Read ``would_convert``, ``per_table``, and ``skipped_by_reason``. Every skip is
   attributed; ``unlisted_escaped_columns`` must be ``{}`` (else a column is missing
   from TARGETS). Runtime: seconds to a couple of minutes — it is one indexed scan
   per target column plus a JSON parse per candidate row.
2. **Check free space.** ``--apply`` writes a FULL second copy of the database
   beside it. Confirm free space exceeds the size of ``~/.swarm-ai/data.db``.
3. **The daemon may keep running.** Writes commit in 500-row chunks with a
   compare-and-swap, so concurrent daemon writes are neither lock-starved nor
   reverted. (A single giant transaction WOULD starve it — that is why it chunks.)
4. **Convert**::

       .venv/bin/python scripts/backfill_cjk_escapes.py --apply

   Note the ``backup_path`` it prints. If it reports ``crashed``, read
   ``resume_hint``: re-running converts only the remainder (idempotent), then
   ``--apply --rebuild-fts`` refreshes the indexes.
5. **Verify success** — a Chinese term that was previously unfindable must now hit::

       sqlite3 ~/.swarm-ai/data.db \
         "SELECT count(*) FROM messages_fts WHERE messages_fts MATCH '\"部署\"'"

   ⚠️ This returns 0 even after a correct conversion if the FTS tokenizer is still
   the default ``unicode61``, which cannot segment CJK — a SEPARATE defect from this
   one. Encoding is a prerequisite for CJK search, not the whole fix.
6. **Delete the backup** once you have verified the result (it is a full copy of all
   chat history).
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_DB = Path.home() / ".swarm-ai" / "data.db"

# Columns known to be written through the shared serializer with JSON payloads.
# A table absent from the DB is skipped silently (a fresh/partial DB is normal).
#
# ⚠️ This list is NOT self-maintaining. ``dumps_json`` sits on the BASE SQLiteTable,
# which has 14 subclasses, so ANY of their JSON columns could hold escapes. A
# whole-DB scan when this was written found these 5 to be exactly the affected set
# (0 columns missed) — but a table added later would silently fall outside it. So
# ``--audit-all`` scans every column and REPORTS anything escaped that is not
# listed here, turning a future gap into a visible finding instead of a silent miss.
TARGETS: tuple[tuple[str, str], ...] = (
    ("messages", "content"),
    ("transcript_chunks", "content"),
    ("transcript_chunks", "metadata"),
    ("knowledge_chunks", "content"),
    ("todos", "linked_context"),
    # A THIRD write door found by the meta-review: chat_thread_manager
    # pre-serializes these two, so _serialize_value never sees the list.
    ("thread_summaries", "key_decisions"),
    ("thread_summaries", "open_questions"),
)

# FTS indexes are DERIVED (external-content over their base table), so rebuilding
# one destroys nothing — verified: content=messages / =transcript_chunks / =knowledge_chunks.
#
# ⚠️ Which base tables have SYNC TRIGGERS is NOT uniform, and assuming it is was a
# real defect here. Measured on the live DB:
#     messages          → 3 triggers (insert/delete/update)  → self-reindexes on UPDATE
#     transcript_chunks → 0 triggers                          → index goes STALE
#     knowledge_chunks  → 0 triggers                          → index goes STALE
# So converting a trigger-less base table WITHOUT rebuilding leaves its index holding
# the old escape tokens — the very defect this script exists to remove, inverted. The
# rebuild is therefore MANDATORY for a touched trigger-less table (see
# ``_required_rebuilds``), not an opt-in flag. (I had measured triggers on `messages`
# only and generalized to all three — REVIEW caught it.)
FTS_BY_BASE: dict[str, str] = {
    "messages": "messages_fts",
    "transcript_chunks": "transcript_fts",
    "knowledge_chunks": "knowledge_fts",
}
FTS_TABLES: tuple[str, ...] = tuple(FTS_BY_BASE.values())

# CJK SEGMENTATION COLUMNS (run_4ed75215). Each FTS index now indexes `*_seg`
# columns holding bigram-expanded text, because SQLite FTS5 has no built-in
# tokenizer that can segment a 2-character Chinese word — so a Chinese query
# against the raw columns was returning almost nothing (measured 11.1% recall on
# real data, 10 of 40 sampled terms returning zero).
#
# Why the expansion lives in a COLUMN and not just inside the index: FTS5
# re-derives tokens from its content source on 'rebuild', on DELETE FROM <fts>,
# and inside the raw-`old.*` triggers. Pointing the index at a persisted _seg
# column makes all of those re-derive the already-expanded text; writing expanded
# text into an index whose source is the raw column corrupts it (verified: stale
# postings survive an UPDATE and `integrity-check rank=1` reports "database disk
# image is malformed").
#
# ORDERING MATTERS AND IS WHY THIS SCRIPT DOES BOTH JOBS IN ONE PASS: escape
# re-encoding must happen BEFORE segmentation, or the _seg column would be built
# from literal \uXXXX text and hold no Chinese characters at all. And the final
# 'rebuild' is only correct because it re-derives from _seg — which is exactly
# why history is touched once, here, rather than by two separate migrations.
SEG_COLUMNS: dict[str, tuple[tuple[str, str], ...]] = {
    "messages": (("content", "content_seg"),),
    "transcript_chunks": (("content", "content_seg"),
                          ("source_file", "source_file_seg")),
    "knowledge_chunks": (("content", "content_seg"),
                         ("heading", "heading_seg"),
                         ("source_file", "source_file_seg")),
}


def _reject_json_constant(name: str):
    """Reject NaN/Infinity during the round-trip check (they break the invariant)."""
    raise ValueError(f"non-JSON constant: {name}")


def classify(stored: str) -> tuple[str | None, str]:
    """Decide whether a value needs re-encoding, and say WHY when it does not.

    Returns ``(reencoded_text_or_None, reason)``. Selection is semantic, not
    pattern-based: a row qualifies iff re-encoding without ASCII escaping actually
    changes the text AND introduces non-ASCII characters. That catches
    ``\\u3000``/``\\u2018``/emoji, which a CJK-plane LIKE pattern misses.

    Every skip carries a reason so the report can ACCOUNT for skipped rows instead
    of printing a silent count. Measured against the real corpus, every skip is
    legitimate and the dominant class is **prose that merely contains a literal
    backslash-u** (a markdown chunk quoting code) — not JSON at all, and it would be
    CORRUPTED by a rewrite. A conservative skip is correct behavior; an unexplained
    one is not.
    """
    # A TEXT-affinity column can still hold a BLOB or an int (SQLite is dynamically
    # typed), and `"\\u" not in <bytes>` raises TypeError — which would abort the
    # whole survey mid-scan with a traceback. Skip non-text explicitly.
    if not isinstance(stored, str):
        return None, "not_text"
    if not stored or "\\u" not in stored:
        return None, "no_escape"
    try:
        obj = json.loads(stored)
    except (json.JSONDecodeError, TypeError):
        # Prose containing a literal `\u` — rewriting it would corrupt content.
        return None, "not_json"
    if not isinstance(obj, (list, dict)):
        return None, "json_scalar"
    reencoded = json.dumps(obj, ensure_ascii=False)
    if reencoded == stored:
        return None, "already_raw"
    if reencoded.isascii():
        # A structural escape (\n, \", \\), not a non-ASCII character.
        return None, "escape_not_non_ascii"
    # Semantic guarantee: the rewrite must preserve the parsed object exactly.
    # ``parse_constant`` rejects NaN/Infinity — those satisfy a naive ``!=`` check
    # only by object identity (by VALUE, nan != nan), so the invariant would be
    # VACUOUS for them, and the output is not valid JSON for any non-Python reader.
    try:
        if json.loads(reencoded, parse_constant=_reject_json_constant) != obj:
            return None, "round_trip_inequality"
    except (json.JSONDecodeError, ValueError):
        return None, "reencode_unparseable"
    # ⚠️ A LONE SURROGATE would abort the ENTIRE run if it reached the UPDATE.
    # Real corpora contain them (truncated model output, a JS client's \\ud800): the
    # STORED form is pure ASCII so SQLite accepted it, and json.loads happily yields
    # the surrogate — but sqlite3's UTF-8 encode then raises UnicodeEncodeError, which
    # propagates out of the chunk loop AND out of main() (which catches only
    # FileNotFoundError/OSError), so the run dies with a traceback, the report and the
    # backup path are DISCARDED, and every re-run dies on the same row forever.
    # Reproduced end-to-end; Gate-2 CRITICAL. Prove encodability HERE, where a failure
    # is one attributed skip instead of a permanently un-completable run.
    try:
        reencoded.encode("utf-8")
    except UnicodeEncodeError:
        return None, "unencodable_surrogate"
    return reencoded, "convert"


def audit_unlisted_columns(db: Path | str) -> dict[str, int]:
    """Find escaped columns that ``TARGETS`` does NOT cover.

    Guards the one real weakness of a hand-maintained target list: it cannot know
    about a table added after it was written. Returns ``{"table.column": row_count}``
    for every column holding escapes that is not in ``TARGETS`` — empty dict means
    the list is still complete. Read-only; skips virtual tables (their modules may
    be unavailable) and any column that cannot be scanned.
    """
    db = Path(db)
    listed = {f"{t}.{c}" for t, c in TARGETS}
    unlisted: dict[str, int] = {}
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        tables = [
            name
            for name, sql in conn.execute(
                "SELECT name, sql FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
            if sql and "USING" not in sql.upper()
        ]
        for table in tables:
            try:
                columns = [r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')]
            except sqlite3.Error:
                continue
            for column in columns:
                key = f"{table}.{column}"
                if key in listed:
                    continue
                try:
                    count = conn.execute(
                        f'SELECT count(*) FROM "{table}" '
                        f'WHERE CAST("{column}" AS TEXT) LIKE ? ESCAPE \'!\'',
                        ("%!\\u%",),
                    ).fetchone()[0]
                except sqlite3.Error:
                    continue
                if count:
                    unlisted[key] = count
    finally:
        conn.close()
    return unlisted


def _has_sync_triggers(conn: sqlite3.Connection, base_table: str) -> bool:
    """Does this base table have an FTS-SYNC trigger (so it self-reindexes on UPDATE)?

    Must match a trigger that actually writes THIS table's FTS index — not merely
    *any* trigger on the table. An unrelated ``updated_at`` touch-trigger would
    otherwise make this return True and silently skip the mandatory rebuild, leaving
    a stale index: the very defect this function exists to prevent, reintroduced
    through its own guard (Gate-2 HIGH, reproduced).

    Trigger presence is NOT uniform here — read it, never assume: only ``messages``
    has sync triggers; ``transcript_chunks``/``knowledge_chunks`` have none.
    """
    fts = FTS_BY_BASE.get(base_table)
    if not fts:
        return False
    rows = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='trigger' AND tbl_name=?",
        (base_table,),
    ).fetchall()
    return any(sql and fts in sql for (sql,) in rows)


def _required_rebuilds(conn: sqlite3.Connection, touched_bases: set[str]) -> list[str]:
    """FTS indexes that MUST be rebuilt because their base table has no triggers.

    A trigger-less external-content index does not follow an UPDATE on its base
    table, so skipping the rebuild would leave it indexing the old escaped tokens.
    """
    required = []
    for base in sorted(touched_bases):
        fts = FTS_BY_BASE.get(base)
        if not fts or not _table_exists(conn, fts):
            continue
        if not _has_sync_triggers(conn, base):
            required.append(fts)
    return required


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name=?",
        (table,),
    ).fetchone()
    return row is not None


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    return any(r[1] == column for r in conn.execute(f'PRAGMA table_info("{table}")'))


def _backup(db: Path) -> Path:
    """Take a SNAPSHOT-CONSISTENT backup via the SQLite backup API.

    ⚠️ Do NOT "simplify" this to ``shutil.copy2``. The production DB runs in **WAL
    mode with a live daemon writing concurrently** (verified: ``journal_mode=wal``,
    a multi-MB ``-wal`` alongside 6 running backend processes). Three independent
    file copies produce a TORN backup: the main file is copied mid-write, then the
    ``-wal`` is copied afterwards so its frames no longer correspond to the copied
    pages — and copying ``-shm`` is actively harmful, since a stale shm with a
    mismatched salt can make SQLite discard or misapply the WAL. The result is a file
    that LOOKS like a backup and fails exactly when it is needed (REVIEW caught this;
    it was the single highest-stakes defect in this script).

    ``Connection.backup`` holds a read transaction for the copy, so the destination
    is a consistent point-in-time image with the WAL already applied — one file, no
    sidecars. Then ``PRAGMA integrity_check`` PROVES it before we let any write
    proceed: an unverified backup is not a backup (STEERING #20).

    Raises ``OSError`` on any failure, after removing the partial file — so a
    truncated copy can never sit there wearing the official backup name and get
    trusted later.
    """
    # DISK PRECHECK. The backup is a FULL copy of the database (636 MB on this
    # machine today), and the caller then grows the index by ~46%. Neither had any
    # check: on a full disk `copy2`/`Connection.backup` fails part-way and leaves a
    # truncated file that LOOKS like a backup — the worst possible outcome for the
    # one artifact that exists to make the write reversible. Refuse early with a
    # number the operator can act on, instead of failing mid-write.
    import shutil as _shutil

    db_bytes = Path(db).stat().st_size
    needed = int(db_bytes * 1.6)      # the backup copy + ~46% index growth + slack
    free = _shutil.disk_usage(Path(db).parent).free
    if free < needed:
        raise OSError(
            f"refusing to back up: need ~{needed // 1_000_000} MB free "
            f"(a {db_bytes // 1_000_000} MB copy plus index growth), "
            f"only {free // 1_000_000} MB available on "
            f"{Path(db).parent}. Free space and re-run."
        )

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = db.with_name(f"{db.stem}.pre-cjk-backfill-{stamp}.db")
    src = dst = None
    try:
        src = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        dst = sqlite3.connect(str(target))
        src.backup(dst)
        dst.close()
        dst = None
        verify = sqlite3.connect(f"file:{target}?mode=ro", uri=True)
        try:
            result = verify.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            verify.close()
        if result != "ok":
            raise OSError(f"backup failed integrity_check: {result}")
        # A full copy of every chat message — narrow it to owner-only rather than
        # leaving a second world-readable history sitting in the home dir.
        os.chmod(target, 0o600)
    except Exception as exc:
        for conn in (src, dst):
            if conn is not None:
                try:
                    conn.close()
                except Exception:  # noqa: BLE001 — best effort on an error path
                    pass
        target.unlink(missing_ok=True)
        raise OSError(f"backup failed, nothing written: {exc}") from exc
    finally:
        if src is not None:
            try:
                src.close()
            except Exception:  # noqa: BLE001
                pass
    return target


#: A caller must pass this EXACT token to write. A bool default is not a gate —
#: `backfill(db, apply=True)` is importable, so a future job/hook/"auto-repair"
#: could reach the UPDATE loop with no human involved, which is precisely the shape
#: that once wiped this project's data.db. A non-guessable token cannot be supplied
#: by accident or by a generic `**kwargs` pass-through (REVIEW HIGH).
WRITE_CONFIRMATION = "I-UNDERSTAND-THIS-REWRITES-USER-DATA"


# How many times a row skipped by the compare-and-swap is re-surveyed before it is
# reported as still-changing. Bounded, because an unbounded retry against a live
# daemon that keeps rewriting the same row would never terminate.
_STALE_RETRY_PASSES = 2


def _populate_seg_columns(
    conn: sqlite3.Connection,
    *,
    apply: bool = False,
    confirm: str | None = None,
    chunk_size: int = 500,
    _only_base: str | None = None,
) -> dict[str, int]:
    """Fill the CJK segmentation columns for every row that needs one.

    Returns ``{base_table: rows_written}``. In dry-run it returns the counts it
    WOULD write and touches nothing.

    IDEMPOTENT BY PREDICATE, not by a marker: a row is selected only when its
    ``*_seg`` value differs from ``expand_cjk(source)``. Re-running therefore
    converges to zero, which is what makes "touch history once" verifiable rather
    than merely intended — the second run's report shows 0 and proves it.

    Chunked + committed per chunk for the same reason the escape phase is: this
    runs against a live database that the daemon is writing to, and holding one
    transaction over ~170K rows would lock it out.

    A table missing its ``*_seg`` column is SKIPPED, not an error: the column is
    created by the owning module's schema path (``ensure_tables`` /
    ``_run_versioned_migrations``), so a database that has not started the new
    code yet simply has nothing to populate. Failing here would make the script
    unusable exactly when someone runs it before restarting the daemon.
    """
    from core.cjk_index import expand_cjk

    # Same gate as `backfill`: a bare `apply=True` boolean is one attribute away
    # for any future job, hook or "auto-repair" that imports this module, and this
    # function rewrites rows in the user's live database. The unguessable token
    # makes reaching the write path a deliberate act rather than a default.
    if apply and confirm != WRITE_CONFIRMATION:
        raise PermissionError(
            "refusing to write: pass confirm=WRITE_CONFIRMATION alongside "
            "apply=True. Segmentation rewrites rows in a live user database."
        )

    written: dict[str, int] = {}
    for base, pairs in SEG_COLUMNS.items():
        if _only_base is not None and base != _only_base:
            continue  # a retry pass, scoped to the one table that had stale rows
        if not _table_exists(conn, base):
            continue
        usable = [(src, seg) for src, seg in pairs
                  if _column_exists(conn, base, src)
                  and _column_exists(conn, base, seg)]
        if not usable:
            continue

        src_cols = ", ".join(src for src, _ in usable)
        seg_cols = ", ".join(seg for _, seg in usable)
        # ITERATE the cursor, never fetchall(): this runs against a live database
        # with ~170K rows across the three tables, and materializing every row's
        # full content PLUS an expansion per row (~3x the source size) is a
        # multi-GB spike in a script the operator runs while the daemon is
        # attached. An OOM kill mid-pass is survivable (the phase is idempotent
        # and commits per chunk) but wastes the whole run.
        cursor = conn.execute(
            f"SELECT rowid, {src_cols}, {seg_cols} FROM {base}")

        n = len(usable)
        pending: list[tuple] = []
        for row in cursor:
            rowid = row[0]
            sources = row[1:1 + n]
            current = row[1 + n:1 + 2 * n]
            wanted = [expand_cjk(str(s or "")) for s in sources]
            if list(current) == wanted:
                continue  # already correct — the idempotence predicate
            # Carry the SOURCE values too: the write below compare-and-swaps on
            # them, because a rowid is not a stable identity here (see the CAS
            # comment at the UPDATE).
            pending.append((rowid, wanted, list(sources)))

        written[base] = len(pending)
        if not apply or not pending:
            continue

        set_clause = ", ".join(f"{seg} = ?" for _, seg in usable)
        # COMPARE-AND-SWAP on the source columns, not just the rowid.
        #
        # A rowid is NOT a stable identity on these tables: `messages` has no
        # AUTOINCREMENT, so SQLite REUSES the rowid of a deleted row, and rows do
        # get deleted (90-day TTL, session cascade). The survey above ran minutes
        # earlier on a live database. Without this guard, a row that expired
        # between survey and write hands its segmented text to whatever NEW row
        # took its rowid — that row becomes unfindable, and searching the old text
        # surfaces the wrong message. Worse, the idempotence predicate would keep
        # re-applying it on every run, so it never self-corrects.
        #
        # The escape phase in this same script already does exactly this for the
        # same reason; omitting it here was the gap.
        where_cas = " AND ".join(f'"{src}" IS ?' for src, _ in usable)
        stale = 0
        for i in range(0, len(pending), chunk_size):
            with conn:  # one transaction per chunk
                for rowid, wanted, sources in pending[i:i + chunk_size]:
                    cur = conn.execute(
                        f"UPDATE {base} SET {set_clause} "
                        f"WHERE rowid = ? AND {where_cas}",
                        (*wanted, rowid, *sources),
                    )
                    if cur.rowcount == 0:
                        stale += 1  # row changed or vanished since the survey
        if stale:
            # RETRY, do not merely count. A row the daemon rewrote between survey
            # and write is skipped by the CAS — correctly, since our expansion is
            # of stale text — but leaving it at that makes the skip PERMANENT for
            # this pass, and the report then shows `would_convert: 0` alongside a
            # nonzero stale count, which reads as "done" to an operator. The row's
            # source has already settled by now, so a bounded re-survey converges.
            for _ in range(_STALE_RETRY_PASSES):
                again = _populate_seg_columns(
                    conn, apply=True, confirm=confirm, chunk_size=chunk_size,
                    _only_base=base)
                remaining = again.get(f"{base}:changed_since_survey", 0)
                written[base] += again.get(base, 0)
                if not remaining:
                    stale = 0
                    break
                stale = remaining
            written[base] -= stale
            if stale:
                written[f"{base}:changed_since_survey"] = stale
    return written




def backfill(
    db: Path | str,
    *,
    apply: bool = False,
    confirm: str | None = None,
    rebuild_fts: bool = False,
) -> dict:
    """Re-encode escaped JSON columns. DRY RUN unless ``apply=True`` AND ``confirm``.

    Writing requires BOTH ``apply=True`` and ``confirm=WRITE_CONFIRMATION``. The
    second is deliberately redundant-looking: it makes an accidental or automated
    write structurally impossible rather than one boolean away.

    Returns a report: ``would_convert``, ``converted``, ``skipped``, ``per_table``,
    ``backup_path`` (only on an applied run), ``fts_rebuilt``.

    Raises:
        PermissionError: ``apply=True`` without the correct ``confirm`` token.
        FileNotFoundError: the database does not exist.
        OSError: the pre-write backup could not be taken or verified.
    """
    db = Path(db)
    if not db.exists():
        raise FileNotFoundError(f"database not found: {db}")
    if apply and confirm != WRITE_CONFIRMATION:
        raise PermissionError(
            "refusing to write: pass confirm=WRITE_CONFIRMATION alongside apply=True. "
            "This rewrites irreplaceable user data and must be a deliberate, "
            "human-authorized act — never reachable by flipping a boolean."
        )

    report: dict = {
        "db": str(db),
        "applied": bool(apply),
        "would_convert": 0,
        "converted": 0,
        "skipped": 0,
        # Every skip is attributed by reason — a bare count would hide whether a
        # row was correctly left alone (prose containing a literal `\u`) or wrongly
        # missed. Measured on the real corpus: all skips are the former.
        "skipped_by_reason": {},
        "per_table": {},
        "backup_path": None,
        "fts_rebuilt": [],
        "seg_populated": {},
    }

    # PASS 1 — read-only survey. Runs identically in both modes, so the dry-run
    # report is exactly what an applied run would do.
    # Each entry carries the ORIGINAL text so PASS 2 can compare-and-swap on it
    # (see the CAS guard below — without it a live daemon's edits get reverted).
    pending: list[tuple[str, str, int, str, str]] = []  # (table, col, rowid, new, old)
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        for table, column in TARGETS:
            if not _table_exists(conn, table) or not _column_exists(conn, table, column):
                continue
            hits = 0
            rows = conn.execute(
                f'SELECT rowid, "{column}" FROM "{table}" '
                f'WHERE "{column}" LIKE ? ESCAPE \'!\'',
                ("%!\\u%",),
            ).fetchall()
            for rowid, stored in rows:
                new_text, reason = classify(stored)
                if new_text is None:
                    report["skipped"] += 1
                    key = f"{table}.{column}:{reason}"
                    report["skipped_by_reason"][key] = (
                        report["skipped_by_reason"].get(key, 0) + 1
                    )
                    continue
                pending.append((table, column, rowid, new_text, stored))
                hits += 1
            if hits:
                report["per_table"][f"{table}.{column}"] = hits
    finally:
        conn.close()

    report["would_convert"] = len(pending)

    # ── CJK segmentation phase ───────────────────────────────────────────────
    # Placed BEFORE the early returns on purpose:
    #   * a DRY RUN must report what it would segment, or its report understates
    #     the work and the operator cannot tell whether running it is worthwhile;
    #   * an --apply with ZERO escaped rows still needs segmenting — the two
    #     phases touch overlapping but different row sets, and after the escape
    #     backlog is cleared once, segmentation is the only work left.
    # It opens its own connection because the survey above closed its own in the
    # `finally`, and it re-checks its own idempotence predicate per row, so
    # running it on both paths is safe.
    def _run_seg_phase(*, write: bool) -> None:
        """Count (and optionally write) the segmentation columns.

        Opens its own connection because the survey above closed its own in a
        `finally`. Idempotent per row, so calling it to COUNT and later to WRITE
        is safe.
        """
        seg_conn = sqlite3.connect(str(db))
        try:
            # MERGE rather than overwrite: on the `pending` path this runs after a
            # dry-run count already recorded figures, and clobbering them would
            # under-report what the pass actually did.
            report["seg_populated"] = {
                **(report.get("seg_populated") or {}),
                **_populate_seg_columns(
                    seg_conn, apply=write,
                    confirm=WRITE_CONFIRMATION if write else None),
            }
        except Exception as exc:  # noqa: BLE001 — recorded, never swallowed
            report["seg_failed"] = f"{type(exc).__name__}: {exc}"
            report["resume_hint"] = (
                "CJK segmentation failed — re-run --apply once the cause is "
                "cleared; the phase is idempotent"
            )
        finally:
            seg_conn.close()

    if not apply:
        # DRY RUN: report what segmentation WOULD write, so the operator can see
        # the real size of the job. Counted against the CURRENT (still-escaped)
        # text, which is the honest answer for "if I ran this now".
        _run_seg_phase(write=False)
        return report
    # An --apply with zero escaped rows still needs segmenting: the two phases
    # touch overlapping but different row sets, and once the escape backlog is
    # cleared, segmentation is the only remaining work.
    if not pending and not rebuild_fts:
        # Back up FIRST. `--apply` promises a snapshot before any row is written,
        # and this branch DOES write (it segments every row that needs it) — the
        # backup used to be taken only on the escape path, so the steady state
        # after the first successful run silently wrote with no snapshot.
        if report.get("backup_path") is None:
            report["backup_path"] = str(_backup(db))
        _run_seg_phase(write=True)
        # The index must be refreshed for whatever the phase just rewrote.
        for _base, _n in (report.get("seg_populated") or {}).items():
            _fts = FTS_BY_BASE.get(_base)
            if _n and _fts:
                seg_conn = sqlite3.connect(str(db))
                try:
                    seg_conn.execute(
                        f"INSERT INTO {_fts}({_fts}) VALUES('rebuild')")
                    seg_conn.commit()
                    report["fts_rebuilt"].append(_fts)
                except Exception as exc:  # noqa: BLE001
                    report["fts_stale"] = True
                    report.setdefault("crashed", f"{type(exc).__name__}: {exc}")
                finally:
                    seg_conn.close()
        return report
    # NOTE: `pending` may be EMPTY here when rebuild_fts is set. That path is
    # load-bearing: after a crashed or already-completed conversion the index can be
    # stale while nothing is left to convert, and an early return would make
    # `--rebuild-fts` a no-op — i.e. unusable as the repair tool it is documented to
    # be (Gate-2 CRITICAL, reproduced: it reported fts_rebuilt=[]).

    # PASS 2 — write. Backup FIRST; a failed backup aborts before any write
    # (STEERING #20: never rewrite an irreplaceable store without a recoverable copy).
    # Taken BEFORE the writable connection exists, so our own lock can never block it.
    if pending:
        report["backup_path"] = str(_backup(db))

    conn = sqlite3.connect(str(db))
    try:
        conn.execute("PRAGMA busy_timeout=10000")
        # COMPARE-AND-SWAP + CHUNKED COMMITS. Both exist because the daemon is
        # WRITING CONCURRENTLY while this runs (REVIEW caught both):
        #
        # 1. CAS (``AND "col" = ?`` on the surveyed text) — PASS 1 read on a separate
        #    read-only connection, so a row can change in between. Worse, `messages`
        #    has no AUTOINCREMENT, so SQLite REUSES the rowid of a deleted row: a
        #    bare ``WHERE rowid=?`` could overwrite an unrelated NEW message with
        #    stale content. That is silent destruction of irreplaceable user data —
        #    exactly what this script exists to avoid. A 0-rowcount UPDATE means the
        #    row moved on; count it, never force it.
        # 2. CHUNKED commits — one transaction over tens of thousands of rows holds
        #    the write lock far longer than the daemon's busy_timeout + retries, so
        #    live message writes would fail with "database is locked" and be LOST.
        #    Chunking bounds lock-hold; the CAS guard makes each chunk independently
        #    safe and the whole run resumable (re-running converts only what remains).
        chunk = 500
        # A mid-chunk failure (disk full, lock timeout, Ctrl-C) must NOT escape as a
        # bare traceback: earlier chunks are already COMMITTED, so the caller needs
        # the truthful partial report — how many landed, where the backup is, and
        # that a trigger-less index is now stale relative to a half-converted base
        # table (false-positive matches until rebuilt). Losing that to an exception
        # is what made a crashed run unrecoverable (Gate-2 CRITICAL).
        try:
            for start in range(0, len(pending), chunk):
                with conn:  # one transaction per chunk
                    for table, column, rowid, new_text, old_text in pending[start:start + chunk]:
                        cur = conn.execute(
                            f'UPDATE "{table}" SET "{column}" = ? '
                            f'WHERE rowid = ? AND "{column}" = ?',
                            (new_text, rowid, old_text),
                        )
                        if cur.rowcount:
                            report["converted"] += cur.rowcount
                        else:
                            # The row changed or vanished since the survey — leave it.
                            report["skipped"] += 1
                            key = f"{table}.{column}:changed_since_survey"
                            report["skipped_by_reason"][key] = (
                                report["skipped_by_reason"].get(key, 0) + 1
                            )
        except Exception as exc:  # noqa: BLE001 — recorded and re-surfaced below
            report["crashed"] = f"{type(exc).__name__}: {exc}"
            report["fts_stale"] = True
            report["resume_hint"] = (
                "re-run the same command to convert the remainder, then "
                "--apply --rebuild-fts to refresh the indexes"
            )
        # MANDATORY rebuilds: a converted base table with NO sync triggers leaves its
        # external-content index holding the old escape tokens. Not optional, and it
        # does not wait for --rebuild-fts. Uses the surveyed set: a table whose rows
        # were all CAS-skipped gets a redundant (harmless, idempotent) rebuild, which
        # is the right side to err on — the index is derived.
        # ── CJK segmentation phase (ordered) ─────────────────────────────────
        # AFTER escape re-encoding, so it segments real Chinese rather than
        # literal \uXXXX — a row stored as escapes would otherwise get a _seg
        # column holding "u914d u7f6e" and stay unsearchable while both phases
        # reported success. BEFORE the rebuild, so the index has something to
        # derive from. This ordering is what makes one pass over history correct.
        _run_seg_phase(write=True)

        touched = {table for table, _, _, _, _ in pending}
        # A table can need a rebuild because its _seg column was (re)populated
        # even when no escaped row was converted — the two phases touch
        # overlapping but different row sets.
        touched |= {b for b, n in (report.get("seg_populated") or {}).items() if n}
        # Guarded for the same reason the chunk loop is: if the rebuild ALSO fails
        # (read-only DB, disk full, lock), the already-committed conversions and the
        # backup path must still reach the caller. An unguarded rebuild threw the
        # whole report away — the exact failure mode the crash handling above exists
        # to prevent, one block further down (found by the crash regression test).
        try:
            to_rebuild = _required_rebuilds(conn, touched)
            if rebuild_fts:  # explicit repair: also refresh the trigger-backed ones
                to_rebuild += [
                    f for f in FTS_TABLES if _table_exists(conn, f) and f not in to_rebuild
                ]
            for fts in to_rebuild:
                conn.execute(f"INSERT INTO {fts}({fts}) VALUES('rebuild')")
                report["fts_rebuilt"].append(fts)
            if to_rebuild:
                conn.commit()  # the `with conn` blocks above already exited
        except Exception as exc:  # noqa: BLE001 — recorded, never swallowed silently
            report["fts_stale"] = True
            report.setdefault("crashed", f"{type(exc).__name__}: {exc}")
            report["resume_hint"] = (
                "conversions landed but an index rebuild failed — re-run "
                "--apply --rebuild-fts once the cause is cleared"
            )
    finally:
        conn.close()

    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Re-encode \\uXXXX-escaped JSON columns as raw UTF-8 "
                    "(dry-run by default)."
    )
    ap.add_argument("--db", type=Path, default=DEFAULT_DB,
                    help=f"SQLite database (default: {DEFAULT_DB})")
    ap.add_argument("--apply", action="store_true",
                    help="Actually write (default: dry-run, reports would_convert only). "
                         "Takes a VERIFIED snapshot backup first and rebuilds any "
                         "trigger-less FTS index it touched.")
    ap.add_argument("--rebuild-fts", action="store_true",
                    help="Also rebuild the derived FTS indexes (repair path — the "
                         "update triggers already reindex each converted row).")
    ap.add_argument("--audit-all", action="store_true",
                    help="Also scan EVERY column for escapes and report any that "
                         "TARGETS does not cover (catches a table added later).")
    args = ap.parse_args(argv)

    try:
        report = backfill(
            args.db,
            apply=args.apply,
            # The CLI IS the human-authorized entry point — typing --apply at a
            # terminal is the deliberate act, so it supplies the token here.
            confirm=WRITE_CONFIRMATION if args.apply else None,
            rebuild_fts=args.rebuild_fts,
        )
        if args.audit_all:
            report["unlisted_escaped_columns"] = audit_unlisted_columns(args.db)
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        # A failed backup must be LOUD and must not read as a partial success —
        # _backup already removed the partial file (STEERING #20).
        print(f"BACKUP FAILED — nothing written: {exc}", file=sys.stderr)
        return 3

    print(json.dumps(report, indent=2, ensure_ascii=False))
    if not args.apply and report["would_convert"]:
        print(
            f"\nDRY RUN — {report['would_convert']} row(s) would be re-encoded. "
            f"Nothing written. Re-run with --apply to convert "
            f"(a timestamped backup is taken first).",
            file=sys.stderr,
        )
    unlisted = report.get("unlisted_escaped_columns")
    if unlisted:
        print(
            f"\n⚠️  {len(unlisted)} escaped column(s) are NOT in TARGETS and were "
            f"NOT converted: {unlisted}. Add them to TARGETS if they should be.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
