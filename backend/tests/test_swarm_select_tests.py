"""Tests for backend/scripts/swarm_select_tests.py, SwarmAI's own test selector.

What is tested (the selection half of the retired pipeline_test.py runner, kept as a
repo tool, design usable-by-anyone §3.3.3): direct importers of a changed module are
selected and unrelated tests are not; a test that runs a changed script by
subprocess is selected, 2-hop importers only with --depth 2; a deleted module's
importers still run; any backend module change selects the source-scan suites, and
that list covers every test that globs *.py; validations.json entries are chosen by
their path patterns; the CLI reads the change from git.

Methodology: tiny real git repos under tmp_path; the CLI runs as a subprocess.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "scripts"))
import swarm_select_tests as st  # noqa: E402

BASE = {
    "backend/core/__init__.py": "",
    "backend/core/mod.py": "def add(a, b):\n    return a + b\n",
    "backend/core/b.py": "def val():\n    return 1\n",
    "backend/core/c.py": "from core.b import val\n\n\ndef twice():\n    return 2 * val()\n",
    "backend/tests/test_x.py": "from core.mod import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n",
    "backend/tests/test_a.py": "from core.c import twice\n\n\ndef test_t():\n    assert twice() == 2\n",
    "backend/tests/test_unrelated.py": "def test_u():\n    assert True\n",
}


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                          text=True).stdout


def _write(repo: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "t@e.com")
    _git(root, "config", "user.name", "t")
    _git(root, "config", "core.hooksPath", "/dev/null")
    _write(root, BASE)
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "base")
    return root


def _select(repo: Path, changed: list[str], depth: int = 1) -> list[str]:
    cache = repo.parent / "cache"
    sgraph = st.build_source_graph(repo, cache)
    graph = st.build_import_graph(repo, cache, sgraph)
    return st.select_backend(repo, changed, graph, sgraph=sgraph, depth=depth)[0]


def test_direct_importers_are_selected_and_unrelated_tests_are_not(repo):
    sel = _select(repo, ["backend/core/mod.py"])
    assert "backend/tests/test_x.py" in sel
    assert "backend/tests/test_unrelated.py" not in sel and "backend/tests/test_a.py" not in sel


def test_a_script_run_by_subprocess_is_selected_and_depth_adds_the_next_hop(repo):
    _write(repo, {
        "backend/scripts/tool.py": "print(1)\n",
        "backend/tests/test_tool_cli.py": (
            "import subprocess, sys\nfrom pathlib import Path\n\n\ndef test_cli():\n"
            "    p = Path(__file__).parents[1] / 'scripts' / 'tool.py'\n"
            "    assert subprocess.run([sys.executable, str(p)]).returncode == 0\n"),
    })
    assert "backend/tests/test_tool_cli.py" in _select(repo, ["backend/scripts/tool.py"])
    assert "backend/tests/test_a.py" not in _select(repo, ["backend/core/b.py"])
    assert "backend/tests/test_a.py" in _select(repo, ["backend/core/b.py"], depth=2)


def test_a_deleted_modules_importers_still_run(repo):
    (repo / "backend/core/mod.py").unlink()
    assert "backend/tests/test_x.py" in _select(repo, ["backend/core/mod.py"])


def test_a_backend_module_change_selects_the_source_scan_suites(repo):
    scan = st._SOURCE_SCAN_SUITES[0]
    _write(repo, {scan: "def test_scan():\n    assert True\n"})
    assert scan in _select(repo, ["backend/core/b.py"])
    assert scan not in _select(repo, ["backend/tests/test_x.py"])


def test_source_scan_suites_cover_every_test_that_globs_python_files():
    """Every test file that globs *.py is listed or exempted; every listed file exists."""
    pat = re.compile(r"""\.r?glob\(\s*['"](\*\*/)?\*\.py['"]""")
    found = {f"backend/tests/{p.name}" for p in (BACKEND / "tests").glob("test_*.py")
             if pat.search(p.read_text(encoding="utf-8", errors="ignore"))}
    listed = set(st._SOURCE_SCAN_SUITES) | set(st._NOT_SOURCE_SCANS)
    assert found - listed == set(), "add to _SOURCE_SCAN_SUITES or _NOT_SOURCE_SCANS"
    assert [t for t in listed if not (BACKEND.parent / t).is_file()] == []


def test_validations_are_chosen_by_path_pattern(repo):
    _write(repo, {"validations.json": json.dumps({"schema": 1, "entries": [
        {"id": "py", "paths": ["backend/**/*.py"], "argv": ["python", "lint.py"]},
        {"id": "ts", "paths": ["desktop/**/*.ts"], "argv": ["npx", "tsc"]}]})})
    assert [e["id"] for e in st.validations_for(repo, ["backend/core/mod.py"])] == ["py"]
    assert [e["id"] for e in st.validations_for(repo, ["backend/x.py"])] == ["py"]
    assert st.validations_for(repo, ["README.md"]) == []


def test_the_cli_reads_the_change_from_git(repo):
    _git(repo, "checkout", "-q", "-b", "work")
    _write(repo, {"backend/core/mod.py": "def add(a, b):\n    return b + a\n"})
    out = subprocess.run([sys.executable, str(BACKEND / "scripts" / "swarm_select_tests.py"),
                          "--json"], cwd=repo, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    data = json.loads(out.stdout)
    assert data["changed"] == ["backend/core/mod.py"]
    assert "backend/tests/test_x.py" in data["selected"]
