"""Tests for daemon_guard — C034 defense-in-depth core logic.

Covers the testable judgment logic that keeps the daemon lifecycle robust:
  AC1: ancestry detection + detached re-exec relay
  AC2: guardian re-bootstraps when deregistered + no sentinel + port dead N
  AC3: guardian SKIPS when a fresh sentinel is present (COE-2026-05-01 guard)
  AC4: sentinel write/read/clear roundtrip + ordering invariants in callers
  AC5: deployed_no_restart scan (fresh returned, stale ignored)
  AC6: guardian plist validates; restart path still SIGKILL (no regression)
  AC7: stale sentinel + port dead → recover (and clear sentinel)

All launchctl/ps/socket interactions are mocked — no real launchd interaction.
"""

from __future__ import annotations

import json
import plistlib
import time
from pathlib import Path
from unittest.mock import patch


from core import daemon_guard as dg


# ---------------------------------------------------------------------------
# AC1: ancestry walk + detached re-exec
# ---------------------------------------------------------------------------

class TestAncestryDetection:
    def test_descendant_detected_when_daemon_in_ppid_chain(self):
        # chain: self(100) → 200 → 300 → daemon(999) → 1
        chain = [100, 200, 300, 999, 1]
        with patch.object(dg, "build_ancestry", return_value=chain):
            assert dg.is_daemon_descendant(daemon_pid=999) is True

    def test_not_descendant_when_daemon_absent_from_chain(self):
        chain = [100, 200, 1]  # daemon 999 not present (we left its pgid AND tree)
        with patch.object(dg, "build_ancestry", return_value=chain):
            assert dg.is_daemon_descendant(daemon_pid=999) is False

    def test_fail_closed_when_daemon_pid_unresolvable(self):
        # SEC-HIGH-7: pid unresolvable = exactly when C034 is likeliest
        # (flaky launchctl). FAIL-CLOSED → treat as descendant → re-exec
        # detached (the safe path). A false positive is harmless.
        with patch.object(dg, "read_daemon_pid", return_value=None):
            assert dg.is_daemon_descendant() is True

    def test_build_ancestry_includes_self_and_caps_hops(self):
        # Each pid's parent is pid-1; with cap=3 we get exactly 3 entries.
        def fake_parent(pid):
            return pid - 1 if pid > 1 else None
        with patch.object(dg, "_parent_pid", side_effect=fake_parent):
            chain = dg.build_ancestry(start_pid=50, max_hops=3)
        assert chain == [50, 49, 48]

    def test_build_ancestry_stops_on_cycle(self):
        # Pathological: parent points back into the chain → must not loop forever.
        mapping = {50: 40, 40: 50}
        with patch.object(dg, "_parent_pid", side_effect=lambda p: mapping.get(p)):
            chain = dg.build_ancestry(start_pid=50, max_hops=12)
        assert chain == [50, 40]  # stops when 50 reappears

    def test_reexec_detached_spawns_new_session_fire_and_forget(self):
        # The C034 prevention primitive: detach into a new session group so the
        # re-execed op survives the daemon's death. Fire-and-forget — NO result
        # relay (the caller is reaped before a daemon-killing op completes, so
        # there's nobody to read a /tmp file; outcome is observed out-of-band).
        with patch.object(dg.subprocess, "Popen") as mock_popen:
            ret = dg.reexec_detached(["echo", "hi"], op="stop")
        assert ret is None  # fire-and-forget returns nothing
        _, kwargs = mock_popen.call_args
        assert kwargs.get("start_new_session") is True
        assert kwargs["env"]["_SWARM_LIFECYCLE_DETACHED"] == "1"
        assert "SWARM_LIFECYCLE_RESULT_FILE" not in kwargs["env"]

    def test_reexec_if_descendant_cli_reexecs_when_descendant(self, monkeypatch):
        monkeypatch.delenv("_SWARM_LIFECYCLE_DETACHED", raising=False)
        with patch.object(dg, "is_daemon_descendant", return_value=True), \
             patch.object(dg, "reexec_detached", return_value=None) as mock_re:
            rc = dg._main(["reexec-if-descendant", "/bin/bash", "script.sh", "daemon", "stop"])
        assert rc == 0  # 0 = re-execed, caller must STOP
        mock_re.assert_called_once()
        assert mock_re.call_args[0][0] == ["/bin/bash", "script.sh", "daemon", "stop"]

    def test_reexec_if_descendant_cli_inlines_when_not_descendant(self, monkeypatch):
        monkeypatch.delenv("_SWARM_LIFECYCLE_DETACHED", raising=False)
        with patch.object(dg, "is_daemon_descendant", return_value=False):
            rc = dg._main(["reexec-if-descendant", "/bin/bash", "script.sh", "daemon", "stop"])
        assert rc == 1

    def test_reexec_if_descendant_cli_inlines_when_already_detached(self, monkeypatch):
        monkeypatch.setenv("_SWARM_LIFECYCLE_DETACHED", "1")
        with patch.object(dg, "is_daemon_descendant", return_value=True):
            rc = dg._main(["reexec-if-descendant", "/bin/bash", "script.sh", "daemon", "stop"])
        assert rc == 1  # already detached → inline, no recursion


# ---------------------------------------------------------------------------
# AC2 + AC3 + AC7: guardian decision (pure function)
# ---------------------------------------------------------------------------

class TestGuardianDecision:
    def test_skip_when_registered(self):
        d = dg.guardian_decision(registered=True, port_alive=False,
                                 dead_probe_count=5, required_dead_probes=3,
                                 sentinel=None)
        assert d["action"] == dg.SKIP

    def test_skip_when_port_alive(self):
        d = dg.guardian_decision(registered=False, port_alive=True,
                                 dead_probe_count=5, required_dead_probes=3,
                                 sentinel=None)
        assert d["action"] == dg.SKIP

    def test_skip_when_not_enough_dead_probes(self):
        # AC2 boundary: avoid racing a normal SIGKILL+KeepAlive restart.
        d = dg.guardian_decision(registered=False, port_alive=False,
                                 dead_probe_count=2, required_dead_probes=3,
                                 sentinel=None)
        assert d["action"] == dg.SKIP

    def test_bootstrap_when_deregistered_no_sentinel_port_dead(self):
        # AC2: the accidental-down case → recover.
        d = dg.guardian_decision(registered=False, port_alive=False,
                                 dead_probe_count=3, required_dead_probes=3,
                                 sentinel=None)
        assert d["action"] == dg.SHOULD_BOOTSTRAP
        assert d["clear_sentinel"] is False

    def test_skip_when_fresh_sentinel_present(self):
        # AC3: intentional stop/upgrade → must NOT resurrect (COE-2026-05-01).
        fresh = {"written_by": "upgrade", "written_at": time.time()}
        d = dg.guardian_decision(registered=False, port_alive=False,
                                 dead_probe_count=10, required_dead_probes=3,
                                 sentinel=fresh)
        assert d["action"] == dg.SKIP

    def test_bootstrap_when_sentinel_stale(self):
        # AC7: crash during stop/upgrade must not suppress recovery forever.
        stale = {"written_by": "upgrade", "written_at": time.time() - (40 * 60)}
        d = dg.guardian_decision(registered=False, port_alive=False,
                                 dead_probe_count=3, required_dead_probes=3,
                                 sentinel=stale)
        assert d["action"] == dg.SHOULD_BOOTSTRAP
        assert d["clear_sentinel"] is True

    def test_sentinel_without_timestamp_treated_stale(self):
        d = dg.guardian_decision(registered=False, port_alive=False,
                                 dead_probe_count=3, required_dead_probes=3,
                                 sentinel={"written_by": "?"})
        assert d["action"] == dg.SHOULD_BOOTSTRAP
        assert d["clear_sentinel"] is True

    def test_stale_window_bounds_outage_to_5min(self):
        # SEC-MED-2: 30min stale window = 30min worst-case outage. Local rsync
        # completes in seconds → 5min ceiling.
        assert dg.SENTINEL_STALE_SECONDS == 5 * 60
        just_stale = {"written_by": "upgrade", "written_at": time.time() - (5 * 60 + 1)}
        assert dg.guardian_decision(registered=False, port_alive=False,
                                    dead_probe_count=3, required_dead_probes=3,
                                    sentinel=just_stale)["action"] == dg.SHOULD_BOOTSTRAP
        fresh = {"written_by": "upgrade", "written_at": time.time() - (4 * 60)}
        assert dg.guardian_decision(registered=False, port_alive=False,
                                    dead_probe_count=3, required_dead_probes=3,
                                    sentinel=fresh)["action"] == dg.SKIP

    def test_permanent_sentinel_never_resurrects_stopped_daemon(self):
        # A `stop` writes permanent=True. Even days later the guardian must NOT
        # restart it — that would defeat `stop`.
        old_permanent = {"written_by": "stop", "permanent": True,
                         "written_at": time.time() - (10 * 24 * 3600)}
        d = dg.guardian_decision(registered=False, port_alive=False,
                                 dead_probe_count=99, required_dead_probes=3,
                                 sentinel=old_permanent)
        assert d["action"] == dg.SKIP
        assert not dg.sentinel_is_stale(old_permanent)

    def test_future_dated_sentinel_treated_stale(self):
        # CORR-LOW: clock moved backward / bogus future timestamp must not
        # suppress recovery of a down daemon indefinitely.
        future = {"written_by": "upgrade", "written_at": time.time() + 3600}
        assert dg.sentinel_is_stale(future) is True


# ---------------------------------------------------------------------------
# AC4: sentinel roundtrip + caller ordering invariants
# ---------------------------------------------------------------------------

class TestSentinel:
    def test_write_read_clear_roundtrip(self, tmp_path):
        sentinel = tmp_path / ".daemon-intentional-down"
        dg.write_sentinel("stopping", "stop", sentinel_path=sentinel)
        data = dg.read_sentinel(sentinel_path=sentinel)
        assert data["reason"] == "stopping"
        assert data["written_by"] == "stop"
        assert "written_at" in data
        dg.clear_sentinel(sentinel_path=sentinel)
        assert dg.read_sentinel(sentinel_path=sentinel) is None

    def test_clear_missing_sentinel_is_noop(self, tmp_path):
        dg.clear_sentinel(sentinel_path=tmp_path / "nonexistent")  # no raise

    def test_read_corrupt_sentinel_returns_none(self, tmp_path):
        sentinel = tmp_path / ".daemon-intentional-down"
        sentinel.write_text("{not valid json")
        assert dg.read_sentinel(sentinel_path=sentinel) is None

    def test_stop_path_writes_sentinel_before_bootout(self):
        """AC4: daemon-lib.sh stop must write the sentinel BEFORE bootout."""
        src = (Path(__file__).parent.parent.parent / "scripts" / "daemon-lib.sh").read_text()
        # Locate the stop) case body.
        assert "write-sentinel" in src, "stop path must write sentinel via daemon_guard"
        stop_idx = src.index("stop)")
        bootout_idx = src.index("launchctl bootout", stop_idx)
        sentinel_idx = src.index("write-sentinel", stop_idx)
        assert sentinel_idx < bootout_idx, "sentinel must be written BEFORE bootout in stop path"

    def test_upgrade_writes_sentinel_before_bootout(self):
        """AC4: /api/system/upgrade must write the sentinel before bootout."""
        src = (Path(__file__).parent.parent / "main.py").read_text()
        assert "daemon-intentional-down" in src or "write_sentinel" in src, \
            "upgrade path must write sentinel"
        # SIGKILL+bootout block must be preceded by sentinel write.
        sentinel_idx = src.find("intentional-down")
        if sentinel_idx == -1:
            sentinel_idx = src.find("write_sentinel")
        bootout_idx = src.index('"bootout"')
        assert sentinel_idx != -1 and sentinel_idx < bootout_idx, \
            "sentinel must be written before bootout in upgrade path"

    def test_main_sentinel_path_matches_daemon_guard(self):
        """CQ-MED: main.py hardcodes the sentinel path in an f-string (it can't
        import the constant — injected into a detached interpreter). If the
        literal drifts from daemon_guard.SENTINEL_PATH, the upgrader writes a
        sentinel the guardian never reads → guardian races the rsync →
        COE-2026-05-01 corruption. Guard the equality."""
        src = (Path(__file__).parent.parent / "main.py").read_text()
        expected_tail = ".swarm-ai/.daemon-intentional-down"
        assert str(dg.SENTINEL_PATH).endswith(expected_tail)
        assert expected_tail in src, \
            "main.py sentinel literal drifted from daemon_guard.SENTINEL_PATH"

    def test_stop_writes_permanent_sentinel(self):
        """Stop must write a PERMANENT sentinel so the stale-guard never
        resurrects a deliberately-stopped daemon."""
        src = (Path(__file__).parent.parent.parent / "scripts" / "daemon-lib.sh").read_text()
        stop_idx = src.index("stop)")
        sentinel_line_idx = src.index("write-sentinel", stop_idx)
        # the permanent flag must be on the stop write-sentinel invocation
        line_end = src.index("\n", sentinel_line_idx)
        assert "permanent" in src[sentinel_line_idx:line_end], \
            "stop's write-sentinel must pass 'permanent'"


# ---------------------------------------------------------------------------
# AC5: deployed_no_restart observability scan
# ---------------------------------------------------------------------------

class TestDeployedNoRestartScan:
    def test_fresh_deployed_no_restart_returned(self, tmp_path):
        f = tmp_path / "swarm-upgrade-abc123.json"
        f.write_text(json.dumps({"status": "deployed_no_restart", "version": "1.2.3"}))
        findings = dg.scan_deployed_no_restart(result_dir=tmp_path)
        assert len(findings) == 1
        assert findings[0]["version"] == "1.2.3"
        assert findings[0]["file"].endswith("swarm-upgrade-abc123.json")

    def test_success_status_ignored(self, tmp_path):
        f = tmp_path / "swarm-upgrade-ok.json"
        f.write_text(json.dumps({"status": "success", "version": "1.2.3"}))
        assert dg.scan_deployed_no_restart(result_dir=tmp_path) == []

    def test_stale_result_ignored_and_cleaned_up(self, tmp_path):
        import os as _os
        f = tmp_path / "swarm-upgrade-old.json"
        f.write_text(json.dumps({"status": "deployed_no_restart"}))
        old = time.time() - (48 * 60 * 60)  # 48h ago
        _os.utime(f, (old, old))
        assert dg.scan_deployed_no_restart(result_dir=tmp_path) == []
        # Stale result files must be unlinked, not left to accumulate forever.
        assert not f.exists(), "stale upgrade result file should be cleaned up"

    def test_corrupt_result_skipped(self, tmp_path):
        (tmp_path / "swarm-upgrade-bad.json").write_text("{broken")
        assert dg.scan_deployed_no_restart(result_dir=tmp_path) == []

    def test_no_results_dir_returns_empty(self, tmp_path):
        assert dg.scan_deployed_no_restart(result_dir=tmp_path / "missing") == []


# ---------------------------------------------------------------------------
# AC6: guardian plist + no regression to restart SIGKILL path
# ---------------------------------------------------------------------------

class TestGuardianPlistAndNoRegression:
    PLIST = Path(__file__).parent.parent / "channels" / "com.swarmai.guardian.plist"

    def test_guardian_plist_valid_and_has_poll_keys(self):
        assert self.PLIST.exists(), "guardian plist must exist"
        data = plistlib.loads(self.PLIST.read_text().encode())
        assert data["Label"] == "com.swarmai.guardian"
        # Frequent polling (StartInterval), NOT once-daily StartCalendarInterval.
        assert "StartInterval" in data, "guardian must poll on an interval"
        assert data["StartInterval"] <= 60
        assert data.get("RunAtLoad") is True  # catch a dead daemon on login

    def test_restart_path_still_uses_sigkill(self):
        """No regression: same-binary restart must stay SIGKILL+KeepAlive."""
        src = (Path(__file__).parent.parent.parent / "scripts" / "daemon-lib.sh").read_text()
        restart_idx = src.index("restart)")
        # within the restart case, before force-restart, SIGKILL must appear
        force_idx = src.index("force-restart)")
        restart_body = src[restart_idx:force_idx]
        assert "SIGKILL" in restart_body, "restart must keep SIGKILL (service stays registered)"

    def test_guardian_script_exists_and_references_guard(self):
        script = Path(__file__).parent.parent / "channels" / "swarmai_guardian.sh"
        assert script.exists(), "guardian loop script must exist"
        body = script.read_text()
        assert "guardian-decision" in body, "guardian script must consult daemon_guard"
        assert "bootstrap" in body, "guardian script must be able to bootstrap"

    def test_guardian_script_uses_standalone_guard_not_module(self):
        # OPS-HIGH: end-user .app has no repo → `python -m core.daemon_guard`
        # can't resolve. The guardian must run the standalone copied script.
        body = (Path(__file__).parent.parent / "channels" / "swarmai_guardian.sh").read_text()
        assert "-m core.daemon_guard" not in body, \
            "guardian must NOT use `-m core.daemon_guard` (fails for .app installs)"
        assert "guardian/daemon_guard.py" in body, \
            "guardian must run the standalone stdlib guard script"

    def test_guardian_script_has_overlap_lock_and_path(self):
        body = (Path(__file__).parent.parent / "channels" / "swarmai_guardian.sh").read_text()
        assert "flock" in body, "guardian must use flock to prevent overlapping runs"
        assert "/opt/homebrew/bin" in body, "guardian must set a PATH (launchd strips it)"

    def test_guardian_rechecks_sentinel_before_bootstrap(self):
        # SEC-LOW-8 TOCTOU: a stop/upgrade may write the sentinel during the
        # bootstrap loop — guardian must re-read it, not rely on stale decision.
        body = (Path(__file__).parent.parent / "channels" / "swarmai_guardian.sh").read_text()
        loop_idx = body.index("for attempt in")
        assert body.index("SENTINEL_FILE", loop_idx) > loop_idx, \
            "guardian must re-check sentinel inside the bootstrap loop"

    def test_install_copies_standalone_guard_script(self):
        src = (Path(__file__).parent.parent / "channels" / "install_backend_daemon.py").read_text()
        assert "GUARDIAN_GUARD_PY_DEST" in src and "daemon_guard.py" in src, \
            "install_guardian must copy the standalone daemon_guard.py"
        assert "GUARDIAN_BACKEND_DIR_FILE" not in src, \
            "obsolete backend_dir mechanism must be removed"

    def test_uninstall_hardens_guardian_removal(self):
        # PE review: a git-conflict revert silently dropped the uninstall
        # hardening, and NO test covered it — so the regression was invisible.
        # Guard the hardening: (1) rc-checked guardian bootout with SIGKILL
        # fallback, (2) guardian plist unlinked BEFORE the backend bootout (so a
        # mid-flight guardian hits its missing-plist guard and can't resurrect
        # the backend being removed).
        src = (Path(__file__).parent.parent / "channels" / "install_backend_daemon.py").read_text()
        uninstall_idx = src.index("def uninstall(")
        next_def = src.index("\ndef ", uninstall_idx + 1)
        body = src[uninstall_idx:next_def]
        assert "returncode not in" in body, \
            "uninstall must check the guardian bootout return code"
        assert "SIGKILL" in body, \
            "uninstall must SIGKILL the guardian as a fallback if bootout fails"
        # Ordering: guardian plist unlink must precede the backend bootout.
        guardian_unlink_idx = body.index("guardian_plist.unlink")
        backend_bootout_idx = body.index("DAEMON_LABEL")
        assert guardian_unlink_idx < backend_bootout_idx, \
            "guardian plist must be unlinked before the backend is booted out"
