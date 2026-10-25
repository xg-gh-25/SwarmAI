"""S6-5a (design 2026-10-24-swarmai-usable-by-anyone.md §3.2): the pipeline CLI has one
command form, ``"$SWARM_BACKEND_BIN" pipeline-v1 ...``.

What is tested:
- the ``backend/scripts/pipeline_v1.py`` script and ``utils/source_repo.py`` are gone;
- the recogniser does not treat ``python …/pipeline_v1.py`` as a pipeline call;
- no product file (core, hooks, routers, jobs, skills, context, templates) tells the
  agent to run the script form, and the build no longer records ``source_repo.json``;
- the CLI parser is reachable as ``core.run_protocol.cli_main`` (the only entry).
"""
from __future__ import annotations

import re
from pathlib import Path

from core import pipeline_command

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent


def test_the_script_and_source_repo_are_gone():
    assert not (BACKEND / "scripts" / "pipeline_v1.py").exists()
    assert not (BACKEND / "utils" / "source_repo.py").exists()
    from core.run_protocol import cli_main
    assert cli_main.parser().prog == "pipeline-v1"


def test_the_script_form_is_not_a_call():
    for command in ("python backend/scripts/pipeline_v1.py status --run-id run_a",
                    "/repo/.venv/bin/python3 -u /x/pipeline_v1.py land --run-id run_a"):
        assert pipeline_command.calls(command)[0] == [], command
    call = pipeline_command.calls('"$SWARM_BACKEND_BIN" pipeline-v1 status --run-id run_a')[0]
    assert call and call[0]["form"] == "program"


_RUN_SCRIPT = re.compile(r"python[\w.]*\s+\S*pipeline_v1\.py|scripts/pipeline_v1\.py")


def test_no_product_text_tells_the_agent_to_run_the_script():
    roots = ["core", "hooks", "routers", "jobs", "skills", "context", "templates", "channels"]
    hits = []
    for root in roots:
        for path in (BACKEND / root).rglob("*"):
            if path.suffix not in (".py", ".md") or "__pycache__" in path.parts:
                continue
            for n, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                if _RUN_SCRIPT.search(line):
                    hits.append(f"{path.relative_to(BACKEND)}:{n}")
    assert not hits, hits
    build = (ROOT / "desktop" / "scripts" / "build-backend.sh").read_text(encoding="utf-8")
    assert "source_repo" not in build
