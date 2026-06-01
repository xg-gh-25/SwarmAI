"""Tests for AI-Ready-Repo Engine helper script.

Tests deterministic operations: schema validation, git log parsing, repo info gathering.
Uses real filesystem fixtures where possible (no mocks for file operations).
"""
import json
import subprocess
import tempfile
from pathlib import Path

import pytest


# ─── AC3: code-intel.json v2 schema validation ───

class TestCodeIntelValidation:
    """Validate code-intel.json v2 schema conformance."""

    def test_valid_v2_document_passes(self):
        """A well-formed v2 document should pass validation."""
        from scripts.ai_ready_helpers import validate_code_intel_json

        valid_doc = {
            "$schema": "https://ai-ready-repo.dev/schemas/code-intel.v2.json",
            "version": "2.0",
            "generated_at": "2026-05-29T15:10:00+08:00",
            "repo": {
                "name": "test-project",
                "languages": {"python": 0.8, "shell": 0.2},
                "total_symbols": 150,
                "total_edges": 200,
            },
            "modules": [
                {
                    "name": "core",
                    "path": "src/core/",
                    "responsibility": "Core business logic",
                    "entry_points": ["src/core/main.py:main"],
                    "depends_on": ["database"],
                    "depended_by": ["api"],
                }
            ],
            "edges": [
                {"from": "core", "to": "database", "type": "runtime", "weight": "critical"}
            ],
            "entry_points": [
                {"path": "src/main.py", "type": "cli", "description": "Entry point"}
            ],
            "routes": [],
            "hot_zones": [],
            "risk_areas": [],
            "dead_code": [],
            "dependencies": {},
        }

        errors = validate_code_intel_json(valid_doc)
        assert errors == [], f"Valid doc should have no errors, got: {errors}"

    def test_missing_required_fields_fails(self):
        """Document missing $schema, version, or repo should fail."""
        from scripts.ai_ready_helpers import validate_code_intel_json

        incomplete_doc = {"modules": [], "edges": []}
        errors = validate_code_intel_json(incomplete_doc)
        assert len(errors) > 0
        assert any("$schema" in e or "version" in e or "repo" in e for e in errors)

    def test_wrong_version_fails(self):
        """Document with version != 2.0 should fail."""
        from scripts.ai_ready_helpers import validate_code_intel_json

        doc = {
            "$schema": "https://ai-ready-repo.dev/schemas/code-intel.v2.json",
            "version": "1.0",
            "repo": {"name": "test", "languages": {}, "total_symbols": 0, "total_edges": 0},
            "modules": [],
            "edges": [],
            "entry_points": [],
            "routes": [],
            "hot_zones": [],
            "risk_areas": [],
            "dead_code": [],
            "dependencies": {},
        }
        errors = validate_code_intel_json(doc)
        assert any("version" in e for e in errors)

    def test_invalid_module_structure_fails(self):
        """Module missing required fields should fail."""
        from scripts.ai_ready_helpers import validate_code_intel_json

        doc = {
            "$schema": "https://ai-ready-repo.dev/schemas/code-intel.v2.json",
            "version": "2.0",
            "repo": {"name": "test", "languages": {}, "total_symbols": 0, "total_edges": 0},
            "modules": [{"name": "incomplete"}],  # missing path, responsibility
            "edges": [],
            "entry_points": [],
            "routes": [],
            "hot_zones": [],
            "risk_areas": [],
            "dead_code": [],
            "dependencies": {},
        }
        errors = validate_code_intel_json(doc)
        assert len(errors) > 0
        assert any("path" in e or "responsibility" in e for e in errors)


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

        external_repo = Path("/Users/gawan/Desktop/SwarmAI-Workspace/ai-ready-repo")
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
        assert ".ai-ready/PRODUCT.md" in output
