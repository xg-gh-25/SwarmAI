"""Tests for the DDD self-audit job handler (run_835f82ff).

Load-bearing invariants (Gate-0):
- DETECT-ONLY: the review toolset has NO Write/Edit/Bash — structurally cannot mutate DDD.
- DOMAIN-AWARE: code-backed projects get a prose-vs-code prompt; non-code get
  internal-contradiction. And a code-backed project grants --add-dir of its source.
- ENUMERATION: discovers every project carrying a canonical DDD doc.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from unittest.mock import MagicMock


from jobs.handlers.ddd_self_audit import (
    _AUDIT_TOOLS,
    _build_audit_prompt,
    _classify_review_result,
    _count_parseable_findings,
    _discover_ddd_projects,
    _is_code_backed,
    _source_repo_for,
)


class TestDetectOnlyInvariant:
    def test_audit_toolset_has_no_write_capability(self):
        """The single structural guarantee that the audit cannot corrupt DDD:
        the agent is granted read-only tools. If this ever gains Write/Edit/Bash,
        the run_b2e85d61 NO-GO invariant (prose-truth rewrite stays human) is broken."""
        for forbidden in ("Write", "Edit", "MultiEdit", "Bash", "NotebookEdit"):
            assert forbidden not in _AUDIT_TOOLS, f"{forbidden} would break detect-only"
        assert set(_AUDIT_TOOLS) <= {"Read", "Grep", "Glob"}


class TestDomainAwarePrompt:
    def test_code_backed_prompt_checks_against_code(self, tmp_path):
        prompt = _build_audit_prompt("SwarmAI", tmp_path, code_backed=True)
        assert "LIVE CODE" in prompt
        assert "not yet built" in prompt  # the canonical code-drift example
        # never invites edits
        assert "MUST NOT edit" in prompt

    def test_non_code_prompt_checks_internal_consistency(self, tmp_path):
        prompt = _build_audit_prompt("CMHK_SalesIntel", tmp_path, code_backed=False)
        assert "INCONSISTENCY" in prompt or "self-contradiction" in prompt
        assert "no source repo" in prompt  # explicitly acknowledges no code to check
        # code-drift language must NOT leak into a non-code prompt
        assert "LIVE CODE" not in prompt

    def test_prompt_requires_radar_todos_surface(self, tmp_path):
        """Findings must route to a RADAR_TODOS block (the in-band forcing function)."""
        prompt = _build_audit_prompt("X", tmp_path, code_backed=True)
        assert "RADAR_TODOS" in prompt
        assert "s_persist" in prompt  # each finding names the human fix path

    def test_prompt_emits_description_not_note(self, tmp_path):
        """Gate-2 MED fix: the todo parser reads 'description', not 'note'. If the prompt
        emits 'note', the evidence payload silently drops → the forcing function rots.
        The evidence must land in the field the parser actually reads."""
        prompt = _build_audit_prompt("X", tmp_path, code_backed=True)
        assert '"description"' in prompt
        assert '"note"' not in prompt  # the dropped-field trap


class TestCodeBackedDetection:
    def test_code_intel_json_makes_project_code_backed(self, tmp_path):
        (tmp_path / "code-intel.json").write_text("{}")
        assert _is_code_backed(tmp_path) is True

    def test_no_code_artifact_is_not_code_backed(self, tmp_path):
        (tmp_path / "PRODUCT.md").write_text("# product")
        assert _is_code_backed(tmp_path) is False

    def test_source_repo_only_for_swarmai(self):
        # Non-SwarmAI projects have no live source repo → None (never a bogus --add-dir)
        assert _source_repo_for("CMHK_SalesIntel") is None
        assert _source_repo_for("PhysicalAI") is None


class TestEnumeration:
    def test_discovers_projects_with_ddd_docs(self, tmp_path, monkeypatch):
        import jobs.handlers.ddd_self_audit as mod
        projects_dir = tmp_path / "Projects"
        (projects_dir / "Alpha").mkdir(parents=True)
        (projects_dir / "Alpha" / "PRODUCT.md").write_text("# a")
        (projects_dir / "Beta").mkdir(parents=True)
        (projects_dir / "Beta" / "TECH.md").write_text("# b")
        (projects_dir / "NoDocs").mkdir(parents=True)  # no DDD doc → excluded
        (projects_dir / ".hidden").mkdir(parents=True)  # dotdir → excluded
        monkeypatch.setattr(mod, "PROJECTS_DIR", projects_dir)

        found = {name for name, _ in _discover_ddd_projects()}
        assert found == {"Alpha", "Beta"}

    def test_discovers_migrated_six_section_projects(self, tmp_path, monkeypatch):
        """REGRESSION (run_64f745d8 P0): a MIGRATED DDD keeps canonical docs under
        2-understanding/ with an EMPTY root. Discovery must resolve via ddd_path or it
        returns [] → the entire self-audit no-ops ('No DDD projects found'). The
        six-section migration silently blinded the audit this way. MUTATION: revert
        the probe to bare `(d / doc).exists()` → this test goes RED (migrated project
        not discovered)."""
        import jobs.handlers.ddd_self_audit as mod
        projects_dir = tmp_path / "Projects"
        # Migrated layout: docs ONLY under 2-understanding/, root empty.
        mig = projects_dir / "Migrated" / "2-understanding"
        mig.mkdir(parents=True)
        (mig / "TECH.md").write_text("# migrated tech")
        assert not (projects_dir / "Migrated" / "TECH.md").exists()  # premise: root empty
        # Un-migrated sibling still discovered via strangler fallback.
        (projects_dir / "Legacy").mkdir(parents=True)
        (projects_dir / "Legacy" / "PRODUCT.md").write_text("# legacy")
        monkeypatch.setattr(mod, "PROJECTS_DIR", projects_dir)

        found = {name for name, _ in _discover_ddd_projects()}
        assert found == {"Migrated", "Legacy"}, (
            f"migrated DDD (docs in 2-understanding/) must be discovered — got {found}"
        )


class TestDiscoveryBlindnessIsFailLoud:
    """run_775f3969: a brain must ANNOUNCE when it's blind. If Projects/ has project
    dirs but ZERO resolve as DDD (the discovery probe can't see them — e.g. a layout
    migration it doesn't follow), the audit must return 'failed' (→ job-failure 🔔),
    NOT a silent 'skipped'. Silent-skip is exactly how the six-section migration
    disabled the whole semantic-drift immune system undetected for weeks."""

    def test_blind_discovery_fails_loud_not_skipped(self, tmp_path, monkeypatch):
        import jobs.handlers.ddd_self_audit as mod
        projects_dir = tmp_path / "Projects"
        # Project dirs EXIST on disk but carry no discoverable DDD doc (simulates the
        # probe being blind to the real layout).
        (projects_dir / "AlphaProj").mkdir(parents=True)
        (projects_dir / "BetaProj").mkdir(parents=True)
        monkeypatch.setattr(mod, "PROJECTS_DIR", projects_dir)
        # Force discovery to return [] (the blind state) while dirs exist on disk.
        monkeypatch.setattr(mod, "_discover_ddd_projects", lambda: [])

        result = mod.run_ddd_self_audit({})
        assert result["status"] == "failed", (
            f"blind discovery (dirs exist, 0 DDD resolved) must FAIL loud, got "
            f"{result['status']!r}: {result.get('summary')}"
        )
        assert "BLIND" in result["summary"] or "blind" in result["summary"].lower()

    def test_genuinely_empty_projects_is_legit_skip(self, tmp_path, monkeypatch):
        """The OTHER side: Projects/ genuinely empty (fresh install) → 'skipped' is
        correct, NOT a false 'failed' alarm."""
        import jobs.handlers.ddd_self_audit as mod
        projects_dir = tmp_path / "Projects"
        projects_dir.mkdir(parents=True)  # exists but empty — no project dirs
        monkeypatch.setattr(mod, "PROJECTS_DIR", projects_dir)
        monkeypatch.setattr(mod, "_discover_ddd_projects", lambda: [])

        result = mod.run_ddd_self_audit({})
        assert result["status"] == "skipped", (
            f"genuinely-empty Projects/ must skip (not false-alarm failed), got "
            f"{result['status']!r}"
        )

    def test_executor_propagates_handler_failed_status(self, monkeypatch):
        """INTEGRATION SEAM (Gate-2 CRITICAL, run_775f3969): the handler returning
        'failed' is INERT unless the executor maps it to JobResult 'failed' — otherwise
        it collapses to 'skipped' → no 🔔, no consecutive_failures increment, and the
        fail-loud fix does nothing in production. This guards executor.py's status map
        (a handler-only test cannot — it bypasses execute_job). MUTATION: revert the
        map to `== "success" else "skipped"` → this goes RED."""
        import jobs.executor as ex
        from jobs.models import Job
        # Handler reports the blind-failure; executor must NOT swallow it.
        monkeypatch.setattr(
            ex, "_write_job_result", lambda *a, **k: None
        )
        import jobs.handlers.ddd_self_audit as sa_mod
        monkeypatch.setattr(
            sa_mod, "run_ddd_self_audit",
            lambda config=None: {"status": "failed", "summary": "DISCOVERY BLIND: test", "output_path": None},
        )
        job = Job(id="ddd-self-audit", name="DDD Self-Audit",
                  type="ddd_self_audit", schedule="0 9 * * 1")
        result = ex.execute_job(job, MagicMock(), [], known_job_ids={"ddd-self-audit"})
        assert result.status == "failed", (
            f"executor must propagate handler 'failed' → JobResult 'failed' (else no "
            f"notification/streak), got {result.status!r}"
        )

    def test_executor_maps_handler_skipped_to_skipped(self, monkeypatch):
        """The other side: a legit handler 'skipped' (empty Projects/) must stay
        'skipped', not become a false 'failed' alarm."""
        import jobs.executor as ex
        from jobs.models import Job
        monkeypatch.setattr(ex, "_write_job_result", lambda *a, **k: None)
        import jobs.handlers.ddd_self_audit as sa_mod
        monkeypatch.setattr(
            sa_mod, "run_ddd_self_audit",
            lambda config=None: {"status": "skipped", "summary": "empty", "output_path": None},
        )
        job = Job(id="ddd-self-audit", name="DDD Self-Audit",
                  type="ddd_self_audit", schedule="0 9 * * 1")
        result = ex.execute_job(job, MagicMock(), [], known_job_ids={"ddd-self-audit"})
        assert result.status == "skipped", f"legit skip must stay skipped, got {result.status!r}"


class TestNoMutation:
    def test_report_persist_path_never_touches_ddd_docs(self, tmp_path):
        """DoD-B: the handler's OUTPUT is a report + todos — it must never write a DDD doc.
        The agent has no Write tool (TestDetectOnlyInvariant); this asserts the Python
        side writes only to JobResults, not into Projects/*/{PRODUCT,TECH,...}.md.

        We prove it structurally: the handler imports no DDD-doc writer and the only
        persist call is _write_job_result (→ Knowledge/JobResults/). A snapshot-hash guard
        on a fake Projects tree confirms the docs are untouched by the pure helpers."""
        projects = tmp_path / "Projects"
        (projects / "SwarmAI").mkdir(parents=True)
        doc = projects / "SwarmAI" / "TECH.md"
        doc.write_text("# TECH\n9-stage pipeline.\n")
        before = hashlib.md5(doc.read_bytes()).hexdigest()

        # Exercise the pure, non-subprocess helpers (the parts that touch the tree).
        _is_code_backed(projects / "SwarmAI")
        _build_audit_prompt("SwarmAI", projects / "SwarmAI", code_backed=True)

        after = hashlib.md5(doc.read_bytes()).hexdigest()
        assert before == after, "DDD doc was mutated — detect-only invariant broken"


class TestReviewResultClassification:
    """Two-state classification (clean / failed). We parse stdout on ANY exit code
    purely for OBSERVABILITY — a failure surfaces its real cause, not an opaque
    'exit N'. There is deliberately NO cost/budget branch: cost is governed centrally
    (scheduler monthly budget), a hang by the subprocess timeout (run_271c39df:
    a per-call dollar cap was WRONG — it截断 real work as a disaster-recovery control)."""

    def test_clean_exit_is_clean(self):
        v = _classify_review_result(0, {"result": "no drift found"}, "")
        assert v["status"] == "clean"
        assert v["result_text"] == "no drift found"

    def test_genuine_exit1_surfaces_error_detail_not_opaque(self):
        """A non-zero exit → 'failed' WITH a real error detail (stderr tail), never
        an opaque 'exit 1'. This is the kept observability improvement."""
        v = _classify_review_result(1, {"result": ""}, "MCP server failed to connect: timeout")
        assert v["status"] == "failed"
        assert "MCP server failed" in v["error_detail"]

    def test_genuine_exit1_prefers_errors_field_over_stderr(self):
        output = {"result": "", "errors": ["Auth error: token expired"]}
        v = _classify_review_result(1, output, "some stderr noise")
        assert v["status"] == "failed"
        assert "Auth error" in v["error_detail"]

    def test_non_string_errors_dont_crash(self):
        """errors may carry non-string items (dicts/ints) — must not crash the join."""
        output = {"result": "", "errors": [{"code": 500}, 42, None]}
        v = _classify_review_result(1, output, "real failure")
        assert v["status"] == "failed"
        assert "real failure" in v["error_detail"]

    def test_no_budget_status_exists(self):
        """Regression guard (run_271c39df cleanup): a former budget-exhaust input must
        NOT produce a special 'partial' status — there is no cost branch anymore.
        A returncode-0 budget-labeled result is simply 'clean'; a nonzero is 'failed'."""
        budget_shaped = {"result": "x", "subtype": "error_max_budget_usd",
                         "errors": ["Reached maximum budget ($0.5)"]}
        assert _classify_review_result(0, budget_shaped, "")["status"] == "clean"
        assert _classify_review_result(1, budget_shaped, "")["status"] == "failed"


class TestNoPerCallBudgetControl:
    """run_271c39df cleanup: cost must NOT be controlled per-call inside this handler.
    No --max-budget-usd flag, no _PER_PROJECT_BUDGET_USD constant."""

    def test_no_budget_flag_in_command(self):
        import jobs.handlers.ddd_self_audit as mod
        src = Path(mod.__file__).read_text()
        assert "--max-budget-usd" not in src, "per-call dollar cap must not be reintroduced"
        assert "_PER_PROJECT_BUDGET_USD" not in src, "per-call budget constant must not return"

    def test_timeout_is_the_only_per_call_bound(self):
        """A hang guard (timeout) is legitimate; a cost cap is not. Assert the hang
        guard exists and is the sole per-call control constant."""
        from jobs.handlers.ddd_self_audit import _PER_PROJECT_TIMEOUT_S
        assert isinstance(_PER_PROJECT_TIMEOUT_S, int) and _PER_PROJECT_TIMEOUT_S > 0


class TestParseableFindingCount:
    """n_findings must reflect ACTUAL parseable todos, not raw '"title"' substrings —
    output can be interrupted mid-JSON (e.g. subprocess timeout), and a malformed
    block must count 0, not the substring count."""

    def test_valid_block_counts_findings(self):
        text = ('drift found\n<!-- RADAR_TODOS\n'
                '[{"title":"a","description":"x"},{"title":"b","description":"y"}]\n-->')
        assert len(_count_parseable_findings(text)) == 2

    def test_no_close_marker_counts_zero(self):
        """A block cut off before the closing --> (no regex match) yields 0."""
        text = 'partial analysis\n<!-- RADAR_TODOS\n[{"title":"a","description":"x"'
        assert _count_parseable_findings(text) == []

    def test_malformed_json_inside_block_counts_zero(self):
        """The load-bearing case: the marker CLOSES (regex matches) but the JSON
        inside is invalid (e.g. trailing comma / truncated obj) — json.loads fails,
        so we count 0, NOT the raw '"title"' substrings. This is what makes the
        parse (vs substring-count) actually matter."""
        # regex captures [...]; inside is invalid JSON but contains two '"title"'
        text = ('drift\n<!-- RADAR_TODOS\n'
                '[{"title":"a","description":"x"},{"title":"b",BROKEN]\n-->')
        assert _count_parseable_findings(text) == [], (
            "malformed JSON must count 0, not the substring count of '\"title\"'"
        )

    def test_no_block_counts_zero(self):
        assert _count_parseable_findings("clean review, no drift") == []
