#!/usr/bin/env python3
"""write_install_manifest — the G2 install manifest of one desktop build.

Design docs/design/2026-10-24-swarmai-usable-by-anyone.md §5.1 (G2). Run by
build-backend.sh after it stages the backend; written next to the staged bundle and
shipped as ``Contents/Resources/install-manifest.json`` (``tauri.bundle.conf.json``).
scripts/verify_installer.sh checks every listed file after a real install.

Lists, with sha256, every file the bundle must carry under Tauri's resource dir:
- ``python-backend/**`` — the staged PyInstaller onedir (the executable is required);
- ``<name>`` / ``daemon/<name>`` — desktop/resources top-level files and daemon/ files,
  mapped exactly as tauri.conf.json ``bundle.resources`` maps them.

Usage: write_install_manifest.py <stage_dir> <resources_dir> <version> <target>
  stage_dir/python-backend must exist; writes stage_dir/install-manifest.json.
Exit 1 when the backend executable is missing.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def build(stage: Path, resources: Path, version: str, target: str) -> dict:
    backend = stage / "python-backend"
    exe = "python-backend.exe" if "windows" in target else "python-backend"
    if not (backend / exe).is_file():
        raise SystemExit(f"write_install_manifest: {backend / exe} missing")
    files = []
    for root, dirs, names in os.walk(backend):
        dirs.sort()
        for name in sorted(names):
            p = Path(root) / name
            if p.is_symlink():
                continue
            files.append({"path": "python-backend/" + p.relative_to(backend).as_posix(),
                          "sha256": _sha(p)})
    for sub, prefix in ((resources, ""), (resources / "daemon", "daemon/")):
        if not sub.is_dir():
            continue
        for p in sorted(sub.iterdir()):
            if p.is_file() and not p.name.startswith(".") and not p.name.endswith(".lock"):
                files.append({"path": prefix + p.name, "sha256": _sha(p)})
    return {"schema": 1, "version": version, "target": target,
            "executable": f"python-backend/{exe}", "files": files}


def main(argv: list[str]) -> int:
    if len(argv) != 5:
        print(__doc__.split("Usage:")[1].split("\n")[0].strip(), file=sys.stderr)
        return 64
    stage, resources = Path(argv[1]), Path(argv[2])
    manifest = build(stage, resources, argv[3], argv[4])
    out = stage / "install-manifest.json"
    out.write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
    print(f"install manifest: {len(manifest['files'])} files -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
