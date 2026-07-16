#!/usr/bin/env python3
"""Update docs/CONVERGENCE.md with current metrics from real data sources.

Reads actual project state (EVOLUTION.md, git tags, test count, DDD docs,
MEMORY.md) and appends a new snapshot row to the Summary table.

Run: python backend/scripts/update_convergence.py
Triggered by: s_swarm-release Stage 1.5 (before tagging).

Exit 0: updated (or no meaningful changes).
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

# Runtime EVOLUTION.md (has real corrections, not sanitized template)
_SWARMWS = Path.home() / ".swarm-ai" / "SwarmWS"
EVOLUTION_RUNTIME = _SWARMWS / ".context" / "EVOLUTION.md"
# Fallback: codebase template (for CI or fresh installs)
EVOLUTION_TEMPLATE = ROOT / "backend" / "context" / "EVOLUTION.md"

# DDD docs in workspace
DDD_DIR = _SWARMWS / "Projects" / "SwarmAI"


def count_corrections() -> int:
    """Count C### entries in EVOLUTION.md (runtime first, template fallback)."""
    for path in [EVOLUTION_RUNTIME, EVOLUTION_TEMPLATE]:
        if path.exists():
            text = path.read_text()
            count = len(re.findall(r"^### C\d+", text, re.MULTILINE))
            if count > 0:
                return count
    return 0


def count_ddd_sections() -> int:
    """Count ## sections across all 4 DDD docs."""
    if not DDD_DIR.exists():
        return 0
    total = 0
    try:  # Run 0: single source of truth (guarded — script may run standalone)
        from core.project_registry import DDD_CANONICAL_DOCS as _docs
    except ImportError:
        _docs = ("PRODUCT.md", "TECH.md", "IMPROVEMENT.md", "PROJECT.md")  # ddd-canonical-fallback
    for doc in _docs:
        path = DDD_DIR / doc
        if path.exists():
            total += len(re.findall(r"^##\s", path.read_text(), re.MULTILINE))
    return total


def count_tests() -> int:
    """Count tests via pytest --collect-only (quiet, no execution)."""
    venv_python = ROOT / "backend" / ".venv" / "bin" / "python"
    if not venv_python.exists():
        return 0
    try:
        result = subprocess.run(
            [str(venv_python), "-m", "pytest", "--co", "-q", "--timeout=30",
             "--override-ini=addopts="],  # clear addopts to avoid -n 4 without xdist
            capture_output=True, text=True, timeout=60,
            cwd=str(ROOT / "backend"),
            env={**__import__("os").environ, "SWARMAI_SUITE": "1"},
        )
        # Look for "N tests/items" in output (typically last meaningful line)
        for line in result.stdout.strip().split("\n")[::-1]:
            match = re.search(r"(\d+)\s+(test|item)", line)
            if match:
                return int(match.group(1))
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
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


def get_p0_current_release() -> str:
    """Estimate P0 count for current release (from IMPROVEMENT.md What Failed)."""
    imp = DDD_DIR / "IMPROVEMENT.md" if DDD_DIR.exists() else None
    if not imp or not imp.exists():
        return "?"
    # Count entries mentioning P0/Sev-1/critical in the "What Failed" section
    # This is a rough heuristic — manual review is more accurate
    return "check"


def generate_snapshot() -> dict:
    """Generate current metrics snapshot."""
    return {
        "date": date.today().isoformat(),
        "version": get_latest_version(),
        "corrections": count_corrections(),
        "ddd_sections": count_ddd_sections(),
        "tests": count_tests(),
    }


def format_table_row(s: dict) -> str:
    """Format as markdown table row matching CONVERGENCE.md Summary table."""
    return (
        f"| {s['date']} | {s['version']} "
        f"| {s['corrections']} "
        f"| {s['ddd_sections']} "
        f"| {s['tests']}+ "
        f"| auto (release) |"
    )


def read_last_snapshot() -> dict | None:
    """Read the last row from the Summary table in CONVERGENCE.md."""
    if not CONVERGENCE.exists():
        return None
    text = CONVERGENCE.read_text()
    # Find all table rows (| date | version | ... |)
    rows = re.findall(r"^\| \d{4}-\d{2}-\d{2} \|.*\|$", text, re.MULTILINE)
    if not rows:
        return None
    last = rows[-1]
    parts = [p.strip() for p in last.split("|") if p.strip()]
    if len(parts) >= 5:
        return {
            "date": parts[0],
            "version": parts[1],
            "corrections": int(re.search(r"\d+", parts[2]).group()) if re.search(r"\d+", parts[2]) else 0,
            "ddd_sections": int(re.search(r"\d+", parts[3]).group()) if re.search(r"\d+", parts[3]) else 0,
            "tests": int(re.search(r"\d+", parts[4]).group()) if re.search(r"\d+", parts[4]) else 0,
        }
    return None


def has_meaningful_change(current: dict, last: dict | None) -> bool:
    """Determine if metrics changed enough to warrant a new row."""
    if last is None:
        return True
    # Any metric changed by >0
    return (
        current["corrections"] != last["corrections"]
        or current["ddd_sections"] != last["ddd_sections"]
        or abs(current["tests"] - last["tests"]) >= 10  # ignore tiny test count noise
        or current["version"] != last["version"]
    )


def append_to_convergence(row: str) -> bool:
    """Append a row to the Summary table in CONVERGENCE.md."""
    if not CONVERGENCE.exists():
        print(f"  WARN: {CONVERGENCE} not found — skipping append", file=sys.stderr)
        return False

    text = CONVERGENCE.read_text()

    # Find the Summary table — look for the sentinel comment or last table row
    # Strategy: find "## Snapshot History" section, append after last table row
    marker = "## Snapshot History"
    if marker not in text:
        # Add the section at the end (before final note)
        text = text.rstrip() + f"\n\n---\n\n{marker}\n\n"
        text += "| Date | Version | Corrections | DDD Sections | Tests | Source |\n"
        text += "|------|---------|-------------|-------------|-------|--------|\n"
        text += row + "\n"
    else:
        # Append after the last | line in that section
        lines = text.split("\n")
        insert_idx = len(lines)  # default: end
        in_section = False
        last_table_line = -1
        for i, line in enumerate(lines):
            if marker in line:
                in_section = True
            if in_section and line.startswith("|"):
                last_table_line = i
        if last_table_line >= 0:
            lines.insert(last_table_line + 1, row)
        else:
            # Section exists but no table yet — add header + row
            for i, line in enumerate(lines):
                if marker in line:
                    lines.insert(i + 1, "")
                    lines.insert(i + 2, "| Date | Version | Corrections | DDD Sections | Tests | Source |")
                    lines.insert(i + 3, "|------|---------|-------------|-------------|-------|--------|")
                    lines.insert(i + 4, row)
                    break
        text = "\n".join(lines)

    CONVERGENCE.write_text(text)
    return True


def main() -> int:
    snapshot = generate_snapshot()
    last = read_last_snapshot()
    row = format_table_row(snapshot)

    print(f"=== Convergence Metrics Snapshot ({snapshot['date']}) ===")
    print(f"  Version:      {snapshot['version']}")
    print(f"  Corrections:  {snapshot['corrections']}")
    print(f"  DDD sections: {snapshot['ddd_sections']}")
    print(f"  Tests:        {snapshot['tests']}+")
    print()

    if not has_meaningful_change(snapshot, last):
        print("  No meaningful change since last snapshot — skipping.")
        return 0

    print(f"  New row: {row}")

    if append_to_convergence(row):
        print(f"  ✅ Appended to {CONVERGENCE}")
        print("  Commit with: git add docs/CONVERGENCE.md && git commit -m 'docs: update convergence metrics'")
    else:
        print("  ⚠️  Could not append — print only mode")

    return 0


if __name__ == "__main__":
    sys.exit(main())
