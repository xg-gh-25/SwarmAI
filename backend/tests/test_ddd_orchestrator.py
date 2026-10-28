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

import pytest


class TestDddDocLockConvergence:
    """run_06350217 — every DDD-doc writer must lock via ONE shared name.

    The bug: IMPROVEMENT.md was guarded by 3 DIFFERENT lock filenames
    (IMPROVEMENT.lock @ apply_to_ddd, IMPROVEMENT.md.lock @ orchestrator auto-apply,
    .IMPROVEMENT.md.lock @ orchestrator decay), so concurrent writers did NOT
    mutually exclude → lost-update race. Fix: all writers use utils.file_lock.md_lock,
    whose single name derivation (<doc>.md.lock) makes exclusion real.
    """

    def test_md_lock_name_is_single_derivation(self, tmp_path):
        """md_lock derives EXACTLY <doc>.md.lock — the one name all writers converge on."""
        from utils.file_lock import md_lock
        doc = tmp_path / "IMPROVEMENT.md"
        doc.write_text("x", encoding="utf-8")
        with md_lock(doc, blocking=True):
            lock = doc.with_suffix(doc.suffix + ".lock")  # IMPROVEMENT.md.lock
            assert lock.exists(), "md_lock must create <doc>.md.lock"
        # The OLD divergent names must NOT be what md_lock uses.
        assert not (tmp_path / "IMPROVEMENT.lock").exists(), "old apply_to_ddd name"
        assert not (tmp_path / ".IMPROVEMENT.md.lock").exists(), "old decay-site name"

    def test_shared_name_gives_real_mutual_exclusion(self, tmp_path):
        """Two md_lock(blocking=False) on the SAME doc: 2nd is excluded (got=False).

        This is the invariant the convergence buys — proves a shared name = real
        exclusion. Under the OLD 3-name scheme these would be different files and
        BOTH would acquire (the race)."""
        from utils.file_lock import md_lock
        doc = tmp_path / "IMPROVEMENT.md"
        doc.write_text("x", encoding="utf-8")
        with md_lock(doc, blocking=False) as got1:
            assert got1 is True, "first acquire must succeed"
            with md_lock(doc, blocking=False) as got2:
                assert got2 is False, "second acquire on SAME doc must be excluded"

    def test_legacy_ddd_mutators_are_read_only(self, tmp_path, monkeypatch):
        from types import SimpleNamespace
        from core.ingestion import api
        from core.ddd_cultivation import CultivationProposal, apply_to_ddd
        from core.ddd_maturity import promote_section
        doc = tmp_path / "IMPROVEMENT.md"
        doc.write_text("# I\n\n## What Worked\n\n- prior\n")
        original = doc.read_text()
        proposal = CultivationProposal(
            target_doc="IMPROVEMENT.md", target_section="What Worked",
            content="Validate inputs before filesystem operations.",
            source_run_id="run-1", confidence=0.9,
        )
        monkeypatch.setattr(
            api, "enqueue",
            lambda *a, **k: SimpleNamespace(status="queued"),
        )
        assert apply_to_ddd(proposal, tmp_path) == "queued"
        assert promote_section(tmp_path, "IMPROVEMENT.md", "What Worked", "mature") is False
        assert doc.read_text() == original


class TestSkillPrefixDerivation:
    """_derive_skill_prefix (used by recall_multi): business projects only, fail-closed."""

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
        names = {c[0] for c in orch.channels}
        # The 3 auto_refresh channels were REMOVED (run_781ffbd9). Assert by
        # membership, not a brittle count: the refresh channels must be GONE and
        # the load-bearing channels must remain.
        assert {"mechanical_refresh", "memory_refresh", "llm_refresh"}.isdisjoint(names)
        assert {"ddd_staleness", "signal_ddd_bridge"}.issubset(names)
        # The TECH-drift channel wrote a file nothing read, for one project only.
        assert {"auto_apply_proposals", "entry_lifecycle", "code_intel_drift"}.isdisjoint(names)


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


class TestStalenessStrategies:
    """DDD staleness: commit-message grep, then commits in the project's bound repos."""

    def test_staleness_detected_via_the_projects_bound_repo(self, tmp_path):
        """Commit grep finds nothing, but the repo the project's bindings.yaml declares
        has recent commits -> stale (usable-by-anyone P7: any project, any layout)."""
        import subprocess
        import time
        from core.ddd_orchestrator import DddCultivationOrchestrator

        repo = tmp_path / "anyrepo"
        repo.mkdir()
        g = lambda *a: subprocess.run(["git", "-C", str(repo), *a], check=True,
                                      capture_output=True)
        g("init", "-q")
        g("config", "user.email", "t@t")
        g("config", "user.name", "t")
        (repo / "x.txt").write_text("x")
        g("add", "-A")
        g("commit", "-qm", "change")

        project_dir = tmp_path / "Projects" / "Other"
        project_dir.mkdir(parents=True)
        tech_file = project_dir / "TECH.md"
        tech_file.write_text("# Tech")
        old_time = time.time() - (20 * 86400)
        os.utime(tech_file, (old_time, old_time))
        (project_dir / "bindings.yaml").write_text(
            f"governed_assets:\n  - kind: code-repo\n    name: anyrepo\n"
            f"    source_workspace: {repo}\n")
        ws = tmp_path / "ws"
        ws.mkdir()
        subprocess.run(["git", "init", "-q", str(ws)], check=True)

        findings = DddCultivationOrchestrator()._ch_ddd_staleness(tmp_path, str(ws))
        assert findings == ["DDD-STALE: Other/TECH.md (20d old, 1 recent commits)"]

    def test_no_bound_repo_no_strategy2_call(self, tmp_path):
        """A project with no declared repo runs no Strategy-2 git call: nothing stands in
        for a declaration (not the SwarmAI checkout, not a watch list)."""
        import time
        from core.ddd_orchestrator import DddCultivationOrchestrator

        project_dir = tmp_path / "Projects" / "SwarmAI"
        project_dir.mkdir(parents=True)
        tech_file = project_dir / "TECH.md"
        tech_file.write_text("# Tech")
        old_time = time.time() - (20 * 86400)
        os.utime(tech_file, (old_time, old_time))
        calls = []

        def mock_run(cmd, **kwargs):
            calls.append(cmd)
            r = MagicMock()
            r.stdout = ""
            return r

        with patch("core.ddd_orchestrator.subprocess.run", side_effect=mock_run):
            findings = DddCultivationOrchestrator()._ch_ddd_staleness(tmp_path, str(tmp_path))
        assert findings == []
        assert all("--grep" in c for c in calls), calls

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

    # ── Core: drift FIRES when HEAD advanced past anchor (AC1) ──

    # ── Fail-safe x3: no anchor / bad path / not-a-git-repo → [] no raise (AC2) ──
    def test_failsafe_no_anchor(self, tmp_path):
        from core.ddd_orchestrator import DddCultivationOrchestrator
        src = tmp_path / "src"
        self._make_source_repo(src)
        self._make_project(tmp_path, "P", src)  # no anchor written
        findings = DddCultivationOrchestrator()._ch_ddd_staleness(tmp_path, str(tmp_path))
        assert not any("DDD-SOURCE-DRIFT" in f for f in findings)




    # ── Gate-2 F1: uppercase-stored anchor must NOT false-drift (case-insensitive) ──


class TestEntryLifecycleArchiveIsAMove:
    """The decay pass archives an entry ONCE and removes it from the live doc.

    Regression for the archive runaway: the pass used to append an archived entry to
    IMPROVEMENT-archive.md and leave its bullet live (still decay:dormant), so every
    later pass appended it again (one entry reached 1,375 copies).
    """

    # Default fixture: a DATED idle [guideline] past grace, the shape that may leave
    # the live doc (may_leave_live_doc). Before the fix it was archived by the decay
    # pass and left live, so every pass appended it again. An UNDATED entry (the
    # bulk of the real runaway) must instead stay live as dormant: its age cannot be
    # proven, the same rule reclaim applies.
    KEEP = "- [guideline] **Fresh lesson** — still in use. ({today})"

    def _doc(self, root: Path, *, old_meta: str = "decay:dormant", dated: bool = True,
             title: str = "Old idle lesson", entry_type: str = "guideline") -> Path:
        from datetime import date as _d
        self.OLD = f"- [{entry_type}] **{title}** -- something nobody used."
        if dated:
            self.OLD += " (2025-01-01)"
        doc = root / "Projects" / "P" / "2-understanding" / "IMPROVEMENT.md"
        doc.parent.mkdir(parents=True)
        doc.write_text(
            "# Improvement\n\n## What Worked\n\n"
            f"{self.OLD}\n  <!-- ref:0 | last:none | {old_meta} -->\n\n"
            f"{self.KEEP.format(today=_d.today().isoformat())}\n"
            "  <!-- ref:0 | last:none | decay:active -->\n",
            encoding="utf-8",
        )
        return doc

    def _run(self, root: Path) -> list:
        from core.ddd_orchestrator import DddCultivationOrchestrator
        return DddCultivationOrchestrator()._ch_entry_lifecycle(root, str(root))






    def test_decay_move_reclaim_and_gate_select_the_same_entries(self, tmp_path):
        # Parity on a fixture with no duplicate (title, section) pairs.
        from datetime import date as _d
        from core.ddd_entry_lifecycle import (
            compute_reclaimable_noise, may_leave_live_doc, parse_entries,
            reclaim_noise_entries,
        )
        today = _d.today()
        lines = ["# Improvement", "", "## What Worked", ""]
        specs = [("A dated idle", "guideline", " (2025-01-01)", "decay:dormant"),
                 ("B undated idle", "guideline", "", "decay:dormant"),
                 ("C COE09 lesson", "guideline", " (2025-01-01)", "decay:dormant"),
                 ("D dated pitfall", "pitfall", " (2025-01-01)", "decay:archived"),
                 ("E decision", "decision", " (2025-01-01)", "decay:dormant"),
                 ("F fresh", "process", f" ({today.isoformat()})", "decay:dormant")]
        for title, typ, date_s, meta in specs:
            lines += [f"- [{typ}] **{title}** -- body text here.{date_s}",
                      f"  <!-- ref:0 | last:none | {meta} -->", ""]
        content = "\n".join(lines)
        entries = parse_entries(content)
        accepted = {e.title for e in entries if may_leave_live_doc(e, today)}
        assert accepted == {"A dated idle", "D dated pitfall"}
        gate = compute_reclaimable_noise(entries, today)
        assert set(gate.noisy_titles) == accepted
        rep = reclaim_noise_entries(content, today, tmp_path)
        assert set(rep.candidates) == accepted






