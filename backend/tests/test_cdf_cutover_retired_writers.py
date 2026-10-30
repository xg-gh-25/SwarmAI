"""CDF cutover: the retired writers stay retired, and what they also did still runs.

Tests:
- distillation_hook and user_observer_hook are gone, and main.py registers neither.
- EvolutionMaintenanceHook.execute leaves EVOLUTION.md byte-identical and has no
  mutating method left (quality gate, fold, size valve, lifecycle).
- L4 effectiveness scoring (once in the distillation hook) runs from the briefing's
  update_learning_from_activity, once per new DailyActivity session.
- memory_health reports MEMORY size against the injection budget, not a valve.
- context_health's write-governance check keeps the orphan-section alarm, no caps.
- s_ddd-persist's bundled writer refuses inside SwarmAI and appends elsewhere.
"""
from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path

from core.proactive_learning import LearningState, update_learning_from_activity

BACKEND = Path(__file__).resolve().parents[1]


def test_retired_hook_modules_are_gone_and_unregistered():
    for mod in ("hooks.distillation_hook", "hooks.user_observer_hook"):
        assert importlib.util.find_spec(mod) is None, mod
    main = (BACKEND / "main.py").read_text(encoding="utf-8")
    assert "DistillationTriggerHook" not in main
    assert "UserObserverHook" not in main


def test_evolution_maintenance_never_writes(tmp_path: Path):
    from core.session_hooks import HookContext
    from hooks.evolution_maintenance_hook import EvolutionMaintenanceHook

    for gone in ("_quality_gate", "_fold_corrections", "_size_evict", "_size_evict_locked",
                 "_deprecate_entry", "_prune_entry"):
        assert not hasattr(EvolutionMaintenanceHook, gone), gone
    ctx = tmp_path / ".context"
    ctx.mkdir()
    evo = ctx / "EVOLUTION.md"
    evo.write_text(
        "# EVOLUTION\n\n## Capabilities Built\n\n"
        "### E001 | 2020-01-01\n- **Status**: active\n- **Usage Count**: 0\n\n"
        "## Competence Learned\n\n### K001 | 2020-01-02\n- **Status**: deprecated\n"
        "- **Usage Count**: 0\n", encoding="utf-8")
    before = evo.read_bytes()
    hook = EvolutionMaintenanceHook(context_dir=ctx, deprecation_days=1)
    context = HookContext(session_id="s", agent_id="a", message_count=1,
                          session_start_time="2026-10-28T00:00:00", session_title="t")
    asyncio.run(hook.execute(context))
    assert evo.read_bytes() == before


def _day(daily: Path, stem: str, sessions: int, delivered: list[str]) -> None:
    body = "\n".join(f"- {d}" for d in delivered)
    (daily / f"{stem}.md").write_text(
        f"---\nsessions_count: {sessions}\n---\n\n**Delivered:**\n{body}\n", encoding="utf-8")


def test_effectiveness_scoring_runs_once_per_new_session(tmp_path: Path):
    daily = tmp_path / "DailyActivity"
    daily.mkdir()
    _day(daily, "2026-10-27", 1, ["fix the recall budget"])
    state = LearningState(last_briefing_suggested=["fix the recall budget", "write the HLD"])

    state = update_learning_from_activity(state, daily)
    assert state.effectiveness["total_suggestions"] == 2
    assert state.effectiveness["followed"] == 1

    # same file, same session count: the dedup guard skips it
    state = update_learning_from_activity(state, daily)
    assert state.effectiveness["total_suggestions"] == 2

    # a new session on the day is counted
    _day(daily, "2026-10-27", 2, ["fix the recall budget", "write the HLD"])
    state = update_learning_from_activity(state, daily)
    assert state.effectiveness["total_suggestions"] == 4
    assert state.effectiveness["followed"] == 3


def test_memory_health_reports_the_size_and_caps_nothing():
    from jobs.handlers.memory_health import _run_integrity_checks

    findings = _run_integrity_checks("## Decisions\n" + "- [DEC01] entry with words\n" * 20000)
    size = next(f for f in findings if f["check"] == "body_size")
    assert size["status"] == "pass" and "injected whole" in size["detail"]


def test_write_governance_keeps_orphan_alarm_without_caps(tmp_path: Path):
    from hooks.context_health_hook import ContextHealthHook

    ctx = tmp_path / ".context"
    ctx.mkdir()
    many = "\n".join(f"- [GL{i:02d}] entry {i}" for i in range(400))
    (ctx / "MEMORY.md").write_text(
        f"# MEMORY\n\n## Guidelines\n{many}\n\n## Distilled\n- stray\n", encoding="utf-8")
    findings = ContextHealthHook()._check_write_governance(ctx)
    assert len(findings) == 1
    assert "orphan '## Distilled'" in findings[0]


def test_ddd_persist_writer_runs_only_outside_swarmai(tmp_path: Path):
    """The locked writer s_ddd-persist ships appends on a host without SwarmAI and
    refuses where SWARM_BACKEND_BIN is set (CDF writes DDD docs there)."""
    import os
    import subprocess
    import sys

    script = BACKEND / "templates" / "ddd-skills" / "s_ddd-persist" / "scripts" / "locked_write.py"
    doc = tmp_path / "TECH.md"
    doc.write_text("# T\n\n## Runtime Traps\n", encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k != "SWARM_BACKEND_BIN"}
    args = [sys.executable, str(script), "--file", str(doc), "--section", "Runtime Traps",
            "--prepend"]
    outside = subprocess.run(args + ["- **outside** x"], env=env, capture_output=True,
                             text=True, timeout=60)
    assert outside.returncode == 0, outside.stderr
    inside = subprocess.run(args + ["- **inside** y"], env={**env, "SWARM_BACKEND_BIN": "/x"},
                            capture_output=True, text=True, timeout=60)
    assert inside.returncode != 0 and "ingest submit" in inside.stderr
    text = doc.read_text(encoding="utf-8")
    assert "**outside**" in text and "**inside**" not in text
