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


def test_ac1_rsync_excludes_resources_anchored():
    """AC1: deploy rsync must exclude resources from --delete, ANCHORED (/resources).

    Anchoring matters: a non-anchored 'resources' would also protect a nested
    bundle dir like _internal/limits/resources (which really ships in the
    bundle), silently freezing it. The pattern MUST be '/resources'.
    """
    line = _deploy_rsync_line()
    assert re.search(r"--exclude(=|\s+)['\"]?/resources['\"]?", line), (
        f"rsync --delete must --exclude '/resources' (anchored, leading slash) so it "
        f"protects only top-level $DAEMON_DIR/resources, not nested bundle dirs. Got: {line}"
    )


def test_ac2_rsync_excludes_version_anchored():
    """AC2: deploy rsync must exclude .version from --delete, ANCHORED (/.version)."""
    line = _deploy_rsync_line()
    assert re.search(r"--exclude(=|\s+)['\"]?/\.version['\"]?", line), (
        f"rsync --delete must --exclude '/.version' (anchored). Got: {line}"
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
    src = tmp_path / "bundle"          # binary bundle — NO top-level resources/
    dst = tmp_path / "daemon"          # daemon dir — HAS resources/.version
    (src / "_internal").mkdir(parents=True)
    (src / "python-backend").write_text("new-binary")
    (src / "_internal" / "lib_new.so").write_text("new")
    # A NESTED 'resources' dir that legitimately ships in the bundle
    # (real example: _internal/limits/resources/redis/lua_scripts). The exclude
    # must be anchored so this STILL syncs — non-anchored would freeze it.
    (src / "_internal" / "limits" / "resources").mkdir(parents=True)
    (src / "_internal" / "limits" / "resources" / "script.lua").write_text("new-lua")

    (dst / "resources").mkdir(parents=True)
    (dst / "_internal").mkdir(parents=True)
    (dst / "resources" / "default-agent.json").write_text('{"behavior":"x"}')
    (dst / ".version").write_text("1.20.1 abc123 2026-06-21")
    (dst / "_internal" / "lib_stale.so").write_text("stale")  # must be pruned
    # Stale file inside a nested resources dir — must ALSO be pruned (proves the
    # exclude is anchored to top-level only, not protecting nested resources).
    (dst / "_internal" / "limits" / "resources").mkdir(parents=True)
    (dst / "_internal" / "limits" / "resources" / "stale.lua").write_text("stale-lua")

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
    # Anchoring proof: a NESTED resources dir must sync + prune normally —
    # non-anchored '--exclude resources' would freeze these (HIGH finding).
    assert (dst / "_internal" / "limits" / "resources" / "script.lua").read_text() == "new-lua", \
        "nested _internal/.../resources NOT synced — exclude is non-anchored (matches at any depth)"
    assert not (dst / "_internal" / "limits" / "resources" / "stale.lua").exists(), \
        "stale file in nested resources NOT pruned — exclude is non-anchored"


def test_ac6_upgrade_endpoint_also_protects_resources():
    """AC6: /api/system/upgrade in main.py runs the SAME destructive rsync into
    the daemon dir but NEVER re-copies resources — so it must ALSO exclude
    /resources and /.version, or it permanently wipes them. (Same bug class —
    STEERING #10: grep ALL consumers of the pattern, not just the one reported.)
    """
    main_py = PROJECT_ROOT / "backend" / "main.py"
    text = main_py.read_text(encoding="utf-8")
    # The rsync call is a Python list literal that may span multiple physical
    # lines. Match the whole subprocess.run([...]) list that contains "rsync"
    # and --delete and daemon_dir, then assert the excludes are inside it.
    spans = re.findall(
        r'\[\s*"rsync".*?daemon_dir\s*\+\s*"/"\s*\]',
        text, flags=re.DOTALL,
    )
    delete_spans = [re.sub(r"\s+", " ", s) for s in spans if "--delete" in s]
    assert delete_spans, "Could not find the upgrade-endpoint rsync --delete list in main.py"
    for span in delete_spans:
        assert '"/resources"' in span and '"/.version"' in span, (
            f"upgrade-endpoint rsync must exclude '/resources' and '/.version' "
            f"(it never re-copies resources → permanent wipe). Got: {span}"
        )


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
