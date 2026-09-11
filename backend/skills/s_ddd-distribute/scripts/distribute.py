#!/usr/bin/env python3
"""s_ddd-distribute orchestrator — THIN wrapper over core.ddd_packager.

All packaging logic lives in core/ddd_packager.py + core/ddd_distribution_policy.py.
This script only: parses args, reads the declaration, applies the subset-only rule,
calls the packager, and prints a human-readable summary. Zero packaging logic here.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Resolve the backend/ root so `core.*` imports work from EITHER tree: the source repo
# (`backend/skills/...`, where the parent walk finds it) or the PROJECTED copy the agent
# actually executes (`<workspace>/.claude/skills/...`, where NO parent contains `core/`).
#
# The parent walk alone is NOT portable for the projected copy — it silently relies on
# whatever else happens to put backend/ on sys.path. On this dev machine a site-packages
# `.pth` hardcodes an absolute Desktop path, so the projected script imported fine and the
# breakage was invisible; on a fresh clone or the Linux/EC2 deployment that `.pth` does not
# exist and the agent-executed copy dies with ImportError (meta-review finding). So: walk
# first, then fall back to explicit, discoverable roots.
def _add_backend_to_path() -> None:
    here = Path(__file__).resolve()
    candidates = [p for p in here.parents if (p / "core" / "ddd_packager.py").is_file()]
    # Explicit override wins for non-standard layouts / containers.
    env_root = os.environ.get("SWARMAI_BACKEND")
    if env_root:
        candidates.append(Path(env_root))
    # Projected-copy fallbacks: the workspace sits at <ws>/.claude/skills/<skill>/scripts/,
    # so the repo is not an ancestor. Try the known checkout locations, cheapest first.
    for guess in (
        Path.home() / "Desktop" / "SwarmAI-Workspace" / "swarmai" / "backend",
        Path.home() / "swarmai" / "backend",
        Path("/opt/swarmai/backend"),
    ):
        candidates.append(guess)
    for cand in candidates:
        if (cand / "core" / "ddd_packager.py").is_file():
            sys.path.insert(0, str(cand))
            return
    raise SystemExit(
        "ERROR: cannot locate the swarmai backend/ root (needed for core.ddd_packager). "
        "Set SWARMAI_BACKEND=/path/to/swarmai/backend and re-run."
    )


_add_backend_to_path()

from core import ddd_distribution_policy as policy  # noqa: E402
from core import ddd_packager as pk  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Package a DDD into distributable target(s).")
    ap.add_argument("--ddd", required=True, help="Path to the DDD dir (Projects/<name>/).")
    ap.add_argument("--out", required=True, help="Output root; each target lands in <out>/<target>/.")
    ap.add_argument("--targets", default=None,
                    help="Comma-separated subset of declared targets. Omit = full declared set.")
    ap.add_argument("--publish", action="store_true",
                    help="Run the external-publish content gate (refused unless visibility=external).")
    ap.add_argument("--with-enablement", action="store_true", dest="with_enablement",
                    help="Ship class-A enablement skills (e.g. s_repo-to-ddd) as a portable "
                         "copy — for BARE foreign hosts (Kiro/Claude Code/Quick) that lack "
                         "SwarmAI/AIM built-ins. Default OFF = lean knowledge-only package.")
    ap.add_argument("--check-stale", action="store_true",
                    help="Do NOT emit. Report whether each already-emitted target under --out "
                         "still matches the DDD source, and exit 1 if any is stale. This is the "
                         "read-only question 'did the source move since we last packaged?' — the "
                         "blind spot that let a source-side fix sit un-emitted for weeks.")
    args = ap.parse_args(argv)

    ddd_dir = Path(args.ddd)
    if not (ddd_dir / "aim.json").is_file():
        print(f"ERROR: no aim.json under {ddd_dir} — not a DDD dir.", file=sys.stderr)
        return 2

    if args.check_stale:
        out_root = Path(args.out)
        # Only KNOWN target names count as emitted packages. Treating every child dir as a
        # target made an unrelated `.git/`, `logs/` or nested `dist/` read as "STALE — no
        # source stamp" (the fail-safe firing on a non-package), printing a spurious red and
        # exiting 1 with nothing actually stale. A read-only check that cries wolf gets
        # ignored, which is how the signal dies (Gate-2 finding).
        known = (pk.TARGET_AIM, pk.TARGET_OPEN_PLUGIN)
        checked = stale = 0
        for name in known:
            target_dir = out_root / name
            if not target_dir.is_dir():
                continue
            checked += 1
            if pk.detect_stale_package(ddd_dir, target_dir):
                stale += 1
                stamp = pk.read_source_stamp(target_dir)
                why = "no source stamp (emitted before stamping existed)" if not stamp \
                    else "source content changed since this package was emitted"
                print(f"STALE  {name} — {why}")
            else:
                print(f"fresh  {name}")
        if not checked:
            # Exit 2 (matching the no-aim.json contract), NOT 0. Returning 0 here is
            # fail-OPEN: a typo'd --out would report GREEN having checked nothing, and any
            # CI/job wired to the exit code would trust it (Gate-2 finding). 0 is reserved
            # strictly for "checked >= 1 target, all fresh".
            print(f"ERROR: no emitted target ({', '.join(known)}) found under {out_root} — "
                  f"nothing was checked. Emit first, or fix --out.", file=sys.stderr)
            return 2
        print(f"\n{stale}/{checked} target(s) stale."
              + (" Re-emit to pick up the current source." if stale else ""))
        return 1 if stale else 0

    pol = policy.validate_distribution_file(ddd_dir / "aim.json")
    print(f"Declared reach: targets={list(pol.targets)} visibility={pol.visibility} "
          f"declared={pol.declared}")
    for w in pol.warnings:
        print(f"  ⚠ {w}")
    if not pol.is_distributable:
        print("Not distributable (fail-closed): no declared targets. "
              "The DDD owner must declare a distribution block to distribute.")
        return 0

    requested = args.targets.split(",") if args.targets else None
    try:
        results = pk.package_ddd(ddd_dir, args.out, requested_targets=requested,
                                 publish=args.publish, with_enablement=args.with_enablement)
    except pk.PackagingError as e:
        print(f"REFUSED / ABORTED: {e}", file=sys.stderr)
        return 1

    if not results:
        print("Nothing emitted (targets:[] or no subset selected).")
        return 0

    for r in results:
        print(f"\n✓ {r.target} → {r.out_dir}")
        print(f"   skills included (class-B domain): {r.skills_included}")
        print(f"   skills excluded (class-A / unclassified): {r.skills_excluded}")
        for w in r.warnings:
            print(f"   ⚠ {w}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
