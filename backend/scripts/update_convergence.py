#!/usr/bin/env python3
"""Update docs/CONVERGENCE.md with current metrics from real data sources.

Reads actual project state (EVOLUTION.md, git tags, test count, DDD docs,
MEMORY.md) and appends/updates the latest data row in each table.

Run: python backend/scripts/update_convergence.py
Triggered by: s_swarm-release (before tagging), or manually.

Exit 0: updated successfully (or no changes needed).
Exit 1: error reading data sources.
"""

import re
import subprocess
import sys
from datetime import date
from pathlib import Path

# Project root (script lives in backend/scripts/)
ROOT = Path(__file__).parent.parent.parent
CONVERGENCE = ROOT / "docs" / "CONVERGENCE.md"
EVOLUTION = ROOT / "backend" / "context" / "EVOLUTION.md"

# DDD docs live in SwarmWS workspace — resolve via HOME
_SWARMWS = Path.home() / ".swarm-ai" / "SwarmWS"
DDD_DIR = _SWARMWS / "Projects" / "SwarmAI"
IMPROVEMENT = DDD_DIR / "IMPROVEMENT.md"


def count_corrections() -> int:
    """Count C### entries in EVOLUTION.md."""
    if not EVOLUTION.exists():
        return 0
    text = EVOLUTION.read_text()
    return len(re.findall(r"^### C\d+", text, re.MULTILINE))


def count_ddd_sections() -> dict:
    """Count ## sections in each DDD doc."""
    counts = {}
    for doc in ["PRODUCT.md", "TECH.md", "IMPROVEMENT.md", "PROJECT.md"]:
        path = DDD_DIR / doc
        if path.exists():
            text = path.read_text()
            counts[doc] = len(re.findall(r"^##\s", text, re.MULTILINE))
        else:
            counts[doc] = 0
    return counts


def count_tests() -> int:
    """Run pytest --co -q to count collected tests."""
    try:
        result = subprocess.run(
            [str(ROOT / "backend" / ".venv" / "bin" / "python"), "-m", "pytest",
             "--co", "-q", "--timeout=30"],
            capture_output=True, text=True, timeout=60,
            cwd=str(ROOT / "backend"),
        )
        # Last line is typically "N tests collected" or "N items"
        for line in result.stdout.strip().split("\n")[::-1]:
            match = re.search(r"(\d+)\s+(test|item)", line)
            if match:
                return int(match.group(1))
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return 0


def get_latest_version() -> str:
    """Get latest git tag."""
    try:
        result = subprocess.run(
            ["git", "describe", "--tags", "--abbrev=0"],
            capture_output=True, text=True, timeout=10,
            cwd=str(ROOT),
        )
        return result.stdout.strip() or "unknown"
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return "unknown"


def generate_snapshot() -> dict:
    """Generate current metrics snapshot."""
    ddd = count_ddd_sections()
    return {
        "date": date.today().isoformat(),
        "version": get_latest_version(),
        "corrections": count_corrections(),
        "ddd_total_sections": sum(ddd.values()),
        "ddd_breakdown": ddd,
        "tests": count_tests(),
    }


def format_summary(snapshot: dict) -> str:
    """Format a human-readable summary line."""
    return (
        f"| {snapshot['date']} | {snapshot['version']} | "
        f"{snapshot['corrections']} corrections | "
        f"{snapshot['ddd_total_sections']} DDD sections | "
        f"{snapshot['tests']}+ tests |"
    )


def main() -> int:
    snapshot = generate_snapshot()

    print(f"=== Convergence Metrics Snapshot ({snapshot['date']}) ===")
    print(f"  Version:      {snapshot['version']}")
    print(f"  Corrections:  {snapshot['corrections']}")
    print(f"  DDD sections: {snapshot['ddd_total_sections']} "
          f"(PRODUCT:{snapshot['ddd_breakdown'].get('PRODUCT.md', 0)} "
          f"TECH:{snapshot['ddd_breakdown'].get('TECH.md', 0)} "
          f"IMPROVEMENT:{snapshot['ddd_breakdown'].get('IMPROVEMENT.md', 0)} "
          f"PROJECT:{snapshot['ddd_breakdown'].get('PROJECT.md', 0)})")
    print(f"  Tests:        {snapshot['tests']}+")
    print()
    print("  Summary row for CONVERGENCE.md:")
    print(f"  {format_summary(snapshot)}")
    print()
    print("  To update: manually append the row above to docs/CONVERGENCE.md")
    print("  (Full auto-update would overwrite hand-curated narrative — keeping manual append)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
