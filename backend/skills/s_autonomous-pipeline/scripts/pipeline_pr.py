"""Auto-create PR after pipeline declares push-ready.

Reads run.json + REPORT.md from a pipeline run directory, constructs PR
title and body, then calls `gh pr create` with auto-merge enabled.

Non-blocking: failure is a warning, not an error. The pipeline is still
push-ready regardless of whether the PR is successfully created.

Usage:
    python scripts/pipeline_pr.py --run-dir <path> [--dry-run]
"""
import argparse
import json
import re
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

# Profiles that produce code deliveries worthy of a PR
PR_ELIGIBLE_PROFILES = frozenset({"full", "bugfix"})

# Max PR title length per GitHub conventions
MAX_TITLE_LENGTH = 70

# Max body size (GitHub API limit is 65536 chars)
MAX_BODY_LENGTH = 60000

# Strict allowlist for run_id in branch names (PE-1: prevent ref manipulation)
_RUN_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


def _load_json(path: Path) -> dict:
    """Load JSON file, return empty dict on failure.

    Logs specific error to stderr for debugging (PE-6).
    """
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return {}
    except (json.JSONDecodeError, OSError) as e:
        print(f"[pipeline_pr] Warning: failed to load {path}: {e}", file=sys.stderr)
        return {}


def _extract_tldr(report_text: str) -> str:
    """Extract TL;DR section from REPORT.md."""
    match = re.search(r"## TL;DR\n(.+?)(?:\n##|\Z)", report_text, re.DOTALL)
    if match:
        return match.group(1).strip()
    # Fallback: first non-header paragraph
    lines = [l for l in report_text.split("\n") if l.strip() and not l.startswith("#")]
    return lines[0] if lines else "Pipeline delivery"


def _extract_files_section(report_text: str) -> str:
    """Extract Files Changed section from REPORT.md."""
    match = re.search(r"## 8\. Files Changed\n(.+?)(?:\n##|\Z)", report_text, re.DOTALL)
    if match:
        return match.group(1).strip()
    return ""


def _infer_scope(files: list[str]) -> str:
    """Infer PR scope prefix from changed file paths."""
    if not files:
        return ""
    dirs = []
    for f in files:
        parts = Path(f).parts
        if len(parts) >= 2:
            dirs.append(parts[0] if parts[0] != "." else parts[1])
    if not dirs:
        return ""
    common = Counter(dirs).most_common(1)[0][0]
    return common


def format_pr_title(requirement: str, files_changed: list[str]) -> str:
    """Format PR title from requirement text.

    Format: feat(<scope>): <condensed requirement>
    Always <=70 chars.
    """
    scope = _infer_scope(files_changed)
    prefix = f"feat({scope}): " if scope else "feat: "

    max_desc = MAX_TITLE_LENGTH - len(prefix)
    desc = requirement.strip()

    # Remove common filler words for brevity
    desc = re.sub(r"^(Add|Implement|Create|Build)\s+", "", desc, flags=re.IGNORECASE)

    if len(desc) > max_desc:
        # Truncate at word boundary (PE-4: handle no-space case)
        truncated = desc[: max_desc - 1].rsplit(" ", 1)[0]
        desc = (truncated if truncated else desc[: max_desc - 1]) + "…"

    # Capitalize first char for consistent casing (PE-9)
    if desc and desc[0].islower():
        desc = desc[0].upper() + desc[1:]

    return prefix + desc


def format_pr_body(run_data: dict, report_text: str) -> str:
    """Format PR body from run.json data and REPORT.md content.

    Includes: Summary, Pipeline Delivery stats, Files Changed, Test Plan.
    Test Plan checkboxes reflect actual data (PE-5: no hardcoded [x]).
    """
    tldr = _extract_tldr(report_text)
    files_section = _extract_files_section(report_text)
    run_id = run_data.get("id", "unknown")
    profile = run_data.get("profile", "unknown")
    confidence = run_data.get("confidence_score", "?")

    # Adversarial stats (PE-8: coerce to int for safety)
    adv = run_data.get("adversarial_review", {})
    adv_total = int(adv.get("pe_findings", 0) or 0) + int(adv.get("user_findings", 0) or 0)
    adv_fixed = int(adv.get("pe_fixed", 0) or 0) + int(adv.get("user_fixed", 0) or 0)
    adv_unfixed = adv_total - adv_fixed

    # Convergence
    conv = run_data.get("convergence", {})
    conv_iter = conv.get("iterations", 0)

    # TDD from stages
    regressions = 0
    for stage in run_data.get("stages", []):
        if stage.get("stage") == "build":
            tdd = stage.get("tdd", {})
            regressions = int(tdd.get("regressions", 0) or 0)
            break

    tdd_info = "0 regressions" if regressions == 0 else f"{regressions} regressions"

    # PE-5: dynamic checkboxes based on actual state
    ac_check = "[x]" if run_data.get("ac_verification", {}).get("status") == "verified" else "[ ]"
    regression_check = "[x]" if regressions == 0 else "[ ]"
    adversarial_check = "[x]" if adv_unfixed == 0 else "[ ]"

    body = f"""## Summary
{tldr}

## Pipeline Delivery
- **Run:** {run_id} | **Profile:** {profile} | **Confidence:** {confidence}/12
- **Adversarial:** {adv_total} findings, {adv_fixed} fixed
- **Convergence:** {conv_iter} iteration(s)
- **TDD:** {tdd_info}

## Files Changed
{files_section if files_section else "(see REPORT.md)"}

## Test Plan
- {ac_check} All acceptance criteria verified (AC Verification)
- {regression_check} Full test suite passes ({tdd_info})
- {adversarial_check} Adversarial review clean

Full report: `.artifacts/runs/{run_id}/REPORT.md`

Generated with [SwarmAI Autonomous Pipeline](https://github.com/xg-gh-25/SwarmAI)
"""
    if len(body) > MAX_BODY_LENGTH:
        body = body[:MAX_BODY_LENGTH - 50] + "\n\n...(truncated, see REPORT.md)"

    return body


def _check_gh_auth() -> tuple[bool, str]:
    """Check if gh CLI is authenticated."""
    try:
        result = subprocess.run(
            ["gh", "auth", "status"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.returncode == 0, result.stderr
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        return False, str(e)


def _get_current_branch() -> str:
    """Get current git branch name."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return "unknown"


def _get_default_branch() -> str | None:
    """Resolve the repo's DEFAULT branch — never assume 'main'.

    A project's base branch may be main/master/develop/anything. Order:
    remote HEAD symref (authoritative) → first existing of common names.
    Returns None if none resolves (so the caller can let `gh` fall back to
    its own default rather than forcing a wrong `--base`).
    """
    try:
        r = subprocess.run(
            ["git", "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        ref = r.stdout.strip()
        if ref:
            # "origin/main" → "main"
            return ref.split("/", 1)[1] if "/" in ref else ref
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    for cand in ("main", "master", "develop"):
        try:
            v = subprocess.run(
                ["git", "rev-parse", "--verify", f"origin/{cand}"],
                capture_output=True, text=True, timeout=5,
            )
            if v.returncode == 0:
                return cand
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return None
    return None


def create_pr(run_dir: str, dry_run: bool = False) -> dict:
    """Create a PR from pipeline run artifacts.

    Args:
        run_dir: Path to pipeline run directory containing run.json.
        dry_run: If True, return the command without executing.

    Returns:
        Dict with keys: success, skipped, pr_url, command, error, reason.
    """
    run_path = Path(run_dir)

    # PE-7: validate run_dir is a directory
    if not run_path.is_dir():
        return {"success": False, "error": f"Not a directory: {run_dir}"}

    run_data = _load_json(run_path / "run.json")

    if not run_data:
        return {"success": False, "error": "run.json not found or invalid"}

    # Profile gate (AC7)
    profile = run_data.get("profile", "")
    if profile not in PR_ELIGIBLE_PROFILES:
        return {"skipped": True, "reason": f"Profile '{profile}' not eligible for auto-PR"}

    # Load REPORT.md for body content
    report_path = run_path / "REPORT.md"
    report_text = ""
    if report_path.exists():
        try:
            report_text = report_path.read_text()
        except OSError:
            pass

    # Format title and body
    requirement = run_data.get("requirement", "Pipeline delivery")
    files_changed = []
    for stage in run_data.get("stages", []):
        if stage.get("stage") == "build":
            files_changed = stage.get("files_changed", [])
            break

    title = format_pr_title(requirement, files_changed)
    body = format_pr_body(run_data, report_text)

    # Build the gh command — use --body-file to avoid ARG_MAX and ps leakage (PE-2)
    if dry_run:
        return {
            "success": True,
            "skipped": False,
            "command": " ".join(["gh", "pr", "create", "--title", repr(title), "--body-file", "<tmp>", "--auto"]),
            "title": title,
            "body_length": len(body),
        }

    # Check gh auth (AC6: graceful failure)
    auth_ok, auth_err = _check_gh_auth()
    if not auth_ok:
        return {"success": False, "error": f"gh auth failed: {auth_err}"}

    # Branch handling (AC5): if on the base branch, create a feature branch first.
    # base_branch = what the PR should target. NEVER hardcode 'main': if we're on
    # a mainline (main/master), the PR base IS that branch; otherwise resolve the
    # repo's default branch (may be develop/master/etc). None → let gh default.
    branch = _get_current_branch()
    if branch in ("main", "master"):
        base_branch = branch  # PR targets the mainline we branched off
        run_id = run_data.get("id", "pipeline")
        # PE-1: validate run_id before using in branch name
        if not _RUN_ID_PATTERN.match(run_id):
            return {"success": False, "error": f"Invalid run_id for branch name: {run_id!r}"}
        feature_branch = f"pipeline/{run_id}"
        try:
            subprocess.run(
                ["git", "checkout", "-b", feature_branch],
                capture_output=True, text=True, timeout=10, check=True,
            )
            subprocess.run(
                ["git", "push", "-u", "origin", feature_branch],
                capture_output=True, text=True, timeout=30, check=True,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            # PE-3: cleanup — return to original branch on failure
            subprocess.run(["git", "checkout", branch], capture_output=True, timeout=5)
            return {"success": False, "error": f"Branch creation failed: {e}"}
    else:
        # Already on a feature branch → PR targets the repo's default branch
        # (resolved, not assumed main). None → omit --base, let gh choose.
        base_branch = _get_default_branch()
        # Push current branch to remote
        try:
            subprocess.run(
                ["git", "push", "-u", "origin", branch],
                capture_output=True, text=True, timeout=30, check=True,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            return {"success": False, "error": f"Push failed: {e}"}

    # Execute PR creation using --body-file (PE-2: avoids ARG_MAX + process table leak)
    body_file = None
    try:
        body_file = tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", prefix="pr-body-", delete=False
        )
        body_file.write(body)
        body_file.close()

        gh_cmd = ["gh", "pr", "create", "--title", title, "--body-file", body_file.name, "--auto"]
        # Target the resolved base branch explicitly (never let gh silently pick
        # the repo default when we KNOW the base — e.g. we branched off master).
        if base_branch:
            gh_cmd += ["--base", base_branch]
        result = subprocess.run(
            gh_cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            pr_url = result.stdout.strip()
            return {"success": True, "pr_url": pr_url}
        else:
            return {"success": False, "error": f"gh pr create failed: {result.stderr.strip()}"}
    except (subprocess.TimeoutExpired, OSError) as e:
        return {"success": False, "error": f"PR creation error: {e}"}
    finally:
        # Cleanup temp file
        if body_file:
            try:
                Path(body_file.name).unlink(missing_ok=True)
            except OSError:
                pass


def main():
    parser = argparse.ArgumentParser(description="Auto-create PR from pipeline run")
    parser.add_argument("--run-dir", required=True, help="Path to pipeline run directory")
    parser.add_argument("--dry-run", action="store_true", help="Print command without executing")
    args = parser.parse_args()

    result = create_pr(args.run_dir, dry_run=args.dry_run)
    print(json.dumps(result, indent=2))

    if result.get("skipped"):
        sys.exit(0)
    sys.exit(0 if result.get("success") else 1)


if __name__ == "__main__":
    main()
