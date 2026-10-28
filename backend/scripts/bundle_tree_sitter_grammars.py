"""Fetch the tree-sitter grammars the packaged app ships, at BUILD time.

Usage (from backend/): ``python scripts/bundle_tree_sitter_grammars.py <out dir>``

Writes the grammar library of every language in
``core.code_intel.parser.tree_sitter_languages()`` — and only those — into *out dir*
(normally ``generated/tree_sitter_grammars``, which desktop/scripts/build-backend.sh
packages as ``tree_sitter_grammars``). At runtime ``core.code_intel.grammars`` points the
library at that directory, so the app never downloads a grammar: the library's own
download holds the GIL and stops the whole process (a fresh-HOME startup froze 192 s on
it, 2026-10-28; the download alone took 125 s).

How: the library's ``download`` into a throw-away cache (its cache_dir is a directory of
grammar libraries, with the release manifest and the platform bundle next to it). When
this machine's own cache already holds the bundle for this library version, it is copied
in first, so the extraction is local and a second build needs no network. Each bundle is
checked against the sha256 the release manifest records. Exit 1 when a language is
missing afterwards (asked of the library, ``downloaded_languages``) or a bundle does not
match: a build must not ship a partial or corrupt set.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _check_bundles(root: Path) -> None:
    bundles = sorted((root / "bundles").glob("*.tar.zst")) if (root / "bundles").is_dir() else []
    if not bundles:
        return
    manifest = root / "manifest.json"
    if not manifest.is_file():
        raise SystemExit(f"tree-sitter bundle without a release manifest in {root}")
    known = {v.get("sha256") for v in json.loads(manifest.read_text()).get("platforms", {}).values()}
    for b in bundles:
        if hashlib.sha256(b.read_bytes()).hexdigest() not in known:
            raise SystemExit(f"tree-sitter bundle {b.name} does not match the release manifest")


def bundle(out: Path) -> list[str]:
    """Fill *out* with the grammar libraries; returns the languages written."""
    import tree_sitter_language_pack as tslp
    from core.code_intel.parser import tree_sitter_languages

    langs = tree_sitter_languages()
    own = Path(tslp.cache_dir()).parent          # this machine's cache for this version
    with tempfile.TemporaryDirectory(prefix="tslp-build-") as tmp:
        root = Path(tmp)
        libs = root / "libs"
        libs.mkdir()
        if (own / "manifest.json").is_file() and list((own / "bundles").glob("*.tar.zst")):
            _check_bundles(own)
            shutil.copy2(own / "manifest.json", root / "manifest.json")
            shutil.copytree(own / "bundles", root / "bundles")
        tslp.configure(tslp.PackConfig(cache_dir=str(libs)))
        tslp.download(langs)
        _check_bundles(root)
        missing = sorted(set(langs) - set(tslp.downloaded_languages()))
        if missing:
            raise SystemExit(f"tree-sitter grammars missing after download: {', '.join(missing)}")
        if out.exists():
            shutil.rmtree(out)
        shutil.copytree(libs, out)
    return langs


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: bundle_tree_sitter_grammars.py <out dir>")
    langs = bundle(Path(sys.argv[1]))
    print(f"tree-sitter grammars bundled: {', '.join(langs)}")
