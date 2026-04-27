"""
Job Executor

Routes jobs to their handlers with error isolation.
Each job runs in a try/except — one failure never affects another.

Handlers:
  signal_fetch    — httpx adapters (RSS, GitHub, HN)
  signal_digest   — Bedrock Haiku relevance scoring
  agent_task      — Headless Claude CLI subprocess with MCP tools
  script          — Deterministic subprocess (no AI, no tokens)
  maintenance     — Prune caches, reset counters (lightweight, no LLM)
  ddd_refresh     — Autonomous DDD doc staleness detection + proposals
  memory_health   — LLM-powered memory analysis + stale entry pruning
  skill_proposer  — Autonomous skill proposals for recurring capability gaps
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

from .paths import SWARMWS, JOB_RESULTS_DIR, JOB_RESULTS_JSONL, MCPS_DIR, DB_PATH, ESTIMATION_LEARNER_FILE


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

# Sonnet 4.6 pricing (us.anthropic.claude-sonnet-4-6)
_SONNET_INPUT_PRICE = 3.0 / 1_000_000   # $3 per 1M input tokens
_SONNET_OUTPUT_PRICE = 15.0 / 1_000_000  # $15 per 1M output tokens

# ── MCP Auth Failure Detection ──────────────────────────────────────
# Generic patterns — catches any MCP auth failure (SSO expiry, token
# revocation, OAuth redirect, etc.) without being provider-specific.
_AUTH_FAIL_PATTERNS = [
    re.compile(r"(?:authentication|auth)\s+(?:error|failed|required)", re.I),
    re.compile(r"(?:token|oauth)\s+(?:expired|revoked|invalid)", re.I),
    re.compile(r"(?:failed to refresh|refresh\s+failed)", re.I),
    re.compile(r"re-?authenticate", re.I),
    re.compile(r"\b(?:401|403)\b.*(?:error|unauth)", re.I),
    re.compile(r"\b302\b.*(?:redirect|token|auth)", re.I),
]


def _detect_auth_failure(result_text: str) -> bool:
    """Return True if agent output indicates MCP auth failure.

    Requires 2+ distinct pattern hits to avoid false positives (e.g. an
    agent discussing auth topics without actually failing).
    """
    if not result_text:
        return False
    hits = sum(1 for p in _AUTH_FAIL_PATTERNS if p.search(result_text))
    return hits >= 2


# ── Estimation Learner (lazy singleton) ──────────────────────────────
_estimation_learner = None


def _get_learner():
    """Return the module-level EstimationLearner, lazily initialized."""
    global _estimation_learner
    if _estimation_learner is None:
        import atexit
        from .estimation_learner import EstimationLearner
        _estimation_learner = EstimationLearner(ESTIMATION_LEARNER_FILE)
        atexit.register(lambda: _estimation_learner.flush() if _estimation_learner else None)
    return _estimation_learner


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

        elif job.type == "ddd_refresh":
            from .handlers.ddd_refresh import run_ddd_refresh
            ddd_result = run_ddd_refresh()
            duration = (datetime.now(timezone.utc) - start).total_seconds()
            result = JobResult(
                job_id=job.id, timestamp=datetime.now(timezone.utc),
                status="success" if ddd_result.get("status") == "success" else "failed",
                summary=ddd_result.get("summary", "DDD refresh completed"),
                duration_seconds=duration,
            )

        elif job.type == "memory_health":
            from .handlers.memory_health import run_memory_health
            health_result = run_memory_health()
            duration = (datetime.now(timezone.utc) - start).total_seconds()
            h_status = health_result.get("status", "unknown")
            h_actions = health_result.get("actions", [])
            summary = "; ".join(h_actions) if h_actions else health_result.get("error", "Memory health: all clear")
            result = JobResult(
                job_id=job.id, timestamp=datetime.now(timezone.utc),
                status="success" if h_status == "success" else "failed",
                summary=summary,
                duration_seconds=duration,
            )

        elif job.type == "skill_proposer":
            from .handlers.skill_proposer import run_skill_proposer
            skill_result = run_skill_proposer()
            duration = (datetime.now(timezone.utc) - start).total_seconds()
            s_status = skill_result.get("status", "unknown")
            if s_status == "success":
                summary = (
                    f"Proposed skill '{skill_result.get('skill_name', '?')}' "
                    f"(confidence={skill_result.get('confidence', '?')})"
                )
            elif s_status == "low_confidence":
                summary = (
                    f"Discarded: confidence {skill_result.get('confidence', 0)} "
                    f"< 6 for '{skill_result.get('target_gap', '')[:50]}'"
                )
            else:
                summary = skill_result.get("reason", s_status)
            result = JobResult(
                job_id=job.id, timestamp=datetime.now(timezone.utc),
                status="success" if s_status in ("success", "skipped", "low_confidence") else "failed",
                summary=summary,
                duration_seconds=duration,
            )

        elif job.type == "todo_resolution":
            from .todo_resolution import run_todo_resolution
            todo_res = run_todo_resolution(
                stale_days=job.config.get("stale_days", 21),
                git_days=job.config.get("git_days", 7),
            )
            duration = (datetime.now(timezone.utc) - start).total_seconds()
            total = todo_res["pipeline_resolved"] + todo_res["git_resolved"] + todo_res["stale_cancelled"]
            parts = []
            if todo_res["pipeline_resolved"]:
                parts.append(f"{todo_res['pipeline_resolved']} pipeline")
            if todo_res["git_resolved"]:
                parts.append(f"{todo_res['git_resolved']} git-match")
            if todo_res["stale_cancelled"]:
                parts.append(f"{todo_res['stale_cancelled']} stale")
            summary = f"Resolved {total} todos ({', '.join(parts)})" if total else "No todos resolved"
            if todo_res["errors"]:
                summary += f" [{len(todo_res['errors'])} errors]"
            result = JobResult(
                job_id=job.id, timestamp=datetime.now(timezone.utc),
                status="success" if not todo_res["errors"] else "partial",
                summary=summary,
                duration_seconds=duration,
            )

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

        # Persist failure to JSONL + markdown so it appears in briefing
        # and job history — not just state.json.
        try:
            _write_job_result(
                job, error_msg, start, tokens=0, duration=duration,
                status="failed",
            )
        except Exception:
            logger.warning("Failed to persist crash result to JSONL (non-blocking)")

        return result


# ── Agent Task Handler ───────────────────────────────────────────────


def _check_claude_auth(claude_path: str) -> str | None:
    """Verify Claude CLI is authenticated before running an agent task.

    Returns None if auth is good, error string if auth is broken.
    Fast check (~2s) that prevents wasting a 300s timeout on dead credentials.
    """
    try:
        env = os.environ.copy()
        # Strip proxy vars — same as _build_cli_env (Claude sandbox proxy poisons child processes)
        for key in list(env.keys()):
            if "proxy" in key.lower():
                del env[key]
        env["CLAUDE_CODE_USE_BEDROCK"] = "true"
        env.setdefault("AWS_REGION", "us-west-2")

        result = subprocess.run(
            [claude_path, "auth", "status"],
            capture_output=True, text=True, timeout=15, env=env,
        )
        if result.returncode != 0:
            return f"claude auth status exit {result.returncode}: {result.stderr[:200]}"

        auth_data = json.loads(result.stdout)
        if not auth_data.get("loggedIn"):
            return "CLI not logged in — run 'claude auth login' or refresh SSO IdC tokens"

        logger.info("Auth pre-check passed: %s via %s",
                     auth_data.get("apiProvider", "?"),
                     auth_data.get("authMethod", "?"))
        return None

    except subprocess.TimeoutExpired:
        return "claude auth status timed out (15s) — CLI may be broken"
    except json.JSONDecodeError:
        # Non-JSON output but exit 0 — probably fine
        logger.debug("Auth check returned non-JSON, assuming OK")
        return None
    except Exception as e:
        return f"Auth pre-check error: {e}"


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


def _cli_supports_bare(claude_path: str) -> bool:
    """Check if the resolved CLI supports --bare (>= 2.1.81).

    Parses ``claude --version`` output like "2.1.85 (Claude Code)".
    Returns False on any parse failure (safe default).
    """
    try:
        result = subprocess.run(
            [claude_path, "--version"],
            capture_output=True, text=True, timeout=5,
        )
        # Output format: "2.1.85 (Claude Code)"
        version_str = result.stdout.strip().split()[0] if result.stdout.strip() else ""
        parts = version_str.split(".")
        if len(parts) >= 3:
            major, minor, patch = int(parts[0]), int(parts[1]), int(parts[2])
            return (major, minor, patch) >= (2, 1, 81)
    except Exception:
        pass
    return False


def _load_mcp_config() -> dict:
    """Load MCP config in Claude CLI ``--mcp-config`` format.

    Delegates to the shared ``mcp_config_loader.load_mcp_config_for_cli()``
    which performs the canonical two-layer merge (catalog + dev overlay).

    Returns empty dict if no MCP files exist (graceful no-op).
    """
    from core.mcp_config_loader import load_mcp_config_for_cli

    return load_mcp_config_for_cli(SWARMWS)


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

    # Pre-flight: verify CLI auth before committing to a long timeout.
    # `claude auth status` returns JSON with loggedIn: true/false.
    # If auth is dead, fail fast instead of wasting 300s on a doomed timeout.
    auth_err = _check_claude_auth(claude_path)
    if auth_err:
        return JobResult(
            job_id=job.id, timestamp=start,
            status="failed",
            summary=f"Auth pre-check failed: {auth_err}",
            duration_seconds=(datetime.now(timezone.utc) - start).total_seconds(),
            error="auth_preflight_failed",
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
    use_bare = _cli_supports_bare(claude_path)
    cmd = [
        claude_path,
        "--print",
        *(["--bare"] if use_bare else []),  # skip hooks/LSP (>= 2.1.81)
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
        elif _detect_auth_failure(result_text):
            status = "auth_failed"
            summary = (
                f"MCP auth failure — agent ran but tools couldn't authenticate. "
                f"Will retry on next scheduler tick after auth is restored."
            )
        else:
            status = "success"
            summary = result_text[:200] if result_text else f"Completed in {duration:.0f}s"

        # Write results (even partial ones are useful)
        output_path = None
        if result_text:
            output_path = _write_job_result(
                job, result_text, start, tokens_in + tokens_out, duration,
                status=status,
            )

        # If signal mode, inject into raw_signals buffer
        if job.config.get("output_as_signals", False) and result_text:
            signals = _extract_signals_from_output(job, result_text)
            state.raw_signals.extend(signals)

        # Auto-create Radar todos from agent_task results (e.g. inbox → email todos)
        if job.config.get("create_todos", False) and result_text and status == "success":
            _create_todos_from_result(job, result_text)

        # Record actual duration for EMA learner (improves future predictions)
        try:
            _get_learner().record(
                job.name, predicted=float(safety.timeout_seconds), actual=duration,
            )
        except Exception:
            pass  # Learner is best-effort, never blocks job execution

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

        # Record timeout as actual = timeout_seconds (learner learns timeouts too)
        try:
            _get_learner().record(
                job.name, predicted=float(safety.timeout_seconds), actual=duration,
            )
        except Exception:
            pass

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

    # Auto-cancel overdue todos stuck for >14 days
    from schemas.todo import TODO_LIFECYCLE
    overdue_result = _escalate_overdue_todos(
        cancel_days=TODO_LIFECYCLE["overdue_cancel_days"],
    )
    if overdue_result["cancelled_count"] > 0:
        actions.append(f"Auto-cancelled {overdue_result['cancelled_count']} stale overdue todos")

    # Purge terminal todos (handled/cancelled/deleted) older than 14 days
    purge_result = _purge_terminal_todos(
        retention_days=TODO_LIFECYCLE["purge_retention_days"],
        archive_before_purge=TODO_LIFECYCLE["archive_before_purge"],
    )
    if purge_result["purged_count"] > 0:
        actions.append(f"Purged {purge_result['purged_count']} old terminal todos")

    # Trim JSONL file (cap at 500 entries)
    if JOB_RESULTS_JSONL.exists():
        lines = JOB_RESULTS_JSONL.read_text().strip().split("\n")
        if len(lines) > 500:
            JOB_RESULTS_JSONL.write_text("\n".join(lines[-500:]) + "\n")
            actions.append(f"Trimmed job results JSONL: {len(lines)} → 500")

    # NOTE: memory_health, ddd_refresh, and skill_proposer are now
    # standalone system jobs with their own schedules. They were extracted
    # from maintenance to allow independent monitoring, scheduling, and
    # failure isolation. See system_jobs.py for their definitions.

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
      message: text to send (static message)
      job_ref: optional job_id whose last result to include
      source: "signal_digest" to auto-format from signal_digest.json
      max_items: max items from signal_digest (default 10)
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

    # Pre-flight: verify notify config exists and Slack is enabled.
    # Prevents circuit-breaker death from config-not-found errors.
    try:
        from skills.s_notify.notify import load_notify_config
        config = load_notify_config()
        slack_cfg = config.get("channels", {}).get("slack", {})
        if not slack_cfg.get("enabled", False):
            return JobResult(
                job_id=job.id, timestamp=start, status="skipped",
                summary="Slack not enabled in notify-channels.yaml",
                duration_seconds=0,
            )
    except Exception:
        pass  # Non-blocking — CLI fallback doesn't need config

    # Source: auto-format from data
    source = job.config.get("source", "")
    if source == "briefing" and not message:
        message = _format_briefing_slack_message()
    elif source == "signal_digest" and not message:
        message = _format_signal_digest_message(
            max_items=job.config.get("max_items", 10),
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


def _format_signal_digest_message(max_items: int = 10) -> str:
    """Format signal_digest.json into a readable Slack message.

    Reads the L4 digest JSON, groups by urgency, formats as compact markdown.
    Returns empty string if no digest or no items.
    """
    digest_path = SWARMWS / "Services" / "signals" / "signal_digest.json"
    if not digest_path.exists():
        return ""

    try:
        data = json.loads(digest_path.read_text())
    except Exception:
        return ""

    items = data.get("items", [])
    if not items:
        return ""

    # Take top N by relevance score
    items = sorted(items, key=lambda x: x.get("relevance_score", 0), reverse=True)
    items = items[:max_items]

    # Group by urgency
    urgency_emoji = {"high": "🔴", "medium": "🟡", "low": "🟢"}
    lines = [f"📡 *Signal Digest* — {data.get('signals_count', len(items))} signals\n"]

    for item in items:
        emoji = urgency_emoji.get(item.get("urgency", "low"), "🔵")
        title = item.get("title", "Untitled")
        source = item.get("source", "")
        summary = item.get("summary", "")[:80]
        url = item.get("url", "")

        source_tag = f" ({source})" if source else ""
        link = f"<{url}|→>" if url else ""
        lines.append(f"{emoji} *{title}*{source_tag} {link}")
        if summary:
            lines.append(f"   {summary}")

    return "\n".join(lines)


def _escape_slack_mrkdwn(text: str) -> str:
    """Escape Slack mrkdwn special characters in user-generated text.

    Prevents format injection from external content (RSS titles, HN posts).
    Slack mrkdwn specials: * _ ~ ` < > that could break formatting or
    inject links when embedded in bold/italic patterns.
    """
    # < and > are the only ones that create link injection; escape them first
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return text


def _format_briefing_slack_message() -> str:
    """Format the full Briefing Hub data as a grouped Slack DM message.

    Uses build_session_briefing_data() to get the same data the Welcome
    Screen shows, then renders it using the area-grouped Slack template
    from the Briefing Hub v2 design (D9: keep source language).

    Returns empty string if no meaningful content to send.
    """
    try:
        from core.proactive_intelligence import build_session_briefing_data
        data = build_session_briefing_data(str(SWARMWS))
    except Exception:
        return ""

    esc = _escape_slack_mrkdwn  # alias for readability

    sections: list[str] = []

    # ── Working ──────────────────────────────────────────────────
    working = data.get("working", [])
    if working:
        priority_emoji = {"high": "🔴", "medium": "🟡", "low": "🟢"}
        lines = [f"📋 *Working* ({len(working)})"]
        for item in working[:5]:
            emoji = priority_emoji.get(item.get("priority", "low"), "🟢")
            title = esc(item.get("title", ""))
            source = item.get("source", "")
            detail = item.get("sourceDetail", item.get("source_detail", ""))
            suffix = f" — _{source}"
            if detail:
                suffix += f", {esc(detail)}"
            suffix += "_"
            lines.append(f"{emoji} {title}{suffix}")
        sections.append("\n".join(lines))

    # ── Signals ──────────────────────────────────────────────────
    urgency_emoji = {"high": "🔴", "medium": "🟡", "low": "🟢"}
    signals = data.get("signals", [])
    if signals:
        lines = [f"📡 *Signals* ({len(signals)})"]
        for sig in signals[:5]:
            emoji = urgency_emoji.get(sig.get("urgency", "medium"), "🟡")
            title = esc(sig.get("title", ""))
            url = sig.get("sourceUrl", sig.get("url", ""))
            link = f" <{url}|→>" if url else ""
            lines.append(f"{emoji} *{title}*{link}")
        sections.append("\n".join(lines))

    # ── Hot News ─────────────────────────────────────────────────
    hot_news = data.get("hotNews", [])
    if hot_news:
        cn_items = [h for h in hot_news if h.get("region") == "cn"]
        intl_items = [h for h in hot_news if h.get("region") != "cn"]
        lines = ["🔥 *Hot News*"]
        if cn_items:
            cn_parts = []
            for h in cn_items[:5]:
                title = esc(h.get("title", ""))
                platform = h.get("platform", "")
                cn_parts.append(f"{title} ({platform})" if platform else title)
            lines.append(f"🇨🇳 {' · '.join(cn_parts)}")
        if intl_items:
            for h in intl_items[:3]:
                title = esc(h.get("title", ""))
                platform = h.get("platform", "")
                url = h.get("url", "")
                link = f" <{url}|→>" if url else ""
                platform_tag = f" ({platform})" if platform else ""
                lines.append(f"🌐 {title}{platform_tag}{link}")
        sections.append("\n".join(lines))

    # ── Stocks ───────────────────────────────────────────────────
    stocks = data.get("stocks", [])
    if stocks:
        status_emoji = {"success": "✅", "partial": "⚠️", "failed": "❌"}
        ok_items = [s for s in stocks if s.get("status") == "success"]
        warn_items = [s for s in stocks if s.get("status") != "success"]
        lines = [f"📈 *Stocks* — {len(stocks)} reports"]
        if ok_items:
            names = " · ".join(f"{status_emoji.get(s['status'], '✅')} {esc(s.get('name', s.get('ticker', '')))}" for s in ok_items[:6])
            lines.append(names)
        for s in warn_items[:3]:
            emoji = status_emoji.get(s.get("status", "failed"), "⚠️")
            lines.append(f"{emoji} {esc(s.get('name', s.get('ticker', '')))} — data fetch {s.get('status', 'unknown')}")
        sections.append("\n".join(lines))

    # ── Swarm Output ─────────────────────────────────────────────
    output = data.get("output", {})
    builds = output.get("builds", [])
    content = output.get("content", [])
    if builds or content:
        lines = ["🐝 *Swarm Output*"]
        if builds:
            build_parts = []
            for b in builds[:3]:
                title = esc(b.get("title", "Build"))
                conf = b.get("confidence")
                conf_tag = f" ({conf}/10)" if conf is not None else ""
                build_parts.append(f"{title}{conf_tag}")
            lines.append(f"🔧 {' · '.join(build_parts)}")
        if content:
            content_parts = []
            type_emoji = {"video": "🎬", "poster": "🖼", "podcast": "🎙", "article": "📄"}
            for c in content[:3]:
                emoji = type_emoji.get(c.get("type", "article"), "📄")
                content_parts.append(f"{emoji} {esc(c.get('title', 'Untitled'))}")
            lines.append(" · ".join(content_parts))
        sections.append("\n".join(lines))

    if not sections:
        return ""

    return "\n\n".join(sections)


def _get_slack_dm_channel() -> str | None:
    """Read the owner's Slack DM channel from config.json in workspace.

    Previously read from Services/slack-bot/config.json (removed).
    Now reads from the general SwarmWS config.json, falling back to None.
    """
    config_path = SWARMWS / "config.json"
    try:
        if config_path.exists():
            data = json.loads(config_path.read_text(encoding="utf-8"))
            return data.get("owner_dm_channel") or None
    except (json.JSONDecodeError, OSError):
        pass
    return None


def _send_slack_dm_webhook(message: str) -> bool:
    """Send a Slack DM via incoming webhook — cheap, no subprocess.

    Reads the Slack webhook URL from notify-channels.yaml.
    Returns True on success, False if webhook not configured or fails.
    """
    try:
        from skills.s_notify.notify import load_notify_config

        config = load_notify_config()
        slack_cfg = config.get("channels", {}).get("slack", {})
        webhook_url = slack_cfg.get("webhook_url", "")

        if not webhook_url or not slack_cfg.get("enabled", False):
            return False

        import httpx
        payload = {
            "blocks": [
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": message.replace("**", "*")},
                },
            ],
        }
        with httpx.Client(timeout=10, trust_env=False) as client:
            resp = client.post(webhook_url, json=payload)
            if resp.status_code == 200:
                logger.info("Slack DM sent via webhook")
                return True
            logger.warning(f"Slack webhook failed: {resp.status_code}")
            return False
    except Exception as e:
        logger.debug(f"Slack webhook path unavailable: {e}")
        return False


def _send_slack_dm_bot_api(message: str) -> bool:
    """Send a Slack DM via Slack Web API using the channel adapter's bot token.

    Reads bot_token from the channels DB table.  Direct HTTP POST to
    chat.postMessage — ~200ms, $0, no subprocess, works regardless of
    Slack Desktop state.
    """
    try:
        import sqlite3
        import httpx

        dm_channel = _get_slack_dm_channel()
        if not dm_channel:
            return False

        # Read bot_token from channels table (same source as channel adapter)
        db_path = DB_PATH
        if not db_path.exists():
            return False
        conn = sqlite3.connect(str(db_path), timeout=5)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT config FROM channels WHERE channel_type = 'slack' LIMIT 1"
            ).fetchone()
        finally:
            conn.close()

        if not row:
            return False
        config = json.loads(row["config"] or "{}")
        bot_token = config.get("bot_token", "")
        if not bot_token or not bot_token.startswith("xoxb-"):
            return False

        # Convert markdown bold to Slack mrkdwn
        mrkdwn_msg = message.replace("**", "*")

        with httpx.Client(timeout=10, trust_env=False) as client:
            resp = client.post(
                "https://slack.com/api/chat.postMessage",
                headers={"Authorization": f"Bearer {bot_token}"},
                json={"channel": dm_channel, "text": mrkdwn_msg},
            )
            data = resp.json()
            if data.get("ok"):
                logger.info("Slack DM sent via bot API (~200ms, $0)")
                return True
            logger.warning(f"Slack bot API failed: {data.get('error', 'unknown')}")
            return False

    except Exception as e:
        logger.debug(f"Slack bot API unavailable: {e}")
        return False


def _send_slack_dm(message: str) -> bool:
    """Send a Slack DM to the user.

    Strategy (fastest to slowest):
      1. Webhook — HTTP POST (~50ms, $0). Needs webhook_url in config.
      2. MCP stdio — JSON-RPC to slack-mcp binary (~2s, $0). Needs Slack Desktop.
      3. Claude CLI — full agent subprocess (~10-30s, ~$0.01). Last resort.
    """
    # Tier 1: direct webhook (no subprocess, no model inference)
    if _send_slack_dm_webhook(message):
        return True

    # Tier 2: Slack Web API with bot token from channels DB (~200ms, $0)
    if _send_slack_dm_bot_api(message):
        return True

    # Tier 3: Claude CLI with slack-mcp (heavy, last resort)
    claude_path = _resolve_claude_cli()
    if not claude_path:
        logger.warning("Slack DM: all paths exhausted (no webhook, no MCP, no CLI)")
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
        use_bare = _cli_supports_bare(claude_path)
        cmd = [
            claude_path, "--print",
            *(["--bare"] if use_bare else []),
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
            logger.info("Slack DM sent via CLI")
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


def _parse_structured_todos(
    result_text: str,
    *,
    source_type: str = "email",
    job_id: str = "",
    job_name: str = "",
    max_todos: int = 5,
) -> list[dict]:
    """Parse structured ``<!-- RADAR_TODOS [...] -->`` JSON from agent output.

    Returns a list of dicts ready for DB insertion, each with:
      title, priority, linked_context (JSON string), description.

    If no structured block is found, returns empty list (caller falls back
    to legacy regex parser).
    """
    import re

    pattern = r"<!--\s*RADAR_TODOS\s*\n(.*?)\n\s*-->"
    match = re.search(pattern, result_text, re.DOTALL)
    if not match:
        return []

    try:
        todos_raw = json.loads(match.group(1))
    except (json.JSONDecodeError, TypeError):
        logger.warning("Failed to parse RADAR_TODOS JSON block")
        return []

    if not isinstance(todos_raw, list):
        return []

    from schemas.todo import validate_linked_context

    items: list[dict] = []
    for raw in todos_raw[:max_todos]:
        if not isinstance(raw, dict) or not raw.get("title"):
            continue

        # Build linked_context from the item's context + job metadata
        ctx = raw.get("context", {})
        if not isinstance(ctx, dict):
            ctx = {}
        ctx["job_id"] = job_id
        ctx["job_name"] = job_name
        ctx["created_by"] = f"job:{job_id}"

        # Validate context against source-specific requirements
        ctx = validate_linked_context(source_type, ctx)

        items.append({
            "title": str(raw["title"])[:500],
            "priority": raw.get("priority", "medium"),
            "linked_context": json.dumps(ctx),
            "description": raw.get("description", f"From scheduled job: {job_name}"),
        })

    return items


def _parse_legacy_todos(
    result_text: str,
    *,
    source_type: str = "email",
    job_id: str = "",
    job_name: str = "",
    default_priority: str = "medium",
    max_todos: int = 5,
) -> list[dict]:
    """Legacy regex parser for backward compatibility.

    Looks for lines matching: urgent:, action needed:, reply needed:, follow up:.
    Returns list of dicts with title, priority, linked_context.
    """
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
            continue
        elif lower.startswith("[skip]") or lower.startswith("skip:"):
            continue

        if title and len(title) > 5:
            ctx = {
                "job_id": job_id,
                "job_name": job_name,
                "created_by": f"job:{job_id}",
                "next_step": title,  # Best guess from legacy format
            }
            items.append({
                "title": title[:120],
                "priority": priority,
                "linked_context": json.dumps(ctx),
                "description": f"From scheduled job: {job_name}",
            })

        if len(items) >= max_todos:
            break

    return items


def _create_todos_from_result(job: Job, result_text: str) -> None:
    """Parse agent output for actionable items and create Radar todos.

    Tries structured ``<!-- RADAR_TODOS -->`` JSON first, falls back to
    legacy regex parsing for backward compatibility.

    Config keys (in job.config):
      create_todos: true          — enables this feature
      todo_source_type: "email"   — source_type for created todos
      todo_priority: "high"       — default priority (default: "medium")
      todo_max: 5                 — max todos per run (default: 5)
    """
    import sqlite3
    import uuid

    if not DB_PATH.exists():
        return

    _VALID_SOURCE_TYPES = frozenset({
        "manual", "email", "slack", "meeting", "integration", "chat", "ai_detected",
    })
    raw_source = job.config.get("todo_source_type", "email")
    source_type = raw_source if raw_source in _VALID_SOURCE_TYPES else "integration"
    default_priority = job.config.get("todo_priority", "medium")
    max_todos = job.config.get("todo_max", 5)

    # Try structured extraction first, fall back to regex
    items = _parse_structured_todos(
        result_text, source_type=source_type,
        job_id=job.id, job_name=job.name, max_todos=max_todos,
    )
    if not items:
        items = _parse_legacy_todos(
            result_text, source_type=source_type,
            job_id=job.id, job_name=job.name,
            default_priority=default_priority, max_todos=max_todos,
        )

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
                        item.get("description", f"From scheduled job: {job.name}"),
                        f"job:{job.id}",
                        source_type,
                        item["priority"],
                        item["linked_context"],
                        now, now,
                    ),
                )
                logger.info("Created %s todo: %s [%s]", source_type, item["title"], item["priority"])
    except Exception as exc:
        logger.warning("Failed to create todos from job result: %s", exc)


# ── Todo Lifecycle: Expiration, Escalation, Purge ────────────────────


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


def _escalate_overdue_todos(
    cancel_days: int = 14,
    db_path: Path | None = None,
) -> dict:
    """Auto-cancel overdue todos that have been overdue for too long.

    Overdue todos with updated_at older than cancel_days are cancelled.
    Active statuses (pending, in_discussion) are NEVER touched.

    Args:
        cancel_days: Days after which overdue todos auto-cancel.
        db_path: Override DB path for testing.

    Returns:
        Dict with cancelled_count.
    """
    import sqlite3
    from datetime import timedelta

    _db = db_path or DB_PATH
    if not _db.exists():
        return {"cancelled_count": 0}

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    cutoff = (datetime.now(timezone.utc) - timedelta(days=cancel_days)).strftime(
        "%Y-%m-%dT%H:%M:%S+00:00"
    )

    try:
        with sqlite3.connect(str(_db), timeout=5.0) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            cursor = conn.execute(
                """UPDATE todos SET status = 'cancelled', updated_at = ?
                   WHERE status = 'overdue' AND updated_at < ?""",
                (now, cutoff),
            )
            count = cursor.rowcount
            if count > 0:
                logger.info("Auto-cancelled %d overdue todos (>%dd)", count, cancel_days)
            return {"cancelled_count": count}
    except Exception as exc:
        logger.warning("Overdue escalation failed: %s", exc)
        return {"cancelled_count": 0}


def _purge_terminal_todos(
    retention_days: int = 14,
    archive_before_purge: bool = True,
    db_path: Path | None = None,
    archive_dir: Path | None = None,
) -> dict:
    """Hard-delete terminal-state todos older than retention_days.

    Terminal states: handled, cancelled, deleted.
    Active states (pending, overdue, in_discussion) are NEVER purged.

    Optionally archives purged todos to a JSONL file before deletion.

    Args:
        retention_days: Days to keep terminal todos before purging.
        archive_before_purge: If True, append to todo-archive.jsonl before delete.
        db_path: Override DB path for testing.
        archive_dir: Override archive directory for testing.

    Returns:
        Dict with purged_count and archive_path (if archived).
    """
    import sqlite3
    from datetime import timedelta

    _db = db_path or DB_PATH
    if not _db.exists():
        return {"purged_count": 0}

    cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).strftime(
        "%Y-%m-%dT%H:%M:%S+00:00"
    )

    terminal_states = ("handled", "cancelled", "deleted")
    placeholders = ",".join("?" for _ in terminal_states)

    try:
        with sqlite3.connect(str(_db), timeout=5.0) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.row_factory = sqlite3.Row

            # Fetch rows to archive before deleting
            rows = conn.execute(
                f"""SELECT * FROM todos
                    WHERE status IN ({placeholders}) AND updated_at < ?""",
                (*terminal_states, cutoff),
            ).fetchall()

            if not rows:
                return {"purged_count": 0}

            # Archive to JSONL if enabled
            archive_path = None
            if archive_before_purge:
                _archive_dir = archive_dir or (Path.home() / ".swarm-ai" / "SwarmWS" / "Knowledge" / "Archives")
                _archive_dir.mkdir(parents=True, exist_ok=True)
                archive_path = _archive_dir / "todo-archive.jsonl"
                with open(archive_path, "a", encoding="utf-8") as f:
                    for row in rows:
                        entry = dict(row)
                        entry["_purged_at"] = datetime.now(timezone.utc).isoformat()
                        f.write(json.dumps(entry) + "\n")

            # Hard delete
            row_ids = [dict(row)["id"] for row in rows]
            id_placeholders = ",".join("?" for _ in row_ids)
            conn.execute(
                f"DELETE FROM todos WHERE id IN ({id_placeholders})",
                row_ids,
            )

            count = len(rows)
            logger.info(
                "Purged %d terminal todos (>%dd)%s",
                count, retention_days,
                f", archived to {archive_path}" if archive_path else "",
            )
            return {"purged_count": count, "archive_path": str(archive_path) if archive_path else None}

    except Exception as exc:
        logger.warning("Todo purge failed: %s", exc)
        return {"purged_count": 0}


def _estimate_cost(input_tokens: int, output_tokens: int, model: str = "sonnet") -> float:
    """Estimate API cost in USD from token counts (Sonnet 4.6 pricing)."""
    return input_tokens * _SONNET_INPUT_PRICE + output_tokens * _SONNET_OUTPUT_PRICE


def _write_job_result(
    job: Job, result_text: str, run_at: datetime,
    tokens: int, duration: float, status: str = "success",
) -> Path:
    """Write job result as markdown + append to JSONL.

    Args:
        status: Actual job status — "success", "failed", "auth_failed",
                "partial", etc. Persisted to both markdown and JSONL.
    """
    JOB_RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    # Markdown report
    date_str = run_at.strftime("%Y-%m-%d")
    slug = job.id.replace(" ", "-").lower()
    md_path = JOB_RESULTS_DIR / f"{date_str}-{slug}.md"

    md_content = f"""---
job_id: {job.id}
job_name: {job.name}
run_at: {run_at.isoformat()}
status: {status}
tokens_used: {tokens}
duration: {duration:.1f}s
---

## {job.name}

{result_text}
"""
    md_path.write_text(md_content)

    # JSONL for L4 machine reading (briefing endpoint)
    jsonl_entry = {
        "job_id": job.id,
        "job_name": job.name,
        "run_at": run_at.isoformat(),
        "status": status,
        "summary": result_text[:300],
        "tokens_used": tokens,
        "duration_seconds": duration,
    }
    with open(JOB_RESULTS_JSONL, "a") as f:
        f.write(json.dumps(jsonl_entry) + "\n")

    # JSONL sidecar for distillation (agent-task jobs only — they produce real insights)
    # Script jobs (tokens_used=0) are just logs, not worth distilling.
    if tokens > 0:
        try:
            import fcntl
            sidecar_path = JOB_RESULTS_DIR / f"{date_str}-{slug}.jsonl"
            sidecar_record = {
                "job_id": job.id,
                "job_name": job.name,
                "run_at": run_at.isoformat(),
                "tokens_used": tokens,
                "duration_seconds": duration,
                "result_text": result_text[:5000],  # Cap for sanity
            }
            line = json.dumps(sidecar_record, ensure_ascii=False, separators=(",", ":")) + "\n"
            with open(sidecar_path, "a") as sf:
                fcntl.flock(sf, fcntl.LOCK_EX)
                try:
                    sf.write(line)
                    sf.flush()
                finally:
                    fcntl.flock(sf, fcntl.LOCK_UN)
        except Exception as exc:
            logger.warning("Job JSONL sidecar write failed (non-blocking): %s", exc)

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
    elif result.status != "auth_failed":
        # auth_failed is transient — don't reset streak (would hide real
        # failures) but don't increment either (not a job bug).
        js.consecutive_failures = 0
