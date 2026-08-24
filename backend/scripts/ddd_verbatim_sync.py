#!/usr/bin/env python3
"""DDD-skills verbatim-copy manifest — source-of-truth, verifier, and discoverer.

The DDD-native skill templates under ``backend/templates/ddd-skills/`` contain two
KINDS of files that look alike but must be treated oppositely:

  • VERBATIM copies of a ``backend/`` source-of-truth (a script/asset that a DDD
    skill reuses unchanged). These MUST stay byte-identical — if the source is
    fixed, the copy has to be re-synced, or the DDD ships a stale generation.
    (This is exactly how ``export-pdf.sh`` silently drifted a full generation
    behind for months — run_ff9db326.)

  • ADAPTED portability forks (e.g. ``_ddd_paths.py``, ``artifact_cli.py``,
    ``ai_ready_helpers.py``) whose WHOLE POINT is to differ — they strip the
    hardcoded ``~/.swarm-ai`` SwarmAI paths so a DDD runs on Kiro / Claude Code /
    an AIM package. Forcing these byte-identical would be a BUG.

"verbatim vs adapted" is a human judgment that cannot be auto-derived from the
files, so ``VERBATIM_PAIRS`` below is a CURATED manifest. This module is the
SINGLE SOURCE OF TRUTH for that manifest — the drift-guard test
(``test_project_crud_properties.py::TestDddNativeSkills``) IMPORTS it rather than
duplicating it (a duplicated manifest would itself drift — the very failure class
this whole mechanism exists to prevent).

CLI (replaces the manual hash-scan):
    python scripts/ddd_verbatim_sync.py verify      # re-check every pair is byte-identical (exit 1 on drift)
    python scripts/ddd_verbatim_sync.py discover     # find copy-pairs + flag UNMANAGED verbatim copies
    python scripts/ddd_verbatim_sync.py sync         # re-copy every drifted pair from its source (mutating)
    python scripts/ddd_verbatim_sync.py sync --dry-run   # show what sync WOULD do, change nothing

Exit codes: 0 = clean / in-sync, 1 = drift or unmanaged copy found (or, for sync,
files were changed — so CI can gate on "nothing to sync").
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

# Repo root = backend/scripts/ -> backend/ -> repo root
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DDD_ROOT = REPO_ROOT / "backend" / "templates" / "ddd-skills"

# File extensions that can be verbatim runtime assets (not docs/config we adapt).
_ASSET_SUFFIXES = (".py", ".sh", ".js", ".css")
# Files below this size are skipped by DISCOVER: an empty/near-empty file (e.g.
# ``__init__.py``) is byte-identical to every other empty file, so a hash hit is
# coincidental, not a real copy relationship. (They can still be pinned in the
# manifest explicitly — that is honored by verify/sync regardless of size.)
_TINY_BYTES = 30

# ─── THE CURATED MANIFEST: ddd-skill file (rel to DDD_ROOT) → source (rel to REPO_ROOT) ───
# Generated + verified by `discover` (2026-07-15). Keep sorted for stable diffs.
# EMPTY by design (2026-08-24): the only DDD-native skills that carried verbatim
# copies of a SwarmAI-native source were s_ddd-pipeline (← s_autonomous-pipeline)
# and s_ddd-pollinate (← s_pollinate), both REMOVED from the DDD-native set. The 3
# retained skills (s_ddd-manager, s_ddd-persist, s_repo-to-ddd) are adapted forks
# that DELIBERATELY differ from their sources (they strip the ~/.swarm-ai paths so a
# DDD runs standalone) — so there is nothing to keep byte-identical. If a future
# native skill ever ships a verbatim runtime copy, re-populate this manifest.
VERBATIM_PAIRS: dict[str, str] = {}

# Files that ARE byte-identical to a source right now but are DELIBERATELY excluded
# from the manifest because they are adapted/coincidental — documented so `discover`
# does not keep re-surfacing them and so the reasoning is not lost. (Currently none:
# the adapted forks like ai_ready_helpers.py already DIFFER from their source, so they
# never show up in discover. This set is here for future coincidental collisions.)
_KNOWN_NON_VERBATIM: set[str] = set()


def _md5(p: Path) -> str:
    # non-crypto content fingerprint for copy-equality checks, not security
    return hashlib.md5(p.read_bytes(), usedforsecurity=False).hexdigest()


def _iter_ddd_assets():
    """Yield (rel_to_ddd_root, Path) for every candidate asset file under ddd-skills."""
    for p in sorted(DDD_ROOT.rglob("*")):
        if not p.is_file() or "__pycache__" in p.parts:
            continue
        if p.suffix not in _ASSET_SUFFIXES:
            continue
        yield str(p.relative_to(DDD_ROOT)), p


# Source trees a ddd copy may pull from. If a future verbatim copy ever tracks a
# file outside these, widen this list (and discover's completeness claim with it).
_SOURCE_TREES = ("backend/skills", "backend/core")


def _build_source_pool() -> dict[str, list[str]]:
    """hash → ALL repo-relative source paths with that content, over the backend/
    trees ddd copies pull from. Keeping the full list (not just one) lets discover
    warn when a hash is ambiguous — many real files ARE byte-identical (e.g. the
    per-report CMHK catalog.py/client.py copies), so reporting a single 'source'
    could name the wrong one and mislead whoever adds the manifest entry."""
    pool: dict[str, list[str]] = {}
    for base in _SOURCE_TREES:
        for p in (REPO_ROOT / base).rglob("*"):
            if not p.is_file() or "ddd-skills" in p.parts:
                continue
            if any(x in p.parts for x in ("__pycache__", "node_modules", ".venv")):
                continue
            if p.suffix not in _ASSET_SUFFIXES or p.stat().st_size < _TINY_BYTES:
                continue
            pool.setdefault(_md5(p), []).append(str(p.relative_to(REPO_ROOT)))
    # Deterministic order (shortest, then lexical) so output is stable across runs.
    for h in pool:
        pool[h].sort(key=lambda r: (len(r), r))
    return pool


def verify() -> int:
    """Assert every manifest pair is byte-identical. Returns 0 clean, 1 on drift."""
    drift = []
    missing = []
    for rel, src in sorted(VERBATIM_PAIRS.items()):
        d = DDD_ROOT / rel
        s = REPO_ROOT / src
        if not s.is_file():
            missing.append(f"SOURCE MISSING: {src}  (referenced by {rel})")
            continue
        if not d.is_file():
            missing.append(f"DDD COPY MISSING: {rel}")
            continue
        if d.read_bytes() != s.read_bytes():
            drift.append((rel, src))
    print(f"verify: {len(VERBATIM_PAIRS)} pairs | "
          f"in-sync {len(VERBATIM_PAIRS) - len(drift) - len(missing)} | "
          f"drift {len(drift)} | missing {len(missing)}")
    for m in missing:
        print(f"  ✗ {m}")
    for rel, src in drift:
        print(f"  ✗ DRIFT: {rel}\n      re-sync: cp '{REPO_ROOT/src}' '{DDD_ROOT/rel}'")
    if not drift and not missing:
        print("  ✓ all verbatim copies byte-identical to their source-of-truth")
        return 0
    return 1


def discover() -> int:
    """Find ddd files byte-identical to a backend/ source. Flags any NOT in the
    manifest (a new verbatim copy that would drift unwatched). Returns 1 if any
    unmanaged copy is found."""
    pool = _build_source_pool()
    known = set(VERBATIM_PAIRS) | _KNOWN_NON_VERBATIM
    managed_hits = 0
    unmanaged = []  # (rel, [candidate sources])
    for rel, p in _iter_ddd_assets():
        # Skip empty/tiny files unless explicitly pinned: an empty __init__.py is
        # byte-identical to every other empty file, so a hit is coincidental. (This
        # is also why verify counts 35 but discover reports fewer managed — the
        # 0-byte engine/__init__.py is pinned+verified but invisible to discover.)
        if p.stat().st_size < _TINY_BYTES and rel not in VERBATIM_PAIRS:
            continue
        h = _md5(p)
        if h in pool:
            if rel in known:
                managed_hits += 1
            else:
                unmanaged.append((rel, pool[h]))
    print(f"discover: {managed_hits} managed verbatim copies | "
          f"{len(unmanaged)} UNMANAGED  "
          f"(source pool scoped to: {', '.join(_SOURCE_TREES)})")
    for rel, srcs in sorted(unmanaged):
        first = srcs[0]
        ambiguous = f"  ⚠ AMBIGUOUS — {len(srcs)} identical sources: {srcs}" if len(srcs) > 1 else ""
        print(f"  ⚠ UNMANAGED verbatim copy: {rel}\n"
              f"      byte-identical to: {first}{ambiguous}\n"
              f"      → add to VERBATIM_PAIRS (if it should track the source), "
              f"or to _KNOWN_NON_VERBATIM (if the collision is coincidental).")
    if not unmanaged:
        print("  ✓ every verbatim copy is in the manifest — nothing drifts unwatched")
        return 0
    return 1


def sync(dry_run: bool = False) -> int:
    """Re-copy every drifted manifest pair from its source. Returns 1 if anything
    was (or would be) changed — so CI can gate on 'nothing to sync'."""
    import shutil
    changed = []
    for rel, src in sorted(VERBATIM_PAIRS.items()):
        d = DDD_ROOT / rel
        s = REPO_ROOT / src
        if not s.is_file():
            print(f"  ✗ SOURCE MISSING, cannot sync: {src}")
            return 1
        if not d.is_file() or d.read_bytes() != s.read_bytes():
            changed.append(rel)
            if not dry_run:
                d.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(s, d)
    verb = "would sync" if dry_run else "synced"
    print(f"sync{' (dry-run)' if dry_run else ''}: {verb} {len(changed)} file(s)")
    for rel in changed:
        print(f"  {'·' if dry_run else '✓'} {rel}")
    if not changed:
        print("  ✓ already in sync — nothing to do")
        return 0
    return 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("verify", help="re-check every manifest pair is byte-identical")
    sub.add_parser("discover", help="find copy-pairs + flag unmanaged verbatim copies")
    sp = sub.add_parser("sync", help="re-copy drifted pairs from their source")
    sp.add_argument("--dry-run", action="store_true", help="show what would change, change nothing")
    args = ap.parse_args(argv)
    if args.cmd == "verify":
        return verify()
    if args.cmd == "discover":
        return discover()
    if args.cmd == "sync":
        return sync(dry_run=args.dry_run)
    return 2


if __name__ == "__main__":
    sys.exit(main())
