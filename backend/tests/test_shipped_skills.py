"""The package carries only the skills the repo ships (scripts/shipped_skills.py; design
docs/design/2026-10-25-skill-listing-recall-and-cleanup.md, S2).

- inside a git work tree: tracked files and untracked-but-not-ignored files ship; a gitignored
  skill, node_modules, __pycache__ and a tracked file deleted from the working tree do not;
- outside a git work tree every file ships (minus node_modules / __pycache__);
- bundled_imports() reads python-deps only from shipped skills;
- the PyInstaller spec takes its skill files from shipped_files(), not a directory walk.
"""
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
sys.path.insert(0, str(BACKEND))
from scripts.shipped_skills import shipped_files, shipped_skill_names  # noqa: E402
from scripts.skill_python_deps import bundled_imports  # noqa: E402


def _write(path: Path, text: str = "x\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _skill(root: Path, name: str, deps: str = "") -> None:
    front = f"---\nname: {name}\n" + (f"python-deps: [{deps}]\n" if deps else "") + "---\n"
    _write(root / name / "SKILL.md", front)
    _write(root / name / "scripts" / "run.py", "print(1)\n")


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A repo whose skills live under backend/skills, like this one."""
    _git(tmp_path, "init", "-q")
    skills = tmp_path / "backend" / "skills"
    _skill(skills, "s_tracked", deps="openpyxl")
    _skill(skills, "s_gone")
    _write(skills / "s_tracked" / "node_modules" / "pkg" / "index.js")
    _write(skills / "s_tracked" / "__pycache__" / "run.cpython-312.pyc")
    _write(tmp_path / ".gitignore", "backend/skills/s_local-*/\nnode_modules/\n__pycache__/\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init")
    _skill(skills, "s_local-private", deps="secretlib")  # gitignored
    _skill(skills, "s_new")  # untracked, not ignored
    (skills / "s_gone" / "scripts" / "run.py").unlink()  # tracked, deleted on disk
    return skills


def test_git_tree_ships_tracked_and_untracked_but_not_ignored(repo: Path):
    files = {str(Path(p).relative_to(repo)) for p in shipped_files(repo)}
    assert files == {
        "s_tracked/SKILL.md", "s_tracked/scripts/run.py",
        "s_new/SKILL.md", "s_new/scripts/run.py",
        "s_gone/SKILL.md",
    }
    assert shipped_skill_names(repo) == {"s_tracked", "s_new", "s_gone"}


def test_paths_keep_the_root_as_given(repo: Path, monkeypatch):
    monkeypatch.chdir(repo.parent)
    assert "skills/s_tracked/SKILL.md" in shipped_files("skills")


def test_bundled_imports_skip_ignored_skills(repo: Path):
    assert bundled_imports(repo) == ["openpyxl"]


def test_outside_git_everything_ships(tmp_path: Path):
    skills = tmp_path / "skills"
    _skill(skills, "s_a")
    _write(skills / "s_a" / "node_modules" / "x.js")
    _write(skills / "s_a" / "__pycache__" / "y.pyc")
    files = {str(Path(p).relative_to(skills)) for p in shipped_files(skills)}
    assert files == {"s_a/SKILL.md", "s_a/scripts/run.py"}


def test_git_failure_raises_instead_of_walking(repo: Path, monkeypatch):
    real = subprocess.run

    def fake(cmd, *a, **kw):
        if "ls-files" in cmd:
            return subprocess.CompletedProcess(cmd, 128, b"", b"boom")
        return real(cmd, *a, **kw)

    monkeypatch.setattr("scripts.shipped_skills.subprocess.run", fake)
    with pytest.raises(RuntimeError, match="boom"):
        shipped_files(repo)


def test_the_build_takes_skill_files_from_shipped_files():
    spec = (REPO / "desktop" / "scripts" / "build-backend.sh").read_text()
    assert "from shipped_skills import shipped_files" in spec
    assert "walk('skills')" not in spec
