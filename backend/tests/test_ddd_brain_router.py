"""Tests for the DDD Brain Hub read-only router (GET /api/ddd/brains[/{name}]).

Methodology: integration tests against the REAL workspace Projects/ tree (no
mocks of parse_entries / git / ddd_paths) — the whole point of the Brain Hub is
to project real cognitive state, so the tests assert against real data.

Key invariants under test:
  - AC1: brains list returns every real DDD project with live health signals
    (sinking = dormant+archived count, pending = staged proposals, uncommitted
    = git dirty, last_change = relative time) — NO stored metric, computed live.
  - AC2: brain detail returns the six sections (via ddd_paths SSOT) with real
    member files; ② canonical docs carry per-entry entry_type + decay_state that
    MATCH a direct parse_entries() call (no fabricated data).
  - ⑤/⑥ are enumerated as single well-known files (bindings.yaml / REFRESHER.md),
    NEVER by iterdir-ing the project root (Gate-1 revision).
  - No recall-heat / ref_count number is emitted anywhere (ref_count is dead).
"""

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from routers.ddd_brain import router

    app = FastAPI()
    app.include_router(router, prefix="/api/ddd")
    return TestClient(app)


class TestLifecycleStageOrdering:
    """M3: REVIEW (pending human decision) must dominate DISTRIBUTE.

    A brain that has already distributed AND accrued new pending proposals must
    surface REVIEW — the frontend renders lifecycleStage as a linear stepper with
    DISTRIBUTE terminal, so DISTRIBUTE-first would light the bar fully green and
    HIDE the un-reviewed work.
    """

    def test_pending_dominates_distribute_output(self, monkeypatch, tmp_path):
        import routers.ddd_brain as m
        # A brain that HAS a distribute output AND HAS pending proposals.
        monkeypatch.setattr(m, "_has_distribute_output", lambda pd: True)
        monkeypatch.setattr(m, "_entry_count", lambda pd: 5)
        stage = m._lifecycle_stage(tmp_path, present={}, pending=2)
        assert stage == "REVIEW", (
            "pending>0 must yield REVIEW even when a distribute output exists — "
            "else the stepper hides the un-reviewed queue behind a terminal DISTRIBUTE"
        )

    def test_distribute_when_no_pending(self, monkeypatch, tmp_path):
        import routers.ddd_brain as m
        monkeypatch.setattr(m, "_has_distribute_output", lambda pd: True)
        monkeypatch.setattr(m, "_entry_count", lambda pd: 5)
        assert m._lifecycle_stage(tmp_path, present={}, pending=0) == "DISTRIBUTE"

    def test_grow_and_create_fallthrough_unchanged(self, monkeypatch, tmp_path):
        import routers.ddd_brain as m
        monkeypatch.setattr(m, "_has_distribute_output", lambda pd: False)
        monkeypatch.setattr(m, "_entry_count", lambda pd: 3)
        assert m._lifecycle_stage(tmp_path, present={}, pending=0) == "GROW"
        monkeypatch.setattr(m, "_entry_count", lambda pd: 0)
        assert m._lifecycle_stage(tmp_path, present={}, pending=0) == "CREATE"


class TestDiffIncompleteFlag:
    """F8: a review-diff git timeout must surface diff_incomplete (loud, not silent-empty)."""

    def test_scoped_diff_raises_on_timeout(self, monkeypatch, tmp_path):
        import subprocess as _sp
        import routers.ddd_brain as m
        ws = tmp_path
        (ws / ".git").mkdir()
        monkeypatch.setattr(m, "_workspace_root", lambda: ws)

        def _boom(*a, **k):
            raise _sp.TimeoutExpired(cmd="git diff", timeout=5)
        monkeypatch.setattr(m.subprocess, "run", _boom)
        with pytest.raises(m.DiffIncompleteError):
            m._scoped_diff_hunks(ws / "Projects" / "X", "abc123")

    def test_clean_empty_diff_does_not_raise(self, monkeypatch, tmp_path):
        """A genuinely clean diff returns [] — only a TIMEOUT raises (F8 distinguishes them)."""
        import routers.ddd_brain as m

        class _R:
            returncode = 0
            stdout = ""
        ws = tmp_path
        (ws / ".git").mkdir()
        monkeypatch.setattr(m, "_workspace_root", lambda: ws)
        monkeypatch.setattr(m.subprocess, "run", lambda *a, **k: _R())
        assert m._scoped_diff_hunks(ws / "Projects" / "X", "abc123") == []


class TestSourceChangedSinceTristate:
    """F2: source_changed_since is None (freshness-unknown) when the output is uncommitted."""

    def test_uncommitted_output_is_none_not_false(self, monkeypatch, tmp_path):
        import routers.ddd_brain as m

        pd = tmp_path / "Proj"
        out = pd / ".artifacts" / "distribute"
        out.mkdir(parents=True)
        (pd / "aim.json").write_text('{"distribution": {"targets": ["open-plugin"], "visibility": "internal"}}')
        monkeypatch.setattr(m, "_distribute_output_dir", lambda p: out)
        # output NOT committed → _output_is_committed False → tristate None
        monkeypatch.setattr(m, "_output_is_committed", lambda p, o: False)
        d = m._distribution_state(pd)
        assert d["has_output"] is True
        assert d["source_changed_since"] is None, "uncommitted output must be freshness-UNKNOWN (None), never a confident False"

    def test_committed_output_content_newer_is_true(self, monkeypatch, tmp_path):
        import routers.ddd_brain as m
        pd = tmp_path / "Proj"
        out = pd / ".artifacts" / "distribute"
        out.mkdir(parents=True)
        (pd / "aim.json").write_text('{"distribution": {"targets": ["open-plugin"], "visibility": "internal"}}')
        monkeypatch.setattr(m, "_distribute_output_dir", lambda p: out)
        monkeypatch.setattr(m, "_output_is_committed", lambda p, o: True)
        # content commit is far in the FUTURE relative to the output dir mtime → changed
        from datetime import datetime, timezone
        future = datetime(2999, 1, 1, tzinfo=timezone.utc).isoformat()
        monkeypatch.setattr(m, "_last_content_commit_iso", lambda p: future)
        d = m._distribution_state(pd)
        assert d["source_changed_since"] is True

    def test_committed_output_no_content_commit_is_false(self, monkeypatch, tmp_path):
        import routers.ddd_brain as m
        pd = tmp_path / "Proj"
        out = pd / ".artifacts" / "distribute"
        out.mkdir(parents=True)
        (pd / "aim.json").write_text('{"distribution": {"targets": ["open-plugin"], "visibility": "internal"}}')
        monkeypatch.setattr(m, "_distribute_output_dir", lambda p: out)
        monkeypatch.setattr(m, "_output_is_committed", lambda p, o: True)
        monkeypatch.setattr(m, "_last_content_commit_iso", lambda p: None)
        d = m._distribution_state(pd)
        assert d["source_changed_since"] is False


class TestDocstringHonesty:
    """H1: the module docstring must not falsely claim pure-read / two endpoints."""

    def test_docstring_does_not_claim_pure_read(self):
        import routers.ddd_brain as m
        doc = m.__doc__ or ""
        assert "PURE READ" not in doc, "docstring still claims PURE READ — Run2 added mutating POSTs"
        # names the mutating reality
        assert "apply -R" in doc and "watermark" in doc.lower()


class TestBrainsList:
    """GET /api/ddd/brains — Gallery data (AC1)."""

    def test_lists_all_real_projects(self, client):
        resp = client.get("/api/ddd/brains")
        assert resp.status_code == 200
        data = resp.json()
        assert "brains" in data
        names = {b["name"] for b in data["brains"]}
        # SwarmAI is the non-deletable default project — MUST be present.
        assert "SwarmAI" in names
        # There are multiple real DDD projects in the workspace.
        assert len(data["brains"]) >= 2

    def test_each_brain_has_live_health_and_sections(self, client):
        resp = client.get("/api/ddd/brains")
        brains = {b["name"]: b for b in resp.json()["brains"]}
        sw = brains["SwarmAI"]

        # six-section presence map — SwarmAI has ② docs + ④ capabilities.
        sp = sw["sectionsPresent"]
        assert set(sp.keys()) == {
            "identity", "knowledge", "gates",
            "capabilities", "delivery", "refresher",
        }
        assert sp["identity"] is True       # AGENTS.md exists at root
        assert sp["knowledge"] is True      # 2-understanding/ docs exist

        # live health signals present + correctly typed.
        h = sw["health"]
        assert isinstance(h["sinking"], int) and h["sinking"] >= 0
        assert isinstance(h["pending"], int) and h["pending"] >= 0
        assert isinstance(h["uncommitted"], bool)
        assert isinstance(h["lastChangeRelative"], str)

        # lifecycle stage is one of the four canonical stages.
        assert sw["lifecycleStage"] in {"CREATE", "GROW", "REVIEW", "DISTRIBUTE"}

    def test_no_recall_heat_number_anywhere(self, client):
        """ref_count is dead → NO heat/crown/recall number in the payload (R30#4)."""
        raw = client.get("/api/ddd/brains").text.lower()
        for banned in ("ref_count", "refcount", "recall_heat", "recallheat", "crown"):
            assert banned not in raw

    def test_sinking_matches_direct_parse_entries(self, client):
        """AC1: health.sinking is NOT a stub — it equals a direct dormant+archived count."""
        from core.ddd_entry_lifecycle import parse_entries
        from core.ddd_paths import ddd_path

        # Compute the truth directly over SwarmAI's ② canonical docs.
        root = _swarmai_dir()
        expected = 0
        for doc in ("PRODUCT.md", "TECH.md", "IMPROVEMENT.md", "PROJECT.md"):
            p = ddd_path(root, doc)
            if p.exists():
                for e in parse_entries(p.read_text()):
                    if e.decay_state in ("dormant", "archived"):
                        expected += 1

        brains = {b["name"]: b for b in client.get("/api/ddd/brains").json()["brains"]}
        assert brains["SwarmAI"]["health"]["sinking"] == expected


class TestBrainDetail:
    """GET /api/ddd/brains/{name} — Brain view data (AC2)."""

    def test_returns_six_sections(self, client):
        resp = client.get("/api/ddd/brains/SwarmAI")
        assert resp.status_code == 200
        detail = resp.json()
        keys = [s["key"] for s in detail["sections"]]
        assert keys == [
            "identity", "knowledge", "gates",
            "capabilities", "delivery", "refresher",
        ]
        # each section carries a stable circled-number label + curator + own/govern.
        by_key = {s["key"]: s for s in detail["sections"]}
        assert by_key["identity"]["num"] == "①"
        assert by_key["refresher"]["num"] == "⑥"
        assert by_key["knowledge"]["ownGovern"] == "OWN"
        assert by_key["delivery"]["ownGovern"] == "GOVERN"

    def test_delivery_and_refresher_are_single_files_not_root_dump(self, client):
        """Gate-1 revision: ⑤/⑥ resolve to '.' (root) — must be enumerated as the
        single well-known file, NEVER by iterdir-ing the whole project root."""
        detail = client.get("/api/ddd/brains/SwarmAI").json()
        by_key = {s["key"]: s for s in detail["sections"]}

        deliv_members = [m["path"] for m in by_key["delivery"]["members"]]
        refr_members = [m["path"] for m in by_key["refresher"]["members"]]

        # ⑤ = bindings.yaml only (if present); NOT AGENTS.md / aim.json / assets/…
        assert all(m.endswith("bindings.yaml") for m in deliv_members)
        # ⑥ = REFRESHER.md only.
        assert all(m.endswith("REFRESHER.md") for m in refr_members)
        # A root-dump bug would list dozens of members; single-file sections have ≤1.
        assert len(deliv_members) <= 1
        assert len(refr_members) <= 1

    def test_knowledge_entries_match_parse_entries(self, client):
        """AC2: ② per-entry type histogram equals a direct parse_entries call."""
        from collections import Counter
        from core.ddd_entry_lifecycle import parse_entries
        from core.ddd_paths import ddd_path

        tech = ddd_path(_swarmai_dir(), "TECH.md")
        direct = parse_entries(tech.read_text())
        direct_hist = Counter(e.entry_type for e in direct)

        detail = client.get("/api/ddd/brains/SwarmAI").json()
        knowledge = next(s for s in detail["sections"] if s["key"] == "knowledge")
        # entries carry the real per-entry fields.
        tech_entries = [e for e in knowledge["entries"]
                        if e.get("file", "").endswith("TECH.md")]
        assert tech_entries, "expected TECH.md entries in the knowledge section"
        for e in tech_entries[:5]:
            assert e["entryType"] in {
                "guideline", "pitfall", "decision", "model",
                "process", "principle", "correction",
            }
            assert e["decayState"] in {"active", "dormant", "archived"}

        api_hist = Counter(e["entryType"] for e in tech_entries)
        assert api_hist == direct_hist

    def test_empty_gates_marked_complete_not_broken(self, client):
        """R31: an empty ③Gates section is COMPLETE, not degraded."""
        detail = client.get("/api/ddd/brains/SwarmAI").json()
        gates = next(s for s in detail["sections"] if s["key"] == "gates")
        if not gates["members"]:
            assert gates["completeNotBroken"] is True

    def test_unknown_brain_returns_404(self, client):
        resp = client.get("/api/ddd/brains/NoSuchProjectXYZ")
        assert resp.status_code == 404

    def test_has_code_intel_reflects_db_presence(self, client):
        """AC1: hasCodeIntel is a live presence check of the on-disk code_intel.db,
        NOT gated on `kind`. SwarmAI has a real code_intel.db → true; a knowledge
        DDD without one → false. This is the field the frontend gates the CodeIntel
        entry on (presence, never kind — all DDDs resolve to kind='knowledge')."""
        from core.code_intel import get_code_intel_db_path

        detail = client.get("/api/ddd/brains/SwarmAI").json()
        assert "hasCodeIntel" in detail, "detail must expose hasCodeIntel"
        # Truth from disk — the field must match the actual .exists() of the db.
        assert detail["hasCodeIntel"] == get_code_intel_db_path("SwarmAI").exists()

    def test_has_code_intel_false_when_db_absent(self, client):
        """A DDD with no code_intel.db must report hasCodeIntel=false (no entry)."""
        from core.code_intel import get_code_intel_db_path

        # Find a real brain that has NO code_intel.db on disk.
        brains = client.get("/api/ddd/brains").json()["brains"]
        without = [
            b["name"] for b in brains
            if not get_code_intel_db_path(b["name"]).exists()
        ]
        assert without, "expected at least one DDD without a code_intel.db"
        detail = client.get(f"/api/ddd/brains/{without[0]}").json()
        assert detail["hasCodeIntel"] is False


class TestResilience:
    """Gate-2 adversarial finding: a non-UTF-8 ② doc must NOT 500 the gallery."""

    def test_non_utf8_doc_does_not_crash_gallery(self, client, tmp_path, monkeypatch):
        """A canonical doc with invalid UTF-8 bytes → read_text raises
        UnicodeDecodeError (a ValueError, NOT OSError). The gallery must degrade
        (that project's sinking count = 0), never return 500. Mutation check:
        revert the `except (OSError, ValueError, UnicodeError)` to `except OSError`
        and this test goes RED (the request 500s)."""
        import routers.ddd_brain as mod

        # Build a fake Projects/ with one project carrying a non-UTF-8 TECH.md.
        projects = tmp_path / "Projects"
        proj = projects / "BadUtf8"
        (proj / "2-understanding").mkdir(parents=True)
        (proj / ".project.json").write_text('{"name":"BadUtf8"}')
        # 0xFF is invalid UTF-8 → read_text(encoding="utf-8") raises UnicodeDecodeError.
        (proj / "2-understanding" / "TECH.md").write_bytes(b"# T\n- **x** stuff \xff\xfe bad")

        monkeypatch.setattr(mod, "_projects_root", lambda: projects)
        monkeypatch.setattr(mod, "_workspace_root", lambda: tmp_path)

        resp = client.get("/api/ddd/brains")
        assert resp.status_code == 200  # NOT 500
        brains = {b["name"]: b for b in resp.json()["brains"]}
        assert "BadUtf8" in brains
        # the unreadable doc degrades to 0 sinking, doesn't crash.
        assert brains["BadUtf8"]["health"]["sinking"] == 0


class TestPathTraversal:
    """Security: {name} → filesystem path is the only external→FS surface, and one
    review endpoint runs a destructive `git apply -R`. Force the escape (R28/GUI15:
    a security guard needs a test that FORCES the traversal, not just the happy 404).

    Mutation check: delete the `if pd.parent != root: return None` containment line
    in _resolve_brain_dir (ddd_brain.py) and the tmp-dir escape assertion below goes
    RED (the crafted name would resolve to a dir outside Projects/)."""

    def test_resolve_rejects_traversal_names(self, tmp_path, monkeypatch):
        """_resolve_brain_dir must return None for any name that escapes Projects/,
        even when the escape target genuinely exists on disk."""
        import routers.ddd_brain as mod

        projects = tmp_path / "Projects"
        (projects / "RealBrain").mkdir(parents=True)
        (projects / "RealBrain" / ".project.json").write_text('{"name":"RealBrain"}')
        # A real, resolvable dir OUTSIDE Projects/ that a traversal would reach:
        secret = tmp_path / "secret"
        secret.mkdir()
        (secret / ".project.json").write_text('{"name":"secret"}')  # even if it looks like a project

        monkeypatch.setattr(mod, "_projects_root", lambda: projects)
        monkeypatch.setattr(mod, "_workspace_root", lambda: tmp_path)

        # Sanity: the legit direct child resolves.
        assert mod._resolve_brain_dir("RealBrain") is not None
        # Every escape shape → None (containment: pd.parent must be Projects/).
        for evil in (
            "../secret",            # relative parent escape to a real project-looking dir
            "../../etc",            # deep escape
            "RealBrain/../../secret",
            "/etc",                 # absolute path
            "..",                   # the Projects parent itself
            "sub/child",            # a non-direct child (nested) is not a brain
        ):
            assert mod._resolve_brain_dir(evil) is None, f"traversal not blocked: {evil!r}"

    def test_http_traversal_name_404s(self, client):
        """The HTTP surface never serves a path outside Projects/. Starlette
        normalizes `%2e%2e` at routing; the containment check backstops the rest."""
        for evil in ("..", "%2e%2e", "..%2f..%2fetc", "RealBrain%2f..%2f..%2fsecret"):
            resp = client.get(f"/api/ddd/brains/{evil}")
            assert resp.status_code == 404, f"{evil!r} did not 404 (status {resp.status_code})"


def _swarmai_dir() -> Path:
    """Resolve the real SwarmAI project dir from the active workspace."""
    from routers.ddd_brain import _projects_root

    return _projects_root() / "SwarmAI"


class TestSpecsInBrainDetail:
    """AC1: _brain_detail exposes spec-details/*.spec.md filenames (a DERIVED
    PROJECTION), so a DDD owner can find the domain's specs. NOT a _SECTIONS
    entry — a sibling informational field (six-section invariant untouched, R31)."""

    def test_specs_listed_when_present(self, tmp_path, monkeypatch):
        from routers import ddd_brain as m
        from core.project_registry import SPEC_DETAILS_DIR
        pd = tmp_path / "Proj"
        (pd / SPEC_DETAILS_DIR).mkdir(parents=True)
        (pd / SPEC_DETAILS_DIR / "chat-session.spec.md").write_text("# spec")
        (pd / SPEC_DETAILS_DIR / "eval.spec.md").write_text("# spec")
        (pd / SPEC_DETAILS_DIR / "not-a-spec.md").write_text("# ignore")  # only *.spec.md
        detail = m._brain_detail(pd)
        assert detail["specs"] == ["chat-session.spec.md", "eval.spec.md"], detail.get("specs")

    def test_specs_empty_when_absent(self, tmp_path):
        from routers import ddd_brain as m
        pd = tmp_path / "NoSpecs"
        pd.mkdir()
        detail = m._brain_detail(pd)
        assert detail["specs"] == []

    def test_sections_still_six_untouched(self, tmp_path):
        """AC4: adding specs must NOT change _SECTIONS (six-section invariant)."""
        from routers import ddd_brain as m
        assert len(m._SECTIONS) == 6
        pd = tmp_path / "P"
        pd.mkdir()
        detail = m._brain_detail(pd)
        assert len(detail["sections"]) == 6


class TestHealthBlockInBrainDetail:
    """DDD Health Metrics (run 1, backend read side): _brain_detail returns a
    `health` block with admission-passing metrics. Every value recomputed on
    read (noise) OR read from the stored scheduled score (trust) — the GET path
    NEVER calls compute_section_health (the writer). Gate-1 CRITICAL guard."""

    def test_health_block_present_with_keys(self, tmp_path):
        from routers import ddd_brain as m
        pd = tmp_path / "P"
        pd.mkdir()
        detail = m._brain_detail(pd)
        assert "health" in detail
        h = detail["health"]
        for k in ("noise", "trust", "escalationPending", "recall",
                  "diagnostics", "computedAt"):
            assert k in h, f"missing health key {k}: {h}"

    def test_cultivation_health_surfaced(self, tmp_path):
        """run_abf49550 M0 (AC2): the learning-organ health (write_failed +
        silent_learning_failure) is READ from the durable per-project sink and
        surfaced in _brain_health — no more write-only counters. A recorded write
        failure must make it visible here."""
        from routers import ddd_brain as m
        from core.ddd_cultivation import record_cultivation_outcome
        pd = tmp_path / "P"
        pd.mkdir()
        # a real write failure was recorded for this project
        record_cultivation_outcome(pd, {
            "applied": 1, "rejected": 0, "write_failed": 1, "escalated": 0,
        })
        h = m._brain_detail(pd)["health"]
        assert "cultivation" in h, f"cultivation health must be surfaced: {h}"
        c = h["cultivation"]
        assert c["write_failed"] == 1
        assert c["silent_learning_failure"] is True

    def test_cultivation_health_clean_when_no_outcomes(self, tmp_path):
        """No recorded outcomes → cultivation block present, not flagged (honest
        zero, never fabricated)."""
        from routers import ddd_brain as m
        pd = tmp_path / "P"
        pd.mkdir()
        h = m._brain_detail(pd)["health"]
        assert "cultivation" in h
        assert h["cultivation"]["silent_learning_failure"] is False

    def test_recall_is_experimental_null(self, tmp_path):
        """recall carries no fabricated value — {value:None, experimental:True}
        (no cheap per-DDD recall metric exists; recall_suite is pinned-corpus)."""
        from routers import ddd_brain as m
        pd = tmp_path / "P"
        pd.mkdir()
        h = m._brain_detail(pd)["health"]
        assert h["recall"] == {"value": None, "experimental": True}

    def test_noise_non_vacuous(self, tmp_path):
        """noise.reclaimable equals a DIRECT compute_reclaimable_noise count over
        the ② docs (not a stub). Mutation-anchor: build a doc with a known-stale
        reclaimable guideline entry and assert the count reflects it."""
        from datetime import date
        from routers import ddd_brain as m
        from core.ddd_entry_lifecycle import (
            compute_reclaimable_noise, parse_entries, MEMORY_EVERGREEN_SECTIONS,
        )
        from core.ddd_paths import ddd_path
        from core.project_registry import DDD_CANONICAL_DOCS
        pd = tmp_path / "P"
        pd.mkdir()
        # Plant one reclaimable-noise guideline (ref 0, dormant, aged past grace) in
        # the FIRST canonical doc, at its real ddd_path location.
        doc = DDD_CANONICAL_DOCS[0]
        p = ddd_path(pd, doc)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            "## Guidelines\n"
            "- [guideline] **Old disposable note** — stale (2026-01-01)\n"
            "  <!-- ref:0 | last:2026-01-01 | decay:dormant -->\n",
            encoding="utf-8",
        )
        h = m._brain_detail(pd)["health"]
        # Direct computation the helper must match:
        entries = m._parse_all_knowledge_entries(pd)
        direct = compute_reclaimable_noise(
            entries, date.today(), evergreen_sections=MEMORY_EVERGREEN_SECTIONS
        ).noisy
        assert h["noise"]["reclaimable"] == direct
        assert direct >= 1, "planted a reclaimable entry — must be counted"

    def test_trust_null_and_no_write_when_no_scheduled_score(self, tmp_path):
        """Gate-1 CRITICAL: a GET must NOT write section_health.json. When the
        stored score is absent, trust/diagnostics are None and NO file is created
        (compute_section_health, the writer, is never called from the read path)."""
        from routers import ddd_brain as m
        pd = tmp_path / "P"
        pd.mkdir()
        health_file = pd / ".artifacts" / "section_health.json"
        assert not health_file.exists()
        h = m._brain_detail(pd)["health"]
        assert h["trust"] is None
        assert h["diagnostics"] is None
        assert h["computedAt"] is None
        # THE no-write assertion — the whole point of the Gate-1 fix:
        assert not health_file.exists(), "GET path wrote section_health.json — forbidden"

    def test_summary_health_unchanged(self, tmp_path):
        """_brain_summary (gallery) is NOT enriched — its health keeps exactly the
        4 gallery keys (no per-open compute N-globs the gallery)."""
        from routers import ddd_brain as m
        pd = tmp_path / "P"
        pd.mkdir()
        summ = m._brain_summary(pd)
        assert set(summ["health"].keys()) == {
            "sinking", "pending", "uncommitted", "lastChangeRelative"
        }


class TestUnifiedBrainStateBuilder:
    """Cycle-1 unify: a SINGLE build_brain_state(project_dir, *, with_noise) is the
    sole state/health constructor. _brain_summary and _brain_detail both delegate to
    it — no second, independent health builder (the fork this run kills).

    The perf invariant: with_noise=False (gallery) provably never calls
    compute_reclaimable_noise (→ parse_entries), so the gallery cannot N-glob.
    """

    def test_single_builder_exists_and_both_routes_delegate(self):
        from routers import ddd_brain as m
        import inspect
        assert hasattr(m, "build_brain_state"), "the single builder must exist"
        # both callers delegate to it (source-level, R27 no-fork proof)
        assert "build_brain_state" in inspect.getsource(m._brain_summary)
        assert "build_brain_state" in inspect.getsource(m._brain_detail)

    def test_gallery_path_never_computes_noise(self, monkeypatch, tmp_path):
        """with_noise=False MUST NOT call compute_reclaimable_noise (the sole
        expensive op). Counter stays 0 → the gallery is cheap by construction."""
        from routers import ddd_brain as m
        calls = {"n": 0}
        real = m.compute_reclaimable_noise

        def _counting(*a, **k):
            calls["n"] += 1
            return real(*a, **k)

        monkeypatch.setattr(m, "compute_reclaimable_noise", _counting)
        pd = tmp_path / "P"
        (pd / ".artifacts").mkdir(parents=True)
        state = m.build_brain_state(pd, with_noise=False)
        assert calls["n"] == 0, "gallery path called compute_reclaimable_noise — N-glob risk"
        # and the cheap health keys are all present
        assert set(state["health"].keys()) == {
            "sinking", "pending", "uncommitted", "lastChangeRelative"
        }

    def test_detail_path_includes_noise(self, monkeypatch, tmp_path):
        """with_noise=True adds the detail metrics (noise/trust/escalation/recall)
        ON TOP of the cheap base — one shape, superset."""
        from routers import ddd_brain as m
        pd = tmp_path / "P"
        (pd / ".artifacts").mkdir(parents=True)
        state = m.build_brain_state(pd, with_noise=True)
        h = state["health"]
        # cheap base still present (superset, not a different shape)
        assert {"sinking", "pending", "uncommitted", "lastChangeRelative"} <= set(h.keys())
        # detail metrics added
        assert "noise" in h and "trust" in h and "escalationPending" in h and "recall" in h

    def test_detail_includes_recent_activity(self, monkeypatch, tmp_path):
        """Q3 'is it growing?' — detail exposes recentActivity = count of
        ddd-changelog entries in the last 30d (value≠size). A real per-brain
        maintenance signal, summed from the SAME changelog compute_section_health
        already reads (near-zero cost). Absent changelog → honest 0."""
        from routers import ddd_brain as m
        import json as _json
        from datetime import datetime, timezone

        pd = tmp_path / "P"
        (pd / ".artifacts").mkdir(parents=True)
        cl = pd / ".artifacts" / "ddd-changelog.jsonl"
        now = datetime.now(timezone.utc).isoformat()
        cl.write_text(
            "\n".join(
                _json.dumps({"target_doc": "TECH.md", "target_section": "s", "timestamp": now})
                for _ in range(3)
            ),
            encoding="utf-8",
        )
        h = m.build_brain_state(pd, with_noise=True)["health"]
        assert "recentActivity" in h, "Q3 growing signal must be exposed"
        assert h["recentActivity"] == 3

    def test_recent_activity_absent_changelog_is_zero(self, monkeypatch, tmp_path):
        """No changelog → recentActivity is honest 0, never fabricated, never None."""
        from routers import ddd_brain as m
        pd = tmp_path / "P"
        (pd / ".artifacts").mkdir(parents=True)
        h = m.build_brain_state(pd, with_noise=True)["health"]
        assert h["recentActivity"] == 0

    def test_gallery_omits_recent_activity(self, monkeypatch, tmp_path):
        """recentActivity is a DETAIL metric — gallery (with_noise=False) stays the
        cheap 4-key base, never gains it (Principle-1 / perf: gallery is minimal)."""
        from routers import ddd_brain as m
        pd = tmp_path / "P"
        (pd / ".artifacts").mkdir(parents=True)
        h = m.build_brain_state(pd, with_noise=False)["health"]
        assert "recentActivity" not in h

    def _mk_typed_doc(self, pd):
        """A ② canonical doc with typed entries spanning all 3 layers. Canonical
        docs resolve under 2-understanding/ (ddd_path), so write there."""
        from core.ddd_paths import ddd_path
        (pd / ".artifacts").mkdir(parents=True, exist_ok=True)
        tech = ddd_path(pd, "TECH.md")
        tech.parent.mkdir(parents=True, exist_ok=True)
        tech.write_text(
            "# T\n\n## Principles\n"
            "- [principle] **P one** — text\n  <!-- ref:0 | last:none | decay:active -->\n"
            "- [correction] **C one** — text\n  <!-- ref:0 | last:none | decay:active -->\n"
            "## Decisions\n"
            "- [decision] **D one** — text\n  <!-- ref:0 | last:none | decay:active -->\n"
            "- [model] **M one** — text\n  <!-- ref:0 | last:none | decay:active -->\n"
            "## Guidelines\n"
            "- [guideline] **G one** — text\n  <!-- ref:0 | last:none | decay:active -->\n"
            "- [pitfall] **PIT one** — text\n  <!-- ref:0 | last:none | decay:active -->\n",
            encoding="utf-8",
        )

    def test_typecounts_is_sibling_of_health_both_densities(self, monkeypatch, tmp_path):
        """typeCounts (7-type histogram for the ontology bar) is a SIBLING of health
        (like lifecycleStage), NOT nested inside health — so the gallery's exact
        4-key health shape is preserved. Present on BOTH densities."""
        from routers import ddd_brain as m
        pd = tmp_path / "P"
        self._mk_typed_doc(pd)
        for wn in (False, True):
            st = m.build_brain_state(pd, with_noise=wn)
            assert "typeCounts" in st, f"typeCounts must be a top-level sibling (with_noise={wn})"
            assert "typeCounts" not in st["health"], "must NOT be nested in health (breaks exact-key test)"
            tc = st["typeCounts"]
            # all 7 keys present, real counts from the doc above
            assert set(tc.keys()) == {"guideline", "pitfall", "decision", "model", "process", "principle", "correction"}
            assert tc["principle"] == 1 and tc["correction"] == 1
            assert tc["decision"] == 1 and tc["model"] == 1
            assert tc["guideline"] == 1 and tc["pitfall"] == 1
            assert tc["process"] == 0

    def test_gallery_health_still_exactly_four_keys(self, monkeypatch, tmp_path):
        """Regression guard for the sibling decision: adding typeCounts must NOT
        leak into health — the gallery health stays exactly the 4 cheap keys."""
        from routers import ddd_brain as m
        pd = tmp_path / "P"
        self._mk_typed_doc(pd)
        h = m.build_brain_state(pd, with_noise=False)["health"]
        assert set(h.keys()) == {"sinking", "pending", "uncommitted", "lastChangeRelative"}

    def test_recent_activity_excludes_undated_and_old(self, monkeypatch, tmp_path):
        """A '30d activity' number must count ONLY entries STAMPED within 30d.
        Undated (ts missing/malformed) and >30d-old entries are NOT counted —
        honest under-count beats dishonest inflation (adversarial review Point 1)."""
        from routers import ddd_brain as m
        import json as _json
        from datetime import datetime, timezone, timedelta

        pd = tmp_path / "P"
        (pd / ".artifacts").mkdir(parents=True)
        now = datetime.now(timezone.utc)
        old = (now - timedelta(days=60)).isoformat()
        lines = [
            _json.dumps({"target_doc": "T", "target_section": "s", "timestamp": now.isoformat()}),  # in
            _json.dumps({"target_doc": "T", "target_section": "s", "timestamp": now.isoformat()}),  # in
            _json.dumps({"target_doc": "T", "target_section": "s", "timestamp": old}),              # old → out
            _json.dumps({"target_doc": "T", "target_section": "s"}),                                # undated → out
            _json.dumps({"target_doc": "T", "target_section": "s", "timestamp": "garbage"}),        # malformed → out
        ]
        (pd / ".artifacts" / "ddd-changelog.jsonl").write_text("\n".join(lines), encoding="utf-8")
        h = m.build_brain_state(pd, with_noise=True)["health"]
        assert h["recentActivity"] == 2, "only the 2 in-window stamped entries count"
