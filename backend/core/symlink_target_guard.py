"""symlink_target_guard — refuse an ``ln`` whose destination already links to a directory.

``ln -s SRC DST`` (and ``ln -sf``) follows DST when DST is an existing symlink to a
directory, and creates the new link INSIDE the directory it points to, exits 0 and
prints nothing. In a worktree whose ``node_modules`` (or ``.venv``) is linked to the
main checkout, re-running the link therefore writes ``node_modules/node_modules`` into
the main checkout: a link to itself that a later ``npm install`` fills with a nested
copy and then fails on (ENOTEMPTY) in whatever session runs it next. The write lands in
a directory the command never named, so nothing in the session that made it shows the
damage.

The guard denies exactly that shape: an ``ln`` without ``-n`` / ``-h`` / ``-T`` (or
their long forms), two operands, and a destination that is a symlink to a directory
when the hook runs. Everything else passes, including linking into a real directory
(``ln -s file ~/bin``, the documented form), ``-t DIR`` and three or more operands
(the target directory is named on purpose), ``ln -sfn`` (replaces the link) and a
destination that does not exist yet.

Where the destination is is read from the hook's ``cwd``, followed through ``cd X &&``
chains and plain ``NAME=value`` assignments earlier in the same command. A destination
that cannot be resolved (an unknown variable, a glob, ``$(…)``) passes: the guard
denies only what it can see, and an unparseable command is bash_syntax_guard's.

Exports: symlink_target_guard (an async PreToolUse hook for Bash), link_into_dir
(the pure check, for tests and other readers).
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any, Optional

from core.security_hooks import (
    _cd_segment_target, _gate_shell_segments, _resolve_dir, _unwrap_program,
)

logger = logging.getLogger(__name__)

_ASSIGN_RE = re.compile(r"^([A-Za-z_]\w*)=(.*)$", re.S)
_VAR_RE = re.compile(r"\$\{([A-Za-z_]\w*)\}|\$([A-Za-z_]\w*)")
#: options that make ln treat an existing link as a file, never as its directory
_NO_DEREF_LONG = ("--no-dereference", "--no-target-directory")
_NO_DEREF_SHORT = set("nhT")


def _expand(word: str, env: dict[str, str]) -> str:
    """*word* with ``$NAME`` / ``${NAME}`` replaced from *env*; a name not in *env*
    stays as written, so _resolve_dir reports the path as unknown."""
    return _VAR_RE.sub(lambda m: env.get(m.group(1) or m.group(2), m.group(0)), word)


def _ln_destination(tokens: list[str]) -> Optional[str]:
    """The destination operand of an ``ln`` that would follow an existing link to a
    directory, or None (not ln, a no-dereference option, ``-t``, or not two operands)."""
    if not tokens or os.path.basename(tokens[0]) != "ln":
        return None
    operands: list[str] = []
    i = 1
    while i < len(tokens):
        t = tokens[i]
        if t == "--":
            operands.extend(tokens[i + 1:])
            break
        if t.startswith("--"):
            if t in _NO_DEREF_LONG or t.startswith("--target-directory"):
                return None
        elif t.startswith("-") and len(t) > 1:
            if set(t[1:]) & _NO_DEREF_SHORT or "t" in t[1:]:
                return None
            if t.endswith("S"):          # --suffix's short form takes a value
                i += 1
        else:
            operands.append(t)
        i += 1
    return operands[1] if len(operands) == 2 else None


def link_into_dir(command: str, cwd: str) -> Optional[tuple[str, str]]:
    """``(destination, the directory it links to)`` for the first ``ln`` in *command*
    that would create its link inside the directory an existing destination link points
    to; None when there is none or it cannot be told."""
    seps: list[str] = []
    segments = _gate_shell_segments(command, seps)
    if not segments:
        return None
    env: dict[str, str] = {}
    cur = cwd or ""
    pending: Optional[str] = None
    for seg, sep in zip(segments, seps):
        if pending is not None:
            # only `cd X && …` is known to run in X: after `;` a failed cd stays put
            cur = pending if "&&" in sep else ""
            pending = None
        if seg and all(_ASSIGN_RE.match(t) for t in seg):
            for t in seg:
                name, value = _ASSIGN_RE.match(t).groups()
                env[name] = _expand(value, env)
            continue
        words = [_expand(t, env) for t in seg]
        target = _cd_segment_target(words, cur)
        if target is not None:
            pending = target
            continue
        dest = _ln_destination(_unwrap_program(words))
        if dest is None:
            continue
        path = _resolve_dir(cur, dest.rstrip("/") or dest)
        if path and os.path.islink(path) and os.path.isdir(path):
            return path, os.path.realpath(path)
    return None


async def symlink_target_guard(
    input_data: dict[str, Any], tool_use_id: str | None, context: Any,
) -> dict[str, Any]:
    """PreToolUse (Bash): deny an ``ln`` that would write into a linked directory."""
    if input_data.get("tool_name") != "Bash":
        return {"decision": "approve"}
    command = (input_data.get("tool_input") or {}).get("command") or ""
    if "ln" not in command:
        return {"decision": "approve"}
    try:
        hit = link_into_dir(command, str(input_data.get("cwd") or ""))
    except Exception as exc:  # noqa: BLE001 — fail open: a guard bug must not block a command
        logger.warning("symlink_target_guard: could not judge the command (%s)", type(exc).__name__)
        return {"decision": "approve"}
    if hit is None:
        return {"decision": "approve"}
    dest, real = hit
    return {"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": (
            f"{dest} is already a symlink to {real}, so this ln would create its link "
            f"INSIDE {real} (for example {real}/{os.path.basename(dest)}), not replace "
            f"{dest}. If {dest} already points where you want, there is nothing to do. "
            f"To replace the link, use `ln -sfn SRC {dest}`. To put a link inside that "
            "directory on purpose, name the full path of the new link."
        ),
    }}
