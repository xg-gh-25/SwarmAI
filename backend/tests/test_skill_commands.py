"""Commands the s_project-manager skill tells the agent to run must run (fixing-spec
2026-11-02-new-brain-setup G7c).

``"$SWARM_BACKEND_BIN" py scripts/artifact_cli.py …`` names a path relative to the working
directory, and ``backend/scripts/artifact_cli.py`` is not next to any directory the agent
works in, so the command exits 2 everywhere. The module form runs the bundled script
from any directory (desktop_main.run_py)."""
import re
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
SKILL = BACKEND / "skills" / "s_project-manager" / "SKILL.md"
_MODULE_FORM = re.compile(r"py -m scripts\.artifact_cli ([a-z][a-z-]*)")


def test_the_skill_never_runs_artifact_cli_by_a_relative_path():
    text = SKILL.read_text(encoding="utf-8")
    bad = [i for i, line in enumerate(text.splitlines(), 1) if "py scripts/artifact_cli.py" in line]
    assert bad == [], f"SKILL.md lines {bad} run artifact_cli by a relative path"


def test_every_subcommand_the_skill_names_exists(tmp_path):
    subs = sorted(set(_MODULE_FORM.findall(SKILL.read_text(encoding="utf-8"))))
    assert len(subs) >= 6
    for sub in subs:
        r = subprocess.run([sys.executable, str(BACKEND / "desktop_main.py"), "py", "-m",
                            "scripts.artifact_cli", sub, "--help"],
                           capture_output=True, text=True, timeout=60, cwd=tmp_path)
        assert r.returncode == 0, f"{sub}: {r.stderr[-400:]}"
