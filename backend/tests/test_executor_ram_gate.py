"""Job agent_task RAM-admission gate (run_409392d4).

Scheduled agent_task jobs spawn `claude --print` CLIs that consume ~1-1.5GB RAM,
but did so OUTSIDE the chat spawn_budget RAM gate — so a job burst could starve
chat-tab spawns. This gate reuses the EXISTING central resource_monitor.spawn_budget()
(not a new per-call cost cap — STEERING #2) and DEFERS the job (skip → retry next
tick) rather than truncating in-progress work.

Tests: under memory pressure (spawn_budget.can_spawn=False) an agent_task is
skipped and NO CLI is spawned; when memory is fine it proceeds normally.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from jobs.models import Job, SchedulerState, SchedulerDefaults
from jobs.executor import execute_job


def _agent_job() -> Job:
    return Job(id="reflect-job", name="Reflect", type="agent_task",
               schedule="0 2 * * *", config={"prompt": "do stuff"})


def _base_state() -> SchedulerState:
    return SchedulerState(jobs={}, monthly_spend_usd=0.0)


def test_agent_task_deferred_under_memory_pressure():
    """spawn_budget.can_spawn=False → job skipped, _handle_agent_task NOT called."""
    job = _agent_job()
    state = _base_state()
    defaults = SchedulerDefaults()

    denied = SimpleNamespace(can_spawn=False, reason="projected 94% > 90% threshold")
    with patch("core.resource_monitor.resource_monitor.spawn_budget",
               return_value=denied) as mock_budget, \
         patch("jobs.executor._handle_agent_task") as mock_handle:
        result = execute_job(job, state, feeds=[], defaults=defaults)

    assert mock_budget.called, "spawn_budget gate was not consulted"
    assert not mock_handle.called, "CLI spawn ran despite memory pressure — gate failed"
    assert result.status == "skipped"
    assert "memor" in result.summary.lower() or "budget" in result.summary.lower()


def test_agent_task_proceeds_when_memory_ok():
    """spawn_budget.can_spawn=True → job runs normally (_handle_agent_task called)."""
    job = _agent_job()
    state = _base_state()
    defaults = SchedulerDefaults()

    ok = SimpleNamespace(can_spawn=True, reason="ok")
    sentinel = MagicMock()
    sentinel.status = "success"
    with patch("core.resource_monitor.resource_monitor.spawn_budget",
               return_value=ok), \
         patch("jobs.executor._handle_agent_task", return_value=sentinel) as mock_handle:
        result = execute_job(job, state, feeds=[], defaults=defaults)

    assert mock_handle.called, "agent_task did not run despite healthy memory"
    assert result is sentinel


def test_ram_gate_never_raises_if_router_absent():
    """spawn_budget with alive_count fallback must not crash when router is None."""
    job = _agent_job()
    state = _base_state()
    defaults = SchedulerDefaults()

    ok = SimpleNamespace(can_spawn=True, reason="ok")
    sentinel = MagicMock(status="success")
    # session_registry.session_router is None (pre-init / job-only context)
    with patch("core.session_registry.session_router", None), \
         patch("core.resource_monitor.resource_monitor.spawn_budget",
               return_value=ok) as mock_budget, \
         patch("jobs.executor._handle_agent_task", return_value=sentinel):
        result = execute_job(job, state, feeds=[], defaults=defaults)

    # alive_count fell back to 0, gate still consulted, no crash
    assert mock_budget.called
    assert result is sentinel
