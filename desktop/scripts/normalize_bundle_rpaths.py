#!/usr/bin/env python3
"""normalize_bundle_rpaths — make a Linux PyInstaller bundle look finished to linuxdeploy.

Run by build-backend.sh on Linux, after PyInstaller and before the install manifest is
written (write_install_manifest.py hashes every file of the bundle).

Why: the AppImage bundler (linuxdeploy) treats every ELF file under the AppDir's usr/lib,
which is where Tauri puts our resources, as its own:
- it resolves each file's dependencies with ldd and fails the whole bundle when one is not
  found ("Could not find dependency"). A PyInstaller bundle can carry such a file: a wheel's
  library copied to _internal/ while the library it needs stays in the wheel's own
  ``<pkg>.libs/`` directory, found at run time only because the loader already has it;
- it then calls ``patchelf --set-rpath`` on each file, with the file's RUNPATH plus
  ``$ORIGIN`` when that is missing. A changed file no longer matches the sha256 the install
  manifest recorded, so the app's runtime deploy refuses the backend on first start.

So each ELF file gets, before the manifest is written, exactly the RUNPATH linuxdeploy will
compute: its current entries, an ``$ORIGIN/<dir>`` entry for each direct dependency that ldd
cannot find but the bundle holds, and ``$ORIGIN``. linuxdeploy then finds every dependency,
and its own ``--set-rpath`` call sets the same value, which patchelf leaves untouched (it
writes nothing when the RUNPATH is unchanged; a DT_RPATH is turned into DT_RUNPATH here
already, by our own call).

Usage: normalize_bundle_rpaths.py <bundle_dir>
Exit 1 when a direct dependency is found neither by ldd nor in the bundle, or a tool fails.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable, Iterable

Runner = Callable[[list], str]

ORIGIN = "$ORIGIN"


def _run(cmd: list) -> str:
    return subprocess.run(cmd, check=True, capture_output=True, text=True).stdout


def is_elf(path: Path) -> bool:
    try:
        with open(path, "rb") as fh:
            return fh.read(4) == b"\x7fELF"
    except OSError:
        return False


def elf_files(bundle: Path) -> list[Path]:
    """Regular ELF files of the bundle; symlinks are skipped (their targets are listed)."""
    return sorted(p for p in bundle.rglob("*")
                  if p.is_file() and not p.is_symlink() and is_elf(p))


def not_found(ldd_output: str) -> set[str]:
    """Library names ldd reports as ``<name> => not found``."""
    out = set()
    for line in ldd_output.splitlines():
        left, sep, right = line.strip().partition("=>")
        if sep and right.strip() == "not found":
            out.add(left.strip())
    return out


def rpath_entry(file: Path, found_dir: Path) -> str:
    rel = os.path.relpath(found_dir, file.parent)
    return ORIGIN if rel == "." else f"{ORIGIN}/{Path(rel).as_posix()}"


def plan(file: Path, needed: Iterable[str], rpath: str, missing: set[str],
         by_name: dict[str, list[Path]]) -> tuple[str, list[str]]:
    """The RUNPATH *file* should carry, and the direct dependencies the bundle lacks."""
    entries = [e for e in rpath.split(":") if e]
    lacking = []
    for name in needed:
        if name not in missing:
            continue
        # the nearest copy first: its own directory, then the fewest steps away
        holders = sorted(by_name.get(name, []),
                         key=lambda h: (len(Path(os.path.relpath(h.parent, file.parent)).parts),
                                        str(h)))
        if not holders:
            lacking.append(name)
            continue
        entry = rpath_entry(file, holders[0].parent)
        if entry not in entries:
            entries.append(entry)
    if ORIGIN not in entries:
        entries.append(ORIGIN)  # where linuxdeploy appends it
    return ":".join(entries), lacking


def normalize(bundle: Path, run: Runner = _run) -> list[str]:
    """Normalize every ELF file of *bundle*; returns the problems found (empty = done)."""
    files = elf_files(bundle)
    by_name: dict[str, list[Path]] = {}
    for p in bundle.rglob("*"):
        if p.is_file():
            by_name.setdefault(p.name, []).append(p)
    problems = []
    for f in files:
        try:
            needed = run(["patchelf", "--print-needed", str(f)]).split()
            rpath = run(["patchelf", "--print-rpath", str(f)]).strip()
            try:
                missing = not_found(run(["ldd", str(f)]))
            except subprocess.CalledProcessError:
                missing = set()  # "not a dynamic executable": nothing for ldd to resolve
            value, lacking = plan(f, needed, rpath, missing, by_name)
            for name in lacking:
                problems.append(f"{f.relative_to(bundle)}: needs {name}, "
                                "which neither the system nor the bundle has")
            run(["patchelf", "--set-rpath", value, str(f)])
        except subprocess.CalledProcessError as e:
            problems.append(f"{f.relative_to(bundle)}: {' '.join(e.cmd[:2])} failed: "
                            f"{(e.stderr or '').strip()}")
    return problems


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: normalize_bundle_rpaths.py <bundle_dir>", file=sys.stderr)
        return 2
    for tool in ("patchelf", "ldd"):
        if shutil.which(tool) is None:
            print(f"normalize_bundle_rpaths: {tool} is required on Linux "
                  "(apt install patchelf)", file=sys.stderr)
            return 1
    bundle = Path(argv[1])
    problems = normalize(bundle)
    for p in problems:
        print(f"normalize_bundle_rpaths: {p}", file=sys.stderr)
    if problems:
        return 1
    print(f"normalize_bundle_rpaths: {len(elf_files(bundle))} ELF files ready for linuxdeploy")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
