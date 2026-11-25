"""Feature Map commit notice: after a commit outside a pipeline run, tell the agent which
features it reaches (design docs/design/2026-11-23-feature-map-replay-conditions.md §4).

Inject-only, never a deny, never a replay. Two Bash hooks:

- **PreToolUse** — a command that may be a commit (the word ``commit`` in it) snapshots
  ``HEAD`` of each repo it works in (its cwd, ``cd X``, ``git -C X``) that has a feature
  folder in the repo, keyed by the call's tool_use_id.
- **PostToolUse** — when the command parses as ``git commit`` and a snapshotted repo's
  HEAD moved during the call, the paths changed between the two heads go through
  ``reach.affected``; the features reached are kept for the session per repo, and one
  line names every feature reached since the last ``feature_map replay`` / ``verify``
  command in that repo, with the command that replays them. A pull, checkout, reset or
  rebase is not a commit and gives no line; a commit that moved nothing gives none.

A replay or verify command clears the features it names (all of them with
``--affected`` or no names). The PostToolUse handler also runs on PostToolUseFailure, so
``git commit … && false`` still gets its line.

Repos under the pipeline worktree root are skipped: a run replays in its TEST stage. A
feature folder kept in the Brain changes without a commit and is out of scope. When
the reach cannot be computed (git fails, the folder is invalid, more than
``_MAX_PATHS`` paths changed, the hook's time bound) the line says so instead of
staying silent. The work runs on its own two-worker pool, never the ``gate`` pool the
per-call checks wait on nor ``subprocess`` behind auto-commit, and paths, reasons and errors enter the context quoted, one
line each, capped.

State is in-process per session under one lock (``forget`` drops it; a snapshot whose
PostToolUse never came is dropped after ``_STALE_S``); losing it costs a notice, never
a block. Exports: create_commit_notice_hooks, forget.
"""
from __future__ import annotations

import asyncio
import logging
import os
import shlex
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from core.feature_map import files, reach

logger = logging.getLogger(__name__)

_HOOK_TIMEOUT = 3.0
_GIT_TIMEOUT = 2.0
_MAX_WHY = 5
_MAX_PATHS = 5000
_STALE_S = 600.0
_POOL = "feature_map_notice"
_REPLAY = '"$SWARM_BACKEND_BIN" py -m scripts.feature_map replay'

# session id -> {"heads": {tool_use_id: (monotonic time, {root: head | None} | {"_error": str})},
#                "reached": {root: {feature id: why}}}
_state: dict[str, dict[str, dict]] = {}
_lock = threading.Lock()


def forget(session_id: str) -> None:
    with _lock:
        _state.pop(session_id, None)


def _q(value: object, limit: int = 160) -> str:
    """Untrusted text (a path, a reason, an error) as one quoted, capped token."""
    flat = " ".join(str(value).split()).replace("`", "'")
    return f"`{flat[:limit]}{'…' if len(flat) > limit else ''}`"


def _words(command: str) -> list[str]:
    try:
        return shlex.split(command, posix=True)
    except ValueError:
        return command.split()


def _git_subcommands(command: str) -> list[str]:
    """The subcommand of every ``git`` invocation in *command* (``-C X``, ``-c k=v`` and
    other leading options skipped)."""
    words, out = _words(command), []
    for i, w in enumerate(words):
        if os.path.basename(w) != "git":
            continue
        j = i + 1
        while j < len(words):
            if words[j] in ("-C", "-c", "--git-dir", "--work-tree", "--namespace"):
                j += 2
            elif words[j].startswith("-"):
                j += 1
            else:
                out.append(words[j].rstrip(";&|"))
                break
    return out


def _is_commit(command: str) -> bool:
    return "commit" in _git_subcommands(command)


_SEPARATORS = ("&&", "||", ";", "|", "&")
_REDIRECTS = (">", ">>", "<", "2>", "2>>", "&>")


def _feature_map_args(command: str):
    """The parsed arguments of the ``scripts.feature_map`` call in *command*, read with
    the CLI's own parser (``core.feature_map.cli``), or None."""
    from core.feature_map.cli import parser
    words = _words(command)
    for k, w in enumerate(words):
        if w != "scripts.feature_map":
            continue
        args, skip = [], False
        for a in words[k + 1:]:
            if skip:
                skip = False
                continue
            if a in _SEPARATORS:
                break
            if a in _REDIRECTS:
                skip = True
                continue
            if a.startswith((">", "<", "2>", "&>")):
                continue
            args.append(a)
        try:
            ns, _rest = parser(exit_on_error=False).parse_known_intermixed_args(args)
        except (SystemExit, Exception) as exc:  # noqa: BLE001 — a call the CLI refuses
            logger.debug("not a feature_map call the CLI accepts (%s): %s",
                         type(exc).__name__, exc)
            return None
        return ns
    return None


def _replayed(command: str) -> "tuple[bool, set[str] | None]":
    """``(is a replay/verify, the feature ids it names or None for all)``."""
    ns = _feature_map_args(command)
    if ns is None or ns.command not in ("replay", "verify"):
        return False, None
    if ns.affected or not ns.features:
        return True, None
    return True, set(ns.features)


def _dirs(command: str, cwd: str) -> list[str]:
    from core.repo_guidance import _bash_dirs
    out = _bash_dirs(command, cwd)
    ns = _feature_map_args(command)
    value = getattr(ns, "repo_root", None) if ns is not None else None
    if value and value != "." and "$" not in value:
        t = os.path.expanduser(value)
        out.append(t if os.path.isabs(t) else os.path.join(cwd or "/", t))
    return out


def _git(root: str, *args: str) -> str:
    return subprocess.run(["git", "-C", root, "-c", "core.quotepath=off", *args],
                          capture_output=True, text=True, check=True,
                          timeout=_GIT_TIMEOUT).stdout


def _watched(command: str, cwd: str) -> list[str]:
    """Repo roots the command works in whose feature folder lives in the repo, outside
    the pipeline worktree root."""
    from core.repo_guidance import _repo_root
    from core.run_worktree import wt_root
    run_root = str(wt_root())
    roots: list[str] = []
    for d in _dirs(command, cwd):
        root = _repo_root(d)
        if not root or root in roots:
            continue
        if root == run_root or root.startswith(run_root + os.sep):
            continue
        if (Path(root) / files.REPO_FOLDER / "features.yaml").is_file():
            roots.append(root)
    return roots


def _head(root: str) -> str | None:
    try:
        return _git(root, "rev-parse", "--verify", "-q", "HEAD").strip() or None
    except subprocess.CalledProcessError:
        return None                        # no commit yet


def _snapshot(command: str, cwd: str) -> dict:
    try:
        return {root: _head(root) for root in _watched(command, cwd)}
    except Exception as exc:  # noqa: BLE001 — said by the PostToolUse line
        return {"_error": f"{type(exc).__name__}: {exc}"[:200]}


def _changed(root: str, old: str | None, new: str) -> list[str]:
    if old is None:
        out = _git(root, "diff-tree", "--root", "-r", "--no-renames", "--name-only",
                   "--no-commit-id", new)
    else:
        out = _git(root, "diff", "--name-only", "--no-renames", old, new)
    return [p for p in out.splitlines() if p]


def _line(root: str, now: dict, since: dict, ignored: list, stale: list) -> str:
    where = f" in {_q(root)}"
    if now:
        whys = "; ".join(f"{fid}: {_q(why)}" for fid, why in list(now.items())[:_MAX_WHY])
        more = len(now) - _MAX_WHY
        head = (f"Feature Map: this commit reaches {len(now)} feature(s){where} ({whys}"
                + (f"; +{more} more" if more > 0 else "") + ")")
    else:
        head = f"Feature Map: this commit reaches no feature{where}"
    parts = [head]
    if ignored:
        parts.append(f"ignored by its targets: {', '.join(_q(i) for i in ignored[:5])}"
                     + (f" +{len(ignored) - 5} more" if len(ignored) > 5 else ""))
    if stale:
        parts.append("owned paths missing (treated as not declared): "
                     + ", ".join(f"{s['feature']}: {_q(s['path'])}" for s in stale[:5]))
    earlier = [f for f in since if f not in now]
    if earlier:
        parts.append(f"also reached by this session's earlier commits: {', '.join(earlier)}")
    text = "; ".join(parts) + "."
    if since:
        text += (" Replay them before you report this work done: "
                 f"cd {shlex.quote(root)} && {_REPLAY} {' '.join(since)}")
    return text


def _notice(session_id: str, command: str, cwd: str, snap: dict | None) -> str | None:
    """The context line(s) for this PostToolUse call, or None."""
    is_replay, named = _replayed(command)
    if is_replay:
        roots = _watched(command, cwd)
        with _lock:
            st = _state.setdefault(session_id, {"heads": {}, "reached": {}})
            for root in roots:
                if named is None:
                    st["reached"].pop(root, None)
                else:
                    for fid in named:
                        st["reached"].get(root, {}).pop(fid, None)
        return None
    if not _is_commit(command) or snap is None:
        return None
    if "_error" in snap:
        return (f"Feature Map: reach not computed ({_q(snap['_error'])}); run "
                "`feature_map affected` in the repo you committed to.")
    lines = []
    for root, old in snap.items():
        try:
            new = _head(root)
            if not new or new == old:
                continue
            changed = _changed(root, old, new)
            if len(changed) > _MAX_PATHS:
                raise RuntimeError(f"{len(changed)} paths changed, more than {_MAX_PATHS}")
            fmap = files.load_folder(Path(root) / files.REPO_FOLDER)
            aff = reach.affected(fmap, changed, root=root, folder_rel=files.REPO_FOLDER)
        except Exception as exc:  # noqa: BLE001 — said, never silent, never a deny
            lines.append(f"Feature Map: reach not computed in {_q(root)} "
                         f"({type(exc).__name__}: {_q(exc, 200)}); run `feature_map affected` there.")
            continue
        now = {fid: aff["why"].get(fid, "") for fid in aff["features"]}
        with _lock:
            st = _state.setdefault(session_id, {"heads": {}, "reached": {}})
            since = st["reached"].setdefault(root, {})
            since.update(now)
            since = dict(since)
        lines.append(_line(root, now, since, aff["ignored"], aff["stale"]))
    return "\n".join(lines) or None


def create_commit_notice_hooks(session_context: "dict[str, Any]"):
    """``(pre, post)`` async hooks for Bash; *post* serves PostToolUse and
    PostToolUseFailure (``post.for_event(name)``). Never deny; bounded; fail open with a
    line."""
    from core import executors

    def _sid() -> str:
        return (session_context.get("sdk_session_id", "") or "") if session_context else ""

    def _err(exc: BaseException) -> str:
        return f"{type(exc).__name__}: {exc}" if str(exc) else f"{type(exc).__name__}: timed out"

    async def pre(input_data: Any, tool_use_id: Any, context: Any) -> dict:
        if not isinstance(input_data, dict) or not tool_use_id:
            return {}
        command = (input_data.get("tool_input") or {}).get("command")
        sid = _sid()
        if not sid or not isinstance(command, str) or "commit" not in command:
            return {}
        cwd = input_data.get("cwd") or ""
        try:
            snap = await asyncio.wait_for(executors.run_in(_POOL, _snapshot, command, cwd),
                                          timeout=_HOOK_TIMEOUT)
        except Exception as exc:  # noqa: BLE001 — the PostToolUse line says so
            snap = {"_error": _err(exc)[:200]}
        now = time.monotonic()
        with _lock:
            heads = _state.setdefault(sid, {"heads": {}, "reached": {}})["heads"]
            for k in [k for k, (at, _s) in heads.items() if now - at > _STALE_S]:
                heads.pop(k, None)
            if snap:
                heads[tool_use_id] = (now, snap)
        return {}

    def _make_post(event: str):
        async def post(input_data: Any, tool_use_id: Any, context: Any) -> dict:
            if not isinstance(input_data, dict):
                return {}
            command = (input_data.get("tool_input") or {}).get("command")
            sid = _sid()
            if not sid or not isinstance(command, str):
                return {}
            if "commit" not in command and "feature_map" not in command:
                return {}
            with _lock:
                entry = _state.get(sid, {}).get("heads", {}).pop(tool_use_id, None)
            snap = entry[1] if entry else None
            cwd = input_data.get("cwd") or ""
            try:
                text = await asyncio.wait_for(
                    executors.run_in(_POOL, _notice, sid, command, cwd, snap),
                    timeout=_HOOK_TIMEOUT)
            except Exception as exc:  # noqa: BLE001 — inject-only: say it, never deny
                logger.debug("feature map notice failed open", exc_info=True)
                text = (f"Feature Map: reach not computed ({_q(_err(exc))}); run "
                        "`feature_map affected` in the repo you committed to."
                        if _is_commit(command) else None)
            if not text:
                return {}
            return {"hookSpecificOutput": {"hookEventName": event, "additionalContext": text}}
        return post

    post = _make_post("PostToolUse")
    post.for_event = _make_post            # type: ignore[attr-defined]
    return pre, post
