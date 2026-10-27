"""AI-Ready-Repo Engine — Helper Script for Deterministic Operations.

Handles operations where LLM would hallucinate or be unreliable:
- Git history parsing (commit hashes, dates, file changes)
- File tree building (accurate filesystem state)
- Tech stack detection (from config files)
- AGENTS.md template rendering

All functions are pure/stateless. No LLM calls. No network.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ─── Input Validation ───

def _validate_repo_path(repo_path: Path) -> Path:
    """Validate repo path: must exist, be a directory, and be git-tracked — EITHER
    a repo root (has its own .git) OR a subdirectory inside a git work-tree
    (a monorepo package member, whose .git lives at the repo root).

    A monorepo member has no .git of its own but git ls-files / log scoped to it
    work against the parent repo, so the analysis functions are fully functional.
    The old .git-must-exist check wrongly rejected every detected monorepo member.

    Resolves symlinks to prevent traversal attacks.
    Raises ValueError if validation fails.
    """
    repo_path = Path(repo_path).resolve()

    if not repo_path.exists():
        raise ValueError(f"Path does not exist: {repo_path}")
    if not repo_path.is_dir():
        raise ValueError(f"Path is not a directory: {repo_path}")
    if (repo_path / ".git").exists():
        return repo_path
    # Not a repo root — accept iff inside a git work-tree (monorepo member).
    try:
        inside = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=repo_path, capture_output=True, text=True, timeout=10,
        )
        if inside.returncode == 0 and inside.stdout.strip() == "true":
            return repo_path
    except (subprocess.TimeoutExpired, OSError):
        pass  # fall through to the strict rejection below
    raise ValueError(f"Not a git repository (no .git): {repo_path}")


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


def _count_langs_by_ext(files) -> "Counter":
    """Count files by language via _LANG_EXTENSIONS. Shared by _detect_tech_stack
    and the multi-package language-mix (Gate-1 B1: single counting source, no
    third copy of the ext map). `files` = iterable of relative path strings."""
    counter: Counter = Counter()
    for f in files:
        if not f:
            continue
        ext = Path(f).suffix.lower()
        if ext in _LANG_EXTENSIONS:
            counter[_LANG_EXTENSIONS[ext]] += 1
    return counter


_IGNORE_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv",
    "dist", "build", ".next", ".nuxt", "target", ".tox",
    ".eggs", ".mypy_cache", ".pytest_cache",
}
# Glob patterns that need fnmatch (can't use set membership)
_IGNORE_DIR_PATTERNS = ["*.egg-info"]


def _is_ignored_dir(part: str) -> bool:
    """Check if a path component should be ignored (exact match or glob pattern)."""
    import fnmatch
    if part in _IGNORE_DIRS:
        return True
    return any(fnmatch.fnmatch(part, pat) for pat in _IGNORE_DIR_PATTERNS)


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
    try:
        result = subprocess.run(
            ["git", "ls-files"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except subprocess.TimeoutExpired:
        logger.warning(f"git ls-files timed out for {repo_path}, falling back to rglob")
        result = None

    if result and result.returncode == 0 and result.stdout.strip():
        files = [f for f in result.stdout.strip().split("\n") if f]
    else:
        # Fallback: walk filesystem
        for path in repo_path.rglob("*"):
            if path.is_file():
                rel = path.relative_to(repo_path)
                # Skip ignored directories
                if any(_is_ignored_dir(part) for part in rel.parts):
                    continue
                if len(rel.parts) <= max_depth:
                    files.append(str(rel))

    return sorted(files)[:500]  # Cap at 500 files


def _detect_tech_stack(repo_path: Path) -> dict[str, Any]:
    """Detect languages, frameworks, and build tools from config files."""
    # Count language by file extension
    lang_counter: Counter = Counter()

    try:
        result = subprocess.run(
            ["git", "ls-files"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=15,
        )
        files = result.stdout.strip().split("\n") if result.returncode == 0 else []
    except subprocess.TimeoutExpired:
        logger.warning(f"git ls-files timed out in _detect_tech_stack for {repo_path}")
        files = []

    lang_counter = _count_langs_by_ext(files)

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

    return {
        "languages": languages,
        "frameworks": frameworks,
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


# ─── Data Transformers ───

def gotchas_for_agents_md(raw_gotchas: list[dict[str, str]]) -> list[dict[str, str]]:
    """Transform parse_git_gotchas output into render_agents_md input format.

    parse_git_gotchas returns: {when, risk, because}
    render_agents_md expects: {summary, evidence}
    """
    return [
        {
            "summary": f"{g['when']} — {g['risk']}",
            "evidence": g["because"],
        }
        for g in raw_gotchas
    ]


# ─── AGENTS.md Template Rendering ───

def render_agents_md(data: dict[str, Any]) -> str:
    """Render AGENTS.md from structured data. Output MUST be ≤150 lines.

    ⚠️ INPUT IS AN AGENT-ASSEMBLED dict. This reads `modules[].path`/`.responsibility`
    and `entry_points[].path`/`.description` — the AGENTS.md authoring shape assembled
    by the skill's GENERATE step (INSTRUCTIONS.md §4.5), NOT the code-intel analysis
    shape (which uses `symbol_count`/`file_path`). Do NOT feed the analysis's
    `modules`/`entry_points` arrays here — they lack these keys and will KeyError.

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
        f"| Score: {data['score']}/10 | [Review Report](.ai-context/REVIEW-REPORT.md)"
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
    lines.append("| Why this exists, what's out of scope | [PRODUCT.md](.ai-context/PRODUCT.md) |")
    lines.append("| Architecture, conventions, invariants | [TECH.md](.ai-context/TECH.md) |")
    lines.append("| What failed, known issues, patterns | [IMPROVEMENT.md](.ai-context/IMPROVEMENT.md) |")
    lines.append("| Current priorities, active decisions | [PROJECT.md](.ai-context/PROJECT.md) |")
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
    # Python: matches "from X import" and "import X" at start of line
    "python": re.compile(r"^\s*(?:from\s+([\w.]+)\s+import|import\s+([\w.]+))"),
    # TypeScript/JS: matches import...from and require() ANYWHERE in line (uses search, not match)
    "typescript": re.compile(r"""(?:import\s+.*?\s+from\s+['"]([^'"]+)['"]|require\(\s*['"]([^'"]+)['"]\s*\))"""),
    # Go: matches quoted import paths (indented in import blocks)
    "go": re.compile(r'^\s*"([^"]+)"'),
}

# TypeScript/JS patterns need search() not match() because require() can appear mid-line
_SEARCH_LANGS = {"typescript"}


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

    # Filter to source files — use prioritized sampling for large repos
    source_extensions = {".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".rb"}
    source_files = [f for f in all_files if Path(f).suffix in source_extensions]

    # Large repo: prioritize important files (entry points, hot zones, interfaces first)
    if len(source_files) > 300:
        source_files = prioritized_file_list(repo_path, max_files=300)

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

        # Python: imports always at top (200 lines sufficient)
        # TypeScript: require() can appear anywhere — scan full file (capped at 500 lines)
        scan_limit = 500 if primary_lang in _SEARCH_LANGS else 200
        pattern = _IMPORT_PATTERNS.get(primary_lang)
        if not pattern:
            continue

        for line_num, line in enumerate(content.split("\n")[:scan_limit], 1):
            # TypeScript/JS uses search() (require can be mid-line)
            # Python/Go uses match() (imports are at line start)
            if primary_lang in _SEARCH_LANGS:
                m = pattern.search(line)
            else:
                m = pattern.match(line)

            if m:
                # Get the first non-None group
                imported = next((g for g in m.groups() if g), None)
                if imported:
                    file_imports.add(imported)
                    edges.append({
                        "from": filepath,
                        "to": imported,
                        "line": line_num,
                        "raw": line.strip(),
                    })

        if file_imports:
            module_imports[filepath] = file_imports

    # Build module-level summary (group by top-level package directory)
    dir_modules: dict[str, set] = {}
    file_to_module: dict[str, str] = {}  # filepath -> module name (for resolution)

    for filepath in source_files:
        parts = Path(filepath).parts
        if len(parts) >= 2:
            # Skip "src/" as a module name — use the next level
            module_name = parts[0] if parts[0] != "src" else (parts[1] if len(parts) > 2 else parts[0])
        else:
            module_name = Path(filepath).stem
        dir_modules.setdefault(module_name, set()).add(filepath)
        file_to_module[filepath] = module_name

    # Also index individual file stems within each module (for relative import resolution)
    # e.g., "myapp/database.py" → file_stem_to_module["database"] = "myapp"
    file_stem_to_module: dict[str, str] = {}
    for filepath in source_files:
        stem = Path(filepath).stem
        mod = file_to_module.get(filepath)
        if mod and stem != "__init__":
            file_stem_to_module[stem] = mod

    # Compute imports_from / imported_by per module
    modules: list[dict] = []
    for mod_name, mod_files in sorted(dir_modules.items()):
        imports_from: set[str] = set()
        for f in mod_files:
            for imp in module_imports.get(f, set()):
                if imp.startswith("."):
                    # Relative import: ".database" → resolve to module containing "database.py"
                    rel_name = imp.lstrip(".").split(".")[0] if imp.lstrip(".") else ""
                    if not rel_name:
                        continue  # "from . import X" = same module, skip
                    # Check if this relative import points to a file in a DIFFERENT module
                    resolved_module = file_stem_to_module.get(rel_name, mod_name)
                    if resolved_module != mod_name:
                        imports_from.add(resolved_module)
                    # Relative imports within same package are expected — not cross-module edges
                else:
                    # Absolute import: "mempalace.backends" → top-level = "mempalace"
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

    Priority (portable — no host-specific path):
    1. User-specified target path (if provided)
    2. $SWARM_WORKSPACE/.artifacts/ai-ready/ai-ready-{name} — if the env var is set
       (honored when present so a governing workspace can collect output centrally;
       it is NOT auto-set by any host today, so leave it unset for the default path)
    3. Alongside the repo itself ({repo_parent}/ai-ready-{name}/)

    Always returns an absolute path. Creates directories if needed.
    """
    if target:
        out = Path(target).resolve()
        out.mkdir(parents=True, exist_ok=True)
        return out

    repo_path = Path(repo_path).resolve()
    name = project_name or repo_path.name

    # If a governed workspace is declared via env, collect output there.
    ws = os.environ.get("SWARM_WORKSPACE", "").strip()
    if ws:
        # expanduser + resolve so a relative / ~ / whitespace value still yields an
        # absolute path (matches the target branch's "always absolute" contract).
        out = (Path(ws).expanduser() / ".artifacts" / "ai-ready" / f"ai-ready-{name}").resolve()
        out.mkdir(parents=True, exist_ok=True)
        return out

    # Fallback: alongside repo (fully portable — no SwarmAI dependency)
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


# ─── Staleness Detection (P3: Self-Maintaining) ───

def check_staleness(output_path: Path, repo_path: Path) -> dict[str, Any]:
    """Compare current repo state against stored ai-ready.json snapshot.

    Returns:
        {
            "overall": "fresh" | "stale",
            "commits_since": int,
            "stale_files": ["TECH.md", ...],  # which DDD files are outdated
            "changes": ["new module added", "config changed", ...]
        }
    """
    output_path = Path(output_path)
    repo_path = _validate_repo_path(Path(repo_path))

    # Read stored snapshot
    meta_path = output_path / ".ai-context" / "ai-ready.json"
    if not meta_path.exists():
        return {"overall": "stale", "commits_since": -1, "stale_files": ["all"], "changes": ["no ai-ready.json found"]}

    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"overall": "stale", "commits_since": -1, "stale_files": ["all"], "changes": ["corrupt ai-ready.json"]}

    # Get current repo state
    current_info = gather_repo_info(repo_path)
    stored_generated = meta.get("generated_at", "")

    # Count commits since generation
    commits_since = 0
    if stored_generated:
        # Extract date from ISO timestamp
        date_part = stored_generated.split("T")[0]
        try:
            result = subprocess.run(
                ["git", "log", f"--since={date_part}", "--oneline"],
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=15,
            )
            if result.returncode == 0:
                commits_since = len([l for l in result.stdout.strip().split("\n") if l])
        except subprocess.TimeoutExpired:
            pass

    # Detect specific changes
    changes: list[str] = []
    stale_files: list[str] = []

    # Check file count delta (new modules?)
    stored_file_count = meta.get("score", {}).get("_file_count", 0)
    current_file_count = len(current_info["file_tree"])
    if stored_file_count and abs(current_file_count - stored_file_count) > 10:
        changes.append(f"file count changed: {stored_file_count} → {current_file_count}")
        stale_files.append("TECH.md")  # Architecture changed

    # Check config files changed
    stored_frameworks = set(meta.get("score", {}).get("_frameworks", []))
    current_frameworks = set(current_info["tech_stack"].get("frameworks", []))
    if stored_frameworks != current_frameworks:
        changes.append(f"frameworks changed: {stored_frameworks} → {current_frameworks}")
        stale_files.append("TECH.md")

    # Commits since threshold
    if commits_since > 50:
        changes.append(f"{commits_since} commits since last generation")
        stale_files.extend(["TECH.md", "IMPROVEMENT.md", "PROJECT.md"])
    elif commits_since > 20:
        changes.append(f"{commits_since} commits since last generation")
        stale_files.append("PROJECT.md")

    # Deduplicate
    stale_files = sorted(set(stale_files))

    overall = "stale" if stale_files else "fresh"
    return {
        "overall": overall,
        "commits_since": commits_since,
        "stale_files": stale_files,
        "changes": changes,
    }


def generate_hook_config(ide: str = "claude-code") -> dict[str, Any]:
    """Generate IDE hook configuration for auto-staleness detection.

    Returns a config dict that can be merged into the IDE's settings.
    Claude Code: .claude/settings.json hooks.
    Kiro: .kiro/hooks/ configuration.
    """
    if ide == "claude-code":
        return {
            "hooks": {
                "FileChanged": [{
                    "pattern": [
                        "src/**/index.*", "package.json", "pyproject.toml",
                        "Makefile", "Cargo.toml", "go.mod", "**/routes/**",
                        "**/api/**", "requirements.txt", "setup.py",
                    ],
                    "command": "echo '🔄 Code structure changed — run refresh ai-ready to update context.'",
                    "onFailure": "notify",
                    "_source": "ai-ready-engine",
                }]
            }
        }
    elif ide == "kiro":
        return {
            "hooks": {
                "onFileChange": {
                    "patterns": ["src/**", "package.json", "pyproject.toml"],
                    "action": "notify",
                    "message": "🔄 Code structure changed — run 'refresh ai-ready' to update context.",
                    "_source": "ai-ready-engine",
                }
            }
        }
    return {}


# ─── Incremental Update (Competitive Feature #2) ───

def incremental_update(output_path: Path, repo_path: Path) -> dict[str, Any]:
    """Detect changed files since last generation and return what needs re-analysis.

    Uses git diff against the commit hash stored in ai-ready.json.
    Returns only the files that need re-processing — not the full repo.

    Returns:
        {
            "needs_update": bool,
            "changed_files": [str],  # relative paths of changed source files
            "new_files": [str],      # files added since last gen
            "deleted_files": [str],  # files removed since last gen
            "commits_since": int,
            "last_commit": str,      # current HEAD hash
        }
    """
    repo_path = _validate_repo_path(Path(repo_path))
    output_path = Path(output_path)

    # Read stored generation commit
    meta_path = output_path / ".ai-context" / "ai-ready.json"
    if not meta_path.exists():
        return {"needs_update": True, "changed_files": [], "new_files": [], "deleted_files": [],
                "commits_since": -1, "last_commit": "", "reason": "no ai-ready.json — full regeneration needed"}

    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"needs_update": True, "changed_files": [], "new_files": [], "deleted_files": [],
                "commits_since": -1, "last_commit": "", "reason": "corrupt ai-ready.json"}

    stored_commit = meta.get("_last_commit", "")
    if not stored_commit:
        return {"needs_update": True, "changed_files": [], "new_files": [], "deleted_files": [],
                "commits_since": -1, "last_commit": "", "reason": "no stored commit hash — full regeneration needed"}

    # Get current HEAD
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_path, capture_output=True, text=True, timeout=10,
        )
        current_head = result.stdout.strip() if result.returncode == 0 else ""
    except subprocess.TimeoutExpired:
        current_head = ""

    if not current_head:
        return {"needs_update": True, "changed_files": [], "new_files": [], "deleted_files": [],
                "commits_since": 0, "last_commit": "", "reason": "cannot determine HEAD"}

    if current_head == stored_commit:
        return {"needs_update": False, "changed_files": [], "new_files": [], "deleted_files": [],
                "commits_since": 0, "last_commit": current_head}

    # Verify stored commit exists locally (handles force-push + shallow clone)
    try:
        verify = subprocess.run(
            ["git", "cat-file", "-e", stored_commit],
            cwd=repo_path, capture_output=True, timeout=5,
        )
        if verify.returncode != 0:
            return {"needs_update": True, "changed_files": [], "new_files": [], "deleted_files": [],
                    "commits_since": -1, "last_commit": current_head,
                    "reason": "stored commit not in local history (force-push or shallow clone) — full regen needed"}
    except subprocess.TimeoutExpired:
        pass  # Continue anyway — best effort

    # Get diff between stored commit and HEAD
    source_exts = {".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".rb", ".java", ".kt", ".swift"}

    try:
        # Changed/modified files
        result = subprocess.run(
            ["git", "diff", "--name-only", "--diff-filter=M", f"{stored_commit}..HEAD"],
            cwd=repo_path, capture_output=True, text=True, timeout=30,
        )
        changed = [f for f in result.stdout.strip().split("\n") if f and Path(f).suffix in source_exts]

        # New files
        result = subprocess.run(
            ["git", "diff", "--name-only", "--diff-filter=A", f"{stored_commit}..HEAD"],
            cwd=repo_path, capture_output=True, text=True, timeout=30,
        )
        new = [f for f in result.stdout.strip().split("\n") if f and Path(f).suffix in source_exts]

        # Deleted files
        result = subprocess.run(
            ["git", "diff", "--name-only", "--diff-filter=D", f"{stored_commit}..HEAD"],
            cwd=repo_path, capture_output=True, text=True, timeout=30,
        )
        deleted = [f for f in result.stdout.strip().split("\n") if f and Path(f).suffix in source_exts]

        # Commit count
        result = subprocess.run(
            ["git", "rev-list", "--count", f"{stored_commit}..HEAD"],
            cwd=repo_path, capture_output=True, text=True, timeout=10,
        )
        commits_since = int(result.stdout.strip()) if result.returncode == 0 else 0

    except (subprocess.TimeoutExpired, ValueError):
        return {"needs_update": True, "changed_files": [], "new_files": [], "deleted_files": [],
                "commits_since": 0, "last_commit": current_head, "reason": "git diff failed"}

    needs_update = bool(changed or new or deleted)
    return {
        "needs_update": needs_update,
        "changed_files": changed,
        "new_files": new,
        "deleted_files": deleted,
        "commits_since": commits_since,
        "last_commit": current_head,
    }


# ─── Guided Learning Tour (Competitive Feature #4) ───

def generate_learning_tour(import_graph: dict[str, Any]) -> list[dict[str, str]]:
    """Generate a topologically-sorted learning order for modules.

    "Learn the codebase in the right order" — start with modules that have
    no dependencies (foundations), then modules that only depend on those,
    and so on. This gives a new developer the optimal reading path.

    Returns list of {name, path, reason, depends_on} in learning order.
    """
    modules = import_graph.get("modules", [])
    if not modules:
        return []

    # Build adjacency: module_name → set of INTERNAL dependencies only
    all_module_names = {m["name"] for m in modules}
    deps: dict[str, set] = {}
    name_to_module: dict[str, dict] = {}
    for mod in modules:
        name = mod["name"]
        name_to_module[name] = mod
        # Filter to only internal deps (external packages like numpy/fastapi don't count)
        deps[name] = set(mod.get("imports_from", [])) & all_module_names

    # Topological sort (Kahn's algorithm)
    in_degree: dict[str, int] = {name: 0 for name in deps}
    for name, mod_deps in deps.items():
        for dep in mod_deps:
            if dep in in_degree:
                in_degree[name] = in_degree.get(name, 0)  # dep adds to name's in-degree
                # Actually: name depends on dep, so dep has an edge TO name
                pass
    # Recompute: in_degree[X] = number of modules X depends on (that are in our set)
    for name, mod_deps in deps.items():
        in_degree[name] = len(mod_deps & set(deps.keys()))

    tour: list[dict[str, str]] = []
    queue = sorted([n for n, d in in_degree.items() if d == 0])
    visited: set[str] = set()

    while queue:
        current = queue.pop(0)
        if current in visited:
            continue
        visited.add(current)

        mod = name_to_module.get(current, {})
        dep_list = sorted(deps.get(current, set()) & set(deps.keys()))

        if not dep_list:
            reason = "Foundation — no internal dependencies. Start here."
        else:
            reason = f"Depends on: {', '.join(dep_list)} (learn those first)"

        tour.append({
            "name": current,
            "path": mod.get("path", f"{current}/"),
            "reason": reason,
            "depends_on": dep_list,
        })

        # Find modules whose in-degree drops to 0 after removing current
        for name, mod_deps in deps.items():
            if name in visited:
                continue
            if current in mod_deps:
                in_degree[name] -= 1
                if in_degree[name] <= 0 and name not in visited:
                    queue.append(name)
        queue.sort()

    # Add any remaining (cycles) at the end
    for name in deps:
        if name not in visited:
            mod = name_to_module.get(name, {})
            tour.append({
                "name": name,
                "path": mod.get("path", f"{name}/"),
                "reason": "Circular dependency — read after understanding the rest",
                "depends_on": sorted(deps.get(name, set()) & set(deps.keys())),
            })

    return tour


# ─── Multi-Package Support (P4) ───

def run_multi_package(
    repo_root: Path,
    output_base: Path,
    project_name: str | None = None,
) -> dict[str, Any]:
    """Run engine analysis on a (mono)repo — AUTO-DETECTS package boundaries, then
    produces per-package material + cross-package synthesis. No hand-fed package list.

    Composes detect_package_roots() (workspace-manifest boundary detection) so the
    caller passes ONE repo root, not a pre-computed member list — the detector and this runner were shipped separately and never wired; now they are.
    A single-package repo degrades to exactly one package rooted at ".".

    Skill-native + core-free by design: uses the skill's own gather_repo_info /
    extract_import_graph / parse_git_gotchas — never core.code_intel. The LLM GENERATE
    fan-out (per-package doc assembly) consumes THIS material; it is
    the INSTRUCTIONS.md orchestration layer, not this deterministic function.

    Args:
        repo_root: the (mono)repo root — package boundaries are detected from it
        output_base: base directory for all output
        project_name: optional system name (default: repo_root dir name)

    Returns:
        {
            "packages": [{name, root, path, output_path, language_mix, detected_by, stats}],
            "partition": [build_packages_partition dicts],  # the raw navigation partition
            "cross_package": {shared_deps, dep_order},
            "output_path": str,
            "project_name": str,
        }
    """
    repo_root = Path(repo_root)
    output_base = Path(output_base)
    output_base.mkdir(parents=True, exist_ok=True)

    if not project_name:
        project_name = repo_root.name or "multi-package"

    # AUTO-DETECT package boundaries (was: hand-fed repo_paths list).
    detected = detect_package_roots(repo_root)
    partition = build_packages_partition(repo_root)

    # Use the partition's DISAMBIGUATED names (path-suffixed on collision) as the
    # single source of package names — meta-review F-1: the F1 root-coverage guard
    # can prepend a root package whose name collides with a member (repo dir 'x' +
    # a member also named 'x'); raw pkg.name would then produce two identical names
    # → same pkg_output dir → the second clobbers the first. `root` is unique, so key
    # the disambiguated name by root.
    name_by_root = {p["root"]: p["name"] for p in partition}

    packages = []
    all_imports: dict[str, set] = {}  # package_name → set of external imports

    for pkg in detected:
        # pkg.root is POSIX-relative to repo_root ("." for a single-package repo).
        repo_path = repo_root if pkg.root == "." else (repo_root / pkg.root)
        if not repo_path.exists():
            continue

        pkg_name = name_by_root.get(pkg.root, pkg.name)
        # Filesystem-safe output dir: a disambiguated name can contain '/' (e.g.
        # 'sub/core') or ':' (':.') — flatten so pkg_output is a single dir segment.
        _safe_seg = pkg_name.replace("/", "__").replace(":", "__")
        pkg_output = output_base / _safe_seg

        # Run per-package analysis (each gets full budget)
        try:
            info = gather_repo_info(repo_path)
            graph = extract_import_graph(repo_path)
            gotchas = parse_git_gotchas(repo_path)
        except (ValueError, OSError):
            packages.append({"name": pkg_name, "root": pkg.root,
                             "path": str(repo_path), "error": "analysis failed"})
            continue

        # Track external imports for cross-package synthesis
        pkg_external_imports: set[str] = set()
        for edge in graph.get("edges", []):
            target = edge["to"]
            if not target.startswith("."):  # absolute import = potentially cross-package
                pkg_external_imports.add(target.split(".")[0])
        all_imports[pkg_name] = pkg_external_imports

        packages.append({
            "name": pkg_name,
            "root": pkg.root,
            "path": str(repo_path),
            "output_path": str(pkg_output),
            "language_mix": pkg.language_mix,
            "detected_by": pkg.detected_by,
            "stats": {
                "files": len(info["file_tree"]),
                "edges": graph["stats"]["edges_found"],
                "gotchas": len(gotchas),
            },
        })

    # Cross-package synthesis: find shared dependencies
    shared_deps: list[str] = []
    if len(all_imports) >= 2:
        # Find imports that appear in 2+ packages
        from collections import Counter
        import_counts: Counter = Counter()
        for pkg_imports in all_imports.values():
            for imp in pkg_imports:
                import_counts[imp] += 1
        shared_deps = [imp for imp, count in import_counts.items() if count >= 2]

    # Determine dependency order (which packages import which)
    dep_order: list[dict] = []
    pkg_names = {p["name"] for p in packages if "error" not in p}
    for pkg_name, pkg_imports in all_imports.items():
        for imp in pkg_imports:
            if imp in pkg_names and imp != pkg_name:
                dep_order.append({"from": pkg_name, "to": imp})

    return {
        "packages": packages,
        "partition": partition,
        "cross_package": {
            "shared_deps": sorted(shared_deps),
            "dep_order": dep_order,
        },
        "output_path": str(output_base),
        "project_name": project_name,
    }


# ─── ENRICH Phase (Targeted Questions) ───

def generate_enrich_questions(
    repo_info: dict[str, Any],
    gotchas: list[dict],
    import_graph: dict[str, Any],
) -> list[dict[str, str]]:
    """Generate max 5 targeted questions about what code analysis COULDN'T determine.

    The engine already knows: file structure, tech stack, git history, import graph,
    conventions from code patterns. ENRICH asks only what's INVISIBLE in code:
    business context, priorities, tribal knowledge not in git.

    Args:
        repo_info: from gather_repo_info()
        gotchas: from parse_git_gotchas()
        import_graph: from extract_import_graph()

    Returns list of {question, target_file, why} — max 5 items.
    """
    questions: list[dict[str, str]] = []

    # Q1: Purpose / audience (PRODUCT.md) — always ask unless README is very explicit
    readme = repo_info.get("readme_content", "")
    if len(readme) < 500 or "who" not in readme.lower():
        questions.append({
            "question": "Who are the primary users of this project, and what problem does it solve for them?",
            "target_file": "PRODUCT.md",
            "why": "README doesn't clearly state audience + value proposition",
        })

    # Q2: Non-goals (PRODUCT.md) — almost never in code
    questions.append({
        "question": "What is explicitly OUT OF SCOPE? What should this project NEVER do?",
        "target_file": "PRODUCT.md",
        "why": "Non-goals are almost never expressed in code — they prevent agents from building wrong things",
    })

    # Q3: Current priorities (PROJECT.md) — git shows activity, not intent
    questions.append({
        "question": "What are your top 1-3 priorities right now? What should agents focus on vs avoid?",
        "target_file": "PROJECT.md",
        "why": "Git shows what was done, not what should be done next",
    })

    # Q4: Constraints (PRODUCT.md) — compliance, SLA, business rules
    config_files = repo_info.get("config_files", {})
    has_ci = ".github/workflows/ci.yml" in config_files or ".github/workflows/ci.yaml" in config_files
    if not has_ci or len(gotchas) > 10:
        questions.append({
            "question": "Any compliance requirements, SLAs, or hard business rules an agent must respect?",
            "target_file": "PRODUCT.md",
            "why": "Regulatory/business constraints are invisible in code but critical for agent judgment",
        })

    # Q5: Tribal knowledge not in git (IMPROVEMENT.md) — only if gotchas are sparse
    if len(gotchas) < 5:
        questions.append({
            "question": "Any gotchas or 'things that burned you' that aren't captured in git history?",
            "target_file": "IMPROVEMENT.md",
            "why": f"Only found {len(gotchas)} evidence-grounded gotchas — verbal knowledge may fill gaps",
        })

    return questions[:5]


def classify_enrich_answer(answer: str) -> str:
    """Classify a user's ENRICH answer into the target DDD file.

    Simple heuristic classification — the LLM should use this as a fallback
    when the target isn't already known from the question context.

    Returns: "PRODUCT.md" | "TECH.md" | "IMPROVEMENT.md" | "PROJECT.md"
    """
    answer_lower = answer.lower()

    # PROJECT.md signals
    project_signals = ["this quarter", "priority", "blocked", "sprint", "focused on", "don't change", "migration"]
    if any(s in answer_lower for s in project_signals):
        return "PROJECT.md"

    # IMPROVEMENT.md signals
    improvement_signals = ["broke", "burned", "reverted", "don't touch", "gotcha", "incident", "failed"]
    if any(s in answer_lower for s in improvement_signals):
        return "IMPROVEMENT.md"

    # TECH.md signals
    tech_signals = ["always use", "never call", "convention", "pattern", "architecture", "we chose"]
    if any(s in answer_lower for s in tech_signals):
        return "TECH.md"

    # Default: PRODUCT.md (purpose, audience, constraints)
    return "PRODUCT.md"


# ─── Large Repo Sampling Strategy ───

def prioritized_file_list(repo_path: Path, max_files: int = 300) -> list[str]:
    """Get source files prioritized by importance, not alphabetical order.

    Priority order:
    1. Entry points (main.*, index.*, app.*, server.*)
    2. Hot zones (recently modified files — from git log)
    3. Config/interface files (base.*, types.*, config.*)
    4. Largest files by line count (more code = more important)
    5. Everything else (alphabetical within remaining budget)

    This ensures that even with a 300-file cap, the MOST IMPORTANT files
    are always included in the import graph.
    """
    repo_path = _validate_repo_path(Path(repo_path))

    # Get all source files
    try:
        result = subprocess.run(
            ["git", "ls-files"],
            cwd=repo_path, capture_output=True, text=True, timeout=15,
        )
        all_files = [f for f in result.stdout.strip().split("\n") if f] if result.returncode == 0 else []
    except subprocess.TimeoutExpired:
        all_files = []

    source_exts = {".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".rb", ".java", ".kt", ".swift"}
    source_files = [f for f in all_files if Path(f).suffix in source_exts]

    if len(source_files) <= max_files:
        return source_files  # No prioritization needed

    # Priority 1: entry points
    entry_patterns = {"main", "index", "app", "server", "cli", "__main__", "mod"}
    priority_1 = [f for f in source_files if Path(f).stem in entry_patterns]

    # Priority 2: recently modified (hot files from git log)
    try:
        result = subprocess.run(
            ["git", "log", "--pretty=format:", "--name-only", "-n", "50"],
            cwd=repo_path, capture_output=True, text=True, timeout=15,
        )
        recent_files = set()
        if result.returncode == 0:
            for line in result.stdout.strip().split("\n"):
                if line.strip() and Path(line.strip()).suffix in source_exts:
                    recent_files.add(line.strip())
        priority_2 = [f for f in source_files if f in recent_files and f not in priority_1]
    except subprocess.TimeoutExpired:
        priority_2 = []

    # Priority 3: interface/config files
    interface_patterns = {"base", "types", "config", "interface", "schema", "models", "constants"}
    priority_3 = [f for f in source_files
                  if Path(f).stem in interface_patterns
                  and f not in priority_1 and f not in priority_2]

    # Priority 4+5: everything else (already have the rest)
    seen = set(priority_1 + priority_2 + priority_3)
    remaining = [f for f in source_files if f not in seen]

    # Assemble prioritized list
    prioritized = priority_1 + priority_2 + priority_3 + remaining
    result_files = prioritized[:max_files]

    # Log what was skipped
    skipped = len(source_files) - len(result_files)
    if skipped > 0:
        logger.warning(
            f"Large repo: {len(source_files)} source files, cap={max_files}. "
            f"Skipped {skipped} files (lowest priority). "
            f"Included: {len(priority_1)} entry points, {len(priority_2)} hot files, "
            f"{len(priority_3)} interfaces, {len(result_files) - len([f for f in result_files if f in set(priority_1 + priority_2 + priority_3)])} others."
        )

    return result_files


# ─── Output Verification (VERIFY Phase) ───

def select_verification_tasks(repo_path: Path) -> list[dict[str, Any]]:
    """Select up to 3 verification tasks from git history.

    Each task has:
      - type: "fix" | "feat" | "refactor" | "general"
      - description: commit subject (what to ask the agent)
      - correct_file: the first source file in the commit NOT already claimed by
        another task (ground truth). NOTE: git `--name-only` lists files
        alphabetically, so this is the first-alphabetical UNCLAIMED source file,
        not necessarily the commit's "primary" file.
      - commit: hash (7-char) for evidence

    Selection (each picks a DISTINCT file — dedup by correct_file):
      1. Most recent fix:/hotfix:/revert: commit
      2. Most recent feat: commit
      3. Largest-diff commit (proxy for refactor)
      Fallback: fill remaining slots from any commit with an unclaimed source file

    May return FEWER than 3 tasks when the repo cannot supply 3 distinct source
    files (small/single-file repos); the caller's pass bar scales to the count.
    """
    repo_path = _validate_repo_path(Path(repo_path))

    # Get recent commits with files changed
    try:
        result = subprocess.run(
            ["git", "log", "--pretty=format:%H|%s", "--name-only", "-n", "100"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        return []

    if result.returncode != 0:
        return []

    # Parse commits
    commits = []
    current: dict | None = None
    for line in result.stdout.strip().split("\n"):
        if not line:
            continue
        if "|" in line:
            parts = line.split("|", 1)
            if len(parts[0]) == 40 and all(c in "0123456789abcdef" for c in parts[0]):
                if current:
                    commits.append(current)
                current = {"hash": parts[0], "subject": parts[1], "files": []}
                continue
        if current and line.strip():
            current["files"].append(line.strip())
    if current:
        commits.append(current)

    # Select by type
    tasks: list[dict[str, Any]] = []
    seen_files: set[str] = set()  # dedup: the 3 tasks must point at DISTINCT files
    fix_pattern = re.compile(r"^(fix|hotfix|revert|bugfix)[\s:(]", re.IGNORECASE)
    feat_pattern = re.compile(r"^feat[\s:(]", re.IGNORECASE)

    def _first_unclaimed_source(c: dict) -> str | None:
        """First source file in the commit NOT already claimed by another task.
        Dedup is by FILE, not just by commit hash: two DIFFERENT commits can both
        list foo.py first, which would make the verification ask about the same
        file twice and inflate the 2/3 pass bar (a plan-gate finding). Returns the
        first unclaimed source file, or None if the commit adds no new source file."""
        for f in c["files"]:
            if f.startswith("tests/") or not f.endswith((".py", ".ts", ".js", ".rs", ".go")):
                continue
            if f not in seen_files:
                return f
        return None

    def _try_add(c: dict, task_type: str) -> bool:
        """Append a task for commit c if it yields an unclaimed source file + the
        commit isn't already used. Records the file in seen_files. Returns success."""
        if c["hash"][:7] in [t.get("commit") for t in tasks]:
            return False
        src = _first_unclaimed_source(c)
        if not src:
            return False
        tasks.append({
            "type": task_type,
            "description": c["subject"],
            "correct_file": src,
            "commit": c["hash"][:7],
        })
        seen_files.add(src)
        return True

    # Task 1: fix commit
    for c in commits:
        if fix_pattern.match(c["subject"]) and _try_add(c, "fix"):
            break

    # Task 2: feat commit
    for c in commits:
        if feat_pattern.match(c["subject"]) and _try_add(c, "feat"):
            break

    # Task 3: largest diff (most files changed)
    for c in sorted(commits[:30], key=lambda x: len(x["files"]), reverse=True):
        if _try_add(c, "refactor"):
            break

    # Fallback: fill from remaining commits with an unclaimed source file
    if len(tasks) < 3:
        for c in commits:
            if len(tasks) >= 3:
                break
            _try_add(c, "general")

    return tasks[:3]


def build_verification_prompt(ddd_content: dict[str, str], tasks: list[dict]) -> str:
    """Build the sub-agent verification prompt.

    Args:
        ddd_content: dict mapping filename → content string
            Expected keys: "AGENTS.md", "TECH.md", "IMPROVEMENT.md"
        tasks: list from select_verification_tasks()

    Returns:
        Complete prompt for the verification sub-agent.
        The prompt contains ONLY DDD text — no source code, no file paths to read.
    """
    prompt_parts = [
        "You are verifying AI-Ready artifacts. You have ONLY the following context",
        "about a codebase — no source code access, no file reading tools.",
        "",
        "Your job: for each task below, identify the CORRECT file and function",
        "to modify. Use ONLY the information provided. If the answer is not",
        "findable from these artifacts, say 'INSUFFICIENT — need: [what is missing]'.",
        "",
        "=" * 60,
        "ARTIFACTS (this is ALL you have):",
        "=" * 60,
        "",
    ]

    for filename, content in ddd_content.items():
        prompt_parts.append(f"### {filename}")
        prompt_parts.append("```")
        prompt_parts.append(content)
        prompt_parts.append("```")
        prompt_parts.append("")

    prompt_parts.append("=" * 60)
    prompt_parts.append("TASKS (answer each):")
    prompt_parts.append("=" * 60)
    prompt_parts.append("")

    for i, task in enumerate(tasks, 1):
        prompt_parts.append(f"Task {i} ({task['type']}): {task['description']}")
        prompt_parts.append(f"  → Which file would you modify?")
        prompt_parts.append(f"  → Which function/class in that file?")
        prompt_parts.append(f"  → What's your approach (1 sentence)?")
        prompt_parts.append("")

    prompt_parts.append("=" * 60)
    prompt_parts.append("FORMAT: For each task, respond exactly:")
    prompt_parts.append("  TASK N: FILE: <path> | FUNCTION: <name> | APPROACH: <1 sentence>")
    prompt_parts.append("  or: TASK N: INSUFFICIENT — need: <what specific info is missing>")

    return "\n".join(prompt_parts)


def evaluate_verification_response(
    response: str,
    tasks: list[dict],
) -> dict[str, Any]:
    """Evaluate the sub-agent's verification response against ground truth.

    Returns:
        {
            "passed": bool (2/3 correct = pass),
            "score": "2/3",
            "results": [{"task": ..., "correct": bool, "detail": str}],
            "feedback": [str] (specific gaps if any task failed)
        }
    """
    results = []
    feedback = []
    correct_count = 0

    for i, task in enumerate(tasks, 1):
        # Look for "TASK N:" in response
        task_pattern = re.compile(
            rf"TASK\s*{i}:?\s*(.*?)(?=TASK\s*{i+1}|$)",
            re.IGNORECASE | re.DOTALL,
        )
        match = task_pattern.search(response)

        if not match:
            results.append({"task": task["description"][:50], "correct": False, "detail": "No response found"})
            feedback.append(f"Task {i}: sub-agent gave no answer — DDD output may be unclear")
            continue

        answer = match.group(1).strip()

        if "INSUFFICIENT" in answer.upper():
            results.append({"task": task["description"][:50], "correct": False, "detail": f"Insufficient: {answer}"})
            # Extract what's missing for feedback
            need_match = re.search(r"need:\s*(.+)", answer, re.IGNORECASE)
            if need_match:
                feedback.append(f"Task {i} ({task['type']}): Missing from output — {need_match.group(1).strip()}")
            else:
                feedback.append(f"Task {i} ({task['type']}): Sub-agent said INSUFFICIENT but didn't specify what's missing")
            continue

        # Check if correct file is mentioned — TOKEN-EXACT match.
        # The old `file_stem in answer` substring test false-passed: a WRONG file
        # sharing the stem (src/utils_v2.py vs src/utils.py), or the stem appearing
        # in APPROACH prose, both matched. Fix: tokenize the answer and accept a
        # token ONLY if it equals the full rel-path OR equals the bare basename.
        #
        # Gate-2 correction: the earlier draft ALSO accepted
        # `Path(tok).name == filename` (basename-OF-a-path-token) so 'src/foo.py'
        # would match correct 'backend/core/foo.py'. That is a NEW false-pass class:
        # 'api/models.py' would match correct 'db/models.py' (same basename, genuinely
        # DIFFERENT file, both common in one repo). And a probe that asks "which file
        # would you modify" SHOULD score a wrong DIRECTORY as a miss — naming
        # 'src/foo.py' when the answer is 'backend/core/foo.py' is not a pass. So the
        # basename arm is now EXACT-BARE-BASENAME only: a token that IS just 'foo.py'
        # (the agent referred to the file by name) passes; a token carrying a
        # different directory must match the full path or it fails.
        correct_file = task["correct_file"]
        filename = Path(correct_file).name  # basename, WITH extension

        # Split on whitespace, the format's '|' delimiter, AND ':' so a label-glued
        # token like 'FILE:foo.py' (no space after the colon) yields ['FILE','foo.py']
        # — else a root-level correct file would false-FAIL (Gate-2 MED). ':' never
        # appears inside a repo-relative POSIX path, so splitting on it is safe.
        raw_tokens = re.split(r"[\s|:]+", answer)
        matched = False
        for tok in raw_tokens:
            tok = tok.strip("`'\"(),.;[]<>")
            if not tok:
                continue
            if tok == correct_file or tok == filename:
                matched = True
                break

        if matched:
            correct_count += 1
            results.append({"task": task["description"][:50], "correct": True, "detail": f"Found: {filename}"})
        else:
            results.append({"task": task["description"][:50], "correct": False, "detail": f"Expected: {correct_file}, got: {answer[:80]}"})
            feedback.append(f"Task {i} ({task['type']}): Agent pointed to wrong file. Expected {correct_file}. TECH.md may need better module mapping for this area.")

    # Pass bar is PROPORTIONAL to task count, not a hardcoded >=2 (adversarial
    # review, HIGH): dedup-by-file can legitimately yield <3 tasks (a small repo
    # where every commit touches the same file), and a hardcoded `>=2` made a
    # PERFECT agent fail 1/1. Bar = ceil(2/3 of tasks), min 1 — a repo that only
    # affords 1 verification task passes on that 1, matching the intended "~2/3
    # correct" threshold at every task count. Zero tasks → not passed (nothing proven).
    n = len(tasks)
    pass_threshold = max(1, (2 * n + 2) // 3) if n else 1  # ceil(2n/3), floor 1
    return {
        "passed": n > 0 and correct_count >= pass_threshold,
        "score": f"{correct_count}/{n}",
        "results": results,
        "feedback": feedback,
    }


# ─── M5 Multi-package: deterministic package-boundary detection ───
#
# Navigational, NOT a correctness fix. Symbol ids are already path-qualified
# (parser.py:_qualify uses rel_path=relative_to(repo_root)) and route.id hashes
# file_path, so a monorepo does NOT collide — verified by Gate-0.
# Wired end-to-end: run_multi_package(repo_root) AUTO-DETECTS via
# detect_package_roots (no hand-fed list); the code-intel analysis
# (core/code_intel/analysis.py, structure layer) reports packages[] from it; the
# INSTRUCTIONS.md §4.9 monorepo fan-out orchestrates per-package GENERATE.
#
# Still skill-layer + core-free: detection uses only stdlib + yaml/tomllib.
# Deferred: per-package full v3 (domains/flows/steps) generation is the LLM fan-out
# layer (§4.9 orchestration), not a deterministic helper.

from dataclasses import dataclass, field


@dataclass(frozen=True)
class PackageRoot:
    """A detected package boundary within a (mono)repo. `root` is POSIX-relative
    to repo_root ('.' for the repo itself). `language_mix` is {lang: file_count}
    from _LANG_EXTENSIONS, excluding _IGNORE_DIRS. `detected_by` names the
    manifest signal(s) that surfaced it (sorted, comma-joined)."""
    name: str
    root: str
    language_mix: dict = field(default_factory=dict)
    detected_by: str = ""


def _validate_dir_path(p: Path) -> Path:
    """Lighter sibling of _validate_repo_path for boundary detection: resolves
    symlinks (traversal safety) + requires exists/is-dir, but does NOT require
    .git. Boundary detection operates on manifests/filesystem, so it must work on
    a package sub-root or a non-git tarball — requiring git would break the real
    fan-out use case."""
    p = Path(p).resolve()
    if not p.exists():
        raise ValueError(f"Path does not exist: {p}")
    if not p.is_dir():
        raise ValueError(f"Path is not a directory: {p}")
    return p


def _rel_posix(path: Path, repo_root: Path) -> str:
    """Relative POSIX path of `path` under `repo_root`; '.' for repo_root itself."""
    rel = path.resolve().relative_to(repo_root.resolve())
    s = rel.as_posix()
    return s if s else "."


def _expand_globs(repo_root: Path, patterns) -> list[Path]:
    """Expand a list of workspace glob patterns ('packages/*', 'libs/core') to
    REAL directories on disk under repo_root. Non-dir / non-existent matches are
    dropped (a glob must never leak a literal pattern or a file — Gate-1 B3).
    Fail-soft: a bad pattern yields nothing, never raises."""
    dirs: list[Path] = []
    for pat in patterns or []:
        if not isinstance(pat, str) or not pat.strip():
            continue
        pat = pat.strip().rstrip("/")
        try:
            # Path.glob handles '*' / '**' relative to repo_root. A literal
            # (non-glob) pattern falls through to a direct existence check.
            if any(ch in pat for ch in "*?[]"):
                matches = list(repo_root.glob(pat))
            else:
                matches = [repo_root / pat]
        except (ValueError, OSError):
            continue
        for m in matches:
            try:
                if m.is_dir():
                    dirs.append(m)
            except OSError:
                continue
    return dirs


def _read_json_soft(path: Path):
    """Parse JSON, returning None on missing/malformed (never raises) — Gate-1 B2."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return None


# ── per-ecosystem manifest readers ──
# Contract (all): take repo_root, return list[Path] of REAL package dirs, or []
# if the manifest is absent/malformed/empty. NEVER raise.

def _npm_workspaces(repo_root: Path) -> list[Path]:
    data = _read_json_soft(repo_root / "package.json")
    if not isinstance(data, dict):
        return []
    ws = data.get("workspaces")
    patterns: list = []
    if isinstance(ws, list):
        patterns = ws
    elif isinstance(ws, dict) and isinstance(ws.get("packages"), list):
        patterns = ws["packages"]
    return _expand_globs(repo_root, patterns)


def _pnpm_workspaces(repo_root: Path) -> list[Path]:
    f = repo_root / "pnpm-workspace.yaml"
    if not f.is_file():
        return []
    try:
        import yaml
        data = yaml.safe_load(f.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(data, dict):
        return []
    pkgs = data.get("packages")
    return _expand_globs(repo_root, pkgs) if isinstance(pkgs, list) else []


def _lerna(repo_root: Path) -> list[Path]:
    data = _read_json_soft(repo_root / "lerna.json")
    if not isinstance(data, dict):
        return []
    pkgs = data.get("packages")
    return _expand_globs(repo_root, pkgs) if isinstance(pkgs, list) else []


def _cargo_workspace(repo_root: Path) -> list[Path]:
    f = repo_root / "Cargo.toml"
    if not f.is_file():
        return []
    try:
        import tomllib
        data = tomllib.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return []
    ws = data.get("workspace") if isinstance(data, dict) else None
    members = ws.get("members") if isinstance(ws, dict) else None
    return _expand_globs(repo_root, members) if isinstance(members, list) else []


def _go_modules(repo_root: Path) -> list[Path]:
    """Every go.mod BELOW the root (a nested go.mod = an independent module).
    The root go.mod itself is handled by the [root] fallback, not here."""
    dirs: list[Path] = []
    try:
        for gomod in repo_root.rglob("go.mod"):
            parent = gomod.parent
            if parent.resolve() == repo_root.resolve():
                continue  # root module → fallback covers it
            if any(_is_ignored_dir(p) for p in parent.relative_to(repo_root).parts):
                continue
            dirs.append(parent)
    except OSError:
        return []
    return dirs


def _python_packages(repo_root: Path) -> list[Path]:
    """Multiple pyproject.toml / setup.py in SUBDIRS = a python multi-package
    layout. Root-level manifest → [root] fallback, not here."""
    dirs: list[Path] = []
    seen: set = set()
    try:
        for marker in ("pyproject.toml", "setup.py"):
            for mf in repo_root.rglob(marker):
                parent = mf.parent
                if parent.resolve() == repo_root.resolve():
                    continue
                if any(_is_ignored_dir(p) for p in parent.relative_to(repo_root).parts):
                    continue
                rp = parent.resolve()
                if rp not in seen:
                    seen.add(rp)
                    dirs.append(parent)
    except OSError:
        return []
    return dirs


# nx.json / turbo.json are monorepo *signals* but carry no portable member list
# (nx infers projects from project.json files; turbo from the package manager's
# workspaces). We surface them as a signal that boosts confidence the repo IS a
# monorepo, but rely on the npm/pnpm reader for the actual member dirs. Presence
# alone never fabricates a member — it just tags detected_by.
def _monorepo_signal(repo_root: Path) -> list[str]:
    sigs = []
    for name in ("nx.json", "turbo.json"):
        if (repo_root / name).is_file():
            sigs.append(name.split(".")[0])
    return sigs


_PACKAGE_READERS = {
    "npm": _npm_workspaces,
    "pnpm": _pnpm_workspaces,
    "lerna": _lerna,
    "cargo": _cargo_workspace,
    "go": _go_modules,
    "python": _python_packages,
}


def _language_mix(abs_root: Path) -> dict:
    """{lang: file_count} under abs_root via the shared counter, excluding
    _IGNORE_DIRS. Uses git ls-files scoped to abs_root when possible (respects
    .gitignore), else rglob. Paths are relative to abs_root throughout."""
    files: list[str] = []
    try:
        result = subprocess.run(
            ["git", "ls-files"], cwd=abs_root,
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0 and result.stdout.strip():
            files = [f for f in result.stdout.strip().split("\n") if f]
    except (subprocess.TimeoutExpired, OSError):
        files = []
    if not files:
        try:
            for p in abs_root.rglob("*"):
                if not p.is_file():
                    continue
                rel = p.relative_to(abs_root)
                if any(_is_ignored_dir(part) for part in rel.parts):
                    continue
                files.append(str(rel))
        except OSError:
            pass
    else:
        # git ls-files does not descend into ignored dirs, but be defensive
        files = [f for f in files
                 if not any(_is_ignored_dir(part) for part in Path(f).parts)]
    return dict(_count_langs_by_ext(files))


def detect_package_roots(repo_root) -> list[PackageRoot]:
    """Deterministically detect package boundaries in a (mono)repo from workspace
    manifests. Returns >=1 PackageRoot, ALWAYS (falls back to [root] when no
    multi-package signal is found). Dedups by resolved path so a dir surfaced by
    two manifests (e.g. npm + lerna both globbing packages/*) appears once, with
    its detected_by merged. Fail-soft throughout — a malformed manifest degrades
    to fewer signals, never an exception."""
    repo_root = _validate_dir_path(Path(repo_root))

    # resolved-path -> {"path": Path, "by": set[str]}
    found: dict = {}
    for label, reader in _PACKAGE_READERS.items():
        try:
            dirs = reader(repo_root)
        except Exception:
            dirs = []
        for d in dirs:
            try:
                key = d.resolve()
            except OSError:
                continue
            if key == repo_root.resolve():
                continue  # a member that IS the root → fallback territory
            entry = found.setdefault(key, {"path": d, "by": set()})
            entry["by"].add(label)

    signals = _monorepo_signal(repo_root)

    if not found:
        # Single-package repo: exactly [root]. detected_by records any monorepo
        # signal seen (nx/turbo present but no members) or "root".
        by = ",".join(sorted(signals)) if signals else "root"
        return [PackageRoot(
            name=repo_root.name,
            root=".",
            language_mix=_language_mix(repo_root),
            detected_by=by,
        )]

    roots: list[PackageRoot] = []
    for key in sorted(found, key=lambda k: str(k)):
        entry = found[key]
        d = entry["path"]
        by = sorted(entry["by"]) + signals
        roots.append(PackageRoot(
            name=d.name,
            root=_rel_posix(d, repo_root),
            language_mix=_language_mix(d),
            detected_by=",".join(sorted(set(by))),
        ))

    # Root-coverage guard (Gate-2 F1): a lone nested manifest (e.g. root app.py +
    # tools/gen/pyproject.toml) surfaces the nested dir as the ONLY member, silently
    # dropping the root application from the partition — the repo gets mislabeled a
    # monorepo whose main code vanishes. If the root carries substantive source that
    # NO detected member contains, include the root itself as a package so nothing is
    # lost. Language-mix comparison uses the root's OWN files vs the union of members'.
    root_mix = _language_mix(repo_root)
    member_total = sum(sum(r.language_mix.values()) for r in roots)
    root_total = sum(root_mix.values())
    if root_total > member_total:
        # The root has source files beyond what the members account for → represent it.
        roots.insert(0, PackageRoot(
            name=repo_root.name,
            root=".",
            language_mix=root_mix,
            detected_by=(",".join(sorted(signals)) if signals else "root"),
        ))
    return roots


def build_packages_partition(repo_root) -> list[dict]:
    """Wrap detect_package_roots() into navigation-metadata dicts for a
    `packages[]` partition, reported by the code-intel analysis's structure layer
    (core/code_intel/analysis.py) and used by the monorepo fan-out. Names are made unique
    (path-suffixed on collision) so two packages both named 'core' stay distinguishable."""
    roots = detect_package_roots(repo_root)
    # Two-pass (Gate-2 F2): disambiguate ALL colliding names symmetrically, not
    # just the 2nd+ occurrence — otherwise the first 'core' keeps the bare name
    # and is indistinguishable from a truly root-level 'core'. Pass 1 counts
    # names; pass 2 path-qualifies every name that collides.
    from collections import Counter as _Counter
    name_counts = _Counter(r.name for r in roots)
    out: list[dict] = []
    for r in roots:
        name = r.name
        if name_counts[name] > 1:
            parent = Path(r.root).parent.name
            name = f"{parent}/{r.name}" if parent and parent != "." else f"{r.name}:{r.root}"
        out.append({
            "name": name,
            "root": r.root,
            "language_mix": r.language_mix,
            "detected_by": r.detected_by,
        })
    return out


# ── Gap 2: Business-Rules Extraction scoring dimension ──────────
#
# The 10th scoring dimension. It certifies the newest engine capability
# (domain_rules / business-rules-extraction) MECHANICALLY — not by an LLM eyeball,
# which is the exact "capability grew but certification lagged" gap this closes.
#
# Score = coverage × traceability_factor × 10, where
#   coverage           = domains-that-produced-rules / total domains detected
#   traceability_factor = fraction of domains whose spec passes traceability
#                         (behavioral §1-8 references the rule, not only the §9
#                          matrix); 1.0 when no specs are passed (coverage-only).
#
# HONESTY RULE: a repo with NO domain_rules layer (non-legacy / non-SQL — nothing
# to extract) is scored N/A (applicable=False, score=None) and MUST be excluded
# from the overall average by the caller. Scoring it 0 would penalize a repo for
# not having business rules it was never supposed to have.

# Matrix heading that opens the §9 traceability appendix — everything AT/AFTER it
# is EXCLUDED from behavioral-coverage (a rule that appears ONLY in the matrix that
# tracks it is NOT covered). Faithful to core.code_intel.domain_rules._MATRIX_HEADING;
# re-inlined here because ai_ready_helpers.py is the PORTABLE toolkit script and must
# stay import-free of core.* (runs on foreign hosts). Keep the two in sync.
_BR_MATRIX_HEADING = re.compile(
    r"^\s{0,3}#{1,6}\s*(?:9\b|traceability\s+matrix)",
    re.IGNORECASE | re.MULTILINE,
)


def _br_spec_covers_all(spec_markdown: str, rule_ids: list[str]) -> bool:
    """True iff EVERY rule_id is referenced in the behavioral chapters (before the
    §9 matrix). Fail-closed: no matrix heading → cannot prove separation → False.
    Mirrors core.code_intel.domain_rules.verify_traceability (inlined for portability)."""
    if not rule_ids:
        # A domain with no rule_ids has nothing to trace — vacuously covered.
        return True
    m = _BR_MATRIX_HEADING.search(spec_markdown or "")
    if not m:
        return False  # no matrix section → fail-closed (same as verify_traceability)
    behavioral = spec_markdown[:m.start()]
    for rid in rule_ids:
        # Word-boundary so BR-1 is not "covered" by BR-12 (hyphen is a boundary here).
        if not re.search(r"(?<![\w-])" + re.escape(rid) + r"(?![\w-])", behavioral):
            return False
    return True


def compute_business_rules_dimension(doc: dict, specs: dict | None = None) -> dict:
    """Score the Business-Rules Extraction dimension from ``doc['domain_rules']``.

    Args:
        doc: the code-intel analysis result (``"$SWARM_BACKEND_BIN" py -m core.code_intel.analysis
             <project> --layers domain_rules``). Reads ``doc['domain_rules']`` =
             ``{"domains":[{domain_id, rule_count, ...}], "rules":[{rule_id, domain_id}]}``
             (the shape emitted by core.code_intel.domain_rules.compute_domain_rules).
        specs: OPTIONAL ``{domain_id: spec_markdown}``. When provided, traceability
             is measured per domain (does the spec cite each rule in its behavioral
             body, not only the §9 matrix). When absent, the score is coverage-only
             (traceability_pass=None) — the doc itself carries no spec text.

    Returns a dict:
        {"applicable": bool, "score": int|None, "coverage": float|None,
         "traceability_pass": float|None, "detail": str}

    ``applicable=False`` (score=None) when there is no domain_rules layer or zero
    domains — the caller MUST exclude such an N/A from the overall average.
    """
    dr = doc.get("domain_rules") if isinstance(doc, dict) else None
    domains = (dr or {}).get("domains") or []
    rules = (dr or {}).get("rules") or []

    if not dr or not domains:
        return {
            "applicable": False, "score": None, "coverage": None,
            "traceability_pass": None,
            "detail": "N/A — no domain_rules layer (non-legacy/non-SQL repo has no "
                      "business rules to extract; excluded from overall, not penalized)",
        }

    # Coverage: how many detected domains actually produced ≥1 business rule.
    domains_with_rules = sum(1 for d in domains if (d.get("rule_count") or 0) > 0)
    coverage = domains_with_rules / len(domains)

    # Traceability (optional): fraction of RULE-BEARING domains whose spec cites
    # every one of its rule_ids in the behavioral chapters. Only rule-bearing
    # domains are eligible — a domain with 0 rules has nothing to trace and must
    # not dilute the ratio in either direction.
    traceability_pass = None
    if specs:
        # Group rule_ids by domain once (rules[] is the authoritative id list).
        ids_by_domain: dict = {}
        for r in rules:
            rid, did = r.get("rule_id"), r.get("domain_id")
            if rid and did is not None:
                ids_by_domain.setdefault(did, []).append(rid)
        eligible = [d for d in domains if (d.get("rule_count") or 0) > 0]
        if eligible:
            passed = 0
            for d in eligible:
                did = d.get("domain_id")
                spec_md = specs.get(did)
                ids = ids_by_domain.get(did, [])
                # A rule-bearing domain (rule_count>0) whose rule_ids cannot be
                # resolved from rules[] — empty rules[], a None/mismatched
                # domain_id — is NOT provably traceable and must FAIL closed. We
                # must not hand an empty id list to _br_spec_covers_all (it treats
                # "no ids" as vacuously covered, which would inflate the score for
                # a domain that claims a rule but cites none). coverage counts this
                # domain (rule_count>0); traceability must not silently pass it.
                if not ids:
                    continue  # fail-closed: rule_count claims rules, rules[] doesn't back it
                # A rule-bearing domain with no spec supplied cannot be proven
                # traceable → counts as a fail (fail-closed, not skipped).
                if spec_md and _br_spec_covers_all(spec_md, ids):
                    passed += 1
            traceability_pass = passed / len(eligible)

    factor = 1.0 if traceability_pass is None else traceability_pass
    score = round(coverage * factor * 10)

    detail = (f"{domains_with_rules}/{len(domains)} domains produced business rules "
              f"(coverage {coverage:.0%})")
    if traceability_pass is not None:
        detail += f"; traceability {traceability_pass:.0%} of rule-bearing domains cite rules in behavioral chapters"
    else:
        detail += "; traceability not measured (no specs supplied — coverage-only score)"

    return {
        "applicable": True, "score": score, "coverage": round(coverage, 4),
        "traceability_pass": (round(traceability_pass, 4) if traceability_pass is not None else None),
        "detail": detail,
    }
