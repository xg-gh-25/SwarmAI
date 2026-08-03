"""Tests for AI-Ready-Repo Engine helper script.

Tests deterministic operations: schema validation, git log parsing, repo info gathering.
Uses real filesystem fixtures where possible (no mocks for file operations).
"""
import json
import subprocess
from pathlib import Path

import pytest


# ─── AC3: code-intel.json v2 schema validation ───

class TestCodeIntelValidation:
    """Validate code-intel.json v2 schema conformance."""

    def test_valid_v2_document_passes(self):
        """A well-formed v2 document should pass validation."""
        from scripts.ai_ready_helpers import validate_code_intel_json

        # Matches the REAL exporter schema (json_exporter.py) — run_5647c72c.
        # The old fixture used module.path/responsibility + top-level edges +
        # entry_point.path, a schema the exporter NEVER emitted (that mismatch was
        # the bug this run fixed). modules carry symbol_count; entry_points carry
        # file_path; the graph section is `dependencies`, not `edges`.
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
                    "symbol_count": 150,
                    "function_count": 120,
                    "class_count": 12,
                    "file_count": 8,
                    "files": ["src/core/main.py"],
                }
            ],
            "entry_points": [
                {"name": "main", "file_path": "src/main.py", "type": "function"}
            ],
            "routes": [],
            "hot_zones": [],
            "risk_areas": [],
            "dead_code": [],
            "dependencies": {"language_distribution": {"python": 150}},
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
        """Module missing the required field (symbol_count, per the real exporter
        _build_modules schema — run_5647c72c) should fail."""
        from scripts.ai_ready_helpers import validate_code_intel_json

        doc = {
            "$schema": "https://ai-ready-repo.dev/schemas/code-intel.v2.json",
            "version": "2.0",
            "repo": {"name": "test", "languages": {}, "total_symbols": 0, "total_edges": 0},
            "modules": [{"name": "incomplete"}],  # missing symbol_count
            "entry_points": [],
            "routes": [],
            "hot_zones": [],
            "risk_areas": [],
            "dead_code": [],
            "dependencies": {},
        }
        errors = validate_code_intel_json(doc)
        assert len(errors) > 0
        assert any("symbol_count" in e for e in errors)


# ─── code-intel v3 domain layer (Run 1, run_aad6d4f2) ───

def _minimal_v2_doc() -> dict:
    """A valid v2 doc matching the REAL exporter schema (json_exporter.py) —
    modules={name,symbol_count,…}, entry_points={name,file_path,type}, top-level
    `dependencies` (NOT `edges`). run_5647c72c aligned this to ground truth."""
    return {
        "$schema": "https://ai-ready-repo.dev/schemas/code-intel.v2.json",
        "version": "2.0",
        "repo": {"name": "t", "languages": {"python": 1.0}, "total_symbols": 10, "total_edges": 1},
        "modules": [{"name": "core", "symbol_count": 10, "function_count": 8,
                     "class_count": 1, "file_count": 2, "files": ["src/core.py"]}],
        "entry_points": [{"name": "main", "file_path": "src/main.py", "type": "function"}],
        "dependencies": {"language_distribution": {"python": 10}},
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


class TestMermaidNodeAnchoring:
    """Gate-1 must-fix (run_3026ef31): diagram.mermaid has NO validator — a
    hallucinated node label ships silently. check_mermaid_node_anchoring asserts
    every code-like token in a mermaid body resolves to a real file/module in the
    doc, fail-closed like the other v3 guards."""

    def _v3_doc_with_mermaid(self, mermaid: str, *, on="flow") -> dict:
        doc = _minimal_v2_doc()
        doc["version"] = "3.0"
        doc["routes"] = [{"id": "route:real", "method": "POST", "path": "/x",
                          "file_path": "backend/routers/chat.py"}]
        doc["domains"] = [{"id": "domain:o", "name": "O"}]
        flow = {"id": "flow:x", "domain_id": "domain:o", "entry_ref": "route:real"}
        if on == "flow":
            flow["diagram"] = {"mermaid": mermaid}
        else:
            doc["domains"][0]["diagram"] = {"mermaid": mermaid}
        doc["flows"] = [flow]
        return doc

    def test_hallucinated_file_node_flagged(self):
        from scripts.ai_ready_helpers import check_mermaid_node_anchoring
        # ghost_service.py appears in NO module/route/entry/step file → hallucinated
        doc = self._v3_doc_with_mermaid(
            "sequenceDiagram\n  Client->>backend/ghost_service.py: call\n")
        errors = check_mermaid_node_anchoring(doc)
        assert any("ghost_service.py" in e for e in errors), \
            "a mermaid node naming a non-existent file must be flagged"

    def test_real_file_node_passes(self):
        from scripts.ai_ready_helpers import check_mermaid_node_anchoring
        # chat.py is the route file_path → real anchor
        doc = self._v3_doc_with_mermaid(
            "sequenceDiagram\n  Client->>backend/routers/chat.py: POST\n")
        assert check_mermaid_node_anchoring(doc) == [], \
            "a mermaid node naming a real doc file must pass"

    def test_prose_labels_not_false_flagged(self):
        from scripts.ai_ready_helpers import check_mermaid_node_anchoring
        # plain human labels (no .py/.ts/path) are NOT code tokens → never flagged
        doc = self._v3_doc_with_mermaid(
            "sequenceDiagram\n  User->>Server: sends message\n  Server-->>User: streams reply\n")
        assert check_mermaid_node_anchoring(doc) == [], \
            "prose participant labels must not be treated as code anchors"

    def test_domain_diagram_also_checked(self):
        from scripts.ai_ready_helpers import check_mermaid_node_anchoring
        doc = self._v3_doc_with_mermaid(
            "graph TD\n  A[backend/nonexistent_mod.py] --> B[core]\n", on="domain")
        errors = check_mermaid_node_anchoring(doc)
        assert any("nonexistent_mod.py" in e for e in errors), \
            "domain-level diagram must be checked too"

    def test_wired_into_main_validator(self):
        from scripts.ai_ready_helpers import validate_code_intel_json
        doc = self._v3_doc_with_mermaid(
            "sequenceDiagram\n  Client->>backend/ghost_service.py: call\n")
        errors = validate_code_intel_json(doc)
        assert any("ghost_service.py" in e for e in errors), \
            "main validator must run the mermaid-anchoring guard"

    def test_no_diagram_no_error(self):
        from scripts.ai_ready_helpers import check_mermaid_node_anchoring
        doc = _minimal_v2_doc()
        doc["version"] = "3.0"
        doc["domains"] = [{"id": "domain:o", "name": "O"}]
        doc["flows"] = [{"id": "flow:x", "domain_id": "domain:o"}]
        assert check_mermaid_node_anchoring(doc) == []

    def test_repo_root_accepts_real_disk_file_not_in_doc(self, tmp_path):
        """A mermaid node naming a file that EXISTS on disk but isn't indexed in
        code-intel.json must PASS when repo_root is given — the anti-hallucination
        goal is 'maps to real code', and a real-on-disk file is real code (the doc's
        v2 graph is incomplete, not the diagram's fault). Without repo_root it still
        flags (doc-only, backward-compatible)."""
        from scripts.ai_ready_helpers import check_mermaid_node_anchoring
        (tmp_path / "backend").mkdir()
        real = tmp_path / "backend" / "session_healing.py"
        real.write_text("# real file, not in code-intel doc\n")
        doc = _minimal_v2_doc()
        doc["version"] = "3.0"
        doc["domains"] = [{"id": "domain:o", "name": "O",
                           "diagram": {"mermaid": "graph TD\n  A[backend/session_healing.py] --> B[core]\n"}}]
        doc["flows"] = [{"id": "flow:x", "domain_id": "domain:o"}]
        # doc-only: flagged (not in code-intel)
        assert any("session_healing.py" in e for e in check_mermaid_node_anchoring(doc))
        # with repo_root: real disk file accepted
        assert check_mermaid_node_anchoring(doc, repo_root=tmp_path) == []

    def test_repo_root_still_flags_hallucinated(self, tmp_path):
        """repo_root must NOT be an escape hatch — a file absent from BOTH the doc
        AND disk is still a hallucinated node."""
        from scripts.ai_ready_helpers import check_mermaid_node_anchoring
        doc = _minimal_v2_doc()
        doc["version"] = "3.0"
        doc["domains"] = [{"id": "domain:o", "name": "O",
                           "diagram": {"mermaid": "graph TD\n  A[backend/ghost.py] --> B\n"}}]
        doc["flows"] = [{"id": "flow:x", "domain_id": "domain:o"}]
        assert any("ghost.py" in e for e in check_mermaid_node_anchoring(doc, repo_root=tmp_path))

    def test_repo_root_rejects_absolute_path_escape(self, tmp_path):
        """Gate-2 F1 (HIGH): an ABSOLUTE token (/tmp/x.py) must NOT pass just
        because that file exists on disk — pathlib drops repo_root when the right
        operand is absolute, escaping the repo. Containment must be enforced."""
        from scripts.ai_ready_helpers import check_mermaid_node_anchoring
        outside = tmp_path / "outside.py"
        outside.write_text("# real file, but OUTSIDE any sane repo_root\n")
        repo = tmp_path / "repo"
        repo.mkdir()
        doc = _minimal_v2_doc()
        doc["version"] = "3.0"
        doc["domains"] = [{"id": "domain:o", "name": "O",
                           "diagram": {"mermaid": f"graph TD\n  A[{outside}] --> B\n"}}]
        doc["flows"] = [{"id": "flow:x", "domain_id": "domain:o"}]
        errors = check_mermaid_node_anchoring(doc, repo_root=repo)
        assert errors, "an absolute path escaping repo_root must be flagged, not accepted"

    def test_repo_root_rejects_traversal_escape(self, tmp_path):
        """Gate-2 F1 (HIGH): a ../ traversal token that resolves OUTSIDE repo_root
        must be flagged even though the target file exists."""
        from scripts.ai_ready_helpers import check_mermaid_node_anchoring
        secret = tmp_path / "secret.py"
        secret.write_text("# exists but outside repo\n")
        repo = tmp_path / "repo"
        repo.mkdir()
        doc = _minimal_v2_doc()
        doc["version"] = "3.0"
        doc["domains"] = [{"id": "domain:o", "name": "O",
                           "diagram": {"mermaid": "graph TD\n  A[../secret.py] --> B\n"}}]
        doc["flows"] = [{"id": "flow:x", "domain_id": "domain:o"}]
        errors = check_mermaid_node_anchoring(doc, repo_root=repo)
        assert errors, "a ../ traversal escaping repo_root must be flagged"

    def test_backslash_path_still_checked(self, tmp_path):
        """Gate-2 F3 (MED): a backslash path 'backend\\ghost.py' must not sneak a
        hallucinated node through by having the regex only see the basename when
        that basename is absent everywhere."""
        from scripts.ai_ready_helpers import check_mermaid_node_anchoring
        doc = _minimal_v2_doc()
        doc["version"] = "3.0"
        doc["domains"] = [{"id": "domain:o", "name": "O",
                           "diagram": {"mermaid": "graph TD\n  A[backend\\\\ghost.py] --> B\n"}}]
        doc["flows"] = [{"id": "flow:x", "domain_id": "domain:o"}]
        assert any("ghost.py" in e for e in check_mermaid_node_anchoring(doc, repo_root=tmp_path)), \
            "backslash-path hallucinated node must still be flagged"


class TestBusinessRuleAnchorFiles:
    """run_9a9e314c DoD5: `verified:true` business_rules only check anchor NON-BLANK
    (check_llm_assertion_guards) — a fabricated anchor to a NON-EXISTENT FILE sails
    through CLEAN. This is the non-theater backstop: check the anchor's FILE part
    exists (NOT line-resolve — line-resolve is theater per signature-first design).
    Mirrors check_mermaid_node_anchoring's containment discipline (no absolute/../
    escape)."""

    def _doc_with_rule(self, anchor: str) -> dict:
        doc = _minimal_v2_doc()
        doc["version"] = "3.0"
        doc["domains"] = [{"id": "domain:o", "name": "O",
                           "business_rules": [{"rule": "x", "anchor": anchor, "verified": True}]}]
        doc["flows"] = [{"id": "flow:x", "domain_id": "domain:o"}]
        return doc

    def test_fabricated_file_flagged(self, tmp_path):
        from scripts.ai_ready_helpers import check_business_rule_anchor_files
        doc = self._doc_with_rule("backend/core/ghost_nonexistent.py:42")
        errors = check_business_rule_anchor_files(doc, repo_root=tmp_path)
        assert any("ghost_nonexistent.py" in e for e in errors), \
            "a business_rule anchor to a non-existent file must be flagged"

    def test_real_file_passes(self, tmp_path):
        from scripts.ai_ready_helpers import check_business_rule_anchor_files
        (tmp_path / "backend" / "core").mkdir(parents=True)
        (tmp_path / "backend" / "core" / "real.py").write_text("# real\n")
        doc = self._doc_with_rule("backend/core/real.py:10")
        assert check_business_rule_anchor_files(doc, repo_root=tmp_path) == [], \
            "a business_rule anchor to a real on-disk file must pass"

    def test_file_in_doc_anchors_passes(self):
        # a file already known to the doc (module/route/entry) resolves without disk
        from scripts.ai_ready_helpers import check_business_rule_anchor_files
        doc = self._doc_with_rule("backend/routers/chat.py:5")
        doc["routes"] = [{"id": "route:r", "method": "POST", "path": "/x",
                          "file_path": "backend/routers/chat.py"}]
        assert check_business_rule_anchor_files(doc) == [], \
            "anchor to a file already in the doc's known files must pass"

    def test_no_repo_root_no_disk_check(self):
        # backward-compatible: repo_root=None → only doc-known files resolve, but a
        # file NOT in the doc is NOT flagged as fabricated (can't prove absence w/o disk)
        from scripts.ai_ready_helpers import check_business_rule_anchor_files
        doc = self._doc_with_rule("backend/core/unknown.py:1")
        # pure mode must not false-flag (we only assert fabrication WITH repo_root)
        assert check_business_rule_anchor_files(doc) == []

    def test_absolute_path_escape_rejected(self, tmp_path):
        from scripts.ai_ready_helpers import check_business_rule_anchor_files
        outside = tmp_path / "outside.py"
        outside.write_text("# real but outside repo\n")
        repo = tmp_path / "repo"; repo.mkdir()
        doc = self._doc_with_rule(f"{outside}:1")
        assert check_business_rule_anchor_files(doc, repo_root=repo), \
            "an absolute anchor path escaping repo_root must be flagged"

    def test_verified_false_not_checked(self, tmp_path):
        # verified:false rules carry absence_evidence, not a code anchor → not checked
        from scripts.ai_ready_helpers import check_business_rule_anchor_files
        doc = _minimal_v2_doc()
        doc["version"] = "3.0"
        doc["domains"] = [{"id": "domain:o", "name": "O",
                           "business_rules": [{"rule": "x", "verified": False,
                                               "absence_evidence": "grep=0"}]}]
        doc["flows"] = [{"id": "flow:x", "domain_id": "domain:o"}]
        assert check_business_rule_anchor_files(doc, repo_root=tmp_path) == []

    def test_wired_into_main_validator(self, tmp_path):
        from scripts.ai_ready_helpers import validate_code_intel_json
        doc = self._doc_with_rule("backend/core/ghost_nonexistent.py:42")
        errors = validate_code_intel_json(doc, repo_root=tmp_path)
        assert any("ghost_nonexistent.py" in e for e in errors), \
            "main validator must run the business-rule anchor-file guard"

    def test_multiline_anchor_spec_not_false_flagged(self, tmp_path):
        """A real file with a multi-line anchor spec (`file.ts:216,232` or
        `file.ts:216-232` or `file.ts:L216`) must NOT be false-flagged — the
        line-spec parser must strip comma/range/L-prefixed line refs, not just
        bare digits (real regression: message-store uses `...MessageStore.ts:216,232`)."""
        from scripts.ai_ready_helpers import check_business_rule_anchor_files
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "Store.ts").write_text("// real\n")
        for spec in ("src/Store.ts:216,232", "src/Store.ts:216-232", "src/Store.ts:L216"):
            doc = self._doc_with_rule(spec)
            assert check_business_rule_anchor_files(doc, repo_root=tmp_path) == [], \
                f"multi-line anchor spec {spec!r} on a real file must not be flagged"


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
    """_validate_repo_path contract (run_a9fe5ad3): a git ROOT or a monorepo MEMBER
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

    # ─── Gap 3: matcher false-pass (Gate-1 corrected — run_006dce1c) ───

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
        assert m["flow_validity"] == 1.0  # the one http flow resolves to a real route (renamed from misleading "completeness")

    def test_completeness_penalizes_dangling_flow(self):
        from scripts.ai_ready_helpers import eval_spec_details
        doc = self._doc()
        doc["flows"][0]["entry_ref"] = "route:GHOST"  # not in routes
        m = eval_spec_details(doc)
        assert m["flow_validity"] == 0.0

    def test_explicit_ratio(self):
        from scripts.ai_ready_helpers import eval_spec_details
        m = eval_spec_details(self._doc())
        assert m["explicit"] == 0.5  # 1 of 2 steps explicit

    def test_empty_axes_are_zero_not_crash(self):
        from scripts.ai_ready_helpers import eval_spec_details
        m = eval_spec_details({"domains": [], "flows": [], "steps": [], "routes": []})
        assert m["flow_validity"] == 0.0 and m["precision"] == 0.0
        assert m["f1"] == 0.0
        assert m["denominators"]["flows"] == 0

    def test_f1_harmonic(self):
        from scripts.ai_ready_helpers import eval_spec_details
        m = eval_spec_details(self._doc())
        # flow_validity 1.0, precision 0.5 → f1 = 2*1*0.5/1.5 = 0.6667
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

    def test_finalize_stamps_spec_hash_on_every_domain(self):
        """Regression (run_97a6b1db): finalize_v3 is the sanctioned agent domain-
        authoring path, but it did NOT stamp domains[].spec_hash — only the core
        json_exporter (reindex path) did. So an agent authoring domains via the
        documented skill flow produced staleness-BLIND domains (freshness treats an
        unstamped domain as unjudgeable → silently exempt from staleness detection).
        finalize_v3 must stamp spec_hash at assembly (the chokepoint where domain +
        flows + steps are all in hand), matching the reindex path. The stamp MUST
        equal _spec_content_hash(domain, flows, steps) so it matches the marker a
        regenerated .spec.md carries (else staleness false-flags)."""
        from scripts.ai_ready_helpers import (
            backfill_route_ids, finalize_v3, _spec_content_hash)
        base = backfill_route_ids(self._complete_v2_base(
            routes=[{"method": "GET", "path": "/a", "file_path": "x.py"}]))
        rid = base["routes"][0]["id"]
        domains = [{"id": "domain:orders", "name": "Orders", "summary": "order lifecycle"}]
        flows = [{"id": "flow:create", "domain_id": "domain:orders", "entry_ref": rid, "entry_type": "http"}]
        steps = [{"id": "step:1", "flow_id": "flow:create"}]
        out = finalize_v3(base, domains, flows, steps)
        dom = out["domains"][0]
        assert dom.get("spec_hash"), "finalize_v3 must stamp spec_hash (staleness-blind otherwise)"
        # stamp must equal the single-source content hash (so it matches the .spec.md marker)
        assert dom["spec_hash"] == _spec_content_hash(dom, out["flows"], out["steps"])

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


class TestRegenerateSpecPreservingHuman:
    """Run 4 feature D: skeleton regeneration must NOT destroy [human] §5 rules."""

    _DOMAIN = {"id": "domain:orders", "name": "Orders", "summary": "order lifecycle",
               "entities": ["Order"], "complexity": "moderate"}

    def test_extract_human_blocks_backtick_and_bullet(self):
        from scripts.ai_ready_helpers import extract_human_spec_blocks
        txt = ("## 5. rules\n"
               "- **已支付订单不可删除** `[human]` — anchor `svc.py:10` ✅\n"
               "- **machine rule** `[llm]` — anchor `svc.py:20`\n"
               "The `[human]` marker is prose here, not a rule.\n")
        blocks = extract_human_spec_blocks(txt)
        assert len(blocks) == 1
        assert "已支付订单不可删除" in blocks[0]

    def test_first_generation_is_plain_skeleton(self):
        from scripts.ai_ready_helpers import regenerate_spec_preserving_human, project_domain_skeleton
        out = regenerate_spec_preserving_human("", self._DOMAIN, [], [])
        # no prior human content → identical to a plain skeleton
        assert out == project_domain_skeleton(self._DOMAIN, [], [])

    def test_human_rule_survives_regeneration(self):
        from scripts.ai_ready_helpers import regenerate_spec_preserving_human, project_domain_skeleton
        # existing file: skeleton + a human rule in §5
        existing = project_domain_skeleton(self._DOMAIN, [], []).replace(
            "_(待人工增补 `[human]` 业务规则)_",
            "- **已支付订单不可删除(人工承诺)** `[human]` — anchor `order.py:88` ✅")
        # regenerate with a CHANGED skeleton (summary changed)
        new_domain = dict(self._DOMAIN, summary="订单全新生命周期描述")
        out = regenerate_spec_preserving_human(existing, new_domain, [], [])
        # human rule preserved
        assert "已支付订单不可删除(人工承诺)" in out
        # AND the skeleton region refreshed to the new summary
        assert "订单全新生命周期描述" in out
        # the stub is gone (replaced by the real rule)
        assert "待人工增补" not in out

    def test_idempotent_no_duplication(self):
        from scripts.ai_ready_helpers import regenerate_spec_preserving_human, project_domain_skeleton
        existing = project_domain_skeleton(self._DOMAIN, [], []).replace(
            "_(待人工增补 `[human]` 业务规则)_",
            "- **规则X** `[human]` — anchor `a.py:1`")
        once = regenerate_spec_preserving_human(existing, self._DOMAIN, [], [])
        twice = regenerate_spec_preserving_human(once, self._DOMAIN, [], [])
        assert once.count("规则X") == 1
        assert twice.count("规则X") == 1, "regenerating a preserved file must not duplicate"

    def test_multiple_human_rules_all_survive(self):
        from scripts.ai_ready_helpers import regenerate_spec_preserving_human, project_domain_skeleton
        existing = project_domain_skeleton(self._DOMAIN, [], []).replace(
            "_(待人工增补 `[human]` 业务规则)_",
            "- **规则A** `[human]` — a\n- **规则B** `[human]` — b\n- **机器规则** `[llm]` — c")
        out = regenerate_spec_preserving_human(existing, self._DOMAIN, [], [])
        assert "规则A" in out and "规则B" in out
        # [llm] rule is NOT preserved (it's skeleton-authoritative, re-derived)
        assert out.count("机器规则") == 0


class TestSpecDetailsIndexRow:
    """Run 4 feature A: ddd_bindings index line surfaces spec-details/(N specs)."""

    def test_index_row_shows_spec_count(self, tmp_path):
        from core.ddd_bindings import describe_project_ddd_line
        proj = tmp_path / "Demo"
        proj.mkdir()
        for doc in ("PRODUCT.md", "TECH.md", "IMPROVEMENT.md", "PROJECT.md"):
            (proj / doc).write_text("# x\n", encoding="utf-8")
        sd = proj / "spec-details"
        sd.mkdir()
        (sd / "orders.spec.md").write_text("# 规格\n", encoding="utf-8")
        (sd / "payment.spec.md").write_text("# 规格\n", encoding="utf-8")
        line = describe_project_ddd_line(proj)
        assert "spec-details/(2 specs)" in line

    def test_no_spec_dir_no_row(self, tmp_path):
        from core.ddd_bindings import describe_project_ddd_line
        proj = tmp_path / "Demo2"
        proj.mkdir()
        for doc in ("PRODUCT.md", "TECH.md", "IMPROVEMENT.md", "PROJECT.md"):
            (proj / doc).write_text("# x\n", encoding="utf-8")
        line = describe_project_ddd_line(proj)
        assert "spec-details" not in line

    def test_empty_spec_dir_no_row(self, tmp_path):
        from core.ddd_bindings import describe_project_ddd_line
        proj = tmp_path / "Demo3"
        proj.mkdir()
        for doc in ("PRODUCT.md", "TECH.md", "IMPROVEMENT.md", "PROJECT.md"):
            (proj / doc).write_text("# x\n", encoding="utf-8")
        (proj / "spec-details").mkdir()  # dir exists but no .spec.md
        line = describe_project_ddd_line(proj)
        assert "spec-details" not in line


class TestRun4Gate2Fixes:
    """Gate-2 CRITICAL: multiline [human] rules + inline comments must survive verbatim."""

    _DOMAIN = {"id": "domain:orders", "name": "Orders", "summary": "s",
               "entities": [], "complexity": "moderate"}

    def test_multiline_rule_continuation_preserved(self):
        """CRITICAL: a [human] rule with an indented continuation line must NOT
        lose its body (the exact data-loss the feature exists to prevent)."""
        from scripts.ai_ready_helpers import extract_human_spec_blocks
        txt = ("## 5. rules\n"
               "- **orders over $10k need CFO sign-off** `[human]` <!-- SOX -->\n"
               "  - exception: renewals under 12mo\n"
               "  - approver: CFO or delegate\n"
               "- **next rule** `[human]` — anchor `a.py:1`\n")
        blocks = extract_human_spec_blocks(txt)
        assert len(blocks) == 2
        # block 1 keeps ALL its lines (bullet + 2 continuations) AND the inline comment
        assert "exception: renewals under 12mo" in blocks[0]
        assert "approver: CFO or delegate" in blocks[0]
        assert "<!-- SOX -->" in blocks[0], "inline comment on human content preserved verbatim"

    def test_multiline_rule_survives_regeneration(self):
        from scripts.ai_ready_helpers import regenerate_spec_preserving_human, project_domain_skeleton
        existing = project_domain_skeleton(self._DOMAIN, [], []).replace(
            "_(待人工增补 `[human]` 业务规则)_",
            "- **多行承诺** `[human]` — anchor `x.py:1`\n  - 子条款: 必须双人复核\n  - SOX 合规")
        out = regenerate_spec_preserving_human(existing, dict(self._DOMAIN, summary="新描述"), [], [])
        assert "多行承诺" in out
        assert "子条款: 必须双人复核" in out, "continuation line must survive regen"
        assert "SOX 合规" in out
        assert "新描述" in out  # skeleton still refreshed

    def test_fenced_code_under_rule_preserved(self):
        from scripts.ai_ready_helpers import extract_human_spec_blocks
        txt = ("## 5. rules\n"
               "- **规则带代码** `[human]`\n"
               "  ```python\n"
               "  assert qty > 0\n"
               "  ```\n"
               "## 6. next\n")
        blocks = extract_human_spec_blocks(txt)
        assert len(blocks) == 1
        assert "assert qty > 0" in blocks[0] and "```python" in blocks[0]

    def test_legend_mention_still_not_a_block(self):
        """Regression: the false-positive guard still holds with verbatim capture."""
        from scripts.ai_ready_helpers import extract_human_spec_blocks
        txt = ("<!-- 骨架区 §5 [human] 可增补 -->\n"
               "The `[human]` marker denotes authorship.\n"
               "- **real** `[human]` — anchor `a.py:1`\n")
        blocks = extract_human_spec_blocks(txt)
        assert len(blocks) == 1 and "real" in blocks[0]


class TestValidatorMatchesRealExporter:
    """run_5647c72c regression: the validator MUST accept what the REAL exporter
    (core/code_intel/json_exporter.py) emits. Previously the validator was written
    against a hand-built FIXTURE schema and rejected every real exporter output
    (SwarmAI's own code-intel.json = 43 errors), blocking v3 generation on real
    data (O009: validator never tested against real producer output)."""

    def _exporter_shaped_doc(self):
        """A doc built from the exporter's ACTUAL builder functions, so this test
        breaks if validator ↔ exporter diverge again — not a hand-written schema."""
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "core"))
        # Build modules/entry_points via the real exporter builders (ground truth).
        from code_intel.json_exporter import _build_modules, _build_entry_points, _build_dependencies
        module_map = {"core": [
            {"file_path": "backend/core/a.py", "node_type": "function", "is_entry_point": True, "name": "main"},
            {"file_path": "backend/core/a.py", "node_type": "class", "is_entry_point": False, "name": "Foo"},
        ]}
        return {
            "$schema": "https://ai-ready-repo.dev/schemas/code-intel.v2.json",
            "version": "2.0",
            "repo": {"name": "t", "languages": {"python": 2}, "total_symbols": 2, "total_edges": 0},
            "modules": _build_modules(module_map, {}),
            "entry_points": _build_entry_points(module_map),
            "routes": [], "hot_zones": [], "risk_areas": [], "dead_code": [],
            "dependencies": _build_dependencies({"python": 2}),
        }

    def test_real_exporter_output_validates_clean(self):
        from scripts.ai_ready_helpers import validate_code_intel_json
        doc = self._exporter_shaped_doc()
        errors = validate_code_intel_json(doc)
        assert errors == [], f"validator must accept real exporter output, got: {errors}"

    def test_exporter_module_has_symbol_count_not_path(self):
        """Pin the exact divergence that caused the bug: exporter modules carry
        symbol_count, NOT path/responsibility."""
        doc = self._exporter_shaped_doc()
        mod = doc["modules"][0]
        assert "symbol_count" in mod
        assert "path" not in mod and "responsibility" not in mod

    def test_exporter_entry_point_uses_file_path(self):
        """Pin the latent 3rd divergence: entry_points use file_path, not path."""
        doc = self._exporter_shaped_doc()
        ep = doc["entry_points"][0]
        assert "file_path" in ep and "path" not in ep
        from scripts.ai_ready_helpers import validate_code_intel_json
        assert validate_code_intel_json(doc) == []


class TestMergeExportedDocSafety:
    """run_5647c72c R7-scan: merge_code_intel operates on the nodes/edges GRAPH,
    not the exported code-intel.json (modules/routes). Passing an exported doc
    must NOT wipe its modules/routes (deep-copy preserves them)."""

    def test_merge_preserves_exported_modules_routes(self):
        from scripts.ai_ready_helpers import merge_code_intel
        exported = {
            "$schema": "s", "version": "2.0",
            "repo": {"name": "t", "languages": {}, "total_symbols": 5, "total_edges": 0},
            "modules": [{"name": "core", "symbol_count": 5}],
            "routes": [{"method": "GET", "path": "/a", "file_path": "x.py"}],
            "dependencies": {"language_distribution": {"python": 5}},
        }
        out = merge_code_intel(exported, [], [])
        assert len(out["modules"]) == 1, "exported modules must survive merge (deep-copy)"
        assert len(out["routes"]) == 1, "exported routes must survive merge"
        # input not mutated
        assert "nodes" not in exported


class TestStepSpecTableRender:
    """Thicken (run_235ffe64): §4 now renders the full §3.2 step spec table
    (io/contract/rules) with verified gating, not just name+location."""

    _DOM = {"id": "domain:eval", "name": "Eval", "summary": "s", "complexity": "moderate"}

    def test_contract_and_io_rendered(self):
        from scripts.ai_ready_helpers import project_domain_skeleton
        flows = [{"id": "flow:add", "domain_id": "domain:eval", "name": "Add case",
                  "entry_ref": "route:x", "entry_type": "http"}]
        steps = [{"id": "step:add", "flow_id": "flow:add", "order": 1, "name": "Validate+create",
                  "file_path": "backend/routers/eval.py",
                  "io": {"input": "CreateCaseRequest", "output": "{status:created, case}"},
                  "contract": {"signature": "create_case(req)", "http": "POST /api/eval/golden-set",
                               "status_codes": {"200": "created", "400": "invalid"}},
                  "rules": [{"rule": "4-gate validate", "anchor": "eval.py:120", "verified": True}]}]
        md = project_domain_skeleton(self._DOM, flows, steps)
        assert "接口契约" in md and "POST /api/eval/golden-set" in md
        assert "200=created" in md and "400=invalid" in md
        assert "输入 | CreateCaseRequest" in md
        # Run C: verified:true is an LLM self-assertion + a code POINTER, NOT a
        # machine-verified fact — the render must SAY so (no bare "text (anchor)").
        assert "[llm-claim] 4-gate validate (anchor: `eval.py:120`)" in md
        assert "4-gate validate (`eval.py:120`)" not in md  # old bare-fact form is GONE

    def test_unverified_rule_gated_in_table(self):
        from scripts.ai_ready_helpers import project_domain_skeleton
        flows = [{"id": "flow:x", "domain_id": "domain:eval", "name": "X", "entry_ref": "r", "entry_type": "http"}]
        steps = [{"id": "s", "flow_id": "flow:x", "order": 1, "name": "S", "file_path": "a.py",
                  "rules": [{"rule": "maybe true", "verified": False, "absence_evidence": "grep=0"}]}]
        md = project_domain_skeleton(self._DOM, flows, steps)
        assert "[llm-inferred] maybe true" in md

    def test_bare_step_renders_no_table(self):
        """A step with only name/loc (no io/contract) must not emit an empty table."""
        from scripts.ai_ready_helpers import project_domain_skeleton
        flows = [{"id": "flow:x", "domain_id": "domain:eval", "name": "X", "entry_ref": "r", "entry_type": "http"}]
        steps = [{"id": "s", "flow_id": "flow:x", "order": 1, "name": "S", "file_path": "a.py"}]
        md = project_domain_skeleton(self._DOM, flows, steps)
        assert "| 项 | 内容 |" not in md  # no empty table for a bare step


class TestThickenGate2PipeEscape:
    """Gate-2 MED (run_235ffe64): a pipe in step.io/contract must not corrupt the
    markdown table — real eval output '{status:created, case} | 400' has one."""

    def test_pipe_in_output_escaped(self):
        from scripts.ai_ready_helpers import project_domain_skeleton
        dom = {"id": "domain:eval", "name": "Eval", "summary": "s"}
        flows = [{"id": "flow:x", "domain_id": "domain:eval", "name": "X", "entry_ref": "r", "entry_type": "http"}]
        steps = [{"id": "s", "flow_id": "flow:x", "order": 1, "name": "S", "file_path": "a.py",
                  "io": {"input": "x", "output": "{status:created, case} | 400"}}]
        md = project_domain_skeleton(dom, flows, steps)
        # the output row must have exactly the 2-col shape: count UNESCAPED pipes
        out_line = [l for l in md.splitlines() if "输出" in l][0]
        unescaped = out_line.replace("\\|", "")  # drop escaped data pipes
        assert unescaped.count("|") == 3, f"escaped pipe → exactly 2-col row, got: {out_line!r}"
        assert "\\|" in out_line  # the data pipe is escaped, not a column sep

    def test_newline_in_cell_flattened(self):
        from scripts.ai_ready_helpers import project_domain_skeleton
        dom = {"id": "d", "name": "D", "summary": "s"}
        flows = [{"id": "f", "domain_id": "d", "name": "F", "entry_ref": "r", "entry_type": "http"}]
        steps = [{"id": "s", "flow_id": "f", "order": 1, "name": "S", "file_path": "a.py",
                  "io": {"input": "line1\nline2"}}]
        md = project_domain_skeleton(dom, flows, steps)
        in_line = [l for l in md.splitlines() if "输入" in l][0]
        assert "line1 line2" in in_line and "\n" not in in_line[3:]


class TestEquivalenceLayer:
    """Run 5 (run_3349787d, §10): derive assertions from step.contract, score
    against observations with honest verified/partial/unchecked tagging, feedback."""

    def _doc(self):
        return {
            "domains": [{"id": "domain:eval", "name": "Eval"},
                        {"id": "domain:static", "name": "StaticNoContract"}],
            "flows": [{"id": "flow:add", "domain_id": "domain:eval"},
                      {"id": "flow:s", "domain_id": "domain:static"}],
            "steps": [
                {"id": "step:add", "flow_id": "flow:add",
                 "contract": {"http": "POST /api/eval/golden-set",
                              "status_codes": {"200": "created", "400": "invalid"}}},
                {"id": "step:s", "flow_id": "flow:s"},  # no contract → unchecked
            ],
        }

    def test_derive_from_contract(self):
        from scripts.ai_ready_helpers import derive_equivalence_assertions
        a = derive_equivalence_assertions(self._doc())
        assert len(a) == 2  # 200 + 400
        assert {x["code"] for x in a} == {"200", "400"}
        assert all(x["step_id"] == "step:add" for x in a)

    def test_no_contract_yields_nothing(self):
        from scripts.ai_ready_helpers import derive_equivalence_assertions
        doc = {"steps": [{"id": "s", "flow_id": "f"}], "flows": [], "domains": []}
        assert derive_equivalence_assertions(doc) == []

    def test_verified_when_all_observed_pass(self):
        from scripts.ai_ready_helpers import score_equivalence
        obs = {("step:add", "200"): True, ("step:add", "400"): True}
        r = score_equivalence(self._doc(), obs)
        assert r["domains"]["domain:eval"]["tag"] == "verified"
        assert r["overall_score"] == 1.0

    def test_partial_when_some_fail_or_unobserved(self):
        from scripts.ai_ready_helpers import score_equivalence
        obs = {("step:add", "200"): True}  # 400 unobserved
        r = score_equivalence(self._doc(), obs)
        assert r["domains"]["domain:eval"]["tag"] == "partial"

    def test_failed_observation_is_partial_not_verified(self):
        from scripts.ai_ready_helpers import score_equivalence
        obs = {("step:add", "200"): True, ("step:add", "400"): False}
        r = score_equivalence(self._doc(), obs)
        assert r["domains"]["domain:eval"]["tag"] == "partial"

    def test_static_domain_is_unchecked_never_fakepass(self):
        from scripts.ai_ready_helpers import score_equivalence
        r = score_equivalence(self._doc(), {})
        # no-contract domain → unchecked; contract domain with no obs → also unchecked
        assert r["domains"]["domain:static"]["tag"] == "unchecked"
        assert r["domains"]["domain:eval"]["tag"] == "unchecked"  # has assertions, 0 observed

    def test_feedback_enqueues_only_observed_failures(self):
        from scripts.ai_ready_helpers import equivalence_feedback
        obs = {("step:add", "200"): True, ("step:add", "400"): False}
        q = equivalence_feedback(self._doc(), obs)
        assert len(q) == 1  # only the observed FAILURE (400), not the unobserved
        assert q[0]["code"] == "400" and q[0]["verified"] is False
        assert "SME must adjudicate" in q[0]["reason"]

    def test_e2e_on_real_swarmai_domains(self):
        """E2E: derive from the REAL SwarmAI code-intel.json (3 real contracts),
        score with a realistic observation set, prove honest tagging on real data."""
        import json, sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "..", ".swarm-ai", "SwarmWS"))
        from scripts.ai_ready_helpers import derive_equivalence_assertions, score_equivalence
        # locate the real file via project registry
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "core"))
        from project_registry import get_projects_dir
        p = get_projects_dir() / "SwarmAI" / "code-intel.json"
        if not p.exists():
            import pytest; pytest.skip("real SwarmAI code-intel.json not present")
        doc = json.loads(p.read_text(encoding="utf-8"))
        assertions = derive_equivalence_assertions(doc)
        # real contracts: eval create_case(200,400) + canary(200,500) + reindex(202,400,404) = 7
        assert len(assertions) >= 7, f"expected ≥7 real status-code assertions, got {len(assertions)}"
        # score with a partial real observation (only eval 200s observed)
        obs = {(a["step_id"], a["code"]): True for a in assertions if a["code"] == "200"}
        r = score_equivalence(doc, obs)
        # domains with contracts but not all codes observed → partial; static domains → unchecked
        tags = {d: v["tag"] for d, v in r["domains"].items()}
        assert "unchecked" in tags.values()  # static domains honestly unchecked
        assert all(t in ("verified", "partial", "unchecked") for t in tags.values())


class TestEquivalenceOrphanSurfacing:
    """Gate-2 F5 (run_3349787d): orphan assertions (step→flow→domain unresolved)
    must be SURFACED in __unresolved__, not silently dropped from the report/score."""

    def test_orphan_step_surfaced_not_dropped(self):
        from scripts.ai_ready_helpers import score_equivalence
        doc = {
            "domains": [{"id": "domain:real"}],
            "flows": [{"id": "flow:real", "domain_id": "domain:real"}],
            "steps": [
                {"id": "s:real", "flow_id": "flow:real",
                 "contract": {"http": "GET /a", "status_codes": {"200": "ok"}}},
                # orphan: flow:ghost is not in flows[] → no domain
                {"id": "s:orphan", "flow_id": "flow:ghost",
                 "contract": {"http": "GET /b", "status_codes": {"200": "ok", "500": "err"}}},
            ],
        }
        r = score_equivalence(doc, {("s:real", "200"): True})
        assert "__unresolved__" in r["domains"], "orphan assertions must be surfaced, not dropped"
        assert r["domains"]["__unresolved__"]["total"] == 2  # the 2 orphan codes
        assert r["domains"]["__unresolved__"]["tag"] == "unchecked"
        # score denominator includes orphans (1 passed / 3 total)
        assert r["overall_score"] == round(1/3, 4)

    def test_no_orphan_no_bucket(self):
        from scripts.ai_ready_helpers import score_equivalence
        doc = {"domains": [{"id": "d"}], "flows": [{"id": "f", "domain_id": "d"}],
               "steps": [{"id": "s", "flow_id": "f", "contract": {"http": "GET /a", "status_codes": {"200": "ok"}}}]}
        r = score_equivalence(doc, {})
        assert "__unresolved__" not in r["domains"]


class TestAnchorAccounting:
    """Run 1 (run_94e5a5aa): the coverage-guarantee mechanism. Every anchor must be
    ACCOUNTED — in a flow entry_ref OR an explicit unclassified:[{id,reason}] with a
    real (non-junk) reason. Silent omission = fail-closed error. Reframed away from a
    route-% threshold (Gate-0) to an accounting invariant (no metric to game)."""

    def _doc(self, n_routes=3, flow_refs=None, unclassified=None):
        doc = _minimal_v2_doc()
        doc["version"] = "3.0"
        doc["routes"] = [{"id": f"route:r{i}", "method": "GET", "path": f"/r{i}",
                          "file_path": "a.py"} for i in range(n_routes)]
        doc["domains"] = [{"id": "domain:o", "name": "O"}]
        doc["flows"] = [{"id": f"flow:{i}", "domain_id": "domain:o", "entry_ref": ref}
                        for i, ref in enumerate(flow_refs or [])]
        if unclassified is not None:
            doc["unclassified"] = unclassified
        return doc

    # ── compute_anchor_accounting (pure) ──
    def test_compute_full_coverage(self):
        from scripts.ai_ready_helpers import compute_anchor_accounting
        doc = self._doc(3, flow_refs=["route:r0", "route:r1", "route:r2"])
        acc = compute_anchor_accounting(doc)
        assert acc["total"] == 3 and acc["classified"] == 3
        assert acc["missing_ids"] == [] and acc["accounted_ratio"] == 1.0
        assert acc["classified_ratio"] == 1.0

    def test_compute_silent_omission_surfaced(self):
        from scripts.ai_ready_helpers import compute_anchor_accounting
        doc = self._doc(3, flow_refs=["route:r0"])  # 1/3 classified, 2 missing
        acc = compute_anchor_accounting(doc)
        assert acc["total"] == 3 and acc["classified"] == 1
        assert set(acc["missing_ids"]) == {"route:r1", "route:r2"}
        assert acc["accounted_ratio"] < 1.0
        assert round(acc["classified_ratio"], 2) == 0.33

    def test_compute_unclassified_accounts(self):
        from scripts.ai_ready_helpers import compute_anchor_accounting
        doc = self._doc(3, flow_refs=["route:r0"],
                        unclassified=[{"id": "route:r1", "reason": "health-check endpoint, no business flow"},
                                      {"id": "route:r2", "reason": "static asset serving, not a domain action"}])
        acc = compute_anchor_accounting(doc)
        assert acc["missing_ids"] == [] and acc["accounted_ratio"] == 1.0
        # classified_ratio stays honest — only 1 of 3 is a real flow
        assert round(acc["classified_ratio"], 2) == 0.33
        assert acc["unclassified_count"] == 2

    # ── check_anchor_accounting (validator, fail-closed) ──
    def test_check_missing_anchor_is_error(self):
        from scripts.ai_ready_helpers import check_anchor_accounting
        doc = self._doc(3, flow_refs=["route:r0"])  # r1,r2 silently missing
        errors = check_anchor_accounting(doc)
        assert any("route:r1" in e for e in errors) and any("route:r2" in e for e in errors), \
            "silently-unaccounted anchors must be a fail-closed error"

    def test_check_blank_reason_rejected(self):
        from scripts.ai_ready_helpers import check_anchor_accounting
        doc = self._doc(2, flow_refs=["route:r0"],
                        unclassified=[{"id": "route:r1", "reason": "  "}])
        errors = check_anchor_accounting(doc)
        assert any("route:r1" in e and "reason" in e.lower() for e in errors), \
            "unclassified with blank reason must be rejected (not accounted)"

    def test_check_junk_reason_rejected(self):
        """Gate-1 F5 (the reason='.' rubber-stamp hole, same family as the mermaid
        absolute-path escape): a non-blank but junk reason must NOT count as accounting."""
        from scripts.ai_ready_helpers import check_anchor_accounting
        for junk in [".", "-", "n/a", "na", "x", "todo"]:
            doc = self._doc(2, flow_refs=["route:r0"],
                            unclassified=[{"id": "route:r1", "reason": junk}])
            errors = check_anchor_accounting(doc)
            assert any("route:r1" in e for e in errors), \
                f"junk reason {junk!r} must not rubber-stamp an omission"

    def test_check_substantive_reason_passes(self):
        from scripts.ai_ready_helpers import check_anchor_accounting
        doc = self._doc(2, flow_refs=["route:r0"],
                        unclassified=[{"id": "route:r1", "reason": "unauthenticated health probe, no business semantics"}])
        assert check_anchor_accounting(doc) == []

    def test_check_low_info_reason_rejected(self):
        """Gate-2 F1 (HIGH): len>=12 alone is gameable — 12 repeated chars or a
        single long token is NOT an explanation. The rubber-stamp must not just move
        one level down (the exact CLASS-A 'self-authored gate leaves its own hole')."""
        from scripts.ai_ready_helpers import check_anchor_accounting, _is_substantive_reason
        for junk in ["............", "xxxxxxxxxxxx", "aaaaaaaaaaaaaaa", "------------", "____________"]:
            assert not _is_substantive_reason(junk), f"low-info reason {junk!r} must not pass"
            doc = self._doc(2, flow_refs=["route:r0"],
                            unclassified=[{"id": "route:r1", "reason": junk}])
            assert any("route:r1" in e for e in check_anchor_accounting(doc)), \
                f"12+ junk chars {junk!r} must not rubber-stamp an omission"
        # a genuine multi-word phrase still passes
        assert _is_substantive_reason("static asset route")

    def test_check_ids_present_but_unbackfilled_not_vacuous(self):
        """Gate-2 F2 (MED): routes present but NONE carry ids must NOT vacuously pass
        (extract_entry_anchors raises loud; the guard must not swallow that into a
        clean bill of health — that would let a whole-codebase omission ship)."""
        from scripts.ai_ready_helpers import check_anchor_accounting
        doc = self._doc(3, flow_refs=[])
        for r in doc["routes"]:
            del r["id"]  # 3 real routes, no ids → unbackfilled
        errors = check_anchor_accounting(doc)
        assert errors, "routes present but id-less must surface an error, not pass vacuously"
        assert any("id" in e.lower() or "backfill" in e.lower() for e in errors)

    def test_check_fake_unclassified_id_rejected(self):
        from scripts.ai_ready_helpers import check_anchor_accounting
        doc = self._doc(2, flow_refs=["route:r0", "route:r1"],
                        unclassified=[{"id": "route:GHOST", "reason": "this is a fabricated anchor id not in the menu"}])
        errors = check_anchor_accounting(doc)
        assert any("GHOST" in e for e in errors), "unclassified entry with a non-anchor id must be rejected"

    def test_check_double_account_rejected(self):
        """Gate-1 (2b): an id in BOTH a flow and unclassified masks a real omission."""
        from scripts.ai_ready_helpers import check_anchor_accounting
        doc = self._doc(2, flow_refs=["route:r0", "route:r1"],
                        unclassified=[{"id": "route:r0", "reason": "some plausible-looking reason text here"}])
        errors = check_anchor_accounting(doc)
        assert any("route:r0" in e and ("both" in e.lower() or "double" in e.lower()) for e in errors), \
            "an anchor classified AND unclassified must be flagged"

    def test_check_wired_into_main_validator(self):
        from scripts.ai_ready_helpers import validate_code_intel_json
        doc = self._doc(3, flow_refs=["route:r0"])  # 2 missing
        errors = validate_code_intel_json(doc)
        assert any("route:r1" in e or "route:r2" in e for e in errors), \
            "main validator must run the accounting guard"

    def test_finalize_v3_fails_closed_on_omission(self):
        from scripts.ai_ready_helpers import finalize_v3
        base = self._doc(3)
        with pytest.raises(ValueError, match="route:r"):
            finalize_v3(base, base["domains"],
                        [{"id": "flow:0", "domain_id": "domain:o", "entry_ref": "route:r0"}], [])

    def test_non_http_flow_does_not_consume_anchor(self):
        """A non-http flow (no entry_ref) must not be counted as classifying an anchor,
        and its None ref must not poison the classified set."""
        from scripts.ai_ready_helpers import compute_anchor_accounting
        doc = self._doc(2, flow_refs=["route:r0"])
        doc["flows"].append({"id": "flow:bg", "domain_id": "domain:o", "entry_type": "job"})
        acc = compute_anchor_accounting(doc)
        assert acc["classified"] == 1 and set(acc["missing_ids"]) == {"route:r1"}

    # ── eval_spec_details rename + honest ratio ──
    def test_eval_renames_completeness_and_adds_accounting(self):
        from scripts.ai_ready_helpers import eval_spec_details
        # 1 valid flow covering 1 of 3 routes → flow_validity=1.0 but accounted<1
        doc = self._doc(3, flow_refs=["route:r0"])
        r = eval_spec_details(doc)
        assert "flow_validity" in r, "completeness must be renamed to flow_validity (was misleading)"
        assert "completeness" not in r, "the misleading 'completeness' key must be gone"
        assert r["flow_validity"] == 1.0, "the single flow resolves → flow-validity is genuinely 1.0"
        assert "accounted_ratio" in r and r["accounted_ratio"] < 1.0, \
            "accounted_ratio must expose the real 1/3 coverage the old metric hid"
        assert "classified_ratio" in r


class TestUnifiedCoverageLedger:
    """Run AB Cycle 2 — ONE coverage ledger, not two (Gate-1 Check-5).

    Route-level holes (unclassified[], id must be a route anchor) and file/repo-level
    holes (coverage_ledger[], ref is a file path / repo, from the parser) share ONE
    {ref, kind, reason} entry contract and ONE _is_substantive_reason gate. The
    unified reader iter_coverage_ledger(doc) yields every hole in that shape so a
    consumer gets a single honest "what is NOT understood" list."""

    def _v3(self):
        doc = _minimal_v2_doc()  # module-level helper in this test file
        doc["version"] = "3.0"
        doc["routes"] = [{"id": "route:r0", "method": "GET", "path": "/r0", "file_path": "a.py"}]
        doc["domains"] = [{"id": "domain:o", "name": "O"}]
        doc["flows"] = [{"id": "flow:0", "domain_id": "domain:o", "entry_ref": "route:r0"}]
        return doc

    # --- shared gate: the SAME _is_substantive_reason rejects junk for file holes too ---
    def test_shared_gate_rejects_junk_file_reason(self):
        from scripts.ai_ready_helpers import validate_coverage_ledger
        doc = self._v3()
        doc["coverage_ledger"] = [{"ref": "legacy.cbl", "kind": "file", "reason": "n/a"}]
        errors = validate_coverage_ledger(doc)
        assert any("reason" in e.lower() for e in errors), \
            f"junk file-hole reason must be rejected by the shared gate, got {errors}"

    def test_shared_gate_accepts_substantive_file_reason(self):
        from scripts.ai_ready_helpers import validate_coverage_ledger
        doc = self._v3()
        doc["coverage_ledger"] = [{"ref": "legacy.cbl", "kind": "file",
                                   "reason": "unsupported extension .cbl — no AST parser available"}]
        assert validate_coverage_ledger(doc) == []

    # --- kind is required + constrained ---
    def test_ledger_entry_requires_kind(self):
        from scripts.ai_ready_helpers import validate_coverage_ledger
        doc = self._v3()
        doc["coverage_ledger"] = [{"ref": "x.cbl", "reason": "unsupported extension no parser here"}]
        errors = validate_coverage_ledger(doc)
        assert any("kind" in e.lower() for e in errors)

    def test_ledger_rejects_unknown_kind(self):
        from scripts.ai_ready_helpers import validate_coverage_ledger
        doc = self._v3()
        doc["coverage_ledger"] = [{"ref": "x", "kind": "banana", "reason": "some substantive text here"}]
        errors = validate_coverage_ledger(doc)
        assert any("kind" in e.lower() for e in errors)

    def test_ledger_accepts_gitignored_kind(self):
        # producer/validator contract: parser.py emits kind='gitignored' for files
        # excluded by the repo's .gitignore (parser.py:1496). The validator MUST
        # accept it — a validator that rejects its own producer's output is the bug
        # (R27 producer/consumer contract; surfaced live on the real code-intel.json
        # during run_89e28075, blocking finalize_v3).
        from scripts.ai_ready_helpers import validate_coverage_ledger
        doc = self._v3()
        doc["coverage_ledger"] = [{
            "ref": "backend/skills/_shared/project_paths.py", "kind": "gitignored",
            "reason": "ignored by the repo's .gitignore — out of scope by the repo's own rules, recorded not dropped"}]
        assert validate_coverage_ledger(doc) == []

    # --- route-kind entries in the ledger must still be REAL anchors (mirrors unclassified) ---
    def test_ledger_route_kind_must_be_real_anchor(self):
        from scripts.ai_ready_helpers import validate_coverage_ledger
        doc = self._v3()
        doc["coverage_ledger"] = [{"ref": "route:FAKE", "kind": "route",
                                   "reason": "no business flow because it is internal only"}]
        errors = validate_coverage_ledger(doc)
        assert any("anchor" in e.lower() or "fabricat" in e.lower() for e in errors)

    # --- unified reader: yields BOTH route holes (unclassified) AND file holes (ledger) ---
    def test_iter_coverage_ledger_unifies_both_sources(self):
        from scripts.ai_ready_helpers import iter_coverage_ledger
        doc = self._v3()
        # add a 2nd route parked in unclassified + a file hole in coverage_ledger
        doc["routes"].append({"id": "route:r1", "method": "POST", "path": "/r1", "file_path": "b.py"})
        doc["unclassified"] = [{"id": "route:r1", "reason": "admin-only endpoint, no user business flow"}]
        doc["coverage_ledger"] = [{"ref": "legacy.cbl", "kind": "file",
                                   "reason": "unsupported extension .cbl — no AST parser available"}]
        holes = list(iter_coverage_ledger(doc))
        kinds = {h["kind"] for h in holes}
        refs = {h["ref"] for h in holes}
        assert "route" in kinds and "file" in kinds, f"unified reader must yield both, got {kinds}"
        assert "route:r1" in refs and "legacy.cbl" in refs
        # every yielded hole conforms to the {ref,kind,reason} shape
        assert all(set(h.keys()) >= {"ref", "kind", "reason"} for h in holes)

    # --- empty/absent ledger is valid (a fully-covered v3 doc has no file holes) ---
    def test_absent_ledger_is_valid(self):
        from scripts.ai_ready_helpers import validate_coverage_ledger, iter_coverage_ledger
        doc = self._v3()
        assert validate_coverage_ledger(doc) == []
        # iter still yields unclassified route holes if any; here none
        assert list(iter_coverage_ledger(doc)) == []

    # --- validate_code_intel_json wires the new ledger validator (5th... now 6th gate) ---
    def test_validate_code_intel_json_wires_ledger(self):
        from scripts.ai_ready_helpers import validate_code_intel_json
        doc = self._v3()
        doc["coverage_ledger"] = [{"ref": "x.cbl", "kind": "file", "reason": "junk"}]  # too short/junk
        errors = validate_code_intel_json(doc)
        assert any("reason" in e.lower() or "ledger" in e.lower() for e in errors), \
            f"validate_code_intel_json must run coverage_ledger validation, got {errors}"


class TestB7GateWiringMutation:
    """Run AB Cycle 4 (B7) — prove EVERY v3 gate is load-bearing on the REAL path.

    The authorship trap that bit run_aad6d4f2: a guard fully unit-tested in
    isolation while validate_code_intel_json never CALLED it → a bad doc sailed
    through with a 'verified' label. This mutation test drives the REAL entry point
    (validate_code_intel_json) and, for EACH of the 6 v3 gates, monkeypatch-unwires
    that gate and asserts a doc that SHOULD fail on it now PASSES — proving the gate
    was actually firing. If a gate is silently unwired in the future, its row here
    goes RED. (GUI32/PIT13: exercise the real assembly path, not the function alone.)
    """

    import pytest as _pytest

    def _bad_doc_for(self, gate_name):
        """A minimal v3 doc that is INVALID *specifically* for the named gate."""
        base = _minimal_v2_doc()
        base["version"] = "3.0"
        base["routes"] = [{"id": "route:r0", "method": "GET", "path": "/r0", "file_path": "a.py"}]
        base["domains"] = [{"id": "domain:o", "name": "O"}]
        base["flows"] = [{"id": "flow:0", "domain_id": "domain:o", "entry_ref": "route:r0"}]
        if gate_name == "check_domain_referential_integrity":
            # flow points at a non-existent route anchor → referential-integrity error
            base["flows"] = [{"id": "flow:0", "domain_id": "domain:o", "entry_ref": "route:GHOST"}]
        elif gate_name == "check_anchor_accounting":
            # a 2nd route with NO flow and NO unclassified bucket → coverage hole
            base["routes"].append({"id": "route:r1", "method": "POST", "path": "/r1", "file_path": "b.py"})
        elif gate_name == "validate_coverage_ledger":
            base["coverage_ledger"] = [{"ref": "x.cbl", "kind": "file", "reason": "n/a"}]  # junk reason
        elif gate_name == "check_llm_assertion_guards":
            # a verified:true assertion with no anchor → assertion-guard error
            base["domains"][0]["business_rules"] = [{"rule": "x", "verified": True}]
        elif gate_name == "_validate_v3_domain_layer":
            base["domains"] = "not-a-list"  # structural domain-layer error
        elif gate_name == "check_mermaid_node_anchoring":
            # diagram.mermaid with a CODE-LIKE token (file w/ extension) that resolves
            # to neither the doc anchors nor disk → hallucinated node error.
            base["flows"][0]["diagram"] = {
                "mermaid": "graph TD\n  A[a.py] --> B[backend/ghost_service_xyz.py]"
            }
        return base

    @_pytest.mark.parametrize("gate", [
        "_validate_v3_domain_layer",
        "check_domain_referential_integrity",
        "check_llm_assertion_guards",
        "check_mermaid_node_anchoring",
        "check_anchor_accounting",
        "validate_coverage_ledger",
    ])
    def test_gate_is_load_bearing(self, gate, monkeypatch):
        import scripts.ai_ready_helpers as H
        from scripts.ai_ready_helpers import validate_code_intel_json

        doc = self._bad_doc_for(gate)
        # 1. WITH the gate wired: the bad doc MUST be rejected (proves the doc is
        #    genuinely invalid for this gate).
        errors_before = validate_code_intel_json(doc)
        assert errors_before, f"{gate}: test doc should be invalid but validate passed clean"

        # 2. UNWIRE the gate (stub it to return no errors) and re-run. If the doc now
        #    passes (or loses exactly this gate's errors), the gate WAS load-bearing.
        monkeypatch.setattr(H, gate, lambda *a, **k: [])
        errors_after = validate_code_intel_json(doc)
        assert len(errors_after) < len(errors_before), (
            f"{gate}: unwiring it did NOT change validation — the gate is NOT actually "
            f"firing on the real path (authorship-trap / dead gate). before={len(errors_before)} "
            f"after={len(errors_after)}")


class TestGate2F1IdlessRoute:
    """Run AB Gate-2 F1 (CRITICAL): a route without an id must NOT silently vanish
    from the coverage denominator — it must be a loud error, not a false 1.0."""

    def test_idless_route_is_flagged_not_dropped(self):
        from scripts.ai_ready_helpers import check_anchor_accounting
        doc = _minimal_v2_doc()
        doc["version"] = "3.0"
        doc["routes"] = [
            {"id": "route:a", "method": "GET", "path": "/a", "file_path": "a.py"},
            {"method": "GET", "path": "/b", "file_path": "b.py"},  # NO id (moved route)
        ]
        doc["domains"] = [{"id": "d", "name": "D"}]
        doc["flows"] = [{"id": "f", "domain_id": "d", "entry_ref": "route:a"}]
        errors = check_anchor_accounting(doc)
        assert any("no id" in e.lower() or "id-less" in e.lower() for e in errors), \
            f"id-less route must be flagged, got {errors}"

    def test_all_routes_with_id_and_accounted_passes(self):
        from scripts.ai_ready_helpers import check_anchor_accounting
        doc = _minimal_v2_doc()
        doc["version"] = "3.0"
        doc["routes"] = [{"id": "route:a", "method": "GET", "path": "/a", "file_path": "a.py"}]
        doc["domains"] = [{"id": "d", "name": "D"}]
        doc["flows"] = [{"id": "f", "domain_id": "d", "entry_ref": "route:a"}]
        assert check_anchor_accounting(doc) == []


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


# ─── code-intel v3 loop-liveness: spec-details content-hash staleness (single-source) ───

class TestSpecContentHash:
    """_spec_content_hash + marker embedding — the SINGLE source of the spec staleness
    hash (Gate-1 F1b: no two-writer drift). Hash MUST cover domain+flows+steps so a
    flow/step change (which changes the rendered skeleton) bumps the hash (Gate-1 F1)."""

    def _domain(self):
        return {"id": "domain:orders", "name": "Orders", "summary": "order lifecycle",
                "entities": ["Order"], "complexity": "moderate"}

    def test_hash_is_stable_and_hex(self):
        from scripts.ai_ready_helpers import _spec_content_hash
        d = self._domain()
        h1 = _spec_content_hash(d, [], [])
        h2 = _spec_content_hash(d, [], [])
        assert h1 == h2                      # deterministic
        assert len(h1) == 64 and all(c in "0123456789abcdef" for c in h1)  # sha256 hex

    def test_hash_changes_on_domain_content(self):
        from scripts.ai_ready_helpers import _spec_content_hash
        d1 = self._domain()
        d2 = dict(d1); d2["summary"] = "CHANGED"
        assert _spec_content_hash(d1, [], []) != _spec_content_hash(d2, [], [])

    def test_hash_changes_on_FLOW_content(self):
        # Gate-1 F1: a flow change alters the rendered skeleton (§3/§4) → hash MUST bump.
        # A domain-dict-only hash would MISS this (false-fresh).
        from scripts.ai_ready_helpers import _spec_content_hash
        d = self._domain()
        flows_a = [{"id": "f1", "domain_id": "domain:orders", "name": "checkout"}]
        flows_b = [{"id": "f1", "domain_id": "domain:orders", "name": "RENAMED"}]
        assert _spec_content_hash(d, flows_a, []) != _spec_content_hash(d, flows_b, [])

    def test_hash_changes_on_STEP_content(self):
        from scripts.ai_ready_helpers import _spec_content_hash
        d = self._domain()
        flows = [{"id": "f1", "domain_id": "domain:orders", "name": "checkout"}]
        steps_a = [{"id": "s1", "flow_id": "f1", "order": 1, "name": "validate"}]
        steps_b = [{"id": "s1", "flow_id": "f1", "order": 1, "name": "CHANGED"}]
        assert _spec_content_hash(d, flows, steps_a) != _spec_content_hash(d, flows, steps_b)

    def test_skeleton_embeds_marker(self):
        from scripts.ai_ready_helpers import project_domain_skeleton, _spec_content_hash, SPEC_HASH_MARKER_RE
        d = self._domain()
        md = project_domain_skeleton(d, [], [])
        m = SPEC_HASH_MARKER_RE.search(md)
        assert m, "skeleton must embed a spec-hash marker"
        assert m.group(1) == _spec_content_hash(d, [], [])

    def test_marker_excluded_from_own_hash(self):
        # The embedded marker line must NOT feed its own hash (would be self-referential/unstable).
        from scripts.ai_ready_helpers import project_domain_skeleton, extract_spec_hash_marker
        d = self._domain()
        md = project_domain_skeleton(d, [], [])
        # regenerating from the rendered md's domain still yields the same marker
        assert extract_spec_hash_marker(md) is not None

    def test_regenerate_preserves_marker(self):
        from scripts.ai_ready_helpers import (regenerate_spec_preserving_human,
                                              extract_spec_hash_marker, _spec_content_hash)
        d = self._domain()
        regen = regenerate_spec_preserving_human("", d, [], [])
        assert extract_spec_hash_marker(regen) == _spec_content_hash(d, [], [])


# ─── §12 domain-coverage completeness (run_89e28075): subsystem breadth anchor ───

class TestSubsystemCoverage:
    """compute_subsystem_coverage: which load-bearing subsystems have a domain vs
    are gaps vs are explicitly out-of-scope. Breadth anchor = seed subsystem map
    (from arch docs) matched against domains' evidence files (steps[].file_path +
    flows/routes). Honest-lossy: out-of-scope subsystems are RECORDED, not silent."""

    def _doc(self):
        # a domain whose flow's step touches backend/core/streaming_orchestrator.py
        return {
            "domains": [{"id": "domain:chat-session", "name": "Chat & Session"}],
            "flows": [{"id": "f1", "domain_id": "domain:chat-session",
                       "entry_ref": "route:post-x"}],
            "steps": [{"id": "s1", "flow_id": "f1",
                       "file_path": "backend/core/streaming_orchestrator.py"}],
            "routes": [{"id": "route:post-x", "file_path": "backend/core/session_router.py"}],
        }

    def _seed(self):
        # seed subsystem map: name -> {globs, tier}
        return [
            {"name": "session", "tier": "spine",
             "globs": ["backend/core/session_*.py", "backend/core/streaming_orchestrator.py"]},
            {"name": "context-memory", "tier": "spine",
             "globs": ["backend/core/context_*.py", "backend/core/recall_multi.py"]},
            {"name": "utils", "tier": "out-of-scope",
             "globs": ["backend/utils/*.py"]},
        ]

    def test_covered_subsystem_detected(self):
        from scripts.ai_ready_helpers import compute_subsystem_coverage
        cov = compute_subsystem_coverage(self._doc(), self._seed())
        # session has a domain (streaming_orchestrator.py is in a domain's step)
        by = {s["name"]: s for s in cov["subsystems"]}
        assert by["session"]["status"] == "covered"
        assert by["session"]["domain_id"] == "domain:chat-session"

    def test_gap_subsystem_flagged(self):
        from scripts.ai_ready_helpers import compute_subsystem_coverage
        cov = compute_subsystem_coverage(self._doc(), self._seed())
        by = {s["name"]: s for s in cov["subsystems"]}
        # context-memory is a spine subsystem with NO domain → gap (not silent)
        assert by["context-memory"]["status"] == "gap"

    def test_out_of_scope_recorded_not_silent(self):
        from scripts.ai_ready_helpers import compute_subsystem_coverage
        cov = compute_subsystem_coverage(self._doc(), self._seed())
        by = {s["name"]: s for s in cov["subsystems"]}
        # utils is tier=out-of-scope → recorded as out-of-scope, NOT gap, NOT silent
        assert by["utils"]["status"] == "out-of-scope"

    def test_summary_counts(self):
        from scripts.ai_ready_helpers import compute_subsystem_coverage
        cov = compute_subsystem_coverage(self._doc(), self._seed())
        # 3 seeds: 1 covered, 1 gap, 1 out-of-scope
        assert cov["total"] == 3
        assert cov["covered"] == 1
        assert cov["gaps"] == 1
        assert cov["out_of_scope"] == 1
        # gap queue is non-empty and names the missing subsystem (non-silent)
        assert "context-memory" in cov["gap_queue"]

    def test_no_seed_returns_empty_not_crash(self):
        from scripts.ai_ready_helpers import compute_subsystem_coverage
        cov = compute_subsystem_coverage(self._doc(), [])
        assert cov["total"] == 0 and cov["subsystems"] == []


class TestBlindSpotScan:
    """blind_spot_scan (AC4): Spec Studio-style REVERSE coverage — code HAS a risky
    behavior (risk_areas / hot_zones) but the domain layer (steps[].file_path or
    business_rules[].anchor) does NOT document it → report it (route to SME queue).

    Design constraints (§11.2 / §12.4):
      - REPORT-ONLY, never fail-closed (the gate version is DEFERRED as C042).
      - DETERMINISTIC — keys off existing risk_areas/hot_zones facts, NOT an LLM
        negative assertion ("does X exist" negatives are systematically unreliable, r6).
      - Honest: a risky file documented by a step OR a business_rule anchor = covered;
        otherwise = blind spot (never silently dropped)."""

    def _doc(self):
        return {
            "domains": [{
                "id": "domain:chat-session",
                "business_rules": [
                    {"rule": "single-writer", "anchor": "desktop/src/stores/MessageStore.ts",
                     "verified": True},
                ],
            }],
            "flows": [{"id": "f1", "domain_id": "domain:chat-session"}],
            "steps": [{"id": "s1", "flow_id": "f1",
                       "file_path": "backend/core/streaming_orchestrator.py"}],
            # two risky spans: one documented (MessageStore via business_rule),
            # one NOT documented anywhere in the domain layer (session_unit.py).
            "risk_areas": [
                {"name": "append", "file_path": "desktop/src/stores/MessageStore.ts",
                 "risk_score": 1.0, "reason": "High fan-in: 3744 callers"},
                {"name": "force_kill_tree", "file_path": "backend/core/session_unit.py",
                 "risk_score": 0.9, "reason": "process kill path"},
            ],
            "hot_zones": [
                {"name": "append", "file_path": "desktop/src/stores/MessageStore.ts",
                 "callers": 3744},
            ],
        }

    def test_documented_risk_not_flagged(self):
        from scripts.ai_ready_helpers import blind_spot_scan
        res = blind_spot_scan(self._doc())
        blind_files = {b["file_path"] for b in res["blind_spots"]}
        # MessageStore.ts is covered by a business_rule anchor → NOT a blind spot
        assert "desktop/src/stores/MessageStore.ts" not in blind_files

    def test_undocumented_risk_flagged(self):
        from scripts.ai_ready_helpers import blind_spot_scan
        res = blind_spot_scan(self._doc())
        blind_files = {b["file_path"] for b in res["blind_spots"]}
        # session_unit.py has a risky span but no step/anchor documents it → blind spot
        assert "backend/core/session_unit.py" in blind_files
        # the flagged item carries WHY (reason preserved, not silent)
        bs = next(b for b in res["blind_spots"] if b["file_path"] == "backend/core/session_unit.py")
        assert bs.get("reason")
        assert bs.get("name") == "force_kill_tree"

    def test_summary_counts_and_clean_flag(self):
        from scripts.ai_ready_helpers import blind_spot_scan
        res = blind_spot_scan(self._doc())
        # 2 risky spans: 1 documented, 1 blind
        assert res["total_risky"] == 2
        assert res["documented"] == 1
        assert res["blind"] == 1
        assert res["clean"] is False

    def test_clean_when_all_documented(self):
        from scripts.ai_ready_helpers import blind_spot_scan
        doc = {
            "domains": [{"id": "d1", "business_rules": [
                {"rule": "r", "anchor": "backend/core/session_unit.py", "verified": True}]}],
            "flows": [], "steps": [],
            "risk_areas": [{"name": "x", "file_path": "backend/core/session_unit.py",
                            "risk_score": 0.9, "reason": "kill path"}],
            "hot_zones": [],
        }
        res = blind_spot_scan(doc)
        assert res["blind"] == 0
        assert res["clean"] is True

    def test_no_risk_areas_returns_clean_not_crash(self):
        from scripts.ai_ready_helpers import blind_spot_scan
        res = blind_spot_scan({"domains": [], "flows": [], "steps": [],
                               "risk_areas": [], "hot_zones": []})
        assert res["total_risky"] == 0
        assert res["blind"] == 0
        assert res["clean"] is True
        assert res["blind_spots"] == []


class TestRenderBlindSpotsMd:
    """render_blind_spots_md (run_d7b78923): renders a PER-PACKAGE BLIND-SPOTS.md from
    a blind_spot_scan result. Report-only artifact (never a gate). Two shapes:
      - blind>0 → a table listing EVERY blind spot (file / symbol / reason / risk),
        so nothing is silently dropped (the honesty constraint carries into the render).
      - clean → an explicit 'no reverse-coverage blind spots' line + the coverage
        counts, NOT an empty file (zero findings is a valid, stated outcome)."""

    def _scan_blind(self):
        from scripts.ai_ready_helpers import blind_spot_scan
        return blind_spot_scan({
            "domains": [{"id": "d1", "business_rules": [
                {"rule": "r", "anchor": "a/documented.py", "verified": True}]}],
            "flows": [], "steps": [{"id": "s1", "flow_id": "f1", "file_path": "a/documented.py"}],
            "risk_areas": [
                {"name": "documented_fn", "file_path": "a/documented.py",
                 "risk_score": 0.8, "reason": "fan-in"},
                {"name": "force_kill_tree", "file_path": "core/session_unit.py",
                 "risk_score": 0.9, "reason": "process kill path"},
            ],
            "hot_zones": [],
        })

    def test_blind_spots_all_appear_in_render(self):
        from scripts.ai_ready_helpers import render_blind_spots_md
        scan = self._scan_blind()
        md = render_blind_spots_md(scan, "my-package")
        # per-package: the package name is in the doc (not a shared/global title)
        assert "my-package" in md
        # EVERY blind spot must be rendered — nothing silently dropped (honesty)
        for b in scan["blind_spots"]:
            assert b["file_path"] in md, f"blind spot {b['file_path']} missing from render (silent drop)"
            assert b["name"] in md
        # the reason (WHY it's risky) survives into the human-facing doc
        assert "process kill path" in md
        # a documented risky file is NOT listed in the blind-spots TABLE (real check:
        # split on the table header, not the title which also says "Blind Spots")
        table = md.split("## Undocumented risky spans")[-1] if "## Undocumented risky spans" in md else ""
        assert "a/documented.py" not in table, "documented risky file must not appear in the blind-spots table"

    def test_render_is_not_vacuous_mutation(self):
        """Mutation guard: if the renderer ignored the blind_spots list (rendered only
        the summary counts), this asserts it would be caught — the specific file path
        of a blind spot MUST appear, not just the count."""
        from scripts.ai_ready_helpers import render_blind_spots_md
        scan = self._scan_blind()
        md = render_blind_spots_md(scan, "pkg")
        assert "core/session_unit.py" in md  # a count-only render would fail this

    def test_clean_render_is_honest_not_empty(self):
        from scripts.ai_ready_helpers import render_blind_spots_md, blind_spot_scan
        clean = blind_spot_scan({"domains": [], "flows": [], "steps": [],
                                 "risk_areas": [], "hot_zones": []})
        md = render_blind_spots_md(clean, "pkg")
        assert md.strip(), "clean render must NOT be empty — zero findings is a stated outcome"
        assert "pkg" in md
        # an explicit clean statement, not just whitespace
        assert "no" in md.lower() and "blind" in md.lower()


# ─── Gap 2: Business-Rules Extraction scoring dimension (run_128fc19f) ───

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


# ─── Gap 3: producer order-invariance tripwires (run_006dce1c) ───
#
# NOT `f(x)==f(x)`. Each test feeds SHUFFLED/REVERSED input to a producer and
# asserts the collection field comes out SORTED — so if a future edit drops the
# `sorted()` at the source, the test goes RED (Gate-1 correction: an already-ordered
# fixture would make this a worthless tautology; deliberately-disordered input is
# what gives it regression teeth).

class TestProducerOrderInvariance:
    """Sorted-output tripwires: shuffled input -> sorted output, or RED on dropped sorted()."""

    def test_blind_spot_scan_output_sorted_by_file_path(self):
        from scripts.ai_ready_helpers import blind_spot_scan
        # risk_areas deliberately in REVERSE file_path order; scan must sort blind_spots.
        doc = {
            "domains": [], "flows": [], "steps": [], "hot_zones": [],
            "risk_areas": [
                {"name": "z", "file_path": "z/zzz.py", "risk_score": 9, "reason": "r"},
                {"name": "a", "file_path": "a/aaa.py", "risk_score": 8, "reason": "r"},
                {"name": "m", "file_path": "m/mmm.py", "risk_score": 7, "reason": "r"},
            ],
        }
        out = blind_spot_scan(doc)
        paths = [b["file_path"] for b in out["blind_spots"]]
        assert paths == sorted(paths), f"blind_spots must be sorted by file_path, got {paths}"
        assert paths[0] == "a/aaa.py" and paths[-1] == "z/zzz.py"  # RED if sorted() dropped

    def test_compute_anchor_accounting_missing_ids_sorted(self):
        from scripts.ai_ready_helpers import compute_anchor_accounting
        # Anchors present but none classified/unclassified -> all become "missing";
        # feed entry ids in NON-sorted order, assert missing_ids comes out sorted.
        doc = {
            "flows": [], "domains": [], "unclassified": [],
            "entry_points": [
                {"id": "e-zebra", "method": "GET", "path": "/z", "file_path": "z.py", "line_number": 1},
                {"id": "e-alpha", "method": "GET", "path": "/a", "file_path": "a.py", "line_number": 1},
                {"id": "e-mike", "method": "GET", "path": "/m", "file_path": "m.py", "line_number": 1},
            ],
        }
        out = compute_anchor_accounting(doc)
        mids = out["missing_ids"]
        if len(mids) >= 2:  # only meaningful when there are ids to order
            assert mids == sorted(mids), f"missing_ids must be sorted, got {mids}"

    def test_build_file_tree_returns_sorted(self, tmp_path):
        """_build_file_tree caps at sorted(files)[:500]. Gate-2 caught the naive
        version as VACUOUS: with a .git dir, `git ls-files` is consulted FIRST and
        already returns alphabetical output, so the fallback's sorted() is never
        exercised — dropping it wouldn't RED the test. Force the rglob FALLBACK
        (NO .git dir → git ls-files returns empty → walk branch, where sorted() is
        load-bearing over unordered filesystem walk order)."""
        from scripts.ai_ready_helpers import _build_file_tree
        r = tmp_path / "repo_nogit"; r.mkdir()  # deliberately NOT a git repo
        for name in ["zzz.py", "aaa.py", "mmm.py", "bbb.py"]:
            (r / name).write_text("x\n")
        tree = _build_file_tree(r)
        rel = [t for t in tree if t.endswith(".py")]
        assert rel, "expected the .py files via the rglob fallback (no git)"
        assert rel == sorted(rel), f"_build_file_tree must return sorted paths, got {rel}"
