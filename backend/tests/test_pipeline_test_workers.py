"""Runner CPU budget: worker scale-up, the vitest pool cap, and the cross-run
worker-token pool (pipeline_test.py).

What is tested:
- ``_scale_up`` raises the base worker count toward the idle cores, never lowers it,
  stops at MAX_WORKERS / cpu-2 / free memory, and keeps the base when another runner
  holds a worker token, when SWARM_TEST_MAX_WORKERS is set, or when psutil fails.
- The vitest job passes ``--maxWorkers`` = a share of the runner's workers, at least 2
  (a single fork lets one hanging file starve the rest), and weighs that many in
  ``_run_jobs``; it leaves one worker for tsc and takes half beside pytest shards.
- Token pool: budget = cpu - WORKER_BUDGET_RESERVE (floor 2, env override); concurrent
  runners split it instead of each taking a full fleet; an exhausted pool waits then
  runs ONE untokened worker; tokens a call cannot use are given back; ``_run_jobs``
  never exceeds its workers counting job weight, and a job heavier than the budget
  still runs alone.

Methodology: psutil, os.cpu_count and the token directory are faked per test
(SWARM_TEST_CACHE_DIR / tmp_path); real flock token files; no subprocess is started.
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


def test_another_runner_holding_a_token_keeps_the_base(machine, tmp_path):
    fd = pt._try_lock(pt._token_path(tmp_path, 1), 0)
    try:
        assert pt._scale_up(4) == 4
    finally:
        pt._unlock(fd)
    assert pt._scale_up(4) > 4  # token released → scales again


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


def test_vitest_pool_shares_the_workers_with_pytest_shards_and_tsc(tmp_path):
    ctx = types.SimpleNamespace(repo=tmp_path, workdir=tmp_path, workers=6)
    f = ["desktop/src/a.test.ts"]
    both = pt._frontend_jobs(ctx, f, with_tsc=False, with_backend=True)[0]
    assert "--maxWorkers=3" in both.argv and both.weight == 3  # half beside pytest shards
    tsc = pt._frontend_jobs(ctx, f, with_tsc=True)
    assert "--maxWorkers=5" in tsc[0].argv and tsc[0].weight == 5  # one left for tsc
    assert tsc[1].kind == "tsc" and tsc[1].weight == 1


# ── cross-run worker-token pool ─────────────────────────────────────────────
@pytest.fixture
def pool(monkeypatch, tmp_path):
    monkeypatch.setenv("SWARM_TEST_WORKER_BUDGET", "4")
    monkeypatch.setattr(pt, "SLOT_WAIT_S", 0.3)
    return tmp_path


def test_budget_is_cores_minus_the_reserve(monkeypatch):
    monkeypatch.delenv("SWARM_TEST_WORKER_BUDGET", raising=False)
    monkeypatch.setattr(pt.os, "cpu_count", lambda: 12)
    assert pt._worker_budget() == 12 - pt.WORKER_BUDGET_RESERVE
    monkeypatch.setattr(pt.os, "cpu_count", lambda: 4)
    assert pt._worker_budget() == 2  # floor
    for bad in ("0", "-1", "x", ""):
        monkeypatch.setenv("SWARM_TEST_WORKER_BUDGET", bad)
        assert pt._worker_budget() == 2, bad


def test_two_runners_split_the_pool_instead_of_each_taking_it_all(pool):
    w = []
    a = pt._acquire_workers(pool, 3, w)
    b = pt._acquire_workers(pool, 3, w)
    try:
        assert len(a) == 3 and len(b) == 1  # 4 tokens total, never 6 workers
        assert any("1 of 3 workers granted" in x for x in w)
    finally:
        pt._release_workers(a)
        pt._release_workers(b)
    c = pt._acquire_workers(pool, 4, [])
    assert len(c) == 4  # released tokens are free again
    pt._release_workers(c)


def test_exhausted_pool_waits_then_runs_one_untokened_worker(pool):
    held = pt._acquire_workers(pool, 4, [])
    w = []
    try:
        assert pt._acquire_workers(pool, 2, w) == []
        assert any("busy" in x and "running with 1 worker" in x for x in w)
    finally:
        pt._release_workers(held)


def test_unused_tokens_are_given_back(pool):
    toks = pt._acquire_workers(pool, 4, [])
    jobs = [types.SimpleNamespace(weight=1)]  # a one-file tdd call
    kept = pt._trim_tokens(toks, jobs)
    try:
        assert len(kept) == 1
        other = pt._acquire_workers(pool, 3, [])
        assert len(other) == 3  # the three trimmed tokens went back to the pool
        pt._release_workers(other)
    finally:
        pt._release_workers(kept)


def test_run_jobs_counts_a_vitest_pool_as_its_weight(tmp_path):
    started = []

    class J:
        def __init__(self, name, weight):
            self.name, self.weight, self.rc, self.ticks = name, weight, None, 0

        def start(self):
            started.append((self.name, [r.name for r in running]))
            running.append(self)

        def poll(self):
            self.ticks += 1
            if self.ticks >= 3:
                running.remove(self)
                return True
            return False

    running: list = []
    import time
    vit, a, b = J("vitest", 3), J("a", 1), J("b", 1)
    pt._run_jobs([vit, a, b], 4, time.monotonic() + 10)
    # 4 workers: vitest (3) + a (1) fit together; b waits until something finishes
    assert started[0] == ("vitest", []) and started[1] == ("a", ["vitest"])
    assert started[2][0] == "b" and len(started[2][1]) < 2
    # a job heavier than the whole budget still runs when nothing else does
    started.clear()
    pt._run_jobs([J("big", 9)], 2, time.monotonic() + 10)
    assert started == [("big", [])]
