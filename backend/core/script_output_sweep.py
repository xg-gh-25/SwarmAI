"""Files a Bash command's SCRIPT wrote — paths the command string never names.

``python make_deck.py`` writes ``out/deck.pptx``, but no parser can read that path
from the command (file_change_classifier only sees redirects, tee, cp/mv, sed -i and
interpreter heredocs). After a successful Bash call, this module lists the files
under the command's working directory whose mtime falls inside the call, so they
can become ``file_changed`` events and the turn's products. The orchestrator then
runs them through the normal write gate (``_build_file_write_events``: resolve,
mtime re-check, needs_human_review), which stays the only authority on kind/noise.

Bounded on purpose (it runs after every Bash call, off the event loop):
- root: the command's leading ``cd /abs/path`` if any, else the session's cwd;
  never ``/`` or the home directory itself
- depth ``MAX_DEPTH``, at most ``MAX_ENTRIES`` directory entries examined
- hidden directories and canvas_noise ``NOISE_SEGMENTS`` are pruned; only
  deliverable-shaped extensions (``SWEEP_EXTENSIONS``) count
- more than ``MAX_HITS`` recent files means a bulk operation (checkout, unzip,
  build output), not a product: nothing is reported
- a command that is a ``git`` invocation is never swept (checkout / pull / stash
  rewrite many files; none of them is a product)

Known limit: another process writing a matching file in the same tree during the
call (another session) is attributed to this call.

Key exports:
- ``sweep_root(command, default_cwd)`` — the directory to sweep, or None
- ``recent_outputs(root, since)`` — absolute paths written at/after ``since``
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional

from core.canvas_noise import NOISE_SEGMENTS, is_noise_path

# Mirrors workspace_surface_watcher._WATCHED_EXTENSIONS (the cheap first-pass
# filter for user-facing deliverables); needs_human_review makes the final call.
SWEEP_EXTENSIONS = frozenset({
    ".md", ".txt", ".html", ".htm", ".json", ".csv", ".tsv",
    ".pdf", ".docx", ".pptx", ".xlsx", ".png", ".jpg", ".jpeg", ".svg",
})
MAX_DEPTH = 3
MAX_ENTRIES = 4000
MAX_HITS = 20
# 1s slack for coarse filesystem mtime resolution (same as the heredoc gate).
MTIME_SLACK_S = 1.0

# A leading `cd /abs/path &&` / `;` (optionally quoted) — the only cwd change trusted.
_LEADING_CD_RE = re.compile(r"""^\s*cd\s+(?:"([^"]+)"|'([^']+)'|([^\s;&|]+))\s*(?:&&|;)""")
_GIT_RE = re.compile(r"^\s*(?:[A-Za-z_][A-Za-z0-9_]*=\S*\s+)*git(?:\s|$)")


def _after_leading_cd(command: str) -> tuple[Optional[str], str]:
    m = _LEADING_CD_RE.match(command)
    if not m:
        return None, command
    return (m.group(1) or m.group(2) or m.group(3)), command[m.end():]


def sweep_root(command: object, default_cwd: object) -> Optional[str]:
    """The directory whose recent files a Bash ``command`` may have produced, or None
    (no sweep). An absolute leading ``cd`` wins over the session cwd; a relative,
    ``~`` or ``$VAR`` cd is not guessed at (no sweep)."""
    if not isinstance(command, str) or not command.strip():
        return None
    cd, rest = _after_leading_cd(command)
    if _GIT_RE.match(rest):
        return None
    if cd is not None:
        if not cd.startswith("/") or "$" in cd or "/.." in cd:
            return None
        root = cd
    elif isinstance(default_cwd, str) and default_cwd.startswith("/"):
        root = default_cwd
    else:
        return None
    try:
        resolved = Path(root).resolve()
    except (OSError, RuntimeError):
        return None
    if resolved == Path("/") or resolved == Path.home().resolve() or not resolved.is_dir():
        return None
    return str(resolved)


def recent_outputs(root: str, since: float) -> list[str]:
    """Absolute paths under ``root`` (depth <= MAX_DEPTH) with a deliverable-shaped
    extension and mtime >= ``since`` - slack. [] for a bulk change (> MAX_HITS) or
    any error. Never raises."""
    hits: list[str] = []
    seen = 0
    floor = since - MTIME_SLACK_S
    stack: list[tuple[str, int]] = [(root, 0)]
    try:
        while stack:
            path, depth = stack.pop()
            try:
                entries = os.scandir(path)
            except OSError:
                continue
            with entries:
                for entry in entries:
                    seen += 1
                    if seen > MAX_ENTRIES:
                        return hits if len(hits) <= MAX_HITS else []
                    name = entry.name
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            if depth < MAX_DEPTH and not name.startswith(".") and name not in NOISE_SEGMENTS:
                                stack.append((entry.path, depth + 1))
                            continue
                        if not entry.is_file(follow_symlinks=False):
                            continue
                        if os.path.splitext(name)[1].lower() not in SWEEP_EXTENSIONS:
                            continue
                        if entry.stat(follow_symlinks=False).st_mtime < floor:
                            continue
                    except OSError:
                        continue
                    if is_noise_path(entry.path):
                        continue
                    hits.append(entry.path)
                    if len(hits) > MAX_HITS:
                        return []
    except Exception:  # noqa: BLE001 — a display aid must never break the turn
        return []
    return sorted(hits)
