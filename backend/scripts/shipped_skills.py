"""Which skill files go into the packaged backend (design
docs/design/2026-10-25-skill-listing-recall-and-cleanup.md, S2: the package carries only the
skills the repo ships).

A skill directory that the checkout's ``.gitignore`` excludes (a user's own local skills, such
as internal-only ones kept next to the product skills) is not part of the product: building
from that checkout must not put it into the installer. So when the skills directory is inside a
git work tree, the file list comes from git: tracked files plus untracked files that are not
ignored (a skill being written but not yet committed still ships in a local build), minus
files deleted from the working tree. ``node_modules`` and ``__pycache__`` are always skipped.

Outside a git work tree (a source tarball) nothing is ignored, so every file is listed. Inside
one, a failing ``git ls-files`` raises, and so does any git failure other than "not a git
repository" (git refusing the checkout, git missing inside a checkout): a silent fallback to
the directory walk would put the ignored files back into the package.

- ``shipped_files(skills_root)`` -> sorted list of file paths, each starting with
  ``skills_root`` as given (the PyInstaller spec uses them as data sources).
- ``shipped_skill_names(skills_root)`` -> set of top-level skill directory names.
- ``copy_shipped(root, dest)`` -> copies the shipped files of ``root`` into ``dest`` at the
  same relative paths (a symlink stays a symlink) and returns their relative paths.
  desktop/scripts/build-backend.sh stages the backend with it
  (``python3 backend/scripts/shipped_skills.py backend --copy-to <build dir>``), so every
  directory the PyInstaller spec packages there (the local modules, ``context``,
  ``templates``, ``skills`` ...) holds only shipped files: the build directory is a copy, not
  a git work tree, so a ``shipped_files`` call inside it lists everything it contains.

Deliberately imports nothing from the backend: the PyInstaller spec in
desktop/scripts/build-backend.sh loads it by path.

hive/release.sh applies the same rule to the whole backend tree
(``python3 backend/scripts/shipped_skills.py backend``), so the Hive package and the desktop
installer leave out the same gitignored files.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

_SKIP_DIRS = {"node_modules", "__pycache__"}


def _skipped(rel_parts: tuple[str, ...]) -> bool:
    return any(part in _SKIP_DIRS for part in rel_parts)


def _inside_git_dir(root: Path) -> bool:
    return any((d / ".git").exists() for d in (root.resolve(), *root.resolve().parents))


def _in_work_tree(root: Path) -> bool:
    """True inside a git work tree; False only for a real tarball (no ``.git`` above and no
    git, or git says it is not a repository). Any other git failure raises: git refusing
    the checkout ("dubious ownership" in a container or CI runner), a missing git inside
    a checkout, a timeout. Falling back to the walk there would ship the ignored files."""
    try:
        out = subprocess.run(["git", "-C", str(root), "rev-parse", "--is-inside-work-tree"],
                             capture_output=True, text=True, timeout=30,
                             env={**os.environ, "LC_ALL": "C"})  # untranslated messages
    except FileNotFoundError:
        if _inside_git_dir(root):
            raise RuntimeError("%s is inside a git checkout but git is not installed: cannot "
                               "tell which files the repo ships" % root)
        return False  # no git on this machine and no checkout: a tarball
    if out.returncode == 0:
        return out.stdout.strip() == "true"
    if "not a git repository" in out.stderr:
        return False
    raise RuntimeError("git cannot read %s: %s" % (root, out.stderr.strip()))


def shipped_files(skills_root: Path | str) -> list[str]:
    root = Path(skills_root)
    rels: list[Path] = []
    if _in_work_tree(root):
        out = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z", "--cached", "--others",
             "--exclude-standard", "--full-name", "--", "."],
            capture_output=True, timeout=60)
        if out.returncode != 0:
            raise RuntimeError("git ls-files failed in %s: %s"
                               % (root, out.stderr.decode(errors="replace").strip()))
        top = subprocess.run(["git", "-C", str(root), "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True, timeout=30, check=True)
        root_abs = root.resolve()
        top_abs = Path(top.stdout.strip()).resolve()
        for name in out.stdout.decode().split("\0"):
            if not name:
                continue
            rel = (top_abs / name).relative_to(root_abs)
            if (root / rel).is_file():  # tracked but deleted from the working tree: skip
                rels.append(rel)
    else:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
            for f in filenames:
                rels.append(Path(dirpath, f).relative_to(root))
    return sorted(str(root / rel) for rel in set(rels) if not _skipped(rel.parts))


def shipped_skill_names(skills_root: Path | str) -> set[str]:
    root = Path(skills_root)
    return {Path(p).relative_to(root).parts[0] for p in shipped_files(root)
            if len(Path(p).relative_to(root).parts) > 1}


def copy_shipped(root: Path | str, dest: Path | str) -> list[str]:
    root, dest = Path(root), Path(dest)
    rels = [Path(p).relative_to(root) for p in shipped_files(root)]
    for rel in rels:
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root / rel, target, follow_symlinks=False)
    return [rel.as_posix() for rel in rels]


if __name__ == "__main__":
    import sys
    args = sys.argv[1:]
    if len(args) == 3 and args[1] == "--copy-to":
        how = "git-tracked + untracked-not-ignored" if _in_work_tree(Path(args[0])) \
            else "not a git work tree: every file"
        print("[shipped] copied %d files of %s (%s)" % (len(copy_shipped(args[0], args[2])), args[0], how))
    else:
        print("\n".join(shipped_files(args[0] if args else "skills")))
