"""shared_checkout — tell the agent when another live session has uncommitted edits in
the repo it is about to work in (design 2026-10-24-swarmai-usable-by-anyone §3.8).

Native work edits the user's checkout in place. When two sessions edit one checkout,
an edit of one lands in the other's commit. So, the first time this session works in a
git repo (a read, a Bash cwd or a write there) while ANOTHER live session has files it
wrote in that repo that git still reports as uncommitted, this hook injects a note
naming them and the command for a session worktree (``scripts/session_worktree.py``).
Inject-only, never a deny, no setting (P3/P4): the agent decides, the user can say
"stay in place".

- "wrote": the other session's ``_files_written`` (runtime_hooks.create_file_tracker:
  Write/Edit/MultiEdit/NotebookEdit and Bash write targets) — not its reads.
- "uncommitted": ``git status`` on exactly those files (GIT_OPTIONAL_LOCKS=0). Git runs
  only when a sibling has written something under this repo, so a lone session pays
  one set scan per call and no subprocess.
- once per session per repo; a repo with no sibling edits is checked again later. The
  git answer is cached for ``_DIRTY_TTL`` seconds per (repo, the files asked about), so a
  sibling's committed files cost one ``git status`` per 30 s, not one per tool call
  (measured: ~30 ms in a 2,000-file repo, on the gate pool, off the event loop).
- SwarmWS and the app data dir are skipped (repo_guidance._excluded): SwarmWS is
  committed per session by WorkspaceAutoCommitHook.

State (the repos already noted) lives in the session's own hook context
(``_shared_checkout_noted``), so it ends with the session. Errors fail open (no note).

The same facts decide dangerous_command_gate's discard rule (a command that would
destroy uncommitted work another session wrote): ``sibling_uncommitted(strict=True)``
reads git fresh and raises GitUnknown instead of reporting "nothing".

Public: ``create_shared_checkout_hook``, ``sibling_uncommitted``, ``sibling_repos``,
``GitUnknown``.
"""
from __future__ import annotations

import logging
import os
import subprocess
from typing import Any

logger = logging.getLogger(__name__)

_HOOK_TIMEOUT = 3.0
_GIT_TIMEOUT = 2.5
MAX_LISTED = 5

_NOTED = "_shared_checkout_noted"
_DIRTY_TTL = 30.0
_dirty_cache: dict = {}      # (root, frozenset(rels)) -> (monotonic ts, set)


def _live_contexts():
    """(label, hook session context) for every SessionUnit the router holds."""
    try:
        from core import session_registry
        router = session_registry.session_router
    except Exception:  # noqa: BLE001
        logger.debug("shared_checkout: no session router", exc_info=True)
        return []
    if router is None:
        return []
    out = []
    for sid, unit in list(getattr(router, "_units", {}).items()):
        ctx = getattr(unit, "_hook_session_context", None)
        if ctx:
            out.append((str(sid), ctx))
    return out


def _git_dirty(root: str, rels: "list[str]", fresh: bool = False) -> "set[str] | None":
    """The subset of *rels* (repo-relative) git reports as modified, added or untracked;
    None when git could not tell (not cached). *fresh* skips the cache."""
    import time
    key = (root, frozenset(rels))
    hit = _dirty_cache.get(key)
    now = time.monotonic()
    if not fresh and hit is not None and now - hit[0] < _DIRTY_TTL:
        return hit[1]
    dirty = _git_status(root, rels)
    if dirty is None:
        return None
    if len(_dirty_cache) > 256:
        _dirty_cache.clear()
    _dirty_cache[key] = (now, dirty)
    return dirty


def _git_status(root: str, rels: "list[str]") -> "set[str] | None":
    """None: git failed or timed out."""
    env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0"}
    try:
        p = subprocess.run(
            ["git", "-C", root, "status", "--porcelain=v1", "-z", "--untracked-files=all",
             "--", *rels],
            capture_output=True, text=True, timeout=_GIT_TIMEOUT, env=env)
    except (OSError, subprocess.SubprocessError):
        return None
    if p.returncode != 0:
        return None
    dirty = set()
    for entry in p.stdout.split("\0"):
        if len(entry) > 3:
            dirty.add(entry[3:])
    return dirty


class GitUnknown(Exception):
    """git could not say which files are uncommitted (sibling_uncommitted strict=True)."""


def sibling_uncommitted(root: str, mine: "dict | None",
                        strict: bool = False) -> "list[tuple[str, list[str]]]":
    """[(other session id, its uncommitted files under *root*, repo-relative)], sorted.

    When git cannot tell: [] by default (the note fails open); with *strict* (the
    dangerous_command_gate's discard rule) GitUnknown is raised, and the cache is not
    used, so a sibling's edit since the last check is seen."""
    root = os.path.realpath(root)
    prefix = root + os.sep
    per_session: dict[str, list[str]] = {}
    for sid, ctx in _live_contexts():
        if ctx is mine:
            continue
        for p in list(ctx.get("_files_written") or ()):
            if isinstance(p, str) and p.startswith(prefix):
                per_session.setdefault(sid, []).append(os.path.relpath(p, root))
    if not per_session:
        return []
    dirty = _git_dirty(root, sorted({r for rels in per_session.values() for r in rels}),
                       fresh=strict)
    if dirty is None:
        if strict:
            raise GitUnknown(root)
        return []
    out = []
    for sid, rels in sorted(per_session.items()):
        hit = sorted(r for r in set(rels) if r in dirty)
        if hit:
            out.append((sid, hit))
    return out


def sibling_repos(mine: "dict | None") -> "set[str]":
    """Every git repo another session has written a file in (repo roots, realpath'd)."""
    from core.repo_guidance import _repo_root
    roots = set()
    for _sid, ctx in _live_contexts():
        if ctx is mine:
            continue
        for p in list(ctx.get("_files_written") or ()):
            if isinstance(p, str):
                r = _repo_root(p)
                if r:
                    roots.add(r)
    return roots


def _render(root: str, found: "list[tuple[str, list[str]]]") -> str:
    lines = [f"[Shared checkout] Another live session has uncommitted changes in {root}:"]
    for sid, rels in found:
        shown = ", ".join(rels[:MAX_LISTED]) + (f" (+{len(rels) - MAX_LISTED} more)"
                                                 if len(rels) > MAX_LISTED else "")
        lines.append(f"- session {sid[:8]}: {shown}")
    lines += [
        "If you change files in this repo here, your edits and theirs mix: either commit "
        "can pick up the other's work. Make your change in your own worktree instead, and "
        "tell the user you did:",
        f'  "$SWARM_BACKEND_BIN" py -m scripts.session_worktree create "{root}"',
        "  (prints the worktree path; edit, test and commit there), then",
        '  "$SWARM_BACKEND_BIN" py -m scripts.session_worktree land "<worktree path>"',
        '  "$SWARM_BACKEND_BIN" py -m scripts.session_worktree remove "<worktree path>"',
        "If the user wants to stay in this checkout, stay; commit only your own files "
        "(git commit -- <paths>). If you already edited files here, say which.",
    ]
    return "\n".join(lines)


def _judge(ctx: dict, input_data: dict) -> "str | None":
    from core.repo_guidance import _excluded, _repo_root, _targets
    paths, written = _targets(input_data)
    if written is not None:
        paths = [written]
    if not paths:
        return None
    noted = ctx.setdefault(_NOTED, set())
    out = []
    for p in paths:
        root = _repo_root(p)
        if root is None or root in noted or _excluded(root):
            continue
        found = sibling_uncommitted(root, ctx)
        if found:
            noted.add(root)
            out.append(_render(root, found))
    return "\n\n".join(out) or None


def create_shared_checkout_hook(session_context: "dict[str, Any]", event: str):
    """PostToolUse (Read|Grep|Glob|Bash) or PreToolUse (write tools) hook. Never denies."""
    import asyncio
    from core import executors

    async def shared_checkout(input_data: Any, tool_use_id: Any, context: Any) -> dict:
        if not isinstance(input_data, dict) or session_context is None:
            return {}
        try:
            text = await asyncio.wait_for(
                executors.run_in("gate", _judge, session_context, input_data),
                timeout=_HOOK_TIMEOUT)
        except Exception:  # noqa: BLE001 — inject-only: an error means no note
            logger.debug("shared_checkout failed open", exc_info=True)
            return {}
        if not text:
            return {}
        return {"hookSpecificOutput": {"hookEventName": event, "additionalContext": text}}

    return shared_checkout
