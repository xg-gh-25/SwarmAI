"""DDD Self-Audit — scheduled per-project semantic-drift REVIEW (run_835f82ff).

The real mechanism for SEMANTIC DDD drift (run_b2e85d61 concluded it is NOT a
mechanizable grep-detector: a present symbol does not prove a prose claim false —
it may be inert, a non-goal, or true-when-written). Prose-truth needs JUDGMENT, so
this is a periodic LLM REVIEW, generalizing the 4-subagent SwarmAI audit to ALL DDD
projects under SwarmWS/Projects/.

DESIGN INVARIANTS (Gate-0 run_835f82ff, all live-verified):
- THE AGENT ONLY READS, structurally: ``--tools Read,Grep,Glob`` (``_build_review_cmd``)
  — it CANNOT mutate a DDD doc. It emits findings, each optionally with a ``fix``; THIS
  Python handler decides whether a fix is certain and writes it (core/ddd_drift_fix,
  design docs/design/2026-10-27-ddd-drift-auto-fix.md). Only three fixes exist, each a
  fixed grammar the OS checks and computes itself (drop a waiting condition a ticked line
  shows is done; swap a path for the one its superseded header names; swap a value for the
  one the code assigns); everything else is reported as "needs you".
- PER-PROJECT focused review (not one diluted 8-in-1 prompt) — one bounded ``claude
  --print`` subprocess per project, looped in-handler (JobSafety.timeout does not bound
  inner spawns; scheduled as a trailing weekly slot per the eval-scheduled precedent).
- DOMAIN-AWARE prompt: a code-backed project (has code-intel.json / a source repo) is
  reviewed for prose-vs-CODE drift; a non-code project (business/product/research) for
  prose-vs-PRODUCT-reality drift (internal contradiction, superseded claims, dead refs).
  A uniform "check against live code" prompt on non-code projects = noise.
- SURFACE: a report at Knowledge/JobResults/*-ddd-self-audit.md that opens with "found N ·
  fixed M · needs you K", lists what was fixed and what needs you, then each project's
  review with a DRIFT_OUTCOMES block. ddd_drift_signal counts only the open findings.
  ``config.dry_run`` checks everything, writes nothing, and names its report
  *-ddd-self-audit-dryrun.md so no reader takes it for the real one. No Radar todo is
  written (run_50db230a).

Reuses (does NOT reimplement): the executor CLI primitives + _discover_projects +
_write_job_result.
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
# ONE model SSOT for every job CLI. Imported from the side-effect-free leaf
# module, NOT from `..executor`: importing executor runs a `zsh -lic` subprocess
# and replaces os.environ["PATH"] at import time, which would fire during pytest
# collection (this handler is imported at test module level) and on an HTTP
# request path (core/ddd_drift_signal lazy-imports it from routers/eval).
from ..model_resolve import resolve_job_model as _resolve_job_model
from ..cli_result import cli_api_error, cli_too_old_message

logger = logging.getLogger("swarm.jobs.ddd_self_audit")

PROJECTS_DIR = SWARMWS / "Projects"

# Read-only toolset: the agent proposes, the OS writes (core/ddd_drift_fix). NEVER add Write/Edit/Bash.
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
      - ``clean``  : returncode 0 and a real result, use the result.
      - ``failed`` : any non-zero exit, OR an exit-0 run the CLI reported as an
        API error (``is_error`` / ``"API Error: ..."``; ``cli_result.cli_api_error``).
        Carries an ``error_detail`` (the API error, the CLI ``errors`` field, else
        a stderr tail) so the report shows the REAL cause instead of "exit N".

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
    api_err = cli_api_error(output)
    if api_err is not None:
        return {"status": "failed", "error_detail": api_err[1]}
    return {"status": "clean", "result_text": result_text}


_RADAR_TODOS_RE = re.compile(r"<!--\s*RADAR_TODOS\s*(\[.*?\])\s*-->", re.DOTALL)


def _count_parseable_findings(result_text: str) -> list:
    """Return the parseable findings — i.e. a RADAR_TODOS block (the agent's
    structured-output convention) that parses as valid JSON. Output can be
    interrupted mid-JSON (e.g. a subprocess timeout / truncated stream); a malformed
    block yields 0, so the reported drift count never over-states findings. These
    findings feed the report + the semantic-drift signal (get_semantic_drift), NOT
    todo creation (removed run_50db230a). Pure, no I/O — unit-testable."""
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
    """The first local repo a code-backed project's bindings.yaml declares
    (ddd_bindings.governed_repo_roots), or None. Any project, declared not guessed
    (usable-by-anyone P7); returning None means no --add-dir the agent cannot use."""
    from core.ddd_bindings import governed_repo_roots
    repos = governed_repo_roots(PROJECTS_DIR / project_name)
    return repos[0] if repos else None


def _build_audit_prompt(project_name: str, project_dir: Path, code_backed: bool) -> str:
    """Domain-aware review prompt. The agent emits a RADAR_TODOS block of findings, each
    optionally with a ``fix`` that core/ddd_drift_fix checks; it has read-only tools."""
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

You have Read/Grep/Glob ONLY — you cannot edit any file. The system can apply exactly three
kinds of fix, and only after checking them itself. Name one in a finding's `fix` when it applies
(the system computes the new text; you never write it):

1. "drop_condition" — a line waits on something a ticked `- [x]` line of this project's DDD
   docs shows is done. `line` = a verbatim piece of that line (unique in the doc); `span` = the
   waiting condition, exactly one of: once / after / until / when `X` exists|lands|ships|is
   built|is done|is ready|is available|is implemented|is shipped|is merged, or 等/待 `X`
   存在|上线|完成|就绪|落地|建好(后); `X` in backticks as in the doc; `done` = the ticked line,
   verbatim, naming the same `X` in backticks — or the same name with or without a prefix
   (`s_x` and `x`, `pkg/x` and `x` count as the same).
2. "swap_superseded_path" — the doc cites a file whose own header says it is SUPERSEDED and
   names the current file. Open every file the docs cite and read its first lines to find
   these. `path_old` = the cited path as written; `path_new` = the path the header names, as
   written there.
3. "swap_code_value" — the doc states a number or version for a code identifier and a
   tracked source file of the bound repo assigns a different one (`IDENT = value`). `line` =
   a verbatim piece of the doc line; `identifier`, `value_old`, `value_new`; `code_file` = the
   source file path inside the repo (not tests, vendored or built code).

Anything else — rewording, a dated record (a `- **Title** …` entry), plans, current focus,
which of two docs is right — gets no `fix`: the owner decides it.

Output format — end your response with EXACTLY this block (omit entirely if you find
NO real drift; do not invent findings to fill it):

<!-- RADAR_TODOS
[
  {{"title": "DDD drift [{project_name}/DOC.md]: <one-line what's stale>",
    "description": "<the exact stale claim, verbatim> — why it's false: <reason> — evidence: <code location file:line OR the contradicting doc line>",
    "fix": {{"type": "drop_condition", "doc": "DOC.md", "line": "<verbatim piece of the line>",
            "span": "once `X` exists", "done": "<the ticked line, verbatim>"}}}}
]
-->

`fix` is optional per finding: omit it unless it is one of the three. At most 5 findings,
highest-confidence first. If the DDD is accurate, say so in one line and emit no block."""


def _build_review_cmd(claude_path: str, use_bare: bool, project_name: str,
                      src: Path | None, prompt: str) -> list[str]:
    """The review subprocess's command line. Pure, so the read-only guarantee is testable.

    The agent reads documents that may carry untrusted text, so it gets nothing else:
    - ``--tools Read,Grep,Glob``: the tools that EXIST (``--allowedTools`` only
      auto-approves; under bypassPermissions it left Bash / Write / Edit usable — the
      lesson scripts/scenario_runner.py records);
    - no MCP servers (``--strict-mcp-config`` with no ``--mcp-config``): an MCP tool
      (Slack, mail) would be a way to send what it read off the machine;
    - ``--restricted`` with ``dontAsk``: file tools confined to the workspace and the
      bound repo (measured: a Read outside them is denied), anything unapproved denied
      (restricted mode refuses bypassPermissions)."""
    cmd = [
        claude_path, "--print",
        *(["--bare"] if use_bare else []),
        "--output-format", "json",
        "--no-session-persistence",
        # Same SSOT as every other job CLI (_resolve_job_model): a bare family
        # alias carries no `[1m]` suffix, which caps the context window and 400s.
        "--model", _resolve_job_model(),
        "--restricted",
        "--permission-mode", "dontAsk",
        "--strict-mcp-config",
    ]
    cmd += ["--tools", ",".join(_AUDIT_TOOLS), "--allowedTools", ",".join(_AUDIT_TOOLS)]
    cmd += ["--add-dir", str(SWARMWS)]
    if src:
        cmd += ["--add-dir", str(src)]
    cmd += ["--system-prompt",
            f"You are SwarmAI running the scheduled DDD self-audit for {project_name}. "
            f"Read-only review. Report drift; never edit."]
    cmd += ["-p", prompt]
    return cmd


def _resolve_findings(findings: list, project_name: str, project_dir: Path, src: Path | None,
                      *, run_id: str, dry_run: bool, today: str) -> list[dict]:
    """Each finding → fixed / would-fix / open, with the reason (core/ddd_drift_fix)."""
    from core.ddd_drift_fix import apply_fix, first_seen, parse_fix

    reports = SWARMWS / "Knowledge" / "JobResults"
    out: list[dict] = []
    for index, f in enumerate(findings):
        if not isinstance(f, dict):
            continue
        title = str(f.get("title") or "")
        fix = parse_fix(f)
        row = {"index": index, "title": title, "doc": (fix or {}).get("doc")}
        if fix is None:
            row.update(outcome="open", reason="no certain fix: the right content needs your judgement")
            needle = _first_quote(str(f.get("description") or ""))
        else:
            try:
                res = apply_fix(fix, project_dir, SWARMWS, src, run_id=run_id,
                                finding_title=title, dry_run=dry_run)
            except Exception as e:  # noqa: BLE001 — one bad fix must not stop the audit
                logger.warning("ddd_self_audit: fix for %r failed: %s", title, e)
                res = {"ok": False, "reason": f"the fix failed to apply ({e.__class__.__name__}); nothing changed"}
            if res.get("ok"):
                row.update(outcome="would-fix" if dry_run else "fixed", reason=res.get("reason"),
                           type=fix["type"], old=res.get("old_line", ""), new=res.get("new_line", ""))
            else:
                row.update(outcome="open", reason=res.get("reason"))
            needle = fix.get("line") or fix.get("path_old") or ""
        if row["outcome"] == "open":
            row["first_seen"] = first_seen(needle, reports, before=today)
        out.append(row)
    return out


def _first_quote(text: str) -> str:
    """The first quoted span of a finding's description (its verbatim stale claim)."""
    m = re.search(r'"([^"]{8,200})"|“([^”]{8,200})”', text)
    return (m.group(1) or m.group(2)) if m else ""


def _fixed_line(project: str, o: dict) -> str:
    old, new = (o.get("old") or "").strip(), (o.get("new") or "").strip()
    return f"- [{project}/{o.get('doc')}] ({o.get('type')}) \"{old[:140]}\" → \"{new[:140]}\""


def _open_line(project: str, o: dict) -> str:
    seen = f" · first seen {o['first_seen']}" if o.get("first_seen") else ""
    return f"- {o.get('title') or project} · {o.get('reason')}{seen}"


def run_ddd_self_audit(config: dict | None = None) -> dict:
    """Loop all DDD projects; per project, run a bounded read-only review subprocess,
    apply the certain fixes (core/ddd_drift_fix) and write the report. ``config.dry_run``: check every fix, change no doc, write only a dry-run report."""
    # Import executor primitives lazily (avoid import cycle: executor imports handlers).
    from ..executor import (
        _resolve_claude_cli, _check_claude_auth, _get_aws_credentials,
        _cli_supports_bare, _cli_version, _build_cli_env, _parse_cli_output,
        _write_job_result,
    )
    from ..models import Job, JobSafety, JobType
    import subprocess

    start = datetime.now(timezone.utc)
    config = config or {}
    dry_run = bool(config.get("dry_run"))
    run_id = start.strftime("%Y%m%dT%H%M%SZ")

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

    cli_version = _cli_version(claude_path)
    too_old = cli_too_old_message(cli_version, _resolve_job_model())
    if too_old:
        return {"status": "failed", "summary": too_old, "output_path": None,
                "error": f"cli_too_old: {too_old}"}

    aws_creds = _get_aws_credentials()
    use_bare = _cli_supports_bare(claude_path, cli_version)
    env = _build_cli_env(aws_creds)

    report_lines = [f"# DDD Self-Audit — {start.strftime('%Y-%m-%d')}" + (" (dry run)" if dry_run else ""), ""]
    fixed_lines: list[str] = []
    open_lines: list[str] = []
    n_fixed = 0
    total_findings = 0
    reviewed = 0
    first_failure: str | None = None  # the cause shown when NOTHING could be reviewed

    for project_name, project_dir in projects:
        code_backed = _is_code_backed(project_dir)
        prompt = _build_audit_prompt(project_name, project_dir, code_backed)

        # Code-backed project: also grant read of the live SOURCE repo it describes.
        src = _source_repo_for(project_name) if code_backed else None
        cmd = _build_review_cmd(claude_path, use_bare, project_name, src, prompt)

        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True,
                timeout=_PER_PROJECT_TIMEOUT_S, env=env, cwd=str(SWARMWS),
            )
        except subprocess.TimeoutExpired:
            report_lines.append(f"## {project_name} — ⏱ timed out ({_PER_PROJECT_TIMEOUT_S}s), skipped")
            first_failure = first_failure or f"timeout: review timed out after {_PER_PROJECT_TIMEOUT_S}s"
            continue

        # Parse stdout regardless of exit code — purely for OBSERVABILITY, so a
        # genuine failure surfaces its REAL cause instead of an opaque "exit N".
        # Pure helper → unit-tested without subprocess mocking.
        verdict = _classify_review_result(
            proc.returncode, _parse_cli_output(proc.stdout), proc.stderr or ""
        )
        kind = "code-backed" if code_backed else "non-code"

        if verdict["status"] == "failed":
            # Genuine failure (MCP/auth/crash) — surface the REAL cause, not "exit N".
            how = f"exit {proc.returncode}" if proc.returncode != 0 else "API error"
            first_failure = first_failure or f"review_failed ({how}): {verdict['error_detail'][:300]}"
            report_lines.append(
                f"## {project_name} — ⚠️ review failed ({how}): {verdict['error_detail']}"
            )
            report_lines.append("")
            continue

        result_text = verdict["result_text"]
        reviewed += 1
        # Count ACTUAL parseable findings, not raw '"title"' substrings — a truncated
        # RADAR_TODOS block yields 0 todos downstream (_parse_structured_todos returns
        # [] on JSONDecodeError); counting substrings would over-report.
        findings = _count_parseable_findings(result_text)
        n_findings = len(findings)
        total_findings += n_findings
        outcomes = _resolve_findings(findings, project_name, project_dir, src,
                                     run_id=run_id, dry_run=dry_run, today=start.strftime("%Y-%m-%d"))
        for o in outcomes:
            if o["outcome"] in ("fixed", "would-fix"):
                n_fixed += 1
                fixed_lines.append(_fixed_line(project_name, o))
            else:
                open_lines.append(_open_line(project_name, o))
        header = f"## {project_name} ({kind}) — {n_findings} finding(s)"
        report_lines.append(header)
        # the agent's own text must not carry an outcomes block (it could mark open drift
        # fixed); ours comes last and cannot be cut short by a "-->" inside a title
        # neither a forged outcomes block nor a "## " line (which would start a new section
        # and detach our block from this project) may come from the agent's text
        agent_text = (result_text.strip() or "_(no output)_").replace("DRIFT_OUTCOMES", "DRIFT-OUTCOMES")
        report_lines.append(re.sub(r"(?m)^(#{1,6}\s)", r" \1", agent_text))
        if outcomes:
            blob = json.dumps(outcomes, ensure_ascii=False).replace("-->", "--\\u003e")
            report_lines.append(f"<!-- DRIFT_OUTCOMES {blob} -->")
        report_lines.append("")

    fixed_word = "would fix" if dry_run else "fixed"
    headline = (f"Found {total_findings} · {fixed_word} {n_fixed} · needs you {total_findings - n_fixed}"
                f" · reviewed {reviewed}/{len(projects)} projects")
    summary_block = [headline, ""]
    if fixed_lines:
        summary_block += [f"**{'Would fix (dry run, nothing written)' if dry_run else 'Fixed automatically'}**", *fixed_lines]
        if not dry_run:
            summary_block.append("Each fix is recorded with the text it replaced; to put one back: "
                                 "`py -m scripts.ddd_drift_fix_cli list <project>`, then `undo <project> <index>`.")
        summary_block.append("")
    if open_lines:
        summary_block += ["**Needs you**", *open_lines, ""]
    report_lines[2:2] = summary_block
    report_text = "\n".join(report_lines)
    duration = (datetime.now(timezone.utc) - start).total_seconds()

    # Persist the report (Python writes it — the agent never had Write).
    synthetic_job = Job(
        # a dry run gets its own file: it must not replace the day's real report, and
        # ddd_drift_signal (``*-ddd-self-audit.md``) must never read its would-fix rows
        id="ddd-self-audit-dryrun" if dry_run else "ddd-self-audit", name="DDD Self-Audit",
        type=JobType.DDD_SELF_AUDIT,
        schedule="", config={},
        safety=JobSafety(),
    )
    output_path = _write_job_result(
        synthetic_job, report_text, start, tokens=0, duration=duration,
        status="success" if reviewed > 0 else "failed")

    # Findings live in the report (Knowledge/JobResults/*-ddd-self-audit.md) and are
    # surfaced as a semantic-drift signal via ddd_drift_signal.get_semantic_drift
    # (open findings only). No Radar todo write (run_50db230a): the ToDo card is
    # a pure user-planning surface — system self-audit findings keep their own home
    # (the report), never an auto-written todo.

    summary = f"{headline} · report → Knowledge/JobResults/"
    logger.info("ddd_self_audit complete: %s", summary)
    # Not a success if NOTHING was reviewed — every project's review failed (e.g.
    # every CLI run came back as an API error). The report still records why.
    status = "success" if reviewed > 0 else "failed"
    if status == "failed":
        summary = f"All {len(projects)} DDD reviews failed — {first_failure} · report → Knowledge/JobResults/"
    return {
        "status": status,
        "error": first_failure if status == "failed" else None,
        "projects_reviewed": reviewed,
        "findings": total_findings,
        "fixed": n_fixed,
        "dry_run": dry_run,
        "summary": summary,
        "output_path": str(output_path) if output_path else None,
    }
