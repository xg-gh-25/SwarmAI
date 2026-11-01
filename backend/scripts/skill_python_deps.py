"""Which third-party Python modules the skills need, read from each SKILL.md frontmatter
(design docs/design/2026-10-24-swarmai-usable-by-anyone.md §3.2, S3c).

A skill's scripts run with the backend program's own Python (``"$SWARM_BACKEND_BIN" py``),
which carries only what was bundled into it. So a skill declares the import names its scripts
need beyond the standard library and the backend's own packages:

    python-deps: [openpyxl, PIL]          # bundled into the backend program at build time
    python-optional-deps: [playwright]    # NOT bundled; the script fails with a clear message

- ``declared(skills_root)`` -> ``{skill_dir_name: (deps, optional_deps)}``
- ``bundled_imports(skills_root)`` -> sorted set of every ``python-deps`` name of the skills
  that go into the package (scripts/shipped_skills.py); the PyInstaller spec in
  desktop/scripts/build-backend.sh collects each one.

One source for the build and for backend/tests/test_skill_python.py. Deliberately imports
nothing from the backend (the PyInstaller spec loads it by path), only PyYAML.
"""
from __future__ import annotations

from pathlib import Path


def _frontmatter(skill_md: Path) -> dict:
    text = skill_md.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end < 0:
        return {}
    import yaml  # the build venv and the backend both carry PyYAML
    data = yaml.safe_load(text[3:end]) or {}
    return data if isinstance(data, dict) else {}


def _names(value) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [value]
    return [str(v) for v in value]


def declared(skills_root: Path | str) -> dict[str, tuple[list[str], list[str]]]:
    root = Path(skills_root)
    out: dict[str, tuple[list[str], list[str]]] = {}
    for skill_md in sorted(root.glob("*/SKILL.md")):
        meta = _frontmatter(skill_md)
        out[skill_md.parent.name] = (_names(meta.get("python-deps")),
                                     _names(meta.get("python-optional-deps")))
    return out


def bundled_imports(skills_root: Path | str) -> list[str]:
    """Every python-deps name of the skills that go into the package (shipped_skills.py)."""
    if __package__:  # imported as scripts.skill_python_deps (tests)
        from .shipped_skills import shipped_skill_names
    else:  # loaded by path (the PyInstaller spec)
        from shipped_skills import shipped_skill_names
    shipped = shipped_skill_names(skills_root)
    return sorted({name for skill, (deps, _opt) in declared(skills_root).items()
                   if skill in shipped for name in deps})


if __name__ == "__main__":
    import sys
    print("\n".join(bundled_imports(sys.argv[1] if len(sys.argv) > 1 else "skills")))
