"""Skill scripts can import what they import.

- every top-level import name in a skill script (tests excluded) resolves to the standard
  library, a module or package inside the same skill, a backend package, an installed
  distribution, or a declared ``python-optional-deps`` entry. Before this, two scripts imported
  a module that does not exist in their skill (web-design-review's search.py -> design_system,
  pollinate's migrate_prefs.py -> learn_design) and failed on every run.
"""
import ast
import importlib.metadata as md
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
