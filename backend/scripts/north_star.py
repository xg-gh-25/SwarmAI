"""Swarm's north star in full (SWARMAI.md § My North Star; core.north_star).

Computes the snapshot now (reads the cognition level, the CDF health and Needs You;
no model call), writes it where the prompt's headline is read from, and prints the
headline and the detail.

Usage (from backend/, or anywhere through the backend binary):
    "$SWARM_BACKEND_BIN" py -m scripts.north_star
    "$SWARM_BACKEND_BIN" py -m scripts.north_star --json
"""
from __future__ import annotations

import argparse
import json
import sys


def main(argv: "list[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true", help="print the snapshot as JSON")
    args = parser.parse_args(argv)
    from core import north_star
    snap = north_star.refresh() or north_star.compute()
    if args.json:
        print(json.dumps(snap, ensure_ascii=False, indent=1))
        return 0
    print(f"North star, as of {snap['at']}")
    print("\n".join(f"- {line}" for line in snap["headline"]))
    print()
    print("\n".join(snap["detail"]).rstrip())
    return 0


if __name__ == "__main__":
    sys.exit(main())
