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
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import get_app_data_dir


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
        skill_refs = re.findall(r"\bs_([\w-]+)\b", block)
        # Also detect tool names from correction text (Bash, Read, Edit, etc.)
        tool_refs = re.findall(
            r"\b(Bash|Read|Edit|Write|WebFetch|Agent|Grep|Glob)\b",
            correction_text + " " + pattern_text,
        )

        # Build entries in the format read_correction_stats() expects:
        # {"ts": float, "type": "user_correction", "tool": "skill_or_tool_name", ...}
        # One entry per affected skill/tool to distribute signal correctly.
        ts = datetime.fromisoformat(f"{date_str}T00:00:00+00:00").timestamp()

        targets = list(set(
            [f"s_{s}" for s in skill_refs]
            + tool_refs
        ))
        if not targets:
            targets = ["_user_correction"]  # fallback bucket

        for target in targets:
            entry = {
                "ts": ts,
                "session_id": f"seed_{cid}",
                "type": "user_correction",
                "tool": target,
                "input_summary": correction_text[:200],
                "error": pattern_text[:200],
                "source": "evolution_md_seed",
                "correction_id": cid,
            }
            corrections.append(entry)

    return corrections


def write_corrections(corrections: list[dict], output_path: Path) -> int:
    """Write corrections to JSONL file. Returns count written."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Append to existing file (don't overwrite organic corrections)
    # Dedup by session_id prefix "seed_C###" to avoid double-seeding
    existing_seeds = set()
    if output_path.exists():
        for line in output_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    entry = json.loads(line)
                    sid = entry.get("session_id", "")
                    if sid.startswith("seed_"):
                        existing_seeds.add(sid)
                except json.JSONDecodeError:
                    pass

    written = 0
    with open(output_path, "a", encoding="utf-8") as f:
        for entry in corrections:
            if entry["session_id"] in existing_seeds:
                continue  # Skip duplicates
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            written += 1

    return written


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed corrections.jsonl from EVOLUTION.md")
    parser.add_argument(
        "--evolution-path",
        type=Path,
        default=get_app_data_dir() / "SwarmWS" / ".context" / "EVOLUTION.md",
        help="Path to EVOLUTION.md",
    )
    parser.add_argument(
        "--output-path",
        type=Path,
        default=None,
        help="Path to corrections.jsonl (default: ~/.swarm-ai/state/corrections.jsonl)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and show corrections without writing",
    )
    args = parser.parse_args()

    if args.output_path is None:
        # Write to the runtime corrections path that read_correction_stats() reads
        from jobs.paths import STATE_DIR
        args.output_path = STATE_DIR / "corrections.jsonl"

    if not args.evolution_path.exists():
        print(f"ERROR: {args.evolution_path} not found", file=sys.stderr)
        return 1

    evolution_text = args.evolution_path.read_text(encoding="utf-8")
    corrections = parse_corrections(evolution_text)

    if not corrections:
        print("No corrections found in EVOLUTION.md", file=sys.stderr)
        return 1

    print(f"Parsed {len(corrections)} correction entries from EVOLUTION.md:")
    for c in corrections:
        print(f"  {c['correction_id']} → {c['tool']:20s} ({c['input_summary'][:60]}...)")

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
