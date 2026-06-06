"""Adversarial commit gate — OS-level enforcement for code review before commit.

Prevents git commit of Python files without prior adversarial review in the
current session. This is a code-enforced gate for CLASS A failure pattern
(11 occurrences of skipping adversarial review, each resulting in real bugs).

The hook fires as PreToolUse on Bash commands containing 'git commit'. It
checks whether any .py files are staged AND whether an Agent tool (sub-agent
for adversarial review) has been invoked in this session. If not, it injects
an advisory warning — it does NOT block the commit (the agent can still proceed
if the user explicitly said to skip).

Exempt from gate:
- Commits with only .md, .json, .txt, .yaml, .toml files staged
- Commits in SwarmWS workspace (non-codebase)
- Commits where session_context shows adversarial_done=True

Public symbols:
- ``create_adversarial_commit_gate`` — factory returning the PreToolUse hook
"""

import logging
import subprocess
from pathlib import PurePosixPath
from typing import Any, Callable

logger = logging.getLogger(__name__)

# Extensions that are "docs-only" and don't require adversarial review
EXEMPT_EXTENSIONS = {".md", ".json", ".txt", ".yaml", ".toml", ".yml", ".csv"}


def create_adversarial_commit_gate(
    session_context: dict[str, Any],
) -> Callable[..., Any]:
    """Factory: returns a PreToolUse hook that gates git commit on adversarial review.

    The session_context dict is shared with the session — other hooks (like
    subagent_capture) set session_context["adversarial_done"] = True when an
    Agent tool is invoked for review purposes.

    Args:
        session_context: Mutable dict shared across the session lifetime.
                         Reads: adversarial_done (bool)
    """

    async def adversarial_commit_gate(
        input_data: dict[str, Any],
        tool_use_id: str | None,
        context: Any,
    ) -> dict[str, Any]:
        # Only fire on Bash tool with git commit
        if input_data.get("tool_name") != "Bash":
            return {"decision": "approve"}

        command = input_data.get("tool_input", {}).get("command", "")
        if "git commit" not in command:
            return {"decision": "approve"}

        # Check if adversarial already done this session
        if session_context.get("adversarial_done"):
            return {"decision": "approve"}

        # Check what's staged — are there Python files?
        try:
            result = subprocess.run(
                ["git", "diff", "--cached", "--name-only"],
                capture_output=True, text=True, timeout=5,
                cwd=session_context.get("work_dir"),
            )
            staged_files = [f.strip() for f in result.stdout.strip().split("\n") if f.strip()]
        except (subprocess.TimeoutExpired, OSError):
            # Can't determine staged files — let it through
            return {"decision": "approve"}

        if not staged_files:
            return {"decision": "approve"}

        # Check if ALL staged files are exempt (docs-only)
        has_code_files = False
        for f in staged_files:
            ext = PurePosixPath(f).suffix  # Robust: handles dotfiles, dirs with dots
            if ext not in EXEMPT_EXTENSIONS:
                has_code_files = True
                break

        if not has_code_files:
            return {"decision": "approve"}

        # Check if commit is in SwarmWS (non-codebase, exempt)
        work_dir = session_context.get("work_dir") or ""
        if work_dir.endswith("SwarmWS"):
            return {"decision": "approve"}

        # CODE FILES STAGED + NO ADVERSARIAL DONE = GATE FIRES
        logger.warning(
            "ADVERSARIAL_COMMIT_GATE: Code files staged for commit but no "
            "adversarial review detected in this session. Injecting warning."
        )

        staged_py = [f for f in staged_files if f.endswith(".py")]
        return {
            "decision": "approve",  # Advisory, not blocking
            "systemMessage": (
                "\n\n🚨 ADVERSARIAL COMMIT GATE: You are about to commit Python "
                f"files ({len(staged_py)} .py) WITHOUT running adversarial review "
                "this session. STEERING R13 requires: code→test→adversarial→fix→commit. "
                "11 prior occurrences of skipping adversarial each shipped real bugs. "
                "STOP and spawn an adversarial sub-agent NOW, or explicitly acknowledge "
                "you are bypassing this gate with user approval.\n\n"
            ),
        }

    return adversarial_commit_gate
