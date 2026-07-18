"""Auto-refresh live codebase metrics + capability list into marker-delimited docs.

Scans the repo for quantitative data (commit count, LOC, skill count, etc.)
and updates marker-delimited sections. Prose outside markers is NEVER touched —
but staleness warnings are emitted when key code patterns diverge from
documented expectations.

Write targets (deliberately split so volatile numbers stay out of context):
  METRICS      → docs/CODEBASE_METRICS.md ONLY. These numbers churn daily, so
                 they live in a standalone file and are NOT written into the
                 context-loaded AGENTS.md (nor AI_CONTEXT.md). Both of those
                 just carry a static pointer to the metrics file.
  CAPABILITIES → AI_CONTEXT.md + AGENTS.md. The engine list is stable and
                 actionable, so it stays inline where readers/agents expect it.

Markers:
  <!-- METRICS_START --> ... <!-- METRICS_END -->
  <!-- CAPABILITIES_START --> ... <!-- CAPABILITIES_END -->

Usage:
  python backend/scripts/refresh_ai_docs.py [--dry-run] [--check-staleness]

Integration:
  Called by context_health_hook on startup (daily gate) and by release preflight.
"""

import re
import shlex
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

try:
    import yaml
except ImportError:
    yaml = None  # Fallback to manual parsing if PyYAML not available

# Global deadline — script must complete within this budget (seconds)
_SCRIPT_DEADLINE: float = 0.0  # Set at entry point
_SCRIPT_TIMEOUT: float = 8.0  # Leave 2s margin for caller


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
AI_CONTEXT = REPO_ROOT / "AI_CONTEXT.md"
AGENTS_MD = REPO_ROOT / "AGENTS.md"
# Volatile metrics live here ONLY — kept out of the context-loaded AGENTS.md.
METRICS_FILE = REPO_ROOT / "docs" / "CODEBASE_METRICS.md"
ENGINES_YAML = Path(__file__).resolve().parent / "engines.yaml"

# Code Intelligence DB locations (checked in order)
CODE_INTEL_DB_PATHS = [
    Path.home() / ".swarm-ai" / "SwarmWS" / "Projects" / "SwarmAI" / "code_intel.db",
    Path.home() / ".swarm-ai" / "SwarmWS" / "Projects" / "SwarmAI" / ".code_intel" / "code_intel.db",
]

# Prose staleness checks — patterns expected in AGENTS.md prose.
# If any check fails, a [STALE] warning is emitted (never auto-fixed).
STALENESS_CHECKS = [
    {
        "name": "SSE event types",
        "description": "SSE Streaming Events section lists event types",
        "prose_file": "AGENTS.md",
        "prose_pattern": r'"type": "session_start"',
        # SSE events are emitted by the streaming orchestrator, not session_unit
        # (the state machine). Pointing at session_unit produced a false-positive
        # STALE warning since 2026-06 (the literal lives in streaming_orchestrator).
        "code_file": "backend/core/streaming_orchestrator.py",
        "code_pattern": r'"type": "session_start"',
    },
    {
        "name": "SSE result event",
        "description": "SSE section documents 'result' event",
        "prose_file": "AGENTS.md",
        "prose_pattern": r'"type": "result"',
        "code_file": "backend/core/streaming_orchestrator.py",
        "code_pattern": r'"type": "result"',
    },
    {
        "name": "Security hooks",
        "description": "Security Architecture mentions 4-layer chain",
        "prose_file": "AGENTS.md",
        "prose_pattern": r"Four-layer PreToolUse",
        "code_file": "backend/core/security_hooks.py",
        "code_pattern": r"pre_tool_logger|dangerous_command|skill_access",
    },
    {
        "name": "Session states",
        "description": "Session unit 5-state machine documented",
        "prose_file": "AGENTS.md",
        "prose_pattern": r"COLD.*IDLE.*STREAMING.*WAITING_INPUT.*DEAD",
        "code_file": "backend/core/session_unit.py",
        "code_pattern": r"class SessionState",
    },
    {
        "name": "Context files count",
        "description": "11 context files documented",
        "prose_file": "AGENTS.md",
        "prose_pattern": r"11 source files",
        "code_file": "backend/core/context_directory_loader.py",
        "code_pattern": r"All 11 context source files",
    },
    {
        "name": "Token budget tiers",
        "description": "Token budget tiers match code",
        "prose_file": "AGENTS.md",
        "prose_pattern": r"100K for .{0,5}500K",
        "code_file": "backend/core/context_directory_loader.py",
        "code_pattern": r"500_000",
    },
]


def _run(cmd: str, cwd: Path | None = None) -> str:
    """Run shell command, return stdout stripped. Empty string on failure.

    Respects global deadline — returns empty string if budget exhausted.
    """
    global _SCRIPT_DEADLINE
    if _SCRIPT_DEADLINE and time.monotonic() > _SCRIPT_DEADLINE:
        return ""  # Budget exhausted
    try:
        remaining = max(1.0, _SCRIPT_DEADLINE - time.monotonic()) if _SCRIPT_DEADLINE else 3.0
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True,
            timeout=min(3.0, remaining), cwd=cwd or REPO_ROOT,
        )
        return result.stdout.strip() if result.returncode == 0 else ""
    except subprocess.TimeoutExpired:
        return ""


def _count_pattern_in_dir(directory: Path, pattern: str) -> int:
    """Count files matching a grep pattern in a directory."""
    if not directory.exists():
        return 0
    result = _run(f"grep -rl {shlex.quote(pattern)} {shlex.quote(str(directory))} 2>/dev/null | wc -l")
    return int(result.strip()) if result.strip().isdigit() else 0


def _load_engines() -> list[dict]:
    """Load engine registry from engines.yaml."""
    if not ENGINES_YAML.exists():
        return []

    content = ENGINES_YAML.read_text(encoding="utf-8")

    if yaml:
        data = yaml.safe_load(content)
        return data.get("engines", []) if data else []

    # Fallback: simple line-based parsing for when PyYAML isn't available
    engines = []
    current = {}
    for line in content.split("\n"):
        line = line.strip()
        if line.startswith("- name:"):
            if current:
                engines.append(current)
            current = {"name": line.split(":", 1)[1].strip().strip('"')}
        elif line.startswith("path:") and current:
            current["path"] = line.split(":", 1)[1].strip().strip('"')
        elif line.startswith("description:") and current:
            current["description"] = line.split(":", 1)[1].strip().strip('"')
    if current:
        engines.append(current)
    return engines


def _get_code_intel_stats() -> tuple[int, int]:
    """Get symbol and edge counts from code_intel.db. Returns (0, 0) if unavailable."""
    for db_path in CODE_INTEL_DB_PATHS:
        if db_path.exists():
            try:
                conn = sqlite3.connect(
                    f"file:{db_path}?mode=ro", uri=True, timeout=3,
                )
                try:
                    # Try both possible table names
                    for node_table, edge_table in [("code_nodes", "code_edges"), ("symbols", "edges")]:
                        try:
                            nodes = conn.execute(f"SELECT COUNT(*) FROM {node_table}").fetchone()[0]
                            edges = conn.execute(f"SELECT COUNT(*) FROM {edge_table}").fetchone()[0]
                            return nodes, edges
                        except sqlite3.OperationalError:
                            continue
                finally:
                    conn.close()
            except Exception:
                continue
    return 0, 0


def collect_metrics() -> dict:
    """Collect all quantitative metrics from the codebase."""
    m = {}

    # Git metrics
    m["commit_count"] = _run("git log --oneline | wc -l").strip()
    m["duration_days"] = _run(
        "echo $(( ($(date +%s) - $(git log --reverse --format='%at' | head -1)) / 86400 ))"
    )

    # Backend metrics — git-tracked, tests-OUT caliber (same as
    # total_backend_loc below). The old `find backend/core …` filesystem
    # commands counted the 10 core-internal test files under
    # backend/core/code_intel/tests/ (2178 LOC) as production code, leaving
    # core_loc inconsistent with total_backend_loc (which excludes /tests/).
    # `grep '^backend/core/'` covers BOTH top-level core/*.py AND nested
    # core/**/*.py (a single git glob '**/*.py' misses the top level).
    # core_loc + core_modules MUST move in lockstep or '143 modules / 70404
    # LOC' would self-contradict (one counts tests, the other doesn't).
    # `awk 'END{if(NR>0) print NR}'` (not `wc -l`) so an empty/degraded
    # git ls-files emits "" not a confident-but-wrong 0 — symmetric with the
    # core_loc + total_backend_loc empty-guards below (Gate-2 LOW, run_e92f91dc).
    _core_mods = _run(
        "git ls-files '*.py' | grep '^backend/core/' | grep -v '/tests/' "
        "| awk 'END{if(NR>0) print NR}'"
    ).strip()
    m["core_modules"] = _core_mods if _core_mods.isdigit() else ""
    _core_loc = _run(
        "git ls-files '*.py' | grep '^backend/core/' | grep -v '/tests/' "
        "| xargs wc -l | awk '$2!=\"total\"{n+=$1} END{if(n>0) print n}'"
    ).strip()
    m["core_loc"] = _core_loc if (_core_loc.isdigit() and int(_core_loc) > 0) else ""
    # git-tracked, non-test caliber: reproducible + auto-excludes .venv
    # site-packages and gitignored skills (e.g. private CMHK). The old
    # `find … | xargs cat | wc -l` (a) cat'd ~313K lines and timed out under
    # the accumulated _run deadline -> blank, and (b) counted venv + gitignored
    # code -> a non-reproducible inflated number. `wc -l` (not cat) is also far
    # cheaper. Consistent with commit_count, which already uses git.
    # `awk` sums each file's line count itself (skipping wc's own 'total' line)
    # -> portable across BSD/GNU xargs AND robust to the empty-list case (GNU
    # xargs would run `wc -l` on empty stdin and print a bogus 0; here awk on an
    # empty stream prints nothing -> caught by the empty/0 guard below).
    _loc = _run(
        "git ls-files '*.py' | grep '^backend/' | grep -v '/tests/' "
        "| xargs wc -l | awk '$2!=\"total\"{n+=$1} END{if(n>0) print n}'"
    ).strip()
    # Empty or 0 = git unavailable / wrong prefix / shallow clone — a known
    # failure signal, not a real count. Leave the prior value's slot empty so
    # the WARN surfaces rather than rendering a confident-but-wrong 0.
    m["total_backend_loc"] = _loc if (_loc.isdigit() and int(_loc) > 0) else ""
    m["test_files"] = _run("find backend/tests -name '*.py' | wc -l").strip()
    m["hooks_count"] = _run("ls backend/hooks/*.py | wc -l").strip()

    # Skills
    m["skill_count"] = _run("ls -d backend/skills/s_* | wc -l").strip()

    # Frontend
    m["react_components"] = _run(
        "find desktop/src -name '*.tsx' | wc -l"
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

    # Jobs
    m["job_count"] = _run(
        "find backend/jobs -name '*.py' -path '*/handlers/*' | wc -l"
    ).strip()

    # Code Intelligence stats (measured from DB)
    symbols, edges = _get_code_intel_stats()
    m["code_intel_symbols"] = symbols
    m["code_intel_edges"] = edges

    # Engines (discovery-based from engines.yaml)
    engines = []
    for entry in _load_engines():
        path = entry.get("path", "")
        if path and (REPO_ROOT / path).exists():
            engines.append(entry)
    m["engines"] = engines

    return m


def _generate_metrics_block(metrics: dict) -> str:
    """Generate the metrics replacement block."""
    # Commands in "How to Verify" match what the script actually runs
    return f"""| Metric | Value | How to Verify |
|--------|-------|---------------|
| Total commits | {metrics['commit_count']}+ | `git log --oneline | wc -l` |
| Duration | ~{metrics['duration_days']} days | First commit to latest (1 human contributor) |
| Backend core modules | {metrics['core_modules']} Python files, {metrics['core_loc']} LOC | `git ls-files '*.py' | grep '^backend/core/' | grep -v '/tests/' | xargs wc -l | awk '$2!="total"{{n+=$1}} END{{if(n>0) print n}}'` |
| Total backend LOC | {metrics['total_backend_loc']} | `git ls-files '*.py' | grep '^backend/' | grep -v '/tests/' | xargs wc -l | awk '$2!="total"{{n+=$1}} END{{print n}}'` |
| Test files | {metrics['test_files']} | `find backend/tests -name "*.py" | wc -l` |
| Skills (agent capabilities) | {metrics['skill_count']} | `ls -d backend/skills/s_* | wc -l` |
| Post-session hooks | {metrics['hooks_count']} | `ls backend/hooks/*.py | wc -l` |
| React components | {metrics['react_components']} | `find desktop/src -name "*.tsx" | wc -l` |
| Pipeline spec depth | {metrics['pipeline_spec_lines']} lines | `wc -l backend/skills/s_autonomous-pipeline/INSTRUCTIONS.md` |
| Largest state machine | {metrics['session_unit_lines']} lines | `wc -l backend/core/session_unit.py` |
| Context system | {metrics['context_loader_lines']} lines | `wc -l backend/core/context_directory_loader.py` |
| Platform modes | {metrics['platform_modes']} | |
| Background jobs | {metrics['job_count']} handlers | `find backend/jobs -name "*.py" -path "*/handlers/*" | wc -l` |
| Code graph | {metrics['code_intel_symbols']:,} symbols, {metrics['code_intel_edges']:,} edges | `code_intel.db` (code_nodes / code_edges tables) |"""


def _generate_capabilities_block(metrics: dict) -> str:
    """Generate the capabilities/engines replacement block.

    Descriptions are STATIC (straight from engines.yaml) — no live counts are
    injected. This keeps the block (which lives inline in the context-loaded
    AGENTS.md) stable: it only changes when an engine is added, removed, or
    re-described. Volatile measurements like the code-graph symbol/edge counts
    belong in the metrics table (docs/CODEBASE_METRICS.md), not here.
    """
    lines = ["| Engine | Path | What It Does |", "|--------|------|-------------|"]

    for engine in metrics.get("engines", []):
        name = engine.get("name", "")
        path = engine.get("path", "")
        desc = engine.get("description", "")
        lines.append(f"| {name} | `{path}` | {desc} |")
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


def check_staleness() -> list[dict]:
    """Check prose sections against code patterns. Returns list of stale findings."""
    warnings = []

    for check in STALENESS_CHECKS:
        prose_path = REPO_ROOT / check["prose_file"]
        code_path = REPO_ROOT / check["code_file"]

        if not prose_path.exists():
            continue

        prose_content = prose_path.read_text(encoding="utf-8")

        # Check prose pattern exists (documentation present)
        prose_match = re.search(check["prose_pattern"], prose_content)
        if not prose_match:
            warnings.append({
                "check": check["name"],
                "severity": "STALE",
                "message": f"Prose pattern not found in {check['prose_file']}: {check['prose_pattern']!r}",
                "description": check["description"],
            })
            continue

        # Check code pattern exists (implementation present)
        if code_path.is_dir():
            # For directories, check if pattern exists in any file
            if "count_check" in check:
                count = _count_pattern_in_dir(code_path, check["code_pattern"])
                if count < check["count_check"]:
                    warnings.append({
                        "check": check["name"],
                        "severity": "STALE",
                        "message": (
                            f"Expected {check['count_check']} matches for "
                            f"'{check['code_pattern']}' in {check['code_file']}, "
                            f"found {count}"
                        ),
                        "description": check["description"],
                    })
        elif code_path.exists():
            code_content = code_path.read_text(encoding="utf-8")
            if not re.search(check["code_pattern"], code_content):
                warnings.append({
                    "check": check["name"],
                    "severity": "STALE",
                    "message": (
                        f"Code pattern '{check['code_pattern']}' not found in "
                        f"{check['code_file']} — prose may be outdated"
                    ),
                    "description": check["description"],
                })
        else:
            warnings.append({
                "check": check["name"],
                "severity": "MISSING",
                "message": f"Code file {check['code_file']} not found",
                "description": check["description"],
            })

    return warnings


def refresh(dry_run: bool = False, staleness_only: bool = False) -> dict:
    """Main entry point — refresh both AI_CONTEXT.md and AGENTS.md."""
    global _SCRIPT_DEADLINE
    # Always reset deadline — prevents stale deadline from prior import-based calls
    _SCRIPT_DEADLINE = time.monotonic() + _SCRIPT_TIMEOUT

    results = {"files_updated": [], "staleness_warnings": []}

    # Always run staleness checks
    staleness = check_staleness()
    results["staleness_warnings"] = staleness
    if staleness:
        for w in staleness:
            print(f"  [{w['severity']}] {w['check']}: {w['message']}")

    if staleness_only:
        return results

    metrics = collect_metrics()

    # Safety: don't write corrupted metrics if deadline exhausted all commands
    if not metrics.get("commit_count") or not metrics.get("core_loc"):
        print("⚠️  Metrics collection incomplete (timeout?) — skipping write")
        results["metrics"] = metrics
        return results

    metrics_block = _generate_metrics_block(metrics)
    capabilities_block = _generate_capabilities_block(metrics)
    results["metrics"] = metrics

    # Per-file section plan. METRICS is volatile and churns daily, so it is
    # written ONLY to the standalone docs/CODEBASE_METRICS.md — never into the
    # context-loaded AGENTS.md (or AI_CONTEXT.md), which just point to it.
    # CAPABILITIES (stable, actionable) stays inline in both prose docs.
    write_plan: list[tuple[Path, list[tuple[str, str, str]]]] = [
        (METRICS_FILE, [("<!-- METRICS_START -->", "<!-- METRICS_END -->", metrics_block)]),
        (AI_CONTEXT, [("<!-- CAPABILITIES_START -->", "<!-- CAPABILITIES_END -->", capabilities_block)]),
        (AGENTS_MD, [("<!-- CAPABILITIES_START -->", "<!-- CAPABILITIES_END -->", capabilities_block)]),
    ]

    for filepath, sections in write_plan:
        if not filepath.exists():
            continue

        original = filepath.read_text(encoding="utf-8")
        updated = original
        for start_marker, end_marker, block in sections:
            updated = _replace_section(updated, start_marker, end_marker, block)

        if updated != original:
            if dry_run:
                print(f"[DRY RUN] Would update: {filepath.name}")
            else:
                filepath.write_text(updated, encoding="utf-8")
                print(f"Updated: {filepath.name}")
            results["files_updated"].append(filepath.name)
        else:
            print(f"No changes: {filepath.name}")

    return results


if __name__ == "__main__":
    _SCRIPT_DEADLINE = time.monotonic() + _SCRIPT_TIMEOUT

    dry_run = "--dry-run" in sys.argv
    staleness_only = "--check-staleness" in sys.argv

    result = refresh(dry_run=dry_run, staleness_only=staleness_only)

    if staleness_only:
        n = len(result["staleness_warnings"])
        print(f"\nStaleness check: {n} warning(s)")
        sys.exit(1 if n > 0 else 0)

    if result["files_updated"]:
        m = result.get("metrics", {})
        print(f"\nRefreshed {len(result['files_updated'])} file(s)")
        print(f"Skills: {m.get('skill_count', '?')}, "
              f"Commits: {m.get('commit_count', '?')}, "
              f"Engines: {len(m.get('engines', []))}")

    n = len(result["staleness_warnings"])
    if n > 0:
        print(f"\n⚠️  {n} staleness warning(s) — prose sections may need manual update")
    else:
        print("\nNo updates needed (markers not found or content unchanged)")
