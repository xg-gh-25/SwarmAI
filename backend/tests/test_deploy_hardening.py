"""Deploy / lifecycle hardening (follow-ups to the 2026-10-18 offline dive).

What is tested:
- install_backend_daemon._deploy_daemon_binary deploys a onedir bundle
  DIRECTORY (it used to copy2 the directory as a file → IsADirectoryError),
  keeps the daemon's own resources/ + .version, and reports a failed sync.
- daemon-lib.sh _deploy_daemon_binary returns non-zero when rsync fails even
  with errexit suspended, and prod.sh / dev.sh stop on version-sync / deploy
  failures instead of restarting.
- A C034-detached lifecycle op (restart/stop/reload) logs to
  daemon-lifecycle.log with its exit code instead of /dev/null.
- The SDK's "exit code -9" reader ERROR is downgraded to INFO only right after
  an intentional SessionUnit kill; an unexplained -9 stays ERROR.
- The startup CLI sweep is skipped for throwaway instances (verify probe,
  new-user smoke via SWARMAI_NO_CLI_SWEEP=1).

Methodology: real shell functions under bash with a temp HOME and stubbed
launchctl; the installer and log filter are exercised in-process.
"""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
DAEMON_LIB = REPO / "scripts" / "daemon-lib.sh"


def _bundle(root: Path, marker: str) -> Path:
    b = root / "bundle"
    (b / "_internal").mkdir(parents=True, exist_ok=True)
    (b / "python-backend").write_text(f"#!/bin/sh\n# {marker}\n")
    (b / "_internal" / "lib.txt").write_text(marker)
    return b


class TestInstallerOnedir:
    def test_deploys_bundle_dir_and_keeps_daemon_resources(self, tmp_path):
        from channels import install_backend_daemon as mod

        src = _bundle(tmp_path, "v2")
        daemon = tmp_path / "daemon"
        (daemon / "resources").mkdir(parents=True)
        (daemon / "resources" / "default-agent.json").write_text("{}")
        (daemon / ".version").write_text("1.0 abc")
        (daemon / "stale.so").write_text("old")  # not in bundle → pruned

        assert mod._deploy_daemon_binary(sources=[src], daemon_dir=daemon) is True
        assert "v2" in (daemon / "python-backend").read_text()
        assert (daemon / "_internal" / "lib.txt").read_text() == "v2"
        assert (daemon / "resources" / "default-agent.json").exists()
        assert (daemon / ".version").read_text() == "1.0 abc"
        assert not (daemon / "stale.so").exists()
        assert (daemon / "python-backend").stat().st_mode & 0o111

    def test_up_to_date_skips(self, tmp_path):
        import os
        from channels import install_backend_daemon as mod

        src = _bundle(tmp_path, "v1")
        daemon = tmp_path / "daemon"
        assert mod._deploy_daemon_binary(sources=[src], daemon_dir=daemon) is True
        (daemon / "python-backend").write_text("deployed-later")
        st = (src / "python-backend").stat()
        os.utime(daemon / "python-backend", (st.st_atime + 60, st.st_mtime + 60))
        assert mod._deploy_daemon_binary(sources=[src], daemon_dir=daemon) is True
        assert (daemon / "python-backend").read_text() == "deployed-later"

    def test_failed_sync_raises_and_install_stops_before_launchctl(self, tmp_path, monkeypatch):
        import pytest
        from unittest.mock import MagicMock, patch
        from channels import install_backend_daemon as mod

        src = _bundle(tmp_path, "v1")
        calls = []

        def fake_run(cmd, **kw):
            calls.append(cmd[0])
            r = MagicMock()
            r.returncode = 23 if cmd[0] == "rsync" else 0
            r.stderr = "disk full"
            return r

        with patch("subprocess.run", side_effect=fake_run):
            with pytest.raises(mod.DaemonDeployError):
                mod._deploy_daemon_binary(sources=[src], daemon_dir=tmp_path / "d")
        # install(): the deploy error must abort before any launchctl call
        calls.clear()
        monkeypatch.setattr(mod, "LAUNCH_AGENTS", tmp_path / "la")
        with patch("subprocess.run", side_effect=fake_run), \
             patch.object(mod, "_deploy_daemon_binary",
                          side_effect=mod.DaemonDeployError("boom")):
            with pytest.raises(mod.DaemonDeployError):
                mod.install()
        assert "launchctl" not in calls

    def test_newest_source_wins(self, tmp_path):
        import os
        from channels import install_backend_daemon as mod

        old = _bundle(tmp_path / "a", "old")
        new = _bundle(tmp_path / "b", "new")
        os.utime(old / "python-backend", (1_700_000_000, 1_700_000_000))
        daemon = tmp_path / "daemon"
        assert mod._deploy_daemon_binary(sources=[old, new], daemon_dir=daemon) is True
        assert "new" in (daemon / "python-backend").read_text()

    def test_failed_bundle_sync_leaves_live_dir_untouched(self, tmp_path):
        import pytest
        from unittest.mock import MagicMock, patch
        from channels import install_backend_daemon as mod

        src = _bundle(tmp_path, "v2")
        daemon = tmp_path / "daemon"
        (daemon / "_internal").mkdir(parents=True)
        (daemon / "python-backend").write_text("LIVE")
        import os
        os.utime(daemon / "python-backend", (1_700_000_000, 1_700_000_000))  # older build
        real_run = subprocess.run

        def run(cmd, **kw):
            if cmd[0] == "rsync" and "--delete" in cmd:
                r = MagicMock(); r.returncode = 12; r.stderr = "disk full"
                return r
            return real_run(cmd, **kw)

        with patch("subprocess.run", side_effect=run):
            with pytest.raises(mod.DaemonDeployError):
                mod._deploy_daemon_binary(sources=[src], daemon_dir=daemon)
        assert (daemon / "python-backend").read_text() == "LIVE"
        assert not (tmp_path / "daemon.next").exists()

    def test_symlinked_prev_leftover_is_cleared(self, tmp_path):
        """rmtree(ignore_errors) silently keeps a symlink → the live→prev rename
        would fail; the leftover must be unlinked (the target left alone)."""
        from channels import install_backend_daemon as mod

        src = _bundle(tmp_path, "v2")
        daemon = tmp_path / "daemon"
        (daemon / "_internal").mkdir(parents=True)
        (daemon / "python-backend").write_text("LIVE")
        target = tmp_path / "elsewhere"
        target.mkdir()
        (target / "keep.txt").write_text("x")
        (tmp_path / "daemon.prev").symlink_to(target)

        mod._sync_bundle_staged(src, daemon)
        assert "v2" in (daemon / "python-backend").read_text()
        assert (tmp_path / "daemon.prev" / "python-backend").read_text() == "LIVE"
        assert (target / "keep.txt").exists()

    def test_failed_move_aside_raises_deploy_error_and_keeps_live(self, tmp_path, monkeypatch):
        import pytest
        from channels import install_backend_daemon as mod

        src = _bundle(tmp_path, "v2")
        daemon = tmp_path / "daemon"
        (daemon / "_internal").mkdir(parents=True)
        (daemon / "python-backend").write_text("LIVE")
        real_rename = Path.rename

        def rename(self, dst):
            if Path(dst).name == "daemon.prev":
                raise OSError("EBUSY")
            return real_rename(self, dst)

        monkeypatch.setattr(Path, "rename", rename)
        with pytest.raises(mod.DaemonDeployError, match="moving the live bundle aside"):
            mod._sync_bundle_staged(src, daemon)
        assert (daemon / "python-backend").read_text() == "LIVE"
        assert not (tmp_path / "daemon.next").exists()

    def test_no_source_returns_false(self, tmp_path):
        from channels import install_backend_daemon as mod
        assert mod._deploy_daemon_binary(sources=[tmp_path / "missing"],
                                         daemon_dir=tmp_path / "d") is False


def _bash(script: str, home: Path) -> subprocess.CompletedProcess:
    prelude = f"""
export HOME="{home}"
PROJECT_ROOT="{home}/repo"; DESKTOP_DIR="{home}/desktop"; LOG_DIR="{home}/logs"
_log() {{ echo "LOG $*"; }}; _ok() {{ echo "OK $*"; }}; _warn() {{ echo "WARN $*"; }}; _err() {{ echo "ERR $*"; }}
source "{DAEMON_LIB}"
"""
    return subprocess.run(["bash", "-c", prelude + script], capture_output=True, text=True,
                          timeout=60)


class TestShellDeployFailures:
    def test_rsync_failure_propagates_with_errexit_suspended(self, tmp_path):
        bundle = tmp_path / "desktop" / "src-tauri" / "binaries" / "python-backend-aarch64-apple-darwin"
        bundle.mkdir(parents=True)
        (bundle / "python-backend").write_text("x")
        # `if !` suspends errexit exactly like prod.sh's `if ! cmd_build`
        live = tmp_path / ".swarm-ai" / "daemon"
        (live / "_internal").mkdir(parents=True)
        (live / "python-backend").write_text("OLD")
        (live / "_internal" / "lib.so").write_text("OLD")
        # fail only the bundle sync (the one with --delete), after staging worked
        r = _bash('rsync() { case " $* " in *" --delete "*) return 23;; esac; command rsync "$@"; }\n'
                  'if ! _deploy_daemon_binary; then echo "DEPLOY_FAILED"; fi', tmp_path)
        assert "DEPLOY_FAILED" in r.stdout and "rsync of the backend bundle" in r.stdout
        # staged, never in place: the live bundle is exactly as it was
        assert (live / "python-backend").read_text() == "OLD"
        assert (live / "_internal" / "lib.so").read_text() == "OLD"
        assert not (tmp_path / ".swarm-ai" / "daemon.next").exists()

    def test_successful_deploy_swaps_and_keeps_daemon_state(self, tmp_path):
        bundle = tmp_path / "desktop" / "src-tauri" / "binaries" / "python-backend-aarch64-apple-darwin"
        (bundle / "_internal").mkdir(parents=True)
        (bundle / "python-backend").write_text("NEW")
        (bundle / "_internal" / "lib.so").write_text("NEW")
        # S2b: dev tooling deploys into the versioned runtime (no tauri.conf.json in this
        # fake project -> version 0.0.0) and points runtime/current at it
        runtime = tmp_path / ".swarm-ai" / "runtime"
        live = runtime / "0.0.0"
        (live / "_internal").mkdir(parents=True)
        (live / "resources").mkdir()
        (live / "python-backend").write_text("OLD")
        (live / "_internal" / "stale.so").write_text("x")
        (live / "resources" / "default-agent.json").write_text("{}")
        import os
        for f in (live / "python-backend",):  # an older deployed build (rsync quick-check)
            os.utime(f, (1_700_000_000, 1_700_000_000))
        r = _bash('_deploy_daemon_binary && echo DEPLOY_OK', tmp_path)
        assert "DEPLOY_OK" in r.stdout, r.stdout + r.stderr
        assert (live / "python-backend").read_text() == "NEW"
        assert (live / "_internal" / "lib.so").read_text() == "NEW"
        assert not (live / "_internal" / "stale.so").exists()
        assert (live / "resources" / "default-agent.json").exists()
        assert (live / ".version").exists()
        prev = runtime / "0.0.0.prev"
        assert (prev / "python-backend").read_text() == "OLD"  # rollback copy
        assert not (runtime / "0.0.0.next").exists()
        assert (runtime / "current").read_text() == "0.0.0"

    def test_invalid_rendered_plist_never_replaces_the_live_one(self, tmp_path):
        bundle = tmp_path / "desktop" / "src-tauri" / "binaries" / "python-backend-aarch64-apple-darwin"
        bundle.mkdir(parents=True)
        (bundle / "python-backend").write_text("x")
        tpl = tmp_path / "desktop" / "resources" / "daemon" / "com.swarmai.backend.plist.template"
        tpl.parent.mkdir(parents=True)
        tpl.write_text("<plist><dict><key>Label</key>")  # truncated / invalid
        live = tmp_path / "Library" / "LaunchAgents" / "com.swarmai.backend.plist"
        live.parent.mkdir(parents=True)
        live.write_text("LIVE")
        r = _bash('if ! _deploy_daemon_binary; then echo "DEPLOY_FAILED"; fi', tmp_path)
        assert "DEPLOY_FAILED" in r.stdout and "invalid" in r.stdout
        assert live.read_text() == "LIVE"

    def test_release_checks_the_app_carries_the_backend(self):
        """S2a: the installer bundles the backend (tauri.bundle.conf.json); prod.sh no
        longer injects it after the build, it refuses an .app without it."""
        src = (REPO / "prod.sh").read_text()
        assert '"$backend_src/" "$backend_dst/"' not in src
        line = next(l for l in src.splitlines()
                    if "Contents/Resources/python-backend/python-backend" in l)
        assert line.strip().startswith("if [ -x")
        assert "aborting release\"; return 1" in src

    def test_prod_and_dev_stop_on_sync_and_deploy_failure(self):
        for script in ("prod.sh", "dev.sh"):
            src = (REPO / script).read_text()
            for line in src.splitlines():
                s = line.strip()
                if s.startswith('bash "$PROJECT_ROOT/scripts/sync-version.sh"') or \
                        s.startswith("_deploy_daemon_binary"):
                    assert "|| {" in s and "return 1" in s, f"{script}: unchecked `{s}`"


_LAUNCHCTL_STUB = """
launchctl() { echo "launchctl $*"; return 0; }
_wait_port_free() { return 0; }
_daemon_wait_healthy() { return 0; }
_bootstrap_daemon() { return 0; }
_guard_python() { return 1; }
"""


class TestDetachedLifecycleLog:
    def test_detached_op_logs_header_and_exit_code(self, tmp_path):
        script = _LAUNCHCTL_STUB + "export _SWARM_LIFECYCLE_DETACHED=1\ncmd_daemon force-restart"
        r = _bash(script, tmp_path)
        log = (tmp_path / "logs" / "daemon-lifecycle.log").read_text()
        assert "daemon force-restart (detached" in log
        assert "launchctl kill SIGKILL" in log
        assert "exit rc=0" in log
        assert "launchctl" not in r.stdout  # went to the file, not /dev/null'd stdout

    def test_detached_op_logs_real_failure_code(self, tmp_path):
        script = (_LAUNCHCTL_STUB + "_daemon_wait_healthy() { return 1; }\n"
                  "export _SWARM_LIFECYCLE_DETACHED=1\ncmd_daemon force-restart")
        _bash(script, tmp_path)
        log = (tmp_path / "logs" / "daemon-lifecycle.log").read_text()
        assert "exit rc=1" in log

    def test_inline_op_does_not_redirect(self, tmp_path):
        script = _LAUNCHCTL_STUB + "_reexec_detached_if_descendant() { return 1; }\ncmd_daemon force-restart"
        r = _bash(script, tmp_path)
        assert "launchctl kill SIGKILL" in r.stdout
        assert not (tmp_path / "logs" / "daemon-lifecycle.log").exists()


class TestExpectedKillLogFilter:
    def _record(self, msg: str, level=logging.ERROR) -> logging.LogRecord:
        return logging.LogRecord("claude_agent_sdk._internal.query", level, __file__, 1,
                                 msg, None, None)

    def test_downgrades_minus9_right_after_intentional_kill(self, monkeypatch):
        from core import session_unit as su
        monkeypatch.setattr(su, "_recent_intentional_kills", su._deque(maxlen=32))
        su._note_intentional_kill()
        rec = self._record("Fatal error in message reader: Command failed with exit code -9")
        assert su._ExpectedKillLogFilter().filter(rec) is True
        assert rec.levelno == logging.INFO and "on purpose" in rec.getMessage()

    def test_unexplained_minus9_stays_error(self, monkeypatch):
        from core import session_unit as su
        monkeypatch.setattr(su, "_recent_intentional_kills", su._deque([0.0], maxlen=32))
        rec = self._record("Fatal error in message reader: Command failed with exit code -9")
        su._ExpectedKillLogFilter().filter(rec)
        assert rec.levelno == logging.ERROR

    def test_one_kill_excuses_one_error(self, monkeypatch):
        from core import session_unit as su
        monkeypatch.setattr(su, "_recent_intentional_kills", su._deque(maxlen=32))
        su._note_intentional_kill()
        msg = "Fatal error in message reader: Command failed with exit code -9"
        first, second = self._record(msg), self._record(msg)
        su._ExpectedKillLogFilter().filter(first)
        su._ExpectedKillLogFilter().filter(second)
        assert first.levelno == logging.INFO
        assert second.levelno == logging.ERROR  # e.g. a real OOM in another session

    def test_sdk_logger_name_still_matches(self):
        # An SDK bump that moves this log must fail here, not silently re-noise.
        from claude_agent_sdk._internal import query
        assert query.logger.name == "claude_agent_sdk._internal.query"

    def test_other_errors_untouched(self, monkeypatch):
        from core import session_unit as su
        monkeypatch.setattr(su, "_recent_intentional_kills", su._deque(maxlen=32))
        su._note_intentional_kill()
        rec = self._record("Fatal error in message reader: Command failed with exit code 1")
        su._ExpectedKillLogFilter().filter(rec)
        assert rec.levelno == logging.ERROR

    def test_filter_installed_on_sdk_logger(self):
        from core import session_unit as su
        flt = logging.getLogger("claude_agent_sdk._internal.query").filters
        assert any(isinstance(f, su._ExpectedKillLogFilter) for f in flt)

    def test_force_kill_notes_the_kill_before_signalling(self):
        src = (REPO / "backend" / "core" / "session_unit.py").read_text()
        body = src[src.index("async def _force_kill(self)"):]
        assert body.index("_note_intentional_kill()") < body.index("os.killpg(pgid, signal.SIGKILL)")


class TestThrowawayInstancesSkipSweep:
    def test_main_honours_no_cli_sweep(self):
        src = (REPO / "backend" / "main.py").read_text()
        i = src.index("session_registry.kill_all_claude_processes()")
        guard = src[src.rindex("if (", 0, i):i]
        assert 'SWARMAI_VERIFY_BUILD") == "1"' in guard and 'SWARMAI_NO_CLI_SWEEP") == "1"' in guard

    def test_newuser_smoke_sets_no_cli_sweep(self):
        src = (REPO / "backend" / "scripts" / "newuser_smoke_e2e.sh").read_text()
        launch = src[src.index('env -i HOME="$TMP"'):src.index('"$BIN" --host')]
        assert "SWARMAI_NO_CLI_SWEEP=1" in launch
