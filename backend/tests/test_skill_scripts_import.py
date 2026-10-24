"""Skill scripts can import what they import, and the two that could not now run.

- every top-level import name in a skill script (tests excluded) resolves to the standard
  library, a module or package inside the same skill, a backend package, an installed
  distribution, or a declared ``python-optional-deps`` entry. Before this, two scripts imported
  a module that does not exist in their skill (web-design-review's search.py -> design_system,
  pollinate's migrate_prefs.py -> learn_design) and failed on every run.
- web-design-review's search.py answers a UX query (exit 0, ux-guidelines.csv).
- pollinate's migrate_prefs.py creates, migrates (v1.1 string voice, v1.2 bool progressBar),
  keeps user values, stamps the template version, and is a no-op when current.
"""
import ast
import importlib.metadata as md
import json
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
SKILLS = BACKEND / "skills"
sys.path.insert(0, str(BACKEND))
from scripts.skill_python_deps import declared  # noqa: E402

_BACKEND_PKGS = {p.name for p in BACKEND.iterdir() if p.is_dir() and (p / "__init__.py").exists()}
_BACKEND_PKGS |= {p.stem for p in BACKEND.glob("*.py")}
_DISTS = md.packages_distributions()


def _is_test(p: Path) -> bool:
    return "tests" in p.parts or p.name.startswith("test_") or p.name == "conftest.py"


def _skill_local_names(skill: Path) -> set[str]:
    names = set()
    for p in skill.rglob("*"):
        if "node_modules" in p.parts:
            continue
        if p.suffix == ".py":
            names.add(p.stem)
        elif p.is_dir() and any(p.rglob("*.py")):   # packages, namespace packages included
            names.add(p.name)
    return names


def test_every_skill_script_import_resolves():
    decl = declared(SKILLS)
    bad = []
    for skill in sorted(d for d in SKILLS.iterdir() if (d / "SKILL.md").exists()):
        local = _skill_local_names(skill)
        optional = set(decl.get(skill.name, ([], []))[1])
        for script in sorted(skill.rglob("*.py")):
            if "node_modules" in script.parts or _is_test(script):
                continue
            for node in ast.walk(ast.parse(script.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Import):
                    mods = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    mods = [node.module]
                else:
                    continue
                for m in mods:
                    top = m.split(".")[0]
                    if (top in sys.stdlib_module_names or top in local or top in _BACKEND_PKGS
                            or top in _DISTS or top in optional):
                        continue
                    bad.append(f"{script.relative_to(SKILLS)}: import {m}")
    assert not bad, "imports nothing provides:\n" + "\n".join(sorted(set(bad)))


def _run(script: Path, *args):
    return subprocess.run([sys.executable, str(script), *args], capture_output=True, text=True,
                          timeout=60, stdin=subprocess.DEVNULL)


def test_web_design_review_search_answers_a_ux_query():
    script = SKILLS / "s_web-design-review" / "scripts" / "search.py"
    r = _run(script, "touch target accessibility", "--domain", "ux", "--json")
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["domain"] == "ux" and out["file"] == "ux-guidelines.csv" and out["count"] >= 1


def test_pollinate_migrate_prefs(tmp_path):
    script = SKILLS / "s_pollinate" / "scripts" / "migrate_prefs.py"
    template = json.loads((SKILLS / "s_pollinate" / "user_prefs.template.json").read_text())
    prefs = tmp_path / "user_prefs.json"

    r = _run(script, "--prefs", str(prefs))
    assert r.returncode == 0, r.stderr
    assert json.loads(prefs.read_text()) == template

    old = {"version": "1.1", "global": {"tts": {"voice": "zh-CN-YunxiNeural", "rate": "+9%"},
                                        "visual": {"progressBar": False}}}
    prefs.write_text(json.dumps(old))
    r = _run(script, "--prefs", str(prefs), "--dry-run")
    assert r.returncode == 0 and json.loads(prefs.read_text()) == old   # dry run writes nothing
    r = _run(script, "--prefs", str(prefs))
    assert r.returncode == 0, r.stderr
    got = json.loads(prefs.read_text())
    assert got["version"] == template["version"]
    tts = got["global"]["tts"]
    assert "voice" not in tts and tts["voices"]["edge"] == "zh-CN-YunxiNeural"
    assert tts["rate"] == "+9%"                                          # user value kept
    assert got["global"]["visual"]["progressBar"]["enabled"] is False
    assert set(template) <= set(got)                                   # new fields filled in

    before = prefs.read_text()
    r = _run(script, "--prefs", str(prefs))
    assert r.returncode == 0 and "no migration needed" in r.stdout and prefs.read_text() == before
    assert not list(tmp_path.glob(".user_prefs.*"))                     # no temp file left
