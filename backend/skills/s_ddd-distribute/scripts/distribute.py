#!/usr/bin/env python3
"""s_ddd-distribute orchestrator — THIN wrapper over core.ddd_packager.

All packaging logic lives in core/ddd_packager.py + core/ddd_distribution_policy.py.
This script only: parses args, reads the declaration, applies the subset-only rule,
calls the packager, and prints a human-readable summary. Zero packaging logic here.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Resolve the backend/ root so `core.*` imports work whether run from the package
# copy or the source tree (portable — no ~/.swarm-ai assumption).
_HERE = Path(__file__).resolve()
for _parent in _HERE.parents:
    if (_parent / "core" / "ddd_packager.py").is_file():
        sys.path.insert(0, str(_parent))
        break

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
    args = ap.parse_args(argv)

    ddd_dir = Path(args.ddd)
    if not (ddd_dir / "aim.json").is_file():
        print(f"ERROR: no aim.json under {ddd_dir} — not a DDD dir.", file=sys.stderr)
        return 2

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
