"""ddd_setup_state — whether a Brain's setup (s_project-manager P0..P6) is finished.

One small file per Brain, ``.artifacts/setup.json``:

    {"state": "setting-up" | "ready", "updated_at": "<ISO>", "counts": {...},
     "open": ["<check name>", …],   # the checks not passed yet: FAIL first, then PENDING
     "assets": ["<repo URL or path>", …]}   # what the New Brain form said it governs

Writers (exactly two, one per transition):
- ``create_project`` writes ``setting-up`` as soon as the folder of a NEW project
  exists (never on a re-provision or migration of an existing one), so a create that
  fails halfway leaves a Brain that can still be continued, with the assets it was
  created with;
- the P6 gate (``verify_ddd_complete.py --record``) writes ``ready`` on PASS, or
  keeps ``setting-up`` on FAIL. A write keeps the recorded ``assets``.

``stalled`` finds a setup nobody is carrying on (the ``brain-setup-resume`` job then
continues it with ``RESUME_PROMPT``: design 2026-11-01-brain-to-hive-zero-touch §1).

Reader: the Brain Hub gallery (``setupState``) shows "being born" while it is
``setting-up``, with ``setupProgress`` (checks done of all, from the last P6 counts, and
``next`` — the first check still open) when a P6 run recorded them. A Brain without the file (made before this existed) is
``None`` and is never shown as being born.

Key public symbols: ``SETUP_FILE``, ``STATES``, ``read_setup_state``,
``read_setup_progress``, ``read_setup_assets``, ``write_setup_state``, ``stalled``,
``resume_prompt``.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

SETUP_FILE = ".artifacts/setup.json"
STATES = frozenset({"setting-up", "ready"})


def _read_record(project_dir: Path) -> Optional[dict]:
    p = project_dir / SETUP_FILE
    try:
        if not p.is_file() or p.is_symlink() or p.stat().st_size > 8192:
            return None
        body = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        logger.debug("setup.json unreadable in %s: %s", project_dir, exc)
        return None
    return body if isinstance(body, dict) else None


def read_setup_state(project_dir: Path) -> Optional[str]:
    """``setting-up`` / ``ready``, or None when the Brain has no (valid) record."""
    body = _read_record(project_dir)
    state = body.get("state") if body else None
    return state if state in STATES else None


def read_setup_progress(project_dir: Path) -> Optional[dict[str, int]]:
    """How far an unfinished setup got, from the last P6 ``--record``:
    ``{"done": PASS + N/A, "total": every check, "next": the first open check or
    None}``. None when the Brain is not setting up, or no P6 run recorded counts yet
    (right after create_project)."""
    body = _read_record(project_dir)
    if not body or body.get("state") != "setting-up":
        return None
    counts = body.get("counts")
    if not isinstance(counts, dict):
        return None
    try:
        nums = {str(k): int(v) for k, v in counts.items()}
    except (TypeError, ValueError):
        return None
    total = sum(nums.values())
    if total <= 0:
        return None
    open_checks = body.get("open")
    nxt = (open_checks[0] if isinstance(open_checks, list) and open_checks
           and isinstance(open_checks[0], str) else None)
    return {"done": nums.get("PASS", 0) + nums.get("N/A", 0), "total": total,
            "next": nxt[:80] if nxt else None}


MAX_ASSETS = 20


def read_setup_assets(project_dir: Path) -> Optional[list[str]]:
    """The assets the Brain was created to govern (the New Brain form's GOVERN items:
    a repository URL or a local path each), or None when none were recorded."""
    body = _read_record(project_dir)
    assets = body.get("assets") if body else None
    if not isinstance(assets, list):
        return None
    return [a for a in assets if isinstance(a, str) and a.strip()][:MAX_ASSETS]


def write_setup_state(project_dir: Path, state: str, counts: Optional[dict[str, Any]] = None,
                      open_checks: Optional[list[str]] = None,
                      assets: Optional[list[str]] = None) -> None:
    """Atomically write the record (temp file + rename). *assets* None keeps the ones
    recorded already."""
    if state not in STATES:
        raise ValueError(f"unknown setup state {state!r}")
    p = project_dir / SETUP_FILE
    p.parent.mkdir(parents=True, exist_ok=True)
    kept = read_setup_assets(project_dir) if assets is None else \
        [str(a).strip()[:1000] for a in assets if str(a).strip()][:MAX_ASSETS]
    resumes = _resumes(project_dir)
    body = {"state": state, "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    if counts:
        body["counts"] = counts
    if open_checks:
        body["open"] = [str(c) for c in open_checks]
    if kept:
        body["assets"] = kept
    if resumes and state == "setting-up":
        body["resumes"] = resumes
    _write_record(project_dir, body)


def _write_record(project_dir: Path, body: dict[str, Any]) -> None:
    """Write the record atomically (temp file + rename; the temp file is removed when
    the write fails)."""
    p = project_dir / SETUP_FILE
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=".setup-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(body, fh, indent=2)
        os.replace(tmp, p)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


#: a setup with no change for this long is not being carried on
STALL_S = 2 * 3600
#: a stalled setup is continued at most this many times, at least RESUME_GAP_S apart;
#: after that it waits for "Continue setup" (it cannot finish on its own)
MAX_RESUMES = 3
RESUME_GAP_S = 24 * 3600


def _resumes(project_dir: Path) -> list[float]:
    body = _read_record(project_dir)
    got = body.get("resumes") if body else None
    return [float(t) for t in got if isinstance(t, (int, float))][-MAX_RESUMES:] if isinstance(got, list) else []


def record_resume(project_dir: Path, now: Optional[float] = None) -> None:
    """Count one automatic continuation of the setup (kept by every later write while
    the Brain is setting up)."""
    import time as _time
    body = _read_record(project_dir) or {"state": "setting-up"}
    body["resumes"] = [*_resumes(project_dir), _time.time() if now is None else now][-MAX_RESUMES:]
    _write_record(project_dir, body)


#: the files whose change means somebody is working on the setup
_WORK_FILES = (SETUP_FILE, "bindings.yaml", "project-agent.yaml", "AGENTS.md")


def _last_change(project_dir: Path) -> float:
    from core.ddd_paths import ddd_path
    paths = [project_dir / f for f in _WORK_FILES]
    paths += [ddd_path(project_dir, doc) for doc in ("PRODUCT.md", "TECH.md", "IMPROVEMENT.md", "PROJECT.md")]
    newest = 0.0
    for p in paths:
        try:
            newest = max(newest, p.stat().st_mtime)
        except OSError:
            continue
    return newest


def stalled(projects_dir: Path, now: Optional[float] = None, idle_s: float = STALL_S) -> list[Path]:
    """The Brains still ``setting-up`` whose setup files have not changed for *idle_s*
    (the session that set them up ended), not continued in the last ``RESUME_GAP_S`` and
    fewer than ``MAX_RESUMES`` times. Oldest first. A folder whose name a New Brain could
    not have (``swarm_workspace_manager._PROJECT_NAME_RE``) is skipped: its name goes into
    the continuing session's instructions."""
    import time as _time
    from core.swarm_workspace_manager import _PROJECT_NAME_RE
    now = _time.time() if now is None else now
    out = []
    try:
        dirs = sorted(d for d in Path(projects_dir).iterdir() if d.is_dir() and not d.is_symlink())
    except OSError:
        return []
    for d in dirs:
        if not _PROJECT_NAME_RE.match(d.name) or read_setup_state(d) != "setting-up":
            continue
        last = _last_change(d)
        resumes = _resumes(d)
        if len(resumes) >= MAX_RESUMES or (resumes and now - resumes[-1] < RESUME_GAP_S):
            continue
        if last and now - last >= idle_s:
            out.append((last, d))
    return [d for _last, d in sorted(out)]


def resume_prompt(name: str) -> str:
    """The prompt that continues an unfinished setup (the Brain Hub's "Continue setup"
    sends the same steps, desktop/src/utils/newBrainDispatch.buildResumeSetupPrompt)."""
    q = json.dumps(name, ensure_ascii=False)          # the name is data, quoted
    return (f"Continue setting up the {q} Brain, following the s_project-manager skill "
            "(.claude/skills/s_project-manager/SKILL.md in the workspace).\n\n"
            "Run the P6 check first (s_project-manager verify_ddd_complete.py --project "
            f"{q}) to see which phases are missing, then finish only those, in order: "
            "P3 bind (each repository the Brain governs, before any knowledge is written), "
            "P2 knowledge (survey, entries, bootstrap-apply; every PRODUCT or TECH section "
            "without evidence gets a gap), P3b the Project Agent (artifact_cli "
            "project-agent-draft), P4 code graph, P5 skills. Ask nobody: decide what you can, "
            "and record what you cannot know as a gap. Do not redo or overwrite what is "
            "already written. Finish with P6 --record.")
