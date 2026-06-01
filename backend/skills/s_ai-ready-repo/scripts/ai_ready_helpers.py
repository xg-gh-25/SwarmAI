"""AI-Ready-Repo Engine — Helper Script for Deterministic Operations.

Handles operations where LLM would hallucinate or be unreliable:
- Git history parsing (commit hashes, dates, file changes)
- File tree building (accurate filesystem state)
- Tech stack detection (from config files)
- code-intel.json v2 schema validation
- AGENTS.md template rendering

All functions are pure/stateless. No LLM calls. No network.
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ─── Input Validation ───

def _validate_repo_path(repo_path: Path) -> Path:
    """Validate repo path: must exist, be a directory, and contain .git.

    Resolves symlinks to prevent traversal attacks.
    Raises ValueError if validation fails.
    """
    repo_path = Path(repo_path).resolve()

    if not repo_path.exists():
        raise ValueError(f"Path does not exist: {repo_path}")
    if not repo_path.is_dir():
        raise ValueError(f"Path is not a directory: {repo_path}")
    if not (repo_path / ".git").exists():
        raise ValueError(f"Not a git repository (no .git): {repo_path}")

    return repo_path


def _safe_file_read(file_path: Path, repo_root: Path, max_size: int = 10 * 1024 * 1024) -> str | None:
    """Read a file safely: resolve symlinks, enforce containment within repo_root.

    Returns file content or None if unsafe/unreadable.
    """
    resolved = file_path.resolve()
    try:
        resolved.relative_to(repo_root.resolve())
    except ValueError:
        # Path traversal attempt — file resolves outside repo
        logger.warning(f"Path traversal blocked: {file_path} resolves to {resolved}")
        return None

    if not resolved.is_file():
        return None

    try:
        if resolved.stat().st_size > max_size:
            logger.warning(f"File too large ({resolved.stat().st_size} bytes), skipping: {resolved}")
            return None
        return resolved.read_text(encoding="utf-8", errors="replace")
    except (OSError, PermissionError) as e:
        logger.warning(f"Cannot read {resolved}: {e}")
        return None


# ─── code-intel.json v2 Schema Validation ───

_REQUIRED_TOP_LEVEL = {"$schema", "version", "repo", "modules", "edges", "entry_points"}
_REQUIRED_REPO = {"name", "languages", "total_symbols", "total_edges"}
_REQUIRED_MODULE = {"name", "path", "responsibility"}
_OPTIONAL_TOP_LEVEL = {"routes", "hot_zones", "risk_areas", "dead_code", "dependencies", "generated_at"}


def validate_code_intel_json(doc: dict) -> list[str]:
    """Validate a code-intel.json document against v2 schema.

    Returns list of error strings. Empty list = valid.
    Does NOT use jsonschema library — pure Python for zero-dep operation.
    """
    errors: list[str] = []

    # Top-level required fields
    for field in _REQUIRED_TOP_LEVEL:
        if field not in doc:
            errors.append(f"Missing required top-level field: '{field}'")

    # Version check
    if doc.get("version") and doc["version"] != "2.0":
        errors.append(f"Invalid version: expected '2.0', got '{doc['version']}'")

    # Repo structure
    repo = doc.get("repo")
    if isinstance(repo, dict):
        for field in _REQUIRED_REPO:
            if field not in repo:
                errors.append(f"Missing required repo field: '{field}'")
    elif "repo" in doc:
        errors.append("'repo' must be a dict")

    # Modules validation
    modules = doc.get("modules")
    if isinstance(modules, list):
        for i, mod in enumerate(modules):
            if not isinstance(mod, dict):
                errors.append(f"modules[{i}] must be a dict")
                continue
            for field in _REQUIRED_MODULE:
                if field not in mod:
                    errors.append(f"modules[{i}] missing required field: '{field}'")
    elif "modules" in doc and not isinstance(modules, list):
        errors.append("'modules' must be a list")

    # Edges validation (basic structure check)
    edges = doc.get("edges")
    if isinstance(edges, list):
        for i, edge in enumerate(edges):
            if not isinstance(edge, dict):
                errors.append(f"edges[{i}] must be a dict")
            elif "from" not in edge or "to" not in edge:
                errors.append(f"edges[{i}] must have 'from' and 'to' fields")

    # Entry points validation
    entry_points = doc.get("entry_points")
    if isinstance(entry_points, list):
        for i, ep in enumerate(entry_points):
            if not isinstance(ep, dict):
                errors.append(f"entry_points[{i}] must be a dict")
            elif "path" not in ep:
                errors.append(f"entry_points[{i}] must have 'path' field")

    return errors


# ─── Git History Parsing for Gotchas ───

_FIX_PATTERN = re.compile(
    r"^(fix|hotfix|revert|bugfix)[\s:(/]",
    re.IGNORECASE,
)


def parse_git_gotchas(repo_path: Path) -> list[dict[str, str]]:
    """Extract gotchas from git history using fix/revert/hotfix commits.

    Returns list of dicts with keys: when, risk, because.
    Only returns entries with real commit hash evidence.
    Raises ValueError if repo_path is not a valid git repository.
    """
    repo_path = _validate_repo_path(Path(repo_path))
    gotchas: list[dict[str, str]] = []

    # Get git log with hash, date, subject, and files changed
    try:
        result = subprocess.run(
            [
                "git", "log", "--pretty=format:%H|%ai|%s",
                "--name-only", "--diff-filter=M",
                "-n", "200",  # Last 200 commits max
            ],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        logger.warning(f"Git log timed out for {repo_path}")
        return []

    if result.returncode != 0:
        logger.warning(f"Git log failed for {repo_path}: {result.stderr.strip()}")
        return []

    # Parse log into commit records
    commits = _parse_git_log(result.stdout)

    # Filter to fix/revert/hotfix commits
    fix_commits = [c for c in commits if _FIX_PATTERN.match(c["subject"])]

    # Group by files touched — repeated fixes to same file = gotcha
    file_fixes: dict[str, list[dict]] = {}
    for commit in fix_commits:
        for f in commit.get("files", []):
            file_fixes.setdefault(f, []).append(commit)

    # Generate gotchas for files with 2+ fix commits (repeated pain)
    for filepath, commits_list in file_fixes.items():
        if len(commits_list) >= 2:
            hashes = ", ".join(c["hash"][:7] for c in commits_list[:3])
            subjects = "; ".join(c["subject"] for c in commits_list[:2])
            gotchas.append({
                "when": f"modifying {filepath}",
                "risk": f"Repeated fixes needed — {subjects}",
                "because": f"commits {hashes} ({len(commits_list)} incidents)",
            })

    # Single fix commits that are reverts are always gotchas
    for commit in fix_commits:
        if commit["subject"].lower().startswith("revert"):
            # Extract what was reverted from subject
            subject = commit["subject"]
            files = commit.get("files", ["unknown file"])
            file_str = files[0] if files else "unknown"
            gotchas.append({
                "when": f"modifying {file_str}",
                "risk": f"Change was reverted — {subject}",
                "because": f"commit {commit['hash'][:7]}",
            })

    # Deduplicate by 'when' field
    seen = set()
    unique_gotchas = []
    for g in gotchas:
        if g["when"] not in seen:
            seen.add(g["when"])
            unique_gotchas.append(g)

    return unique_gotchas


def _parse_git_log(log_output: str) -> list[dict]:
    """Parse git log --pretty=format:%H|%ai|%s --name-only output.

    Handles pipes in commit subjects by splitting on first 2 pipes only.
    Supports both SHA-1 (40 char) and future SHA-256 (64 char) hashes.
    """
    commits = []
    current: dict | None = None

    for line in log_output.strip().split("\n"):
        if not line:
            continue

        # Check if this is a header line (hash|date|subject)
        # Split on first 2 pipes only — subject may contain pipes
        if "|" in line:
            parts = line.split("|", 2)
            if len(parts) >= 3:
                hash_candidate = parts[0]
                # Support SHA-1 (40) and SHA-256 (64) hashes
                if (
                    len(hash_candidate) in (40, 64)
                    and all(c in "0123456789abcdef" for c in hash_candidate)
                ):
                    if current:
                        commits.append(current)
                    current = {
                        "hash": hash_candidate,
                        "date": parts[1].strip(),
                        "subject": parts[2].strip(),
                        "files": [],
                    }
                    continue

        # Otherwise it's a filename
        if current and line.strip():
            current["files"].append(line.strip())

    if current:
        commits.append(current)

    return commits


# ─── Repository Info Gathering ───

_LANG_EXTENSIONS = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".jsx": "javascript",
    ".rs": "rust",
    ".go": "go",
    ".java": "java",
    ".rb": "ruby",
    ".c": "c",
    ".cpp": "cpp",
    ".h": "c",
    ".cs": "csharp",
    ".kt": "kotlin",
    ".swift": "swift",
    ".php": "php",
    ".sh": "shell",
    ".bash": "shell",
}

_IGNORE_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv",
    "dist", "build", ".next", ".nuxt", "target", ".tox",
    ".eggs", "*.egg-info", ".mypy_cache", ".pytest_cache",
}


def gather_repo_info(repo_path: Path) -> dict[str, Any]:
    """Gather repository metadata for engine input.

    Returns dict with: file_tree, tech_stack, git_stats, readme_content, config_files.
    Works on ANY git repository — no SwarmAI-specific assumptions.
    Raises ValueError if repo_path is not a valid git repository.
    """
    repo_path = _validate_repo_path(Path(repo_path))

    return {
        "file_tree": _build_file_tree(repo_path),
        "tech_stack": _detect_tech_stack(repo_path),
        "git_stats": _get_git_stats(repo_path),
        "readme_content": _read_readme(repo_path),
        "config_files": _find_config_files(repo_path),
    }


def _build_file_tree(repo_path: Path, max_depth: int = 4) -> list[str]:
    """Build a flat file tree listing (relative paths), respecting .gitignore."""
    files = []

    # Use git ls-files if possible (respects .gitignore)
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=repo_path,
        capture_output=True,
        text=True,
        timeout=10,
    )

    if result.returncode == 0 and result.stdout.strip():
        files = [f for f in result.stdout.strip().split("\n") if f]
    else:
        # Fallback: walk filesystem
        for path in repo_path.rglob("*"):
            if path.is_file():
                rel = path.relative_to(repo_path)
                # Skip ignored directories
                if any(part in _IGNORE_DIRS for part in rel.parts):
                    continue
                if len(rel.parts) <= max_depth:
                    files.append(str(rel))

    return sorted(files)[:500]  # Cap at 500 files


def _detect_tech_stack(repo_path: Path) -> dict[str, Any]:
    """Detect languages, frameworks, and build tools from config files."""
    # Count language by file extension
    lang_counter: Counter = Counter()

    result = subprocess.run(
        ["git", "ls-files"],
        cwd=repo_path,
        capture_output=True,
        text=True,
        timeout=10,
    )

    files = result.stdout.strip().split("\n") if result.returncode == 0 else []

    for f in files:
        ext = Path(f).suffix.lower()
        if ext in _LANG_EXTENSIONS:
            lang_counter[_LANG_EXTENSIONS[ext]] += 1

    total = sum(lang_counter.values()) or 1
    languages = {lang: round(count / total, 2) for lang, count in lang_counter.most_common(10)}

    # Detect frameworks from config files
    frameworks: list[str] = []
    configs = {
        "pyproject.toml": "python-project",
        "package.json": "node-project",
        "Cargo.toml": "rust-project",
        "go.mod": "go-project",
        "pom.xml": "java-maven",
        "build.gradle": "java-gradle",
        "Gemfile": "ruby-project",
    }

    for config_file, framework in configs.items():
        if (repo_path / config_file).exists():
            frameworks.append(framework)

    # Detect web frameworks from imports
    framework_signals = {
        "fastapi": "FastAPI",
        "flask": "Flask",
        "django": "Django",
        "express": "Express",
        "next": "Next.js",
        "react": "React",
        "vue": "Vue",
        "angular": "Angular",
    }

    return {
        "languages": languages,
        "frameworks": frameworks,
        "framework_signals": [],  # Populated by LLM during UNDERSTAND phase
    }


def _get_git_stats(repo_path: Path) -> dict[str, Any]:
    """Get git statistics: total commits, contributors, recent activity."""
    stats: dict[str, Any] = {"total_commits": 0, "contributors": [], "last_commit_date": ""}

    # Total commits
    result = subprocess.run(
        ["git", "rev-list", "--count", "HEAD"],
        cwd=repo_path,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode == 0:
        try:
            stats["total_commits"] = int(result.stdout.strip())
        except (ValueError, AttributeError):
            stats["total_commits"] = 0

    # Contributors
    result = subprocess.run(
        ["git", "shortlog", "-sn", "--no-merges", "-n", "10"],
        cwd=repo_path,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode == 0:
        stats["contributors"] = [
            line.strip().split("\t", 1)[-1]
            for line in result.stdout.strip().split("\n")
            if line.strip()
        ][:10]

    # Last commit date
    result = subprocess.run(
        ["git", "log", "-1", "--format=%ai"],
        cwd=repo_path,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode == 0:
        stats["last_commit_date"] = result.stdout.strip()

    return stats


def _read_readme(repo_path: Path) -> str:
    """Read README content (first 200 lines). Uses safe file read with containment check."""
    for name in ["README.md", "readme.md", "README.rst", "README.txt", "README"]:
        content = _safe_file_read(repo_path / name, repo_path)
        if content:
            lines = content.split("\n")
            return "\n".join(lines[:200])
    return ""


def _find_config_files(repo_path: Path) -> dict[str, str]:
    """Find and read key config files (first 50 lines each). Uses safe file reads."""
    config_names = [
        "pyproject.toml", "package.json", "Cargo.toml", "go.mod",
        "Makefile", "Dockerfile", "docker-compose.yml",
        ".github/workflows/ci.yml", ".github/workflows/ci.yaml",
    ]

    configs: dict[str, str] = {}
    for name in config_names:
        content = _safe_file_read(repo_path / name, repo_path)
        if content:
            lines = content.split("\n")
            configs[name] = "\n".join(lines[:50])

    return configs


# ─── AGENTS.md Template Rendering ───

def render_agents_md(data: dict[str, Any]) -> str:
    """Render AGENTS.md from structured data. Output MUST be ≤150 lines.

    Args:
        data: Dict with keys: project_name, build_command, test_command,
              lint_command, test_duration, modules, entry_points,
              critical_rules, gotchas, score, generated_date.
    """
    lines: list[str] = []

    # Header
    lines.append(f"# {data['project_name']}")
    lines.append("")
    lines.append(
        f"> AI-Ready (DDD) | Generated {data['generated_date']} "
        f"| Score: {data['score']}/10 | [Review Report](.ai-ready/REVIEW-REPORT.md)"
    )
    lines.append("")

    # Quick Start
    lines.append("## Quick Start")
    lines.append(f"```")
    lines.append(f"{data.get('build_command', 'make build')}     # Build")
    lines.append(f"{data.get('test_command', 'make test')}      # Test ({data.get('test_duration', '~30s')})")
    if data.get("lint_command"):
        lines.append(f"{data['lint_command']}      # Lint")
    lines.append("```")
    lines.append("")

    # Architecture
    modules = data.get("modules", [])
    lines.append(f"## Architecture ({len(modules)} modules)")
    for mod in modules[:15]:  # Cap at 15 modules
        lines.append(f"- `{mod['path']}` — {mod['responsibility']}")
    lines.append("")

    # Entry Points
    entry_points = data.get("entry_points", [])
    if entry_points:
        lines.append("## Entry Points")
        for ep in entry_points[:5]:
            lines.append(f"- `{ep['path']}` → {ep['type']} ({ep['description']})")
        lines.append("")

    # Critical Rules
    rules = data.get("critical_rules", [])
    if rules:
        lines.append("## Critical Rules")
        for rule in rules[:10]:
            prefix = "❌" if rule.get("type") == "never" else "✅"
            lines.append(f"- {prefix} {rule['rule']} — {rule['reason']}")
        lines.append("")

    # Top Gotchas
    gotchas = data.get("gotchas", [])
    if gotchas:
        lines.append("## Top Gotchas")
        for i, g in enumerate(gotchas[:5], 1):
            lines.append(f"{i}. {g['summary']} (evidence: {g['evidence']})")
        lines.append("")

    # Deep Context (DDD) table
    lines.append("## Deep Context (DDD)")
    lines.append("| Need to understand... | Read |")
    lines.append("|---|---|")
    lines.append("| Why this exists, what's out of scope | [PRODUCT.md](.ai-ready/PRODUCT.md) |")
    lines.append("| Architecture, conventions, invariants | [TECH.md](.ai-ready/TECH.md) |")
    lines.append("| What failed, known issues, patterns | [IMPROVEMENT.md](.ai-ready/IMPROVEMENT.md) |")
    lines.append("| Current priorities, active decisions | [PROJECT.md](.ai-ready/PROJECT.md) |")
    lines.append("| Module dependencies, blast radius | [code-intel.json](.ai-ready/code-intel.json) |")
    lines.append("")

    # User section marker
    lines.append("<!-- user: Your additions below — refresh preserves this section -->")

    # Enforce ≤150 line hard limit — trim gotchas and rules if over
    MAX_LINES = 150
    if len(lines) > MAX_LINES:
        # Find sections we can trim (gotchas first, then rules)
        for section_header in ("## Top Gotchas", "## Critical Rules"):
            if len(lines) <= MAX_LINES:
                break
            start = next((i for i, l in enumerate(lines) if l == section_header), -1)
            if start == -1:
                continue
            end = next(
                (i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")),
                len(lines),
            )
            # Keep header + max 2 items + blank line
            keep = min(4, end - start)
            lines = lines[:start + keep] + lines[end:]

    return "\n".join(lines)


# ─── Import Graph Extraction ───

_IMPORT_PATTERNS = {
    "python": re.compile(r"^\s*(?:from\s+([\w.]+)\s+import|import\s+([\w.]+))"),
    "typescript": re.compile(r"""(?:import\s+.*?\s+from\s+['"]([^'"]+)['"]|require\(['"]([^'"]+)['"]\))"""),
    "go": re.compile(r'^\s*"([^"]+)"'),
}


def extract_import_graph(repo_path: Path) -> dict[str, Any]:
    """Extract REAL dependency graph from actual import statements in source code.

    Returns dict with:
      - modules: list of {name, path, imports_from, imported_by}
      - edges: list of {from, to, file, line}
      - stats: {files_scanned, edges_found}

    This function does NOT guess. Every edge has a source file:line citation.
    """
    repo_path = _validate_repo_path(Path(repo_path))

    # Get all source files
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=repo_path,
        capture_output=True,
        text=True,
        timeout=15,
    )
    if result.returncode != 0:
        return {"modules": [], "edges": [], "stats": {"files_scanned": 0, "edges_found": 0}}

    all_files = [f for f in result.stdout.strip().split("\n") if f]

    # Filter to source files only
    source_extensions = {".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".rb"}
    source_files = [f for f in all_files if Path(f).suffix in source_extensions]

    # Detect primary language
    lang_counter: Counter = Counter()
    for f in source_files:
        ext = Path(f).suffix
        if ext in (".py",):
            lang_counter["python"] += 1
        elif ext in (".ts", ".tsx", ".js", ".jsx"):
            lang_counter["typescript"] += 1
        elif ext == ".go":
            lang_counter["go"] += 1

    primary_lang = lang_counter.most_common(1)[0][0] if lang_counter else "python"

    # Extract imports from each source file
    edges: list[dict[str, str]] = []
    module_imports: dict[str, set] = {}  # file -> set of modules it imports
    files_scanned = 0

    for filepath in source_files[:300]:  # Cap at 300 files for large repos
        full_path = repo_path / filepath
        if not full_path.exists() or not full_path.is_file():
            continue

        try:
            content = full_path.read_text(encoding="utf-8", errors="replace")
        except (OSError, PermissionError):
            continue

        files_scanned += 1
        file_imports: set[str] = set()

        for line_num, line in enumerate(content.split("\n")[:200], 1):  # First 200 lines (imports at top)
            pattern = _IMPORT_PATTERNS.get(primary_lang)
            if not pattern:
                break

            match = pattern.match(line)
            if match:
                # Get the first non-None group
                imported = next((g for g in match.groups() if g), None)
                if imported:
                    # Normalize: "from .palace import X" -> ".palace"
                    # "from mempalace.backends import Y" -> "mempalace.backends"
                    file_imports.add(imported)
                    edges.append({
                        "from": filepath,
                        "to": imported,
                        "line": line_num,
                        "raw": line.strip(),
                    })

        if file_imports:
            module_imports[filepath] = file_imports

    # Build module-level summary (group by directory)
    dir_modules: dict[str, set] = {}
    for filepath in source_files:
        parts = Path(filepath).parts
        if len(parts) >= 2:
            module_name = parts[0] if parts[0] != "src" else (parts[1] if len(parts) > 2 else parts[0])
        else:
            module_name = Path(filepath).stem
        dir_modules.setdefault(module_name, set()).add(filepath)

    # Compute imports_from / imported_by per module
    modules: list[dict] = []
    for mod_name, mod_files in sorted(dir_modules.items()):
        imports_from: set[str] = set()
        for f in mod_files:
            for imp in module_imports.get(f, set()):
                # Resolve relative imports to module names
                if imp.startswith("."):
                    # Relative import within same package
                    imp_module = imp.lstrip(".").split(".")[0] if imp.lstrip(".") else mod_name
                else:
                    imp_module = imp.split(".")[0]
                if imp_module != mod_name and imp_module in dir_modules:
                    imports_from.add(imp_module)

        modules.append({
            "name": mod_name,
            "path": f"{mod_name}/",
            "files": sorted(mod_files)[:20],
            "imports_from": sorted(imports_from),
        })

    # Compute imported_by (inverse of imports_from)
    for mod in modules:
        mod["imported_by"] = sorted(
            m["name"] for m in modules
            if mod["name"] in m.get("imports_from", [])
        )

    return {
        "modules": modules,
        "edges": edges[:500],  # Cap for memory
        "stats": {
            "files_scanned": files_scanned,
            "edges_found": len(edges),
            "primary_language": primary_lang,
        },
    }


# ─── Output Path Resolution ───

def resolve_output_path(
    repo_path: Path,
    project_name: str | None = None,
    target: str | None = None,
) -> Path:
    """Resolve where to write AI-Ready output.

    Priority:
    1. User-specified target path (if provided)
    2. SwarmWS .artifacts/ directory (if running inside SwarmAI)
    3. Alongside the repo itself ({repo_parent}/ai-ready-{name}/)

    Always returns an absolute path. Creates directories if needed.
    """
    if target:
        out = Path(target).resolve()
        out.mkdir(parents=True, exist_ok=True)
        return out

    repo_path = Path(repo_path).resolve()
    name = project_name or repo_path.name

    # Check if we're in SwarmAI workspace
    swarmws = Path.home() / ".swarm-ai" / "SwarmWS"
    if swarmws.exists():
        out = swarmws / "Projects" / "ai_ready_repo" / ".artifacts" / f"ai-ready-{name}"
        out.mkdir(parents=True, exist_ok=True)
        return out

    # Fallback: alongside repo
    out = repo_path.parent / f"ai-ready-{name}"
    out.mkdir(parents=True, exist_ok=True)
    return out


# ─── AI-Ready Metadata ───

def build_ai_ready_meta(score: float, project_name: str) -> dict[str, Any]:
    """Build ai-ready.json metadata document."""
    now = datetime.now(timezone.utc).isoformat()
    return {
        "version": "1.0",
        "engine": "SwarmAI AI-Ready-Repo Engine",
        "generated_at": now,
        "project": project_name,
        "score": {
            "overall": score,
            "dimensions": {},  # Populated by LLM during GENERATE phase
        },
        "freshness": {
            "overall": "fresh",
            "last_structural_check": now,
            "last_semantic_refresh": now,
            "commits_since_refresh": 0,
            "per_file": {
                "PRODUCT.md": {"status": "fresh", "last_verified": now[:10]},
                "TECH.md": {"status": "fresh", "last_verified": now[:10]},
                "IMPROVEMENT.md": {"status": "fresh", "last_verified": now[:10]},
                "PROJECT.md": {"status": "fresh", "last_verified": now[:10]},
            },
        },
    }
