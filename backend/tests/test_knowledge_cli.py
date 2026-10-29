"""``python-backend knowledge``: works from any cwd, prints whole units with path:line and
id (knowledge addressing §2.4, §6.3 test 5)."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

from core import knowledge_cli

BACKEND = Path(__file__).resolve().parents[1]


@pytest.fixture
def ws(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("SWARMWS", str(tmp_path))
    und = tmp_path / "Projects" / "P" / "2-understanding"
    und.mkdir(parents=True)
    (und / "TECH.md").write_text(
        "# TECH\n\n## Traps\n\n- [pitfall] **Quokka rule** — never feed quokkas after midnight.\n"
        "  <!-- ref:0 | last:none | decay:active | source:manual -->\n", encoding="utf-8")
    return tmp_path


def test_find_prints_whole_unit_with_location_and_id(ws, capsys):
    assert knowledge_cli.main(["find", "--project", "P", "--query", "quokkas midnight"]) == 0
    out = capsys.readouterr().out
    from core.addressing import find
    u = find("P", "quokkas midnight").units[0]
    assert f"- `{u.id}` · {u.path}:{u.line}\n<recalled>\n§ Traps\n" in out
    assert "never feed quokkas after midnight." in out and out.rstrip().endswith("</recalled>")


def test_json_has_one_envelope_for_ddd_and_library(ws, capsys):
    assert knowledge_cli.main(["find", "--project", "P", "--query", "quokkas", "--json"]) == 0
    import json
    d = json.loads(capsys.readouterr().out)
    assert set(d) == {"units", "omitted", "skipped"} and d["units"][0]["line"] >= 1
    assert knowledge_cli.main(["find", "--in", "library", "--query", "quokkas", "--json"]) == 0
    assert set(json.loads(capsys.readouterr().out)) == {"units", "omitted", "skipped"}


def test_get_by_id_and_unknown(ws, capsys):
    from core.addressing import find
    u = find("P", "quokkas").units[0]
    assert knowledge_cli.main(["get", "--project", "P", u.id]) == 0
    assert "Quokka rule" in capsys.readouterr().out
    assert knowledge_cli.main(["get", "--project", "P", "deadbeef"]) == 1


def test_unknown_project_and_nothing_found(ws, capsys):
    assert knowledge_cli.main(["find", "--project", "Nope", "--query", "x"]) == 1
    assert knowledge_cli.main(["find", "--project", "P", "--query", "zzzunmatched"]) == 0
    assert "nothing matches" in capsys.readouterr().out


def test_map_and_projects(ws, capsys):
    assert knowledge_cli.main(["map", "--project", "P"]) == 0
    assert "TECH.md" in capsys.readouterr().out
    assert knowledge_cli.main(["map", "--projects"]) == 0
    assert "P: " in capsys.readouterr().out


def test_from_another_repo_cwd_it_never_imports_that_repos_modules(ws, tmp_path):
    repo = tmp_path / "other_repo"
    (repo / "core").mkdir(parents=True)
    (repo / "scripts").mkdir()
    for pkg in ("core", "scripts"):
        (repo / pkg / "__init__.py").write_text("print('HIJACKED')\n", encoding="utf-8")
    (repo / "core" / "addressing.py").write_text("print('HIJACKED')\n", encoding="utf-8")
    env = {**os.environ, "SWARM_WORKSPACE": str(ws), "SWARMWS": str(ws)}
    proc = subprocess.run(
        [sys.executable, str(BACKEND / "desktop_main.py"), "knowledge", "find",
         "--project", "P", "--query", "quokkas"],
        cwd=repo, env=env, capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    assert "HIJACKED" not in proc.stdout + proc.stderr
    assert "Quokka rule" in proc.stdout


def test_find_and_get_record_the_cdf_units_they_show(ws, tmp_path, monkeypatch, capsys):
    """The knowledge CLI is the main way the DDD is read, so its reads feed CDF's usage
    measure. Matching the shown text against the parsed units' bytes matched nothing on
    real files (the shown text is not byte-identical); the unit's lines are what match."""
    monkeypatch.setenv("SWARM_DATA_DIR", str(tmp_path / "data"))
    from core.addressing import find
    from core.ingestion.store import connect_ro
    assert knowledge_cli.main(["find", "--project", "P", "--query", "quokkas midnight"]) == 0
    u = find("P", "quokkas midnight").units[0]
    assert knowledge_cli.main(["get", "--project", "P", u.id]) == 0
    capsys.readouterr()
    conn = connect_ro()
    rows = conn.execute("SELECT anchor, kind FROM unit_usage ORDER BY kind").fetchall()
    conn.close()
    kinds = [kind for _anchor, kind in rows]
    assert kinds == ["read_by_agent", "recall_injected"], rows
    assert all("TECH.md" in anchor for anchor, _kind in rows)
