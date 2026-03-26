"""Shared LLM client for background jobs.

Uses ``claude --print`` (headless CLI) instead of direct boto3 InvokeModel.
This ensures all LLM calls go through the CLI's own auth/routing path,
which works from any geo location (no Anthropic geo-restriction).

Direct boto3 calls fail from China due to Anthropic's geo-blocking policy
on Bedrock InvokeModel, even with valid AWS credentials.

Key exports:

- ``llm_call``          -- Sync function: prompt in, text out
- ``llm_call_json``     -- Sync function: prompt in, parsed JSON out
- ``LLMCallError``      -- Raised on CLI failure (timeout, exit code, parse)
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


class LLMCallError(Exception):
    """Raised when a background LLM call fails."""

    def __init__(self, message: str, stderr: str = "", exit_code: int = -1):
        super().__init__(message)
        self.stderr = stderr
        self.exit_code = exit_code


# ---------------------------------------------------------------------------
# PATH fix for launchd (same pattern as executor.py)
# ---------------------------------------------------------------------------

def _get_path() -> str:
    """Get PATH that includes mise/npm/nvm bins.

    launchd gives a minimal PATH. We need to find claude CLI
    and Node >= 22 for MCP servers.
    """
    base_path = os.environ.get("PATH", "/usr/bin:/bin")

    # Add mise shims and installs
    home = Path.home()
    extra_dirs = [
        home / ".local/share/mise/shims",
        home / ".local/bin",
        Path("/opt/homebrew/bin"),
    ]
    for d in extra_dirs:
        if d.is_dir() and str(d) not in base_path:
            base_path = f"{d}:{base_path}"

    return base_path


def _resolve_claude() -> str | None:
    """Find the claude CLI binary."""
    path = shutil.which("claude", path=_get_path())
    if path:
        return path

    # Fallback: login shell discovery
    try:
        result = subprocess.run(
            ["zsh", "-lic", "which claude"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            resolved = result.stdout.strip().splitlines()[-1]
            if resolved and Path(resolved).is_file():
                return resolved
    except Exception:
        pass
    return None


def _build_env() -> dict[str, str]:
    """Build clean env for claude CLI subprocess."""
    env = os.environ.copy()
    env["PATH"] = _get_path()
    env["CLAUDE_CODE_USE_BEDROCK"] = "true"
    env.setdefault("AWS_REGION", "us-west-2")
    env.setdefault("AWS_DEFAULT_REGION", "us-west-2")

    # Strip proxy vars (Claude CLI manages its own proxy)
    for key in list(env.keys()):
        if "proxy" in key.lower():
            del env[key]

    # Disable CLI auto-memory
    env["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] = "1"
    return env


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def llm_call(
    prompt: str,
    *,
    model: str = "haiku",
    max_budget_usd: float = 0.50,
    timeout_seconds: int = 60,
    system_prompt: str | None = None,
) -> str:
    """Call an LLM via claude --print and return the text response.

    Args:
        prompt: User message to send
        model: Model name (haiku, sonnet, opus)
        max_budget_usd: Per-call spend cap
        timeout_seconds: Process timeout
        system_prompt: Optional system prompt

    Returns:
        Response text content

    Raises:
        LLMCallError: On CLI not found, non-zero exit, or timeout
    """
    claude_path = _resolve_claude()
    if not claude_path:
        raise LLMCallError("Claude CLI not found. Install: npm i -g @anthropic-ai/claude-code")

    cmd = [
        claude_path,
        "--print",
        "--output-format", "text",
        "--no-session-persistence",
        "--model", model,
        "--max-budget-usd", str(max_budget_usd),
    ]

    if system_prompt:
        cmd.extend(["--system-prompt", system_prompt])

    cmd.extend(["-p", prompt])

    env = _build_env()

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            env=env,
            cwd=str(Path.home() / ".swarm-ai" / "SwarmWS"),
        )
    except subprocess.TimeoutExpired:
        raise LLMCallError(f"LLM call timed out after {timeout_seconds}s")

    if proc.returncode != 0:
        raise LLMCallError(
            f"CLI exited {proc.returncode}: {proc.stderr[:300]}",
            stderr=proc.stderr,
            exit_code=proc.returncode,
        )

    return proc.stdout.strip()


def llm_call_json(
    prompt: str,
    *,
    model: str = "haiku",
    max_budget_usd: float = 0.50,
    timeout_seconds: int = 60,
    system_prompt: str | None = None,
) -> dict | list:
    """Call an LLM and parse the response as JSON.

    The prompt should instruct the model to output JSON only.
    Handles markdown code fences (```json ... ```) automatically.

    Returns:
        Parsed JSON (dict or list)

    Raises:
        LLMCallError: On CLI failure or JSON parse failure
    """
    text = llm_call(
        prompt,
        model=model,
        max_budget_usd=max_budget_usd,
        timeout_seconds=timeout_seconds,
        system_prompt=system_prompt,
    )

    # Strip markdown code fences
    cleaned = text.strip()
    if cleaned.startswith("```"):
        # Remove first line (```json or ```)
        cleaned = cleaned.split("\n", 1)[-1] if "\n" in cleaned else cleaned[3:]
    if cleaned.endswith("```"):
        cleaned = cleaned.rsplit("```", 1)[0]
    cleaned = cleaned.strip()

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise LLMCallError(
            f"Failed to parse LLM JSON response: {e}. Raw: {text[:200]}",
        )
