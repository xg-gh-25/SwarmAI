#!/usr/bin/env python3
"""CLI for Reversible Context Recall (run_9de88af9).

Lets the agent retrieve a SPECIFIC excluded context section on demand, instead
of re-reading a whole 60K-token file. Mirrors the existing selective-injection
manifest footer which points the agent here:

    [Not loaded (N): COE Registry, Lessons Learned, … — recall_context("MEMORY.md", query) …]

The privacy gate (AC4) is enforced by mapping the session type to its policy
exclusion set HERE, then passing it into ``recall_context`` — so a session that
withheld a file for privacy can never recall it, regardless of how the tool is
invoked.

Usage:
    python backend/scripts/context_recall_cli.py \
        --file MEMORY.md --query "exit code -9 sigkill" --session-type desktop

    --session-type ∈ {desktop, owner_channel, nonowner_channel, group_channel}
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Allow running as a script: ensure backend/ is importable.
_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from core.context_directory_loader import (  # noqa: E402
    CHANNEL_LIGHT_EXCLUDE,
    GROUP_CHANNEL_EXCLUDE,
)
from core.context_recall import recall_context  # noqa: E402

# Session type → files excluded by POLICY (privacy), which recall must deny.
# Derived from the same constants context assembly uses, so the two never drift.
_POLICY_EXCLUSIONS = {
    "desktop": frozenset(),
    "owner_channel": frozenset(),
    "nonowner_channel": CHANNEL_LIGHT_EXCLUDE,
    "group_channel": GROUP_CHANNEL_EXCLUDE | CHANNEL_LIGHT_EXCLUDE,
}

# Default context directory (workspace .context/).
_DEFAULT_CONTEXT_DIR = Path.home() / ".swarm-ai" / "SwarmWS" / ".context"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Recall an excluded context section.")
    p.add_argument("--file", required=True, help="Context filename, e.g. MEMORY.md")
    p.add_argument("--query", required=True, help="What to retrieve")
    p.add_argument(
        "--session-type",
        default="desktop",
        choices=sorted(_POLICY_EXCLUSIONS),
        help="Session type (determines the privacy gate).",
    )
    p.add_argument("--max-sections", type=int, default=3)
    p.add_argument(
        "--context-dir",
        default=str(_DEFAULT_CONTEXT_DIR),
        help="Directory holding the context files.",
    )
    args = p.parse_args(argv)

    policy_excluded = _POLICY_EXCLUSIONS[args.session_type]

    # Fast-path denial: don't even read the file if policy forbids recall.
    if args.file in policy_excluded:
        res = recall_context(
            args.file, args.query, memory_content="",
            policy_excluded_files=policy_excluded, max_sections=args.max_sections,
        )
        print(json.dumps({"allowed": False, "reason": res.reason, "content": ""}))
        return 0

    path = Path(args.context_dir) / args.file
    if not path.exists():
        print(json.dumps({"allowed": True, "content": "",
                          "reason": f"file not found: {path}"}))
        return 0

    content = path.read_text(encoding="utf-8")
    res = recall_context(
        args.file, args.query, memory_content=content,
        policy_excluded_files=policy_excluded, max_sections=args.max_sections,
    )
    print(json.dumps({
        "allowed": res.allowed,
        "reason": res.reason,
        "sections": list(res.sections),
        "content": res.content,
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
