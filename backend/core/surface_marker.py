"""surface_marker — evidence that a run's committed source rows reached Canvas.

The pipeline completion gate (``run-update --status completed``) refuses a run that
committed source but never surfaced it to the Canvas OUTPUTS rail
(``completion_gate.unsurfaced_source``). Its evidence used to be a flag the agent
wrote into the DELIVER stage record with a ``run-update --stage-json`` finalize.
That finalize REPLACES the stage record, so it erased DELIVER's token_cost and
decisions on most runs (30 of 43 completed runs, run_a87bfe3f).

This module moves the evidence out of the stage record:

* when the agent calls the ``surface_run_outputs`` tool, the daemon's streaming
  orchestrator takes :func:`read_snapshot` BEFORE building the run's rows, yields
  them, and only then calls :func:`write_marker` with that snapshot's commits. The
  marker is written only when at least one committed source row was emitted, so it
  never attests rows this session did not send (the orchestrator dedups emission
  per session), and a commit landing mid-build is left out (the gate blocks);
* the CLI gate calls :func:`marker_covers`, which accepts the marker only if it
  names every commit the run has recorded now, so a commit made after surfacing
  does not pass on an old marker.

Honest scope: anything that can write the run directory can forge this file. It is
stronger than a flag the agent types (the agent's normal path never writes it), not
proof that the frontend rendered the rows.

Public symbols:
    MARKER_NAME
    find_run_json(run_id, workspace_root) -> Path | None
    read_snapshot(run_id, workspace_root) -> (Path, commits) | None
    commit_shas(commits) -> set[str]
    write_marker(run_json, events, commits=None) -> bool   (never raises)
    marker_covers(run_dir, commits) -> bool     (never raises)
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

MARKER_NAME = "outputs_surfaced.json"

# The same pattern as core/pipeline_stage_events._RUN_ID_RE (kept local so the CLI
# can import this module without the project registry): an alnum/_/- token, so a
# run id can never glob-match another run or leave the runs directory.
_RUN_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")


def find_run_json(run_id: object, workspace_root: object) -> "Path | None":
    """Return ``Projects/*/.artifacts/runs/<run_id>/run.json`` under the workspace,
    or None for an unsafe id, a missing run, or any filesystem error."""
    if not isinstance(run_id, str) or not _RUN_ID_RE.fullmatch(run_id):
        return None
    try:
        ws = Path(str(workspace_root)).resolve()
        for path in (ws / "Projects").glob("*/.artifacts/runs/" + run_id + "/run.json"):
            if path.is_file():
                return path
    except OSError:
        return None
    return None


def _commit_shas(commits: object) -> set[str]:
    if not isinstance(commits, list):
        return set()
    return {c["sha"] for c in commits
            if isinstance(c, dict) and isinstance(c.get("sha"), str) and c["sha"]}


commit_shas = _commit_shas  # public name for callers outside this module


def read_snapshot(run_id: object, workspace_root: object) -> "tuple[Path, list] | None":
    """``(run.json path, its commits list)`` read ONCE, before the surface batch is
    built, so the marker names only commits whose rows could be in that batch (a
    commit landing during the build is left out and the gate blocks: fail closed).
    None for an unsafe id, a missing run, or an unreadable run.json."""
    run_json = find_run_json(run_id, workspace_root)
    if run_json is None:
        return None
    try:
        state = json.loads(run_json.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(state, dict):
        return None
    commits = state.get("commits")
    return run_json, (commits if isinstance(commits, list) else [])


def write_marker(run_json: object, events: object, commits: object = None) -> bool:
    """Write the marker beside ``run_json`` when ``events`` hold at least one
    ``kind == "source-final"`` row. Returns True when written. Never raises: a
    failure only means the gate falls back to the agent's ``--outputs-surfaced``.

    ``commits`` is the snapshot from :func:`read_snapshot`, taken before the batch was
    built; when omitted, run.json is read now (callers that built nothing first)."""
    try:
        rows = {e.get("path") for e in (events or [])
                if isinstance(e, dict) and e.get("kind") == "source-final" and e.get("path")}
        if not rows:
            return False
        path = Path(str(run_json))
        state = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(state, dict):
            return False
        payload = {
            "run_id": state.get("id"),
            "source_rows": len(rows),
            "commits": sorted(_commit_shas(state.get("commits") if commits is None else commits)),
            "at": datetime.now(timezone.utc).isoformat(),
        }
        # mkstemp, not a pid-based name: the daemon writes from worker threads, so two
        # surface calls for one run can overlap inside a single process.
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{MARKER_NAME}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(payload, indent=2))
            os.replace(tmp, path.parent / MARKER_NAME)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        return True
    except (OSError, ValueError, TypeError, AttributeError):
        return False


def marker_covers(run_dir: object, commits: object) -> bool:
    """True when the run dir holds a marker with at least one source row that names
    every commit in ``commits`` (the run's current ``commits``). Never raises."""
    try:
        data = json.loads((Path(str(run_dir)) / MARKER_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return False
    if not isinstance(data, dict):
        return False
    rows = data.get("source_rows")
    if not isinstance(rows, int) or isinstance(rows, bool) or rows < 1:
        return False
    marked = data.get("commits")
    if not isinstance(marked, list):
        return False
    return _commit_shas(commits) <= {s for s in marked if isinstance(s, str)}
