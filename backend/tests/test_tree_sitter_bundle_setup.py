"""The tree-sitter grammars are put on disk by every path that sets up a backend from
source, outside the server process, and a failure there only warns.

core.code_intel.grammars never downloads a grammar inside the backend (the library's
download holds the GIL and stops every thread). So the grammars come from
scripts/bundle_tree_sitter_grammars.py, run by:
- the developer start scripts (dev.sh, desktop/scripts/dev-with-backend.sh, which
  `npm run tauri:dev` runs), after the Python dependencies and before the server starts;
- a Hive's first boot (hive/user_data.py, both the full and the prebaked-AMI script,
  and the manual hive/setup.sh) and every update (the provisioner's SSM update script
  and the manual hive/update-hive.sh), after the dependencies and before the restart,
  with the update's file sync keeping the directory it wrote.

Questions: does each path run the script in that order, does a failure of the script
only warn (the backend works without grammars: code intel takes its regex path), and do
the rendered Hive scripts still parse? The build path (desktop/scripts/build-backend.sh)
is held by tests/test_tree_sitter_grammars.py.
"""
from __future__ import annotations

import asyncio
import os
import re
import shutil
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hive.provisioner import UPDATE_WAIT_INTERVAL_S, UPDATE_WAIT_POLLS
from hive.user_data import TREE_SITTER_GRAMMARS_SH, TREE_SITTER_SETUP_DEADLINE_S, render_user_data

REPO = Path(__file__).resolve().parents[2]
BUNDLE = "scripts/bundle_tree_sitter_grammars.py generated/tree_sitter_grammars"

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="no bash")


def _logical_lines(text: str) -> list[str]:
    """Shell lines with backslash continuations joined."""
    return re.sub(r"\\\n\s*", " ", text).splitlines()


def _bundle_line(text: str) -> str:
    lines = [ln for ln in _logical_lines(text)
             if BUNDLE in ln and not ln.lstrip().startswith("#")]
    assert len(lines) == 1, lines
    return lines[0]


# (file, what must come before the bundle, what must come after it)
_SETUP_PATHS = [
    ("dev.sh", "uv sync --group dev", "python main.py"),
    ("desktop/scripts/dev-with-backend.sh", "source .venv/bin/activate", "python main.py"),
    ("hive/setup.sh", ".venv/bin/pip install --quiet -e", "systemctl"),
    ("hive/update-hive.sh", ".venv/bin/pip install -q -e", "systemctl restart swarmai-hive"),
]


@pytest.mark.parametrize("path,before,after", _SETUP_PATHS)
def test_every_setup_path_runs_the_bundle_and_only_warns_on_failure(path, before, after):
    text = (REPO / path).read_text()
    line = _bundle_line(text)
    # A failure (offline, a stalled download ended by the script's deadline) warns and
    # the setup goes on: `<bundle> || <warn>`, never a bare command under `set -e`.
    m = re.search(re.escape(BUNDLE) + r" --deadline (\d+)\)?\s*\|\|\s*(echo|_warn)\b", line)
    assert m, line
    # a stalled download holds a start or an update for a bounded, short time
    assert int(m.group(1)) <= TREE_SITTER_SETUP_DEADLINE_S
    if path == "dev.sh":
        # dev.sh wraps it in a function, called after the dependencies
        call = text.index("    _bundle_grammars\n")
        assert text.index(before) < call < text.index(after)
    else:
        at = text.index(BUNDLE)
        assert text.index(before) < at < text.index(after, at)
    r = subprocess.run(["bash", "-n", str(REPO / path)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_the_manual_hive_update_never_uploads_this_machines_grammars():
    # A developer's own directory holds its platform's libraries (a Mac's .dylib):
    # the Hive builds its own.
    text = (REPO / "hive/update-hive.sh").read_text()
    upload = text[text.index('"${PROJECT_ROOT}/backend/"') - 400:text.index('"${PROJECT_ROOT}/backend/"')]
    assert "--exclude='generated/tree_sitter_grammars*'" in upload   # and a killed build's leftovers


def test_the_hive_step_only_warns_when_the_bundle_fails(tmp_path):
    """The shared Hive step under the first boot's `set -euo pipefail`: the bundle
    fails (a fake sudo exits 1, in a temp INSTALL_DIR, so nothing on this machine is
    touched) and the script goes on with a warning."""
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "sudo").write_text("#!/bin/sh\necho \"sudo $*\" >&2\nexit 1\n")
    (tmp_path / "bin" / "sudo").chmod(0o755)
    (tmp_path / "install" / "backend").mkdir(parents=True)
    script = "set -euo pipefail\n" + TREE_SITTER_GRAMMARS_SH + "echo AFTER\n"
    env = {**os.environ, "PATH": f"{tmp_path / 'bin'}:{os.environ.get('PATH', '')}",
           "INSTALL_DIR": str(tmp_path / "install"), "SWARM_USER": "someone"}
    r = subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env,
                       timeout=30)
    assert r.returncode == 0, r.stderr
    assert "WARN: tree-sitter grammars not bundled" in r.stdout
    assert r.stdout.rstrip().endswith("AFTER")
    assert "sudo -H -u someone .venv/bin/python " + BUNDLE in r.stderr   # the script's own vars


def test_the_hive_step_deadline_fits_inside_the_update_wait():
    # The provisioner stops watching the update script after this long; the grammar
    # step, plus the rest of the script, must end well before it.
    assert TREE_SITTER_SETUP_DEADLINE_S * 2 <= UPDATE_WAIT_POLLS * UPDATE_WAIT_INTERVAL_S
    assert f"--deadline {TREE_SITTER_SETUP_DEADLINE_S})" in TREE_SITTER_GRAMMARS_SH


_RENDER = dict(s3_bucket="swarmai-hive-releases-us-east-1", version="2.5.2",
               auth_user="admin", auth_hash="$2b$12$" + "a" * 53, region="us-east-1")


@pytest.mark.parametrize("variant", [{}, {"prebaked": True}, {"origin_secret": "a" * 48}])
def test_both_first_boot_scripts_bundle_after_the_venv(variant):
    script = render_user_data(**_RENDER, **variant)
    assert script.count(TREE_SITTER_GRAMMARS_SH) == 1
    at = script.index(TREE_SITTER_GRAMMARS_SH)
    assert script.index("[4/9]") < at < script.index("# ── 5. Install Caddy ──")
    assert at < script.index("systemctl start swarmai-hive")
    # run as the service user, from the installed backend, with its venv
    assert ('cd "${INSTALL_DIR:-/opt/swarmai}/backend" && sudo -H -u "${SWARM_USER:-swarm}" '
            ".venv/bin/python " + BUNDLE) in _bundle_line(script)
    assert 'INSTALL_DIR="/opt/swarmai"' in script and 'SWARM_USER="swarm"' in script
    r = subprocess.run(["bash", "-n"], input=script, text=True, capture_output=True)
    assert r.returncode == 0, r.stderr


def _update_script():
    from hive.provisioner import HiveProvisioner
    p = HiveProvisioner(Path("/tmp/test.db"))
    ssm = MagicMock()
    ssm.send_command.return_value = {"Command": {"CommandId": "c-1"}}
    ssm.get_command_invocation.return_value = {"Status": "Success", "StandardOutputContent": ""}
    session = MagicMock()
    session.client.return_value = ssm

    async def fake_to_thread(fn, *args, **kwargs):
        return fn() if getattr(fn, "__name__", "") == "_run_command" else None

    instance = {"id": "inst-1", "name": "test-hive", "account_ref": "acc-1",
                "region": "us-east-1", "ec2_instance_id": "i-abc123",
                "s3_bucket": "swarmai-hive-test"}
    with patch.object(p, "_get_instance", new_callable=AsyncMock, return_value=instance), \
         patch.object(p, "_get_account", new_callable=AsyncMock, return_value={}), \
         patch.object(p, "_update_instance", new_callable=AsyncMock), \
         patch.object(p, "_sync_release_to_s3", new_callable=AsyncMock), \
         patch.object(p, "_wait_healthy_via_ssm", new_callable=AsyncMock, return_value=True), \
         patch.object(p, "_get_session", return_value=session), \
         patch("hive.provisioner.asyncio.to_thread", side_effect=fake_to_thread):
        asyncio.run(p.update("inst-1", "9.9.9", claimed=True))
    return ssm.send_command.call_args.kwargs["Parameters"]["commands"][0]


def test_the_hive_update_keeps_the_grammar_dir_and_bundles_before_the_restart():
    script = _update_script()
    assert script.count(TREE_SITTER_GRAMMARS_SH) == 1
    at = script.index(TREE_SITTER_GRAMMARS_SH)
    assert script.index(".venv/bin/pip install -q -e .") < at
    assert at < script.index("systemctl restart swarmai-hive && systemctl is-active")
    # the package never holds the directory (gitignored), so the sync's --delete
    # must not remove the one the Hive built
    sync = next(ln for ln in _logical_lines(script)
                if ln.startswith("rsync -a --delete") and "/tmp/hive-new/ /opt/swarmai/" in ln)
    assert "--exclude='backend/generated/tree_sitter_grammars'" in sync
    r = subprocess.run(["bash", "-n"], input=script, text=True, capture_output=True)
    assert r.returncode == 0, r.stderr
