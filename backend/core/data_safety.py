"""Unified pre-action destruction guard for irreplaceable data stores.

WHY THIS EXISTS (COE data.db loss, 2026-08-12): the entire user data.db was wiped
(months of chat/channel/agent data, no backup) and destructive operations were
scattered across code paths (main.py `_purge_corrupt_db` hard-unlinked data.db,
`swarm_workspace_manager.delete_project` rmtree'd a whole DDD, etc.) with NO
pre-action human authorization and NO isolation — the heaviest irreversible action
gated by the weakest judge (STEERING #20, SOUL P4).

⚠️ CAUSAL NOTE (do NOT cite the purge path as the 8-12 trigger — log-falsified):
A `DatabaseError → _purge_corrupt_db(unlink) → _reseed_from_seed(empty)` recovery
path DID exist (introduced 7-18) and is exactly this destroy-user-data anti-pattern,
which is WHY this module guards it. But daemon-log evidence shows it NEVER FIRED for
the 8-12 loss: the purge/reseed signature is absent from all logs (incl. the window
covering 8-12), and the 8-12 boot was `Using existing user database / Fast startup`.
The actual 8-12 mechanism was DIFFERENT — a daemon-external process defaulting to the
live production DB and full-init'ing a fresh one (the upstream `get_app_data_dir` /
`SQLiteDatabase(db_path=None)` / create-if-missing class, addressed separately by the
`SWARM_DATA_DIR` escape hatch). SAME failure CLASS (irreplaceable user data destroyed,
no approval/backup), DIFFERENT path. This module hardens the anti-pattern regardless
of which path fired; the lesson holds either way.

THE FIX — a shared PRESERVE-not-DESTROY primitive (`isolate_store`) plus a
full pre-action-approval chokepoint (`guard_destructive`) for the destroy sites
that CAN await a human:

    ISOLATE (rename, never delete)  →  PRE-ACTION approval (reuse PermissionManager)
                                     →  approve: return (caller destroys)
                                     →  deny/timeout/no-session/cold-start: raise
                                        DestructionBlocked (data already isolated → preserved)

WHAT ROUTES THROUGH WHAT (be precise — do not claim more wiring than exists):
  * `isolate_store` (rename + sidecars, never unlink) is the shared preserve
    primitive. The BOOT DB-corruption path (`main._init_db_bounded` →
    `_purge_corrupt_db`) uses it DIRECTLY: boot cannot await an approval (it would
    wedge the daemon in `initializing` until launchd kills it — the cold_start
    degraded branch), so it isolates + re-seeds a fresh store + drops a recovery
    marker that surfaces the recover-vs-discard decision at the next session open.
  * `delete_project` (a user-initiated delete) applies the SAME preserve invariant
    as a directory trash-move (rename into `Projects/.trash/`), not this async guard
    — a user who clicked delete should not be blocked on a 4h approval.
  * `guard_destructive` is the FULL chokepoint (isolate → await approval → raise on
    deny) for any FUTURE destroy site that runs in a live chat-session context and
    genuinely can await a human. It is intentionally NOT yet on a hot path — the two
    current destroy sites above are structurally sessionless (boot / HTTP DELETE),
    so wiring them through the awaiting guard would only ever hit its degraded
    branch. Kept as the canonical entry for session-context destroys to come.

DESIGN INVARIANTS (run_a456640f — do NOT regress):
  * PRESERVE, never DESTROY: `isolate_store` RENAMES (target + -wal/-shm sidecars
    together) to `<name>.corrupt-<ts>`; it NEVER unlink/replace-over the original.
    Reverting the rename to an unlink turns test_ac1_* RED (mutation-proven).
  * SELECTIVE, not blanket: `classify_store` returns REPLACEABLE for
    index/cache/*.tmp → guard returns IMMEDIATELY, permission engine untouched
    (no latency on rebuildable stores). Fail-CLOSED: an unknown path → IRREPLACEABLE.
  * PRE-ACTION, not post-hoc: approval is awaited BEFORE the destroy, surfaced in
    the initiating chat session; a deny leaves the isolated copy intact.
  * REUSE, don't rebuild: the approval flow is the SAME `permission_manager`
    (store_pending_request → enqueue_permission_request → wait_for_permission_decision)
    + the SAME `cmd_permission_request` SSE event that `dangerous_command_gate` uses.
    Only an additive `kind: "destructive_data"` field is added. No new engine/queue/event.
  * NEVER await on a sessionless path: cold-start (`cold_start=True`) and the
    no-session case (`session_id is None`, e.g. an HTTP DELETE with no chat context)
    take the DEGRADED branch — isolate + raise, NEVER enqueue to a None/unmonitored
    queue, NEVER block boot. (An awaited approval at boot would wedge the daemon in
    `initializing` until launchd kills it.)
  * NON-BLOCKING: all async/await; the isolate rename is a sync-fast (<1ms) op run
    inline (NOT via `asyncio.to_thread` / the default pool — COE run_b36c7880's
    16-worker saturation). The guard fires ONLY at the rare destroy-moment; it is
    never on the send/streaming hot path.
"""
from __future__ import annotations

import errno
import json
import logging
import shutil
import sqlite3
from datetime import datetime
from enum import Enum
from pathlib import Path
from uuid import uuid4

from core.permission_manager import permission_manager

logger = logging.getLogger(__name__)

# The filename of the boot-recovery marker dropped in the app data dir when a
# suspected-corrupt store was isolated + a fresh store re-seeded. Its EXISTENCE
# is what makes the reseed NON-SILENT: the attention channel reads it at the
# next session open and surfaces a BLOCKING "recover or discard?" decision to
# the user IN their chat window (STEERING #20 "reach the human"). Cleared once
# the user decides.
RECOVERY_MARKER_NAME = ".db-recovery-pending.json"

# Genuine SQLite corruption signatures. ONLY these justify isolating + re-seeding
# the store. STEERING #20 + the COE (run_2d3417d9): the heaviest irreversible
# action (replacing the live store) must NEVER be triggered by the weakest judge
# — a bare `DatabaseError`. A locked/busy db, a full disk, a permissions error
# are NOT corruption; treating them as such is exactly what destroyed a valid db.
_CORRUPTION_SIGNATURES = (
    "database disk image is malformed",
    "file is not a database",
    "file is encrypted or is not a database",
    "malformed database schema",
    "unsupported file format",   # header/version damage (adv review — real corruption)
    "database is corrupt",
    "database corruption",
)


def is_corruption_error(exc: BaseException) -> bool:
    """Is this exception genuine whole-store corruption (vs a transient/benign fault)?

    FAIL-CLOSED FOR THE DESTRUCTIVE ACTION (the inverse of classify_store's
    fail-closed): an UNRECOGNIZED error is NOT classified as corruption, so the
    caller does NOT isolate+reseed — it propagates as a bounded restart instead.
    Data loss is unbounded+irreversible; a crash-loop is bounded (launchd
    KeepAlive) — never trade the unbounded harm to avoid the bounded one.

    * sqlite3.OperationalError (locked/busy) → False (subclass of DatabaseError,
      checked first — a valid-but-busy db must never be judged corrupt).
    * sqlite3.DatabaseError whose message matches a known corruption signature
      → True.
    * anything else (full disk, permission, novel message, non-sqlite) → False.
    """
    if isinstance(exc, sqlite3.OperationalError):
        return False
    if isinstance(exc, sqlite3.DatabaseError):
        msg = str(exc).lower()
        return any(sig in msg for sig in _CORRUPTION_SIGNATURES)
    return False


def write_recovery_marker(app_data_dir: Path | str, *, isolated_path: Path | str, reason: str) -> Path:
    """Record that a store was isolated, pending the user's recover-vs-discard decision.

    Written on the cold-start recovery path (option B), IMMEDIATELY after isolate
    (before any reseed), so the isolated location is recorded on every path.

    ACCRUES, never clobbers (adversarial MED, run_a456640f): a SECOND corruption
    before the first is resolved must NOT overwrite the first marker — that would
    orphan the first isolated `.corrupt-<ts>` file (data preserved on disk but never
    surfaced). So the marker holds a LIST of pending entries; a new isolate appends,
    and entries whose isolated file no longer exists (already resolved) are pruned on
    write. `read_recovery_marker` returns the MOST RECENT pending entry for back-compat
    (the shape stays a flat dict), plus a `pending` list for callers that surface all.

    Best-effort: a marker-write failure must never block boot (caller wraps it).
    """
    marker = Path(app_data_dir) / RECOVERY_MARKER_NAME
    entry = {
        "isolated_path": str(isolated_path),
        "reason": reason,
        "isolated_at": datetime.now().isoformat(),
    }
    # Load any existing pending entries; prune resolved ones (isolated file gone).
    pending: list[dict] = []
    if marker.exists():
        try:
            prior = json.loads(marker.read_text(encoding="utf-8"))
            prior_list = prior.get("pending") if isinstance(prior, dict) else None
            if not prior_list and isinstance(prior, dict) and prior.get("isolated_path"):
                prior_list = [prior]  # migrate a legacy flat marker into the list
            for e in (prior_list or []):
                ip = e.get("isolated_path", "")
                # keep only still-pending prior isolates (file present, not a sentinel)
                if ip and ip != "<already-absent>" and Path(ip).exists():
                    pending.append(e)
        except Exception as exc:
            logger.warning("data_safety: unreadable prior recovery marker (overwriting): %s", exc)
    pending.append(entry)
    # Flat fields mirror the MOST RECENT entry (back-compat); `pending` is the full set.
    payload = {**entry, "pending": pending}
    marker.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return marker


def read_recovery_marker(app_data_dir: Path | str) -> dict | None:
    """Read the pending-recovery marker, or None if absent. FAIL-SOFT: a garbage
    marker returns None (never crash the session-open / attention scan).

    Returns a flat dict (most-recent entry's fields) that ALSO carries a `pending`
    list of all unresolved isolate entries. Back-compat: callers reading
    `isolated_path`/`reason` see the newest; callers surfacing every pending
    recovery iterate `pending`."""
    marker = Path(app_data_dir) / RECOVERY_MARKER_NAME
    if not marker.exists():
        return None
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
        if isinstance(data, dict) and "pending" not in data and data.get("isolated_path"):
            data["pending"] = [dict(data)]  # legacy flat marker → expose a pending list
        return data
    except Exception as exc:
        logger.warning("data_safety: unreadable recovery marker %s: %s", marker, exc)
        return None


def clear_recovery_marker(app_data_dir: Path | str) -> None:
    """Remove the pending-recovery marker once the user has decided. Idempotent."""
    Path(Path(app_data_dir) / RECOVERY_MARKER_NAME).unlink(missing_ok=True)


class StoreClass(str, Enum):
    """Durability class of a data store — decides whether destruction is gated."""

    IRREPLACEABLE = "irreplaceable"  # user data that cannot be rebuilt → gate + isolate
    RECOVERABLE = "recoverable"      # recoverable via recall (archives) → isolate, softer
    REPLACEABLE = "replaceable"      # index/cache/tmp — rebuildable from source → pass


class DestructionBlocked(Exception):
    """Raised when a destructive op is NOT authorized.

    On raise, the target has ALREADY been isolated (renamed) — so the data is
    preserved, not lost. The caller MUST treat this as "do not proceed with the
    destroy"; it is the safe outcome, not an error to swallow.
    """


class IsolationError(Exception):
    """Raised when isolate_store() COULD NOT preserve the store (rename failed and
    no recovery was possible — e.g. a read-only filesystem or a Windows open handle).

    This is DISTINCT from `isolate_store` returning normally: it means the store is
    STILL AT ITS ORIGINAL PATH, untouched. The boot caller MUST let this propagate
    (a bounded launchd restart with the data intact) and MUST NOT proceed to reseed —
    reseeding over a store that was never moved away would be a 2nd data-wipe
    (STEERING #20). A cross-device rename (EXDEV) is NOT this error: it is recovered
    via shutil.move (still preserves), so isolate_store returns normally there.
    """


# --- classification tables (declarative; edit here, not in the guard logic) ---
#
# ⚠️ ORDER OF PRECEDENCE (adversarial HIGH, run_a456640f): a GOVERNED STORE DIR
# dominates a basename hint. A file living under .context / Projects / Knowledge/
# Library is IRREPLACEABLE no matter what its basename contains — otherwise
# `.context/knowledge_fts_notes.md` (basename has "_fts") would misclassify
# REPLACEABLE and skip BOTH the Bash gate AND guard_destructive's isolation (a
# fail-OPEN in the one authority both layers trust). So the dir check runs FIRST,
# and the REPLACEABLE markers are ANCHORED (exact/suffix), never loose substrings.

# IRREPLACEABLE governed-store DIR markers (matched as PATH SEGMENTS, highest
# precedence) — user data with no rebuild path.
_IRREPLACEABLE_DIR_SEGMENTS = (".context", "projects")  # single-segment markers
# adjacency pair: Knowledge/Library (a store), not any stray knowledge+library
_IRREPLACEABLE_DIR_PAIRS = (("knowledge", "library"),)
_IRREPLACEABLE_NAME_EXACT = ("data.db",)

# REPLACEABLE: rebuildable from a source of truth → never gated. ANCHORED, not
# substring: an exact basename or a real suffix, so a store file whose name merely
# CONTAINS one of these tokens is NOT downgraded.
_REPLACEABLE_SUFFIXES = (".tmp", ".lock", ".cache")
_REPLACEABLE_NAME_EXACT = ("code_intel.db", "l1_system_prompts.md", "l1_system_prompts.json")
_REPLACEABLE_NAME_SUFFIXES = ("_fts.db", "_fts")  # FTS5 index tables (knowledge_fts, transcript_fts)

# RECOVERABLE: archived cognition — recall can still reach it; isolate but softer.
_RECOVERABLE_NAME_MARKERS = ("-archive",)


def classify_store(target: Path | str) -> StoreClass:
    """Classify a path's durability. FAIL-CLOSED: unknown → IRREPLACEABLE.

    PRECEDENCE (do NOT reorder — adversarial HIGH run_a456640f):
      1. Governed-store DIR (path segment .context / projects / Knowledge-Library)
         → IRREPLACEABLE. A store dir dominates any basename hint, so a REPLACEABLE-
         looking name inside a governed tree can never be downgraded to ungated.
      2. Exact IRREPLACEABLE basename (data.db).
      3. ANCHORED REPLACEABLE (suffix/exact basename) → REPLACEABLE (rebuildable
         index/cache/tmp; never gated, no over-ask). Anchored, not substring.
      4. RECOVERABLE archive basename.
      5. Fail-closed default: unknown → IRREPLACEABLE (ask before destroying).
    """
    p = Path(target)
    name = p.name.lower()
    segs = str(p).lower().replace("\\", "/").split("/")

    # 1. Governed-store DIR wins outright (segment match, not substring).
    if any(m in segs for m in _IRREPLACEABLE_DIR_SEGMENTS):
        return StoreClass.IRREPLACEABLE
    for a, b in _IRREPLACEABLE_DIR_PAIRS:
        if a in segs and (segs.index(a) + 1 < len(segs)) and segs[segs.index(a) + 1] == b:
            return StoreClass.IRREPLACEABLE

    # 2. Exact irreplaceable basename.
    if name in _IRREPLACEABLE_NAME_EXACT:
        return StoreClass.IRREPLACEABLE

    # 3. ANCHORED REPLACEABLE (rebuildable) — suffix or exact basename only.
    if p.suffix.lower() in _REPLACEABLE_SUFFIXES:
        return StoreClass.REPLACEABLE
    if name in _REPLACEABLE_NAME_EXACT:
        return StoreClass.REPLACEABLE
    if any(name.endswith(s) for s in _REPLACEABLE_NAME_SUFFIXES):
        return StoreClass.REPLACEABLE

    # 4. RECOVERABLE archive.
    if any(m in name for m in _RECOVERABLE_NAME_MARKERS):
        return StoreClass.RECOVERABLE

    # 5. Fail-closed default: unknown → IRREPLACEABLE (ask before destroying).
    return StoreClass.IRREPLACEABLE


def _sidecars(target: Path) -> list[Path]:
    """SQLite -wal/-shm sidecars that MUST move with a DB so no orphan WAL is left
    beside a fresh file (foreign-WAL-replay re-corruption, COE run_2d3417d9)."""
    return [Path(str(target) + suffix) for suffix in ("-wal", "-shm")]


def isolate_store(target: Path | str) -> Path:
    """PRESERVE the store by RENAMING it (never deleting). Returns the isolated path.

    Renames the target AND its -wal/-shm sidecars together to `<name>.corrupt-<ts>`
    so no orphan sidecar is left behind. Sync-fast (<1ms) — safe to call inline on
    an event loop; never uses a thread pool. This is the ONLY action the guard takes
    WITHOUT approval, precisely because it is non-destructive.

    ⚠️ This function must NEVER unlink / os.replace-over the original. That reversal
    is what turns test_ac1_* RED (the mutation guard).
    """
    target = Path(target)
    # A deterministic-enough, collision-resistant stamp (uuid tail avoids clobber if
    # two isolations land in the same second).
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:6]
    isolated = target.with_name(f"{target.name}.corrupt-{stamp}")
    # rename() can raise on a read-only fs, a Windows open handle, or a cross-device
    # move (EXDEV). A bare rename that propagated would crash the boot caller
    # (main.py:_purge_corrupt_db → lifespan) into a KeepAlive crash-loop. Handle it:
    #   - EXDEV → the destination is on another filesystem; shutil.move copies+unlinks
    #     across devices, still PRESERVING the data (returns normally).
    #   - any other OSError (read-only fs, held handle) → we CANNOT preserve here;
    #     raise IsolationError so the boot caller skips reseed + re-raises (the store
    #     is untouched at its original path — a bounded restart, never a 2nd wipe).
    def _relocate(src: Path, dst: Path) -> None:
        try:
            src.rename(dst)
        except OSError as exc:
            if exc.errno == errno.EXDEV:
                # cross-device: shutil.move copies+unlinks, still PRESERVING the data.
                # But the copy itself can fail mid-way (ENOSPC on a full dest, EROFS,
                # a shutil.Error) — that failure MUST surface as IsolationError too,
                # never a bare OSError: the boot caller relies on IsolationError to skip
                # reseed (else it would os.replace over the un-moved live store = 2nd
                # wipe), and guard_destructive relies on it to raise DestructionBlocked.
                try:
                    shutil.move(str(src), str(dst))
                except (OSError, shutil.Error) as move_exc:
                    raise IsolationError(
                        f"could not isolate {src} → {dst} (cross-device copy failed): "
                        f"{type(move_exc).__name__}: {move_exc} "
                        f"(store left in place, NOT destroyed)"
                    ) from move_exc
            else:
                raise IsolationError(
                    f"could not isolate {src} → {dst}: {type(exc).__name__}: {exc} "
                    f"(store left in place, NOT destroyed)"
                ) from exc

    _relocate(target, isolated)
    for sidecar in _sidecars(target):
        if sidecar.exists():
            sidecar_dst = isolated.with_name(isolated.name + sidecar.name[len(target.name):])
            # A sidecar (-wal/-shm) is reconstructable; never fail the whole isolation
            # (primary data already preserved) on a sidecar relocation hiccup.
            # BUT: the primary is now gone from `target`, so a sidecar LEFT BEHIND is
            # an orphan WAL beside where reseed will write a FRESH data.db → SQLite
            # replays the foreign WAL → re-corruption (COE run_2d3417d9, the exact
            # failure this run exists to prevent). So if relocation fails, DELETE the
            # orphan (it is reconstructable and now references a db that no longer
            # exists at this path) — never leave it.
            try:
                _relocate(sidecar, sidecar_dst)
            except IsolationError as _sc_exc:
                logger.warning(
                    "data_safety: sidecar not relocated (%s); deleting orphan to prevent "
                    "foreign-WAL-replay re-corruption", _sc_exc,
                )
                try:
                    sidecar.unlink()
                except OSError as _rm_exc:
                    logger.error(
                        "data_safety: orphan sidecar %s could NOT be removed (%s) — "
                        "reseed target may replay foreign WAL", sidecar, _rm_exc,
                    )
    logger.warning(
        "data_safety: ISOLATED %s -> %s (preserved, not destroyed; awaiting authorization)",
        target, isolated,
    )
    return isolated


async def guard_destructive(
    target: Path | str,
    action: str,
    reason: str,
    *,
    session_id: str | None = None,
    cold_start: bool = False,
) -> None:
    """Pre-action authorization chokepoint for destroying an irreplaceable store.

    Call this BEFORE any whole-store destroy (unlink data.db, rmtree a DDD project,
    overwrite a live context file). On return, the caller is authorized to proceed.
    On DestructionBlocked, the caller MUST NOT proceed — the target has been isolated
    (data preserved) and the destroy is not authorized.

    Args:
        target: the store about to be destroyed.
        action: short verb for the approval prompt ("purge" / "delete" / "overwrite").
        reason: why the destroy was proposed (surfaced to the user).
        session_id: the initiating chat session (routes the approval). None → degraded.
        cold_start: True on the boot/lifespan path → NEVER await approval.

    Raises:
        DestructionBlocked: not authorized (denied / timed out / no session / cold-start).
                            Target already isolated → data preserved.
    """
    kind = classify_store(target)
    if kind == StoreClass.REPLACEABLE:
        # Rebuildable (index/cache/tmp) — not gated, zero overhead, no isolation.
        return

    # PRESERVE first: isolate before anything can go wrong (and before any await).
    # If isolation itself CANNOT preserve (read-only fs / held handle), that is still
    # a "do not destroy" outcome — surface it as DestructionBlocked (never let the
    # caller proceed to destroy an un-preservable store). EXDEV is already recovered
    # inside isolate_store (shutil.move), so it does not reach here.
    try:
        isolate_store(target)
    except IsolationError as exc:
        logger.error(
            "data_safety: destroy of %s (%s) BLOCKED — isolation failed, store left "
            "in place (NOT destroyed): %s", target, action, exc,
        )
        raise DestructionBlocked(
            f"{action} of {target} blocked: could not isolate ({exc}); store preserved in place"
        ) from exc

    # DEGRADED branch — no live session or a cold boot: never await, never enqueue.
    if cold_start or session_id is None:
        logger.warning(
            "data_safety: destroy of %s (%s) BLOCKED — %s; isolated + degraded, "
            "NOT destroyed. Reason: %s",
            target, action,
            "cold-start (no session to authorize)" if cold_start else "no active session",
            reason,
        )
        raise DestructionBlocked(
            f"{action} of {target} blocked: "
            f"{'cold-start' if cold_start else 'no active session'} — isolated, not destroyed"
        )

    # PRE-ACTION approval — reuse the existing PermissionManager flow + SSE event.
    request_id = f"destroy_{uuid4().hex[:12]}"
    permission_manager.store_pending_request({
        "id": request_id,
        "session_id": session_id,
        "tool_name": "destructive_data",
        "kind": "destructive_data",  # additive field — UI may surface more prominently
        "tool_input": f"{action}: {target}",
        "reason": reason,
        "status": "pending",
        "created_at": datetime.now().isoformat(),
    })
    await permission_manager.enqueue_permission_request(session_id, {
        "sessionId": session_id,
        "requestId": request_id,
        "toolName": "destructive_data",
        "kind": "destructive_data",
        "toolInput": {"action": action, "target": str(target), "reason": reason},
        "reason": reason,
        "options": ["approve", "deny"],
    })
    logger.warning(
        "data_safety: destroy of %s (%s) AWAITING approval in session %s (request %s)",
        target, action, session_id, request_id,
    )
    decision = await permission_manager.wait_for_permission_decision(request_id)
    permission_manager.remove_pending_request(request_id)

    if decision != "approve":
        raise DestructionBlocked(
            f"{action} of {target} blocked: user decision={decision} — isolated, not destroyed"
        )
    # Approved: the target has already been isolated; the caller proceeds with its
    # own destroy/recreate. (The isolated copy remains as a recoverable backup.)
