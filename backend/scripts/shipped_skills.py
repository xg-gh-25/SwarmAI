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
one, a failing ``git ls-files`` raises: a silent fallback to the directory walk would put the
ignored skills back into the package.

- ``shipped_files(skills_root)`` -> sorted list of file paths, each starting with
  ``skills_root`` as given (the PyInstaller spec uses them as data sources).
- ``shipped_skill_names(skills_root)`` -> set of top-level skill directory names.

Deliberately imports nothing from the backend: the PyInstaller spec in
desktop/scripts/build-backend.sh loads it by path.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

_SKIP_DIRS = {"node_modules", "__pycache__"}


def _skipped(rel_parts: tuple[str, ...]) -> bool:
    return any(part in _SKIP_DIRS for part in rel_parts)


def _in_work_tree(root: Path) -> bool:
    try:
        out = subprocess.run(["git", "-C", str(root), "rev-parse", "--is-inside-work-tree"],
                             capture_output=True, text=True, timeout=30)
    except FileNotFoundError:  # no git on this machine: treat like a tarball
        return False
    return out.returncode == 0 and out.stdout.strip() == "true"


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


if __name__ == "__main__":
    import sys
    print("\n".join(shipped_files(sys.argv[1] if len(sys.argv) > 1 else "skills")))
