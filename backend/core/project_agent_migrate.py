"""One-time cleanup of what the retired Project Agent confirmation left on disk.

Until 2026-11-01 a person confirmed each Brain's ``project-agent.yaml``; that step is
gone (design 2026-11-01-brain-to-hive-zero-touch §2 item 1). Existing Brains still
carry its traces, which mislead whoever reads them, the agent included:

- retired keys (``status``, ``reviewed_*``, ``reserved_for_human``, ``placements``, …)
  and a header saying the file is a draft until someone confirms it;
- ``.project-agent-check.json``, the source check of the confirm step;
- in ``AGENTS.md``, the sentence that the definition "takes effect only once the owner
  confirms it on the Brain Hub card".

``migrate(workspace)`` runs at every workspace start and is a no-op once nothing is left
to clean, so it needs no marker. Every file it rewrites or deletes is first copied to
the Brain's ``.artifacts/project-agent/`` and the copy is checked byte for byte; when
the copy cannot be made or does not match, that file is left as it is and reported.
"""
from __future__ import annotations

import logging
import os
import re
import time
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

PROJECT_AGENT_FILE = "project-agent.yaml"
CHECK_FILE = ".project-agent-check.json"
BACKUP_DIR = ".artifacts/project-agent"
# the old headers: "A draft until a person confirms it on the Brain Hub form.",
# "Confirmed on the Brain Hub review card; ..." (the current HEADER says "Nothing in it is
# confirmed", which must not match)
_CONFIRM_HEADER = re.compile(r"a draft until|confirmed on the brain hub|confirms it on the brain hub", re.I)
# the AGENTS.md sentence, as agents wrote it: "It is a draft and takes effect only once
# the owner confirms it on the Brain Hub card." or "... and takes effect only once ..."
_DRAFT_SENTENCE = re.compile(r"It is a draft and takes effect only once the\s+owner confirms it on the\s+"
                             r"Brain Hub card\.")
_AND_CLAUSE = re.compile(r"\s+and takes effect only once the\s+owner confirms it on the\s+Brain Hub card\.")
_KEEPS = "The agent keeps it current; a dispatch uses the latest."


def _backup(project_dir: Path, src: Path, name: str) -> bool:
    """Copy *src* into the Brain's backup directory and check the copy; False when it
    could not be made or does not match (the caller then leaves *src* alone)."""
    try:
        data = src.read_bytes()
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        dest = project_dir / BACKUP_DIR / f"{name}-{stamp}{src.suffix}"
        n = 1
        while dest.exists():
            n += 1
            dest = dest.with_name(f"{name}-{stamp}-{n}{src.suffix}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        return dest.read_bytes() == data
    except OSError as exc:
        logger.warning("project agent migration: no backup of %s: %s", src, exc)
        return False


def _write(path: Path, text: str) -> None:
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _definition(project_dir: Path) -> str:
    """``rewritten``, ``clean``, ``absent``, ``unreadable`` or ``no-backup``."""
    from core import project_agent_standard as std
    p = project_dir / PROJECT_AGENT_FILE
    if not p.is_file() or p.is_symlink():
        return "absent"
    try:
        raw = p.read_text(encoding="utf-8")
        doc = yaml.safe_load(raw) or {}
    except (OSError, UnicodeError, yaml.YAMLError):
        return "unreadable"
    if not isinstance(doc, dict):
        return "unreadable"
    header = "".join(line for line in raw.splitlines(keepends=True)[:8] if line.startswith("#"))
    clean = std.ordered(doc)
    if clean == doc and not _CONFIRM_HEADER.search(header):
        return "clean"
    if not _backup(project_dir, p, "previous"):
        return "no-backup"
    _write(p, std.HEADER + "# Rewritten in the standard order, retired fields dropped.\n"
           + yaml.safe_dump(clean, sort_keys=False, allow_unicode=True, width=100))
    return "rewritten"


def _check_file(project_dir: Path) -> str:
    p = project_dir / CHECK_FILE
    if not p.is_file() or p.is_symlink():
        return "absent"
    if not _backup(project_dir, p, "project-agent-check"):
        return "no-backup"
    p.unlink()
    return "deleted"


def _agents_md(project_dir: Path) -> str:
    p = project_dir / "AGENTS.md"
    if not p.is_file() or p.is_symlink():
        return "absent"
    try:
        text = p.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return "unreadable"
    new = _AND_CLAUSE.sub(". " + _KEEPS, _DRAFT_SENTENCE.sub(_KEEPS, text))
    if new == text:
        return "clean"
    if not _backup(project_dir, p, "AGENTS"):
        return "no-backup"
    _write(p, new)
    return "rewritten"


def brain_dirs(workspace: Path) -> list[Path]:
    from core.project_agent import general_brain_dir
    projects = Path(workspace) / "Projects"
    out = [d for d in sorted(projects.iterdir()) if d.is_dir() and not d.is_symlink()] \
        if projects.is_dir() else []
    general = general_brain_dir(Path(workspace))
    return out + ([general] if general.is_dir() else [])


def migrate(workspace: Path) -> dict[str, dict[str, Any]]:
    """Clean every Brain of *workspace*; ``{brain: {definition, check_file, agents_md}}``
    for each Brain where something was done or could not be. Never raises."""
    out: dict[str, dict[str, Any]] = {}
    for d in brain_dirs(Path(workspace)):
        try:
            row = {"definition": _definition(d), "check_file": _check_file(d),
                   "agents_md": _agents_md(d)}
        except Exception as exc:  # noqa: BLE001 — one Brain never stops the others
            logger.warning("project agent migration of %s failed: %s: %s", d.name, type(exc).__name__, exc)
            row = {"error": type(exc).__name__}
        if any(v not in ("clean", "absent") for v in row.values()):
            out[d.name] = row
    if out:
        logger.info("project agent migration: %s", out)
    return out
