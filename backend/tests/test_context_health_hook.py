"""Tests for hooks.context_health_hook — context health harness."""
import json
import logging
import os
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hooks.context_health_hook import ContextHealthHook
from core.session_hooks import HookContext


@pytest.fixture
def hook():
    return ContextHealthHook()


@pytest.fixture
def workspace(tmp_path):
    """Create a minimal SwarmWS workspace."""
    ws = tmp_path / "SwarmWS"
    ws.mkdir()

    # .context/ with context files
    ctx = ws / ".context"
    ctx.mkdir()
    for name in ["SWARMAI.md", "IDENTITY.md", "SOUL.md", "AGENT.md",
                 "USER.md", "STEERING.md", "TOOLS.md", "MEMORY.md",
                 "EVOLUTION.md", "KNOWLEDGE.md", "PROJECTS.md"]:
        (ctx / name).write_text(f"# {name}\n\nContent for {name}\n")

    # Knowledge/ with a note
    notes = ws / "Knowledge" / "Notes"
    notes.mkdir(parents=True)
    (notes / "2026-03-25-test-note.md").write_text(
        "---\ntitle: Test Note\n---\n\n# Test Note\n\nContent.\n"
    )

    designs = ws / "Knowledge" / "Designs"
    designs.mkdir(parents=True)

    da = ws / "Knowledge" / "DailyActivity"
    da.mkdir(parents=True)

    # Projects/
    proj = ws / "Projects" / "TestProject"
    proj.mkdir(parents=True)
    (proj / "TECH.md").write_text("# Tech\n\nArchitecture.\n")
    (proj / "PRODUCT.md").write_text("# Product\n\nVision.\n")

    # Init git repo (configure user for CI where global git config may be absent)
    subprocess.run(["git", "init"], cwd=ws, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=ws, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=ws, capture_output=True)
    subprocess.run(["git", "add", "-A"], cwd=ws, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=ws, capture_output=True)

    return ws


@pytest.fixture
def hook_context():
    return HookContext(
        session_id="test-session",
        agent_id="default",
        message_count=5,
        session_start_time=datetime.now().isoformat(),
        session_title="Test session",
    )


# --------------------------------------------------------------------------
# Light refresh
# --------------------------------------------------------------------------

class TestLightRefresh:
    def test_skips_when_rev_unchanged(self, hook, workspace):
        """Light refresh is a no-op if git HEAD hasn't changed."""
        rev = hook._git_rev(str(workspace))
        hook._last_refresh_rev = rev  # Pretend we already refreshed

        # Should skip git-gated work — verify by checking no write to KNOWLEDGE.md
        # (except Active Projects section which is always refreshed on Projects/ mtime change)
        original = (workspace / ".context" / "KNOWLEDGE.md").read_text()
        # Stub out Bedrock-dependent methods — they hang in sandbox (no network)
        with patch.object(hook, "_sync_knowledge_library"), \
             patch.object(hook, "_sync_transcript_index"), \
             patch.object(hook, "_refresh_knowledge_projects_section"):
            hook._light_refresh(workspace, str(workspace))
        assert (workspace / ".context" / "KNOWLEDGE.md").read_text() == original

    def test_refreshes_knowledge_index(self, hook, workspace):
        """Light refresh updates KNOWLEDGE.md index section."""
        # Add the Knowledge Index section marker
        km = workspace / ".context" / "KNOWLEDGE.md"
        km.write_text("# Knowledge\n\nDomain knowledge.\n\n## Knowledge Index\n\nOld index.\n")

        # Stub out Bedrock-dependent methods — they hang in sandbox (no network)
        with patch.object(hook, "_sync_knowledge_library"), \
             patch.object(hook, "_sync_transcript_index"):
            hook._light_refresh(workspace, str(workspace))

        content = km.read_text()
        assert "test-note" in content.lower() or "Test Note" in content

    def test_extract_title_from_frontmatter(self, hook, tmp_path):
        """Extract title from YAML frontmatter."""
        f = tmp_path / "test.md"
        f.write_text('---\ntitle: "My Title"\n---\n\n# Heading\n')
        assert hook._extract_title(f) == "My Title"

    def test_extract_title_from_heading(self, hook, tmp_path):
        """Extract title from first # heading when no frontmatter."""
        f = tmp_path / "test.md"
        f.write_text("# My Heading\n\nContent.\n")
        assert hook._extract_title(f) == "My Heading"


# --------------------------------------------------------------------------
# Hot/Cold Knowledge Index
# --------------------------------------------------------------------------

class TestHotColdKnowledgeIndex:
    """AC1-AC4: Hot/Cold dual-layer index format."""

    def test_large_dir_shows_hot_5_plus_cold_summary(self, hook, workspace):
        """AC1: Directories with >10 files show only Hot _HOT_ENTRIES (=5) + summary."""
        # Create 15 design files
        designs = workspace / "Knowledge" / "Designs"
        for i in range(15):
            date = f"2026-05-{i+1:02d}"
            (designs / f"{date}-design-{i}.md").write_text(f"# Design {i}\n\nContent.\n")

        km = workspace / ".context" / "KNOWLEDGE.md"
        km.write_text("# Knowledge\n\nDomain.\n\n## Knowledge Index\n\nOld.\n")

        with patch.object(hook, "_sync_knowledge_library"), \
             patch.object(hook, "_sync_transcript_index"):
            hook._light_refresh(workspace, str(workspace))

        content = km.read_text()
        # Should have Hot _HOT_ENTRIES (=5) most recent dates (run_5f040023: 10→5)
        assert "2026-05-15" in content  # Most recent
        assert "2026-05-11" in content  # 5th most recent (Hot tier boundary)
        # The 6th-most-recent (05-10) and older must NOT be in the Hot table
        assert "2026-05-10" not in content
        # Should NOT have the oldest entries
        assert "design-0.md" not in content or "older files" in content.lower() or "+ " in content
        # Should have a cold summary line
        assert "older" in content.lower() or "more" in content.lower()

    def test_small_dir_shows_full_listing(self, hook, workspace):
        """AC2: Directories with ≤10 files keep full listing."""
        library = workspace / "Knowledge" / "Library"
        library.mkdir(parents=True, exist_ok=True)
        for i in range(5):
            (library / f"2026-04-{i+1:02d}-lib-{i}.md").write_text(f"# Lib {i}\n")

        km = workspace / ".context" / "KNOWLEDGE.md"
        km.write_text("# Knowledge\n\n## Knowledge Index\n\nOld.\n")

        with patch.object(hook, "_sync_knowledge_library"), \
             patch.object(hook, "_sync_transcript_index"):
            hook._light_refresh(workspace, str(workspace))

        content = km.read_text()
        # All 5 should be present
        for i in range(5):
            assert f"lib-{i}" in content

    def test_compact_dirs_remain_summary_only(self, hook, workspace):
        """AC3: DailyActivity/JobResults stay as summary line."""
        da = workspace / "Knowledge" / "DailyActivity"
        for i in range(20):
            (da / f"2026-05-{i+1:02d}.md").write_text(f"# Day {i}\n")

        km = workspace / ".context" / "KNOWLEDGE.md"
        km.write_text("# Knowledge\n\n## Knowledge Index\n\nOld.\n")

        with patch.object(hook, "_sync_knowledge_library"), \
             patch.object(hook, "_sync_transcript_index"):
            hook._light_refresh(workspace, str(workspace))

        content = km.read_text()
        # Should have count + pattern, NOT individual files
        assert "20 files" in content
        assert "Pattern:" in content
        # Should NOT have individual file rows
        assert "| 2026-05-01" not in content

    def test_index_line_cap(self, hook, workspace):
        """AC4: Knowledge Index section total ≤120 lines."""
        # Create many files across directories
        for subdir in ["Designs", "Notes", "Learned", "Reports"]:
            d = workspace / "Knowledge" / subdir
            d.mkdir(parents=True, exist_ok=True)
            for i in range(30):
                (d / f"2026-04-{i+1:02d}-item-{i}.md").write_text(f"# Item {i}\n")

        km = workspace / ".context" / "KNOWLEDGE.md"
        km.write_text("# Knowledge\n\n## Knowledge Index\n\nOld.\n")

        with patch.object(hook, "_sync_knowledge_library"), \
             patch.object(hook, "_sync_transcript_index"):
            hook._light_refresh(workspace, str(workspace))

        content = km.read_text()
        # Count lines in index section only
        idx_start = content.find("## Knowledge Index")
        index_section = content[idx_start:]
        index_lines = [l for l in index_section.split("\n") if l.strip()]
        assert len(index_lines) <= 120, f"Index has {len(index_lines)} lines, expected ≤120"


# --------------------------------------------------------------------------
# Deep check
# --------------------------------------------------------------------------

class TestGovernanceBudgets:
    """Guards the LIVE principle-count enforcement gate against the cap drifting
    out of sync with the SOUL/AGENT taxonomy (run_dc0f3c56 Gate-2 finding: the
    advisory string was synced to cap=12 but this enforcing gate still said 5,
    which would emit a false OVER BUDGET on every health run)."""

    def _ctx_with_principles(self, workspace, n: int):
        soul = workspace / ".context" / "SOUL.md"
        body = "# SOUL\n\n" + "".join(
            f"### P{i}: Principle {i}\n\nbody\n\n" for i in range(1, n + 1)
        )
        soul.write_text(body)
        return workspace / ".context"

    def test_seven_principles_within_budget(self, hook, workspace):
        """7 principles (current taxonomy) must NOT be flagged — cap is 12."""
        ctx = self._ctx_with_principles(workspace, 7)
        findings = hook._check_governance_budgets(workspace, ctx)
        assert not any("SOUL.md principles OVER BUDGET" in f for f in findings), \
            f"7 principles wrongly flagged over budget: {findings}"

    def test_twelve_principles_within_budget(self, hook, workspace):
        """12 principles = exactly at cap, still within budget (>12 flags)."""
        ctx = self._ctx_with_principles(workspace, 12)
        findings = hook._check_governance_budgets(workspace, ctx)
        assert not any("SOUL.md principles OVER BUDGET" in f for f in findings), \
            f"12 principles (at cap) wrongly flagged: {findings}"

    def test_thirteen_principles_over_budget(self, hook, workspace):
        """13 principles exceeds cap=12 — gate MUST fire, citing /12 not /5."""
        ctx = self._ctx_with_principles(workspace, 13)
        findings = hook._check_governance_budgets(workspace, ctx)
        over = [f for f in findings if "SOUL.md principles OVER BUDGET" in f]
        assert over, "13 principles should be flagged over budget"
        assert "13/12" in over[0], f"gate must cite the current cap /12: {over[0]}"


class TestDeepCheck:
    def test_detects_empty_context_file(self, hook, workspace, caplog):
        """Deep check flags empty context files."""
        (workspace / ".context" / "SOUL.md").write_text("")
        with caplog.at_level(logging.WARNING, logger="hooks.context_health_hook"):
            hook._deep_check(workspace, str(workspace))
        assert any("EMPTY: SOUL.md" in r.message for r in caplog.records)

    def test_passes_when_healthy(self, hook, workspace, caplog):
        """Deep check passes when all files are healthy (no warnings)."""
        # Create today's DailyActivity so that check passes
        today_file = workspace / "Knowledge" / "DailyActivity" / f"{date.today().isoformat()}.md"
        today_file.write_text("# Today\n\nActivity.\n")

        # Ensure all 4 DDD docs exist for every project (prevents DDD staleness findings)
        for proj_dir in (workspace / "Projects").iterdir():
            if proj_dir.is_dir():
                for doc in ["PRODUCT.md", "TECH.md", "IMPROVEMENT.md", "PROJECT.md"]:
                    p = proj_dir / doc
                    if not p.exists():
                        p.write_text(f"# {doc.replace('.md', '')}\n\nContent.\n")

        # Commit everything so git health check finds no uncommitted files
        subprocess.run(["git", "add", "-A"], cwd=workspace, capture_output=True)
        subprocess.run(["git", "commit", "-m", "add today"], cwd=workspace, capture_output=True)

        with caplog.at_level(logging.INFO, logger="hooks.context_health_hook"):
            hook._deep_check(workspace, str(workspace))

        # If findings were reported, show them for debugging
        warnings = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("deep check passed" in r.message for r in caplog.records), \
            f"Expected 'deep check passed' but got warnings: {warnings}"

    def test_detects_missing_daily_activity(self, hook, workspace, caplog):
        """Deep check flags missing today's DailyActivity file."""
        with caplog.at_level(logging.WARNING, logger="hooks.context_health_hook"):
            hook._deep_check(workspace, str(workspace))
        assert any("MISSING: DailyActivity/" in r.message for r in caplog.records)


class TestDddCompleteness:
    """DDD 4-doc completeness detection — the gap that let CMHK_SalesIntel
    sit with only IMPROVEMENT.md (3 missing docs) for >1 month unwarned."""

    def test_half_created_project_warns(self, hook, workspace):
        """A project with ≥1 but <4 DDD docs is flagged as half-created."""
        # workspace's TestProject has only PRODUCT.md + TECH.md (2/4)
        findings = hook._check_ddd_completeness(workspace)
        assert any("DDD-INCOMPLETE" in f and "TestProject" in f for f in findings), \
            f"Expected TestProject flagged incomplete, got: {findings}"
        # names the missing docs
        joined = " ".join(findings)
        assert "IMPROVEMENT.md" in joined and "PROJECT.md" in joined

    def test_complete_project_silent(self, hook, workspace):
        """A project with all 4 DDD docs produces no completeness finding."""
        proj = workspace / "Projects" / "Complete"
        proj.mkdir(parents=True)
        for doc in ["PRODUCT.md", "TECH.md", "IMPROVEMENT.md", "PROJECT.md"]:
            (proj / doc).write_text(f"# {doc}\n\nContent.\n")
        findings = hook._check_ddd_completeness(workspace)
        assert not any("Complete" in f for f in findings), \
            f"Complete project should not be flagged, got: {findings}"

    def test_zero_doc_dir_not_flagged(self, hook, workspace):
        """A dir with 0 DDD docs is NOT a DDD project — don't flag it."""
        empty = workspace / "Projects" / "NotADddProject"
        empty.mkdir(parents=True)
        (empty / "random.txt").write_text("not a ddd doc")
        findings = hook._check_ddd_completeness(workspace)
        assert not any("NotADddProject" in f for f in findings), \
            f"0-doc dir should not be flagged, got: {findings}"

    def test_wired_into_deep_check(self, hook, workspace, caplog):
        """The completeness check is actually CALLED by _deep_check (not just
        defined). Mutation-guard: if the wiring is removed, this goes RED."""
        # TestProject (2/4 docs) is half-created → deep_check must surface it
        with caplog.at_level(logging.WARNING, logger="hooks.context_health_hook"):
            hook._deep_check(workspace, str(workspace))
        assert any("DDD-INCOMPLETE" in r.message and "TestProject" in r.message
                   for r in caplog.records), \
            "deep_check did not surface the DDD-INCOMPLETE finding — wiring missing"

    def _write_readme(self, workspace, section_names):
        """Helper: write Projects/README.md mentioning the given section names."""
        readme = workspace / "Projects" / "README.md"
        readme.parent.mkdir(parents=True, exist_ok=True)
        body = "# Projects\n\nThe six sections:\n" + "\n".join(
            f"- {n}: desc" for n in section_names
        )
        readme.write_text(body)
        return readme

    def test_readme_six_sections_all_present_silent(self, hook, workspace):
        """A Projects/README.md that mentions all six canonical section names is silent."""
        from core.swarm_workspace_manager import DDD_SIX_SECTION_NAMES
        self._write_readme(workspace, DDD_SIX_SECTION_NAMES)
        findings = hook._check_readme_six_sections(workspace)
        assert findings == [], f"Complete README should be silent, got: {findings}"

    def test_readme_six_sections_missing_one_warns(self, hook, workspace):
        """Dropping one section name from README → a WARN finding naming it."""
        from core.swarm_workspace_manager import DDD_SIX_SECTION_NAMES
        # omit "Refresher" (the last, most-likely-to-be-dropped section)
        self._write_readme(workspace, [n for n in DDD_SIX_SECTION_NAMES if n != "Refresher"])
        findings = hook._check_readme_six_sections(workspace)
        assert any("Refresher" in f for f in findings), \
            f"Expected a finding naming the missing 'Refresher' section, got: {findings}"

    def test_readme_six_sections_absent_file_silent(self, hook, workspace):
        """No Projects/README.md → fail-open silent (nothing to check, not an error)."""
        # workspace has Projects/ but no README.md
        findings = hook._check_readme_six_sections(workspace)
        assert findings == [], f"Absent README must fail-open silent, got: {findings}"

    def test_readme_six_sections_wired_into_deep_check(self, hook, workspace, caplog):
        """The README drift check is actually CALLED by _deep_check (mutation-guard)."""
        from core.swarm_workspace_manager import DDD_SIX_SECTION_NAMES
        self._write_readme(workspace, [n for n in DDD_SIX_SECTION_NAMES if n != "Gates"])
        with caplog.at_level(logging.WARNING, logger="hooks.context_health_hook"):
            hook._deep_check(workspace, str(workspace))
        assert any("README" in r.message and "Gates" in r.message for r in caplog.records), \
            "deep_check did not surface the README six-section drift finding — wiring missing"

    def test_detects_stale_git_lock(self, hook, workspace, caplog):
        """Deep check removes stale .git/index.lock."""
        lock = workspace / ".git" / "index.lock"
        lock.write_text("stale")
        # Make it look old
        old_time = datetime.now().timestamp() - 600
        os.utime(lock, (old_time, old_time))

        findings = hook._check_git_health(workspace, str(workspace))
        assert any("AUTO-FIXED" in f and "index.lock" in f for f in findings)
        assert not lock.exists()

    def test_cache_invalidation(self, hook, workspace):
        """L1 cache invalidated when source file is newer."""
        ctx = workspace / ".context"
        cache = ctx / "L1_SYSTEM_PROMPTS.md"
        cache.write_text("cached content")

        # Make a source file newer than cache
        import time
        time.sleep(0.1)
        (ctx / "MEMORY.md").write_text("# Updated memory\n\nNew content.\n")

        findings: list[str] = []
        hook._check_cache_freshness(ctx, findings)
        assert any("AUTO-FIXED" in f and "L1 cache" in f for f in findings)
        assert not cache.exists()


# --------------------------------------------------------------------------
# Daily gate
# --------------------------------------------------------------------------

class TestDailyGate:
    @pytest.mark.asyncio
    async def test_deep_check_runs_once_per_day(self, hook, workspace, hook_context):
        """Deep check only runs once per calendar day."""
        with patch.object(hook, '_light_refresh'), \
             patch.object(hook, '_deep_check') as mock_deep, \
             patch('hooks.context_health_hook.initialization_manager') as mock_init:
            mock_init.get_cached_workspace_path.return_value = str(workspace)

            await hook.execute(hook_context)
            assert mock_deep.call_count == 1

            await hook.execute(hook_context)
            assert mock_deep.call_count == 1  # Still 1 — same day

    @pytest.mark.asyncio
    async def test_deep_check_runs_on_new_day(self, hook, workspace, hook_context):
        """Deep check runs again on a new calendar day."""
        with patch.object(hook, '_light_refresh'), \
             patch.object(hook, '_deep_check') as mock_deep, \
             patch('hooks.context_health_hook.initialization_manager') as mock_init:
            mock_init.get_cached_workspace_path.return_value = str(workspace)

            await hook.execute(hook_context)
            assert mock_deep.call_count == 1

            # Simulate next day
            hook._last_deep_date = "2026-03-24"
            await hook.execute(hook_context)
            assert mock_deep.call_count == 2


# --------------------------------------------------------------------------
# DDD staleness
# --------------------------------------------------------------------------

class TestDDDStaleness:
    def test_detects_stale_tech_md(self, hook, workspace):
        """Flags TECH.md older than 14 days with recent commits."""
        tech = workspace / "Projects" / "TestProject" / "TECH.md"
        # Make TECH.md 20 days old
        old_time = datetime.now().timestamp() - (20 * 86400)
        os.utime(tech, (old_time, old_time))

        # Add a recent commit mentioning the project name
        result = subprocess.run(
            ["git", "commit", "--allow-empty", "-m", "fix: TestProject update"],
            cwd=workspace, capture_output=True, text=True,
        )
        assert result.returncode == 0, f"git commit failed: {result.stderr}"

        # Verify the commit is findable
        verify = subprocess.run(
            ["git", "log", "--oneline", "--since=14 days ago",
             "--grep", "TestProject", "--", "."],
            cwd=workspace, capture_output=True, text=True,
        )

        findings = hook._check_ddd_staleness(workspace, str(workspace))
        # If git log can find the commit, we should detect staleness
        if verify.stdout.strip():
            assert any("DDD-STALE" in f and "TestProject" in f for f in findings)
        else:
            # Git may not find it due to date precision — skip gracefully
            pass

    def test_no_staleness_when_recently_updated(self, hook, workspace):
        """No staleness flag when DDD docs are recent."""
        findings = hook._check_ddd_staleness(workspace, str(workspace))
        assert not any("DDD-STALE" in f for f in findings)


# --------------------------------------------------------------------------
# Auto-cultivation
# --------------------------------------------------------------------------

class TestAutoCultivation:
    """Tests for _auto_cultivate_pipeline_lessons."""

    def _make_run(self, workspace, project, run_id, *, lessons=None,
                  cultivated=False, status="completed"):
        """Create a run.json with a reflect stage."""
        runs_dir = workspace / "Projects" / project / ".artifacts" / "runs" / run_id
        runs_dir.mkdir(parents=True, exist_ok=True)
        stages = []
        if lessons is not None:
            reflect_stage = {"stage": "reflect", "status": "completed", "lessons": lessons}
            if cultivated:
                reflect_stage["cultivated"] = True
            stages.append(reflect_stage)
        run_data = {
            "id": run_id,
            "project": project,
            "status": status,
            "stages": stages,
        }
        (runs_dir / "run.json").write_text(json.dumps(run_data), encoding="utf-8")
        return runs_dir / "run.json"

    def test_inflight_run_not_consumed(self, hook, workspace):
        """Gate-2 fix: a run whose status is non-terminal (running/paused) must
        NOT be marked cultivated — else a mid-pipeline hook event consumes it,
        HOLD-BACKs every MEMORY lesson as 'unqualified', and never retries after
        the run completes (silently defeats the feature for actively-worked runs).
        The run must be left un-cultivated so a later session re-processes it.
        """
        proj = workspace / "Projects" / "TestProject"
        (proj / "IMPROVEMENT.md").write_text("# Lessons\n\n## What Worked\n\n- x\n")
        run_file = self._make_run(
            workspace, "TestProject", "run_inflight",
            lessons=["Use nc -z instead of lsof for port checks on macOS"],
            status="running",
        )
        hook._auto_cultivate_pipeline_lessons(workspace)
        run_data = json.loads(run_file.read_text(encoding="utf-8"))
        reflect_stage = next(s for s in run_data["stages"] if s["stage"] == "reflect")
        assert "cultivated" not in reflect_stage, (
            "an in-flight (status=running) run was consumed (cultivated=True) — "
            "its MEMORY lessons are now permanently held back and never retried"
        )

    def test_cultivates_uncultivated_run(self, hook, workspace):
        """Cultivates a completed run with reflect.lessons and no cultivated flag."""
        # Create project with DDD docs (needed for cultivation target)
        proj = workspace / "Projects" / "TestProject"
        (proj / "IMPROVEMENT.md").write_text(
            "# Lessons\n\n## What Worked\n\n- existing\n\n## What Failed\n\n- nothing\n"
        )

        run_file = self._make_run(
            workspace, "TestProject", "run_abc123",
            lessons=["Use nc -z instead of lsof for port checks"]
        )

        hook._auto_cultivate_pipeline_lessons(workspace)

        # Verify cultivated:true was set
        run_data = json.loads(run_file.read_text(encoding="utf-8"))
        reflect_stage = next(s for s in run_data["stages"] if s["stage"] == "reflect")
        assert reflect_stage["cultivated"] is True

    def test_skips_already_cultivated(self, hook, workspace):
        """Skips runs that already have cultivated:true."""
        proj = workspace / "Projects" / "TestProject"
        (proj / "IMPROVEMENT.md").write_text("# Lessons\n\n## What Worked\n\n- x\n")

        run_file = self._make_run(
            workspace, "TestProject", "run_def456",
            lessons=["Some lesson"], cultivated=True
        )

        # Should not re-cultivate (file unchanged)
        original_content = run_file.read_text()
        hook._auto_cultivate_pipeline_lessons(workspace)
        assert run_file.read_text() == original_content

    def test_skips_run_without_lessons(self, hook, workspace):
        """Skips runs whose reflect stage has no lessons."""
        proj = workspace / "Projects" / "TestProject"
        (proj / "IMPROVEMENT.md").write_text("# Lessons\n\n## What Worked\n\n- x\n")

        run_file = self._make_run(
            workspace, "TestProject", "run_ghi789",
            lessons=[]
        )

        original_content = run_file.read_text()
        hook._auto_cultivate_pipeline_lessons(workspace)
        # No cultivated flag should be added for empty lessons
        run_data = json.loads(run_file.read_text(encoding="utf-8"))
        reflect_stage = next(s for s in run_data["stages"] if s["stage"] == "reflect")
        assert "cultivated" not in reflect_stage

    def test_handles_missing_reflect_stage(self, hook, workspace):
        """Gracefully handles runs without a reflect stage."""
        proj = workspace / "Projects" / "TestProject"
        (proj / "IMPROVEMENT.md").write_text("# Lessons\n\n## What Worked\n\n- x\n")

        self._make_run(workspace, "TestProject", "run_jkl012", lessons=None)
        # Should not raise
        hook._auto_cultivate_pipeline_lessons(workspace)

    def test_handles_corrupt_json(self, hook, workspace, caplog):
        """Gracefully handles corrupt run.json files."""
        proj = workspace / "Projects" / "TestProject"
        proj.mkdir(parents=True, exist_ok=True)
        runs_dir = proj / ".artifacts" / "runs" / "run_bad"
        runs_dir.mkdir(parents=True, exist_ok=True)
        (runs_dir / "run.json").write_text("not valid json{{{")

        with caplog.at_level(logging.DEBUG):
            hook._auto_cultivate_pipeline_lessons(workspace)
        # Should not raise — just log and continue

    def test_does_not_mark_cultivated_on_failure(self, hook, workspace):
        """cultivated:true is NOT set when cultivate_from_reflect raises."""
        proj = workspace / "Projects" / "TestProject"
        (proj / "IMPROVEMENT.md").write_text("# Lessons\n\n## What Worked\n\n- x\n")

        run_file = self._make_run(
            workspace, "TestProject", "run_fail_test",
            lessons=["Some lesson that will fail"]
        )

        with patch("core.ddd_cultivation.cultivate_from_reflect", side_effect=RuntimeError("boom")):
            hook._auto_cultivate_pipeline_lessons(workspace)

        # cultivated should NOT be set
        run_data = json.loads(run_file.read_text(encoding="utf-8"))
        reflect_stage = next(s for s in run_data["stages"] if s["stage"] == "reflect")
        assert "cultivated" not in reflect_stage or reflect_stage.get("cultivated") is not True

    def test_idempotent_multiple_calls(self, hook, workspace):
        """Multiple calls don't re-cultivate already-done runs."""
        proj = workspace / "Projects" / "TestProject"
        (proj / "IMPROVEMENT.md").write_text(
            "# Lessons\n\n## What Worked\n\n- existing\n\n## What Failed\n\n- nothing\n"
        )

        self._make_run(
            workspace, "TestProject", "run_idempotent",
            lessons=["Pattern: always validate threshold against real data"]
        )

        # First call cultivates
        hook._auto_cultivate_pipeline_lessons(workspace)
        # Second call skips (cultivated:true now set)
        hook._auto_cultivate_pipeline_lessons(workspace)

        # Should still only be cultivated once
        run_file = workspace / "Projects" / "TestProject" / ".artifacts" / "runs" / "run_idempotent" / "run.json"
        run_data = json.loads(run_file.read_text(encoding="utf-8"))
        reflect_stage = next(s for s in run_data["stages"] if s["stage"] == "reflect")
        assert reflect_stage["cultivated"] is True


class TestAutoSessionSignalCultivation:
    """Tests for _auto_cultivate_session_signals — Ch5 + Ch6 feed."""

    def _write_jsonl(self, da_dir, filename, records):
        """Write records to a JSONL file in the DailyActivity dir."""
        path = da_dir / filename
        with open(path, "w", encoding="utf-8") as f:
            for rec in records:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return path

    def test_cultivates_corrections_from_jsonl(self, hook, workspace):
        """Corrections in JSONL get routed to DDD docs (Ch6)."""
        # Setup project with IMPROVEMENT.md that has the target sections
        proj = workspace / "Projects" / "SwarmAI"
        proj.mkdir(parents=True, exist_ok=True)
        (proj / "IMPROVEMENT.md").write_text(
            "# Lessons\n\n## What Worked\n\n- seed\n\n## What Failed\n\n- seed\n"
        )
        (proj / "TECH.md").write_text(
            "# Tech\n\n## Architecture\n\n- seed\n\n## Runtime Traps\n\n- seed\n\n## Conventions\n\n- seed\n"
        )

        # Create DailyActivity JSONL with a correction
        da_dir = workspace / "Knowledge" / "DailyActivity"
        da_dir.mkdir(parents=True, exist_ok=True)
        today = date.today().isoformat()
        self._write_jsonl(da_dir, f"{today}.jsonl", [{
            "session_id": "session_corr_001",
            "timestamp": "14:00",
            "corrections": [
                "Bug: daemon crashed because subprocess.run() blocks the event loop — must use asyncio.to_thread",
            ],
            "decisions": [],
        }])

        hook._auto_cultivate_session_signals(workspace)

        # Verify state file was created with the session ID
        state_path = workspace / ".context" / ".session_cultivated.json"
        assert state_path.exists()
        state = json.loads(state_path.read_text())
        assert "session_corr_001" in state

    def test_cultivates_decisions_from_jsonl(self, hook, workspace):
        """Decisions in JSONL get routed to DDD docs (Ch5)."""
        proj = workspace / "Projects" / "SwarmAI"
        proj.mkdir(parents=True, exist_ok=True)
        (proj / "TECH.md").write_text(
            "# Tech\n\n## Conventions\n\n- seed\n\n## Runtime Traps\n\n- seed\n"
        )

        da_dir = workspace / "Knowledge" / "DailyActivity"
        da_dir.mkdir(parents=True, exist_ok=True)
        today = date.today().isoformat()
        self._write_jsonl(da_dir, f"{today}.jsonl", [{
            "session_id": "session_dec_001",
            "timestamp": "15:00",
            "corrections": [],
            "decisions": [
                "Convention: prefer nc -z over lsof for all port checks in daemon scripts — standing rule",
            ],
        }])

        # The MEMORY/DDD admission judge is an LLM (Bedrock) call that FAILS CLOSED
        # to "discard" with no backend (the test env). commit 2c8fc37f routed this
        # cultivation door through that judge (P8: judge is the sole admit authority),
        # so the write no longer happens on a raw fixture. Stub the judge to a
        # deterministic pass — this test asserts the ROUTING/WRITE behavior, not the
        # judge's semantics (that lives in test_admission_band). Patch at the
        # ddd_cultivation call site (admission_band → self_adversarial_judge).
        import core.ddd_cultivation as _dc
        with patch.object(_dc, "self_adversarial_judge", lambda *a, **k: ("pass", "t")):
            hook._auto_cultivate_session_signals(workspace)

        # Verify the decision was applied to TECH.md
        content = (proj / "TECH.md").read_text()
        assert "nc -z" in content or "port checks" in content

    def test_idempotent_skips_already_cultivated_sessions(self, hook, workspace):
        """Same session is never cultivated twice."""
        proj = workspace / "Projects" / "SwarmAI"
        proj.mkdir(parents=True, exist_ok=True)
        (proj / "TECH.md").write_text(
            "# Tech\n\n## Conventions\n\n- seed\n\n## Runtime Traps\n\n- seed\n"
        )

        da_dir = workspace / "Knowledge" / "DailyActivity"
        da_dir.mkdir(parents=True, exist_ok=True)
        today = date.today().isoformat()
        self._write_jsonl(da_dir, f"{today}.jsonl", [{
            "session_id": "session_idem_001",
            "timestamp": "16:00",
            "corrections": [],
            "decisions": [
                "Pattern: always use atomic tmp+rename for file writes to prevent corruption",
            ],
        }])

        # First call cultivates
        hook._auto_cultivate_session_signals(workspace)
        content_after_first = (proj / "TECH.md").read_text()

        # Second call should be no-op (session already in state)
        hook._auto_cultivate_session_signals(workspace)
        content_after_second = (proj / "TECH.md").read_text()

        assert content_after_first == content_after_second

    def test_skips_old_jsonl_files(self, hook, workspace):
        """JSONL files older than 7 days are not processed."""
        proj = workspace / "Projects" / "SwarmAI"
        proj.mkdir(parents=True, exist_ok=True)
        (proj / "TECH.md").write_text("# Tech\n\n## Conventions\n\n- seed\n")

        da_dir = workspace / "Knowledge" / "DailyActivity"
        da_dir.mkdir(parents=True, exist_ok=True)
        # Create a file from 10 days ago
        old_date = (date.today() - timedelta(days=10)).isoformat()
        self._write_jsonl(da_dir, f"{old_date}.jsonl", [{
            "session_id": "session_old_001",
            "timestamp": "10:00",
            "corrections": ["This pattern always fails — never use it again"],
            "decisions": [],
        }])

        hook._auto_cultivate_session_signals(workspace)

        # State should be empty — nothing processed
        state_path = workspace / ".context" / ".session_cultivated.json"
        if state_path.exists():
            state = json.loads(state_path.read_text())
            assert "session_old_001" not in state

    def test_handles_empty_corrections_and_decisions(self, hook, workspace):
        """Sessions with no corrections and no decisions are marked done but don't modify DDD."""
        proj = workspace / "Projects" / "SwarmAI"
        proj.mkdir(parents=True, exist_ok=True)
        (proj / "TECH.md").write_text("# Tech\n\n## Conventions\n\n- seed\n")

        da_dir = workspace / "Knowledge" / "DailyActivity"
        da_dir.mkdir(parents=True, exist_ok=True)
        today = date.today().isoformat()
        self._write_jsonl(da_dir, f"{today}.jsonl", [{
            "session_id": "session_empty_001",
            "timestamp": "11:00",
            "corrections": [],
            "decisions": [],
        }])

        hook._auto_cultivate_session_signals(workspace)

        # Session marked as cultivated (skip in future) but no DDD changes
        state_path = workspace / ".context" / ".session_cultivated.json"
        assert state_path.exists()
        state = json.loads(state_path.read_text())
        assert "session_empty_001" in state

        # DDD unchanged
        content = (proj / "TECH.md").read_text()
        assert content == "# Tech\n\n## Conventions\n\n- seed\n"

    def test_graceful_on_missing_project_dir(self, hook, workspace):
        """Does not crash when Projects/SwarmAI doesn't exist."""
        # Don't create SwarmAI project dir
        da_dir = workspace / "Knowledge" / "DailyActivity"
        da_dir.mkdir(parents=True, exist_ok=True)
        today = date.today().isoformat()
        self._write_jsonl(da_dir, f"{today}.jsonl", [{
            "session_id": "session_no_proj",
            "timestamp": "12:00",
            "corrections": ["Some correction that should not crash"],
            "decisions": [],
        }])

        # Should not raise
        hook._auto_cultivate_session_signals(workspace)


# --------------------------------------------------------------------------
# PROJECTS.md refresh after cultivation (line number drift fix)
# --------------------------------------------------------------------------


class TestProjectsRefreshAfterCultivation:
    """Verify PROJECTS.md is refreshed when cultivation modifies DDD docs."""

    @pytest.mark.asyncio
    async def test_refresh_called_when_cultivation_writes(self, hook, workspace):
        """When cultivation applies content to DDD docs, refresh_projects_index is called."""
        # Setup: project with DDD docs (needs TECH.md with Conventions section
        # because the lesson contains TECH_KEYWORDS like "nc -z")
        proj = workspace / "Projects" / "SwarmAI"
        proj.mkdir(parents=True, exist_ok=True)
        (proj / "IMPROVEMENT.md").write_text(
            "# Lessons\n\n## What Worked\n\n- seed\n\n## What Failed\n\n- seed\n"
        )
        (proj / "TECH.md").write_text(
            "# Tech\n\n## Conventions\n\n- seed\n\n## Runtime Traps\n\n- seed\n"
        )

        # Create a run with uncultivated lessons
        runs_dir = proj / ".artifacts" / "runs" / "run_drift001"
        runs_dir.mkdir(parents=True, exist_ok=True)
        run_data = {
            "id": "run_drift001",
            "project": "SwarmAI",
            "status": "completed",
            "stages": [{"stage": "reflect", "status": "completed",
                        "lessons": ["Pattern: use nc -z instead of lsof for port checks"]}],
        }
        (runs_dir / "run.json").write_text(json.dumps(run_data), encoding="utf-8")

        # Stub the LLM admission judge to a deterministic pass (see the note in
        # test_cultivates_decisions_from_jsonl): with no Bedrock backend it fails
        # closed to "discard", so nothing would be written and _ddd_docs_modified
        # would stay False. This test asserts the refresh-on-write WIRING, not the
        # judge's semantics. Patch both call sites (DDD cultivation + MEMORY door).
        import core.ddd_cultivation as _dc
        import core.ingestion_gate as _ig
        # Also stub _sync_knowledge_library: _light_refresh drives the full Knowledge-
        # Library aiosqlite sync, which hangs on the connection worker thread in the
        # test env (unrelated to this test — it asserts the cultivation→refresh wiring).
        with patch(
            "hooks.context_health_hook.ContextHealthHook._refresh_projects_index_sync"
        ) as mock_refresh, \
             patch.object(hook, "_sync_knowledge_library", lambda *a, **k: None), \
             patch.object(_dc, "self_adversarial_judge", lambda *a, **k: ("pass", "t")), \
             patch.object(_ig, "self_adversarial_judge", lambda *a, **k: ("pass", "t")):
            hook._light_refresh(workspace, str(workspace))

            # Cultivation should have written to DDD docs and set dirty flag
            assert hook._ddd_docs_modified is True
            mock_refresh.assert_called_once_with(workspace)

    @pytest.mark.asyncio
    async def test_refresh_not_called_when_no_cultivation(self, hook, workspace):
        """When cultivation has nothing to apply, refresh is NOT called."""
        # Setup: project with DDD docs but no uncultivated runs
        proj = workspace / "Projects" / "SwarmAI"
        proj.mkdir(parents=True, exist_ok=True)
        (proj / "IMPROVEMENT.md").write_text(
            "# Lessons\n\n## What Worked\n\n- seed\n\n## What Failed\n\n- seed\n"
        )

        # Pre-set Projects/ mtime so no filesystem change is detected
        projects_dir = workspace / "Projects"
        if projects_dir.is_dir():
            hook._last_projects_mtime = projects_dir.stat().st_mtime

        with patch(
            "hooks.context_health_hook.ContextHealthHook._refresh_projects_index_sync"
        ) as mock_refresh:
            hook._light_refresh(workspace, str(workspace))

            # No cultivation happened AND no Projects/ mtime change — no refresh
            assert hook._ddd_docs_modified is False
            mock_refresh.assert_not_called()


class TestExtractLessonsNoOrphan:
    """R-1 C2: _extract_lessons_to_memory must not orphan an existing meta.

    Root cause (run_55c02bbe): the raw-splice insert math landed a new entry+meta
    BETWEEN an existing bullet and its meta, orphaning the existing meta as a 2nd
    consecutive metadata line. Fix routes through _modify_content (section-boundary
    insert) so the orphan is structurally impossible.
    """

    def test_extract_does_not_orphan_existing_meta(self, hook, tmp_path):
        ws = tmp_path / "SwarmWS"
        ctx = ws / ".context"
        ctx.mkdir(parents=True)
        # MEMORY.md with an existing Guidelines entry that already has its meta
        (ctx / "MEMORY.md").write_text(
            "## Guidelines\n"
            "_Operational lessons._\n\n"
            "- [guideline] **Existing entry** — prior lesson (2026-06-01)\n"
            "  <!-- ref:3 | last:2026-06-20 | decay:active -->\n"
        )
        # Stub the LLM admission judge to a deterministic pass (see the note in
        # test_cultivates_decisions_from_jsonl): _extract_lessons_to_memory routes
        # through admit_memory_lesson → ingestion_gate → self_adversarial_judge,
        # which fails closed to "discard" with no Bedrock backend, so the lesson
        # would never be written. This test asserts the WRITER's insert math (no
        # orphaned meta, newest-at-top), not the judge's admit decision.
        import core.ingestion_gate as _ig
        with patch.object(_ig, "self_adversarial_judge", lambda *a, **k: ("pass", "t")):
            # Extract a new confident lesson into MEMORY.md
            hook._extract_lessons_to_memory(
                ws, ["Always verify before asserting — a new guideline lesson here"],
                "run_test", "TestProject",
            )
        content = (ctx / "MEMORY.md").read_text()
        # No bullet may be followed by TWO consecutive metadata lines
        import re
        lines = content.splitlines()
        stacked = sum(
            1 for i in range(1, len(lines))
            if re.match(r"\s*<!-- ref:", lines[i]) and re.match(r"\s*<!-- ref:", lines[i - 1])
        )
        assert stacked == 0, f"writer orphaned a meta — {stacked} stacked pairs:\n{content}"
        # The pre-existing entry's real-date meta must survive intact
        assert "last:2026-06-20" in content
        # R-1 Gate-2 #2/#3: NEW lessons must land at section TOP (newest-first),
        # matching distillation_hook._write_section's prepend convention and the
        # newest-at-top assumption _enforce_section_caps relies on (it trims the
        # bottom = oldest). The new lesson must appear ABOVE the pre-existing one.
        new_pos = content.find("Always verify before asserting")
        old_pos = content.find("Existing entry")
        assert new_pos != -1, "new lesson not written"
        assert new_pos < old_pos, (
            "new lesson must be prepended (newest-at-top) — found below the "
            f"existing entry:\n{content}"
        )

    def test_memory_lifecycle_auto_heals_stacked_metadata(self, hook, tmp_path):
        # R-1 Gate-2 #1: collapse_stacked_metadata must be WIRED into the
        # per-session lifecycle, not just a manually-invoked helper. A MEMORY.md
        # that already carries an orphan stack must come out healed after one
        # _run_memory_lifecycle pass.
        ws = tmp_path / "SwarmWS"
        ctx = ws / ".context"
        ctx.mkdir(parents=True)
        (ctx / "MEMORY.md").write_text(
            "## Guidelines\n"
            "_Operational lessons._\n\n"
            "- [guideline] **Stacked entry** — a lesson (2026-06-01)\n"
            "  <!-- ref:3 | last:2026-06-20 | decay:active -->\n"
            "  <!-- ref:0 | last:none | decay:active -->\n"
        )
        hook._run_memory_lifecycle(ws)
        content = (ctx / "MEMORY.md").read_text()
        import re
        lines = content.splitlines()
        stacked = sum(
            1 for i in range(1, len(lines))
            if re.match(r"\s*<!-- ref:", lines[i]) and re.match(r"\s*<!-- ref:", lines[i - 1])
        )
        assert stacked == 0, f"lifecycle did not auto-heal the orphan:\n{content}"
        assert "last:2026-06-20" in content, "must keep the real-date meta"
        assert "last:none" not in content, "must drop the orphan default"


class TestEvolutionLifecycle:
    """run_2816ab1c: EVOLUTION.md wired into the lifecycle as DEDUP-ONLY. It is
    FULLY evergreen for age-decay (all 7 sections listed) — the O-entries are
    distilled wisdom, not age-churn (Principle 1: value, not age). So age-decay
    strips NOTHING; only exact-dup dedup acts. The Corrections narrative is
    fold_corrections' domain and must be untouched."""

    def test_optimizations_never_age_decayed(self, hook, tmp_path):
        from datetime import timedelta
        ws = tmp_path / "SwarmWS"
        ctx = ws / ".context"
        ctx.mkdir(parents=True)
        old = (date.today() - timedelta(days=300)).isoformat()  # ancient + ref:0
        corrections_marker = "### CLASS A: a permanent judgment narrative"
        (ctx / "EVOLUTION.md").write_text(
            "## Optimizations Learned\n"
            f"- [guideline] **O999 old but load-bearing** — a distilled lesson ({old})\n"
            "  <!-- ref:0 | last:none | decay:active -->\n\n"
            "## Corrections Captured\n"
            f"{corrections_marker}\n"
            "- **correction** — this is prose narrative, not a - **Title** bullet\n"
        )
        hook._run_evolution_lifecycle(ws)
        content = (ctx / "EVOLUTION.md").read_text()
        # O-entry SURVIVES despite ancient+ref:0 — fully evergreen, age-decay is inert
        assert "O999 old but load-bearing" in content, "EVOLUTION age-decay wrongly stripped an O-entry (must be dedup-only)"
        # Corrections narrative untouched
        assert corrections_marker in content
        assert "this is prose narrative" in content

    def test_exact_dup_still_deduped(self, hook, tmp_path):
        ws = tmp_path / "SwarmWS"
        ctx = ws / ".context"
        ctx.mkdir(parents=True)
        # Two exact-dup plain guidelines in Optimizations Learned → dedup DOES act
        # (dedup is the one live sweep on EVOLUTION), keeping the higher-ref survivor.
        (ctx / "EVOLUTION.md").write_text(
            "## Optimizations Learned\n"
            "- [guideline] **Dup opt** — identical distilled text (2026-01-01, run_a)\n"
            "  <!-- ref:4 | last:2026-01-01 | decay:active -->\n"
            "- [guideline] **Dup opt** — identical distilled text (2026-06-01, run_b)\n"
            "  <!-- ref:0 | last:none | decay:active -->\n"
        )
        hook._run_evolution_lifecycle(ws)
        content = (ctx / "EVOLUTION.md").read_text()
        # exactly ONE survives (the exact-dup was archived+stripped)
        assert content.count("identical distilled text") == 1
        archive = ctx / "EVOLUTION-archive.md"
        assert archive.exists() and "identical distilled text" in archive.read_text()


class TestNoProseBump:
    """R2-prime: the toxic prose-substring ref bump is removed. An entry whose
    title coincidentally appears in DailyActivity prose must NOT get its
    ref_count bumped — that fake signal protected generic-titled entries
    (DISCUSSION ref:1009) while real entries starved at ref:0. The honest
    id-based signal (memory_decay, via distillation) is the only ref producer."""

    def test_prose_mention_does_not_bump_ref(self, hook, tmp_path):
        ws = tmp_path / "SwarmWS"
        ctx = ws / ".context"
        ctx.mkdir(parents=True)
        # Entry titled "Correction" — a word that appears all over prose.
        # Use a RECENT date: a hardcoded old date ages past the 45-day dormant
        # threshold and gets reclaim-stripped before the assertion — a calendar
        # time-bomb (this fixture's `2026-06-01` broke CI on 2026-07-16, exactly
        # 45 days later). ref:0 + last:none is preserved (that IS the test).
        recent = date.today().isoformat()
        (ctx / "MEMORY.md").write_text(
            "## Pitfalls\n"
            "_Operational lessons._\n\n"
            f"- [pitfall] **Correction** — a specific lesson ({recent})\n"
            "  <!-- ref:0 | last:none | decay:active -->\n"
        )
        # DailyActivity prose that mentions the word "Correction" many times.
        da = ws / "Knowledge" / "DailyActivity"
        da.mkdir(parents=True)
        (da / f"{date.today().isoformat()}-x.md").write_text(
            "# Activity\n\nCorrection here. Another Correction. Correction again.\n" * 5
        )
        hook._run_memory_lifecycle(ws)
        content = (ctx / "MEMORY.md").read_text()
        import re
        m = re.search(r"<!-- ref:(\d+)", content)
        assert m is not None, f"metadata missing:\n{content}"
        assert int(m.group(1)) == 0, (
            f"prose-bump still active — 'Correction' got bumped to ref:{m.group(1)} "
            f"from DailyActivity prose coincidence:\n{content}"
        )


class TestUsageRefBridge:
    """R2-real (run_77504e11): _run_memory_lifecycle bridges .memory-usage.json
    to body ref_count (reclaim-protection + injection-priority), log-damped,
    threshold-gated, survives the inject round-trip."""

    def test_usage_bridge_sets_body_ref_and_survives_roundtrip(self, hook, tmp_path):
        import json as _json
        from core.ddd_entry_lifecycle import parse_entries
        ws = tmp_path / "SwarmWS"
        ctx = ws / ".context"
        ctx.mkdir(parents=True)
        # RECENT dates: an old title date would age past the 45-day dormant
        # threshold and let reclaim STRIP the ref:0 "Rarely used note" entry —
        # which would then satisfy `entries.get(..., 0) == 0` by DELETION rather
        # than by the behavior under test (below-threshold entry not bridged).
        # Recent dates keep both entries present so the assertions are non-vacuous.
        recent = date.today().isoformat()
        (ctx / "MEMORY.md").write_text(
            "<!-- MEMORY_INDEX_START -->\n"
            "- [PIT07] Gate caught a real bug | gate, adversarial\n"
            "- [GUI99] Rarely used note | x\n"
            "<!-- MEMORY_INDEX_END -->\n"
            "## Pitfalls\n"
            "_lessons_\n\n"
            f"- [pitfall] **Gate caught a real bug** — body ({recent})\n"
            "  <!-- ref:0 | last:none | decay:active -->\n"
            f"- [guideline] **Rarely used note** — body ({recent})\n"
            "  <!-- ref:0 | last:none | decay:active -->\n"
        )
        (ctx / ".memory-usage.json").write_text(_json.dumps({"PIT07": 40, "GUI99": 2}))

        hook._run_memory_lifecycle(ws)
        content = (ctx / "MEMORY.md").read_text()

        entries = {e.title: e.ref_count for e in parse_entries(content)}
        assert entries.get("Gate caught a real bug", 0) > 0, f"used entry not bridged:\n{content}"
        assert entries.get("Rarely used note", 0) == 0, "below-threshold entry wrongly protected"


class TestUsageDecayGate:
    """run_81f6d20c: _track_memory_usage applies write-time decay ONCE per calendar
    day, gated by a sidecar .memory-usage-meta.json last_decay date. Legacy flat-int
    files (no sidecar) get NO decay on first upgrade → currently-used entries keep
    full count (no mass unprotect). The cumulative ratchet is broken without
    re-scanning transcripts (scanned-marker preserved)."""

    def _empty_transcripts(self, monkeypatch, tmp_path):
        """Point the transcript scan at an empty dir so counts only come from the file."""
        empty = tmp_path / "no_transcripts"
        empty.mkdir()
        monkeypatch.setattr(
            "hooks.context_health_hook.Path.home", staticmethod(lambda: tmp_path)
        )
        # Path.home()/.claude/projects must be absent/empty → no increments
        return empty

    def test_legacy_file_no_sidecar_no_decay_first_run(self, hook, tmp_path, monkeypatch):
        import json as _json
        ws = tmp_path / "SwarmWS"
        ctx = ws / ".context"
        ctx.mkdir(parents=True)
        (ctx / ".memory-usage.json").write_text(_json.dumps({"PIT07": 40, "COE02": 100}))
        self._empty_transcripts(monkeypatch, tmp_path)

        hook._track_memory_usage(ws)

        usage = _json.loads((ctx / ".memory-usage.json").read_text())
        # No sidecar existed → first run treats last_decay=today → NO decay applied.
        assert usage["PIT07"] == 40
        assert usage["COE02"] == 100
        # Sidecar is now created with today's date.
        meta = _json.loads((ctx / ".memory-usage-meta.json").read_text())
        assert "last_decay" in meta

    def test_stale_sidecar_triggers_decay_once(self, hook, tmp_path, monkeypatch):
        import json as _json
        from datetime import date, timedelta
        ws = tmp_path / "SwarmWS"
        ctx = ws / ".context"
        ctx.mkdir(parents=True)
        (ctx / ".memory-usage.json").write_text(_json.dumps({"PIT07": 40.0}))
        # Sidecar says last decayed exactly one half-life ago → expect halving.
        from core.memory_decay import USAGE_HALFLIFE_DAYS
        old = (date.today() - timedelta(days=int(USAGE_HALFLIFE_DAYS))).isoformat()
        (ctx / ".memory-usage-meta.json").write_text(_json.dumps({"last_decay": old}))
        self._empty_transcripts(monkeypatch, tmp_path)

        hook._track_memory_usage(ws)

        usage = _json.loads((ctx / ".memory-usage.json").read_text())
        assert usage["PIT07"] == pytest.approx(20.0, rel=0.05)
        # last_decay advanced to today → a second same-day run won't re-decay.
        meta = _json.loads((ctx / ".memory-usage-meta.json").read_text())
        assert meta["last_decay"] == date.today().isoformat()

    def test_same_day_rerun_idempotent(self, hook, tmp_path, monkeypatch):
        import json as _json
        from datetime import date
        ws = tmp_path / "SwarmWS"
        ctx = ws / ".context"
        ctx.mkdir(parents=True)
        (ctx / ".memory-usage.json").write_text(_json.dumps({"PIT07": 40.0}))
        (ctx / ".memory-usage-meta.json").write_text(
            _json.dumps({"last_decay": date.today().isoformat()})
        )
        self._empty_transcripts(monkeypatch, tmp_path)

        hook._track_memory_usage(ws)

        usage = _json.loads((ctx / ".memory-usage.json").read_text())
        # last_decay == today → no decay this run.
        assert usage["PIT07"] == 40.0

    def test_corrupt_sidecar_fails_safe_no_decay(self, hook, tmp_path, monkeypatch):
        import json as _json
        ws = tmp_path / "SwarmWS"
        ctx = ws / ".context"
        ctx.mkdir(parents=True)
        (ctx / ".memory-usage.json").write_text(_json.dumps({"PIT07": 40.0}))
        (ctx / ".memory-usage-meta.json").write_text("{not valid json")
        self._empty_transcripts(monkeypatch, tmp_path)

        hook._track_memory_usage(ws)  # must not raise

        usage = _json.loads((ctx / ".memory-usage.json").read_text())
        # Corrupt sidecar → fail safe → no decay, counts intact.
        assert usage["PIT07"] == 40.0

    def test_corrupt_sidecar_self_heals(self, hook, tmp_path, monkeypatch):
        """Gate-2 MEDIUM (run_241014d4): a corrupt sidecar must be REWRITTEN to a
        valid date this run, so decay resumes the next day. Without the heal, the
        corrupt file is re-read forever and decay is permanently disabled."""
        import json as _json
        from datetime import date
        ws = tmp_path / "SwarmWS"
        ctx = ws / ".context"
        ctx.mkdir(parents=True)
        (ctx / ".memory-usage.json").write_text(_json.dumps({"PIT07": 40.0}))
        (ctx / ".memory-usage-meta.json").write_text("{not valid json")
        self._empty_transcripts(monkeypatch, tmp_path)

        hook._track_memory_usage(ws)

        # Corrupt sidecar healed to a valid today's-date JSON (self-repair).
        meta = _json.loads((ctx / ".memory-usage-meta.json").read_text())
        assert meta["last_decay"] == date.today().isoformat()

    def test_future_sidecar_clamped_and_healed(self, hook, tmp_path, monkeypatch):
        """Gate-2 MEDIUM (run_241014d4): a last_decay in the FUTURE (clock skew /
        hand-edit) gives negative days_elapsed → decay skipped. The future date
        must be clamped to today and rewritten, else decay stays disabled until the
        wall clock passes the bogus future date."""
        import json as _json
        from datetime import date, timedelta
        ws = tmp_path / "SwarmWS"
        ctx = ws / ".context"
        ctx.mkdir(parents=True)
        (ctx / ".memory-usage.json").write_text(_json.dumps({"PIT07": 40.0}))
        future = (date.today() + timedelta(days=5)).isoformat()
        (ctx / ".memory-usage-meta.json").write_text(_json.dumps({"last_decay": future}))
        self._empty_transcripts(monkeypatch, tmp_path)

        hook._track_memory_usage(ws)

        # No decay this run (future → clamped to today → days_elapsed 0), counts intact...
        usage = _json.loads((ctx / ".memory-usage.json").read_text())
        assert usage["PIT07"] == 40.0
        # ...but the bogus future date is healed to today so decay resumes normally.
        meta = _json.loads((ctx / ".memory-usage-meta.json").read_text())
        assert meta["last_decay"] == date.today().isoformat()

# NOTE: test_memory_embedding_recovery_converges (M1 backfill convergence,
# run_e9b15722) was RETIRED in the pure-filesystem READ-line finalize
# (run_2f621986, design 2026-06-28 §3): _sync_memory_embeddings and the
# memory_vec writer it drove were physically removed — recall is keyword/FTS5
# only. There is no embedding-orphan backlog to converge anymore.


class TestCognitiveAdmissionGate:
    """Deterministic-floor + judge admission gate on _extract_lessons_to_memory.

    THE MUTATION-PROOF CONTRACT (the fix for the false-green class): every reject-path
    test STUBS THE JUDGE TO PASS and asserts the entry is STILL held. That proves the
    DETERMINISTIC FLOOR does the holding — not an accidental judge fail-close (no
    Bedrock in CI). Before this rewrite these tests were green only because the judge
    crashed; stubbing it to pass turned them red, exposing that the floor 2c8fc37f
    dropped was gone. The autouse _judge_pass fixture enforces the stub structurally so
    a future reject-path test cannot silently regress to "green because the judge died".

    Verdicts: auto = written; pending = held recoverably (judge UNAVAILABLE — infra
    error / budget); discard = a real rejection (a deterministic floor, or the judge
    online saying suspect/noise). keep-types are NOT held by a pre-judge short-circuit
    (that can never be re-judged → infinite requeue, an adversarial HIGH we fixed); they
    flow through the judge normally, and XG 乙 ("never DROP a keep-type when the judge is
    down") is delivered by the CONVERGENT judge-down→pending path.
    """

    @pytest.fixture(autouse=True)
    def _judge_pass(self):
        """STRUCTURAL guard against the false-green class: force the LLM judge to PASS
        for every test in this class. A reject-path test that still holds its entry is
        then proving the DETERMINISTIC FLOOR, never a judge fail-close. Remove this and
        the reject tests would go green for the wrong reason (judge crash) — exactly the
        bug this class now guards against."""
        import core.ingestion_gate as _ig
        with patch.object(_ig, "self_adversarial_judge", lambda *a, **k: ("pass", "judged")):
            yield

    def _mk_memory(self, tmp_path, body: str = ""):
        ws = tmp_path / "SwarmWS"
        ctx = ws / ".context"
        ctx.mkdir(parents=True)
        base = (
            "## Principles\n_meta._\n\n"
            "## Guidelines\n_Operational lessons._\n\n"
            "## Pitfalls\n_Traps._\n\n"
        )
        (ctx / "MEMORY.md").write_text(base + body)
        return ws, ctx / "MEMORY.md"

    def test_ac1_keep_type_deferred_when_judge_unavailable(self, hook, tmp_path):
        """AC1 (safety core, XG 乙): when the judge is UNAVAILABLE a keep-type principle is
        NOT auto-written — it defers (pending), so a permanent/decay-immune entry never
        rides into MEMORY on a judge that could not actually vet it. This overrides the
        autouse judge=pass with an infra-error stub. (The judge-AVAILABLE keep-type path —
        pass → written — is covered in test_distillation_admission; splitting the two is
        what makes the design CONVERGENT instead of an infinite pre-judge hold.)"""
        import core.ingestion_gate as _ig
        ws, mem = self._mk_memory(tmp_path)
        principle = (
            "The first principle is that confidence is a counter-signal: the more "
            "certain I feel, the more likely I skipped verification."
        )
        with patch.object(_ig, "self_adversarial_judge",
                          lambda *a, **k: ("suspect", "judge_error:EndpointConnectionError")):
            hook._extract_lessons_to_memory(ws, [principle], "run_x", "Proj")
        content = mem.read_text()
        assert "confidence is a counter-signal" not in content, (
            "a keep-type principle was written to MEMORY while the judge was UNAVAILABLE "
            f"— it must defer (pending), never ride an un-vetted permanent write:\n{content}"
        )

    def test_ac2_operational_admitted(self, hook, tmp_path):
        """AC2: a clean operational guideline (judge passes) → ADMITTED (written)."""
        ws, mem = self._mk_memory(tmp_path)
        g = ("When editing MEMORY.md always take the .lock before the "
             "read-modify-write to avoid a lost-update race across writers.")
        hook._extract_lessons_to_memory(ws, [g], "run_ok", "Proj")
        content = mem.read_text()
        assert "lost-update race across writers" in content, (
            f"operational lesson was NOT written:\n{content}"
        )

    def test_ac4_thin_held_even_when_judge_passes(self, hook, tmp_path):
        """AC4a: a too-thin fragment is held by the deterministic thin floor EVEN WHEN
        THE JUDGE PASSES (not an accidental fail-close)."""
        ws, mem = self._mk_memory(tmp_path)
        hook._extract_lessons_to_memory(ws, ["too short"], "r", "P")
        assert "too short" not in mem.read_text()

    def test_ac4_low_confidence_denied_even_when_judge_passes(self, hook, tmp_path):
        """AC4b: content_floor holds a low-confidence (<=0.3) entry EVEN WHEN THE JUDGE
        PASSES. classify_content NEVER rejects (always routes), so the numeric floor —
        not a truthiness check — must do the holding. Patched to the 0.1-confidence dict
        real noise gets; asserts NOT written. Direct floor-level assertion too."""
        from core.ingestion_gate import content_floor
        lesson = ("Always take the .lock before the read-modify-write on MEMORY.md "
                  "to avoid a lost-update race across concurrent writers here.")
        with patch("core.persist_routing.classify_content",
                   return_value={"confidence": 0.1, "is_governance": False}):
            deny, reason = content_floor(lesson)
            assert deny is True, "low-confidence entry must be DENIED by content_floor"
            assert "confidence" in reason.lower(), f"reason must cite confidence: {reason!r}"
            ws, mem = self._mk_memory(tmp_path)
            hook._extract_lessons_to_memory(ws, [lesson], "r", "P")
            assert "lost-update race across concurrent writers" not in mem.read_text()

    def test_ac4_high_confidence_passes_floor(self, hook):
        """AC4b companion: a normal-confidence dict does NOT trip content_floor (proves
        the floor isn't over-broad — an operational lesson passes through)."""
        from core.ingestion_gate import content_floor
        lesson = ("Always take the .lock before the read-modify-write on MEMORY.md "
                  "to avoid a lost-update race across concurrent writers here.")
        with patch("core.persist_routing.classify_content",
                   return_value={"confidence": 0.6, "is_governance": False}):
            deny, reason = content_floor(lesson)
        assert deny is False, f"normal-confidence lesson must pass the floor, got: {reason}"

    def test_ac4_governance_held_even_when_judge_passes(self, hook, tmp_path):
        """AC4c: a governance-phrased lesson is held by content_floor EVEN WHEN THE
        JUDGE PASSES (belongs to s_self-evolution, not MEMORY)."""
        ws, mem = self._mk_memory(tmp_path)
        gov = ("From now on always run the full adversarial review before every "
               "merge to main — this is a new standing rule for the team.")
        from core.persist_routing import classify_content
        assert classify_content(gov).get("is_governance"), "fixture not governance"
        hook._extract_lessons_to_memory(ws, [gov], "r", "P")
        assert "new standing rule" not in mem.read_text()

    def test_ac4_duplicate_held(self, hook, tmp_path):
        """AC4d: an exact-title duplicate of an existing section entry → HELD (dedup)."""
        body = (
            "- [guideline] **Always lock before write** — prior lesson (2026-06-01)\n"
            "  <!-- ref:2 | last:2026-06-10 | decay:active -->\n"
        )
        ws = tmp_path / "SwarmWS"
        ctx = ws / ".context"
        ctx.mkdir(parents=True)
        (ctx / "MEMORY.md").write_text(
            "## Principles\n_meta._\n\n"
            "## Guidelines\n_Operational lessons._\n\n" + body +
            "\n## Pitfalls\n_Traps._\n\n"
        )
        mem = ctx / "MEMORY.md"
        dup = "Always lock before write — take the .lock before read-modify-write always"
        hook._extract_lessons_to_memory(ws, [dup], "r", "P")
        content = mem.read_text()
        assert content.count("Always lock before write") == 1, (
            f"duplicate title was auto-written (expected 1 occurrence):\n{content}"
        )

    def test_ac5_no_regression_operational_written(self, hook, tmp_path):
        """AC5: the writer still writes a legit operational lesson (back-compat, the
        judge-pass happy path)."""
        ws, mem = self._mk_memory(tmp_path)
        g = ("Verify runtime state by observation before asserting a cause — "
             "read the live gauge, do not infer from a stale log string.")
        hook._extract_lessons_to_memory(ws, [g], "run_z", "Proj")
        assert "read the live gauge" in mem.read_text()


class TestKnowledgeProjectsSectionClassification:
    """run_99b70b3c: the LIVE writer of 'Active Projects & DDD' must be
    classification-aware ([none|external|internal]) + structure-aware (six-section
    markers), NOT the stale 4-doc-only format that clobbered the index post-deploy.
    """

    def _make_ws(self, tmp_path):
        ws = tmp_path / "SwarmWS"
        (ws / ".context").mkdir(parents=True)
        (ws / ".context" / "KNOWLEDGE.md").write_text(
            "# KNOWLEDGE\n\n### Active Projects & DDD\n\n- old\n\n## The 11 Context Files\n\nx\n"
        )
        projects = ws / "Projects"
        # internal project: bindings.yaml kind:internal + six-section extras
        intp = projects / "IntProj"
        intp.mkdir(parents=True)
        for doc in ("PRODUCT.md", "TECH.md", "IMPROVEMENT.md", "PROJECT.md"):
            (intp / doc).write_text(f"# {doc}\n")
        (intp / "skills" / "s_internal-brazil").mkdir(parents=True)
        (intp / "gates").mkdir()
        (intp / "Knowledge").mkdir()
        (intp / "bindings.yaml").write_text(
            'version: 1\nbindings:\n  - repo: P\n    kind: internal\n'
            '    clone: "brazil ws create --name P"\n'
            '    delivery_contract:\n      remote_kind: code-amazon-cr\n'
            '      branch: mainline\n      review_path: cr\n      auto_send: "false"\n'
        )
        # none project: pure DDD
        nonep = projects / "NoneProj"
        nonep.mkdir(parents=True)
        for doc in ("PRODUCT.md", "TECH.md", "IMPROVEMENT.md", "PROJECT.md"):
            (nonep / doc).write_text(f"# {doc}\n")
        return ws

    def _run(self, hook, ws):
        hook._refresh_knowledge_projects_section(ws)
        return (ws / ".context" / "KNOWLEDGE.md").read_text()

    def test_classification_and_structure(self, hook, tmp_path):
        ws = self._make_ws(tmp_path)
        content = self._run(hook, ws)
        int_line = next(ln for ln in content.splitlines() if "**IntProj**" in ln)
        assert "`[internal]`" in int_line
        assert "1 skills" in int_line and "gates" in int_line
        assert "Knowledge/" in int_line and "bindings" in int_line
        # freshness suffix preserved
        assert "(updated" in int_line

        none_line = next(ln for ln in content.splitlines() if "**NoneProj**" in ln)
        assert "`[none]`" in none_line
        assert "bindings" not in none_line

    def test_all_four_docs_still_listed(self, hook, tmp_path):
        ws = self._make_ws(tmp_path)
        content = self._run(hook, ws)
        int_line = next(ln for ln in content.splitlines() if "**IntProj**" in ln)
        for doc in ("PRODUCT.md", "TECH.md", "IMPROVEMENT.md", "PROJECT.md"):
            assert doc in int_line


class TestMaturityWritebackResolvesLayout:
    """run_ff06972d: _set_verified_from_pipeline_runs must resolve canonical docs
    via ddd_paths (migrated → 2-understanding/), not a raw project_root path.

    The pre-fix code did `doc_path = project_path / doc_name`, so for a MIGRATED
    DDD (docs under 2-understanding/) the root path did not exist → `continue` →
    maturity verification was SILENTLY never written. This test creates a migrated
    doc + a completed run with a deliver stage and asserts the migrated doc gets
    verified_by_production=True (the raw-path version leaves it False → RED)."""

    def test_writeback_reaches_migrated_2understanding_doc(self, hook, tmp_path):
        proj = tmp_path / "Projects" / "MigratedProj"
        und = proj / "2-understanding"
        und.mkdir(parents=True)
        # A migrated TECH.md with an UNVERIFIED maturity state.
        (und / "TECH.md").write_text(
            "# TECH\n\n## Architecture\n"
            "<!-- maturity: sparse | sources: 1 | verified: false | used: false | days: 0 | promoted: none -->\n"
            "- [decision] seed\n"
        )
        # NO root TECH.md — this is the migrated shape that the raw path missed.

        # A completed pipeline run with a completed deliver stage.
        run_dir = proj / ".artifacts" / "runs" / "run_x"
        run_dir.mkdir(parents=True)
        (run_dir / "run.json").write_text(json.dumps({
            "status": "completed",
            "stages": [{"stage": "deliver", "status": "completed"}],
        }))

        hook._set_verified_from_pipeline_runs(proj)

        # The migrated doc must now be verified (proves the resolver was used).
        content = (und / "TECH.md").read_text()
        assert "verified: true" in content, \
            f"migrated 2-understanding/ doc should be marked verified, got:\n{content}"
        # And the run marked processed.
        run_data = json.loads((run_dir / "run.json").read_text())
        assert run_data.get("maturity_updated") is True
        # No stale lock stranded at project root (lock co-locates with resolved doc).
        assert not (proj / ".TECH.md.lock").exists(), \
            "lock must co-locate with the resolved doc, not strand at project root"


class TestRecallDegradationReader:
    """run_e9861490: the recall/DDD-inject degradation counters were WRITE-ONLY.
    This wires + verifies the READ side (getters + _deep_check finding)."""

    @pytest.fixture(autouse=True)
    def _reset_counters(self):
        import core.session_router as sr
        sr._recall_degraded_count.clear()
        sr._ddd_inject_count.clear()
        yield
        sr._recall_degraded_count.clear()
        sr._ddd_inject_count.clear()

    # AC1: getters read live counts
    def test_getters_snapshot_live_counts(self):
        import core.session_router as sr
        sr._record_recall_degraded("vec_db_unavailable")
        sr._record_ddd_inject("injected")
        assert sr.get_recall_degraded_snapshot()["vec_db_unavailable"] == 1
        assert sr.get_ddd_inject_snapshot()["injected"] == 1

    # AC4: getter returns a COPY (mutation doesn't leak into the live counter)
    def test_getter_returns_copy_not_live_ref(self):
        import core.session_router as sr
        sr._record_recall_degraded("leg_failure")
        snap = sr.get_recall_degraded_snapshot()
        snap["leg_failure"] = 999
        assert sr._recall_degraded_count["leg_failure"] == 1  # live unchanged

    # AC2: true-failure aggregation excludes informational no-match keys
    def test_true_failure_excludes_informational(self):
        import core.session_router as sr
        sr._record_recall_degraded("vec_db_unavailable")     # failure
        sr._record_recall_degraded("disaster_timeout")       # failure
        sr._record_recall_degraded("exception:ValueError")   # failure (prefix)
        sr._record_recall_degraded("empty_with_keywords")    # INFORMATIONAL
        sr._record_recall_degraded("unified_empty_fallback_legacy")  # INFORMATIONAL
        assert sr.recall_true_failure_total() == 3  # only the 3 real failures
        # ddd side
        sr._record_ddd_inject("declined:disaster_timeout")   # failure
        sr._record_ddd_inject("declined:exception:OSError")  # failure (prefix)
        sr._record_ddd_inject("declined:no_ddd_hits")        # by-design
        sr._record_ddd_inject("declined:multi_project")      # by-design (signal)
        sr._record_ddd_inject("injected")                    # success
        assert sr.ddd_inject_true_failure_total() == 2

    # AC3: a true failure surfaces a finding; only-informational surfaces none
    def test_deep_check_finding_on_true_failure(self, hook):
        import core.session_router as sr
        sr._record_recall_degraded("vec_db_unavailable")
        findings = hook._check_recall_degradation()
        assert any("RECALL DEGRADED" in f and "vec_db_unavailable" in f for f in findings)

    def test_no_finding_when_only_informational(self, hook):
        import core.session_router as sr
        sr._record_recall_degraded("empty_with_keywords")   # not a failure
        sr._record_ddd_inject("declined:no_ddd_hits")        # not a failure
        findings = hook._check_recall_degradation()
        assert not any("DEGRADED" in f for f in findings), \
            "informational-only counters must NOT raise a failure finding"

    def test_no_finding_when_clean(self, hook):
        findings = hook._check_recall_degradation()
        assert findings == []

    # synonym-miss is a SEPARATE signal-quality note, not a failure
    def test_synonym_miss_reported_separately_not_as_failure(self, hook):
        import core.session_router as sr
        for _ in range(25):
            sr._record_recall_degraded("empty_with_keywords")
        findings = hook._check_recall_degradation()
        assert any("synonym-miss" in f for f in findings)
        assert not any("RECALL DEGRADED" in f for f in findings), \
            "synonym-miss must not be reported as a true failure"

    # AC5: the reader has a real caller (not dead code)
    def test_reader_is_called_by_deep_check(self):
        import inspect
        from hooks.context_health_hook import ContextHealthHook
        src = inspect.getsource(ContextHealthHook._deep_check)
        assert "_check_recall_degradation()" in src, \
            "_check_recall_degradation must be called from _deep_check (else it is dead)"

    # Gate-2 LOW fix: an UNCLASSIFIED reason (future writer) must SURFACE, not vanish
    def test_unclassified_reason_surfaces(self, hook):
        import core.session_router as sr
        sr._record_recall_degraded("db_corrupt")           # not failure, not known-informational
        sr._record_ddd_inject("declined:db_broke")          # ditto on the DDD side
        findings = hook._check_recall_degradation()
        assert any("UNCLASSIFIED" in f and "db_corrupt" in f for f in findings), \
            "a new unclassified recall reason must surface (dead-signal recursion guard)"
        assert any("ddd:declined:db_broke" in f for f in findings)

    def test_all_known_reasons_no_unclassified_line(self, hook):
        import core.session_router as sr
        # every string a current writer actually emits — none should be 'unclassified'
        for r in ("vec_db_unavailable", "leg_failure", "disaster_timeout",
                  "exception:ValueError", "inject_exception:OSError",
                  "unified_exception:KeyError", "empty_with_keywords",
                  "unified_empty_fallback_legacy"):
            sr._record_recall_degraded(r)
        for o in ("injected", "declined:disaster_timeout", "declined:exception:X",
                  "declined:no_ddd_hits", "declined:no_projects",
                  "declined:ambiguous", "declined:no_signal"):
            sr._record_ddd_inject(o)
        findings = hook._check_recall_degradation()
        assert not any("UNCLASSIFIED" in f for f in findings), \
            "all current writer reasons must be classified (no unclassified line)"

    # anti-drift: every literal a writer emits is classified (grep the source)
    def test_all_emitted_reasons_are_classified(self):
        import re, inspect
        import core.session_router as sr
        src = inspect.getsource(sr)
        # static literal args to _record_recall_degraded("...") / _record_ddd_inject("...")
        recall_lits = set(re.findall(r'_record_recall_degraded\(\s*"([^"{]+)"', src))
        ddd_lits = set(re.findall(r'_record_ddd_inject\(\s*"([^"{]+)"', src))
        for r in recall_lits:
            assert (sr._is_recall_true_failure(r) or r in sr._RECALL_KNOWN_INFORMATIONAL), \
                f"recall reason {r!r} is emitted but unclassified — classify it"
        for o in ddd_lits:
            assert (sr._is_ddd_true_failure(o) or o in sr._DDD_KNOWN_INFORMATIONAL), \
                f"ddd outcome {o!r} is emitted but unclassified — classify it"


class TestExpireStaleProposalsArchives:
    """run_419ff7d4 (Debt 1): _expire_stale_proposals must RECLAIM terminal proposals
    (move to proposals/archive/), not just flip status → the file graveyard (514 live)
    was caused by the old expirer flipping pending→expired but never removing files.

    Extended behavior: terminal (status NOT in {pending,escalated}) + older than the
    retention window → MOVE to archive/. Live (pending/escalated) → NEVER moved,
    regardless of age. Malformed/missing created date on a terminal → SKIP (safe)."""

    def _seed(self, proposals_dir, name, *, status, age_days, reset_mtime_days=None):
        """Seed a proposal whose TRUE age is encoded in the filename stamp
        (proposal_<name>_YYYYMMDD-HHMMSS.json — the real created time). Optionally
        set a DIFFERENT (fresher) mtime to simulate the git-checkout/rsync reset
        that defeated an mtime-based gate (Gate-2 HIGH, run_419ff7d4)."""
        import json, os, time
        from datetime import datetime, timedelta, timezone
        proposals_dir.mkdir(parents=True, exist_ok=True)
        created = datetime.now(timezone.utc) - timedelta(days=age_days)
        stamp = created.strftime("%Y%m%d-%H%M%S")
        f = proposals_dir / f"proposal_{name}_{stamp}.json"
        f.write_text(json.dumps({"id": f"proposal_{name}", "source_stage": "reflect_feed",
                                 "status": status, "change_type": "append",
                                 "created_at": created.isoformat()}), encoding="utf-8")
        # mtime = the RESET time if given (fresher than real age), else the real age.
        mt = time.time() - (reset_mtime_days if reset_mtime_days is not None else age_days) * 86400
        os.utime(f, (mt, mt))
        return f

    def test_terminal_old_archived_live_kept(self, hook, tmp_path):
        proposals = tmp_path / "Projects" / "SwarmAI" / ".artifacts" / "proposals"
        # terminal + old → should move
        self._seed(proposals, "dismissed_old", status="dismissed", age_days=40)
        self._seed(proposals, "rejected_old", status="rejected", age_days=40)
        # live → must stay even when old
        self._seed(proposals, "escalated_old", status="escalated", age_days=99)
        self._seed(proposals, "pending_old", status="pending", age_days=99)
        # terminal but RECENT (within window) → stay
        self._seed(proposals, "dismissed_recent", status="dismissed", age_days=1)

        hook._expire_stale_proposals(tmp_path)

        archive = proposals / "archive"
        moved = {p.name for p in archive.glob("proposal_*.json")} if archive.is_dir() else set()
        live = {p.name for p in proposals.glob("proposal_*.json")}
        assert any("dismissed_old" in n for n in moved), "terminal+old must be archived"
        assert any("rejected_old" in n for n in moved), "terminal+old must be archived"
        assert any("escalated_old" in n for n in live), "escalated is LIVE — never move"
        assert any("pending_old" in n for n in live), "pending is LIVE — never move"
        assert any("dismissed_recent" in n for n in live), "terminal but within window — keep"

    def test_reset_mtime_does_not_hide_true_age(self, hook, tmp_path):
        """Gate-2 HIGH (run_419ff7d4): a terminal proposal created 60d ago but whose
        mtime was bulk-reset to 5d ago (git checkout / rsync) MUST still be reclaimed —
        age comes from the filename/created stamp, not the reset mtime. An mtime gate
        archived 58 of 259 truly-old; this proves the filename gate reclaims it."""
        proposals = tmp_path / "Projects" / "SwarmAI" / ".artifacts" / "proposals"
        # created 60d ago (truly old) but mtime reset to 5d ago (looks fresh by mtime)
        self._seed(proposals, "reset_terminal", status="dismissed", age_days=60, reset_mtime_days=5)

        hook._expire_stale_proposals(tmp_path)

        archive = proposals / "archive"
        moved = {p.name for p in archive.glob("proposal_*.json")} if archive.is_dir() else set()
        assert any("reset_terminal" in n for n in moved), (
            "a 60d-old terminal proposal with a reset (5d) mtime MUST be archived — "
            "age must derive from the filename/created stamp, NOT mtime"
        )
