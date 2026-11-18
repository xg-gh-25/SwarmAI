"""Pin GoogleChrome/modern-web-guidance's guides into s_frontend-design.

The skill reads the guides from its own data directory
(``backend/skills/s_frontend-design/data/modern-web-guidance/``), so it works offline,
on every platform and without Node, and runs no remote code. This script is how that
directory is refreshed: it copies the guides of a local checkout at one commit, verbatim,
with the upstream license, and writes the ``PROVENANCE.json`` that
``core.app_provenance.load_verified_provenance`` verifies (every file's sha256, and no
undeclared file in the directory).

Usage (from backend/):
    git clone --depth 1 https://github.com/GoogleChrome/modern-web-guidance.git <dir>
    python scripts/vendor_modern_web_guidance.py --src <dir>

The checkout is read as data: nothing in it is executed or imported. The commit is read
from ``<dir>/.git`` with ``git rev-parse HEAD``. ``ADAPTATION.md`` in the target is
SwarmAI's own file and is kept as it is.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

REPOSITORY = "https://github.com/GoogleChrome/modern-web-guidance"
SOURCE_ID = "modern-web-guidance"
UPSTREAM_GUIDES = Path("skills/modern-web-guidance/guides")
TARGET = (Path(__file__).resolve().parents[1]
          / "skills/s_frontend-design/data/modern-web-guidance")
ADAPTATION = "ADAPTATION.md"
ADAPTATION_NOTE = ("SwarmAI's search/retrieve script over the pinned guides and the "
                   "INSTRUCTIONS.md section that tells the agent when to use them.")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _commit(src: Path) -> str:
    out = subprocess.run(["git", "-C", str(src), "rev-parse", "HEAD"],
                         capture_output=True, text=True, check=True, timeout=30)
    commit = out.stdout.strip()
    if len(commit) != 40:
        raise SystemExit(f"not a commit id: {commit!r}")
    return commit


def vendor(src: Path, target: Path = TARGET) -> dict:
    """Replace the guides in *target* with those of *src* and write PROVENANCE.json."""
    guides = src / UPSTREAM_GUIDES
    if not guides.is_dir():
        raise SystemExit(f"no guides directory at {guides}")
    if any(p.is_symlink() for p in guides.rglob("*")):
        raise SystemExit("the upstream guides contain a symlink; refusing to copy it")
    commit = _commit(src)
    target.mkdir(parents=True, exist_ok=True)
    if (target / "guides").exists():
        shutil.rmtree(target / "guides")
    files = []
    for path in sorted(p for p in guides.rglob("*.md") if p.is_file()):
        rel = path.relative_to(guides)
        dest = target / "guides" / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, dest)
        files.append({"source": SOURCE_ID,
                      "upstreamPath": (UPSTREAM_GUIDES / rel).as_posix(),
                      "localPath": f"guides/{rel.as_posix()}", "sha256": _sha(dest)})
    shutil.copyfile(src / "LICENSE", target / "LICENSE.upstream")
    files.append({"source": SOURCE_ID, "upstreamPath": "LICENSE",
                  "localPath": "LICENSE.upstream", "sha256": _sha(target / "LICENSE.upstream")})
    if not (target / ADAPTATION).is_file():
        raise SystemExit(f"{target / ADAPTATION} is missing; write it before vendoring")
    provenance = {
        "schemaVersion": 1,
        "sources": [{"id": SOURCE_ID, "repository": REPOSITORY, "commit": commit,
                     "license": "Apache-2.0"}],
        "files": files,
        "modifications": [{"localPath": ADAPTATION, "description": ADAPTATION_NOTE}],
    }
    (target / "PROVENANCE.json").write_text(json.dumps(provenance, indent=2) + "\n",
                                           encoding="utf-8")
    return {"commit": commit, "guides": len(files) - 1, "target": str(target)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--src", required=True, type=Path,
                        help="a local checkout of modern-web-guidance")
    args = parser.parse_args(argv)
    print(json.dumps(vendor(args.src.resolve())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
