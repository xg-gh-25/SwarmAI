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

# Default per-scenario timeout for the run_scenario/run_scenario_full wrappers.
# ⚠️ NOT the production eval path: eval_trajectory_capture (eval_runner.py) passes
# an EXPLICIT timeout (case.get("scenario_timeout", 240)) — THAT is the operative
# value for behavior-eval spawns. This default only bounds the test-only wrapper
# callers. Kept aligned at 240 for consistency. Cold real-agent turns run 82-95s
# (run_e6921209); a hung spawn is still hard-bounded here.
DEFAULT_TIMEOUT_SECONDS = 240


class ScenarioInfraError(RuntimeError):
    """The scenario could not be RUN (CLI missing, timeout, spawn crash, unsafe
    prompt, or non-zero exit with zero tool calls) — an INFRA/config failure,
    NOT "the agent ran and chose not to act". Callers must score this `error`
    (red infra signal), never a behavior `failed`. Mirrors the eval_llm_judge
    "infra failure = error not skip/fail" lesson so transient throttling can't
    silently lie the health score.
    """


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


def parse_final_text(stdout: str) -> str:
    """Extract the agent's final assistant text from stream-json stdout.

    Used by the decision-class behavior case: proving a Read happened is not
    enough — we must check the agent's CONCLUSION actually reflects the read
    content (else "read IMPROVEMENT.md then ignored it and recommended the
    big-bang rewrite" would falsely pass). This returns the concatenated text
    blocks of assistant messages (the agent's spoken answer), so a caller can
    assert expected_response_contains against it.

    Returns "" if no assistant text is present (a caller asserting on content
    then fails closed — the agent produced no usable conclusion).
    """
    # The terminal `result` event carries the final answer VERBATIM and is the
    # authoritative conclusion. Assistant `text` blocks are emitted along the
    # way and the LAST one is duplicated by the result event — and intermediate
    # blocks may be discarded "thinking out loud" ("a big-bang would be fast…")
    # that must NOT pollute a content-keyword match. So: prefer the result event
    # when present; fall back to concatenated text blocks only if there is none
    # (adversarial Gate-2 LOW V1: avoid double-count + thinking-text pollution).
    result_text: str | None = None
    block_texts: list[str] = []
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
        if event.get("type") == "result" and isinstance(event.get("result"), str):
            result_text = event["result"]
            continue
        message = event.get("message")
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                t = block.get("text")
                if isinstance(t, str):
                    block_texts.append(t)
    if result_text is not None:
        return result_text
    return "\n".join(block_texts)


def run_scenario_full(
    prompt: str,
    allowed_tools: list[str] | None = None,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> tuple[list[str], str]:
    """Spawn a headless agent and return BOTH its tool-call trajectory AND its
    final answer text.

    The decision-class behavior case needs the answer text to verify the read
    content actually DROVE the conclusion (not just that a Read happened). Most
    callers only need the trajectory and use ``run_scenario`` (which wraps this).

    Returns:
        (trajectory, final_text). Raises ScenarioInfraError on any infra failure
        (CLI missing, timeout, spawn crash, unsafe prompt, or non-zero exit with
        zero tool calls) so the caller scores `error`, never a misleading `failed`.
    """
    # Defense-in-depth: this filter is a SHELL-command blocklist (curl/rm -rf/…).
    # Against a natural-language prompt it catches only accidentally-embedded
    # shell strings — it is NOT the real safety boundary. The real protection is
    # the read-only tool lock applied by the caller (eval_trajectory_capture
    # intersects allowed_tools with {Read,Grep,Glob}) + bypassPermissions only
    # granting those. Kept as cheap belt-and-suspenders (adversarial Gate-2 LOW).
    if _validate_canary_command is not None:
        safety_error = _validate_canary_command(prompt)
        if safety_error:
            logger.warning("scenario_runner: prompt rejected by safety filter: %s", safety_error)
            raise ScenarioInfraError(f"prompt rejected by safety filter: {safety_error}")

    claude_path = _resolve_claude_cli()
    if not claude_path:
        logger.error(
            "scenario_runner: claude CLI not found — cannot run behavior scenario. "
            "Install: npm i -g @anthropic-ai/claude-code"
        )
        raise ScenarioInfraError("claude CLI not found")

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
        raise ScenarioInfraError(f"spawn timed out after {timeout}s")
    except Exception as e:  # spawn failure = infra, not behavior
        logger.error("scenario_runner: spawn failed: %s: %s", type(e).__name__, e)
        raise ScenarioInfraError(f"spawn failed: {type(e).__name__}: {e}") from e

    trajectory = parse_trajectory(proc.stdout or "")
    final_text = parse_final_text(proc.stdout or "")
    if proc.returncode != 0:
        logger.warning(
            "scenario_runner: claude exited %s: %s",
            proc.returncode, (proc.stderr or "")[:200],
        )
        # Non-zero exit with NO tool calls parsed = the run did not actually
        # execute (infra/auth/throttle), NOT "agent chose not to read". Raise
        # so it scores `error`, not a misleading behavior `failed`. If tools
        # WERE emitted before the failure, keep them (partial real behavior).
        if not trajectory:
            raise ScenarioInfraError(
                f"claude exited {proc.returncode} with no tool calls: "
                f"{(proc.stderr or '')[:150]}"
            )

    return trajectory, final_text


def run_scenario(
    prompt: str,
    allowed_tools: list[str] | None = None,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> list[str]:
    """Spawn a headless agent on ``prompt`` and return its tool-call trajectory.

    Thin wrapper over ``run_scenario_full`` for the common case (trajectory
    only). Signature preserved for existing callers.

    Returns:
        list[str]: tool-call trajectory, one string per tool_use. Raises
        ScenarioInfraError on infra failure (caller scores `error`).
    """
    trajectory, _ = run_scenario_full(prompt, allowed_tools=allowed_tools, timeout=timeout)
    return trajectory
