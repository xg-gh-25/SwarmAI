"""Tests for decoupling the weekly evolution cycle from the per-session hook.

Bug (run_6ac3fc0b): the evolution_maintenance lifecycle hook ran a ~5-minute
evolution cycle (mine 3629 transcripts + Bedrock) synchronously on every session
close. The hook budget is 180s (main.py:943); the cycle grew to ~293s, so
asyncio.wait_for cancelled the hook coroutine before it reached the post-await
state-write — .evolution_last_run never advanced, days_since>=7 stayed true, and
EVERY session close re-triggered the cycle (59x in one day). The run_in_executor
thread cannot be cancelled, so each trigger also spawned a 293s zombie thread.

Structural fix (3 parts, all necessary — a partial fix relocates the bug):
  1. DELETE the hook's cycle-execution path (_maybe_run_evolution + its call site).
     The scheduled `evolution-cycle` job becomes the SOLE trigger. The hook keeps
     ONLY its cheap governance work (quality gate, deprecation, promotion, v3
     classifier) which is measured at ~7ms.
  2. Give the scheduled job a timeout with real headroom over the measured 293s
     cycle (Gate-1 FLAW-1: it inherited the default 300s = only 7s margin →
     first slow run times out → circuit-breaker disables evolution after 3 fails).
  3. Verify the scheduler owns cadence (job_state.last_run), so the deleted hook
     path cannot re-trigger regardless of state-file contents.

These tests are the regression guard for all three parts. Mutation intent: if
someone re-adds the hook's evolution-cycle execution, or reverts the timeout to
the 300s default, a test here goes RED.
"""
from __future__ import annotations

import asyncio



# The measured cycle time that motivated this fix (daemon.log 2026-07-02:
# 18:21:33 triggering -> 18:26:26 complete = 293s). The scheduled job's timeout
# MUST clear this with generous headroom, since the transcript corpus only grows.
MEASURED_CYCLE_SECONDS = 293


class TestScheduledJobTimeoutHeadroom:
    """Part 2 — the sole trigger must be able to actually finish a real cycle."""

    def test_evolution_cycle_job_has_generous_timeout(self):
        """evolution-cycle job timeout must clear the measured 293s cycle with
        real headroom (Gate-1 FLAW-1: default 300s = 7s margin relocates the bug).

        RED before fix: the job has no `safety` override → inherits the 300s
        default (models.py JobSafety.timeout_seconds=300), which is < the required
        headroom, so this assertion fails.
        """
        from jobs.system_jobs import get_all_system_jobs

        jobs = {j.id: j for j in get_all_system_jobs()}
        assert "evolution-cycle" in jobs, "evolution-cycle system job must exist"
        job = jobs["evolution-cycle"]

        assert job.safety is not None, (
            "evolution-cycle must set an explicit JobSafety — inheriting the 300s "
            "default gives only 7s over the measured 293s cycle (Gate-1 FLAW-1)."
        )
        # Require >= 2x the measured cycle. 293s * 2 = 586s; we set 1800s (30min).
        assert job.safety.timeout_seconds >= 2 * MEASURED_CYCLE_SECONDS, (
            f"evolution-cycle timeout {job.safety.timeout_seconds}s must be >= "
            f"{2 * MEASURED_CYCLE_SECONDS}s (2x measured cycle) — a weekly "
            f"deterministic script has no reason to be capped near its runtime."
        )


def _make_context():
    from core.session_hooks import HookContext

    return HookContext(
        session_id="test-decouple",
        agent_id="default",
        message_count=5,
        session_start_time="2026-07-02T00:00:00Z",
        session_title="Test",
    )


class TestHookDoesNotRunEvolutionCycle:
    """Part 1 — the session-close hook must NOT execute the evolution cycle.

    This is the core structural fix: the ~5-min cycle is removed from the
    per-session lifecycle path entirely. The hook keeps ONLY its cheap
    governance work.
    """

    def test_hook_has_no_maybe_run_evolution_method(self):
        """The cycle-execution method is DELETED, not just unwired.

        Mutation guard: if someone re-adds _maybe_run_evolution, this goes RED.
        """
        from hooks.evolution_maintenance_hook import EvolutionMaintenanceHook

        assert not hasattr(EvolutionMaintenanceHook, "_maybe_run_evolution"), (
            "_maybe_run_evolution must be removed — the evolution cycle is now "
            "triggered SOLELY by the scheduled evolution-cycle job (run_6ac3fc0b)."
        )

    def test_execute_never_calls_run_evolution_cycle(self, tmp_path, monkeypatch):
        """Forcing execute() with a stale state file must NOT invoke the heavy
        cycle (R28: force the recovery path, assert the heavy call never happens).

        RED before fix: execute() awaits _maybe_run_evolution → run_evolution_cycle
        is called → the spy fires → assertion fails.
        """
        from hooks.evolution_maintenance_hook import EvolutionMaintenanceHook

        ctx_dir = tmp_path / ".context"
        ctx_dir.mkdir()
        # A stale state file — the OLD code would trigger the cycle here.
        state_file = ctx_dir / ".evolution_last_run"
        state_file.write_text("2026-06-25", encoding="utf-8")
        # Minimal EVOLUTION.md so the cheap governance work has something to read.
        (ctx_dir / "EVOLUTION.md").write_text(
            "# SwarmAI Evolution Registry\n\n"
            "## Capabilities Built\n\n_None._\n\n"
            "## Corrections Captured\n\n_None._\n\n"
            "## Competence Learned\n\n_None._\n",
            encoding="utf-8",
        )

        # Spy: if the heavy cycle is ever called, record it.
        called = []
        def _spy_cycle(*args, **kwargs):
            called.append(True)
            from core.evolution_optimizer import CycleReport
            return CycleReport(cycle_id="spy", skills_checked=0, eligible=0)

        monkeypatch.setattr(
            "core.evolution_optimizer.run_evolution_cycle", _spy_cycle
        )

        hook = EvolutionMaintenanceHook(context_dir=ctx_dir)
        asyncio.run(hook.execute(_make_context()))

        assert called == [], (
            "execute() must NOT call run_evolution_cycle — the cycle is decoupled "
            "to the scheduled job. It was called, so the hook still runs the "
            "5-min heavy work on the per-session path (the bug)."
        )
        # And the stale state file is NOT touched by the hook anymore (the
        # scheduler owns cadence, not this hook).
        assert state_file.read_text(encoding="utf-8") == "2026-06-25", (
            "hook must not write .evolution_last_run — the scheduler owns cadence."
        )

    def test_execute_still_runs_cheap_governance(self, tmp_path):
        """Deletion must be SURGICAL: the cheap governance work (quality gate on
        garbage competence entries) must still run in execute().

        This guards against over-broad deletion removing the hook's real value.
        """
        from hooks.evolution_maintenance_hook import EvolutionMaintenanceHook

        ctx_dir = tmp_path / ".context"
        ctx_dir.mkdir()
        state_file = ctx_dir / ".evolution_last_run"
        state_file.write_text("2026-06-25", encoding="utf-8")
        # A garbage competence entry (<3 words) that the quality gate removes.
        evo = ctx_dir / "EVOLUTION.md"
        evo.write_text(
            "# SwarmAI Evolution Registry\n\n"
            "## Capabilities Built\n\n_None._\n\n"
            "## Corrections Captured\n\n_None._\n\n"
            "## Competence Learned\n\n"
            "### K001 | reactive | skill | 2026-01-01\n"
            "- **Competence**: bad\n"
            "- **Usage Count**: 0\n"
            "- **Status**: active\n\n",
            encoding="utf-8",
        )

        hook = EvolutionMaintenanceHook(context_dir=ctx_dir)
        asyncio.run(hook.execute(_make_context()))

        # The garbage entry (single word "bad") should have been removed by the
        # quality gate — proving the cheap governance path still executes.
        # (ID must match _ENTRY_HEADER_RE: ^[EOKCF]\d{3} — K001 qualifies.)
        assert "K001" not in evo.read_text(encoding="utf-8"), (
            "quality gate must still run in execute() — deletion was over-broad "
            "if the garbage competence entry survived."
        )
