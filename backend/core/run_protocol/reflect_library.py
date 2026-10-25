"""REFLECT lessons a project's DDD cannot take go to the shared Library (design
docs/design/2026-10-25-knowledge-delivery-gaps.md §3.1, part B / G1).

``Knowledge/Library/pipeline-lessons/<repo>.md``, one file per main checkout: in the
Recall index (``knowledge_store`` indexes all of ``Knowledge/``), and delivered the
first time a session touches the repo (``repo_guidance``, part A).

- ``file_for(workspace, root)`` — the file of one main checkout. Its name is the
  checkout's directory name; the file's first line names the checkout, and a second
  checkout with the same name gets ``<name>-<sha256(path)[:8]>.md``.
- ``write(workspace, root, lesson, destination, run_id)`` — appends the lesson:
  a ``domain:`` lesson under ``## 经验`` with its DDD target (``[TECH:Conventions]``),
  any other (MEMORY / EVOLUTION / self-evolution proposal: it changes the agent's own
  rules) under ``## 待审``, never in force from here. The same statement again adds a
  ``reinforced by <run> <lesson>`` line under the entry; the same run's lesson again
  (a replay) writes nothing. Each entry names its run, lesson id and date. Returns
  ``library:<path relative to the workspace>``, or None when it cannot write (a
  symlink on the way, an unreadable file): the caller keeps the lesson in the run.

Writes walk ``Knowledge/Library/pipeline-lessons`` by directory fds that follow no
symlink, take a sidecar lock (``utils.file_lock``) and replace the file atomically.

Public: ``SUBDIR``, ``file_for``, ``write``.
"""
from __future__ import annotations

import hashlib
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

SUBDIR = ("Knowledge", "Library", "pipeline-lessons")
LESSONS, PENDING = "## 经验", "## 待审"
_HEADER = "<!-- swarmai pipeline lessons for: {root} -->"
_HEADER_RE = re.compile(r"^<!-- swarmai pipeline lessons for: (.+) -->$")
_PLAIN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def _dir_fd(workspace: Path) -> int:
    """An fd of the lessons directory, made as needed; every step refuses a symlink."""
    fd = os.open(workspace, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in SUBDIR:
            try:
                os.mkdir(part, 0o755, dir_fd=fd)
            except FileExistsError:
                pass
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def _read(fd: int, name: str) -> "str | None":
    try:
        handle = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd)
    except FileNotFoundError:
        return None
    with os.fdopen(handle, "r", encoding="utf-8") as f:
        return f.read()


def _owner(text: "str | None") -> "str | None":
    first = (text or "").split("\n", 1)[0]
    m = _HEADER_RE.match(first)
    return m.group(1) if m else None


def _name(fd: int, root: str) -> str:
    base = Path(root).name
    base = base if _PLAIN.match(base) else "repo"
    plain = f"{base}.md"
    text = _read(fd, plain)
    if text is None or _owner(text) == root:
        return plain
    return f"{base}-{hashlib.sha256(root.encode('utf-8')).hexdigest()[:8]}.md"


def file_for(workspace: "str | Path", root: str) -> "Path | None":
    """The lessons file of main checkout *root* (it may not exist yet); None when the
    directory cannot be opened safely."""
    try:
        fd = _dir_fd(Path(workspace))
    except OSError:
        return None
    try:
        return Path(workspace).joinpath(*SUBDIR, _name(fd, root))
    finally:
        os.close(fd)


def _fresh(root: str) -> str:
    return (f"{_HEADER.format(root=root)}\n# Pipeline 经验：{Path(root).name}\n\n"
            f"`{root}` 的 pipeline run 留下的经验（这个仓库没有对应的项目 DDD）。\n\n"
            f"{LESSONS}\n\n{PENDING}\n\n"
            "下面的条目改的是 agent 自己的规则，要人看过才生效，这里只是记录。\n")


def _insert(text: str, section: str, statement: str, entry: str, note: str,
            run_mark: str) -> "str | None":
    """The new text, or None when nothing changes (a replay)."""
    lines = text.splitlines(keepends=True)
    if not text.endswith("\n"):
        lines[-1] += "\n"
    start = next((i for i, line in enumerate(lines) if line.strip() == section), None)
    if start is None:
        lines += [f"\n{section}\n", "\n"]
        start = len(lines) - 2
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")),
               len(lines))
    for i in range(start + 1, end):
        if lines[i].startswith("- ") and statement in lines[i]:
            j = i + 1
            while j < end and lines[j].startswith("  "):
                j += 1
            block = "".join(lines[i:j])
            if run_mark in block:
                return None                     # this run's lesson is already here
            lines.insert(j, f"  - {note}\n")
            return "".join(lines)
    at = end
    while at > start + 1 and not lines[at - 1].strip():
        at -= 1
    lines.insert(at, entry + "\n")
    if at == start + 1 or not lines[at - 1].startswith(("- ", "  ")):
        lines.insert(at, "\n")          # a list starts after a blank line
    return "".join(lines)


def write(workspace: "str | Path", root: str, lesson: Mapping[str, Any], destination: str,
          run_id: str) -> "str | None":
    from utils.file_lock import flock_exclusive, flock_unlock
    workspace = Path(workspace)
    statement = " ".join(str(lesson.get("statement") or "").split())
    lesson_id = str(lesson.get("id") or "")
    if not statement or not lesson_id:
        return None
    day = datetime.now(timezone.utc).date().isoformat()
    run_mark = f"{run_id} {lesson_id}"
    if destination.startswith("domain:"):
        section, target = LESSONS, destination.split(":", 2)[2]
    else:
        section, target = PENDING, destination
    entry = f"- [{target}] {statement}（{run_mark}，{day}）"
    note = f"reinforced by {run_mark}（{day}）"
    try:
        fd = _dir_fd(workspace)
    except OSError:
        return None
    try:
        lock_fd = os.open(".lock", os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o644, dir_fd=fd)
        lock = os.fdopen(lock_fd, "w")
        try:
            flock_exclusive(lock)
            name = _name(fd, root)
            text = _read(fd, name)
            if text is None:
                text = _fresh(root)
            elif _owner(text) != root:
                return None
            new = _insert(text, section, statement, entry, note, run_mark)
            if new is not None:
                temp = f".{name}.{uuid.uuid4().hex}.tmp"
                handle = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                 0o644, dir_fd=fd)
                try:
                    with os.fdopen(handle, "w", encoding="utf-8") as f:
                        f.write(new)
                    os.replace(temp, name, src_dir_fd=fd, dst_dir_fd=fd)
                except BaseException:
                    try:
                        os.unlink(temp, dir_fd=fd)
                    except OSError:
                        pass
                    raise
        finally:
            try:
                flock_unlock(lock)
            finally:
                lock.close()
    except (OSError, UnicodeDecodeError):
        return None
    finally:
        os.close(fd)
    return f"library:{Path(*SUBDIR) / name}"
