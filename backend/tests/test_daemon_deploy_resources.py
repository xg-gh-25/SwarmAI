"""Tests for the deploy-window resource race fix in scripts/daemon-lib.sh.

Bug (run_5322e196): ``_deploy_daemon_binary`` ran ``rsync -a --delete`` from the
binary bundle (which has no ``resources/``) into ``$DAEMON_DIR``. ``--delete``
wiped ``$DAEMON_DIR/resources`` and ``$DAEMON_DIR/.version`` ~50s before they
were re-copied. A daemon restarted by launchd KeepAlive inside that window logged
``default-agent.json`` not-found errors and silently fell back to DB defaults
(PIT69 silent degradation).

Fix: exclude ``resources`` and ``.version`` from the destructive ``--delete`` so
they are never momentarily absent, regardless of restart timing.

Acceptance criteria:
  AC1: rsync line excludes ``resources`` from --delete
  AC2: rsync line excludes ``.version`` from --delete
  AC3: stale files under _internal are STILL pruned (exclude is surgical)
  AC5: execution test — real rsync proves resources/.version survive a
       bundle-without-resources sync while binary + _internal still sync

Methodology: contract test (grep the actual shell line) + execution test
(run real rsync, assert filesystem outcome). The execution test is mandatory
per STEERING #11 (recovery/edge paths need a test that forces execution).
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent.parent
DAEMON_LIB = PROJECT_ROOT / "scripts" / "daemon-lib.sh"


def _deploy_rsync_line() -> str:
    """Return the rsync --delete deploy command from _deploy_daemon_binary.

    Joins shell line-continuations (``\\`` at EOL) so the matcher works whether
    the command is on one line or wrapped across several.
    """
    text = DAEMON_LIB.read_text(encoding="utf-8")
    # Collapse backslash-newline continuations into single logical lines.
    joined = re.sub(r"\\\n\s*", " ", text)
    # The destructive deploy sync: rsync ... --delete ... "$BACKEND_BUNDLE_DIR/" "$DAEMON_DIR/"
    for line in joined.splitlines():
        stripped = line.strip()
        if stripped.startswith("rsync") and "--delete" in stripped and "BACKEND_BUNDLE_DIR" in stripped:
            return re.sub(r"\s+", " ", stripped)
    raise AssertionError("Could not find the deploy rsync --delete line in daemon-lib.sh")


# ---------------------------------------------------------------------------
# Contract tests — the shell command must protect resources + .version
# ---------------------------------------------------------------------------

def test_daemon_lib_exists():
    assert DAEMON_LIB.is_file(), f"daemon-lib.sh not found at {DAEMON_LIB}"


def test_ac1_rsync_excludes_resources():
    """AC1: the deploy rsync must exclude 'resources' from --delete."""
    line = _deploy_rsync_line()
    assert re.search(r"--exclude(=|\s+)['\"]?resources['\"]?", line), (
        f"rsync --delete must --exclude resources to avoid the deploy-window wipe. Got: {line}"
    )


def test_ac2_rsync_excludes_version():
    """AC2: the deploy rsync must exclude '.version' from --delete."""
    line = _deploy_rsync_line()
    assert re.search(r"--exclude(=|\s+)['\"]?\.version['\"]?", line), (
        f"rsync --delete must --exclude .version (rewritten separately). Got: {line}"
    )


# ---------------------------------------------------------------------------
# AC5: Execution test — run real rsync, prove the outcome
# ---------------------------------------------------------------------------

@pytest.mark.skipif(shutil.which("rsync") is None, reason="rsync not available")
def test_ac5_execution_resources_survive_real_rsync(tmp_path):
    """Force the EXACT rsync flags from daemon-lib.sh through real rsync and
    assert: resources + .version survive, binary syncs, stale _internal pruned.

    This reproduces the deploy and proves the fix on the real (possibly
    openrsync on macOS) binary — not the man page (P1: verify, don't infer).
    """
    src = tmp_path / "bundle"          # binary bundle — NO resources/
    dst = tmp_path / "daemon"          # daemon dir — HAS resources/.version
    (src / "_internal").mkdir(parents=True)
    (src / "python-backend").write_text("new-binary")
    (src / "_internal" / "lib_new.so").write_text("new")

    (dst / "resources").mkdir(parents=True)
    (dst / "_internal").mkdir(parents=True)
    (dst / "resources" / "default-agent.json").write_text('{"behavior":"x"}')
    (dst / ".version").write_text("1.20.1 abc123 2026-06-21")
    (dst / "_internal" / "lib_stale.so").write_text("stale")  # must be pruned

    # Extract the actual flags the shell uses, so the test tracks the source.
    line = _deploy_rsync_line()
    excludes = re.findall(r"--exclude(?:=|\s+)['\"]?([^'\"\s]+)['\"]?", line)
    cmd = ["rsync", "-a", "--delete"]
    for ex in excludes:
        cmd.append(f"--exclude={ex}")
    cmd += [f"{src}/", f"{dst}/"]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, f"rsync failed: {result.stderr}"

    # The fix's guarantees:
    assert (dst / "resources" / "default-agent.json").is_file(), \
        "resources/ was wiped by --delete — the deploy-window race is NOT fixed"
    assert (dst / ".version").is_file(), ".version was wiped by --delete"
    # The deploy must still work:
    assert (dst / "python-backend").read_text() == "new-binary", "binary not synced"
    assert (dst / "_internal" / "lib_new.so").is_file(), "new _internal not synced"
    # --delete must still prune stale bundle files (exclude is surgical):
    assert not (dst / "_internal" / "lib_stale.so").exists(), \
        "stale _internal file not pruned — exclude is too broad"


@pytest.mark.skipif(shutil.which("rsync") is None, reason="rsync not available")
def test_ac5_negative_bare_delete_wipes_resources(tmp_path):
    """Control: the OLD command (bare --delete, no excludes) WIPES resources.

    Proves the test would have caught the bug (RED on pre-fix command).
    """
    src = tmp_path / "bundle"
    dst = tmp_path / "daemon"
    src.mkdir()
    (src / "python-backend").write_text("bin")
    (dst / "resources").mkdir(parents=True)
    (dst / "resources" / "default-agent.json").write_text("{}")

    subprocess.run(
        ["rsync", "-a", "--delete", f"{src}/", f"{dst}/"],
        capture_output=True, text=True, timeout=30,
    )
    assert not (dst / "resources").exists(), (
        "Expected bare --delete to wipe resources (the bug). If this fails, "
        "the platform rsync has different --delete semantics — re-examine the fix."
    )
