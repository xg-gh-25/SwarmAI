"""The package carries only the skills the repo ships (scripts/shipped_skills.py; design
docs/design/2026-10-25-skill-listing-recall-and-cleanup.md, S2).

- inside a git work tree: tracked files and untracked-but-not-ignored files ship; a gitignored
  skill, node_modules, __pycache__ and a tracked file deleted from the working tree do not;
- outside a git work tree every file ships (minus node_modules / __pycache__);
- bundled_imports() reads python-deps only from shipped skills;
- the PyInstaller spec takes its skill files from shipped_files(), not a directory walk;
- copy_shipped() (how build-backend.sh stages the whole backend) copies exactly the shipped
  files, so a gitignored local module, context file or template stays out of the package.
"""
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
sys.path.insert(0, str(BACKEND))
from scripts.shipped_skills import copy_shipped, shipped_files, shipped_skill_names  # noqa: E402
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


def test_git_refusing_the_checkout_raises_instead_of_walking(repo: Path, monkeypatch):
    """git's "dubious ownership" refusal (a checkout owned by another user) is not a tarball."""
    real = subprocess.run

    def fake(cmd, *a, **kw):
        if "rev-parse" in cmd:
            return subprocess.CompletedProcess(cmd, 128, "", "fatal: detected dubious ownership "
                                               "in repository at '/src'\n")
        return real(cmd, *a, **kw)

    monkeypatch.setattr("scripts.shipped_skills.subprocess.run", fake)
    with pytest.raises(RuntimeError, match="dubious ownership"):
        shipped_files(repo)


def test_missing_git_inside_a_checkout_raises(repo: Path, monkeypatch):
    def no_git(cmd, *a, **kw):
        raise FileNotFoundError("git")

    monkeypatch.setattr("scripts.shipped_skills.subprocess.run", no_git)
    with pytest.raises(RuntimeError, match="git is not installed"):
        shipped_files(repo)


def test_the_build_takes_skill_files_from_shipped_files():
    spec = (REPO / "desktop" / "scripts" / "build-backend.sh").read_text()
    assert "from shipped_skills import shipped_files" in spec
    assert "walk('skills')" not in spec


@pytest.fixture
def backend_repo(tmp_path: Path) -> Path:
    """A backend tree with a gitignored local module, context file and template, as a
    developer's checkout has (the rules are in the repo's own .gitignore)."""
    _git(tmp_path, "init", "-q")
    be = tmp_path / "backend"
    for rel in ("main.py", "core/__init__.py", "core/engine.py", "context/SOUL.md",
                "templates/ddd/PRODUCT.md", "skills/s_a/SKILL.md"):
        _write(be / rel)
    _write(tmp_path / ".gitignore", "\n".join([
        "backend/core/local_*.py", "backend/context/*.private.md",
        "backend/templates/ddd-internal/", "__pycache__/", ""]))
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init")
    for rel in ("core/local_tool.py", "context/USER.private.md",
                "templates/ddd-internal/PRODUCT.md", "core/__pycache__/engine.cpython-312.pyc"):
        _write(be / rel)  # gitignored
    _write(be / "core" / "new_mod.py")  # untracked, not ignored: a local build still ships it
    return be


SHIPPED_BACKEND = {"main.py", "core/__init__.py", "core/engine.py", "core/new_mod.py",
                   "context/SOUL.md", "templates/ddd/PRODUCT.md", "skills/s_a/SKILL.md"}


def _files_under(root: Path) -> set[str]:
    return {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}


def test_copy_shipped_leaves_ignored_modules_context_and_templates_out(backend_repo, tmp_path):
    dest = tmp_path / "build"
    assert set(copy_shipped(backend_repo, dest)) == SHIPPED_BACKEND
    assert _files_under(dest) == SHIPPED_BACKEND


def test_copy_cli_is_what_the_build_runs(backend_repo, tmp_path):
    """build-backend.sh runs `shipped_skills.py "$BACKEND_DIR" --copy-to "$BUILD_DIR"`."""
    dest = tmp_path / "build"
    dest.mkdir()  # mktemp -d made it
    out = subprocess.run([sys.executable, str(BACKEND / "scripts" / "shipped_skills.py"),
                          str(backend_repo), "--copy-to", str(dest)],
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert _files_under(dest) == SHIPPED_BACKEND
