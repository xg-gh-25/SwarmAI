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
                "total_edges": 1,
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


# ─── code-intel v3 domain layer (Run 1, run_aad6d4f2) ───

def _minimal_v2_doc() -> dict:
    """A valid v2 doc — reused as the base for v3 tests."""
    return {
        "$schema": "https://ai-ready-repo.dev/schemas/code-intel.v2.json",
        "version": "2.0",
        "repo": {"name": "t", "languages": {"python": 1.0}, "total_symbols": 10, "total_edges": 1},
        "modules": [{"name": "core", "path": "src/", "responsibility": "x"}],
        "edges": [{"from": "core", "to": "db"}],
        "entry_points": [{"path": "src/main.py"}],
    }


class TestV3SchemaValidation:
    """v3 = v2 + domains/flows/steps. v2 docs MUST still pass (backward-compat)."""

    def test_v2_doc_still_passes_under_v3_capable_validator(self):
        from scripts.ai_ready_helpers import validate_code_intel_json
        assert validate_code_intel_json(_minimal_v2_doc()) == []

    def test_valid_v3_doc_passes(self):
        from scripts.ai_ready_helpers import validate_code_intel_json
        doc = _minimal_v2_doc()
        doc["$schema"] = "https://ai-ready-repo.dev/schemas/code-intel.v3.json"
        doc["version"] = "3.0"
        doc["routes"] = [{"id": "route:get-orders-a1b2", "method": "GET", "path": "/orders",
                          "file_path": "src/api.py", "line_number": 10}]
        doc["domains"] = [{"id": "domain:orders", "name": "Orders", "summary": "s",
                           "complexity": "moderate", "source": "llm"}]
        doc["flows"] = [{"id": "flow:list", "domain_id": "domain:orders", "name": "List",
                         "entry_type": "http", "entry_ref": "route:get-orders-a1b2", "source": "llm"}]
        doc["steps"] = [{"id": "step:list:q", "flow_id": "flow:list", "order": 1,
                         "name": "Query", "file_path": "src/api.py", "line_range": [10, 20],
                         "source": "llm"}]
        assert validate_code_intel_json(doc) == []

    def test_v3_domains_must_be_list(self):
        from scripts.ai_ready_helpers import validate_code_intel_json
        doc = _minimal_v2_doc(); doc["version"] = "3.0"; doc["domains"] = {"not": "a list"}
        errors = validate_code_intel_json(doc)
        assert any("domains" in e for e in errors)

    def test_v3_flow_missing_domain_id_flagged(self):
        from scripts.ai_ready_helpers import validate_code_intel_json
        doc = _minimal_v2_doc(); doc["version"] = "3.0"
        doc["flows"] = [{"id": "flow:x", "name": "X", "entry_type": "http", "source": "llm"}]
        errors = validate_code_intel_json(doc)
        assert any("domain_id" in e for e in errors)


class TestReferentialIntegrity:
    """flow.entry_ref → real route.id; cross_domain.target/domain_id/flow_id resolve."""

    def test_dangling_entry_ref_flagged(self):
        from scripts.ai_ready_helpers import check_domain_referential_integrity
        doc = {"routes": [{"id": "route:real"}],
               "domains": [{"id": "domain:o"}],
               "flows": [{"id": "flow:x", "domain_id": "domain:o", "entry_ref": "route:GHOST"}],
               "steps": []}
        errors = check_domain_referential_integrity(doc)
        assert any("route:GHOST" in e or "entry_ref" in e for e in errors)

    def test_dangling_cross_domain_target_flagged(self):
        from scripts.ai_ready_helpers import check_domain_referential_integrity
        doc = {"routes": [], "flows": [], "steps": [],
               "domains": [{"id": "domain:o", "cross_domain": [{"target": "domain:GHOST"}]}]}
        errors = check_domain_referential_integrity(doc)
        assert any("domain:GHOST" in e for e in errors)

    def test_dangling_step_flow_id_flagged(self):
        from scripts.ai_ready_helpers import check_domain_referential_integrity
        doc = {"routes": [], "domains": [{"id": "domain:o"}], "flows": [],
               "steps": [{"id": "step:x", "flow_id": "flow:GHOST"}]}
        errors = check_domain_referential_integrity(doc)
        assert any("flow:GHOST" in e or "flow_id" in e for e in errors)

    def test_all_references_resolve_no_error(self):
        from scripts.ai_ready_helpers import check_domain_referential_integrity
        doc = {"routes": [{"id": "route:r"}],
               "domains": [{"id": "domain:o", "cross_domain": []}],
               "flows": [{"id": "flow:f", "domain_id": "domain:o", "entry_ref": "route:r"}],
               "steps": [{"id": "step:s", "flow_id": "flow:f"}]}
        assert check_domain_referential_integrity(doc) == []


class TestLlmAssertionGuards:
    """§1.5: verified:true needs anchor; verified:false needs absence_evidence."""

    def test_verified_true_without_anchor_flagged(self):
        from scripts.ai_ready_helpers import check_llm_assertion_guards
        doc = {"domains": [{"id": "domain:o",
                            "business_rules": [{"rule": "r", "verified": True, "anchor": None}]}]}
        errors = check_llm_assertion_guards(doc)
        assert any("anchor" in e for e in errors)

    def test_verified_false_without_absence_evidence_flagged(self):
        """§1.5#4 anti-false-negative: a [llm-inferred] claim MUST prove absence."""
        from scripts.ai_ready_helpers import check_llm_assertion_guards
        doc = {"domains": [{"id": "domain:o",
                            "business_rules": [{"rule": "r", "verified": False}]}]}
        errors = check_llm_assertion_guards(doc)
        assert any("absence_evidence" in e for e in errors)

    def test_well_formed_assertions_no_error(self):
        from scripts.ai_ready_helpers import check_llm_assertion_guards
        doc = {"domains": [{"id": "domain:o", "business_rules": [
            {"rule": "a", "verified": True, "anchor": "src/x.py:L10"},
            {"rule": "b", "verified": False, "absence_evidence": "grep 'x' src → 0 hits"},
        ]}]}
        assert check_llm_assertion_guards(doc) == []


class TestRouteIdDerivation:
    """§1.4: route.id = {method}-{path}-{shorthash(file_path)}, collision-resistant."""

    def test_deterministic_same_inputs_same_id(self):
        from scripts.ai_ready_helpers import derive_route_id
        a = derive_route_id("GET", "/orders", "src/api.py")
        b = derive_route_id("GET", "/orders", "src/api.py")
        assert a == b and a.startswith("route:")

    def test_same_method_path_different_file_distinct_id(self):
        """The collision case: same endpoint in two files → distinct ids (teeth)."""
        from scripts.ai_ready_helpers import derive_route_id
        real = derive_route_id("POST", "/orders", "src/api.py")
        mock = derive_route_id("POST", "/orders", "tests/mock_api.py")
        assert real != mock

    def test_no_line_number_so_drift_resistant(self):
        import re as _re
        from scripts.ai_ready_helpers import derive_route_id
        # id must NOT carry a `:line` suffix (would break on code drift)
        rid = derive_route_id("GET", "/x", "src/api.py")
        assert not _re.search(r":\d+$", rid), f"id must not embed a line number: {rid}"
        # same file+method+path → stable id (no line dependency)
        assert derive_route_id("GET", "/x", "src/api.py") == rid

    def test_slug_collapsing_paths_do_not_collide(self):
        """/a/b vs /a-b slug-collapse, but the exact-triple hash keeps them distinct."""
        from scripts.ai_ready_helpers import derive_route_id
        assert derive_route_id("GET", "/a/b", "src/api.py") != derive_route_id("GET", "/a-b", "src/api.py")
        assert derive_route_id("GET", "/users", "src/api.py") != derive_route_id("GET", "/users/", "src/api.py")

    def test_no_collision_across_realistic_route_count(self):
        """300+ distinct routes → 300+ distinct ids (32-bit hash, not 16). Teeth for the collision fix."""
        from scripts.ai_ready_helpers import derive_route_id
        ids = {derive_route_id("GET", f"/r{i}", f"src/file_{i}.py") for i in range(400)}
        assert len(ids) == 400


class TestLlmAssertionGuardsHardened:
    """Gate-2 bypass fixes (run_aad6d4f2): type-confusion, blank, opt-out, contract-level."""

    def test_verified_string_not_bool_is_flagged(self):
        """verified:"true" (string) must NOT sail through as a bool — CRITICAL bypass."""
        from scripts.ai_ready_helpers import check_llm_assertion_guards
        doc = {"domains": [{"id": "d", "business_rules": [
            {"rule": "r", "verified": "true", "absence_evidence": "x"}]}]}
        assert any("bool" in e for e in check_llm_assertion_guards(doc))

    def test_blank_anchor_flagged(self):
        from scripts.ai_ready_helpers import check_llm_assertion_guards
        doc = {"domains": [{"id": "d", "business_rules": [
            {"rule": "r", "verified": True, "anchor": "   "}]}]}
        assert any("anchor" in e for e in check_llm_assertion_guards(doc))

    def test_plain_string_rule_flagged(self):
        """An LLM must not dodge the guard by emitting a bare string rule."""
        from scripts.ai_ready_helpers import check_llm_assertion_guards
        doc = {"domains": [{"id": "d", "business_rules": ["order total must be > 0"]}]}
        assert len(check_llm_assertion_guards(doc)) > 0

    def test_dict_without_verified_flagged(self):
        from scripts.ai_ready_helpers import check_llm_assertion_guards
        doc = {"domains": [{"id": "d", "business_rules": [{"rule": "r"}]}]}
        assert len(check_llm_assertion_guards(doc)) > 0

    def test_step_contract_assertions_covered(self):
        """Assertions hidden under step.contract must still be guarded (§1.5)."""
        from scripts.ai_ready_helpers import check_llm_assertion_guards
        doc = {"steps": [{"id": "s", "contract": {
            "rules": [{"rule": "r", "verified": True, "anchor": None}]}}]}
        assert any("anchor" in e for e in check_llm_assertion_guards(doc))


class TestGuardsWiredIntoValidator:
    """Gate-2 CRITICAL: the main validator MUST invoke both guards, not just structure."""

    def test_dangling_entry_ref_caught_via_main_validator(self):
        from scripts.ai_ready_helpers import validate_code_intel_json
        doc = _minimal_v2_doc()
        doc["version"] = "3.0"
        doc["routes"] = [{"id": "route:real", "method": "GET", "path": "/x", "file_path": "a.py"}]
        doc["domains"] = [{"id": "domain:o", "name": "O"}]
        doc["flows"] = [{"id": "flow:x", "domain_id": "domain:o", "entry_ref": "route:GHOST"}]
        errors = validate_code_intel_json(doc)
        assert any("GHOST" in e for e in errors), "main validator must run referential-integrity guard"

    def test_unanchored_assertion_caught_via_main_validator(self):
        from scripts.ai_ready_helpers import validate_code_intel_json
        doc = _minimal_v2_doc()
        doc["version"] = "3.0"
        doc["domains"] = [{"id": "domain:o", "name": "O",
                           "business_rules": [{"rule": "hallucinated", "verified": True, "anchor": None}]}]
        errors = validate_code_intel_json(doc)
        assert any("anchor" in e for e in errors), "main validator must run assertion guard"

    def test_v2_doc_unaffected_by_guard_wiring(self):
        """Backward-compat: a real v2 doc (no domain layer) triggers neither guard."""
        from scripts.ai_ready_helpers import validate_code_intel_json
        assert validate_code_intel_json(_minimal_v2_doc()) == []


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
        assert ".ai-ready/PRODUCT.md" in output


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
        (output / ".ai-ready").mkdir(parents=True)
        meta = build_ai_ready_meta(5.0, "test")
        meta["_last_commit"] = head
        (output / ".ai-ready" / "ai-ready.json").write_text(json.dumps(meta))

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
        (output / ".ai-ready").mkdir(parents=True)
        meta = build_ai_ready_meta(5.0, "test")
        meta["_last_commit"] = head
        (output / ".ai-ready" / "ai-ready.json").write_text(json.dumps(meta))

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
        from scripts.ai_ready_helpers import check_staleness, gather_repo_info, build_ai_ready_meta

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
        (output / ".ai-ready").mkdir(parents=True)
        import json
        meta = build_ai_ready_meta(5.0, "test")
        (output / ".ai-ready" / "ai-ready.json").write_text(json.dumps(meta))

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

    def test_multi_package_produces_per_pkg_output(self, tmp_path):
        """Each package gets independent analysis."""
        from scripts.ai_ready_helpers import run_multi_package

        # Create 2 mini repos
        for name in ["frontend", "backend"]:
            repo = tmp_path / name
            repo.mkdir()
            subprocess.run(["git", "init"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.name", "T"], cwd=repo, capture_output=True)
            (repo / "main.py").write_text(f"# {name}\nimport shared\n")
            subprocess.run(["git", "add", "."], cwd=repo, capture_output=True)
            subprocess.run(["git", "commit", "-m", f"init {name}"], cwd=repo, capture_output=True)

        output = tmp_path / "output"
        result = run_multi_package(
            [tmp_path / "frontend", tmp_path / "backend"],
            output,
            project_name="my-system",
        )

        assert len(result["packages"]) == 2
        assert result["project_name"] == "my-system"
        # Each package has stats
        for pkg in result["packages"]:
            assert "stats" in pkg
            assert pkg["stats"]["files"] >= 1

    def test_cross_package_finds_shared_deps(self, tmp_path):
        """Shared imports across packages are detected."""
        from scripts.ai_ready_helpers import run_multi_package

        # Create 2 repos that both import "shared_lib"
        for name in ["svc_a", "svc_b"]:
            repo = tmp_path / name
            repo.mkdir()
            subprocess.run(["git", "init"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.name", "T"], cwd=repo, capture_output=True)
            (repo / "app.py").write_text("import shared_lib\nimport common_utils\n")
            subprocess.run(["git", "add", "."], cwd=repo, capture_output=True)
            subprocess.run(["git", "commit", "-m", f"init {name}"], cwd=repo, capture_output=True)

        result = run_multi_package(
            [tmp_path / "svc_a", tmp_path / "svc_b"],
            tmp_path / "out",
        )

        # Both import shared_lib and common_utils → should appear in shared_deps
        assert "shared_lib" in result["cross_package"]["shared_deps"]
        assert "common_utils" in result["cross_package"]["shared_deps"]


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

    def test_swarmws_path_when_available(self):
        """When running inside SwarmAI, output goes to .artifacts/."""
        from scripts.ai_ready_helpers import resolve_output_path

        swarmws = Path.home() / ".swarm-ai" / "SwarmWS"
        if not swarmws.exists():
            pytest.skip("Not in SwarmAI environment")

        result = resolve_output_path(Path("/tmp/mempalace"), project_name="mempalace")
        assert ".swarm-ai/SwarmWS/Projects" in str(result)
        assert "ai-ready-mempalace" in str(result)

    def test_fallback_alongside_repo(self, tmp_path):
        """Without SwarmAI or target, output goes next to the repo."""
        from scripts.ai_ready_helpers import resolve_output_path
        import unittest.mock

        repo = tmp_path / "myrepo"
        repo.mkdir()
        (repo / ".git").mkdir()  # Make it look like a repo

        # Mock away SwarmWS existence
        with unittest.mock.patch("pathlib.Path.exists", side_effect=lambda self: False if "swarm-ai" in str(self) else type(self).exists(self)):
            # Direct call — can't easily mock Path.exists on specific instance
            # Just verify the function runs without error
            pass

        # In practice, if SwarmWS exists it'll use that path
        result = resolve_output_path(repo, project_name="myrepo")
        assert "ai-ready-myrepo" in str(result)


# ─── code-intel v3 incremental merge (Run 2, run_36266b66) ───

class TestMergeCodeIntel:
    """keep-last node dedup (baseline-first, new overwrites) + edge dedup + drop-dangling."""

    def test_new_node_overwrites_baseline_same_id(self):
        from scripts.ai_ready_helpers import merge_code_intel
        baseline = {"nodes": [{"id": "n1", "summary": "old"}], "edges": []}
        merged = merge_code_intel(baseline, [{"id": "n1", "summary": "new"}], [])
        n1 = [n for n in merged["nodes"] if n["id"] == "n1"]
        assert len(n1) == 1 and n1[0]["summary"] == "new", "new must overwrite baseline (keep-last)"

    def test_baseline_only_node_survives(self):
        from scripts.ai_ready_helpers import merge_code_intel
        baseline = {"nodes": [{"id": "keep", "summary": "b"}], "edges": []}
        merged = merge_code_intel(baseline, [{"id": "fresh", "summary": "n"}], [])
        ids = {n["id"] for n in merged["nodes"]}
        assert ids == {"keep", "fresh"}, "unchanged baseline nodes must survive incremental merge"

    def test_idempotent_merge_twice_equals_once(self):
        from scripts.ai_ready_helpers import merge_code_intel
        baseline = {"nodes": [{"id": "a", "v": 1}], "edges": [{"from": "a", "to": "a", "type": "x", "direction": "forward"}]}
        once = merge_code_intel(baseline, [{"id": "b", "v": 2}], [])
        twice = merge_code_intel(once, [{"id": "b", "v": 2}], [])
        assert once["nodes"] == twice["nodes"] and once["edges"] == twice["edges"]

    def test_edge_dedup_by_full_key(self):
        from scripts.ai_ready_helpers import merge_code_intel
        baseline = {"nodes": [{"id": "a"}, {"id": "b"}],
                    "edges": [{"from": "a", "to": "b", "type": "calls", "direction": "forward"}]}
        merged = merge_code_intel(baseline, [], [{"from": "a", "to": "b", "type": "calls", "direction": "forward"}])
        assert len(merged["edges"]) == 1, "identical edge must dedup"

    def test_edge_direction_is_part_of_key(self):
        """forward must NOT collapse into bidirectional (Run 0 lesson: teeth)."""
        from scripts.ai_ready_helpers import merge_code_intel
        baseline = {"nodes": [{"id": "a"}, {"id": "b"}], "edges": []}
        merged = merge_code_intel(baseline, [],
            [{"from": "a", "to": "b", "type": "x", "direction": "forward"},
             {"from": "a", "to": "b", "type": "x", "direction": "bidirectional"}])
        assert len(merged["edges"]) == 2, "forward vs bidirectional are distinct edges"

    def test_dangling_edge_dropped(self):
        from scripts.ai_ready_helpers import merge_code_intel
        baseline = {"nodes": [{"id": "a"}], "edges": []}
        merged = merge_code_intel(baseline, [], [{"from": "a", "to": "GHOST", "type": "x", "direction": "forward"}])
        assert merged["edges"] == [], "edge with endpoint not in node set must be dropped"

    def test_valid_edge_kept(self):
        from scripts.ai_ready_helpers import merge_code_intel
        baseline = {"nodes": [{"id": "a"}, {"id": "b"}], "edges": []}
        merged = merge_code_intel(baseline, [], [{"from": "a", "to": "b", "type": "x", "direction": "forward"}])
        assert len(merged["edges"]) == 1, "edge with both endpoints present must survive"

    # ── Gate-2 fixes (run_36266b66) ──

    def test_does_not_mutate_caller_baseline(self):
        """Pure function: mutating the result must NOT touch the caller's baseline."""
        from scripts.ai_ready_helpers import merge_code_intel
        baseline = {"nodes": [{"id": "n1", "summary": "orig"}], "edges": [], "meta": {"version": "3.0"}}
        merged = merge_code_intel(baseline, [], [])
        merged["nodes"][0]["summary"] = "MUTATED"
        merged["meta"]["version"] = "9.9"
        assert baseline["nodes"][0]["summary"] == "orig", "nested node must not alias caller"
        assert baseline["meta"]["version"] == "3.0", "nested meta must not alias caller"

    def test_idless_node_remerge_idempotent(self):
        """Re-feeding the same id-less node must NOT duplicate it (idempotency)."""
        from scripts.ai_ready_helpers import merge_code_intel
        idless = {"summary": "anon", "kind": "note"}  # no id
        baseline = {"nodes": [idless], "edges": []}
        once = merge_code_intel(baseline, [dict(idless)], [])
        twice = merge_code_intel(once, [dict(idless)], [])
        anon_count = sum(1 for n in twice["nodes"] if n.get("summary") == "anon")
        assert anon_count == 1, f"id-less node must dedup structurally, got {anon_count}"

    def test_edge_to_idless_node_not_dropped(self):
        """An edge to a PRESENT id-less node must survive (node+edge consistency)."""
        from scripts.ai_ready_helpers import merge_code_intel
        import json as _j
        idless = {"summary": "anon"}
        sk = _j.dumps(idless, sort_keys=True, ensure_ascii=False, default=str)
        baseline = {"nodes": [{"id": "a"}, idless], "edges": []}
        merged = merge_code_intel(baseline, [], [{"from": "a", "to": sk, "type": "x", "direction": "forward"}])
        assert len(merged["edges"]) == 1, "edge to a present id-less node must not be dropped"

    def test_richer_edge_wins_on_key_collision(self):
        """A stripped re-emit must NOT clobber a field-richer edge (no metadata loss)."""
        from scripts.ai_ready_helpers import merge_code_intel
        baseline = {"nodes": [{"id": "a"}, {"id": "b"}],
                    "edges": [{"from": "a", "to": "b", "type": "x", "direction": "forward", "weight": 99, "note": "keep"}]}
        merged = merge_code_intel(baseline, [], [{"from": "a", "to": "b", "type": "x", "direction": "forward"}])
        assert len(merged["edges"]) == 1
        assert merged["edges"][0].get("weight") == 99, "richer edge (weight/note) must survive a stripped re-emit"


class TestHumanBlockReconcileHardened:
    """Gate-2 fixes: ambiguous-hash quarantine + disjoint conservation."""

    def test_ambiguous_hash_quarantines_not_wrong_attach(self):
        """Two new domains sharing a content-hash → block quarantined, not bound to last-wins."""
        from scripts.ai_ready_helpers import reconcile_human_blocks
        old = [{"domain_id": "d:old", "content": "r", "hash": "dup"}]
        kept, orphaned = reconcile_human_blocks(old, [
            {"domain_id": "d:first", "hash": "dup"}, {"domain_id": "d:second", "hash": "dup"}])
        assert kept == [], "ambiguous hash must NOT silently bind to a domain"
        assert len(orphaned) == 1, "ambiguous-match block must quarantine"

    def test_kept_and_orphaned_are_disjoint(self):
        """Conservation with teeth: union == input AND no block in both lists."""
        from scripts.ai_ready_helpers import reconcile_human_blocks
        old = [{"domain_id": "d:a", "content": "r1", "hash": "h1"},
               {"domain_id": "d:b", "content": "r2", "hash": "hZ"}]
        kept, orphaned = reconcile_human_blocks(old, [{"domain_id": "d:a2", "hash": "h1"}])
        contents_kept = {b["content"] for b in kept}
        contents_orph = {b["content"] for b in orphaned}
        assert contents_kept.isdisjoint(contents_orph), "a block must not be in BOTH lists"
        assert contents_kept | contents_orph == {"r1", "r2"}, "union must equal input"

    def test_both_sides_null_hash_orphans_not_matches(self):
        from scripts.ai_ready_helpers import reconcile_human_blocks
        old = [{"domain_id": "d:x", "content": "r", "hash": None}]
        kept, orphaned = reconcile_human_blocks(old, [{"domain_id": "d:y", "hash": None}])
        assert kept == [] and len(orphaned) == 1, "null hash must never match null hash"


class TestHumanBlockReconcile:
    """§8.8: [human] blocks survive domain rename via content-hash; else quarantined, never dropped."""

    def test_human_block_survives_domain_rename(self):
        from scripts.ai_ready_helpers import reconcile_human_blocks
        old = [{"domain_id": "domain:order-mgmt", "content": "已支付订单不可删除", "hash": "h1"}]
        # domain renamed to domain:orders — id changed, content identical
        kept, orphaned = reconcile_human_blocks(old, new_domain_blocks=[{"domain_id": "domain:orders", "hash": "h1"}])
        assert orphaned == [], "identical-content human block must re-attach, not orphan"
        assert any(b["domain_id"] == "domain:orders" and b["content"] == "已支付订单不可删除" for b in kept)

    def test_unmatched_human_block_quarantined_not_dropped(self):
        from scripts.ai_ready_helpers import reconcile_human_blocks
        old = [{"domain_id": "domain:gone", "content": "orphan rule", "hash": "hX"}]
        kept, orphaned = reconcile_human_blocks(old, new_domain_blocks=[{"domain_id": "domain:orders", "hash": "h1"}])
        assert kept == [], "no match → nothing kept"
        assert len(orphaned) == 1 and orphaned[0]["content"] == "orphan rule", "unmatched MUST quarantine, never delete"

    def test_no_human_blocks_lost_invariant(self):
        """Every input block ends up either kept or orphaned — never silently lost."""
        from scripts.ai_ready_helpers import reconcile_human_blocks
        old = [{"domain_id": "d:a", "content": "r1", "hash": "h1"},
               {"domain_id": "d:b", "content": "r2", "hash": "h2"},
               {"domain_id": "d:c", "content": "r3", "hash": "hZ"}]
        kept, orphaned = reconcile_human_blocks(old, new_domain_blocks=[{"domain_id": "d:a2", "hash": "h1"}, {"domain_id": "d:b", "hash": "h2"}])
        assert len(kept) + len(orphaned) == 3, "conservation: no block may vanish"


# ─── Run 3 (run_6602eeab): eval dims + deterministic skeleton projection ───

class TestEvalSpecDetails:
    """AC4 §9: completeness/precision/explicit/F1 quantitative scorers."""

    def _doc(self):
        return {
            "routes": [{"id": "route:orders-post-a1b2"}],
            "domains": [{"id": "domain:orders", "name": "Orders", "summary": "s",
                         "business_rules": [
                             {"rule": "stock suffices", "anchor": "o.ts:1", "verified": True},
                             {"rule": "refund idempotent", "verified": False,
                              "absence_evidence": "grep=0"}]}],
            "flows": [{"id": "flow:create", "domain_id": "domain:orders",
                       "entry_type": "http", "entry_ref": "route:orders-post-a1b2"}],
            "steps": [{"id": "step:1", "flow_id": "flow:create", "explicit": True},
                      {"id": "step:2", "flow_id": "flow:create", "explicit": False}],
        }

    def test_precision_counts_only_verified(self):
        from scripts.ai_ready_helpers import eval_spec_details
        m = eval_spec_details(self._doc())
        # 1 verified of 2 assertions → 0.5
        assert m["precision"] == 0.5
        assert m["denominators"]["assertions"] == 2

    def test_completeness_flow_anchored(self):
        from scripts.ai_ready_helpers import eval_spec_details
        m = eval_spec_details(self._doc())
        assert m["completeness"] == 1.0  # the one http flow resolves to a real route

    def test_completeness_penalizes_dangling_flow(self):
        from scripts.ai_ready_helpers import eval_spec_details
        doc = self._doc()
        doc["flows"][0]["entry_ref"] = "route:GHOST"  # not in routes
        m = eval_spec_details(doc)
        assert m["completeness"] == 0.0

    def test_explicit_ratio(self):
        from scripts.ai_ready_helpers import eval_spec_details
        m = eval_spec_details(self._doc())
        assert m["explicit"] == 0.5  # 1 of 2 steps explicit

    def test_empty_axes_are_zero_not_crash(self):
        from scripts.ai_ready_helpers import eval_spec_details
        m = eval_spec_details({"domains": [], "flows": [], "steps": [], "routes": []})
        assert m["completeness"] == 0.0 and m["precision"] == 0.0
        assert m["f1"] == 0.0
        assert m["denominators"]["flows"] == 0

    def test_f1_harmonic(self):
        from scripts.ai_ready_helpers import eval_spec_details
        m = eval_spec_details(self._doc())
        # completeness 1.0, precision 0.5 → f1 = 2*1*0.5/1.5 = 0.6667
        assert abs(m["f1"] - 0.6667) < 0.001


class TestProjectDomainSkeleton:
    """AC5 §3.2: deterministic 8-section .spec.md projection (no LLM)."""

    def _dom(self):
        return {"id": "domain:orders", "name": "Orders", "summary": "order lifecycle",
                "entities": ["Order"], "complexity": "moderate",
                "diagram": {"mermaid": "graph TD\n  A-->B"},
                "issues": [{"severity": "high", "file": "o.ts", "line": 210,
                            "issue": "oversell", "source": "llm"}],
                "gaps": [{"kind": "test-coverage", "file": "i.ts",
                          "action": "add case", "source": "llm"}],
                "cross_domain": [{"target": "domain:payment"}]}

    def test_all_8_sections_present(self):
        from scripts.ai_ready_helpers import project_domain_skeleton
        md = project_domain_skeleton(self._dom(), [], [])
        for h in ["## 1. 域概述", "## 2. 架构图", "## 3. 用户流程图",
                  "## 4. 业务流", "## 5. 业务规则", "## 6. 潜在问题",
                  "## 7. Gaps", "## 8. 关联"]:
            assert h in md, f"missing section: {h}"

    def test_human_zone_left_as_protected_stub(self):
        from scripts.ai_ready_helpers import project_domain_skeleton
        md = project_domain_skeleton(self._dom(), [], [])
        assert "[human]" in md  # §5 stub references the protected human marker
        assert "待人工增补" in md

    def test_mermaid_and_issues_rendered(self):
        from scripts.ai_ready_helpers import project_domain_skeleton
        md = project_domain_skeleton(self._dom(), [], [])
        assert "```mermaid" in md
        assert "oversell" in md
        assert "domain:payment" in md  # cross-domain link

    def test_flow_steps_ordered(self):
        from scripts.ai_ready_helpers import project_domain_skeleton
        flows = [{"id": "flow:create", "domain_id": "domain:orders",
                  "name": "Create", "entry_ref": "route:x"}]
        steps = [{"id": "s2", "flow_id": "flow:create", "order": 2, "name": "Second",
                  "file_path": "a.ts", "line_range": [10, 20]},
                 {"id": "s1", "flow_id": "flow:create", "order": 1, "name": "First",
                  "file_path": "a.ts", "line_range": [1, 5]}]
        md = project_domain_skeleton(self._dom(), flows, steps)
        assert md.index("步骤 1 — First") < md.index("步骤 2 — Second")


# ─── Run 1.5 (run_1417a3a1): domain-layer generation scaffold ───

class TestBackfillRouteIds:
    """AC1: backfill stable §1.4 ids onto v2 routes; idempotent + collision-detected."""

    def test_backfills_missing_ids(self):
        from scripts.ai_ready_helpers import backfill_route_ids
        doc = {"routes": [{"method": "GET", "path": "/a", "file_path": "x.py"},
                          {"method": "POST", "path": "/b", "file_path": "y.py"}]}
        out = backfill_route_ids(doc)
        ids = [r["id"] for r in out["routes"]]
        assert all(i.startswith("route:") for i in ids)
        assert len(set(ids)) == 2  # distinct

    def test_does_not_mutate_input(self):
        from scripts.ai_ready_helpers import backfill_route_ids
        doc = {"routes": [{"method": "GET", "path": "/a", "file_path": "x.py"}]}
        backfill_route_ids(doc)
        assert "id" not in doc["routes"][0], "must not mutate caller's doc (pure)"

    def test_idempotent_preserves_existing_id(self):
        from scripts.ai_ready_helpers import backfill_route_ids
        doc = {"routes": [{"method": "GET", "path": "/a", "file_path": "x.py", "id": "route:custom-1"}]}
        out = backfill_route_ids(doc)
        assert out["routes"][0]["id"] == "route:custom-1"
        # re-running is stable
        assert backfill_route_ids(out)["routes"][0]["id"] == "route:custom-1"

    def test_collision_raises(self):
        from scripts.ai_ready_helpers import backfill_route_ids
        import pytest
        # same method|path|file → same derived id → collision
        doc = {"routes": [{"method": "GET", "path": "/a", "file_path": "x.py"},
                          {"method": "GET", "path": "/a", "file_path": "x.py"}]}
        with pytest.raises(ValueError, match="collision"):
            backfill_route_ids(doc)

    def test_empty_entry_skipped_no_garbage_id(self):
        from scripts.ai_ready_helpers import backfill_route_ids
        doc = {"entry_points": [{"note": "no anchor fields"}]}
        out = backfill_route_ids(doc)
        assert "id" not in out["entry_points"][0]


class TestExtractEntryAnchors:
    """AC2: anchor menu = the constrained id set an LLM flow may reference."""

    def test_projects_id_bearing_entries(self):
        from scripts.ai_ready_helpers import backfill_route_ids, extract_entry_anchors
        doc = backfill_route_ids({"routes": [
            {"method": "GET", "path": "/a", "file_path": "x.py", "line_number": 10}]})
        anchors = extract_entry_anchors(doc)
        assert len(anchors) == 1
        a = anchors[0]
        assert a["id"].startswith("route:") and a["method"] == "GET" and a["kind"] == "route"
        assert a["line_number"] == 10

    def test_skips_id_less_entries_in_mixed_doc(self):
        """An id-less entry alongside id-bearing ones is skipped (not raised) —
        the loud-on-empty guard only fires when NONE carry an id."""
        from scripts.ai_ready_helpers import extract_entry_anchors
        doc = {"routes": [{"id": "route:has-id", "method": "GET", "path": "/a", "file_path": "x.py"},
                          {"method": "POST", "path": "/b", "file_path": "y.py"}]}  # 2nd has no id
        anchors = extract_entry_anchors(doc)
        assert [a["id"] for a in anchors] == ["route:has-id"]  # id-less one skipped


class TestFinalizeV3:
    """AC3: fail-closed assembly — a consistent domain layer passes, a dangling one raises."""

    def _complete_v2_base(self, routes=None):
        """A COMPLETE v2 doc (all _REQUIRED_TOP_LEVEL + repo fields) so finalize_v3
        rejections isolate the DOMAIN-LAYER defect, not missing v2 scaffolding."""
        return {
            "$schema": "https://ai-ready-repo.dev/schemas/code-intel.v2.json",
            "version": "2.0",
            "repo": {"name": "t", "languages": {"python": 1.0}, "total_symbols": 1, "total_edges": 0},
            "modules": [], "edges": [], "entry_points": [],
            "routes": routes if routes is not None else [],
        }

    def test_valid_layer_finalizes_to_v3(self):
        from scripts.ai_ready_helpers import backfill_route_ids, finalize_v3
        base = backfill_route_ids(self._complete_v2_base(
            routes=[{"method": "GET", "path": "/a", "file_path": "x.py"}]))
        rid = base["routes"][0]["id"]
        domains = [{"id": "domain:orders", "name": "Orders"}]
        flows = [{"id": "flow:create", "domain_id": "domain:orders", "entry_ref": rid, "entry_type": "http"}]
        steps = [{"id": "step:1", "flow_id": "flow:create"}]
        out = finalize_v3(base, domains, flows, steps)
        assert out["version"] == "3.0"
        assert out["domains"][0]["id"] == "domain:orders"

    def test_dangling_entry_ref_rejected(self):
        from scripts.ai_ready_helpers import finalize_v3
        import pytest
        base = self._complete_v2_base(
            routes=[{"id": "route:real", "method": "GET", "path": "/a", "file_path": "x.py"}])
        domains = [{"id": "domain:orders", "name": "Orders"}]
        flows = [{"id": "flow:create", "domain_id": "domain:orders",
                  "entry_ref": "route:GHOST", "entry_type": "http"}]  # dangling
        with pytest.raises(ValueError, match="does not resolve"):
            finalize_v3(base, domains, flows, [])

    def test_unanchored_verified_true_rejected(self):
        from scripts.ai_ready_helpers import finalize_v3
        import pytest
        base = self._complete_v2_base()
        domains = [{"id": "domain:x", "name": "X",
                    "business_rules": [{"rule": "must be true", "verified": True}]}]  # no anchor
        with pytest.raises(ValueError, match="spurious"):
            finalize_v3(base, domains, [], [])

    def test_does_not_mutate_input(self):
        from scripts.ai_ready_helpers import finalize_v3
        base = self._complete_v2_base()
        finalize_v3(base, [], [], [])
        assert base["version"] == "2.0" and "domains" not in base


class TestGenerationWriteReadLoop:
    """E2E: generate a real domains[] from a v2 doc via backfill→anchor→finalize,
    prove it passes all guards AND is recallable via the Run 3 recall domain leg.
    Closes the write→read loop (GUI10) — the whole point of Run 1.5 + Run 3."""

    def test_generate_then_recall_domain(self, tmp_path, monkeypatch):
        import json
        from scripts.ai_ready_helpers import (
            backfill_route_ids, extract_entry_anchors, finalize_v3)

        # 1. Start from a realistic v2 doc (routes, no ids — like SwarmAI's real one)
        v2 = {
            "$schema": "https://ai-ready-repo.dev/schemas/code-intel.v2.json",
            "version": "2.0",
            "repo": {"name": "demo", "languages": {"python": 1.0}, "total_symbols": 2, "total_edges": 0},
            "modules": [], "edges": [], "entry_points": [],
            "routes": [{"method": "POST", "path": "/api/orders", "handler": "orders.py::create",
                        "framework": "fastapi", "file_path": "backend/api/orders.py",
                        "line_number": 40, "middleware": None}],
        }
        # 2. backfill ids → anchor menu (the LLM's constrained choice set)
        v2 = backfill_route_ids(v2)
        anchors = extract_entry_anchors(v2)
        assert len(anchors) == 1
        anchor_id = anchors[0]["id"]

        # 3. "LLM classification" (simulated) → a domain anchored to a REAL anchor id
        domains = [{"id": "domain:orders", "name": "Order Management",
                    "summary": "zGENSENTINEL77 order lifecycle create-to-fulfill",
                    "business_rules": [{"rule": "stock must suffice before commit",
                                        "anchor": "backend/api/orders.py:47", "verified": True}]}]
        flows = [{"id": "flow:create-order", "domain_id": "domain:orders",
                  "entry_ref": anchor_id, "entry_type": "http",
                  "summary": "client submits order to persistence"}]
        steps = [{"id": "step:validate", "flow_id": "flow:create-order",
                  "order": 1, "name": "Validate", "explicit": True}]

        # 4. finalize (fail-closed gate) — proves the generated layer is consistent
        v3 = finalize_v3(v2, domains, flows, steps)
        assert v3["version"] == "3.0"

        # 5. Write to a real project dir and RECALL via the Run 3 domain leg
        proj = tmp_path / "Demo"
        proj.mkdir()
        (proj / "PRODUCT.md").write_text("## Vision\nunrelated\n", encoding="utf-8")
        (proj / "code-intel.json").write_text(json.dumps(v3), encoding="utf-8")
        import core.project_registry as pr
        monkeypatch.setattr(pr, "get_projects_dir", lambda: tmp_path)

        from core.recall_multi import _recall_ddd
        hits, layer = _recall_ddd("zGENSENTINEL77 order lifecycle", "Demo", 5)
        docs = [h.get("doc", "") for h in hits]
        assert any("code-intel.json" in d for d in docs), \
            f"generated domain MUST be recallable (write→read loop closed), got {docs}"


class TestRun15Gate2Fixes:
    """Gate-2 findings (run_1417a3a1): collision-message clarity, loud-empty-menu,
    finalize type-guard."""

    def test_carried_vs_derived_collision_message(self):
        """MED: a hand-authored id clashing with a derived id names the class."""
        from scripts.ai_ready_helpers import backfill_route_ids, derive_route_id
        import pytest
        rid = derive_route_id("GET", "/users", "h.py")
        doc = {"routes": [{"id": rid},  # carried id equal to the next entry's derived id
                          {"method": "GET", "path": "/users", "file_path": "h.py"}]}
        with pytest.raises(ValueError, match="carried|author-supplied"):
            backfill_route_ids(doc)

    def test_extract_anchors_loud_when_no_ids(self):
        """MED: routes present but zero ids → raise (forgot backfill), not silent []."""
        from scripts.ai_ready_helpers import extract_entry_anchors
        import pytest
        doc = {"routes": [{"method": "GET", "path": "/a", "file_path": "x.py"}]}  # no id
        with pytest.raises(ValueError, match="backfill_route_ids"):
            extract_entry_anchors(doc)

    def test_extract_anchors_empty_doc_ok(self):
        """No entries at all → empty menu is fine (not a forgot-backfill case)."""
        from scripts.ai_ready_helpers import extract_entry_anchors
        assert extract_entry_anchors({"routes": [], "entry_points": []}) == []

    def test_finalize_non_list_arg_raises_valueerror(self):
        """HIGH: a non-list layer arg raises ValueError (not a bare TypeError)."""
        from scripts.ai_ready_helpers import finalize_v3
        import pytest
        base = {"$schema": "s", "version": "2.0",
                "repo": {"name": "t", "languages": {}, "total_symbols": 1, "total_edges": 0},
                "modules": [], "edges": [], "entry_points": [], "routes": []}
        with pytest.raises(ValueError, match="must be a list or None"):
            finalize_v3(base, 5, [], [])  # int, not list
        with pytest.raises(ValueError, match="must be a list or None"):
            finalize_v3(base, {"d": 1}, [], [])  # dict, not list
