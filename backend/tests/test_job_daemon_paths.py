"""Tests for job system path resolution in daemon context.

Verifies that script jobs resolve paths correctly regardless of whether
the process is running from swarmai source tree or PyInstaller daemon binary.
Also: _handle_script timeout diagnostics (stdout/stderr tails on
TimeoutExpired, SWARM_JOB_TIMEOUT_S passed to the script env).
"""
import os
import time
from pathlib import Path
from unittest.mock import patch



class TestSwarmaRootResolution:
    """_SWARMAI_ROOT must resolve to swarmai source tree in all contexts."""

    def test_swarmai_root_from_env_var(self, tmp_path):
        """SWARMAI_SOURCE env var overrides __file__-based resolution."""
        from jobs.system_jobs import _get_swarmai_root
        # Use tmp_path (real directory) so is_dir() check passes
        with patch.dict(os.environ, {"SWARMAI_SOURCE": str(tmp_path)}):
            assert _get_swarmai_root() == str(tmp_path)

    def test_swarmai_root_fallback_is_valid_dir(self):
        """Without env var, falls back to a path that actually exists."""
        from jobs.system_jobs import _get_swarmai_root
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SWARMAI_SOURCE", None)
            root = _get_swarmai_root()
            # Should be a real directory (the swarmai source tree)
            assert Path(root).is_dir(), f"_get_swarmai_root() returned non-existent: {root}"

    def test_swarmai_root_never_resolves_to_daemon_internal(self):
        """Must never resolve to daemon/_internal/ even if __file__ is there."""
        from jobs.system_jobs import _get_swarmai_root
        root = _get_swarmai_root()
        assert "daemon/_internal" not in root
        assert "_internal" not in root


class TestFallbackCwdHandling:
    """Fallback script cwd must never be None."""

    def test_fallback_job_cwd_not_none(self):
        """When fallback_script has no cwd, executor provides a valid default."""
        from jobs.executor import _handle_script
        from jobs.models import Job, JobSafety

        fallback_job = Job(
            id="test-fallback",
            name="Test Fallback",
            type="script",
            schedule="0 0 * * *",
            enabled=True,
            category="system",
            config={
                "command": "echo ok",
                "cwd": None,  # This was the bug — None cwd
                "output_mode": "report",
            },
            safety=JobSafety(max_budget_usd=0, timeout_seconds=5),
        )

        # Should not raise TypeError
        from jobs.models import SchedulerState
        state = SchedulerState(jobs={})
        result = _handle_script(fallback_job, state)
        # May fail for other reasons but should NOT crash with TypeError
        assert result.status in ("success", "failed")
        assert "TypeError" not in (result.error or "")
        assert "NoneType" not in (result.summary or "")


class TestEvolutionLockStaleness:
    """Stale evolution lock files should be force-broken."""

    def test_stale_lock_is_broken_when_holder_dead(self, tmp_path):
        """Lock older than 1 hour with dead PID should be force-broken."""
        lock_file = tmp_path / ".evolution_cycle.lock"
        # Write a PID that doesn't exist (99999999)
        lock_file.write_text("99999999")
        # Make it appear old (2 hours ago)
        old_time = time.time() - 7200
        os.utime(lock_file, (old_time, old_time))

        from core.evolution_optimizer import _break_stale_lock
        result = _break_stale_lock(lock_file, max_age_seconds=3600)
        assert result is True, "Should have broken the stale lock (dead PID)"

    def test_stale_lock_not_broken_when_holder_alive(self, tmp_path):
        """Lock older than 1 hour with alive PID should NOT be broken."""
        lock_file = tmp_path / ".evolution_cycle.lock"
        # Write our own PID (definitely alive)
        lock_file.write_text(str(os.getpid()))
        # Make it appear old
        old_time = time.time() - 7200
        os.utime(lock_file, (old_time, old_time))

        from core.evolution_optimizer import _break_stale_lock
        result = _break_stale_lock(lock_file, max_age_seconds=3600)
        assert result is False, "Should NOT break lock when holder is alive"

    def test_fresh_lock_not_broken(self, tmp_path):
        """Lock younger than 1 hour should NOT be broken."""
        lock_file = tmp_path / ".evolution_cycle.lock"
        lock_file.touch()
        # Fresh — just created

        from core.evolution_optimizer import _break_stale_lock
        result = _break_stale_lock(lock_file, max_age_seconds=3600)
        assert result is False, "Should NOT break a fresh lock"


def _timeout_job(timeout_s: int = 7):
    from jobs.models import Job, JobSafety
    return Job(
        id="test-timeout", name="Test Timeout", type="script",
        schedule="0 0 * * *", enabled=True, category="system",
        config={"command": "true", "cwd": None, "output_mode": "report"},
        safety=JobSafety(max_budget_usd=0, timeout_seconds=timeout_s),
    )


class TestScriptTimeoutDiagnostics:
    """_handle_script keeps the killed script's output and passes its budget.

    subprocess.run is mocked (no real process, no real wait): the test drives
    the TimeoutExpired branch with bytes / None output — CPython hands back
    bytes there even with text=True.
    """

    def test_timeout_includes_stderr_and_stdout_tails(self):
        import subprocess
        from jobs.executor import _handle_script
        from jobs.models import SchedulerState

        exc = subprocess.TimeoutExpired(
            "true", 7, output=b"phase stdout line\n",
            stderr=b"[code_intel_reindex] p: parse_repo 3.0s\nThread 0x1 (most recent call first):\n",
        )
        with patch("jobs.executor.subprocess.run", side_effect=exc):
            result = _handle_script(_timeout_job(), SchedulerState(jobs={}))
        assert result.status == "failed"
        assert result.summary.startswith("Script timed out after 7s")
        assert "--- stderr (tail) ---" in result.summary
        assert "most recent call first" in result.summary
        assert "--- stdout (tail) ---" in result.summary
        assert "phase stdout line" in result.summary
        # error (-> JobState.last_error) carries a short stderr tail.
        assert result.error.startswith("timeout: ")
        assert "most recent call first" in result.error
        assert len(result.error) <= len("timeout: ") + 400

    def test_timeout_without_output_keeps_plain_error(self):
        import subprocess
        from jobs.executor import _handle_script
        from jobs.models import SchedulerState

        exc = subprocess.TimeoutExpired("true", 7, output=None, stderr=None)
        with patch("jobs.executor.subprocess.run", side_effect=exc):
            result = _handle_script(_timeout_job(), SchedulerState(jobs={}))
        assert result.error == "timeout"
        assert result.summary == "Script timed out after 7s"

    def test_stderr_tail_is_bounded_to_last_chars(self):
        import subprocess
        from jobs.executor import _handle_script
        from jobs.models import SchedulerState

        big = ("x" * 10_000 + "TAIL_MARKER").encode()
        exc = subprocess.TimeoutExpired("true", 7, output=None, stderr=big)
        with patch("jobs.executor.subprocess.run", side_effect=exc):
            result = _handle_script(_timeout_job(), SchedulerState(jobs={}))
        assert result.summary.endswith("TAIL_MARKER")
        assert len(result.summary) < 3200  # header + 3000-char tail

    def test_output_tail_handles_str_bytes_none(self):
        from jobs.executor import _output_tail
        assert _output_tail(None, 10) == ""
        assert _output_tail(b"", 10) == ""
        assert _output_tail(b"abc\xff def ", 4) == "def"
        assert _output_tail("hello world", 5) == "world"

    def test_script_env_carries_timeout_budget(self):
        from jobs.executor import _handle_script
        from jobs.models import SchedulerState
        import subprocess

        seen = {}

        def fake_run(*args, **kwargs):
            seen.update(kwargs)
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

        with patch("jobs.executor.subprocess.run", side_effect=fake_run):
            result = _handle_script(_timeout_job(timeout_s=42), SchedulerState(jobs={}))
        assert result.status == "success"
        assert seen["env"]["SWARM_JOB_TIMEOUT_S"] == "42"
        assert seen["timeout"] == 42
        # Must not mutate the daemon's own environment.
        assert os.environ.get("SWARM_JOB_TIMEOUT_S") != "42"
