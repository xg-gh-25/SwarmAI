"""Headless agent scenario runner — captures a REAL tool-call trajectory.

This is the engine behind behavior-trajectory eval cases. Instead of asking an
LLM judge "would a compliant agent read the DDD doc?" (circular — the judge is
handed the answer), this spawns a REAL headless agent on a scenario prompt and
records what tools it ACTUALLY called. The captured trajectory is then matched
against expected_trajectory by the existing eval_trajectory() evaluator.

Why a separate module (not jobs.executor): executor._handle_agent_task is bound
to the Job/JobResult/SchedulerState types and uses --output-format json (no
per-tool events). We need --output-format stream-json --verbose to see each
tool_use. But we REUSE executor's hard-won spawn helpers (_resolve_claude_cli,
_build_cli_env, _cli_supports_bare, SWARMWS) so the spawn runs in the workspace
with file-read access — Gate 1 (run_75b656c1) flagged that omitting cwd=SWARMWS
+ --add-dir makes every Read fail deterministically.

Verified empirically (tracer bullet, run_75b656c1): a live spawn with these
flags read .context/SELF.md and emitted a parseable Read tool_use event.

Key public symbols:
- ``parse_trajectory`` — pure parser: stream-json text -> list[str]
- ``run_scenario`` — spawn headless agent on a prompt, return tool-call trajectory
"""
from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path

# Reuse executor's proven spawn helpers — do NOT re-derive the CLI resolution,
# env construction, or bare-flag detection (Gate 1: under-specified spawn =
# Reads fail). SWARMWS is the workspace root the agent must run inside.
from jobs.executor import (
    _resolve_claude_cli,
    _build_cli_env,
    _cli_supports_bare,
    SWARMWS,
)

logger = logging.getLogger(__name__)

# Reuse the canary command safety filter as defense-in-depth. Prompts come from
# golden_set.yaml (trusted local data), but a prompt that embeds a destructive
# command string should never reach a bypassPermissions spawn.
try:
    from scripts.eval_runner import _validate_canary_command
except Exception:  # pragma: no cover - import path fallback
    try:
        from eval_runner import _validate_canary_command  # type: ignore
    except Exception:  # pragma: no cover
        _validate_canary_command = None  # type: ignore

# Default per-scenario timeout. A real agent turn with 1-3 tool calls completes
# well under this; cap prevents a hung spawn from stalling the eval run.
DEFAULT_TIMEOUT_SECONDS = 120


def parse_trajectory(stdout: str) -> list[str]:
    """Parse `claude --output-format stream-json` stdout into a trajectory.

    Each non-empty line is a JSON event. Assistant messages carry a ``content``
    list; each ``tool_use`` block becomes one trajectory string of the form
    ``"<ToolName> <input-json>"`` (e.g. ``Read {"file_path": ".../SELF.md"}``).
    This is exactly the shape eval_trajectory()'s substring/token matcher
    expects ("Read SELF.md" matches "Read {...SELF.md}").

    Malformed lines are skipped (never crash). No tool_use -> empty list, which
    is the correct NEGATIVE control: an agent that answered from memory without
    reading anything produces an empty trajectory and fails a Read assertion.
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
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                name = block.get("name", "")
                tool_input = block.get("input", {})
                try:
                    input_str = json.dumps(tool_input, ensure_ascii=False)
                except (TypeError, ValueError):
                    input_str = str(tool_input)
                trajectory.append(f"{name} {input_str}".strip())
    return trajectory


def run_scenario(
    prompt: str,
    allowed_tools: list[str] | None = None,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> list[str]:
    """Spawn a headless agent on ``prompt`` and return its tool-call trajectory.

    Args:
        prompt: The scenario the agent should act on (from golden_set case).
        allowed_tools: Tools the agent may use (e.g. ["Read", "Grep"]). Limits
            blast radius and keeps the trajectory focused on the behavior tested.
        timeout: Hard cap in seconds. On timeout, returns [] (the run did not
            demonstrate the behavior -> the assertion correctly fails).

    Returns:
        list[str]: tool-call trajectory, one string per tool_use. Empty list if
        the agent used no tools, the prompt failed the safety filter, the CLI
        is missing, or the spawn timed out/failed. An empty trajectory makes a
        Read-assertion fail cleanly rather than crashing the eval run.
    """
    # Defense-in-depth safety gate (prompts are trusted but bypassPermissions
    # is powerful — refuse a prompt embedding a destructive command). A rejected
    # prompt returns [] (the scenario did not run -> assertion fails cleanly),
    # never crashing the surrounding eval run.
    if _validate_canary_command is not None:
        safety_error = _validate_canary_command(prompt)
        if safety_error:
            logger.warning("scenario_runner: prompt rejected by safety filter: %s", safety_error)
            return []

    claude_path = _resolve_claude_cli()
    if not claude_path:
        logger.error(
            "scenario_runner: claude CLI not found — cannot run behavior scenario. "
            "Install: npm i -g @anthropic-ai/claude-code"
        )
        return []

    tools = allowed_tools or ["Read"]
    cmd = [
        claude_path,
        "--print",
        *(["--bare"] if _cli_supports_bare(claude_path) else []),
        "--output-format", "stream-json",
        "--verbose",  # required for stream-json to emit per-tool events
        "--no-session-persistence",
        "--model", "sonnet",
        "--permission-mode", "bypassPermissions",
        "--strict-mcp-config",  # ignore user Claude settings; tools below only
        "--allowedTools", ",".join(tools),
        # CRITICAL (Gate 1): grant read access to the workspace, else every
        # Read of SELF.md / DDD docs is denied -> all behavior cases fail.
        "--add-dir", str(SWARMWS),
        "-p", prompt,
    ]

    env = _build_cli_env(None)
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            cwd=str(SWARMWS),  # CRITICAL (Gate 1): run INSIDE the workspace
        )
    except subprocess.TimeoutExpired:
        logger.warning("scenario_runner: spawn timed out after %ss", timeout)
        return []
    except Exception as e:  # spawn failure -> no trajectory (assertion fails)
        logger.error("scenario_runner: spawn failed: %s: %s", type(e).__name__, e)
        return []

    if proc.returncode != 0:
        logger.warning(
            "scenario_runner: claude exited %s: %s",
            proc.returncode, (proc.stderr or "")[:200],
        )
        # Still attempt to parse — partial trajectory may have been emitted.

    return parse_trajectory(proc.stdout or "")
