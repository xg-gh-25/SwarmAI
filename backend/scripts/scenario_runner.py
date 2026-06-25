"""Scenario Runner — capture a REAL agent's tool-call trajectory.

The self-eval LLM judge is a counterfactual static analyzer: it feeds the
agent's docs into the judge's own context, then asks "would a compliant agent
do X". That confirms doc EXISTENCE, never observed USAGE. This module closes
that gap: it spawns a real headless `claude` sub-session on a scenario prompt,
captures the actual tool calls it makes, and returns them as a trajectory list
that eval_trajectory() can assert against ("did the agent actually Read SELF.md?").

It reuses the PROVEN headless-spawn recipe from jobs/executor.py
(_handle_agent_task): same CLI resolution, env, --bare/--add-dir/cwd flags that
let the spawned agent read workspace files. Empirically validated (tracer
bullet): a spawn here actually Reads .context/SELF.md and emits a tool_use event.

Public:
- run_scenario(prompt, allowed_tools, timeout) -> list[str]   # spawn + capture
- parse_trajectory(stdout) -> list[str]                       # pure parse (testable)
"""
from __future__ import annotations

import json
import logging
import subprocess
import sys
from pathlib import Path

# Reuse executor's proven spawn helpers — do NOT re-derive CLI resolution / env.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from jobs.executor import (  # noqa: E402
    _resolve_claude_cli,
    _build_cli_env,
    _cli_supports_bare,
    SWARMWS,
)
# Reuse the canary safety filter so scenario prompts can't trigger exfiltration.
from scripts.eval_runner import _validate_canary_command  # noqa: E402

logger = logging.getLogger(__name__)

# Spawning a real agent is expensive; cap hard so a runaway scenario can't hang.
DEFAULT_TIMEOUT = 120


def parse_trajectory(stdout: str) -> list[str]:
    """Parse `claude --output-format stream-json` stdout into a trajectory list.

    Each non-empty line is a JSON event. Assistant events carry a `message`
    with a `content` list; `tool_use` blocks become "TOOL: <name> <input>"
    strings — exactly the shape eval_trajectory()'s substring/token matcher
    expects (e.g. "TOOL: Read {file_path: .../SELF.md}" matches "Read SELF.md").

    Garbage lines and non-tool events are ignored (robust to log noise).
    """
    trajectory: list[str] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(event, dict):
            continue
        message = event.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                name = block.get("name", "?")
                tool_input = block.get("input", {})
                try:
                    input_str = json.dumps(tool_input, ensure_ascii=False)
                except (TypeError, ValueError):
                    input_str = str(tool_input)
                trajectory.append(f"TOOL: {name} {input_str}")
    return trajectory


def run_scenario(
    prompt: str,
    allowed_tools: list[str] | None = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> list[str]:
    """Spawn a real headless agent on `prompt`, return its tool-call trajectory.

    Returns [] on any failure (CLI missing, unsafe prompt, timeout, non-zero
    exit) — the caller (eval_trajectory) then sees an empty trajectory and
    FAILS the assertion, which is the correct signal: "the agent did not
    perform the expected tool call."
    """
    # Safety: reject prompts that could trigger exfiltration / destruction.
    if _validate_canary_command(prompt) is not None:
        logger.warning("scenario_runner: prompt blocked by safety filter")
        return []

    claude_path = _resolve_claude_cli()
    if not claude_path:
        logger.warning("scenario_runner: claude CLI not found")
        return []

    tools = allowed_tools or ["Read"]
    cmd = [
        claude_path,
        "--print",
        *(["--bare"] if _cli_supports_bare(claude_path) else []),
        "--output-format", "stream-json",
        "--verbose",
        "--no-session-persistence",
        "--model", "sonnet",
        "--permission-mode", "bypassPermissions",
        "--strict-mcp-config",          # ignore user-level settings; tools below only
        "--add-dir", str(SWARMWS),      # REQUIRED: lets the agent Read workspace files
        "--allowedTools", ",".join(tools),
        "-p", prompt,
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=_build_cli_env(None),
            cwd=str(SWARMWS),           # REQUIRED: relative Reads resolve here
        )
    except subprocess.TimeoutExpired:
        logger.warning("scenario_runner: spawn timed out after %ss", timeout)
        return []
    except Exception as exc:  # noqa: BLE001 — spawn failure must not crash eval
        logger.warning("scenario_runner: spawn failed: %s: %s", type(exc).__name__, exc)
        return []

    if proc.returncode != 0:
        logger.warning("scenario_runner: claude exit %s: %s",
                       proc.returncode, (proc.stderr or "")[:200])
        # Still parse stdout — a non-zero exit may follow valid tool calls.

    return parse_trajectory(proc.stdout or "")
