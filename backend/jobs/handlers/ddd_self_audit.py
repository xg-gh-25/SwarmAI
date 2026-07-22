"""DDD Self-Audit — scheduled per-project semantic-drift REVIEW (run_835f82ff).

The real mechanism for SEMANTIC DDD drift (run_b2e85d61 concluded it is NOT a
mechanizable grep-detector: a present symbol does not prove a prose claim false —
it may be inert, a non-goal, or true-when-written). Prose-truth needs JUDGMENT, so
this is a periodic LLM REVIEW, generalizing the 4-subagent SwarmAI audit to ALL DDD
projects under SwarmWS/Projects/.

DESIGN INVARIANTS (Gate-0 run_835f82ff, all live-verified):
- DETECT-ONLY, structurally: the review agent gets ``Read,Grep`` ONLY (no Write/Edit,
  no Bash) — it CANNOT mutate a DDD doc. It emits findings as text; THIS Python handler
  writes the report. The fix stays human via s_persist (the run_b2e85d61 NO-GO invariant).
- PER-PROJECT focused review (not one diluted 8-in-1 prompt) — one bounded ``claude
  --print`` subprocess per project, looped in-handler (JobSafety.timeout does not bound
  inner spawns; scheduled as a trailing weekly slot per the eval-scheduled precedent).
- DOMAIN-AWARE prompt: a code-backed project (has code-intel.json / a source repo) is
  reviewed for prose-vs-CODE drift; a non-code project (business/product/research) for
  prose-vs-PRODUCT-reality drift (internal contradiction, superseded claims, dead refs).
  A uniform "check against live code" prompt on non-code projects = noise.
- SURFACE in-band: findings → a report (archive) AND a RADAR_TODO per project with
  findings (the forcing function — a report file alone rots, run_b2e85d61's lesson).

Reuses (does NOT reimplement): the executor CLI primitives + _discover_projects +
_write_job_result + _create_todos_from_result.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from core.project_registry import DDD_CANONICAL_DOCS
from core.ddd_paths import ddd_path  # six-section layout resolver (strangler-aware)
from ..paths import SWARMWS

logger = logging.getLogger("swarm.jobs.ddd_self_audit")

PROJECTS_DIR = SWARMWS / "Projects"

# Read-only toolset — the structural detect-only guarantee. NEVER add Write/Edit/Bash.
_AUDIT_TOOLS = ["Read", "Grep", "Glob"]

# Per-project HANG guard (seconds) — the ONLY per-call bound this handler imposes.
# Rationale (O030): a subprocess.run with no timeout can hang the ENTIRE weekly job
# forever on a network stall / CLI deadlock; this bounds a genuine hang, it does NOT
# cap slow-but-progressing work. Cost is governed centrally by the scheduler's global
# monthly budget (_check_monthly_budget), NOT by a per-call dollar cap in this handler.
_PER_PROJECT_TIMEOUT_S = 240


def _classify_review_result(returncode: int, output: dict, stderr: str) -> dict:
    """Classify one per-project review subprocess result — pure, no I/O.

    Two outcomes:
      - ``clean``  : returncode 0, use the result.
      - ``failed`` : any non-zero exit. Carries an ``error_detail`` (the CLI
        ``errors`` field, else a stderr tail) so the report shows the REAL cause
        instead of an opaque "exit N".

    We parse stdout regardless of returncode purely for OBSERVABILITY — to surface
    WHY a review failed. There is deliberately NO cost/budget branch here: cost is
    governed centrally (scheduler monthly budget), and a genuine hang is bounded by
    the subprocess timeout — neither belongs as per-call logic in this handler.
    """
    result_text = output.get("result", "") or ""
    errors = [e for e in (output.get("errors", []) or []) if isinstance(e, str)]

    if returncode != 0:
        stderr_tail = (stderr or "").strip()[-200:]
        error_detail = "; ".join(str(e) for e in errors) if errors else (stderr_tail or "no stderr")
        return {"status": "failed", "error_detail": error_detail}
    return {"status": "clean", "result_text": result_text}


_RADAR_TODOS_RE = re.compile(r"<!--\s*RADAR_TODOS\s*(\[.*?\])\s*-->", re.DOTALL)


def _count_parseable_findings(result_text: str) -> list:
    """Return the list of findings that will ACTUALLY become todos — i.e. a
    RADAR_TODOS block that parses as valid JSON. Output can be interrupted mid-JSON
    (e.g. a subprocess timeout / truncated stream); a malformed block yields 0
    (matching _parse_structured_todos, which returns [] on JSONDecodeError), so the
    reported count never over-states findings that won't materialize. Pure, no
    I/O — unit-testable."""
    if "RADAR_TODOS" not in result_text:
        return []
    m = _RADAR_TODOS_RE.search(result_text)
    if not m:
        return []
    try:
        parsed = json.loads(m.group(1))
    except (json.JSONDecodeError, TypeError):
        return []
    return parsed if isinstance(parsed, list) else []


def _discover_ddd_projects() -> list[tuple[str, Path]]:
    """All SwarmWS/Projects/*/ dirs carrying at least one canonical DDD doc."""
    projects: list[tuple[str, Path]] = []
    if not PROJECTS_DIR.is_dir():
        return projects
    for d in sorted(PROJECTS_DIR.iterdir()):
        if not d.is_dir() or d.name.startswith("."):
            continue
        # ddd_path (strangler-aware): a migrated DDD keeps canonical docs under
        # 2-understanding/, so a bare `d / doc` root-probe finds NOTHING and this
        # discovery returns [] → the whole self-audit no-ops ("No DDD projects
        # found"). Route through the resolver so migrated + un-migrated both resolve.
        if any(ddd_path(d, doc).exists() for doc in DDD_CANONICAL_DOCS):
            projects.append((d.name, d))
    return projects


def _is_code_backed(project_dir: Path) -> bool:
    """A project is code-backed if it carries a code-intel artifact (real source graph)."""
    return (project_dir / "code-intel.json").exists() or (project_dir / "code_intel.db").exists()


def _source_repo_for(project_name: str) -> Path | None:
    """The live SOURCE repo a code-backed project's DDD describes. Only SwarmAI today;
    returns None otherwise so we never hand the agent a --add-dir it can't use."""
    if project_name == "SwarmAI":
        try:
            from ..system_jobs import _get_swarmai_root
            root = Path(_get_swarmai_root())
            return root if root.is_dir() else None
        except Exception:
            return None
    return None


def _build_audit_prompt(project_name: str, project_dir: Path, code_backed: bool) -> str:
    """Domain-aware review prompt. Instructs the agent to emit a RADAR_TODOS block so
    findings become in-band todos; agent has Read/Grep ONLY, so it cannot fix — only report."""
    # ddd_path (strangler-aware): name the docs at their real location so the prompt
    # doesn't tell the agent to "Read the DDD docs" while listing none (migrated docs
    # live under 2-understanding/, not the project root).
    present_docs = [d for d in DDD_CANONICAL_DOCS if ddd_path(project_dir, d).exists()]
    docs = ", ".join(present_docs)
    rel = f"Projects/{project_name}"
    # Point the agent at the docs' ACTUAL parent dir (2-understanding/ for a migrated
    # DDD, project root for legacy) — deriving it from the resolver, not assuming root,
    # so the location hint stays correct across the strangler migration.
    if present_docs:
        docs_dir = ddd_path(project_dir, present_docs[0]).parent
        try:
            docs_rel = f"Projects/{project_name}/{docs_dir.relative_to(project_dir)}".rstrip("/.")
        except ValueError:
            docs_rel = rel
    else:
        docs_rel = rel

    if code_backed:
        drift_def = (
            "SEMANTIC DRIFT = a DDD prose claim that no longer matches the LIVE CODE. "
            "Read the DDD docs, then verify load-bearing claims against the actual source "
            "(prefer the project's code-intel.json for a fast symbol/route/module map; use "
            "Grep/Read on the source for specifics). Flag a claim ONLY when the code "
            "CONTRADICTS it — e.g. 'X not yet built' while X is implemented, a described "
            "mechanism that was removed, a wrong count/version/status."
        )
        caveats = (
            "DO NOT flag: (a) a deliberate NON-GOAL ('we chose NOT to build X', 'by-design', "
            "'rejected'); (b) an INERT-but-present symbol (code exists but doc says it's "
            "dead/dormant — that can be TRUE); (c) a dated claim that was true WHEN WRITTEN. "
            "When unsure whether prose is actually false, DO NOT flag — a false alarm on a "
            "correct statement erodes trust more than a missed one."
        )
    else:
        drift_def = (
            "SEMANTIC DRIFT = an internal INCONSISTENCY or self-contradiction in the DDD "
            "itself (this is a NON-CODE project — there is no source repo to check against). "
            "Flag: a claim in one doc contradicted by another; a superseded/obviously-stale "
            "status ('current focus' describing finished work); a dangling reference to "
            "something the docs elsewhere say was removed/renamed."
        )
        caveats = (
            "DO NOT flag: subjective wording, strategy/vision phrasing, or anything that is "
            "merely 'could be updated'. Only concrete internal contradictions or plainly "
            "superseded claims. When unsure, DO NOT flag."
        )

    return f"""Review the DDD documentation for project **{project_name}** ({rel}) for semantic drift.

{drift_def}

{caveats}

Docs present: {docs}. Read them under {docs_rel}/.

You have Read/Grep/Glob ONLY — you CANNOT and MUST NOT edit any file. Your job is to
REPORT drift, not fix it (the human fixes via s_persist).

Output format — end your response with EXACTLY this block (omit entirely if you find
NO real drift; do not invent findings to fill it). The `description` carries the
evidence (it becomes the todo body — the whole point); `context` makes it actionable:

<!-- RADAR_TODOS
[
  {{"title": "DDD drift [{project_name}/DOC.md]: <one-line what's stale>",
    "description": "<the exact stale claim, verbatim> — why it's false: <reason> — evidence: <code location file:line OR the contradicting doc line>. Fix via s_persist.",
    "priority": "medium",
    "context": {{"detection_reason": "DDD self-audit semantic-drift review", "next_step": "correct the claim in {project_name}/DOC.md via s_persist"}}}}
]
-->

At most 5 findings, highest-confidence first. If the DDD is accurate, say so in one line and emit no block."""


def run_ddd_self_audit(config: dict | None = None) -> dict:
    """Loop all DDD projects; per project, run a bounded read-only review subprocess;
    aggregate findings into a report + per-project Radar todos. Detect-only."""
    # Import executor primitives lazily (avoid import cycle: executor imports handlers).
    from ..executor import (
        _resolve_claude_cli, _check_claude_auth, _get_aws_credentials,
        _cli_supports_bare, _load_mcp_config, _build_cli_env, _parse_cli_output,
        _write_job_result, _create_todos_from_result,
    )
    from ..models import Job, JobSafety, JobType
    import subprocess, tempfile

    start = datetime.now(timezone.utc)
    config = config or {}

    projects = _discover_ddd_projects()
    if not projects:
        # FAIL-LOUD when the brain is blind (run_775f3969). "0 DDD projects" has two
        # very different meanings and they MUST NOT collapse to a silent 'skipped':
        #   • Projects/ genuinely empty (fresh install) → legit skip, nothing to audit.
        #   • Projects/ HAS project dirs but none resolved as a DDD → the discovery
        #     probe is BLIND (e.g. a layout migration moved canonical docs and the
        #     probe still reads the old path). This is exactly how the six-section
        #     migration silently disabled the whole semantic-drift immune system for
        #     weeks — caught only by manual dive-deep. A brain must announce when it
        #     can't see its own organs, so this path returns 'failed' → job-failure 🔔
        #     + surfaces in briefing/health, not a quiet skip.
        candidate_dirs = [
            d for d in (PROJECTS_DIR.iterdir() if PROJECTS_DIR.is_dir() else [])
            if d.is_dir() and not d.name.startswith(".")
        ]
        if candidate_dirs:
            names = ", ".join(sorted(d.name for d in candidate_dirs)[:10])
            return {
                "status": "failed",
                "summary": (
                    f"DISCOVERY BLIND: {len(candidate_dirs)} project dir(s) on disk "
                    f"({names}) but ZERO resolved as DDD — the self-audit would no-op. "
                    "Likely a layout change the discovery probe doesn't follow "
                    "(canonical-doc path resolution). The brain cannot audit itself."
                ),
                "output_path": None,
            }
        return {"status": "skipped", "summary": "No DDD projects found (Projects/ empty)", "output_path": None}

    claude_path = _resolve_claude_cli()
    if not claude_path:
        return {"status": "failed", "summary": "Claude CLI not found", "output_path": None}
    auth_err = _check_claude_auth(claude_path)
    if auth_err:
        return {"status": "failed", "summary": f"Auth pre-check failed: {auth_err}", "output_path": None}

    aws_creds = _get_aws_credentials()
    use_bare = _cli_supports_bare(claude_path)
    mcp_config = _load_mcp_config()
    env = _build_cli_env(aws_creds)

    report_lines = [f"# DDD Self-Audit — {start.strftime('%Y-%m-%d')}", ""]
    total_findings = 0
    reviewed = 0
    per_project_results: list[tuple[str, str]] = []  # (project, result_text) for todo creation

    for project_name, project_dir in projects:
        code_backed = _is_code_backed(project_dir)
        prompt = _build_audit_prompt(project_name, project_dir, code_backed)

        cmd = [
            claude_path, "--print",
            *(["--bare"] if use_bare else []),
            "--output-format", "json",
            "--no-session-persistence",
            "--model", "sonnet",
            "--permission-mode", "bypassPermissions",
        ]
        mcp_file = None
        if mcp_config:
            mcp_file = tempfile.NamedTemporaryFile(
                mode="w", suffix=".json", prefix="swarm-audit-mcp-", delete=False)
            json.dump(mcp_config, mcp_file); mcp_file.close()
            cmd += ["--mcp-config", mcp_file.name, "--strict-mcp-config"]
        else:
            cmd.append("--strict-mcp-config")
        # Read-only toolset = the structural detect-only guarantee.
        cmd += ["--allowedTools", ",".join(_AUDIT_TOOLS)]
        cmd += ["--add-dir", str(SWARMWS)]
        # Code-backed project: also grant read of the live SOURCE repo it describes.
        src = _source_repo_for(project_name) if code_backed else None
        if src:
            cmd += ["--add-dir", str(src)]
        cmd += ["--system-prompt",
                f"You are SwarmAI running the scheduled DDD self-audit for {project_name}. "
                f"Read-only review. Report drift; never edit."]
        cmd += ["-p", prompt]

        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True,
                timeout=_PER_PROJECT_TIMEOUT_S, env=env, cwd=str(SWARMWS),
            )
        except subprocess.TimeoutExpired:
            report_lines.append(f"## {project_name} — ⏱ timed out ({_PER_PROJECT_TIMEOUT_S}s), skipped")
            continue
        finally:
            if mcp_file:
                try:
                    Path(mcp_file.name).unlink(missing_ok=True)
                except OSError:
                    pass

        # Parse stdout regardless of exit code — purely for OBSERVABILITY, so a
        # genuine failure surfaces its REAL cause instead of an opaque "exit N".
        # Pure helper → unit-tested without subprocess mocking.
        verdict = _classify_review_result(
            proc.returncode, _parse_cli_output(proc.stdout), proc.stderr or ""
        )
        kind = "code-backed" if code_backed else "non-code"

        if verdict["status"] == "failed":
            # Genuine failure (MCP/auth/crash) — surface the REAL cause, not "exit N".
            report_lines.append(
                f"## {project_name} — ⚠️ review failed (exit {proc.returncode}): {verdict['error_detail']}"
            )
            report_lines.append("")
            continue

        result_text = verdict["result_text"]
        reviewed += 1
        # Count ACTUAL parseable findings, not raw '"title"' substrings — a truncated
        # RADAR_TODOS block yields 0 todos downstream (_parse_structured_todos returns
        # [] on JSONDecodeError); counting substrings would over-report.
        n_findings = len(_count_parseable_findings(result_text))
        total_findings += n_findings
        header = f"## {project_name} ({kind}) — {n_findings} finding(s)"
        report_lines.append(header)
        report_lines.append(result_text.strip() or "_(no output)_")
        report_lines.append("")
        if n_findings:  # only queue todo-creation when findings actually parse
            per_project_results.append((project_name, result_text))

    report_lines.insert(2, f"Reviewed {reviewed}/{len(projects)} projects · {total_findings} drift finding(s). "
                           f"Findings surface as Radar todos; fix via s_persist (detect-only, no auto-edit).")
    report_text = "\n".join(report_lines)
    duration = (datetime.now(timezone.utc) - start).total_seconds()

    # Persist the report (Python writes it — the agent never had Write).
    synthetic_job = Job(
        id="ddd-self-audit", name="DDD Self-Audit", type=JobType.DDD_SELF_AUDIT,
        schedule="", config={
            "create_todos": True, "todo_source_type": "ai_detected",
            "todo_priority": "medium", "todo_max": 40,
        },
        safety=JobSafety(),
    )
    output_path = _write_job_result(
        synthetic_job, report_text, start, tokens=0, duration=duration, status="success")

    # Surface findings as Radar todos (the in-band forcing function). Each project's
    # RADAR_TODOS block is parsed + created; report alone would rot.
    todos_created = 0
    for project_name, result_text in per_project_results:
        try:
            _create_todos_from_result(synthetic_job, result_text)
            todos_created += 1
        except Exception as e:
            logger.warning("ddd_self_audit: todo creation failed for %s: %s", project_name, e)

    summary = (f"Audited {reviewed}/{len(projects)} DDD projects · {total_findings} drift finding(s) "
               f"· {todos_created} project(s) → Radar todos")
    logger.info("ddd_self_audit complete: %s", summary)
    return {
        "status": "success",
        "projects_reviewed": reviewed,
        "findings": total_findings,
        "summary": summary,
        "output_path": str(output_path) if output_path else None,
    }
