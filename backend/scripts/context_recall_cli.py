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
        required=True,  # default-DENY: a privacy gate must never default permissive
        choices=sorted(_POLICY_EXCLUSIONS),
        help="Session type (determines the privacy gate). REQUIRED — no default.",
    )
    p.add_argument("--max-sections", type=int, default=3)
    p.add_argument(
        "--context-dir",
        default=str(_DEFAULT_CONTEXT_DIR),
        help="Directory holding the context files.",
    )
    args = p.parse_args(argv)

    policy_excluded = _POLICY_EXCLUSIONS[args.session_type]

    try:
        context_dir = Path(args.context_dir).resolve()
        # CRITICAL: confine to context_dir and use the RESOLVED basename for both
        # the policy check and the read. Blocks `../MEMORY.md`, absolute paths,
        # and case tricks (recall_context casefolds the basename internally).
        resolved = (context_dir / args.file).resolve()
        if not resolved.is_relative_to(context_dir):
            print(json.dumps({"allowed": False, "content": "",
                              "reason": "path escapes context directory — denied"}))
            return 0
        safe_name = resolved.name  # bare basename; recall_context normalizes case

        # Fast-path denial: don't even read the file if policy forbids recall.
        # recall_context applies the canonical (casefold + basename) gate.
        gate = recall_context(
            safe_name, args.query, memory_content="",
            policy_excluded_files=policy_excluded, max_sections=args.max_sections,
        )
        if gate.allowed is False:
            print(json.dumps({"allowed": False, "reason": gate.reason, "content": ""}))
            return 0

        # Inode-identity gate: a HARDLINK is a second directory entry for the
        # same inode that .resolve() cannot detect (no symlink to follow), so a
        # name-only gate would serve `NOTES.md` hardlinked to MEMORY.md. Deny if
        # the resolved target shares an inode with ANY policy-excluded file.
        if resolved.exists() and policy_excluded:
            try:
                target_id = resolved.stat()
                target_key = (target_id.st_dev, target_id.st_ino)
                for excluded in policy_excluded:
                    ex_path = context_dir / excluded
                    if ex_path.exists():
                        ex_id = ex_path.stat()
                        if (ex_id.st_dev, ex_id.st_ino) == target_key:
                            print(json.dumps({
                                "allowed": False, "content": "",
                                "reason": (f"'{args.file}' resolves to the same inode as "
                                           f"policy-excluded '{excluded}' — denied."),
                            }))
                            return 0
            except OSError:
                pass  # stat failure → fall through to normal not-found handling

        if not resolved.exists():
            print(json.dumps({"allowed": True, "content": "",
                              "reason": f"file not found: {safe_name}"}))
            return 0

        content = resolved.read_text(encoding="utf-8")
        res = recall_context(
            safe_name, args.query, memory_content=content,
            policy_excluded_files=policy_excluded, max_sections=args.max_sections,
        )
        print(json.dumps({
            "allowed": res.allowed,
            "reason": res.reason,
            "sections": list(res.sections),
            "content": res.content,
            # Hit-log surface (§6c): the observable the GS_RCHAIN eval cases
            # assert against, and the signal ingestion's Darwin will consume.
            "hit_layer": res.hit_layer,
            "hit_section": res.sections[0] if res.sections else "",
            "drilled": res.drilled,
        }))
        return 0
    except Exception as exc:  # noqa: BLE001 — never emit a bare traceback to the agent
        print(json.dumps({"allowed": False, "content": "",
                          "reason": f"recall error: {type(exc).__name__}: {exc}"}))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
