"""Tests for DddCultivationOrchestrator — God Object extraction from context_health_hook.

Verifies:
- Orchestrator exists and has run() method (AC1)
- _deep_check delegates to orchestrator (AC2)
- Channel isolation — one failure doesn't affect others (AC3)
"""
import json
import os
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch



class TestWatchPathsDerivation:
    """M1 (run_91bc0651, DDD-alive): _SOURCE_WATCH_PATHS hardcoded → auto-derived.

    A NEW project following the `s_<x>-*` skill-prefix convention gets a
    staleness watch leg with ZERO manual registration (default-enable).
    verify-before-attach: a non-existent prefix attaches nothing (no phantom).
    """

    def test_derive_skill_prefix_allowlisted_suffixes(self):
        from core.ddd_orchestrator import _derive_skill_prefix
        # allowlisted business suffixes → derive
        assert _derive_skill_prefix("CMHK_SalesIntel") == "s_cmhk-"
        assert _derive_skill_prefix("BMS_BIZ") == "s_bms-"
        assert _derive_skill_prefix("Rocky_ISV") == "s_rocky-"
        assert _derive_skill_prefix("") is None

    def test_derive_rejects_non_business_names(self):
        """Gate-2 H3/H4: a naive token-prefix mis-attributes. Non-business
        names must NOT derive (fail-closed allowlist), else GitHub_Community
        would wrongly attach unrelated s_github-* skills."""
        from core.ddd_orchestrator import _derive_skill_prefix
        assert _derive_skill_prefix("GitHub_Community") is None
        assert _derive_skill_prefix("ai_ready_repo") is None
        assert _derive_skill_prefix("SwarmAI") is None       # single token
        assert _derive_skill_prefix("PhysicalAI") is None

    def test_no_unrelated_skill_attached(self):
        """Gate-2 H4 regression guard: GitHub_Community must NOT pull in the
        unrelated s_github-research / s_github-trending standalone skills."""
        from core.ddd_orchestrator import _watch_paths_for, _find_swarmai_root
        root = _find_swarmai_root()
        paths = _watch_paths_for("GitHub_Community", root)
        assert not any("s_github-research" in p or "s_github-trending" in p
                       for p in paths), f"attached unrelated skills: {paths}"

    def test_convention_project_auto_derives_real_skills(self, tmp_path):
        """E2 anchor: a business-suffix project auto-derives its s_<domain>-*
        skills with NO entry in _MANUAL_WATCH_PATHS.

        Uses a SYNTHETIC skills dir (tmp_path) — NOT the real private s_cmhk-*
        skills. Those are .gitignored (local-only), so an external clone has zero
        s_cmhk-*; a test asserting they exist would pass ONLY on the author's
        machine (env-coupled test-theater). This validates the derive LOGIC
        environment-independently. (external-isolation verify, run_ccd1b6c5.)"""
        from core.ddd_orchestrator import _watch_paths_for, _MANUAL_WATCH_PATHS
        # synthetic repo root with two matching skill dirs + one unrelated
        skills = tmp_path / "backend" / "skills"
        (skills / "s_foo-alpha").mkdir(parents=True)
        (skills / "s_foo-beta").mkdir(parents=True)
        (skills / "s_other-thing").mkdir(parents=True)
        assert "Foo_BIZ" not in _MANUAL_WATCH_PATHS  # zero manual reg
        paths = _watch_paths_for("Foo_BIZ", tmp_path)
        assert sorted(paths) == [
            "backend/skills/s_foo-alpha/", "backend/skills/s_foo-beta/",
        ], f"should derive exactly the s_foo-* skills, got {paths}"

    def test_manual_and_derived_union(self):
        """SwarmAI keeps its manual core paths AND gets any derived ones."""
        from core.ddd_orchestrator import _watch_paths_for, _find_swarmai_root
        root = _find_swarmai_root()
        paths = _watch_paths_for("SwarmAI", root)
        # manual core files preserved
        assert "backend/core/session_unit.py" in paths
        assert "backend/main.py" in paths

    def test_no_inrepo_source_attaches_nothing(self):
        """verify-before-attach: a project with no matching skill dir gets an
        empty watch list (falls back to Strategy-1), never a phantom path."""
        from core.ddd_orchestrator import _watch_paths_for, _find_swarmai_root
        root = _find_swarmai_root()
        # PhysicalAI/Rocky_ISV have no s_physicalai-*/s_rocky-* skills
        assert _watch_paths_for("PhysicalAI", root) == []
        assert _watch_paths_for("Rocky_ISV", root) == []

    def test_nonexistent_prefix_no_phantom_path(self):
        """A derived prefix that matches no real dir attaches nothing —
        the watch leg never contains a path that doesn't exist on disk."""
        from core.ddd_orchestrator import _watch_paths_for, _find_swarmai_root
        root = _find_swarmai_root()
        paths = _watch_paths_for("Nonexistent_Xyzzy", root)
        assert paths == [], f"phantom paths derived: {paths}"


class TestOrchestratorExists:
    """AC1: DddCultivationOrchestrator class exists with run() method."""

    def test_import_and_instantiate(self):
        from core.ddd_orchestrator import DddCultivationOrchestrator

        orch = DddCultivationOrchestrator()
        assert hasattr(orch, "run")

    def test_run_returns_findings_list(self, tmp_path):
        from core.ddd_orchestrator import DddCultivationOrchestrator

        # Minimal workspace structure
        (tmp_path / ".context").mkdir()
        (tmp_path / "Projects" / "SwarmAI").mkdir(parents=True)

        orch = DddCultivationOrchestrator()
        findings = orch.run(tmp_path, str(tmp_path))
        assert isinstance(findings, list)

    def test_orchestrator_has_expected_channels(self):
        from core.ddd_orchestrator import DddCultivationOrchestrator

        orch = DddCultivationOrchestrator()
        assert hasattr(orch, "channels")
        assert len(orch.channels) == 11  # 8 original + mechanical_refresh + memory_refresh + llm_refresh


class TestChannelIsolation:
    """AC3: Each channel executes independently — one crash doesn't stop others."""

    def test_one_channel_raises_others_still_run(self, tmp_path):
        from core.ddd_orchestrator import DddCultivationOrchestrator

        (tmp_path / ".context").mkdir()
        (tmp_path / "Projects" / "SwarmAI").mkdir(parents=True)
        (tmp_path / "Projects" / "SwarmAI" / ".artifacts" / "proposals").mkdir(parents=True)

        orch = DddCultivationOrchestrator()

        # Mock first channel to raise
        original_channels = orch.channels.copy()
        call_log = []

        def make_channel(name, should_raise=False):
            def channel_fn(root, ws_path):
                if should_raise:
                    raise RuntimeError(f"Channel {name} exploded")
                call_log.append(name)
                return []
            return channel_fn

        from core.cultivation_dispatcher import EventType
        orch.channels = [
            ("ch_raises", make_channel("ch_raises", should_raise=True), {EventType.SESSION_CLOSE}),
            ("ch_ok_1", make_channel("ch_ok_1"), {EventType.SESSION_CLOSE}),
            ("ch_ok_2", make_channel("ch_ok_2"), {EventType.SESSION_CLOSE}),
        ]

        findings = orch.run(tmp_path, str(tmp_path))

        # Other channels still executed despite first one crashing
        assert "ch_ok_1" in call_log
        assert "ch_ok_2" in call_log

    def test_channel_error_logged_as_finding(self, tmp_path):
        from core.ddd_orchestrator import DddCultivationOrchestrator

        (tmp_path / ".context").mkdir()
        (tmp_path / "Projects" / "SwarmAI").mkdir(parents=True)
        (tmp_path / "Projects" / "SwarmAI" / ".artifacts" / "proposals").mkdir(parents=True)

        orch = DddCultivationOrchestrator()

        def exploding_channel(root, ws_path):
            raise ValueError("boom")

        from core.cultivation_dispatcher import EventType
        orch.channels = [("exploder", exploding_channel, {EventType.SESSION_CLOSE})]

        findings = orch.run(tmp_path, str(tmp_path))
        # Error should be captured, not swallowed silently
        assert any("exploder" in f.lower() or "boom" in f.lower() for f in findings)

    def test_channels_return_findings_merged(self, tmp_path):
        from core.ddd_orchestrator import DddCultivationOrchestrator

        (tmp_path / ".context").mkdir()
        (tmp_path / "Projects" / "SwarmAI").mkdir(parents=True)
        (tmp_path / "Projects" / "SwarmAI" / ".artifacts" / "proposals").mkdir(parents=True)

        orch = DddCultivationOrchestrator()

        def channel_a(root, ws_path):
            return ["finding_A"]

        def channel_b(root, ws_path):
            return ["finding_B"]

        from core.cultivation_dispatcher import EventType
        orch.channels = [("a", channel_a, {EventType.SESSION_CLOSE}), ("b", channel_b, {EventType.SESSION_CLOSE})]

        findings = orch.run(tmp_path, str(tmp_path))
        assert "finding_A" in findings
        assert "finding_B" in findings


class TestDeepCheckDelegation:
    """AC2: context_health_hook._deep_check delegates DDD section to orchestrator."""

    def test_deep_check_calls_orchestrator(self, tmp_path):
        from hooks.context_health_hook import ContextHealthHook

        # Create minimal workspace
        context_dir = tmp_path / ".context"
        context_dir.mkdir()
        (context_dir / "MEMORY.md").write_text("# Memory\n## Open Threads\n")
        (tmp_path / "Knowledge" / "DailyActivity").mkdir(parents=True)
        (tmp_path / "Projects" / "SwarmAI" / ".artifacts" / "proposals").mkdir(parents=True)

        hook = ContextHealthHook()

        # Phase E: _deep_check uses event-driven path when dispatcher.loop is set,
        # fallback to legacy orchestrator.run() only when loop is None.
        # Force legacy path by ensuring dispatcher singleton has loop=None.
        from core.cultivation_dispatcher import get_dispatcher
        dispatcher = get_dispatcher()
        original_loop = dispatcher.loop
        dispatcher.loop = None  # Force legacy fallback

        try:
            # autospec=True: bind to the real DddCultivationOrchestrator API so a
            # renamed/removed/signature-changed .run() fails this test rather than
            # silently passing (mock-masks-breakage precedent — _create_health_todo).
            with patch("core.ddd_orchestrator.DddCultivationOrchestrator", autospec=True) as MockOrch:
                mock_instance = MockOrch.return_value
                mock_instance.run.return_value = ["test_finding"]

                hook._deep_check(tmp_path, str(tmp_path))

                # Verify orchestrator was called via legacy path
                MockOrch.assert_called_once()
                mock_instance.run.assert_called_once_with(tmp_path, str(tmp_path))
        finally:
            dispatcher.loop = original_loop


class TestSourceWatchPaths:
    """DDD staleness detects changes via watched source paths, not just commit message grep."""

    def test_source_watch_paths_config_exists(self):
        # M1 (run_91bc0651): renamed _SOURCE_WATCH_PATHS → _MANUAL_WATCH_PATHS
        # (manual overrides) + auto-derive via _watch_paths_for.
        from core.ddd_orchestrator import _MANUAL_WATCH_PATHS

        assert "AIDLC" in _MANUAL_WATCH_PATHS
        assert "SwarmAI" in _MANUAL_WATCH_PATHS
        assert any("autonomous-pipeline" in p for p in _MANUAL_WATCH_PATHS["AIDLC"])

    def test_staleness_detected_via_watch_path(self, tmp_path):
        """When commit grep finds nothing but watched path has commits, still flags stale."""
        import time
        from core.ddd_orchestrator import DddCultivationOrchestrator

        # Setup: project with old TECH.md (>14 days)
        project_dir = tmp_path / "Projects" / "AIDLC"
        project_dir.mkdir(parents=True)
        tech_file = project_dir / "TECH.md"
        tech_file.write_text("# AIDLC Tech")
        # Set mtime to 20 days ago
        old_time = time.time() - (20 * 86400)
        os.utime(tech_file, (old_time, old_time))

        orch = DddCultivationOrchestrator()

        # Track cwd passed to subprocess calls
        cwd_values = []

        def mock_run(cmd, **kwargs):
            cwd_values.append(kwargs.get("cwd"))
            if "--grep" in cmd:
                # Strategy 1: no commits mention "AIDLC"
                result = MagicMock()
                result.stdout = ""
                return result
            elif "backend/skills/s_autonomous-pipeline/" in cmd:
                # Strategy 2: watched path has commits
                result = MagicMock()
                result.stdout = "abc123 feat(pipeline): add new specialist\n"
                return result
            else:
                result = MagicMock()
                result.stdout = ""
                return result

        # Mock _find_swarmai_root to return a known path
        fake_swarmai = tmp_path / "swarmai"
        (fake_swarmai / "backend").mkdir(parents=True)

        with patch("core.ddd_orchestrator.subprocess.run", side_effect=mock_run), \
             patch("core.ddd_orchestrator._find_swarmai_root", return_value=fake_swarmai):
            findings = orch._ch_ddd_staleness(tmp_path, str(tmp_path))

        assert len(findings) == 1
        assert "AIDLC/TECH.md" in findings[0]
        assert "DDD-STALE" in findings[0]

        # Verify cwd: Strategy 1 uses ws_path, Strategy 2 uses swarmai_root
        assert cwd_values[0] == str(tmp_path)  # Strategy 1: SwarmWS
        assert cwd_values[1] == str(fake_swarmai)  # Strategy 2: swarmai repo

    def test_no_false_positive_when_fresh(self, tmp_path):
        """Fresh DDD docs (<14 days) are NOT flagged even with watched path commits."""
        from core.ddd_orchestrator import DddCultivationOrchestrator

        project_dir = tmp_path / "Projects" / "AIDLC"
        project_dir.mkdir(parents=True)
        tech_file = project_dir / "TECH.md"
        tech_file.write_text("# AIDLC Tech")
        # mtime is NOW (fresh) — no staleness

        orch = DddCultivationOrchestrator()
        with patch("core.ddd_orchestrator.subprocess.run") as mock_run:
            findings = orch._ch_ddd_staleness(tmp_path, str(tmp_path))

        assert findings == []
        mock_run.assert_not_called()  # Should short-circuit on mtime check

    def test_strategy2_batches_all_watch_paths_in_one_call(self, tmp_path):
        """Strategy 2 must query ALL watched paths in a SINGLE git call.

        Regression guard for the cultivation 2s-budget fix: previously this
        spawned one `git log` subprocess PER watched path (N spawns → blew the
        budget, CHANNEL_TIMEOUT ×17/day). Now it must be a single multi-pathspec
        call.
        """
        import time
        from core.ddd_orchestrator import DddCultivationOrchestrator, _MANUAL_WATCH_PATHS

        project_dir = tmp_path / "Projects" / "AIDLC"
        project_dir.mkdir(parents=True)
        tech_file = project_dir / "TECH.md"
        tech_file.write_text("# AIDLC Tech")
        old_time = time.time() - (20 * 86400)
        os.utime(tech_file, (old_time, old_time))

        orch = DddCultivationOrchestrator()
        strategy2_calls = []

        def mock_run(cmd, **kwargs):
            result = MagicMock()
            if "--grep" in cmd:
                result.stdout = ""  # Strategy 1: no message match → fall to Strategy 2
            else:
                strategy2_calls.append(cmd)  # path-scoped Strategy 2 call
                result.stdout = "abc123 commit\n"
            return result

        fake_swarmai = tmp_path / "swarmai"
        (fake_swarmai / "backend").mkdir(parents=True)
        with patch("core.ddd_orchestrator.subprocess.run", side_effect=mock_run), \
             patch("core.ddd_orchestrator._find_swarmai_root", return_value=fake_swarmai):
            findings = orch._ch_ddd_staleness(tmp_path, str(tmp_path))

        assert len(findings) == 1
        # Exactly ONE Strategy-2 git call (batched), not one per watched path.
        assert len(strategy2_calls) == 1
        # That single call includes EVERY watched path for AIDLC.
        cmd = strategy2_calls[0]
        # fake_swarmai has backend/ but no backend/skills/ → derived attaches
        # nothing; watch paths = manual AIDLC entries only.
        for wp in _MANUAL_WATCH_PATHS["AIDLC"]:
            assert wp in cmd, f"watch path {wp} missing from batched git call: {cmd}"

    def test_both_docs_share_single_strategy1_call(self, tmp_path):
        """Both TECH.md + PRODUCT.md stale → Strategy 1 git runs ONCE per project.

        Regression guard: previously the identical `--grep` query ran once per
        stale doc (2× for a project with both docs stale). Now it is computed
        once per project and reused for every stale doc.
        """
        import time
        from core.ddd_orchestrator import DddCultivationOrchestrator

        project_dir = tmp_path / "Projects" / "AIDLC"
        project_dir.mkdir(parents=True)
        old_time = time.time() - (20 * 86400)
        for name in ("TECH.md", "PRODUCT.md"):
            f = project_dir / name
            f.write_text(f"# AIDLC {name}")
            os.utime(f, (old_time, old_time))

        orch = DddCultivationOrchestrator()
        grep_calls = []

        def mock_run(cmd, **kwargs):
            result = MagicMock()
            if "--grep" in cmd:
                grep_calls.append(cmd)
                result.stdout = "abc123 feat(aidlc): change\n"  # Strategy 1 finds commits
            else:
                result.stdout = ""
            return result

        with patch("core.ddd_orchestrator.subprocess.run", side_effect=mock_run):
            findings = orch._ch_ddd_staleness(tmp_path, str(tmp_path))

        # Both docs flagged
        assert len(findings) == 2
        assert any("TECH.md" in f for f in findings)
        assert any("PRODUCT.md" in f for f in findings)
        # Strategy 1 git ran exactly ONCE (deduped), not once per stale doc.
        assert len(grep_calls) == 1


class TestSourceAnchorDrift:
    """Source-anchor staleness: detect when a DDD's bound UPSTREAM source repo
    (declared in bindings.yaml governed_assets[data-source].source_workspace) has
    moved its git HEAD past the last-verified anchor in .refresh_state.json.

    Fail-safe by construction: any missing/unreadable/non-git/errored input →
    ZERO findings, NO exception. Additive to the existing mtime+git-activity path.
    """

    @staticmethod
    def _git(cwd, *args):
        subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)

    def _make_source_repo(self, path: Path) -> str:
        """Create a real git repo with one commit; return full HEAD sha."""
        path.mkdir(parents=True, exist_ok=True)
        self._git(path, "init", "-q")
        self._git(path, "config", "user.email", "t@t.co")
        self._git(path, "config", "user.name", "t")
        (path / "model.smithy").write_text("v1", encoding="utf-8")
        self._git(path, "add", "-A")
        self._git(path, "commit", "-qm", "v1")
        r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=path,
                           capture_output=True, text=True, check=True)
        return r.stdout.strip()

    def _make_project(self, root: Path, name: str, source_ws: Path) -> Path:
        """A DDD project dir with a bindings.yaml declaring a data-source source_workspace."""
        proj = root / "Projects" / name
        proj.mkdir(parents=True, exist_ok=True)
        # fresh TECH.md (NOT mtime-stale) — proves the new check is mtime-INDEPENDENT
        (proj / "TECH.md").write_text("# Tech\ncurrent", encoding="utf-8")
        bindings = (
            "governed_assets:\n"
            "  - kind: data-source\n"
            "    name: upstream-svc\n"
            f"    source_workspace: {source_ws}\n"
            "bindings: []\n"
        )
        (proj / "bindings.yaml").write_text(bindings, encoding="utf-8")
        return proj

    # ── Helper unit: anchor round-trip (AC3) ──
    def test_anchor_round_trip(self, tmp_path):
        from core.ddd_orchestrator import _read_source_anchor, write_source_anchor
        proj = tmp_path / "Projects" / "P"
        proj.mkdir(parents=True)
        assert _read_source_anchor(proj) is None  # nothing stored yet
        write_source_anchor(proj, "abc1234def5678")
        assert _read_source_anchor(proj) == "abc1234def5678"
        # merge-write preserves other keys
        (proj / ".refresh_state.json").write_text(
            json.dumps({"source_anchor_commit": "abc1234def5678", "other": 1}),
            encoding="utf-8")
        write_source_anchor(proj, "9999999")
        data = json.loads((proj / ".refresh_state.json").read_text())
        assert data["source_anchor_commit"] == "9999999"
        assert data["other"] == 1

    # ── Core: drift FIRES when HEAD advanced past anchor (AC1) ──
    def test_drift_fires_when_head_moved(self, tmp_path):
        from core.ddd_orchestrator import (
            DddCultivationOrchestrator, write_source_anchor,
        )
        src = tmp_path / "src"
        head1 = self._make_source_repo(src)
        proj = self._make_project(tmp_path, "IVTHubT", src)
        write_source_anchor(proj, head1)  # anchor == current HEAD → no drift yet

        orch = DddCultivationOrchestrator()
        findings = orch._ch_ddd_staleness(tmp_path, str(tmp_path))
        assert not any("DDD-SOURCE-DRIFT" in f for f in findings), \
            "anchor==HEAD must NOT drift"

        # advance HEAD
        (src / "model.smithy").write_text("v2", encoding="utf-8")
        self._git(src, "add", "-A")
        self._git(src, "commit", "-qm", "v2")

        findings = orch._ch_ddd_staleness(tmp_path, str(tmp_path))
        drift = [f for f in findings if "DDD-SOURCE-DRIFT" in f]
        assert len(drift) == 1, f"expected 1 drift finding, got {findings}"
        assert "IVTHubT" in drift[0]
        assert head1[:7] in drift[0]
        assert "re-verify" in drift[0]

    # ── Fail-safe x3: no anchor / bad path / not-a-git-repo → [] no raise (AC2) ──
    def test_failsafe_no_anchor(self, tmp_path):
        from core.ddd_orchestrator import DddCultivationOrchestrator
        src = tmp_path / "src"
        self._make_source_repo(src)
        self._make_project(tmp_path, "P", src)  # no anchor written
        findings = DddCultivationOrchestrator()._ch_ddd_staleness(tmp_path, str(tmp_path))
        assert not any("DDD-SOURCE-DRIFT" in f for f in findings)

    def test_failsafe_nonexistent_source_path(self, tmp_path):
        from core.ddd_orchestrator import DddCultivationOrchestrator, write_source_anchor
        proj = self._make_project(tmp_path, "P", tmp_path / "does_not_exist")
        write_source_anchor(proj, "deadbeef")
        findings = DddCultivationOrchestrator()._ch_ddd_staleness(tmp_path, str(tmp_path))
        assert not any("DDD-SOURCE-DRIFT" in f for f in findings)

    def test_failsafe_not_a_git_repo(self, tmp_path):
        from core.ddd_orchestrator import DddCultivationOrchestrator, write_source_anchor
        src = tmp_path / "src"
        src.mkdir()  # dir exists but is NOT a git repo
        proj = self._make_project(tmp_path, "P", src)
        write_source_anchor(proj, "deadbeef")
        findings = DddCultivationOrchestrator()._ch_ddd_staleness(tmp_path, str(tmp_path))
        assert not any("DDD-SOURCE-DRIFT" in f for f in findings)

    def test_failsafe_no_bindings_at_all(self, tmp_path):
        from core.ddd_orchestrator import DddCultivationOrchestrator, write_source_anchor
        proj = tmp_path / "Projects" / "P"
        proj.mkdir(parents=True)
        (proj / "TECH.md").write_text("# Tech", encoding="utf-8")
        write_source_anchor(proj, "deadbeef")  # anchor but NO bindings.yaml
        findings = DddCultivationOrchestrator()._ch_ddd_staleness(tmp_path, str(tmp_path))
        assert not any("DDD-SOURCE-DRIFT" in f for f in findings)

    # ── Gate-2 F1: uppercase-stored anchor must NOT false-drift (case-insensitive) ──
    def test_uppercase_anchor_no_false_drift(self, tmp_path):
        from core.ddd_orchestrator import DddCultivationOrchestrator, write_source_anchor
        src = tmp_path / "src"
        head1 = self._make_source_repo(src)  # full 40-char lowercase sha
        proj = self._make_project(tmp_path, "P", src)
        # store the SAME commit but UPPERCASED (simulates a tool/UI copy-paste)
        write_source_anchor(proj, head1.upper())
        findings = DddCultivationOrchestrator()._ch_ddd_staleness(tmp_path, str(tmp_path))
        assert not any("DDD-SOURCE-DRIFT" in f for f in findings), \
            "uppercase anchor of the SAME commit must not fire a false drift"
