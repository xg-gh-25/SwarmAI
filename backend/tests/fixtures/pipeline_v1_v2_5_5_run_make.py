"""Record a bugfix run that the v2.5.5 backend drove past TEST, for the upgrade replay test.

The fixture ``pipeline_v1_v2_5_5_run/`` is what an install upgraded from 2.5.5 finds on
disk: the run's event log, blobs and contract as 2.5.5 wrote them, and the workspace's
run folder. tests/test_pipeline_v1_upgrade_replay.py reads it with the current code.

Regenerate (only when the fixture must change; it records what 2.5.5 wrote, so the
same release's code must write it)::

    mkdir /tmp/v255 && git archive ad726dd41 backend | tar -x -C /tmp/v255
    cd /tmp/v255/backend && python <this file> /tmp/v255/backend <fixture dir>

ad726dd41 is the commit "release: v2.5.5". The repo's one commit gets a fixed date
(``GIT_DATE``), so the test can rebuild the same base commit the log names.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

GIT_DATE = "2026-11-16T12:00:00+00:00"
ROOT = "<ROOT>"


def main(backend: Path, out: Path) -> None:
    sys.path.insert(0, str(backend))
    os.chdir(backend)
    os.environ["GIT_AUTHOR_DATE"] = os.environ["GIT_COMMITTER_DATE"] = GIT_DATE
    from _pytest.monkeypatch import MonkeyPatch
    from tests.pipeline_v1_rig import Rig

    tmp = Path(tempfile.mkdtemp(prefix="v255run_"))
    rig = Rig(tmp, MonkeyPatch(), profile="bugfix")
    rig.create()
    state = rig.drive(until="deliver")
    assert state["stages"]["test"] == "completed", state
    run_id = rig.run_id
    audit = rig.data / "state" / "pipeline_agent_audit"
    if out.exists():
        shutil.rmtree(out)
    skip = shutil.ignore_patterns("*.lock", "*.marker")
    shutil.copytree(audit / "runs" / run_id, out / "audit" / "runs" / run_id, ignore=skip)
    shutil.copytree(audit / "sessions", out / "audit" / "sessions", ignore=skip)
    shutil.copytree(rig.ws / "Projects" / "P" / ".artifacts" / "runs" / run_id,
                    out / "workspace_run", ignore=skip)
    # the files that name the rig's temporary root (the repo, the worktree) name
    # <ROOT> instead; the test puts its own root there (same layout below it). The
    # event log is left as written: it is hash-chained
    for path in (out / "audit" / "runs" / run_id / "contract.json",
                 out / "workspace_run" / "run.json"):
        text = path.read_text(encoding="utf-8")
        path.write_text(text.replace(str(tmp), ROOT), encoding="utf-8")
    # run.json's projection_of names the log's tail hash, which the repo's secret scan
    # reads as a secret; it only marks which log the projection was made from, and the
    # reader projects the run again, so it is left out
    projection = out / "workspace_run" / "run.json"
    value = json.loads(projection.read_text(encoding="utf-8"))
    value.pop("projection_of", None)
    projection.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (out / "fixture.json").write_text(json.dumps({"run_id": run_id, "git_date": GIT_DATE,
                                                  "written_by": "v2.5.5 (ad726dd41)"}) + "\n",
                                      encoding="utf-8")
    shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
