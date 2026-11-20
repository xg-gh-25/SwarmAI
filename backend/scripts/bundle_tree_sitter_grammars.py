"""Put the tree-sitter grammars code intel loads on disk, OUTSIDE the backend process.

Usage (from backend/):
``python scripts/bundle_tree_sitter_grammars.py <out dir> [--deadline SECONDS]``

Writes the grammar library of every language in
``core.code_intel.parser.tree_sitter_languages()`` — and only those — into *out dir*
(normally ``generated/tree_sitter_grammars``). ``core.code_intel.grammars`` loads only
grammars already on disk and never downloads one inside the backend: the library's own
download holds the GIL and stops the whole process (a fresh-HOME startup froze 192 s on
it, 2026-10-28; a stalled one never ends). So this script is where they come from, run as
its own process by:

- ``desktop/scripts/build-backend.sh``, which packages the directory as
  ``tree_sitter_grammars`` (the packaged app);
- the developer start scripts ``dev.sh`` and ``desktop/scripts/dev-with-backend.sh``;
- a Hive's first boot (``hive/user_data.py``, ``hive/setup.sh``) and every update (the
  provisioner's update script, ``hive/update-hive.sh``), which keep the directory across
  the update's file sync.

Those callers only warn when it fails (offline): the backend then runs with the
grammars it has and code intel takes its regex path for the rest.

A directory that already holds every language for the installed library version (the
version is written to ``.tree-sitter-language-pack-version`` inside it, and the library,
pointed at it, lists every language) is left as it is, with no network: the developer
scripts run this at every start. Another version, a missing language, or grammars of
another platform (the library lists only its own platform's files) rebuild it.

How: the library's ``download`` into a work directory next to *out*
(``<out>.build``; its cache_dir is a directory of grammar libraries, with the release
manifest and the platform bundle next to it), then *out* is swapped in whole. When
this machine's own cache already holds the bundle for this library version, it is copied
in first, so the extraction is local. Each bundle is checked against the sha256 the
release manifest records. Exit 1 when a language is missing afterwards (asked of the
library, ``downloaded_languages``) or a bundle does not match: a build must not ship a
partial or corrupt set. The download runs in the library's native code holding the GIL,
so no Python timer can stop a stalled one: ``--deadline`` (default 600 s; a slow download
measured 125 s) arms SIGALRM with its default action, and the operating system ends the
process (exit 142 in a shell) when it is still running then.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import signal
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

STAMP = ".tree-sitter-language-pack-version"
DEFAULT_DEADLINE = 600


def _library_version() -> str | None:
    """The installed tree-sitter-language-pack version, or None when unknown."""
    from importlib import metadata
    try:
        return metadata.version("tree-sitter-language-pack")
    except metadata.PackageNotFoundError:
        return None


def up_to_date(out: Path, langs: list[str]) -> bool:
    """Whether *out* already holds every language in *langs* for the installed
    library version, on this platform. Local only: the library lists the directory."""
    import tree_sitter_language_pack as tslp
    version = _library_version()
    stamp = out / STAMP
    if version is None or not stamp.is_file() or stamp.read_text().strip() != version:
        return False
    tslp.configure(tslp.PackConfig(cache_dir=str(out)))
    return set(langs) <= set(tslp.downloaded_languages())


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


def _work_dirs(out: Path) -> tuple[Path, Path]:
    """Where a build happens and where the replaced directory waits, next to *out*:
    a deadline ends the process without unwinding, so a fixed place lets the next run
    remove what a killed one left (a temp directory would stay behind for good)."""
    return out.with_name(out.name + ".build"), out.with_name(out.name + ".old")


def bundle(out: Path) -> list[str]:
    """Fill *out* with the grammar libraries, unless it is already up to date; returns
    the languages it holds. *out* is swapped in whole (a rename on the same
    filesystem) only once every library and the stamp are written, so a run ended
    midway leaves the previous directory, or none, never a partial one."""
    import tree_sitter_language_pack as tslp
    from core.code_intel.parser import tree_sitter_languages

    langs = tree_sitter_languages()
    own = Path(tslp.cache_dir()).parent          # this machine's cache for this version
    work, old = _work_dirs(out)
    if old.is_dir() and not out.exists():
        old.rename(out)                          # ended between the two renames: put it back
    for leftover in (work, old):                 # what a run ended midway left, every run
        if leftover.exists():
            shutil.rmtree(leftover)
    if up_to_date(out, langs):
        return langs
    libs = work / "libs"
    libs.mkdir(parents=True)
    try:
        if (own / "manifest.json").is_file() and list((own / "bundles").glob("*.tar.zst")):
            _check_bundles(own)
            shutil.copy2(own / "manifest.json", work / "manifest.json")
            shutil.copytree(own / "bundles", work / "bundles")
        tslp.configure(tslp.PackConfig(cache_dir=str(libs)))
        tslp.download(langs)
        _check_bundles(work)
        missing = sorted(set(langs) - set(tslp.downloaded_languages()))
        if missing:
            raise SystemExit(f"tree-sitter grammars missing after download: {', '.join(missing)}")
        version = _library_version()
        if version:
            (libs / STAMP).write_text(version + "\n")
        if out.exists():
            out.rename(old)
        try:
            libs.rename(out)
        except BaseException:
            if old.exists() and not out.exists():
                old.rename(out)
            raise
    finally:
        shutil.rmtree(work, ignore_errors=True)
        shutil.rmtree(old, ignore_errors=True)
    return langs


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="bundle_tree_sitter_grammars.py",
                                 description="Put the tree-sitter grammars on disk.")
    ap.add_argument("out", type=Path, help="output directory (generated/tree_sitter_grammars)")
    ap.add_argument("--deadline", type=int, default=DEFAULT_DEADLINE,
                    help="seconds before a stalled download ends the process (0: none)")
    args = ap.parse_args(argv)
    if args.deadline > 0 and hasattr(signal, "SIGALRM"):
        signal.signal(signal.SIGALRM, signal.SIG_DFL)
        signal.alarm(args.deadline)
    langs = bundle(args.out)
    print(f"tree-sitter grammars in {args.out}: {', '.join(langs)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
