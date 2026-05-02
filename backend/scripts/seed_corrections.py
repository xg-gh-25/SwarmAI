#!/usr/bin/env python3
"""Seed corrections.jsonl from EVOLUTION.md real corrections.

Parses the Corrections Captured section of EVOLUTION.md and writes
entries to corrections.jsonl in the format expected by the evolution
optimizer's confidence computation.

This is a one-time bootstrap script — once runtime hooks are generating
organic corrections, this script is no longer needed.

Usage:
    python scripts/seed_corrections.py [--evolution-path PATH] [--output-path PATH] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path


def parse_corrections(evolution_text: str) -> list[dict]:
    """Parse EVOLUTION.md Corrections Captured section into structured entries.

    Each correction entry has:
    - id: C001, C002, etc.
    - date: ISO date string
    - correction: the user's correction text
    - pattern: the structural pattern identified
    - status: active/resolved
    - skills_affected: list of skill names mentioned (best-effort)
    """
    corrections = []

    # Find the Corrections Captured section
    section_match = re.search(
        r"## Corrections Captured\s*\n(.*?)(?=\n## |\Z)",
        evolution_text,
        re.DOTALL,
    )
    if not section_match:
        return corrections

    section_text = section_match.group(1)

    # Parse each ### C### | date block
    # Pattern: ### C014 | 2026-05-02
    blocks = re.split(r"(?=### C\d+)", section_text)

    for block in blocks:
        block = block.strip()
        if not block.startswith("### C"):
            continue

        # Extract header: ### C014 | 2026-05-02
        header_match = re.match(
            r"### (C\d+)\s*\|\s*(\d{4}-\d{2}-\d{2})",
            block,
        )
        if not header_match:
            continue

        cid = header_match.group(1)
        date_str = header_match.group(2)

        # Extract correction text (first **Correction**: line)
        corr_match = re.search(
            r"\*\*Correction\*\*:\s*(.+?)(?=\n-\s*\*\*|\Z)",
            block,
            re.DOTALL,
        )
        correction_text = corr_match.group(1).strip() if corr_match else ""

        # Extract pattern text
        pattern_match = re.search(
            r"\*\*Pattern\*\*:\s*(.+?)(?=\n-\s*\*\*|\Z)",
            block,
            re.DOTALL,
        )
        pattern_text = pattern_match.group(1).strip() if pattern_match else ""

        # Extract status
        status_match = re.search(r"\*\*Status\*\*:\s*(.+)", block)
        status = status_match.group(1).strip() if status_match else "unknown"

        # Extract skill names mentioned (s_xxx pattern)
        skill_refs = re.findall(r"\bs_[\w-]+\b", block)

        # Build the corrections.jsonl entry
        entry = {
            "id": cid,
            "timestamp": f"{date_str}T00:00:00Z",
            "source": "evolution_md_seed",
            "correction_text": correction_text[:500],  # Cap for readability
            "pattern": pattern_text[:500],
            "status": status,
            "skills_affected": list(set(skill_refs)) if skill_refs else [],
            "seeded_at": datetime.now(timezone.utc).isoformat(),
        }
        corrections.append(entry)

    return corrections


def write_corrections(corrections: list[dict], output_path: Path) -> int:
    """Write corrections to JSONL file. Returns count written."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Append to existing file (don't overwrite organic corrections)
    existing_ids = set()
    if output_path.exists():
        for line in output_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    entry = json.loads(line)
                    existing_ids.add(entry.get("id", ""))
                except json.JSONDecodeError:
                    pass

    written = 0
    with open(output_path, "a", encoding="utf-8") as f:
        for entry in corrections:
            if entry["id"] in existing_ids:
                continue  # Skip duplicates
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            written += 1

    return written


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed corrections.jsonl from EVOLUTION.md")
    parser.add_argument(
        "--evolution-path",
        type=Path,
        default=Path.home() / ".swarm-ai" / "SwarmWS" / ".context" / "EVOLUTION.md",
        help="Path to EVOLUTION.md",
    )
    parser.add_argument(
        "--output-path",
        type=Path,
        default=None,
        help="Path to corrections.jsonl (default: ~/.swarm-ai/SwarmWS/.context/corrections.jsonl)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and show corrections without writing",
    )
    args = parser.parse_args()

    if args.output_path is None:
        args.output_path = args.evolution_path.parent / "corrections.jsonl"

    if not args.evolution_path.exists():
        print(f"ERROR: {args.evolution_path} not found", file=sys.stderr)
        return 1

    evolution_text = args.evolution_path.read_text(encoding="utf-8")
    corrections = parse_corrections(evolution_text)

    if not corrections:
        print("No corrections found in EVOLUTION.md", file=sys.stderr)
        return 1

    print(f"Parsed {len(corrections)} corrections from EVOLUTION.md:")
    for c in corrections:
        print(f"  {c['id']} ({c['timestamp'][:10]}): {c['correction_text'][:80]}...")

    if args.dry_run:
        print("\n[DRY RUN] Would write to:", args.output_path)
        return 0

    written = write_corrections(corrections, args.output_path)
    print(f"\nWrote {written} new corrections to {args.output_path}")
    if written < len(corrections):
        print(f"  ({len(corrections) - written} already existed, skipped)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
