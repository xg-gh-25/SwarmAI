"""Runner CPU budget: worker scale-up and the vitest pool cap (pipeline_test.py).

What is tested:
- ``_scale_up`` raises the base worker count toward the idle cores, never lowers it,
  stops at MAX_WORKERS / cpu-2 / free memory, and keeps the base when another runner
  holds a slot, when SWARM_TEST_MAX_WORKERS is set, or when psutil fails.
- The vitest job passes ``--maxWorkers`` equal to the runner's worker budget, at least
  2 (vitest's default pool takes almost every core while the runner counts it as one
  worker; a single fork lets one hanging file starve the rest).

Methodology: psutil, os.cpu_count and the slot directory are faked per test
(SWARM_TEST_CACHE_DIR → tmp_path); no subprocess is started.
"""
import sys
import types

import pytest

from scripts import pipeline_test as pt

GB = 1024 ** 3


@pytest.fixture
def machine(monkeypatch, tmp_path):
    """Fake 12-core machine: set ``busy_pct`` / ``free_gb`` on the returned object."""
    m = types.SimpleNamespace(busy_pct=0.0, free_gb=32, fail=False)

    def cpu_percent(interval=None):
        if m.fail:
            raise RuntimeError("no psutil")
        return m.busy_pct

    fake = types.SimpleNamespace(
        cpu_percent=cpu_percent,
        virtual_memory=lambda: types.SimpleNamespace(available=m.free_gb * GB))
    monkeypatch.setitem(sys.modules, "psutil", fake)
    monkeypatch.setattr(pt.os, "cpu_count", lambda: 12)
    monkeypatch.setenv("SWARM_TEST_CACHE_DIR", str(tmp_path))
    monkeypatch.delenv("SWARM_TEST_MAX_WORKERS", raising=False)
    return m


def test_idle_machine_scales_to_the_ceiling(machine):
    assert pt._scale_up(4) == min(pt.MAX_WORKERS, 12 - 2)


def test_partly_busy_machine_scales_to_idle_cores(machine):
    machine.busy_pct = 50.0  # 6 idle cores
    assert pt._scale_up(4) == 6


def test_loaded_machine_never_drops_below_base(machine):
    machine.busy_pct = 100.0
    assert pt._scale_up(4) == 4


def test_free_memory_caps_the_scale_up(machine):
    machine.free_gb = 9  # 9 - 4 GB headroom = 5 workers
    assert pt._scale_up(4) == 5


def test_another_runner_holding_a_slot_keeps_the_base(machine, tmp_path):
    fd = pt._try_lock(tmp_path / "slots" / "1.lock", 0)
    try:
        assert pt._scale_up(4) == 4
    finally:
        pt._unlock(fd)
    assert pt._scale_up(4) > 4  # slot released → scales again


def test_explicit_cap_env_disables_scaling(machine, monkeypatch):
    monkeypatch.setenv("SWARM_TEST_MAX_WORKERS", "2")
    assert pt._scale_up(4) == 4  # the cap itself is applied by _worker_count's caller


def test_psutil_failure_keeps_the_base(machine):
    machine.fail = True
    assert pt._scale_up(3) == 3


def test_vitest_pool_is_capped_to_the_runner_budget(tmp_path):
    ctx = types.SimpleNamespace(repo=tmp_path, workdir=tmp_path, workers=5)
    jobs = pt._frontend_jobs(ctx, ["desktop/src/a.test.ts"], with_tsc=False)
    assert "--maxWorkers=5" in jobs[0].argv
    ctx.workers = 1  # never 1 fork: one hanging file would starve every other file
    assert "--maxWorkers=2" in pt._frontend_jobs(ctx, ["desktop/src/a.test.ts"], False)[0].argv
