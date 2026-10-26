"""turn_changes — what one chat turn changed in the code repos.

The chat's "N files changed" card (KiroCrew style: per-file +/- and an expandable
diff). Built from the same git primitive the pipeline's run worktrees use
(core/run_worktree.change_tree): a CHANGE TREE written from a private copy of the
index (``git add -u`` + ``write-tree`` under ``GIT_INDEX_FILE``), which never
touches the working tree, the real index or any ref.

Flow (one ``TurnChanges`` per turn, owned by the SessionUnit):

- ``TurnChanges.start()`` — at send() admission, snapshot the change tree of every
  code tree (bound-repo checkouts + live run worktrees: the trees
  needs_human_review classifies as ``source``). One job per tree on the dedicated
  ``turn_changes`` pool (core/executors — never the default pool, never the
  ``subprocess`` pool the fail-open safety gates wait on), while the model starts
  thinking; ~0.2s per repo, done long before its first tool call writes. A tree
  whose snapshot timed out (a dead mount) is skipped for ``_COOL_OFF_S``.
- ``note(event)`` / ``note_tool(block)`` — the router feeds every ``file_changed`` of
  the turn and every Edit/Write tool path (a dot-directory file such as
  ``.github/…`` has no ``file_changed``). Bash and interpreter writes are in the
  ``file_changed`` stream (streaming_orchestrator emits them).
- ``finish()`` — at ``result``: for each tree the turn touched, snapshot again (plus
  its new untracked files) and diff the two trees limited to the touched files, so
  another session's edits elsewhere in the repo never show. A tree that went live
  during the turn (a run worktree the turn created) diffs from its HEAD — the run's
  base. A tree whose start snapshot failed is left off (diffing it from HEAD would
  show earlier uncommitted work as this turn's), and so is any other tree. Files
  outside the trees (SwarmWS knowledge and content) are the turn's deliverables,
  not code changes. Bookkeeping paths inside a tree (``Knowledge/DailyActivity/``,
  ``.context/``, ``.aidlc/deliverables/``) are left off too (``_excluded``).

The block carries counts and the two tree ids, not the diff text: the card fetches
one file's diff on demand (``diff_text``, GET /api/chat/turn-diff), so diff text
never lands in the messages table — its FTS index and session recall read message
content into prompts. The trees are unreferenced objects in the repo's object
store; git gc prunes them after its grace period (two weeks by default), after
which a row still shows its counts but its diff is gone.

Content-based: a commit made during the turn does not hide its changes. Known
limit: an untracked file that existed before the turn and was rewritten shows as
added. A display aid: every failure degrades to "no card" and never holds the
turn — ``finish`` is bounded by ``FINISH_BUDGET_S`` (a hang guard for a git call
stuck on a dead network mount; on expiry the result goes out with the trees diffed
so far).

Key public symbols: ``TurnChanges``, ``diff_text``, ``diff_trees``, ``allowed_repo``, ``code_roots``,
``MAX_FILES``, ``MAX_DIFF_LINES``.
"""
from __future__ import annotations

import asyncio
import copy
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Iterable, Optional

logger = logging.getLogger(__name__)

# Rows on the card; beyond: the block says how many were left out.
MAX_FILES = 60
# A file with more changed lines than this is "file only" (its diff is not offered);
# the on-demand diff is refused beyond MAX_DIFF_CHARS whatever the row says.
MAX_DIFF_LINES = 4000
MAX_DIFF_CHARS = 1_000_000
# Hang guards only (a git call on a dead network mount never returns). Per call, and
# for the whole of finish(); on expiry that tree / the card is left out.
GIT_TIMEOUT_S = 30.0
FINISH_BUDGET_S = 10.0
# Landings expanded on one card (core/session_landings, ~9 git calls each); a turn
# with more says so (landed_truncated) and Canvas lists them.
MAX_LANDINGS = 4
# The tools whose input names the file they write (dot paths have no file_changed).
WRITE_TOOLS = frozenset({"Edit", "Write", "MultiEdit", "NotebookEdit"})

# Bookkeeping a turn writes alongside its work, never a code change of its own: the
# session log, context files, and the run's committed copies of its documents (the
# documents themselves are the turn's deliverables). Matched at the ROOT of the
# repo-relative path only: a deeper `src/.context/x.py` is code and stays on the card
# (matching at any depth would let a change hide from it).
_EXCLUDED_PREFIXES = ("Knowledge/DailyActivity/", ".context/", ".aidlc/deliverables/")


def _excluded(rel: str) -> bool:
    """A repo-relative path the changes card leaves off (chat turn spec v6 §7.4)."""
    rel = rel.replace(os.sep, "/")
    return any(rel.startswith(p) for p in _EXCLUDED_PREFIXES)
_SHA_RE = re.compile(r"^[0-9a-f]{40}([0-9a-f]{24})?$")


def _git(cwd: str, *args: str, env: Optional[dict] = None, ok: Iterable[int] = (0,),
         stdin: Optional[bytes] = None) -> str:
    # Literal pathspecs: a Next.js route file `[id].tsx` is a glob otherwise and
    # would pull another file's hunks into its row. GIT_OPTIONAL_LOCKS=0: these reads
    # run at every turn end and must never take the checkout's index.lock (a status /
    # diff refresh would, and a concurrent commit there would then fail).
    r = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, timeout=GIT_TIMEOUT_S, input=stdin,
        env={**os.environ, "GIT_LITERAL_PATHSPECS": "1", "GIT_OPTIONAL_LOCKS": "0",
             **(env or {})},
    )
    if r.returncode not in tuple(ok):
        raise RuntimeError(f"git {args[0]} failed ({r.returncode}): {r.stderr.decode('utf-8', 'replace')[:200]}")
    return r.stdout.decode("utf-8", "replace")


def _tree(top: str, add: Iterable[str] = ()) -> str:
    """The change tree of repo *top*: a private index copy, ``add -u`` (+ *add*), write-tree."""
    index = _git(top, "rev-parse", "--git-path", "index").strip()
    index_path = index if os.path.isabs(index) else os.path.join(top, index)
    # A private DIRECTORY: with split-index on, git writes sharedindex.* next to the
    # index file it is given; removing the directory removes those too.
    tmpdir = tempfile.mkdtemp(prefix="swarm-turn-idx-")
    tmp = os.path.join(tmpdir, "index")
    try:
        if os.path.exists(index_path):
            # copy2 keeps the mtime: git's racy-entry check compares entry mtimes with
            # the index file's, and a fresh mtime would hide a same-second edit.
            shutil.copy2(index_path, tmp)
        env = {"GIT_INDEX_FILE": tmp}
        _git(top, "add", "-u", env=env)
        new = [r for r in add if os.path.lexists(os.path.join(top, r))]
        if new:
            # One call for all new files (a scaffold turn writes hundreds); an ignored
            # path is skipped (exit 1). A path inside a submodule aborts the whole call
            # (128) — then one at a time, skipping the refused ones.
            listed = "\0".join(new).encode("utf-8", "surrogateescape")
            try:
                _git(top, "add", "--ignore-errors", "--pathspec-from-file=-", "--pathspec-file-nul",
                     env=env, ok=(0, 1), stdin=listed)
            except RuntimeError:
                for rel in new:
                    _git(top, "add", "--", rel, env=env, ok=(0, 1, 128))
        return _git(top, "write-tree", env=env).strip()
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def _toplevel(path: str) -> Optional[str]:
    d = path if os.path.isdir(path) else os.path.dirname(path)
    while d and not os.path.isdir(d):
        d = os.path.dirname(d)
    if not d:
        return None
    try:
        top = _git(d, "rev-parse", "--show-toplevel").strip()
    except (RuntimeError, OSError, subprocess.SubprocessError):
        return None
    return os.path.realpath(top) if top else None


def _main_checkout(top: str) -> str:
    """The checkout whose object store holds *top*'s objects (a worktree → its main)."""
    try:
        common = _git(top, "rev-parse", "--path-format=absolute", "--git-common-dir").strip()
    except (RuntimeError, OSError, subprocess.SubprocessError):
        return top
    return os.path.realpath(os.path.dirname(common)) if os.path.basename(common) == ".git" else top


def _parse_numstat(out: str) -> dict[str, tuple[Optional[int], Optional[int]]]:
    """``--numstat -z`` → {path: (added, deleted)}; None for a binary file."""
    stats: dict[str, tuple[Optional[int], Optional[int]]] = {}
    for rec in out.split("\0"):
        parts = rec.split("\t", 2) if rec else []
        if len(parts) == 3:
            a, d, p = parts
            stats[p] = (None, None) if a == "-" else (int(a), int(d))
    return stats


def _parse_name_status(out: str) -> dict[str, str]:
    items = [x for x in out.split("\0") if x]
    return {items[i + 1]: {"A": "added", "D": "deleted"}.get(items[i][:1], "modified")
            for i in range(0, len(items) - 1, 2)}


def code_roots() -> list[str]:
    """Every code tree a turn may write: bound checkouts + live run worktrees."""
    try:
        from core.needs_human_review import _worktree_roots
        from core.project_registry import get_swarmws
        return [os.path.realpath(p) for p, _repo in _worktree_roots(str(get_swarmws())) if Path(p).is_dir()]
    except Exception as exc:  # noqa: BLE001 — no roots = no card
        logger.debug("turn_changes: code roots unavailable: %s", exc)
        return []


def session_trees(session_id: Optional[str]) -> list[tuple[str, str]]:
    """This session's own worktrees (``scripts/session_worktree`` create:
    ``refs/heads/session/<key>-…``) in the bound repos, as (realpath, branch): code
    trees of its turns (design 2026-10-26-turn-cards-unified.md §3.4). One
    ``git worktree list`` per bound repo; blocking, run it in a pool."""
    from core.app_session_id import session_key, valid_app_session_id
    if not valid_app_session_id(session_id):
        return []
    try:
        from core.session_landings import _bound_checkouts
        repos = _bound_checkouts()
    except Exception as exc:  # noqa: BLE001 — no repos: no session trees
        logger.debug("turn_changes: bound repos unavailable: %s", exc)
        return []
    prefix = f"refs/heads/session/{session_key(session_id)}-"
    try:
        from scripts.session_worktree import root as _sw_root
        sw_root = os.path.realpath(_sw_root())
    except Exception as exc:  # noqa: BLE001 — no root known: no detached worktree is ours
        logger.debug("turn_changes: session-worktree root unavailable: %s", exc)
        sw_root = None
    out: list[tuple[str, str]] = []
    for repo in repos:
        try:
            listing = _git(repo, "worktree", "list", "--porcelain")
        except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
            logger.debug("turn_changes: worktree list of %s failed: %s", repo, exc)
            continue
        ours: Optional[set[str]] = None      # this session's branches, read once if needed
        path = None
        for line in listing.splitlines():
            if line.startswith("worktree "):
                path = line[len("worktree "):]
            elif line.startswith("branch ") and path and line[len("branch "):].startswith(prefix):
                if os.path.isdir(path):
                    out.append((os.path.realpath(path), line[len("branch refs/heads/"):]))
            elif line == "detached" and path and sw_root and os.path.isdir(path):
                # create names it <root>/<name>/<repo> on session/<key>-<name> (§3.6):
                # detached, it is ours when that branch exists for its <name>
                real = os.path.realpath(path)
                parts = os.path.relpath(real, sw_root).split(os.sep)
                if len(parts) != 2 or parts[0] in ("..", "."):
                    continue
                if ours is None:
                    try:
                        # for-each-ref matches whole path segments: list session/, filter
                        ours = {r for r in _git(repo, "for-each-ref", "--format=%(refname)",
                                                "refs/heads/session/").split() if r.startswith(prefix)}
                    except (RuntimeError, OSError, subprocess.SubprocessError):
                        ours = set()
                if prefix + parts[0] in ours:
                    out.append((real, (prefix + parts[0])[len("refs/heads/"):]))
    return out


def _created_tree(top: str, branch: str) -> Optional[str]:
    """The tree of the commit *branch* was created at, or None (see _created_commit)."""
    commit = _created_commit(top, branch)
    return _tree_of(top, commit) if commit else None


def _tree_of(top: str, commit: str) -> Optional[str]:
    try:
        return _git(top, "rev-parse", f"{commit}^{{tree}}").strip() or None
    except (RuntimeError, OSError, subprocess.SubprocessError):
        return None


def _created_commit(top: str, branch: str) -> Optional[str]:
    """The commit *branch* was created at (its reflog's ``branch: Created from``
    entry, read through git), or None: then the tree is left off, never diffed from
    a guess."""
    try:
        raw = _git(top, "reflog", "show", "--format=%H%x00%gs", f"refs/heads/{branch}", "--")
    except (RuntimeError, OSError, subprocess.SubprocessError):
        return None
    created = [x.split("\0", 1)[0] for x in raw.splitlines()
               if "\0" in x and x.split("\0", 1)[1].startswith("branch: Created from")]
    return created[-1] if created else None


# A fixed identity for the throwaway commit _rebased_start makes of a start tree
# (unreferenced; gc removes it). Never the user's.
_REPLAY_ID = {"GIT_AUTHOR_NAME": "swarm-turn", "GIT_AUTHOR_EMAIL": "turn@swarm.invalid",
              "GIT_COMMITTER_NAME": "swarm-turn", "GIT_COMMITTER_EMAIL": "turn@swarm.invalid",
              "GIT_AUTHOR_DATE": "@0 +0000", "GIT_COMMITTER_DATE": "@0 +0000"}


def _rebased_start(top: str, start: str, head_s: str, base_s: str, base_e: str) -> Optional[str]:
    """The turn-start tree *start* (HEAD *head_s* then) moved from merge-base *base_s*
    onto *base_e* (design 2026-10-26-turn-cards-unified.md §3.6): *start* itself when
    the base did not move, the replayed tree when it did, None when the replay
    conflicts or git cannot do it (the tree is then left off, never diffed S..E)."""
    if base_s == base_e:
        return start
    try:
        commit = _git(top, "commit-tree", start, "-p", head_s, "-m", "turn start",
                      env=_REPLAY_ID).strip()
        out = _git(top, "merge-tree", "--write-tree", f"--merge-base={base_s}", base_e, commit)
    except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
        logger.info("turn_changes: start of %s not replayable onto the new base (%s) — left off", top, exc)
        return None
    return out.splitlines()[0].strip() if out.strip() else None


def _start_on_base(top: str, start: str, head_s: Optional[str]) -> Optional[str]:
    """_rebased_start for a session tree: the bases are its merge-bases with the main
    checkout's branch (where ``land`` lands it) at the start and now. Unknown head or
    base: *start*, as before (nothing to correct with)."""
    if not head_s:
        return start
    try:
        ref = _git(_main_checkout(top), "symbolic-ref", "-q", "HEAD").strip()
        head_e = _git(top, "rev-parse", "HEAD").strip()
        base_s = _git(top, "merge-base", head_s, ref).strip()
        base_e = _git(top, "merge-base", head_e, ref).strip()
    except (RuntimeError, OSError, subprocess.SubprocessError):
        return start
    return _rebased_start(top, start, head_s, base_s, base_e)


# Start snapshots per send are bounded: past this many trees the rest are not
# snapshotted, and their edits are left off the card (never diffed from a guess).
MAX_START_SNAPSHOTS = 32


def _pool(name: str = "turn_changes"):
    """A dedicated pool (core/executors): "turn_snapshot" for start snapshots,
    "turn_changes" for the rest — never the default pool, never the 'subprocess'
    pool the fail-open safety gates wait on."""
    from core import executors
    return executors.get_pool(name)


# A root whose snapshot timed out (a dead network mount) is skipped for a while, so
# it does not tie up a worker on every send.
_COOL_OFF_S = 600.0
_cool_off: dict[str, float] = {}


def _cooling(root: str) -> bool:
    return _cool_off.get(root, 0.0) > time.monotonic()


def _head(top: str) -> Optional[str]:
    try:
        return _git(top, "rev-parse", "-q", "--verify", "HEAD^{commit}").strip() or None
    except (RuntimeError, OSError, subprocess.SubprocessError):
        return None


def _snapshot_root(root: str) -> tuple[str, str, float, Optional[str]]:
    """(toplevel, start tree, monotonic time it was taken) of one code tree. A job that ran as long as a git
    timeout — whatever it raised: _toplevel turns a timeout into "not a tree" —
    puts the root in cool-off."""
    if _cooling(root):
        raise RuntimeError("cooling off after a timeout")
    began = time.monotonic()
    try:
        top = _toplevel(root)
        if not top:
            raise RuntimeError("not a git tree")
        head = _head(top)
        return top, _tree(top), time.monotonic(), head
    except Exception:
        if time.monotonic() - began >= GIT_TIMEOUT_S * 0.9:
            _cool_off[root] = time.monotonic() + _COOL_OFF_S
        raise


def _retrieve(fut: "asyncio.Future") -> None:
    """A job nobody awaits (the turn did not touch that repo) must not log
    'exception was never retrieved' every turn."""
    if not fut.cancelled() and fut.exception() is not None:
        logger.debug("turn_changes: start snapshot failed: %s", fut.exception())


class TurnChanges:
    """One turn's code changes: a start snapshot per tree + the files the turn wrote."""

    def __init__(self, roots: Optional[Iterable[str]] = None, *,
                 session_id: Optional[str] = None, turn_id: Optional[str] = None,
                 context: Optional[dict] = None):
        # None: discover the trees in the pool (code_roots reads run.json files).
        self._given = None if roots is None else [os.path.realpath(r) for r in roots]
        # This turn's landings are read by (session, turn id) from the stamp
        # session_worktree land writes (core/session_landings); no turn id, none.
        # *context* is the session's hook context: run_owner_gate marks there that the
        # turn ran a session_worktree command (``_turn_sw``); none, no read.
        self._session_id, self._turn_id, self._context = session_id, turn_id, context
        # The card, computed once per turn: result and Stop may both finish it.
        self._result: Optional[asyncio.Future] = None
        self._jobs: dict[str, asyncio.Future] = {}      # root -> (toplevel, start tree)
        self._discovery: Optional[asyncio.Future] = None
        self._raw: dict[str, str] = {}                  # path as noted -> last operation
        self._partial: list[dict] = []
        self._skipped: set[str] = set()                 # live at the start, not snapshotted
        self._session_roots: set[str] = set()           # this session's worktrees at the start
        # When the turn's first tool call was seen: a start snapshot taken after it
        # may already hold the turn's own edit, so that tree is left off.
        self._first_tool_at: Optional[float] = None

    @classmethod
    def start(cls, roots: Optional[Iterable[str]] = None, *, session_id: Optional[str] = None,
              turn_id: Optional[str] = None, context: Optional[dict] = None) -> "TurnChanges":
        """Begin a turn: discover the trees, then snapshot each in its own pool job."""
        tc = cls(roots, session_id=session_id, turn_id=turn_id, context=context)
        loop = asyncio.get_running_loop()
        pool = _pool("turn_snapshot")

        async def kick() -> None:
            if tc._given is not None:
                found = tc._given
            else:
                found = await loop.run_in_executor(pool, code_roots)
                own = await loop.run_in_executor(pool, session_trees, tc._session_id)
                tc._session_roots = {p for p, _b in own}
                found = [*found, *(p for p, _b in own if p not in found)]
            tc._skipped = set(found[MAX_START_SNAPSHOTS:])
            if tc._skipped:
                logger.warning("turn_changes: %d code trees not snapshotted (cap %d)", len(tc._skipped), MAX_START_SNAPSHOTS)
            for root in found[:MAX_START_SNAPSHOTS]:
                fut = loop.run_in_executor(pool, _snapshot_root, root)
                fut.add_done_callback(_retrieve)
                tc._jobs[root] = fut

        tc._discovery = asyncio.ensure_future(kick())
        tc._discovery.add_done_callback(_retrieve)
        return tc

    async def ready(self) -> None:
        """Wait for every start snapshot (tests; finish() waits only for what it needs)."""
        if self._discovery is not None:
            await self._discovery
        for fut in list(self._jobs.values()):
            try:
                await fut
            except Exception:  # noqa: BLE001 — finish() reports a failed start
                logger.debug("ready: suppressed exception on a fail-safe path", exc_info=True)
                pass

    def note(self, event: dict) -> None:
        """Remember a ``file_changed`` event's file (latest operation wins)."""
        path = event.get("absolutePath") or event.get("path")
        if isinstance(path, str) and os.path.isabs(path):
            self._raw[path] = str(event.get("operation") or "written")

    def note_tool(self, block: dict) -> None:
        """Remember the file an Edit/Write tool_use names, and when the turn's first
        tool call (any tool: a Bash write is only seen after it ran) went out."""
        if not isinstance(block, dict) or block.get("type") != "tool_use":
            return
        if self._first_tool_at is None:
            self._first_tool_at = time.monotonic()
        if block.get("name") not in WRITE_TOOLS:
            return
        inp = block.get("input") if isinstance(block.get("input"), dict) else {}
        path = inp.get("file_path") or inp.get("notebook_path")
        if isinstance(path, str) and os.path.isabs(path):
            self._raw.setdefault(path, "written")

    async def finish(self) -> Optional[dict]:
        """The ``turn_changes`` block for this turn, or None when nothing changed.
        Computed once: every call of the turn (``result`` and Stop may both come)
        awaits the same task and gets its own copy, so a second call never sees a
        card without the in-place rows the first one consumed. The task is shielded:
        a cancelled caller does not cancel it. Bounded by FINISH_BUDGET_S: on expiry
        the trees already diffed are shown, the rest are left off."""
        if self._result is None:
            self._result = asyncio.ensure_future(self._compute())
            self._result.add_done_callback(_retrieve)
        block = await asyncio.shield(self._result)
        return copy.deepcopy(block) if block is not None else None

    async def _compute(self) -> Optional[dict]:
        from core.session_landings import Deadline
        deadline = Deadline(FINISH_BUDGET_S)    # one budget for rows and landings
        raw, self._raw = self._raw, {}
        self._partial = []
        landings: Optional[dict] = None
        if raw:
            try:
                await asyncio.wait_for(self._finish(raw), max(deadline.left(), 0))
            except asyncio.TimeoutError:
                logger.warning("turn_changes: finish exceeded %.0fs — showing %d rows", FINISH_BUDGET_S, len(self._partial))
        if self._session_id and self._turn_id and (
                self._context is None or self._context.get("_turn_sw")):
            landings = await self._landings(deadline)   # no session_worktree: no git
        block = _block(self._partial, landings)
        if block is not None and self._turn_id:
            block["turn_id"] = self._turn_id
        return block

    async def _landings(self, deadline) -> Optional[dict]:
        """This turn's landings (core/session_landings.read_turn: an exact search by
        the turn id), on the turn_changes pool, with what is left of the turn end's
        budget (*deadline*: no git call starts after it)."""
        from core import session_landings
        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(
                _pool(), lambda: session_landings.read_turn(
                    self._session_id, self._turn_id, deadline=deadline,
                    limit=MAX_LANDINGS))
        except Exception as exc:  # noqa: BLE001 — a display aid
            logger.debug("turn_changes: landings unreadable: %s", exc)
            return None

    async def _finish(self, raw: dict[str, str]) -> None:
        loop = asyncio.get_running_loop()
        pool = _pool()
        if self._discovery is not None:
            await self._discovery
        known = sorted([*self._jobs, *self._skipped], key=len, reverse=True)
        by_root, later = await loop.run_in_executor(pool, _group, raw, known,
                                                    self._session_id)
        for root, paths in sorted(by_root.items()):
            if root not in self._jobs:
                logger.info("turn_changes: %s was not snapshotted (cap) — left off", root)
                continue
            try:
                # shield: a timed-out finish must not cancel the shared job
                top, start, taken, head_s = await asyncio.shield(self._jobs[root])
            except Exception as exc:  # noqa: BLE001 — no start: this tree stays off
                logger.info("turn_changes: no start snapshot for %s (%s) — left off", root, exc)
                continue
            if self._first_tool_at is not None and taken > self._first_tool_at:
                logger.info("turn_changes: start snapshot of %s came after the first tool call — left off", root)
                continue
            if root in self._session_roots:
                start = await loop.run_in_executor(pool, _start_on_base, top, start, head_s)
                if start is None:
                    continue
            await self._diff_into(loop, pool, top, paths, start)
        # Trees that went live during the turn: a run worktree from its HEAD, a
        # session worktree from the commit it was created at.
        for top, (paths, start, head_s) in sorted(later.items()):
            if head_s:
                start = await loop.run_in_executor(pool, _start_on_base, top, start, head_s)
                if start is None:
                    continue
            await self._diff_into(loop, pool, top, paths, start)

    async def _diff_into(self, loop, pool, top: str, paths: list[str], start: Optional[str]) -> None:
        rels = sorted({rel for rel in (os.path.relpath(p, top) for p in paths if p.startswith(top + os.sep))
                       if not _excluded(rel)})
        if not rels:
            return
        try:
            self._partial.extend(await loop.run_in_executor(pool, _diff_top, top, rels, start))
        except Exception as exc:  # noqa: BLE001 — leave this tree off the card
            logger.warning("turn_changes: diff of %s failed: %s", top, exc)


def _group(raw: dict[str, str], known: list[str], session_id: Optional[str] = None,
           ) -> tuple[dict[str, list[str]], dict[str, tuple[list[str], Optional[str], Optional[str]]]]:
    """Touched paths (realpath'd, in the pool) by the code tree that was snapshotted
    at the start; paths in a tree that is live NOW but was not then, by toplevel,
    with the start to diff from (None: its HEAD) and, for a session worktree, the
    commit it was created at (to move that start onto a new base, §3.6)."""
    by_root: dict[str, list[str]] = {}
    rest: list[str] = []
    for p in raw:
        path = os.path.realpath(p)
        root = next((r for r in known if path == r or path.startswith(r + os.sep)), None)
        if root:
            by_root.setdefault(root, []).append(path)
        else:
            rest.append(path)
    later: dict[str, tuple[list[str], Optional[str], Optional[str]]] = {}
    if not rest:
        return by_root, later
    wt_home = _run_worktree_home()
    # Only a run worktree the turn CREATED may diff from HEAD (its HEAD is the run's
    # base), and a session worktree it created from the commit it was created at. Any
    # other tree missing at the start (a binding added mid-turn) is left off: HEAD
    # would show earlier uncommitted work as this turn's.
    starts: dict[str, tuple[Optional[str], Optional[str]]] = {}
    for r in code_roots():
        if r not in known and wt_home and r.startswith(wt_home + os.sep):
            starts[r] = (None, None)
    for r, branch in session_trees(session_id):
        if r not in known and r not in starts:
            commit = _created_commit(r, branch)
            created = _tree_of(r, commit) if commit else None
            if created:
                starts[r] = (created, commit)
    trees = sorted(starts, key=len, reverse=True)
    for path in rest:
        root = next((r for r in trees if path.startswith(r + os.sep)), None)
        top = _toplevel(root) if root else None
        if top:
            later.setdefault(top, ([], *starts[root]))[0].append(path)
    return by_root, later


_DIFF = ("diff", "--no-renames", "--no-ext-diff", "--no-textconv")


def _diff_top(top: str, rels: list[str], start: Optional[str]) -> list[dict]:
    tracked = set(_git(top, "ls-files", "-z", "--", *rels).split("\0")) - {""}
    end = _tree(top, add=[r for r in rels if r not in tracked])
    if start is None:
        start = _git(top, "rev-parse", "HEAD^{tree}").strip()
    if start == end:
        return []
    numstat = _parse_numstat(_git(top, *_DIFF, "--numstat", "-z", start, end, "--", *rels))
    status = _parse_name_status(_git(top, *_DIFF, "--name-status", "-z", start, end, "--", *rels))
    repo, git_root = os.path.basename(top), _main_checkout(top)
    out = []
    for rel in rels:
        if rel not in numstat:
            continue   # written, but its content ended where it started
        added, deleted = numstat[rel]
        binary = added is None
        out.append({
            "path": rel, "absolutePath": os.path.join(top, rel), "repo": repo,
            "status": status.get(rel, "modified"), "added": added, "deleted": deleted,
            "binary": binary,
            "diffable": not binary and (added or 0) + (deleted or 0) <= MAX_DIFF_LINES,
            "gitRoot": git_root, "start": start, "end": end,
        })
    return out


def _block(files: list[dict], landings: Optional[dict] = None) -> Optional[dict]:
    """The card: in-place rows sorted by size, then this turn's landings
    (core/session_landings). One row budget, MAX_FILES, spans all of them; each
    group says how many it left out. None when there is nothing to show."""
    landings = landings or {}
    landed = [dict(x) for x in landings.get("landed") or []]
    # a file both edited in place and landed this turn is shown once, under the landing
    under = {(f.get("gitRoot"), f.get("path")) for x in landed for f in x.get("files") or []}
    files = [f for f in files if (f.get("gitRoot"), f.get("path")) not in under]
    files = sorted(files, key=lambda f: -((f["added"] or 0) + (f["deleted"] or 0)))
    unknown = list(landings.get("unknown") or [])
    unverified = int(landings.get("unverified") or 0)
    truncated = bool(landings.get("truncated"))
    if not files and not landed and not unknown and not unverified and not truncated:
        return None
    budget = MAX_FILES
    block: dict = {"type": "turn_changes", "files": files[:budget]}
    if len(files) > budget:
        block["omitted"] = len(files) - budget
    budget -= len(block["files"])
    for land in landed:
        rows = land.get("files") or []
        land["files"] = rows[:max(budget, 0)]
        if len(rows) > len(land["files"]):
            land["omitted"] = len(rows) - len(land["files"])
        budget -= len(land["files"])
    if landings:
        block.update({"landed": landed, "landed_truncated": truncated,
                      "unverified": unverified, "unknown": unknown})
    return block


# ── on-demand diff (GET /api/chat/turn-diff) ────────────────────────────────

def _run_worktree_home() -> Optional[str]:
    try:
        from core.run_worktree import wt_root
        return os.path.realpath(str(wt_root()))
    except Exception:  # noqa: BLE001
        logger.debug("_run_worktree_home: suppressed exception on a fail-safe path", exc_info=True)
        return None


class DiffUnavailable(Exception):
    """A transient failure (a git timeout, an OS error): worth a retry."""


def allowed_repo(git_root: str) -> bool:
    """Only the object store of a known code tree may be read."""
    want = os.path.realpath(git_root)
    roots = [r for r in code_roots() if not _cooling(r)]
    if want in roots:
        return True
    for r in roots:
        top = _toplevel(r)
        if top and (want == top or want == _main_checkout(top)):
            return True
    return False


def _is_file_in(root: str, tree: str, path: str) -> bool:
    out = _git(root, "ls-tree", "-z", tree, "--", path)
    return any(e.split("\t", 1)[-1] == path and " blob " in e.split("\t", 1)[0]
               for e in out.split("\0") if "\t" in e)


def _valid_diff_args(start: str, end: str, path: str) -> bool:
    """Two full tree ids and a safe repo-relative file path."""
    if not (_SHA_RE.fullmatch(start or "") and _SHA_RE.fullmatch(end or "")):
        return False
    parts = Path(path or "").parts
    return not (not path or os.path.isabs(path) or "\0" in path or path.endswith(("/", "\\"))
                or not parts or any(p in (".", "..") for p in parts))


def diff_trees(root: str, start: str, end: str, path: str) -> Optional[str]:
    """One file's unified diff between two trees of *root*, a repo the CALLER has
    authorized (``diff_text`` for the chat card, core/session_changes for the Canvas);
    None if not available (a malformed id, an unsafe path, a path that is not a file
    in either tree, a pruned tree, a diff larger than MAX_DIFF_CHARS). Raises
    DiffUnavailable on a transient failure."""
    if not _valid_diff_args(start, end, path):
        return None
    try:
        # A file in one of the trees only — a directory path would diff a whole
        # subtree, other sessions' edits included.
        if not (_is_file_in(root, start, path) or _is_file_in(root, end, path)):
            return None
        text = _git(root, *_DIFF, "--no-color", "-U3", start, end, "--", path)
    except RuntimeError as exc:          # git refused: a pruned tree, a bad object
        logger.debug("turn_changes: diff for %s unavailable: %s", path, exc)
        return None
    except (OSError, subprocess.SubprocessError) as exc:
        raise DiffUnavailable(str(exc)) from exc
    return text if len(text) <= MAX_DIFF_CHARS else None


def diff_text(git_root: str, start: str, end: str, path: str) -> Optional[str]:
    """One file's unified diff between two change trees for the chat card; None if
    not available (an unknown repo — only a known code tree's object store is read —
    or any case ``diff_trees`` refuses). Raises DiffUnavailable on a transient
    failure."""
    if not _valid_diff_args(start, end, path):
        return None
    if not allowed_repo(git_root):
        return None
    return diff_trees(os.path.realpath(git_root), start, end, path)
