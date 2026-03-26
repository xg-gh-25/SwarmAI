"""
Job Executor

Routes jobs to their handlers with error isolation.
Each job runs in a try/except — one failure never affects another.

Handlers:
  signal_fetch  — httpx adapters (RSS, GitHub, HN)
  signal_digest — Bedrock Haiku relevance scoring
  agent_task    — Headless Claude CLI subprocess with MCP tools
  script        — Deterministic subprocess (no AI, no tokens)
  maintenance   — Prune caches, reset counters
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .models import (
    Feed,
    Job,
    JobResult,
    JobState,
    RawSignal,
    SchedulerDefaults,
    SchedulerState,
)

logger = logging.getLogger("swarm.jobs.executor")

from .paths import SWARMWS, JOB_RESULTS_DIR, JOB_RESULTS_JSONL, MCPS_DIR, DB_PATH


# ── Module-level PATH fix ────────────────────────────────────────────
# GUI apps (Tauri) don't inherit login shell PATH.  credential_process
# tools (ada/toolbox), mise shims, and npm globals are invisible.
# Resolve once at import time so ALL functions (pre-flight boto3 check,
# CLI subprocess, script jobs) see the full PATH.
def _fix_path_from_login_shell() -> None:
    """Merge login shell PATH into os.environ so child processes and
    credential_process (ada) work regardless of how we were launched."""
    try:
        result = subprocess.run(
            ["zsh", "-lic", "echo $PATH"],
            capture_output=True, text=True, timeout=10,
        )
        shell_path = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ""
        if shell_path:
            os.environ["PATH"] = shell_path
    except Exception:
        pass  # Fall through — keep inherited PATH

_fix_path_from_login_shell()

# Sonnet pricing (us.anthropic.claude-sonnet-4) — update when model changes
_SONNET_INPUT_PRICE = 3.0 / 1_000_000   # $3 per 1M input tokens
_SONNET_OUTPUT_PRICE = 15.0 / 1_000_000  # $15 per 1M output tokens
_HAIKU_INPUT_PRICE = 0.25 / 1_000_000
_HAIKU_OUTPUT_PRICE = 1.25 / 1_000_000


def execute_job(
    job: Job,
    state: SchedulerState,
    feeds: list[Feed],
    user_context: str = "",
    defaults: SchedulerDefaults | None = None,
    known_job_ids: set[str] | None = None,
) -> JobResult:
    """
    Dispatch a job to its handler. Fully isolated — catches all exceptions.

    Args:
        job: Job definition from jobs.yaml
        state: Mutable scheduler state
        feeds: Feed configs (for signal_fetch jobs)
        user_context: User context summary (for signal_digest jobs)
        defaults: Global scheduler defaults (for budget checks)
        known_job_ids: All valid job IDs (for maintenance orphan cleanup)

    Returns:
        JobResult — always returns, never raises
    """
    logger.info(f"Executing job '{job.id}' (type: {job.type})")
    start = datetime.now(timezone.utc)

    if defaults is None:
        defaults = SchedulerDefaults()

    try:
        if job.type == "signal_fetch":
            from .handlers.signal_fetch import handle_signal_fetch
            max_age = job.config.get("max_age_hours", 48)
            result = handle_signal_fetch(feeds, state, max_age_hours=max_age)

        elif job.type == "signal_digest":
            from .handlers.signal_digest import handle_signal_digest
            window_days = job.config.get("window_days")
            result = handle_signal_digest(
                state, user_context=user_context, window_days=window_days,
            )

        elif job.type == "agent_task":
            budget_err = _check_monthly_budget(state, defaults)
            daily_err = _check_daily_agent_limit(state, defaults) if not budget_err else None
            gate_err = budget_err or daily_err
            if gate_err:
                result = JobResult(
                    job_id=job.id, timestamp=start,
                    status="skipped", summary=gate_err,
                    duration_seconds=0,
                )
            else:
                result = _handle_agent_task(job, state)

        elif job.type == "script":
            result = _handle_script(job, state)

        elif job.type == "maintenance":
            result = _handle_maintenance(job, state, known_job_ids=known_job_ids)

        elif job.type == "notify":
            result = _handle_notify(job, state)

        else:
            result = JobResult(
                job_id=job.id, timestamp=start,
                status="skipped",
                summary=f"Unknown job type: {job.type}",
                duration_seconds=0,
            )

        _update_job_state(state, job.id, result)

        # Post-job notification (if configured via config.notify)
        if result.status in ("success", "failed"):
            try:
                send_post_job_notification(job, result)
            except Exception as notify_err:
                logger.warning(f"Post-job notification failed for '{job.id}': {notify_err}")

        return result

    except Exception as e:
        duration = (datetime.now(timezone.utc) - start).total_seconds()
        error_msg = f"Job '{job.id}' crashed: {type(e).__name__}: {e}"
        logger.error(error_msg, exc_info=True)

        result = JobResult(
            job_id=job.id,
            timestamp=datetime.now(timezone.utc),
            status="failed",
            summary=error_msg,
            duration_seconds=duration,
            error=str(e),
        )
        _update_job_state(state, job.id, result)
        return result


# ── Agent Task Handler ───────────────────────────────────────────────


def _get_aws_credentials() -> dict[str, str]:
    """Try to get temporary AWS credentials via boto3 (credential_process → ada).

    Returns a dict of env vars (AWS_ACCESS_KEY_ID, etc.) if boto3 resolves.
    Returns empty dict if boto3 fails — caller falls through to CLI's own auth
    (SSO IdC tokens), which works even when ada/Isengard is unreachable.

    Two credential chains coexist on this machine:
      1. boto3 → credential_process → ada → Isengard (needs VPN/DNS)
      2. Claude CLI → AWS SSO IdC tokens (local files, auto-refreshed)

    We try to inject boto3 creds so the CLI uses the same chain as signal_digest.
    If boto3 fails (VPN off, Isengard unreachable), the CLI still works via its
    own SSO IdC tokens. Best of both worlds — no single point of failure.
    """
    try:
        import boto3

        session = boto3.Session()
        credentials = session.get_credentials()
        if credentials is None:
            logger.debug("boto3: no credentials found, CLI will use its own auth")
            return {}

        frozen = credentials.get_frozen_credentials()
        if not frozen.access_key:
            logger.debug("boto3: empty access_key, CLI will use its own auth")
            return {}

        creds = {
            "AWS_ACCESS_KEY_ID": frozen.access_key,
            "AWS_SECRET_ACCESS_KEY": frozen.secret_key,
        }
        if frozen.token:
            creds["AWS_SESSION_TOKEN"] = frozen.token

        logger.info("boto3 credentials resolved — injecting into CLI env")
        return creds

    except Exception as e:
        logger.debug("boto3 credential resolution failed (%s), CLI will use its own auth", e)
        return {}


def _resolve_claude_cli() -> str | None:
    """Find the claude CLI binary, checking login shell PATH.

    GUI apps (Tauri) don't inherit shell PATH, so shutil.which() may
    miss binaries installed via npm/mise/nvm.  Fall back to login shell
    discovery, same pattern as backend/core/claude_environment.py.
    """
    path = shutil.which("claude")
    if path:
        return path

    # Login shell PATH discovery (macOS: zsh -lic)
    try:
        result = subprocess.run(
            ["zsh", "-lic", "which claude"],
            capture_output=True, text=True, timeout=10,
        )
        resolved = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ""
        if resolved and os.path.isfile(resolved):
            return resolved
    except Exception:
        pass

    return None


def _load_mcp_config() -> dict:
    """Load MCP config using the same two-layer merge as SwarmAI chat sessions.

    Reads:
      1. .claude/mcps/mcp-catalog.json (product-seeded, default enabled=false)
      2. .claude/mcps/mcp-dev.json (user/dev, default enabled=true)

    Merges: dev overrides catalog by id, filters disabled entries.
    Converts to Claude CLI format: {"mcpServers": {"name": {...}}}

    Returns empty dict if no MCP files exist (graceful no-op).
    """
    mcps_dir = MCPS_DIR
    catalog_path = mcps_dir / "mcp-catalog.json"
    dev_path = mcps_dir / "mcp-dev.json"

    # Read both layers
    def _read_layer(path: Path, default_enabled: bool) -> list[dict]:
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, list):
                return []
            for entry in data:
                if isinstance(entry, dict) and "enabled" not in entry:
                    entry["enabled"] = default_enabled
            return data
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Cannot read MCP layer %s: %s", path, e)
            return []

    catalog = _read_layer(catalog_path, default_enabled=False)
    dev = _read_layer(dev_path, default_enabled=True)

    # Merge: dev overrides catalog by id, filter disabled
    merged: dict[str, dict] = {}
    for entry in catalog:
        eid = entry.get("id")
        if eid:
            merged[eid] = entry
    for entry in dev:
        eid = entry.get("id")
        if eid:
            merged[eid] = entry

    enabled = [e for e in merged.values() if e.get("enabled") is not False]

    if not enabled:
        return {}

    # Convert to Claude CLI mcpServers format
    mcp_servers = {}
    for entry in enabled:
        name = entry.get("name", entry.get("id", "unknown"))
        conn_type = entry.get("connection_type", "stdio")
        config = entry.get("config", {})

        if conn_type == "stdio":
            command = config.get("command")
            if not command:
                continue
            server = {
                "type": "stdio",
                "command": command,
                "args": [os.path.expandvars(a) for a in config.get("args", [])],
            }
            env = config.get("env")
            if env and isinstance(env, dict):
                server["env"] = {
                    k: os.path.expandvars(v) if isinstance(v, str) else v
                    for k, v in env.items()
                }
            mcp_servers[name] = server
        elif conn_type in ("sse", "http"):
            url = config.get("url")
            if url:
                mcp_servers[name] = {"type": conn_type, "url": url}

    return {"mcpServers": mcp_servers} if mcp_servers else {}


def _handle_agent_task(job: Job, state: SchedulerState) -> JobResult:
    """Execute a job via headless Claude CLI subprocess.

    Uses `claude --print` (non-interactive, stdout output).
    MCP config loaded from SwarmWS .claude/mcps/ (same two-layer merge as chat sessions).
    Per-job tool restriction via --allowedTools.
    """
    start = datetime.now(timezone.utc)
    safety = job.safety

    # Pre-flight: CLI must exist
    claude_path = _resolve_claude_cli()
    if not claude_path:
        return JobResult(
            job_id=job.id, timestamp=start,
            status="failed",
            summary="Claude CLI not found. Install: npm i -g @anthropic-ai/claude-code",
            duration_seconds=0,
            error="cli_not_found",
        )

    # Credential resolution: try boto3 first (same chain as signal_digest),
    # fall through to CLI's own SSO IdC auth if boto3 fails.
    # No hard-fail — at least one chain should work.
    aws_creds = _get_aws_credentials()

    prompt = job.config.get("prompt", "")
    if not prompt:
        return JobResult(
            job_id=job.id, timestamp=start,
            status="failed", summary="No prompt configured",
            duration_seconds=0,
        )

    # Build CLI command (use resolved absolute path, not bare "claude")
    cmd = [
        claude_path,
        "--print",
        "--output-format", "json",
        "--no-session-persistence",
        "--model", "sonnet",
        "--max-budget-usd", str(safety.max_budget_usd),
    ]

    # MCP config: load from SwarmWS .claude/mcps/ (same merge as chat sessions)
    # Write to temp file for --mcp-config, use --strict-mcp-config to ignore
    # user-level Claude settings and only use our consolidated config.
    mcp_config = _load_mcp_config()
    mcp_config_file = None

    if safety.allowed_tools and mcp_config:
        # Write consolidated MCP config to temp file
        mcp_config_file = tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", prefix="swarm-mcp-",
            delete=False,
        )
        json.dump(mcp_config, mcp_config_file)
        mcp_config_file.close()
        cmd.extend([
            "--mcp-config", mcp_config_file.name,
            "--strict-mcp-config",
            "--allowedTools", ",".join(safety.allowed_tools),
        ])
    elif safety.allowed_tools:
        # Job wants tools but no MCP config available
        logger.warning("Job '%s' requests tools but no MCP config found", job.id)
        cmd.extend(["--allowedTools", ",".join(safety.allowed_tools)])
    else:
        # No tools needed — skip MCP startup entirely
        cmd.append("--strict-mcp-config")

    # --add-dir grants read access to SwarmWS files if needed
    cmd.extend(["--add-dir", str(SWARMWS)])

    # System prompt: minimal context for headless execution
    system = (
        f'You are SwarmAI running scheduled job: "{job.name}".\n'
        f"Execute concisely. Results matter, not explanations.\n"
        f"Time: {start.isoformat()}\n"
        f"Budget: ${safety.max_budget_usd:.2f} max spend."
    )
    cmd.extend(["--system-prompt", system])
    cmd.extend(["-p", prompt])

    env = _build_cli_env(aws_creds)
    logger.info(f"CLI command: {' '.join(cmd[:6])}... (timeout={safety.timeout_seconds}s)")

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=safety.timeout_seconds,
            env=env,
            cwd=str(SWARMWS),
        )

        duration = (datetime.now(timezone.utc) - start).total_seconds()

        if proc.returncode != 0:
            error_text = proc.stderr or proc.stdout
            return JobResult(
                job_id=job.id, timestamp=start,
                status="failed",
                summary=f"CLI exit code {proc.returncode}: {error_text[:200]}",
                duration_seconds=duration,
                error=error_text[:500],
            )

        # Parse structured output
        output = _parse_cli_output(proc.stdout)

        # Extract token usage — CLI nests it differently than raw API
        usage = output.get("usage", {})
        tokens_in = usage.get("input_tokens", 0)
        tokens_out = usage.get("output_tokens", 0)
        # Also check modelUsage for more accurate counts
        model_usage = output.get("modelUsage", {})
        for model_info in model_usage.values():
            tokens_in = max(tokens_in, model_info.get("inputTokens", 0))
            tokens_out = max(tokens_out, model_info.get("outputTokens", 0))

        # Use actual cost from CLI if available, otherwise estimate
        actual_cost = output.get("total_cost_usd", 0)
        if actual_cost:
            state.monthly_spend_usd += actual_cost
        else:
            cost = _estimate_cost(tokens_in, tokens_out, model="sonnet")
            state.monthly_spend_usd += cost

        # Extract result text — CLI puts agent output in "result" field
        result_text = output.get("result", "")
        if not result_text or result_text.startswith("API Error"):
            # No usable result — might be budget exceeded or error
            result_text = ""

        # Detect budget exceeded — job ran but didn't finish
        subtype = output.get("subtype", "")
        if subtype == "error_max_budget_usd":
            status = "partial"
            num_turns = output.get("num_turns", 0)
            cost_usd = output.get("total_cost_usd", 0)
            summary = (
                f"Budget exceeded after {num_turns} turns (${cost_usd:.2f}). "
                f"Increase safety.max_budget_usd if task needs more turns."
            )
            if result_text:
                summary += f"\nPartial result: {result_text[:150]}"
        else:
            status = "success"
            summary = result_text[:200] if result_text else f"Completed in {duration:.0f}s"

        # Write results (even partial ones are useful)
        output_path = None
        if result_text:
            output_path = _write_job_result(
                job, result_text, start, tokens_in + tokens_out, duration,
            )

        # If signal mode, inject into raw_signals buffer
        if job.config.get("output_as_signals", False) and result_text:
            signals = _extract_signals_from_output(job, result_text)
            state.raw_signals.extend(signals)

        # Auto-create Radar todos from agent_task results (e.g. inbox → email todos)
        if job.config.get("create_todos", False) and result_text and status == "success":
            _create_todos_from_result(job, result_text)

        return JobResult(
            job_id=job.id, timestamp=start,
            status=status,
            summary=summary,
            output_path=str(output_path) if output_path else None,
            tokens_used=tokens_in + tokens_out,
            duration_seconds=duration,
        )

    except subprocess.TimeoutExpired:
        duration = (datetime.now(timezone.utc) - start).total_seconds()
        return JobResult(
            job_id=job.id, timestamp=start,
            status="failed",
            summary=f"Timeout after {safety.timeout_seconds}s",
            duration_seconds=duration,
            error="timeout",
        )

    finally:
        # Clean up temp MCP config file
        if mcp_config_file is not None:
            try:
                os.unlink(mcp_config_file.name)
            except OSError:
                pass


# ── Script Handler ───────────────────────────────────────────────────


def _handle_script(job: Job, state: SchedulerState) -> JobResult:
    """Execute a script job via subprocess. No AI, no tokens."""
    start = datetime.now(timezone.utc)

    command = job.config.get("command")
    if not command:
        return JobResult(
            job_id=job.id, timestamp=start,
            status="failed", summary="No command configured",
            duration_seconds=0,
        )

    timeout = job.safety.timeout_seconds or 120

    # Script jobs run from the swarm-jobs directory (where venv and scripts live),
    # not from SwarmWS root. Use config.cwd to override if needed.
    script_cwd = job.config.get("cwd", str(Path(__file__).parent))

    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=script_cwd,
            env=_build_script_env(),
        )

        duration = (datetime.now(timezone.utc) - start).total_seconds()
        output = result.stdout[-2000:] if result.stdout else ""
        stderr = result.stderr[-500:] if result.stderr else ""

        if result.returncode != 0:
            return JobResult(
                job_id=job.id, timestamp=start,
                status="failed",
                summary=f"Exit code {result.returncode}: {stderr or output[:200]}",
                duration_seconds=duration,
                error=f"exit={result.returncode}",
            )

        # Write result if output mode is "report"
        if job.config.get("output_mode", "report") == "report" and output.strip():
            _write_job_result(job, output, start, tokens=0, duration=duration)

        return JobResult(
            job_id=job.id, timestamp=start,
            status="success",
            summary=f"Script completed ({duration:.1f}s): {output[:200]}",
            duration_seconds=duration,
        )

    except subprocess.TimeoutExpired:
        duration = (datetime.now(timezone.utc) - start).total_seconds()
        return JobResult(
            job_id=job.id, timestamp=start,
            status="failed",
            summary=f"Script timed out after {timeout}s",
            duration_seconds=duration,
            error="timeout",
        )


# ── Maintenance Handler ──────────────────────────────────────────────


def _handle_maintenance(
    job: Job, state: SchedulerState, known_job_ids: set[str] | None = None,
) -> JobResult:
    """Maintenance job: prune old state, trim caches, clean orphans."""
    from .dedup import trim_dedup_cache

    start = datetime.now(timezone.utc)
    actions = []

    # Prune orphaned job state (jobs removed from yaml but state lingers)
    if known_job_ids:
        orphaned = [jid for jid in state.jobs if jid not in known_job_ids]
        for jid in orphaned:
            del state.jobs[jid]
        if orphaned:
            actions.append(f"Pruned orphaned state: {', '.join(orphaned)}")

    # Trim dedup cache
    before = len(state.dedup_cache)
    state.dedup_cache = trim_dedup_cache(state.dedup_cache, max_size=300)
    after = len(state.dedup_cache)
    if before != after:
        actions.append(f"Trimmed dedup cache: {before} → {after}")

    # Clear stale raw signals (shouldn't happen, but safety)
    if len(state.raw_signals) > 100:
        state.raw_signals = state.raw_signals[-50:]
        actions.append("Trimmed raw signal buffer to 50")

    # Reset monthly spend on 1st of month
    current_month = start.strftime("%Y-%m")
    if state.monthly_reset_date != current_month:
        old_spend = state.monthly_spend_usd
        old_tokens = state.monthly_tokens_used
        state.monthly_spend_usd = 0.0
        state.monthly_tokens_used = 0
        state.monthly_reset_date = current_month
        if old_spend > 0 or old_tokens > 0:
            actions.append(f"Reset monthly spend (was ${old_spend:.2f}, {old_tokens} tokens)")

    # Expire stale pending todos (>30 days)
    todo_result = _expire_stale_todos(max_age_days=30)
    if todo_result:
        actions.append(todo_result)

    # Trim JSONL file (cap at 500 entries)
    if JOB_RESULTS_JSONL.exists():
        lines = JOB_RESULTS_JSONL.read_text().strip().split("\n")
        if len(lines) > 500:
            JOB_RESULTS_JSONL.write_text("\n".join(lines[-500:]) + "\n")
            actions.append(f"Trimmed job results JSONL: {len(lines)} → 500")

    # LLM-powered memory health (weekly — the expensive but smart part)
    try:
        from .handlers.memory_health import run_memory_health
        health_result = run_memory_health()
        if health_result.get("status") == "success":
            health_actions = health_result.get("actions", [])
            actions.extend(health_actions)
            if not health_actions:
                actions.append("Memory health: all clear")
        elif health_result.get("status") == "error":
            actions.append(f"Memory health error: {health_result.get('error', 'unknown')}")
    except Exception as e:
        logger.warning("Memory health check failed (non-blocking): %s", e)
        actions.append(f"Memory health skipped: {e}")

    summary = "; ".join(actions) if actions else "No maintenance needed"
    duration = (datetime.now(timezone.utc) - start).total_seconds()

    return JobResult(
        job_id=job.id,
        timestamp=datetime.now(timezone.utc),
        status="success",
        summary=summary,
        duration_seconds=duration,
    )


# ── Notify Handler ──────────────────────────────────────────────────


def _handle_notify(job: Job, state: SchedulerState) -> JobResult:
    """Send a notification via Slack DM.

    Config:
      channel: "slack" (only supported channel for now)
      message: text to send
      job_ref: optional job_id whose last result to include
    """
    start = datetime.now(timezone.utc)
    channel = job.config.get("channel", "slack")
    message = job.config.get("message", "")

    if channel != "slack":
        return JobResult(
            job_id=job.id, timestamp=start, status="failed",
            summary=f"Unsupported notify channel: {channel}",
            duration_seconds=0,
        )

    # If job_ref is set, include that job's last result
    job_ref = job.config.get("job_ref")
    if job_ref and not message:
        js = state.jobs.get(job_ref)
        if js:
            message = f"Job '{job_ref}' last ran at {js.last_run} — status: {js.last_status}"
        else:
            message = f"Job '{job_ref}' has no run history yet."

    if not message:
        return JobResult(
            job_id=job.id, timestamp=start, status="skipped",
            summary="No message to send", duration_seconds=0,
        )

    result = _send_slack_dm(message)
    duration = (datetime.now(timezone.utc) - start).total_seconds()

    if result:
        return JobResult(
            job_id=job.id, timestamp=start, status="success",
            summary=f"Slack DM sent: {message[:100]}", duration_seconds=duration,
        )
    else:
        return JobResult(
            job_id=job.id, timestamp=start, status="failed",
            summary="Failed to send Slack DM", duration_seconds=duration,
            error="slack_send_failed",
        )


def _get_slack_dm_channel() -> str | None:
    """Read the owner's DM channel from Slack bot config.json."""
    config_path = SWARMWS / "Services" / "slack-bot" / "config.json"
    try:
        if config_path.exists():
            data = json.loads(config_path.read_text(encoding="utf-8"))
            return data.get("owner_dm_channel") or None
    except (json.JSONDecodeError, OSError):
        pass
    return None


def _send_slack_dm(message: str) -> bool:
    """Send a Slack DM to the user via the slack-mcp CLI tool.

    Uses the same MCP config as chat sessions. Falls back to a simple
    webhook if MCP isn't available.
    """
    # Try slack-mcp via Claude CLI (short budget, single tool)
    claude_path = _resolve_claude_cli()
    if not claude_path:
        logger.warning("Slack DM: Claude CLI not found, cannot send")
        return False

    mcp_config = _load_mcp_config()
    if not mcp_config or "slack-mcp" not in mcp_config.get("mcpServers", {}):
        logger.warning("Slack DM: slack-mcp not in MCP config, cannot send")
        return False

    mcp_config_file = None
    try:
        mcp_config_file = tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", prefix="swarm-notify-",
            delete=False,
        )
        json.dump(mcp_config, mcp_config_file)
        mcp_config_file.close()

        # Read owner DM channel from Slack bot config
        dm_channel = _get_slack_dm_channel()
        if not dm_channel:
            logger.warning("Slack DM: no owner_dm_channel in config")
            return False
        prompt = (
            f'Send this message as a Slack DM to channel {dm_channel}: '
            f'"{message}"'
        )
        cmd = [
            claude_path, "--print",
            "--output-format", "text",
            "--no-session-persistence",
            "--model", "sonnet",
            "--max-budget-usd", "1.00",
            "--mcp-config", mcp_config_file.name,
            "--strict-mcp-config",
            "--allowedTools", "mcp__slack-mcp__post_message,mcp__slack-mcp__open_dm_channel",
            "-p", prompt,
        ]

        env = _build_cli_env()
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30,
            env=env, cwd=str(SWARMWS),
        )

        if proc.returncode == 0:
            logger.info("Slack DM sent successfully")
            return True
        else:
            logger.warning(f"Slack DM failed: exit {proc.returncode}: {proc.stderr[:200]}")
            return False

    except subprocess.TimeoutExpired:
        logger.warning("Slack DM timed out after 30s")
        return False
    except Exception as e:
        logger.warning(f"Slack DM error: {e}")
        return False
    finally:
        if mcp_config_file:
            try:
                os.unlink(mcp_config_file.name)
            except OSError:
                pass


def send_post_job_notification(job: Job, result: JobResult) -> None:
    """Send a notification after job completion if configured.

    Jobs can opt into post-completion notifications:
      config:
        notify: slack     # Send result summary as Slack DM
    """
    notify_channel = job.config.get("notify")
    if not notify_channel:
        return

    status_icon = "✅" if result.status == "success" else "❌"
    message = (
        f"{status_icon} Job '{job.name}' completed: {result.status}\n"
        f"{result.summary[:200]}"
    )

    if notify_channel == "slack":
        _send_slack_dm(message)
    else:
        logger.warning(f"Unknown notify channel: {notify_channel}")


# ── Helpers ──────────────────────────────────────────────────────────


def _check_monthly_budget(state: SchedulerState, defaults: SchedulerDefaults) -> str | None:
    """Return error message if monthly budget exceeded, else None."""
    cap = defaults.max_monthly_spend_usd
    if state.monthly_spend_usd >= cap:
        return (
            f"Monthly budget reached (${state.monthly_spend_usd:.2f}/${cap:.2f}). "
            f"Reset on 1st of next month."
        )
    return None


def _check_daily_agent_limit(state: SchedulerState, defaults: SchedulerDefaults) -> str | None:
    """Return error message if daily agent_task run limit exceeded, else None."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    daily_count = sum(
        1 for js in state.jobs.values()
        if js.last_run and js.last_run.strftime("%Y-%m-%d") == today
        and js.last_status in ("success", "failed")
    )
    # This is a rough count — includes all job types. Acceptable since
    # agent_task jobs are the expensive ones and the cap is generous (20).
    if daily_count >= defaults.max_daily_agent_tasks:
        return f"Daily agent task limit reached ({daily_count}/{defaults.max_daily_agent_tasks})."
    return None


def _build_cli_env(aws_creds: dict[str, str] | None = None) -> dict:
    """Build clean environment for Claude CLI subprocess.

    PATH is already fixed at module level (_fix_path_from_login_shell).
    This adds Bedrock config, injects pre-resolved AWS credentials, and
    strips problematic vars.

    Credential strategy: if boto3 resolved credentials (ada → Isengard),
    inject them so CLI uses the same chain as signal_digest. If boto3
    failed (VPN off), aws_creds is empty — CLI falls through to its own
    SSO IdC tokens. Either way, at least one auth path works.
    """
    env = os.environ.copy()

    # Bedrock config
    env["CLAUDE_CODE_USE_BEDROCK"] = "true"
    env.setdefault("AWS_REGION", "us-west-2")
    env.setdefault("AWS_DEFAULT_REGION", "us-west-2")

    # Inject pre-resolved AWS credentials from boto3 (ada → Isengard).
    # This overrides any SSO IdC or credential_process the CLI would
    # otherwise try to resolve on its own. Single credential chain.
    if aws_creds:
        env.update(aws_creds)
        # Remove credential_process config — we already resolved credentials.
        # Prevents the CLI from trying its own (potentially different) resolution.
        env.pop("AWS_PROFILE", None)

    # MCP servers (aws-outlook-mcp, etc.) require Node >= 22 but mise
    # defaults to Node 20. Find the newest Node >= 22 and prepend to PATH.
    mise_node_dir = Path.home() / ".local/share/mise/installs/node"
    if mise_node_dir.is_dir():
        node22_dirs = sorted(
            [d for d in mise_node_dir.iterdir()
             if d.is_dir() and d.name.split(".")[0].isdigit()
             and int(d.name.split(".")[0]) >= 22],
            key=lambda d: d.name,
            reverse=True,
        )
        if node22_dirs:
            node_bin = node22_dirs[0] / "bin"
            if node_bin.is_dir():
                env["PATH"] = f"{node_bin}:{env.get('PATH', '')}"

    # Strip ALL proxy vars — Claude CLI manages its own proxy internally.
    # External proxy vars (especially Claude's own localhost proxy re-inherited
    # from a parent Claude process) cause MCP tools to route through the wrong
    # proxy and get blocked.
    for key in list(env.keys()):
        if "proxy" in key.lower():
            del env[key]

    # Disable CLI auto-memory (SwarmAI owns the memory pipeline)
    env["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] = "1"
    return env


def _build_script_env() -> dict:
    """Minimal env for script jobs. Inherits PATH, strips sensitive vars."""
    env = os.environ.copy()
    # Strip AWS creds — scripts that need them should use credential_process
    for key in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"):
        env.pop(key, None)
    return env


def _parse_cli_output(stdout: str) -> dict:
    """Parse Claude CLI --output-format json response."""
    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        return {"result": stdout}


# ── Todo Creation from Agent Task Results ────────────────────────────

# DB_PATH imported from .paths


def _create_todos_from_result(job: Job, result_text: str) -> None:
    """Parse agent_task output for actionable items and create Radar todos.

    Looks for lines matching patterns like:
      - "urgent: ..." or "URGENT: ..."
      - "action needed: ..."
      - "reply needed: ..."
      - "- [urgent] Subject — Sender"

    Deduplicates by title — won't create a pending todo with a title
    that already exists in pending state.

    Config keys (in job.config):
      create_todos: true          — enables this feature
      todo_source_type: "email"   — source_type for created todos (default: job type)
      todo_priority: "high"       — default priority (default: "medium")
      todo_max: 5                 — max todos per run (default: 5)
    """
    import sqlite3
    import uuid

    if not DB_PATH.exists():
        return

    source_type = job.config.get("todo_source_type", "email")
    default_priority = job.config.get("todo_priority", "medium")
    max_todos = job.config.get("todo_max", 5)

    # Extract actionable items from result text
    items: list[dict] = []
    for line in result_text.splitlines():
        stripped = line.strip().lstrip("- ").lstrip("* ")
        lower = stripped.lower()

        priority = default_priority
        title = None

        if lower.startswith("urgent:") or lower.startswith("[urgent]"):
            priority = "high"
            title = stripped.split(":", 1)[-1].strip() if ":" in stripped else stripped[8:].strip()
        elif lower.startswith("action needed:") or lower.startswith("[action needed]"):
            priority = "high"
            title = stripped.split(":", 1)[-1].strip()
        elif lower.startswith("reply needed:") or lower.startswith("[reply needed]"):
            priority = "high"
            title = stripped.split(":", 1)[-1].strip()
        elif lower.startswith("follow up:") or lower.startswith("[follow up]"):
            priority = "medium"
            title = stripped.split(":", 1)[-1].strip()
        elif lower.startswith("informational:") or lower.startswith("[informational]"):
            continue  # skip informational items
        elif lower.startswith("[skip]") or lower.startswith("skip:"):
            continue

        if title and len(title) > 5:
            items.append({"title": title[:120], "priority": priority})

        if len(items) >= max_todos:
            break

    if not items:
        return

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    try:
        with sqlite3.connect(str(DB_PATH), timeout=5.0) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            for item in items:
                # Dedup: skip if pending todo with same title exists
                existing = conn.execute(
                    "SELECT id FROM todos WHERE title = ? AND status = 'pending' LIMIT 1",
                    (item["title"],),
                ).fetchone()
                if existing:
                    continue

                todo_id = str(uuid.uuid4())
                conn.execute(
                    """INSERT INTO todos (id, workspace_id, title, description, source,
                       source_type, status, priority, due_date, linked_context, task_id,
                       created_at, updated_at)
                       VALUES (?, 'swarmws', ?, ?, ?, ?, 'pending', ?, NULL, ?, NULL, ?, ?)""",
                    (
                        todo_id, item["title"],
                        f"From scheduled job: {job.name}",
                        f"job:{job.id}",
                        source_type,
                        item["priority"],
                        json.dumps({"job_id": job.id, "job_name": job.name}),
                        now, now,
                    ),
                )
                logger.info("Created %s todo: %s [%s]", source_type, item["title"], item["priority"])
    except Exception as exc:
        logger.warning("Failed to create todos from job result: %s", exc)


# ── Todo Expiration (called from maintenance) ────────────────────────


def _expire_stale_todos(max_age_days: int = 30) -> str:
    """Cancel pending todos older than max_age_days.

    Returns a summary string for maintenance logging.
    Handled/cancelled/deleted todos are untouched.
    """
    import sqlite3

    if not DB_PATH.exists():
        return ""

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    from datetime import timedelta
    cutoff = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).strftime(
        "%Y-%m-%dT%H:%M:%S+00:00"
    )

    try:
        with sqlite3.connect(str(DB_PATH), timeout=5.0) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            cursor = conn.execute(
                """UPDATE todos SET status = 'cancelled', updated_at = ?
                   WHERE status = 'pending' AND created_at < ?""",
                (now, cutoff),
            )
            count = cursor.rowcount
            if count > 0:
                return f"Expired {count} stale todos (>{max_age_days} days)"
    except Exception as exc:
        logger.warning("Todo expiration failed: %s", exc)
    return ""


def _estimate_cost(input_tokens: int, output_tokens: int, model: str = "sonnet") -> float:
    """Estimate API cost in USD from token counts."""
    if model == "haiku":
        return input_tokens * _HAIKU_INPUT_PRICE + output_tokens * _HAIKU_OUTPUT_PRICE
    # Default: sonnet
    return input_tokens * _SONNET_INPUT_PRICE + output_tokens * _SONNET_OUTPUT_PRICE


def _write_job_result(
    job: Job, result_text: str, run_at: datetime,
    tokens: int, duration: float,
) -> Path:
    """Write job result as markdown + append to JSONL."""
    JOB_RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    # Markdown report
    date_str = run_at.strftime("%Y-%m-%d")
    slug = job.id.replace(" ", "-").lower()
    md_path = JOB_RESULTS_DIR / f"{date_str}-{slug}.md"

    md_content = f"""---
job_id: {job.id}
job_name: {job.name}
run_at: {run_at.isoformat()}
status: success
tokens_used: {tokens}
duration: {duration:.1f}s
---

## {job.name}

{result_text}
"""
    md_path.write_text(md_content)

    # JSONL for L4 machine reading
    jsonl_entry = {
        "job_id": job.id,
        "job_name": job.name,
        "run_at": run_at.isoformat(),
        "status": "success",
        "summary": result_text[:300],
        "tokens_used": tokens,
        "duration_seconds": duration,
    }
    with open(JOB_RESULTS_JSONL, "a") as f:
        f.write(json.dumps(jsonl_entry) + "\n")

    return md_path


def _extract_signals_from_output(job: Job, result_text: str) -> list[RawSignal]:
    """Extract RawSignal items from CLI output for signal pipeline.

    Tries structured extraction first (JSON array with title/url/summary),
    falls back to one mega-signal per job result.
    """
    # Try structured extraction (CLI prompt should include JSON array instructions)
    try:
        json_match = re.search(r'\[[\s\S]*\]', result_text)
        if json_match:
            items = json.loads(json_match.group())
            if isinstance(items, list) and len(items) > 0 and "title" in items[0]:
                return [
                    RawSignal(
                        feed_id=f"cli:{job.id}",
                        title=item.get("title", f"{job.name} item"),
                        url=item.get("url", ""),
                        summary=item.get("summary", "")[:500],
                        published=datetime.now(timezone.utc),
                        source=f"swarm-job:{job.id}",
                        tags=["cli-job"],
                    )
                    for item in items[:50]  # Cap at 50 signals per job
                ]
    except (json.JSONDecodeError, TypeError):
        pass

    # Fallback: one signal per job result
    return [RawSignal(
        feed_id=f"cli:{job.id}",
        title=f"{job.name} result",
        url="",
        summary=result_text[:500],
        published=datetime.now(timezone.utc),
        source=f"swarm-job:{job.id}",
        tags=["cli-job"],
    )]


def _update_job_state(state: SchedulerState, job_id: str, result: JobResult) -> None:
    """Update persistent job state after execution."""
    if job_id not in state.jobs:
        state.jobs[job_id] = JobState()

    js = state.jobs[job_id]
    js.last_run = result.timestamp
    js.last_status = result.status
    js.total_runs += 1
    js.total_tokens += result.tokens_used or 0

    if result.status == "failed":
        js.consecutive_failures += 1
    else:
        js.consecutive_failures = 0
