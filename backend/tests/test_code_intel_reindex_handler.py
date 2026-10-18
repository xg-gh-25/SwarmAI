"""Tests for the code_intel_reindex script entrypoint's hang diagnostics.

What is tested:
  - ``main`` arms ``faulthandler.dump_traceback_later`` at
    SWARM_JOB_TIMEOUT_S - 10 (floor 5s, default budget 120s, bad value falls
    back) with exit=False, and ALWAYS cancels it — on success and on raise.
  - ``--full`` is forwarded to ``reindex_projects``.
  - Phase timing lines go to stderr.

Methodology: faulthandler and reindex_projects are mocked on the module, so
no real watchdog thread is armed and no workspace/DB is touched.
"""
from unittest.mock import MagicMock

import pytest

import jobs.handlers.code_intel_reindex as mod


@pytest.fixture
def fake_fh(monkeypatch):
    fh = MagicMock()
    monkeypatch.setattr(mod, "faulthandler", fh)
    return fh


@pytest.mark.parametrize("env,expected", [
    (None, 110), ("300", 290), ("120.0", 110), ("12", 5), ("3", 5), ("garbage", 110),
])
def test_dump_after_seconds(monkeypatch, env, expected):
    if env is None:
        monkeypatch.delenv("SWARM_JOB_TIMEOUT_S", raising=False)
    else:
        monkeypatch.setenv("SWARM_JOB_TIMEOUT_S", env)
    assert mod._dump_after_seconds() == expected


def test_main_arms_then_cancels_on_success(monkeypatch, fake_fh, capsys):
    monkeypatch.setenv("SWARM_JOB_TIMEOUT_S", "300")
    calls = []

    def fake_reindex(full=False):
        # Armed BEFORE the work runs, not cancelled yet.
        calls.append(("reindex", full))
        assert fake_fh.dump_traceback_later.call_count == 1
        assert fake_fh.cancel_dump_traceback_later.call_count == 0
        return {"status": "success", "projects": []}

    monkeypatch.setattr(mod, "reindex_projects", fake_reindex)
    summary = mod.main(["--full"])

    assert summary == {"status": "success", "projects": []}
    assert calls == [("reindex", True)]
    fake_fh.dump_traceback_later.assert_called_once_with(290, exit=False)
    fake_fh.cancel_dump_traceback_later.assert_called_once_with()
    err = capsys.readouterr().err
    assert "stack_dump_at=290s" in err
    assert "all: total" in err


def test_main_cancels_even_when_reindex_raises(monkeypatch, fake_fh):
    monkeypatch.delenv("SWARM_JOB_TIMEOUT_S", raising=False)

    def boom(full=False):
        raise RuntimeError("db locked")

    monkeypatch.setattr(mod, "reindex_projects", boom)
    with pytest.raises(RuntimeError, match="db locked"):
        mod.main([])
    fake_fh.dump_traceback_later.assert_called_once_with(110, exit=False)
    fake_fh.cancel_dump_traceback_later.assert_called_once_with()


def test_main_incremental_by_default(monkeypatch, fake_fh):
    seen = {}

    def fake_reindex(full=False):
        seen["full"] = full
        return {}

    monkeypatch.setattr(mod, "reindex_projects", fake_reindex)
    mod.main([])
    assert seen["full"] is False


def test_phase_prints_timing_to_stderr(capsys):
    import time
    mark = mod._phase("Proj", "parse_repo", time.monotonic() - 1.5)
    err = capsys.readouterr().err
    assert err.startswith("[code_intel_reindex] Proj: parse_repo ")
    assert err.strip().endswith("s")
    assert isinstance(mark, float)
