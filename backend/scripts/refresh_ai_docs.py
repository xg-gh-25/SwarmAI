"""Auto-refresh AI_CONTEXT.md and AGENTS.md with live codebase metrics.

Scans the repo for quantitative data (commit count, LOC, skill count, etc.)
and updates marker-delimited sections in both files. Prose outside markers
is NEVER touched.

Markers:
  <!-- METRICS_START --> ... <!-- METRICS_END -->
  <!-- CAPABILITIES_START --> ... <!-- CAPABILITIES_END -->

Usage:
  python backend/scripts/refresh_ai_docs.py [--dry-run]

Integration:
  Called by context_health_hook on startup (daily gate) and by release preflight.
"""

import re
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
AI_CONTEXT = REPO_ROOT / "AI_CONTEXT.md"
AGENTS_MD = REPO_ROOT / "AGENTS.md"


def _run(cmd: str, cwd: Path | None = None) -> str:
    """Run shell command, return stdout stripped. Empty string on failure."""
    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True,
            timeout=10, cwd=cwd or REPO_ROOT,
        )
        return result.stdout.strip() if result.returncode == 0 else ""
    except subprocess.TimeoutExpired:
        return ""


def collect_metrics() -> dict:
    """Collect all quantitative metrics from the codebase."""
    m = {}

    # Git metrics
    m["commit_count"] = _run("git log --oneline | wc -l").strip()
    m["duration_days"] = _run(
        "echo $(( ($(date +%s) - $(git log --reverse --format='%at' | head -1)) / 86400 ))"
    )
    m["contributors"] = _run("git log --format='%aN' | sort -u | wc -l").strip()

    # Backend metrics
    m["core_modules"] = _run("find backend/core -name '*.py' | wc -l").strip()
    m["core_loc"] = _run(
        "find backend/core -name '*.py' -exec cat {} + | wc -l"
    ).strip()
    m["total_backend_loc"] = _run(
        "find backend -name '*.py' -not -path '*/.*' -not -path '*/__pycache__/*' | xargs cat | wc -l"
    ).strip()
    m["test_files"] = _run("find backend/tests -name '*.py' | wc -l").strip()
    m["hooks_count"] = _run("ls backend/hooks/*.py | wc -l").strip()

    # Skills
    m["skill_count"] = _run("ls -d backend/skills/s_* | wc -l").strip()
    skills_raw = _run("ls -d backend/skills/s_* | xargs -I{} basename {}")
    m["skill_names"] = [s.replace("s_", "") for s in skills_raw.split("\n") if s]

    # Frontend
    m["react_components"] = _run(
        "find desktop/src -name '*.tsx' | wc -l"
    ).strip()
    m["frontend_loc"] = _run(
        "find desktop/src -name '*.ts' -o -name '*.tsx' | xargs wc -l | tail -1 | awk '{print $1}'"
    ).strip()

    # Platform
    m["platform_modes"] = "4 (macOS daemon, Windows subprocess, Linux subprocess, Hive systemd)"

    # Pipeline
    pipeline_instructions = REPO_ROOT / "backend/skills/s_autonomous-pipeline/INSTRUCTIONS.md"
    m["pipeline_spec_lines"] = _run(f"wc -l < {pipeline_instructions}").strip()

    # Session unit
    session_unit = REPO_ROOT / "backend/core/session_unit.py"
    m["session_unit_lines"] = _run(f"wc -l < {session_unit}").strip()

    # Context system
    context_loader = REPO_ROOT / "backend/core/context_directory_loader.py"
    m["context_loader_lines"] = _run(f"wc -l < {context_loader}").strip()

    # Key engines (detect existence)
    engines = []
    engine_checks = {
        "DDD Cultivation Engine (event-driven v2)": "backend/core/cultivation_dispatcher.py",
        "Autonomous Pipeline (9-stage)": "backend/skills/s_autonomous-pipeline/INSTRUCTIONS.md",
        "Pollinate Content Engine": "backend/skills/s_pollinate/INSTRUCTIONS.md",
        "GitHub Community Engine": "backend/skills/s_github_community/scripts/monitor.py",
        "Evolution Pipeline (MINE→ASSESS→ACT→AUDIT)": "backend/core/evolution_optimizer.py",
        "Code Intelligence (AST graph)": "backend/core/code_intel/__init__.py",
        "Session Resume Enrichment": "backend/core/context_injector.py",
        "Proactive Intelligence (L0-L4)": "backend/core/proactive_intelligence.py",
        "Slack Channel Adapter": "backend/channels/adapters/slack.py",
        "Background Job System": "backend/jobs/scheduler.py",
        "Star Attribution Tracking": "backend/skills/s_github_community/scripts/track.py",
        "AI Docs Auto-Refresh": "backend/scripts/refresh_ai_docs.py",
    }
    for name, path in engine_checks.items():
        if (REPO_ROOT / path).exists():
            engines.append(name)
    m["engines"] = engines

    # Jobs
    m["job_count"] = _run(
        "find backend/jobs -name '*.py' -path '*/handlers/*' | wc -l"
    ).strip()

    return m


def _generate_metrics_block(metrics: dict) -> str:
    """Generate the metrics replacement block."""
    return f"""| Metric | Value | How to Verify |
|--------|-------|---------------|
| Total commits | {metrics['commit_count']}+ | `git log --oneline | wc -l` |
| Duration | ~{metrics['duration_days']} days | First commit to latest (1 human contributor) |
| Backend core modules | {metrics['core_modules']} Python files, {metrics['core_loc']} LOC | `find backend/core -name "*.py" | wc -l` |
| Total backend LOC | {metrics['total_backend_loc']} | `find backend -name "*.py" | xargs wc -l | tail -1` |
| Test files | {metrics['test_files']} | `find backend/tests -name "*.py" | wc -l` |
| Skills (agent capabilities) | {metrics['skill_count']} | `ls -d backend/skills/s_* | wc -l` |
| Post-session hooks | {metrics['hooks_count']} | `ls backend/hooks/*.py | wc -l` |
| React components | {metrics['react_components']} | `find desktop/src -name "*.tsx" | wc -l` |
| Pipeline spec depth | {metrics['pipeline_spec_lines']} lines | `wc -l backend/skills/s_autonomous-pipeline/INSTRUCTIONS.md` |
| Largest state machine | {metrics['session_unit_lines']} lines | `wc -l backend/core/session_unit.py` |
| Context system | {metrics['context_loader_lines']} lines | `wc -l backend/core/context_directory_loader.py` |
| Platform modes | {metrics['platform_modes']} | |
| Background jobs | {metrics['job_count']} handlers | `find backend/jobs -name "*.py" -path "*/handlers/*" | wc -l` |"""


def _generate_capabilities_block(metrics: dict) -> str:
    """Generate the capabilities/engines replacement block."""
    lines = ["| Engine | Path | What It Does |", "|--------|------|-------------|"]
    engine_details = {
        "DDD Cultivation Engine (event-driven v2)": ("backend/core/cultivation_dispatcher.py", "Event-driven domain knowledge growth — 6 event sources, gate-based promotion, maturity tracking"),
        "Autonomous Pipeline (9-stage)": ("backend/skills/s_autonomous-pipeline/", "EVALUATE→THINK→PLAN→BUILD(TDD)→REVIEW→TEST→DELIVER→REFLECT with adversarial review gate"),
        "Pollinate Content Engine": ("backend/skills/s_pollinate/", "Message-first media delivery — transforms ideas into posters, videos, narratives, README"),
        "GitHub Community Engine": ("backend/skills/s_github_community/", "Autonomous learning flywheel — monitor, match, draft, track, cultivate, report across GitHub"),
        "Evolution Pipeline (MINE→ASSESS→ACT→AUDIT)": ("backend/core/evolution_optimizer.py", "Confidence-gated self-evolution from session mining and skill fitness scoring"),
        "Code Intelligence (AST graph)": ("backend/core/code_intel/", "11K+ symbols, 12K+ edges — deterministic graph traversal for code context retrieval"),
        "Session Resume Enrichment": ("backend/core/context_injector.py", "Cold resume from ~3K to ~50-100K tokens of structured context"),
        "Proactive Intelligence (L0-L4)": ("backend/core/proactive_intelligence.py", "Session briefing, corrections, open threads, signals — fires on every session start"),
        "Slack Channel Adapter": ("backend/channels/adapters/slack.py", "24/7 Socket Mode bot — responds as XG's AI assistant to allowlisted users"),
        "Background Job System": ("backend/jobs/", "Cron + event-triggered headless Claude CLI tasks — signal pipeline, monitoring, reports"),
        "Star Attribution Tracking": ("backend/skills/s_github_community/scripts/track.py", "Tracks stargazers with timestamps, attributes to engagement activity via shared discussions"),
        "AI Docs Auto-Refresh": ("backend/scripts/refresh_ai_docs.py", "Self-maintaining documentation — scans codebase metrics and capabilities daily, updates AI_CONTEXT.md + AGENTS.md"),
    }
    for engine in metrics.get("engines", []):
        path, desc = engine_details.get(engine, ("", ""))
        lines.append(f"| {engine} | `{path}` | {desc} |")
    return "\n".join(lines)


def _replace_section(content: str, start_marker: str, end_marker: str, new_block: str) -> str:
    """Replace content between markers. If markers don't exist, return unchanged."""
    pattern = re.compile(
        rf"({re.escape(start_marker)}\n)(.*?)({re.escape(end_marker)})",
        re.DOTALL,
    )
    if not pattern.search(content):
        return content
    return pattern.sub(rf"\g<1>{new_block}\n\g<3>", content)


def refresh(dry_run: bool = False) -> dict:
    """Main entry point — refresh both AI_CONTEXT.md and AGENTS.md."""
    metrics = collect_metrics()
    metrics_block = _generate_metrics_block(metrics)
    capabilities_block = _generate_capabilities_block(metrics)

    results = {"files_updated": [], "metrics": metrics}

    for filepath in [AI_CONTEXT, AGENTS_MD]:
        if not filepath.exists():
            continue

        original = filepath.read_text()
        updated = original

        # Replace metrics section
        updated = _replace_section(
            updated,
            "<!-- METRICS_START -->",
            "<!-- METRICS_END -->",
            metrics_block,
        )

        # Replace capabilities section
        updated = _replace_section(
            updated,
            "<!-- CAPABILITIES_START -->",
            "<!-- CAPABILITIES_END -->",
            capabilities_block,
        )

        if updated != original:
            if dry_run:
                print(f"[DRY RUN] Would update: {filepath.name}")
            else:
                filepath.write_text(updated)
                print(f"Updated: {filepath.name}")
            results["files_updated"].append(filepath.name)
        else:
            print(f"No changes: {filepath.name}")

    return results


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    result = refresh(dry_run=dry_run)
    if result["files_updated"]:
        print(f"\nRefreshed {len(result['files_updated'])} file(s)")
        print(f"Skills: {result['metrics']['skill_count']}, "
              f"Commits: {result['metrics']['commit_count']}, "
              f"Engines: {len(result['metrics']['engines'])}")
    else:
        print("\nNo updates needed (markers not found or content unchanged)")
