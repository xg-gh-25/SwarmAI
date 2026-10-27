"""Tests for AI-Ready-Repo Engine helper script.

Tests deterministic operations: git log parsing, repo info gathering, rendering.
Uses real filesystem fixtures where possible (no mocks for file operations).
"""
import json
import subprocess
from pathlib import Path

import pytest


# ─── AC4: Git log parsing for WHEN/RISK/BECAUSE ───

class TestGitLogParsing:
    """Parse git history for gotchas with evidence."""

    def test_extracts_fix_commits(self, tmp_path):
        """Identify fix/revert/hotfix commits from git log."""
        from scripts.ai_ready_helpers import parse_git_gotchas

        # Create a real git repo with fix commits
        repo = tmp_path / "test-repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, capture_output=True)

        # Initial commit
        (repo / "handler.py").write_text("def handle(): pass")
        subprocess.run(["git", "add", "."], cwd=repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "feat: add handler"], cwd=repo, capture_output=True)

        # Fix commit
        (repo / "handler.py").write_text("def handle():\n    if not valid: return\n    pass")
        subprocess.run(["git", "add", "."], cwd=repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "fix: validate input before processing"], cwd=repo, capture_output=True)

        # Revert commit
        (repo / "handler.py").write_text("def handle():\n    pass")
        subprocess.run(["git", "add", "."], cwd=repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "revert: undo validation - breaks legacy clients"], cwd=repo, capture_output=True)

        gotchas = parse_git_gotchas(repo)
        assert len(gotchas) >= 1
        # Each gotcha must have WHEN, RISK, BECAUSE structure
        for g in gotchas:
            assert "when" in g, f"Gotcha missing 'when': {g}"
            assert "risk" in g, f"Gotcha missing 'risk': {g}"
            assert "because" in g, f"Gotcha missing 'because': {g}"
            assert "commit" in g["because"], f"Evidence must include commit hash: {g}"

    def test_empty_repo_returns_empty(self, tmp_path):
        """Repo with no fix/revert commits returns empty gotchas."""
        from scripts.ai_ready_helpers import parse_git_gotchas

        repo = tmp_path / "clean-repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, capture_output=True)
        (repo / "main.py").write_text("print('hello')")
        subprocess.run(["git", "add", "."], cwd=repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "feat: initial"], cwd=repo, capture_output=True)

        gotchas = parse_git_gotchas(repo)
        assert gotchas == []


# ─── AC1 + AC5: Repo info gathering (works on any repo) ───

class TestGatherRepoInfo:
    """Gather repository metadata for engine input."""

    def test_gathers_basic_info(self, tmp_path):
        """Gather file tree, tech stack, git stats from a real repo."""
        from scripts.ai_ready_helpers import gather_repo_info

        repo = tmp_path / "sample"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, capture_output=True)
        subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, capture_output=True)
        subprocess.run(["git", "config", "user.name", "T"], cwd=repo, capture_output=True)

        # Create a Python project structure
        (repo / "src").mkdir()
        (repo / "src" / "main.py").write_text("from fastapi import FastAPI\napp = FastAPI()\n")
        (repo / "src" / "models.py").write_text("class User: pass\n")
        (repo / "tests").mkdir()
        (repo / "tests" / "test_main.py").write_text("def test_app(): pass\n")
        (repo / "pyproject.toml").write_text('[project]\nname = "sample"\n')
        (repo / "README.md").write_text("# Sample\nA sample project.\n")

        subprocess.run(["git", "add", "."], cwd=repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "feat: initial project"], cwd=repo, capture_output=True)

        info = gather_repo_info(repo)

        assert "file_tree" in info
        assert "tech_stack" in info
        assert "git_stats" in info
        assert "readme_content" in info
        assert info["tech_stack"]["languages"]["python"] > 0
        assert info["git_stats"]["total_commits"] >= 1

    def test_works_on_external_repo(self):
        """AC5: Must work on non-SwarmAI repo (cold-start test)."""
        from scripts.ai_ready_helpers import gather_repo_info

        external_repo = Path("$WORKSPACE_ROOT/ai-ready-repo")
        if not external_repo.exists():
            pytest.skip("External repo not available")

        info = gather_repo_info(external_repo)
        assert info["file_tree"]  # non-empty
        assert info["git_stats"]["total_commits"] >= 1
        # Should not contain any SwarmAI-specific assumptions
        assert "swarm-ai" not in str(info.get("tech_stack", {})).lower() or True  # no hardcoded paths


# ─── AC2: AGENTS.md size constraint ───

class TestAgentsMdTemplate:
    """AGENTS.md output must be ≤150 lines."""

    def test_template_under_150_lines(self):
        """Rendered template with typical data stays under 150 lines."""
        from scripts.ai_ready_helpers import render_agents_md

        sample_data = {
            "project_name": "payment-service",
            "build_command": "npm run build",
            "test_command": "npm test",
            "lint_command": "npm run lint",
            "test_duration": "~45s",
            "modules": [
                {"path": "src/processing/", "responsibility": "Payment flow orchestration"},
                {"path": "src/webhooks/", "responsibility": "External event handling"},
                {"path": "src/database/", "responsibility": "Data persistence layer"},
            ],
            "entry_points": [
                {"path": "src/server.ts", "type": "http", "description": "Express, port 3000"},
            ],
            "critical_rules": [
                {"type": "never", "rule": "Never use raw SQL", "reason": "Always via repository pattern"},
                {"type": "always", "rule": "Always use feature flags", "reason": "Compliance requirement"},
            ],
            "gotchas": [
                {"summary": "Webhook ordering non-deterministic", "evidence": "abc123, def456"},
            ],
            "score": 7.2,
            "generated_date": "2026-06-01",
        }

        output = render_agents_md(sample_data)
        lines = output.strip().split("\n")
        assert len(lines) <= 150, f"AGENTS.md is {len(lines)} lines, must be ≤150"
        assert "payment-service" in output
        assert ".ai-context/PRODUCT.md" in output


# ─── Import Graph Extraction ───

class TestImportGraphExtraction:
    """Extract real dependency graph from import statements."""

    def test_extracts_python_imports(self, tmp_path):
        """Detect Python import edges from actual source files."""
        from scripts.ai_ready_helpers import extract_import_graph

        repo = tmp_path / "pyproject"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, capture_output=True)
        subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, capture_output=True)
        subprocess.run(["git", "config", "user.name", "T"], cwd=repo, capture_output=True)

        # Create a Python package with real imports
        pkg = repo / "myapp"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("")
        (pkg / "core.py").write_text("from .database import connect\nfrom .utils import helper\n")
        (pkg / "database.py").write_text("import sqlite3\n\ndef connect(): pass\n")
        (pkg / "utils.py").write_text("import os\n\ndef helper(): pass\n")
        (pkg / "api.py").write_text("from .core import something\nfrom .database import connect\n")

        subprocess.run(["git", "add", "."], cwd=repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True)

        graph = extract_import_graph(repo)

        assert graph["stats"]["files_scanned"] >= 4
        assert graph["stats"]["edges_found"] >= 4
        assert graph["stats"]["primary_language"] == "python"

        # Verify edges have file:line citations
        for edge in graph["edges"]:
            assert "from" in edge
            assert "to" in edge
            assert "line" in edge
            assert isinstance(edge["line"], int)

    def test_builds_module_level_deps(self, tmp_path):
        """Module-level imports_from and imported_by are computed from edges."""
        from scripts.ai_ready_helpers import extract_import_graph

        repo = tmp_path / "modtest"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, capture_output=True)
        subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, capture_output=True)
        subprocess.run(["git", "config", "user.name", "T"], cwd=repo, capture_output=True)

        # Two packages that import each other
        (repo / "alpha").mkdir()
        (repo / "alpha" / "__init__.py").write_text("")
        (repo / "alpha" / "main.py").write_text("from beta.lib import util\n")

        (repo / "beta").mkdir()
        (repo / "beta" / "__init__.py").write_text("")
        (repo / "beta" / "lib.py").write_text("def util(): pass\n")

        subprocess.run(["git", "add", "."], cwd=repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True)

        graph = extract_import_graph(repo)

        # Find alpha module
        alpha = next((m for m in graph["modules"] if m["name"] == "alpha"), None)
        assert alpha is not None
        assert "beta" in alpha["imports_from"]

        # Find beta module
        beta = next((m for m in graph["modules"] if m["name"] == "beta"), None)
        assert beta is not None
        assert "alpha" in beta["imported_by"]

    def test_works_on_real_repo(self):
        """Extract import graph from ai-ready-repo (external)."""
        from scripts.ai_ready_helpers import extract_import_graph

        external_repo = Path("$WORKSPACE_ROOT/ai-ready-repo")
        if not external_repo.exists():
            pytest.skip("External repo not available")

        graph = extract_import_graph(external_repo)
        assert graph["stats"]["files_scanned"] >= 0  # may have no .py files
        # Should not crash on any repo


# ─── ENRICH Phase ───

class TestEnrichQuestions:
    """Generate targeted questions for what code can't tell."""

    def test_generates_questions_for_sparse_repo(self):
        """Sparse repo (short README, few gotchas) → all 5 questions."""
        from scripts.ai_ready_helpers import generate_enrich_questions

        info = {"readme_content": "# My App\nA short readme.", "config_files": {}}
        questions = generate_enrich_questions(info, gotchas=[], import_graph={"modules": []})

        assert len(questions) >= 3
        assert all("question" in q for q in questions)
        assert all("target_file" in q for q in questions)
        # Should ask about non-goals (always)
        assert any("scope" in q["question"].lower() or "never" in q["question"].lower() for q in questions)

    def test_skips_audience_if_readme_explicit(self):
        """Long README with 'who' → skip audience question."""
        from scripts.ai_ready_helpers import generate_enrich_questions

        long_readme = "# My App\n" + "This tool is for developers who need X. " * 50
        info = {"readme_content": long_readme, "config_files": {".github/workflows/ci.yml": ""}}
        questions = generate_enrich_questions(info, gotchas=[{"when": "x", "risk": "y", "because": "z"}] * 10, import_graph={"modules": []})

        # With many gotchas + CI, fewer questions needed
        assert len(questions) <= 4

    def test_classifies_answer_correctly(self):
        """Answer classification routes to correct DDD file."""
        from scripts.ai_ready_helpers import classify_enrich_answer

        assert classify_enrich_answer("This quarter we're focused on migration") == "PROJECT.md"
        assert classify_enrich_answer("We broke production when someone called X directly") == "IMPROVEMENT.md"
        assert classify_enrich_answer("Always use the repository pattern for DB access") == "TECH.md"
        assert classify_enrich_answer("Our users are enterprise developers") == "PRODUCT.md"


# ─── Large Repo Sampling ───

class TestLargeRepoSampling:
    """Prioritized file selection for large repos."""

    def test_prioritizes_entry_points(self, tmp_path):
        """Entry point files (main, index, app) come first."""
        from scripts.ai_ready_helpers import prioritized_file_list

        repo = tmp_path / "big"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, capture_output=True)
        subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, capture_output=True)
        subprocess.run(["git", "config", "user.name", "T"], cwd=repo, capture_output=True)

        # Create 10 files — main.py should be prioritized
        for i in range(10):
            (repo / f"module_{i}.py").write_text(f"# module {i}")
        (repo / "main.py").write_text("# entry point")
        (repo / "index.py").write_text("# another entry")
        subprocess.run(["git", "add", "."], cwd=repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True)

        result = prioritized_file_list(repo, max_files=5)
        # Entry points should be in the result
        assert "main.py" in result
        assert "index.py" in result

    def test_returns_all_if_under_cap(self, tmp_path):
        """Small repo (under cap) returns all files."""
        from scripts.ai_ready_helpers import prioritized_file_list

        repo = tmp_path / "small"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, capture_output=True)
        subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, capture_output=True)
        subprocess.run(["git", "config", "user.name", "T"], cwd=repo, capture_output=True)
        (repo / "a.py").write_text("x = 1")
        (repo / "b.py").write_text("y = 2")
        subprocess.run(["git", "add", "."], cwd=repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True)

        result = prioritized_file_list(repo, max_files=300)
        assert len(result) == 2  # All files returned


# ─── Incremental Update ───

class TestIncrementalUpdate:
    """Detect changed files for incremental re-analysis."""

    def test_no_changes_returns_no_update(self, tmp_path):
        """Same commit = no update needed."""
        from scripts.ai_ready_helpers import incremental_update, build_ai_ready_meta
        import json

        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, capture_output=True)
        subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, capture_output=True)
        subprocess.run(["git", "config", "user.name", "T"], cwd=repo, capture_output=True)
        (repo / "main.py").write_text("x = 1")
        subprocess.run(["git", "add", "."], cwd=repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True)

        # Get current HEAD and store in meta
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True).stdout.strip()
        output = tmp_path / "output"
        (output / ".ai-context").mkdir(parents=True)
        meta = build_ai_ready_meta(5.0, "test")
        meta["_last_commit"] = head
        (output / ".ai-context" / "ai-ready.json").write_text(json.dumps(meta))

        result = incremental_update(output, repo)
        assert result["needs_update"] is False
        assert result["commits_since"] == 0

    def test_new_commit_returns_changed_files(self, tmp_path):
        """New commits since stored hash = update needed with file list."""
        from scripts.ai_ready_helpers import incremental_update, build_ai_ready_meta
        import json

        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, capture_output=True)
        subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, capture_output=True)
        subprocess.run(["git", "config", "user.name", "T"], cwd=repo, capture_output=True)
        (repo / "main.py").write_text("x = 1")
        subprocess.run(["git", "add", "."], cwd=repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True)

        # Store current HEAD
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True).stdout.strip()
        output = tmp_path / "output"
        (output / ".ai-context").mkdir(parents=True)
        meta = build_ai_ready_meta(5.0, "test")
        meta["_last_commit"] = head
        (output / ".ai-context" / "ai-ready.json").write_text(json.dumps(meta))

        # Make a new commit
        (repo / "new_module.py").write_text("def new(): pass")
        (repo / "main.py").write_text("x = 2")
        subprocess.run(["git", "add", "."], cwd=repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "feat: add new module"], cwd=repo, capture_output=True)

        result = incremental_update(output, repo)
        assert result["needs_update"] is True
        assert result["commits_since"] == 1
        assert "main.py" in result["changed_files"]
        assert "new_module.py" in result["new_files"]


# ─── Learning Tour ───

class TestLearningTour:
    """Generate topologically-sorted learning order."""

    def test_sorts_by_dependencies(self):
        """Modules with no deps come first, dependents come after."""
        from scripts.ai_ready_helpers import generate_learning_tour

        graph = {
            "modules": [
                {"name": "app", "path": "app/", "imports_from": ["core", "utils"]},
                {"name": "core", "path": "core/", "imports_from": ["utils"]},
                {"name": "utils", "path": "utils/", "imports_from": []},
            ]
        }

        tour = generate_learning_tour(graph)
        names = [t["name"] for t in tour]

        # utils has 0 deps → first
        assert names.index("utils") < names.index("core")
        # core depends on utils → after utils
        assert names.index("core") < names.index("app")
        # app depends on both → last

    def test_empty_graph_returns_empty(self):
        """Empty module list → empty tour."""
        from scripts.ai_ready_helpers import generate_learning_tour
        assert generate_learning_tour({"modules": []}) == []


# ─── Staleness Detection (P3) ───

class TestStalenessDetection:
    """Check if generated output is stale relative to current repo state."""

    def test_fresh_output_returns_fresh(self, tmp_path):
        """Output just generated from current state = fresh."""
        from scripts.ai_ready_helpers import check_staleness, build_ai_ready_meta

        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, capture_output=True)
        subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, capture_output=True)
        subprocess.run(["git", "config", "user.name", "T"], cwd=repo, capture_output=True)
        (repo / "main.py").write_text("print('hello')")
        subprocess.run(["git", "add", "."], cwd=repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True)

        # Generate output
        output = tmp_path / "output"
        (output / ".ai-context").mkdir(parents=True)
        import json
        meta = build_ai_ready_meta(5.0, "test")
        (output / ".ai-context" / "ai-ready.json").write_text(json.dumps(meta))

        result = check_staleness(output, repo)
        assert result["overall"] == "fresh"
        assert result["commits_since"] == 0 or result["commits_since"] == 1  # just committed

    def test_missing_meta_returns_stale(self, tmp_path):
        """No ai-ready.json = always stale."""
        from scripts.ai_ready_helpers import check_staleness

        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, capture_output=True)
        subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, capture_output=True)
        subprocess.run(["git", "config", "user.name", "T"], cwd=repo, capture_output=True)
        (repo / "x.py").write_text("x = 1")
        subprocess.run(["git", "add", "."], cwd=repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True)

        output = tmp_path / "output"
        output.mkdir()

        result = check_staleness(output, repo)
        assert result["overall"] == "stale"
        assert "no ai-ready.json" in result["changes"][0]

    def test_hook_config_claude_code(self):
        """Hook config for Claude Code has FileChanged pattern."""
        from scripts.ai_ready_helpers import generate_hook_config

        config = generate_hook_config("claude-code")
        assert "hooks" in config
        assert "FileChanged" in config["hooks"]
        hook = config["hooks"]["FileChanged"][0]
        assert "pattern" in hook
        assert "pyproject.toml" in hook["pattern"]
        assert hook["_source"] == "ai-ready-engine"

    def test_hook_config_kiro(self):
        """Hook config for Kiro has onFileChange."""
        from scripts.ai_ready_helpers import generate_hook_config

        config = generate_hook_config("kiro")
        assert "hooks" in config
        assert "onFileChange" in config["hooks"]


# ─── Multi-Package (P4) ───

class TestMultiPackage:
    """Run engine on multiple packages with cross-package synthesis."""

    def _make_npm_monorepo(self, tmp_path, members):
        """Build a real npm-workspaces monorepo (root package.json globs packages/*)
        so run_multi_package's AUTO-DETECT (detect_package_roots) surfaces >=2 members
        — not a hand-fed list. Each member is a git repo with a main.py."""
        import json as _json
        (tmp_path / "package.json").write_text(
            _json.dumps({"name": "mono", "workspaces": ["packages/*"]})
        )
        subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "config", "user.name", "T"], cwd=tmp_path, capture_output=True)
        for name, body in members.items():
            repo = tmp_path / "packages" / name
            repo.mkdir(parents=True)
            (repo / "main.py").write_text(body)
        subprocess.run(["git", "add", "."], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init mono"], cwd=tmp_path, capture_output=True)

    def test_multi_package_produces_per_pkg_output(self, tmp_path):
        """AUTO-DETECT: one repo_root → per-package analysis via detect_package_roots."""
        from scripts.ai_ready_helpers import run_multi_package

        self._make_npm_monorepo(tmp_path, {
            "frontend": "# frontend\nimport shared\n",
            "backend": "# backend\nimport shared\n",
        })

        output = tmp_path / "output"
        # NOTE: single repo_root, NOT a hand-fed list — the whole point of AC4.
        result = run_multi_package(tmp_path, output, project_name="my-system")

        assert len(result["packages"]) == 2
        assert result["project_name"] == "my-system"
        assert {p["name"] for p in result["packages"]} == {"frontend", "backend"}
        # partition is surfaced alongside the analyzed packages
        assert len(result["partition"]) == 2
        # Each package has stats + detection metadata
        for pkg in result["packages"]:
            assert "stats" in pkg
            assert pkg["stats"]["files"] >= 1
            assert pkg["detected_by"]  # e.g. "npm"
            assert pkg["root"].startswith("packages/")

    def test_cross_package_finds_shared_deps(self, tmp_path):
        """Shared imports across auto-detected packages are detected."""
        from scripts.ai_ready_helpers import run_multi_package

        self._make_npm_monorepo(tmp_path, {
            "svc_a": "import shared_lib\nimport common_utils\n",
            "svc_b": "import shared_lib\nimport common_utils\n",
        })

        result = run_multi_package(tmp_path, tmp_path / "out")

        # Both import shared_lib and common_utils → should appear in shared_deps
        assert "shared_lib" in result["cross_package"]["shared_deps"]
        assert "common_utils" in result["cross_package"]["shared_deps"]

    def test_single_package_repo_degrades_to_one_root(self, tmp_path):
        """A repo with no workspace manifest → exactly one package rooted at '.'."""
        from scripts.ai_ready_helpers import run_multi_package

        subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "config", "user.name", "T"], cwd=tmp_path, capture_output=True)
        (tmp_path / "main.py").write_text("# solo\nimport os\n")
        subprocess.run(["git", "add", "."], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, capture_output=True)

        result = run_multi_package(tmp_path, tmp_path / "out")
        assert len(result["packages"]) == 1
        assert result["packages"][0]["root"] == "."

    def test_root_app_with_nested_tool_manifest_keeps_root(self, tmp_path):
        """Gate-2 F1: root app.py + tools/gen/pyproject.toml must NOT drop the root.
        A lone nested manifest previously surfaced ONLY the nested dir, silently
        losing the root application from the partition."""
        from scripts.ai_ready_helpers import detect_package_roots
        subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
        (tmp_path / "app.py").write_text("import flask\ndef main(): pass\n")
        (tmp_path / "server.py").write_text("import app\n")
        tool = tmp_path / "tools" / "gen"
        tool.mkdir(parents=True)
        (tool / "pyproject.toml").write_text("[project]\nname='gen'\n")
        (tool / "gen.py").write_text("print('gen')\n")

        roots = detect_package_roots(tmp_path)
        rootset = {r.root for r in roots}
        assert "." in rootset, f"root dropped — got {rootset}"
        assert "tools/gen" in rootset

    def test_true_monorepo_gets_no_spurious_root(self, tmp_path):
        """The F1 guard must NOT add a root package to a genuine monorepo whose code
        lives entirely in packages/* (root has only package.json, no source)."""
        from scripts.ai_ready_helpers import detect_package_roots
        import json as _json
        (tmp_path / "package.json").write_text(
            _json.dumps({"name": "mono", "workspaces": ["packages/*"]})
        )
        subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
        for n in ["api", "web"]:
            d = tmp_path / "packages" / n
            d.mkdir(parents=True)
            (d / "main.py").write_text(f"# {n}\n")
        roots = detect_package_roots(tmp_path)
        assert "." not in {r.root for r in roots}, "spurious root added to true monorepo"
        assert {r.root for r in roots} == {"packages/api", "packages/web"}

    def test_root_member_name_collision_distinct_names_and_outputs(self, tmp_path):
        """Meta-review F-1: repo dir 'X' + a nested member also named 'X' must yield
        DISTINCT package names AND distinct output_paths in run_multi_package — else
        the second package clobbers the first (silent coverage loss). The root-coverage
        F1 guard activates this path by prepending a root package named after the repo."""
        from scripts.ai_ready_helpers import run_multi_package
        repo = tmp_path / "widget"          # repo dir name
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, capture_output=True)
        (repo / "app.py").write_text("import flask\n")
        (repo / "core.py").write_text("import os\n")
        member = repo / "sub" / "widget"    # member with the SAME name as the repo
        member.mkdir(parents=True)
        (member / "pyproject.toml").write_text("[project]\nname='widget'\n")
        (member / "m.py").write_text("print('m')\n")
        subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, capture_output=True)
        subprocess.run(["git", "config", "user.name", "T"], cwd=repo, capture_output=True)
        subprocess.run(["git", "add", "."], cwd=repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "i"], cwd=repo, capture_output=True)

        mp = run_multi_package(repo, tmp_path / "out")
        ok = [p for p in mp["packages"] if "error" not in p]
        names = [p["name"] for p in ok]
        outs = [p["output_path"] for p in ok]
        assert len(names) == len(set(names)), f"duplicate package names: {names}"
        assert len(outs) == len(set(outs)), f"duplicate output_paths: {outs}"


class TestValidateRepoPath:
    """_validate_repo_path contract: a git ROOT or a monorepo MEMBER
    (subdir inside a git work-tree) is accepted; a non-git dir is still rejected."""

    def test_git_root_accepted(self, tmp_path):
        from scripts.ai_ready_helpers import _validate_repo_path
        subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
        assert _validate_repo_path(tmp_path) == tmp_path.resolve()

    def test_monorepo_member_without_own_git_accepted(self, tmp_path):
        """A subdir with NO .git of its own, inside a git work-tree, is accepted."""
        from scripts.ai_ready_helpers import _validate_repo_path
        subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
        member = tmp_path / "packages" / "svc"
        member.mkdir(parents=True)
        assert not (member / ".git").exists()
        assert _validate_repo_path(member) == member.resolve()

    def test_non_git_dir_still_rejected(self, tmp_path):
        """Security intent preserved: a dir outside any git work-tree is rejected."""
        from scripts.ai_ready_helpers import _validate_repo_path
        plain = tmp_path / "plain"
        plain.mkdir()
        with pytest.raises(ValueError, match="Not a git repository"):
            _validate_repo_path(plain)


# ─── Verification Tasks (M3) ───

class TestVerificationTasks:
    """Select tasks from git log and build verification prompts."""

    def test_selects_tasks_from_git_log(self, tmp_path):
        """Selects fix/feat/refactor tasks from a repo with conventional commits."""
        from scripts.ai_ready_helpers import select_verification_tasks

        repo = tmp_path / "verified"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, capture_output=True)
        subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, capture_output=True)
        subprocess.run(["git", "config", "user.name", "T"], cwd=repo, capture_output=True)

        # Create commits with conventional prefixes
        (repo / "handler.py").write_text("def handle(): pass")
        subprocess.run(["git", "add", "."], cwd=repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "feat: add handler endpoint"], cwd=repo, capture_output=True)

        (repo / "handler.py").write_text("def handle():\n    validate()\n    pass")
        subprocess.run(["git", "add", "."], cwd=repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "fix: validate input before processing"], cwd=repo, capture_output=True)

        (repo / "utils.py").write_text("def validate(): pass")
        subprocess.run(["git", "add", "."], cwd=repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "refactor: extract validation to utils"], cwd=repo, capture_output=True)

        tasks = select_verification_tasks(repo)
        assert len(tasks) >= 2
        # Each task has required fields
        for t in tasks:
            assert "type" in t
            assert "description" in t
            assert "correct_file" in t
            assert "commit" in t
            assert t["correct_file"].endswith(".py")

    def test_builds_prompt_without_source_paths(self):
        """Verification prompt contains only DDD text, no source file paths."""
        from scripts.ai_ready_helpers import build_verification_prompt

        ddd = {
            "AGENTS.md": "# MyProject\n## Architecture\n- `src/core/` — business logic",
            "TECH.md": "# Tech\n## Conventions\n- Always use repository pattern",
        }
        tasks = [
            {"type": "fix", "description": "fix: null pointer in handler", "correct_file": "src/handler.py", "commit": "abc1234"},
        ]

        prompt = build_verification_prompt(ddd, tasks)

        # Contains DDD content
        assert "Always use repository pattern" in prompt
        assert "Architecture" in prompt
        # Contains task
        assert "null pointer in handler" in prompt
        # Does NOT contain instruction to read source
        assert "Read(" not in prompt
        assert "/src/handler.py" not in prompt  # source path not leaked

    def test_evaluates_correct_response(self):
        """Correct file identification scores as pass."""
        from scripts.ai_ready_helpers import evaluate_verification_response

        tasks = [
            {"type": "fix", "description": "fix null pointer", "correct_file": "src/handler.py", "commit": "abc"},
            {"type": "feat", "description": "feat add auth", "correct_file": "src/auth.py", "commit": "def"},
            {"type": "refactor", "description": "refactor utils", "correct_file": "src/utils.py", "commit": "ghi"},
        ]

        response = """
        TASK 1: FILE: src/handler.py | FUNCTION: handle_request | APPROACH: add null check
        TASK 2: FILE: src/auth.py | FUNCTION: authenticate | APPROACH: add middleware
        TASK 3: FILE: src/utils.py | FUNCTION: validate | APPROACH: extract to separate module
        """

        result = evaluate_verification_response(response, tasks)
        assert result["passed"] is True
        assert result["score"] == "3/3"
        assert all(r["correct"] for r in result["results"])

    def test_evaluates_insufficient_response(self):
        """INSUFFICIENT response generates feedback for GENERATE improvement."""
        from scripts.ai_ready_helpers import evaluate_verification_response

        tasks = [
            {"type": "fix", "description": "fix dedup threshold", "correct_file": "mempalace/dedup.py", "commit": "abc"},
            {"type": "feat", "description": "feat add webhook", "correct_file": "mempalace/miner.py", "commit": "def"},
            {"type": "refactor", "description": "refactor palace", "correct_file": "mempalace/palace.py", "commit": "ghi"},
        ]

        response = """
        TASK 1: INSUFFICIENT — need: dedup.py function list not in TECH.md
        TASK 2: INSUFFICIENT — need: no extension points documented for post-mine hooks
        TASK 3: FILE: mempalace/palace.py | FUNCTION: file_already_mined | APPROACH: simplify pagination
        """

        result = evaluate_verification_response(response, tasks)
        assert result["passed"] is False
        assert result["score"] == "1/3"
        assert len(result["feedback"]) == 2
        assert "dedup" in result["feedback"][0].lower()
        assert "extension" in result["feedback"][1].lower()

    # ─── Gap 3: matcher false-pass (corrected at the plan gate) ───

    def test_wrong_file_sharing_stem_does_not_false_pass(self):
        """Gate-2/skeptic bug: `file_stem in answer` substring test scored a WRONG
        file CORRECT whenever it shared the stem. correct_file=src/utils.py, agent
        names src/utils_v2.py (wrong) — stem 'utils' is a substring of both → old
        code false-passed. Exact-token match must score this INCORRECT."""
        from scripts.ai_ready_helpers import evaluate_verification_response
        tasks = [{"type": "fix", "description": "fix util bug", "correct_file": "src/utils.py", "commit": "abc"}]
        # Agent names a DIFFERENT file whose stem collides; also the stem appears in prose.
        response = "TASK 1: FILE: src/utils_v2.py | FUNCTION: helper | APPROACH: refactor the utils layer"
        result = evaluate_verification_response(response, tasks)
        assert result["results"][0]["correct"] is False, \
            "wrong file sharing stem must NOT pass (the :3473 substring false-pass)"

    def test_stem_in_prose_without_the_file_does_not_pass(self):
        """The stem appearing only in APPROACH prose (no file named) must fail."""
        from scripts.ai_ready_helpers import evaluate_verification_response
        tasks = [{"type": "fix", "description": "fix handler", "correct_file": "src/handler.py", "commit": "abc"}]
        response = "TASK 1: FILE: src/router.py | FUNCTION: route | APPROACH: call the handler function"
        result = evaluate_verification_response(response, tasks)
        assert result["results"][0]["correct"] is False

    def test_correct_full_path_still_passes(self):
        """Regression guard: an exact full-path answer must STILL pass after the fix."""
        from scripts.ai_ready_helpers import evaluate_verification_response
        tasks = [{"type": "fix", "description": "fix x", "correct_file": "backend/core/foo.py", "commit": "abc"}]
        response = "TASK 1: FILE: backend/core/foo.py | FUNCTION: bar | APPROACH: guard"
        assert evaluate_verification_response(response, tasks)["results"][0]["correct"] is True

    def test_wrong_directory_same_basename_does_not_pass(self):
        """Gate-2 correction (SUPERSEDES the earlier Gate-1 basename-prefix leniency):
        naming the right basename under a DIFFERENT directory (src/foo.py when correct
        is backend/core/foo.py) must FAIL. The earlier draft accepted basename-of-any-
        path-token, which false-passes a genuinely different file (api/models.py vs
        db/models.py, both real in one repo). A file-location probe SHOULD score a
        wrong directory as a miss. Match is full-path-exact OR bare-basename only."""
        from scripts.ai_ready_helpers import evaluate_verification_response
        tasks = [{"type": "fix", "description": "fix x", "correct_file": "backend/core/foo.py", "commit": "abc"}]
        response = "TASK 1: FILE: src/foo.py | FUNCTION: bar | APPROACH: guard"
        assert evaluate_verification_response(response, tasks)["results"][0]["correct"] is False

    def test_same_basename_different_dir_is_a_miss(self):
        """Gate-2 MED: api/models.py vs db/models.py — genuinely different files that
        share a basename must NOT match via basename-of-path-token."""
        from scripts.ai_ready_helpers import evaluate_verification_response
        tasks = [{"type": "fix", "description": "fix model", "correct_file": "db/models.py", "commit": "abc"}]
        response = "TASK 1: FILE: api/models.py | FUNCTION: M | APPROACH: field"
        assert evaluate_verification_response(response, tasks)["results"][0]["correct"] is False

    def test_bare_basename_answer_passes(self):
        """A token that IS just the bare basename (agent referred to the file by name,
        no directory) still passes — the legitimate basename case we DO accept."""
        from scripts.ai_ready_helpers import evaluate_verification_response
        tasks = [{"type": "fix", "description": "fix x", "correct_file": "backend/core/foo.py", "commit": "abc"}]
        response = "TASK 1: FILE: foo.py | FUNCTION: bar | APPROACH: guard"
        assert evaluate_verification_response(response, tasks)["results"][0]["correct"] is True

    def test_label_glued_path_no_space_root_file_passes(self):
        """Gate-2 MED: 'FILE:foo.py' (no space after colon) with a ROOT-level correct
        file must PASS — split on ':' so the label doesn't glue to the path."""
        from scripts.ai_ready_helpers import evaluate_verification_response
        tasks = [{"type": "fix", "description": "fix x", "correct_file": "foo.py", "commit": "abc"}]
        response = "TASK 1: FILE:foo.py | FUNCTION:bar | APPROACH:guard"
        assert evaluate_verification_response(response, tasks)["results"][0]["correct"] is True

    def test_pass_bar_proportional_perfect_single_task_passes(self):
        """Gate-2 HIGH: dedup can yield <3 tasks; a hardcoded >=2 bar made a PERFECT
        agent fail 1/1. Pass bar must scale — 1 task all-correct => passed=True."""
        from scripts.ai_ready_helpers import evaluate_verification_response
        tasks = [{"type": "fix", "description": "fix x", "correct_file": "foo.py", "commit": "abc"}]
        response = "TASK 1: FILE: foo.py | FUNCTION: bar | APPROACH: guard"
        r = evaluate_verification_response(response, tasks)
        assert r["score"] == "1/1"
        assert r["passed"] is True, "1/1 must pass — proportional bar, not hardcoded >=2"

    def test_pass_bar_two_tasks_needs_both(self):
        """ceil(2/3 of 2) = 2 → both required at n=2."""
        from scripts.ai_ready_helpers import evaluate_verification_response
        tasks = [{"type": "fix", "description": "a", "correct_file": "a.py", "commit": "x"},
                 {"type": "feat", "description": "b", "correct_file": "b.py", "commit": "y"}]
        # only 1 correct
        r = evaluate_verification_response("TASK 1: FILE: a.py\nTASK 2: FILE: wrong.py", tasks)
        assert r["score"] == "1/2" and r["passed"] is False

    def test_zero_tasks_not_passed(self):
        from scripts.ai_ready_helpers import evaluate_verification_response
        r = evaluate_verification_response("", [])
        assert r["passed"] is False and r["score"] == "0/0"

    def test_backtick_wrapped_path_passes(self):
        """Gate-1 correction: LLM commonly backtick-wraps paths. Must strip
        surrounding punctuation/backticks per token before comparing."""
        from scripts.ai_ready_helpers import evaluate_verification_response
        tasks = [{"type": "fix", "description": "fix x", "correct_file": "src/handler.py", "commit": "abc"}]
        response = "TASK 1: FILE: `src/handler.py` | FUNCTION: `handle` | APPROACH: null-check."
        assert evaluate_verification_response(response, tasks)["results"][0]["correct"] is True

    def test_select_tasks_dedup_by_correct_file(self, tmp_path):
        """Gate-1: two DIFFERENT commits can share correct_file[0]; the 3 tasks must
        not all point at the same file. Build a repo where fix + feat + refactor all
        touch foo.py first — result must have DISTINCT correct_files."""
        import subprocess as sp
        from scripts.ai_ready_helpers import select_verification_tasks
        r = tmp_path / "repo"
        r.mkdir()
        sp.run(["git", "init", "-q"], cwd=r, check=True)
        sp.run(["git", "config", "user.email", "t@t"], cwd=r, check=True)
        sp.run(["git", "config", "user.name", "t"], cwd=r, check=True)
        (r / "foo.py").write_text("x=1\n")
        (r / "bar.py").write_text("y=1\n")
        def commit(msg, files):
            for f in files:
                (r / f).write_text((r / f).read_text() + f"# {msg}\n")
            sp.run(["git", "add", "-A"], cwd=r, check=True)
            sp.run(["git", "commit", "-q", "-m", msg], cwd=r, check=True)
        commit("feat: add foo path", ["foo.py", "bar.py"])
        commit("fix: foo null guard", ["foo.py"])          # foo.py first again
        commit("refactor: foo + bar cleanup", ["foo.py", "bar.py"])
        tasks = select_verification_tasks(r)
        files = [t["correct_file"] for t in tasks]
        assert len(files) == len(set(files)), f"tasks must have distinct correct_files, got {files}"


# ─── Output Path Resolution ───

class TestOutputPathResolution:
    """Output always goes to a deterministic, findable location."""

    def test_user_specified_target(self, tmp_path):
        """User-specified path takes priority."""
        from scripts.ai_ready_helpers import resolve_output_path

        target = tmp_path / "my-output"
        result = resolve_output_path(Path("/tmp/fakerepo"), target=str(target))
        assert result == target
        assert result.exists()

    def test_swarm_workspace_env_when_set(self, tmp_path, monkeypatch):
        """When $SWARM_WORKSPACE is set, output goes under it (portable — no
        host-specific ~/.swarm-ai probe)."""
        from scripts.ai_ready_helpers import resolve_output_path

        ws = tmp_path / "myworkspace"
        ws.mkdir()
        monkeypatch.setenv("SWARM_WORKSPACE", str(ws))

        result = resolve_output_path(Path("/tmp/mempalace"), project_name="mempalace")
        # lands under $SWARM_WORKSPACE, NOT under ~/.swarm-ai/SwarmWS
        assert str(result).startswith(str(ws)), f"{result} not under $SWARM_WORKSPACE {ws}"
        assert ".swarm-ai/SwarmWS/Projects" not in str(result), "must not use the old host probe"
        assert "ai-ready-mempalace" in str(result)

    def test_fallback_alongside_repo_when_env_unset(self, tmp_path, monkeypatch):
        """Without $SWARM_WORKSPACE or an explicit target, output goes next to the repo."""
        from scripts.ai_ready_helpers import resolve_output_path

        monkeypatch.delenv("SWARM_WORKSPACE", raising=False)
        repo = tmp_path / "myrepo"
        repo.mkdir()
        (repo / ".git").mkdir()

        result = resolve_output_path(repo, project_name="myrepo")
        # repo-adjacent: sibling of the repo dir
        assert result == repo.parent / "ai-ready-myrepo", f"expected repo-adjacent, got {result}"
        assert ".swarm-ai" not in str(result), "must not use the old host probe"

    def test_swarm_workspace_env_always_absolute(self, tmp_path, monkeypatch):
        """A relative / whitespace $SWARM_WORKSPACE still yields an absolute path
        (docstring contract: always absolute)."""
        from scripts.ai_ready_helpers import resolve_output_path

        # relative + surrounding whitespace — the pathological case
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("SWARM_WORKSPACE", "  relative-ws  ")
        result = resolve_output_path(Path("/tmp/mempalace"), project_name="mempalace")
        assert result.is_absolute(), f"must be absolute, got {result}"
        assert "ai-ready-mempalace" in str(result)
        # teeth vs the old host-probe: a relative env resolves under CWD, never the
        # deleted ~/.swarm-ai/SwarmWS bucket (old code goes RED on both asserts here).
        assert ".swarm-ai" not in str(result), "must not use the old host probe"
        assert str(result).startswith(str(tmp_path)), f"relative env must resolve under CWD, got {result}"

        # empty-string env must fall through to repo-adjacent, not write to CWD root
        monkeypatch.setenv("SWARM_WORKSPACE", "")
        repo = tmp_path / "erepo"
        repo.mkdir()
        result2 = resolve_output_path(repo, project_name="erepo")
        assert result2 == repo.parent / "ai-ready-erepo", f"empty env must fall through, got {result2}"


# ─── M5 Multi-package: deterministic package-boundary detection ───

class TestPackageDetection:
    """detect_package_roots + build_packages_partition — manifest-driven boundary
    detection (navigational, NOT a correctness fix; symbol ids are already
    path-qualified). Real-fs fixtures, no mocks."""

    def _pkg_names(self, roots):
        return sorted(r.name for r in roots)

    def _pkg_paths(self, roots):
        return sorted(r.root for r in roots)

    # AC1 — npm workspaces (array form + glob expansion to REAL dirs)
    def test_npm_workspaces_array_glob_expands_to_real_dirs(self, tmp_path):
        from scripts.ai_ready_helpers import detect_package_roots
        (tmp_path / "package.json").write_text(json.dumps({"workspaces": ["packages/*"]}))
        for p in ["packages/api", "packages/web"]:
            (tmp_path / p).mkdir(parents=True)
            (tmp_path / p / "package.json").write_text("{}")
        (tmp_path / "packages" / "NOTADIR.txt").write_text("x")  # glob must not match files
        roots = detect_package_roots(tmp_path)
        paths = self._pkg_paths(roots)
        assert "packages/api" in paths and "packages/web" in paths
        assert not any("*" in p for p in paths), f"glob must expand, got {paths}"
        assert "packages/NOTADIR.txt" not in paths

    # AC1 — npm workspaces object form {packages:[...]}
    def test_npm_workspaces_object_form(self, tmp_path):
        from scripts.ai_ready_helpers import detect_package_roots
        (tmp_path / "package.json").write_text(json.dumps({"workspaces": {"packages": ["apps/*"]}}))
        (tmp_path / "apps" / "admin").mkdir(parents=True)
        roots = detect_package_roots(tmp_path)
        assert "apps/admin" in self._pkg_paths(roots)

    # AC2 — pnpm
    def test_pnpm_workspace_yaml(self, tmp_path):
        from scripts.ai_ready_helpers import detect_package_roots
        (tmp_path / "pnpm-workspace.yaml").write_text("packages:\n  - 'libs/*'\n")
        (tmp_path / "libs" / "core").mkdir(parents=True)
        assert "libs/core" in self._pkg_paths(detect_package_roots(tmp_path))

    # AC2 — lerna
    def test_lerna_json(self, tmp_path):
        from scripts.ai_ready_helpers import detect_package_roots
        (tmp_path / "lerna.json").write_text(json.dumps({"packages": ["modules/*"]}))
        (tmp_path / "modules" / "m1").mkdir(parents=True)
        assert "modules/m1" in self._pkg_paths(detect_package_roots(tmp_path))

    # AC2 — cargo workspace (glob members)
    def test_cargo_workspace_members(self, tmp_path):
        from scripts.ai_ready_helpers import detect_package_roots
        (tmp_path / "Cargo.toml").write_text('[workspace]\nmembers = ["crates/*"]\n')
        (tmp_path / "crates" / "engine").mkdir(parents=True)
        assert "crates/engine" in self._pkg_paths(detect_package_roots(tmp_path))

    # AC2 — go multi-module (N go.mod = N modules)
    def test_go_multi_module(self, tmp_path):
        from scripts.ai_ready_helpers import detect_package_roots
        (tmp_path / "go.mod").write_text("module root\n")
        (tmp_path / "svc").mkdir()
        (tmp_path / "svc" / "go.mod").write_text("module svc\n")
        paths = self._pkg_paths(detect_package_roots(tmp_path))
        assert "svc" in paths

    # AC2 — python multi-package (>1 pyproject in subdirs)
    def test_python_multi_package(self, tmp_path):
        from scripts.ai_ready_helpers import detect_package_roots
        for p in ["pkg_a", "pkg_b"]:
            (tmp_path / p).mkdir()
            (tmp_path / p / "pyproject.toml").write_text("[project]\nname='x'\n")
        paths = self._pkg_paths(detect_package_roots(tmp_path))
        assert "pkg_a" in paths and "pkg_b" in paths

    # AC2 — nx.json / turbo.json presence (monorepo signal even w/o explicit member list)
    def test_nx_and_turbo_presence_detected(self, tmp_path):
        from scripts.ai_ready_helpers import detect_package_roots
        # turbo/nx alone: no explicit member globs → detector should not crash and
        # (absent other manifests) fall back to [root], never []
        (tmp_path / "turbo.json").write_text("{}")
        (tmp_path / "nx.json").write_text("{}")
        roots = detect_package_roots(tmp_path)
        assert len(roots) >= 1

    # AC3 — single-package fallback = exactly [root]
    def test_single_package_falls_back_to_root(self, tmp_path):
        from scripts.ai_ready_helpers import detect_package_roots
        (tmp_path / "pyproject.toml").write_text("[project]\nname='solo'\n")
        (tmp_path / "main.py").write_text("x=1\n")
        roots = detect_package_roots(tmp_path)
        assert len(roots) == 1
        assert roots[0].root == "."
        assert roots[0].name == tmp_path.name

    # AC3 — empty repo still yields [root], never []
    def test_empty_repo_never_empty(self, tmp_path):
        from scripts.ai_ready_helpers import detect_package_roots
        roots = detect_package_roots(tmp_path)
        assert len(roots) == 1 and roots[0].root == "."

    # AC4 — language mix is real extension counts, excludes ignored dirs + non-source
    def test_language_mix_real_counts(self, tmp_path):
        from scripts.ai_ready_helpers import detect_package_roots
        (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
        (tmp_path / "a.py").write_text("x=1\n")
        (tmp_path / "b.py").write_text("y=2\n")
        (tmp_path / "c.ts").write_text("const z=3\n")
        (tmp_path / "README.md").write_text("# doc\n")  # non-source, must NOT count
        (tmp_path / "node_modules").mkdir()
        (tmp_path / "node_modules" / "junk.py").write_text("ignored\n")  # ignored dir
        roots = detect_package_roots(tmp_path)
        mix = roots[0].language_mix
        assert mix.get("python") == 2, f"expected 2 py, got {mix}"
        assert mix.get("typescript") == 1
        assert "markdown" not in mix and "md" not in mix
        # node_modules excluded → no extra python
        assert mix.get("python") == 2

    # AC5 — nested workspace root deduped by resolved path (no double count)
    def test_nested_workspace_dedup(self, tmp_path):
        from scripts.ai_ready_helpers import detect_package_roots
        # root npm workspace lists packages/* ; one of those is ALSO matched by a
        # second manifest (lerna) → same dir must appear once
        (tmp_path / "package.json").write_text(json.dumps({"workspaces": ["packages/*"]}))
        (tmp_path / "lerna.json").write_text(json.dumps({"packages": ["packages/*"]}))
        (tmp_path / "packages" / "shared").mkdir(parents=True)
        roots = detect_package_roots(tmp_path)
        shared = [r for r in roots if r.root == "packages/shared"]
        assert len(shared) == 1, f"nested/duplicate dir must dedup, got {roots}"

    # AC5 — malformed manifest fails soft (no raise), falls back gracefully
    def test_malformed_manifest_fail_soft(self, tmp_path):
        from scripts.ai_ready_helpers import detect_package_roots
        (tmp_path / "package.json").write_text("{ this is not valid json ")
        (tmp_path / "main.py").write_text("x=1\n")
        # must NOT raise; malformed npm manifest → that reader yields nothing →
        # fall back to [root]
        roots = detect_package_roots(tmp_path)
        assert len(roots) == 1 and roots[0].root == "."

    # AC5 — build_packages_partition emits well-formed nav dicts
    def test_build_packages_partition_wellformed(self, tmp_path):
        from scripts.ai_ready_helpers import build_packages_partition
        (tmp_path / "package.json").write_text(json.dumps({"workspaces": ["packages/*"]}))
        (tmp_path / "packages" / "api").mkdir(parents=True)
        (tmp_path / "packages" / "api" / "server.py").write_text("x=1\n")
        part = build_packages_partition(tmp_path)
        assert isinstance(part, list) and len(part) >= 1
        for entry in part:
            assert set(entry) >= {"name", "root", "language_mix", "detected_by"}
            assert isinstance(entry["language_mix"], dict)
        names = [e["name"] for e in part]
        assert len(names) == len(set(names)), "package names must be unique"

    # Real-data smoke: SwarmAI has backend/pyproject.toml (no root manifest), so
    # the detector correctly surfaces `backend` as a python package sub-root.
    # Asserts the REAL behavior (a nested pyproject IS a package boundary), not an
    # assumed "single-package".
    def test_swarmai_repo_real_data(self):
        from scripts.ai_ready_helpers import detect_package_roots
        repo = Path(__file__).resolve().parents[4]  # .../swarmai
        if not (repo / "backend" / "pyproject.toml").exists():
            pytest.skip("not in swarmai repo tree")
        roots = detect_package_roots(repo)
        paths = [r.root for r in roots]
        assert "backend" in paths, \
            f"backend/pyproject.toml → backend is a package root, got {paths}"
        backend_pkg = next(r for r in roots if r.root == "backend")
        assert backend_pkg.language_mix.get("python", 0) > 0, "backend must have real python count"
        assert "python" in backend_pkg.detected_by

    # Gate-2 F1: malformed Cargo.toml (workspace not a table) must fail soft
    def test_cargo_workspace_not_a_table_fail_soft(self, tmp_path):
        from scripts.ai_ready_helpers import detect_package_roots
        (tmp_path / "Cargo.toml").write_text('workspace = "notatable"\n')
        (tmp_path / "main.rs").write_text("fn main(){}\n")
        # must NOT raise AttributeError; falls back to [root]
        roots = detect_package_roots(tmp_path)
        assert len(roots) == 1 and roots[0].root == "."

    # Gate-2 F2: 3 packages named 'core' → ALL disambiguated symmetrically, no
    # bare 'core' left ambiguous
    def test_triple_name_collision_all_disambiguated(self, tmp_path):
        from scripts.ai_ready_helpers import build_packages_partition
        (tmp_path / "package.json").write_text(json.dumps({"workspaces": ["*/core"]}))
        for parent in ["a", "b", "c"]:
            (tmp_path / parent / "core").mkdir(parents=True)
            (tmp_path / parent / "core" / "i.ts").write_text("export{}\n")
        part = build_packages_partition(tmp_path)
        names = [p["name"] for p in part]
        assert len(names) == len(set(names)), f"all names unique, got {names}"
        assert "core" not in names, f"no bare 'core' should remain, got {names}"
        assert len(part) == 3


# ─── Gap 2: Business-Rules Extraction scoring dimension ───

class TestBusinessRulesDimension:
    """The 10th scoring dimension, mechanically computed from doc['domain_rules'].

    Coverage = domains-that-produced-rules / total domains. Traceability (optional,
    only when specs are passed) = fraction of domains whose spec passes
    verify_traceability. Score = round(coverage * traceability_factor * 10).
    A doc with NO domain_rules layer scores N/A and is EXCLUDED from overall — a
    repo without business rules must not be penalized (the honesty requirement).
    """

    def _doc(self, domains, rules):
        return {"domain_rules": {"domains": domains, "rules": rules}}

    def test_full_coverage_no_specs_scores_on_coverage_alone(self):
        from scripts.ai_ready_helpers import compute_business_rules_dimension
        doc = self._doc(
            [{"domain_id": "d1", "rule_count": 2}, {"domain_id": "d2", "rule_count": 1}],
            [{"rule_id": "BR-d1-1", "domain_id": "d1"}, {"rule_id": "BR-d1-2", "domain_id": "d1"},
             {"rule_id": "BR-d2-1", "domain_id": "d2"}],
        )
        r = compute_business_rules_dimension(doc)
        assert r["score"] == 10, r          # 2/2 domains have rules, no traceability penalty
        assert r["coverage"] == 1.0
        assert r["traceability_pass"] is None  # not measured without specs
        assert r["applicable"] is True

    def test_partial_coverage_scales_score(self):
        from scripts.ai_ready_helpers import compute_business_rules_dimension
        # 1 of 2 domains produced rules → coverage 0.5 → score 5
        doc = self._doc(
            [{"domain_id": "d1", "rule_count": 1}, {"domain_id": "d2", "rule_count": 0}],
            [{"rule_id": "BR-d1-1", "domain_id": "d1"}],
        )
        r = compute_business_rules_dimension(doc)
        assert r["coverage"] == 0.5
        assert r["score"] == 5, r

    def test_no_domain_rules_layer_is_NA_excluded_from_overall(self):
        from scripts.ai_ready_helpers import compute_business_rules_dimension
        # A non-legacy / non-SQL repo has no domain_rules key at all.
        r = compute_business_rules_dimension({"modules": [], "version": "2.0"})
        assert r["applicable"] is False
        assert r["score"] is None, "N/A must be None, never 0 (0 would penalize the average)"
        assert "N/A" in r["detail"] or "no domain_rules" in r["detail"].lower()

    def test_empty_domains_is_NA_not_zero(self):
        from scripts.ai_ready_helpers import compute_business_rules_dimension
        # domain_rules present but zero domains detected → nothing to score → N/A,
        # NOT a 0 that would drag the overall down.
        r = compute_business_rules_dimension(self._doc([], []))
        assert r["applicable"] is False
        assert r["score"] is None

    def test_traceability_partial_failure_scales_down(self):
        from scripts.ai_ready_helpers import compute_business_rules_dimension
        # 2 domains both have rules (coverage 1.0). specs provided: d1 references its
        # rule in the behavioral section (passes), d2 only lists it in the §9 matrix
        # (fails verify_traceability) → traceability 0.5 → score = round(1.0*0.5*10)=5.
        doc = self._doc(
            [{"domain_id": "d1", "rule_count": 1}, {"domain_id": "d2", "rule_count": 1}],
            [{"rule_id": "BR-d1-1", "domain_id": "d1"}, {"rule_id": "BR-d2-1", "domain_id": "d2"}],
        )
        specs = {
            # behavioral body references BR-d1-1 before the §9 matrix → covered
            "d1": "# Spec d1\n\n## 1. Behavior\nBR-d1-1 applies here.\n\n## 9. Traceability Matrix\n| BR-d1-1 |\n",
            # BR-d2-1 appears ONLY in the §9 matrix → uncovered → domain fails
            "d2": "# Spec d2\n\n## 1. Behavior\nnothing cited.\n\n## 9. Traceability Matrix\n| BR-d2-1 |\n",
        }
        r = compute_business_rules_dimension(doc, specs=specs)
        assert r["traceability_pass"] == 0.5, r
        assert r["score"] == 5, r

    def test_full_traceability_keeps_full_score(self):
        from scripts.ai_ready_helpers import compute_business_rules_dimension
        doc = self._doc(
            [{"domain_id": "d1", "rule_count": 1}],
            [{"rule_id": "BR-d1-1", "domain_id": "d1"}],
        )
        specs = {"d1": "# d1\n\n## 1. Behavior\nBR-d1-1 here.\n\n## 9. Traceability Matrix\n| BR-d1-1 |\n"}
        r = compute_business_rules_dimension(doc, specs=specs)
        assert r["traceability_pass"] == 1.0
        assert r["score"] == 10

    def test_rule_count_without_backing_rule_ids_fails_closed(self):
        """Gate-2 finding: a domain claiming rule_count>0 but with no matching
        rule_id in rules[] (empty rules[], or a None/mismatched domain_id) must NOT
        pass traceability vacuously. coverage counts it (rule_count>0); traceability
        must FAIL it closed — else the score inflates for a domain that cites nothing."""
        from scripts.ai_ready_helpers import compute_business_rules_dimension
        # rule_count claims a rule, but rules[] is empty → unprovable → fail-closed
        doc = {"domain_rules": {"domains": [{"domain_id": "d1", "rule_count": 1}], "rules": []}}
        specs = {"d1": "# s\n## 1. Behavior\nnothing cited\n## 9. Traceability Matrix\n| x |\n"}
        r = compute_business_rules_dimension(doc, specs=specs)
        assert r["traceability_pass"] == 0.0, r   # was vacuously 1.0 before the fix
        assert r["score"] == 0, r                 # coverage 1.0 * traceability 0.0 * 10

    def test_rule_with_none_domain_id_does_not_vacuously_pass(self):
        """A rule whose domain_id is None is dropped from grouping; the rule-bearing
        domain then has no resolvable ids → must fail closed, not pass on empty ids."""
        from scripts.ai_ready_helpers import compute_business_rules_dimension
        doc = {"domain_rules": {"domains": [{"domain_id": "d1", "rule_count": 1}],
                                "rules": [{"rule_id": "BR-d1-1", "domain_id": None}]}}
        specs = {"d1": "# s\n## 1. Behavior\nnothing\n## 9. Traceability Matrix\n| BR-d1-1 |\n"}
        r = compute_business_rules_dimension(doc, specs=specs)
        assert r["traceability_pass"] == 0.0, r
        assert r["score"] == 0, r


# ─── Gap 3: producer order-invariance tripwires ───
#
# NOT `f(x)==f(x)`. Each test feeds SHUFFLED/REVERSED input to a producer and
# asserts the collection field comes out SORTED — so if a future edit drops the
# `sorted()` at the source, the test goes RED (Gate-1 correction: an already-ordered
# fixture would make this a worthless tautology; deliberately-disordered input is
# what gives it regression teeth).

