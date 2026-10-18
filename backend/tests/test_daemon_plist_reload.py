"""Daemon launchd job definition: priority + reload-on-change.

What is tested:
- Both backend plist copies (the desktop bundle template and backend/channels,
  used by /api/system/upgrade + install_backend_daemon.py) run the daemon at
  ``ProcessType=Standard``; the guardian stays ``Background``.
- ``_deploy_daemon_binary`` (scripts/daemon-lib.sh) reports whether the rendered
  plist changed via ``_DAEMON_PLIST_CHANGED``.
- ``_daemon_restart_after_deploy`` reloads the job (``cmd_daemon reload``) when
  the plist changed OR the loaded job still runs at background priority, and
  otherwise keeps the SIGKILL + KeepAlive binary swap.

Methodology: the shell functions run for real in ``bash`` with a temp HOME and
stubbed ``launchctl`` / ``plutil`` / ``cmd_daemon`` so nothing touches launchd.

Why: ``ProcessType=Background`` put the daemon and all its children at Darwin
priority 4 (efficiency cores, throttled I/O); under foreground load the loop and
the heartbeat thread starved for 20s+ and the app showed a false offline. launchd
only re-reads a plist on bootstrap, so a SIGKILL restart would never apply it.
"""
from __future__ import annotations

import plistlib
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
DAEMON_LIB = REPO / "scripts" / "daemon-lib.sh"
TEMPLATE = REPO / "desktop" / "resources" / "daemon" / "com.swarmai.backend.plist.template"
CHANNELS_PLIST = REPO / "backend" / "channels" / "com.swarmai.backend.plist"
GUARDIAN_PLISTS = [
    REPO / "backend" / "channels" / "com.swarmai.guardian.plist",
    REPO / "desktop" / "resources" / "daemon" / "com.swarmai.guardian.plist.template",
]


def _load(path: Path) -> dict:
    text = path.read_text()
    for ph, val in (("__WRAPPER_PATH__", "/tmp/w.sh"), ("__LOG_DIR__", "/tmp/logs"),
                    ("__HOME__", "/tmp/home")):
        text = text.replace(ph, val)
    return plistlib.loads(text.encode())


class TestPlistPriority:
    def test_both_backend_plists_are_standard(self):
        for p in (TEMPLATE, CHANNELS_PLIST):
            assert _load(p).get("ProcessType") == "Standard", f"{p} must use Standard"

    def test_guardian_stays_background(self):
        for p in GUARDIAN_PLISTS:
            assert _load(p).get("ProcessType") == "Background", f"{p} must stay Background"


def _run(script: str, home: Path) -> str:
    """Source daemon-lib.sh with a temp HOME and run ``script``; return stdout."""
    prelude = f"""
export HOME="{home}"
PROJECT_ROOT="{home}/repo"; DESKTOP_DIR="{home}/desktop"; LOG_DIR="{home}/logs"
_log() {{ :; }}; _ok() {{ :; }}; _warn() {{ :; }}; _err() {{ :; }}
source "{DAEMON_LIB}"
"""
    r = subprocess.run(["bash", "-c", prelude + script], capture_output=True, text=True,
                       timeout=60)
    assert r.returncode == 0, r.stderr
    return r.stdout


def _fake_bundle(home: Path, process_type: str) -> None:
    bundle = home / "desktop" / "src-tauri" / "binaries" / "python-backend-aarch64-apple-darwin"
    bundle.mkdir(parents=True, exist_ok=True)
    (bundle / "python-backend").write_text("#!/bin/sh\n")
    daemon_res = home / "desktop" / "resources" / "daemon"
    daemon_res.mkdir(parents=True, exist_ok=True)
    tpl = TEMPLATE.read_text().replace("<string>Standard</string>",
                                       f"<string>{process_type}</string>")
    (daemon_res / "com.swarmai.backend.plist.template").write_text(tpl)


class TestDeployReportsPlistChange:
    def test_first_deploy_changed_then_unchanged_then_changed(self, tmp_path):
        _fake_bundle(tmp_path, "Standard")
        out = _run('_deploy_daemon_binary >/dev/null; echo "C=$_DAEMON_PLIST_CHANGED"', tmp_path)
        assert "C=1" in out  # no plist yet
        out = _run('_deploy_daemon_binary >/dev/null; echo "C=$_DAEMON_PLIST_CHANGED"', tmp_path)
        assert "C=0" in out  # identical render
        _fake_bundle(tmp_path, "Interactive")
        out = _run('_deploy_daemon_binary >/dev/null; echo "C=$_DAEMON_PLIST_CHANGED"', tmp_path)
        assert "C=1" in out  # job definition changed
        plist = tmp_path / "Library" / "LaunchAgents" / "com.swarmai.backend.plist"
        assert "<string>Interactive</string>" in plist.read_text()
        assert "__HOME__" not in plist.read_text()


_STUBS = """
launchctl() {
  if [ "$1" = print ]; then echo "${FAKE_SPAWN:-spawn type = daemon (3)}"; return 0; fi
  echo "launchctl $*"
}
plutil() { echo "${FAKE_WANT:-Standard}"; }
cmd_daemon() { echo "cmd_daemon $*"; }
_daemon_wait_healthy() { echo "wait $1"; }
mkdir -p "$HOME/Library/LaunchAgents"; touch "$HOME/Library/LaunchAgents/com.swarmai.backend.plist"
"""


class TestRestartAfterDeploy:
    def test_unchanged_plist_and_fresh_job_keeps_sigkill_swap(self, tmp_path):
        out = _run(_STUBS + "_DAEMON_PLIST_CHANGED=0; _daemon_restart_after_deploy 30", tmp_path)
        assert "launchctl kill SIGKILL" in out and "wait 30" in out
        assert "cmd_daemon reload" not in out

    def test_changed_plist_reloads_job(self, tmp_path):
        out = _run(_STUBS + "_DAEMON_PLIST_CHANGED=1; _daemon_restart_after_deploy 30", tmp_path)
        assert "cmd_daemon reload" in out
        assert "SIGKILL" not in out

    def test_loaded_background_job_with_standard_plist_reloads(self, tmp_path):
        script = (_STUBS + 'FAKE_SPAWN="spawn type = background (5)"; '
                  "_DAEMON_PLIST_CHANGED=0; _daemon_restart_after_deploy 30")
        assert "cmd_daemon reload" in _run(script, tmp_path)

    def test_detached_reload_waits_for_new_process(self, tmp_path):
        # From inside the daemon's tree cmd_daemon reload re-execs detached and
        # returns at once; the caller must wait for a NEW boot_id, not report on
        # (or smoke-test) the old daemon.
        script = (_STUBS + 'cmd_daemon() { _DAEMON_OP_DETACHED=1; echo "cmd_daemon $*"; }\n'
                  '_daemon_health_line() { echo "healthy OLDBOOT"; }\n'
                  '_daemon_wait_restarted() { echo "wait_restarted $1 $2"; }\n'
                  "_DAEMON_PLIST_CHANGED=1; _daemon_restart_after_deploy 30")
        out = _run(script, tmp_path)
        assert "cmd_daemon reload" in out and "wait_restarted OLDBOOT 150" in out

    def test_wait_restarted_requires_new_boot_id(self, tmp_path):
        # _daemon_health_line runs in a $(...) subshell → count calls in a file.
        script = ('C="$HOME/calls"; : > "$C"\n'
                  '_daemon_health_line() { echo x >> "$C"; '
                  'if [ "$(wc -l < "$C")" -lt 3 ]; then echo "healthy OLD"; else echo "healthy NEW"; fi; }\n'
                  "sleep() { :; }\n"
                  '_daemon_wait_restarted OLD 10 && echo "RC=0 calls=$(wc -l < "$C" | tr -d " ")"')
        out = _run(script, tmp_path)
        assert "RC=0 calls=3" in out

    def test_wait_restarted_times_out_on_same_process(self, tmp_path):
        script = ('_daemon_health_line() { echo "healthy OLD"; }\nsleep() { :; }\n'
                  '_daemon_wait_restarted OLD 5 || echo "RC=fail"')
        assert "RC=fail" in _run(script, tmp_path)

    def test_background_plist_and_background_job_is_not_stale(self, tmp_path):
        script = (_STUBS + 'FAKE_SPAWN="spawn type = background (5)"; FAKE_WANT=Background; '
                  "_DAEMON_PLIST_CHANGED=0; _daemon_restart_after_deploy 30")
        assert "cmd_daemon reload" not in _run(script, tmp_path)


class TestReloadSubcommand:
    def test_reload_is_c034_guarded_and_sentinel_precedes_bootout(self):
        src = DAEMON_LIB.read_text()
        assert "restart|force-restart|stop|reload)" in src
        body = src[src.index("        reload)"):src.index("        stop)")]
        assert body.index("write-sentinel") < body.index("launchctl bootout")
        assert "_bootstrap_daemon" in body
        # only OUR sentinel is cleared — never one a concurrent stop/upgrade wrote
        assert '"written_by": "reload"' in body

    def test_deploy_paths_use_shared_restart(self):
        for script in ("prod.sh", "dev.sh"):
            src = (REPO / script).read_text()
            assert "_daemon_restart_after_deploy" in src, f"{script} must use the shared restart"
