"""A Hive's /tmp is on the root disk, not on tmpfs (hive/user_data.py TMP_ON_DISK_SH).

Questions:
- is tmp.mount masked only once the directory under the mount was made world-writable,
  so a failure can never leave a Hive whose next boot has a /tmp nobody can write;
- does every failure only warn, so neither the first boot nor an update stops on it;
- does the first boot move /tmp to disk at once (lazy unmount), only when the mask is in
  place and /tmp is a mount, before anything is downloaded to /tmp;
- is the /tmp cleanup shortened to 3 days with the system's other tmpfiles lines kept;
- does an update set the mask without unmounting the /tmp a running Hive is using, before
  the daemon restarts, and start from an empty /tmp/hive-new;
- do both rendered scripts still parse.

The system commands are fakes, so these tests prove the order of the commands and what
each failure does; that a bind view reaches the directory under the mount and that the
OS boots with the mask are kernel and systemd behaviour, checked on a real Hive.
"""
import asyncio
import shutil
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hive.user_data import TMP_ON_DISK_SH, render_user_data

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="no bash")

RENDER = dict(s3_bucket="swarmai-hive-releases-us-east-1", version="2.5.2", auth_user="admin",
              auth_hash="$2b$14$" + "a" * 53, region="us-east-1")

FAKES = {
    # each fake appends its name and arguments to $CALLS, and fails when $FAIL_<NAME> is set
    "mktemp": 'echo "mktemp $*" >> "$CALLS"; [ -z "$FAIL_MKTEMP" ] || exit 1; echo "$VIEW"',
    "mount": 'echo "mount $*" >> "$CALLS"; [ -z "$FAIL_MOUNT" ] || exit 32',
    "chmod": 'echo "chmod $*" >> "$CALLS"; [ -z "$FAIL_CHMOD" ] || exit 1',
    "umount": 'echo "umount $*" >> "$CALLS"; [ -z "$FAIL_UMOUNT" ] || exit 32',
    "rmdir": 'echo "rmdir $*" >> "$CALLS"',
    "mountpoint": 'echo "mountpoint $*" >> "$CALLS"; [ -z "$NOT_MOUNTED" ]',
    # is-enabled answers "masked" once mask was called, as systemctl does
    "systemctl": ('echo "systemctl $*" >> "$CALLS"\n'
                  'if [ "$1" = mask ]; then [ -z "$FAIL_MASK" ] || exit 1; touch "$MASKED"; fi\n'
                  'if [ "$1" = is-enabled ]; then\n'
                  '  if [ -e "$MASKED" ]; then echo masked; exit 1; fi; echo static; fi'),
}


def _run(tmp_path, script, **fail):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    for name, body in FAKES.items():
        f = bin_dir / name
        f.write_text("#!/bin/bash\n" + body + "\n")
        f.chmod(0o755)
    calls = tmp_path / "calls.log"
    calls.write_text("")
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "CALLS": str(calls), "VIEW": "/run/swarm-rootfs.TEST",
           "MASKED": str(tmp_path / "masked")}
    env.update({k.upper(): "1" for k, v in fail.items() if v})
    proc = subprocess.run(["bash", "-c", "set -euo pipefail\n" + script], env=env,
                          capture_output=True, text=True, timeout=30)
    return proc, calls.read_text().splitlines()


def _first_boot_block():
    script = render_user_data(**RENDER)
    start = script.index("# ── /tmp on disk ──")
    return script[start:script.index('HIVE_S3_BUCKET="')]


def test_the_mask_follows_a_writable_on_disk_tmp(tmp_path):
    proc, calls = _run(tmp_path, TMP_ON_DISK_SH)
    assert proc.returncode == 0, proc.stderr
    assert calls.index("chmod 1777 /run/swarm-rootfs.TEST/tmp") < calls.index("systemctl mask tmp.mount")
    assert "mount --bind / /run/swarm-rootfs.TEST" in calls
    assert calls[-2:] == ["umount /run/swarm-rootfs.TEST", "rmdir /run/swarm-rootfs.TEST"]
    assert "on disk from the next boot" in proc.stdout


@pytest.mark.parametrize("failure", ["fail_mktemp", "fail_mount", "fail_chmod"])
def test_no_mask_when_the_on_disk_tmp_could_not_be_made_writable(tmp_path, failure):
    proc, calls = _run(tmp_path, TMP_ON_DISK_SH, **{failure: True})
    assert proc.returncode == 0, proc.stderr
    assert not any(c.startswith("systemctl mask") for c in calls)
    assert "WARN:" in proc.stdout and "stays on tmpfs" in proc.stdout


def test_a_failed_mask_only_warns(tmp_path):
    proc, calls = _run(tmp_path, TMP_ON_DISK_SH, fail_mask=True)
    assert proc.returncode == 0, proc.stderr
    assert "WARN: could not mask tmp.mount" in proc.stdout


def test_the_first_boot_moves_tmp_to_disk_at_once(tmp_path):
    proc, calls = _run(tmp_path, _first_boot_block())
    assert proc.returncode == 0, proc.stderr
    assert calls.index("systemctl mask tmp.mount") < calls.index("umount -l /tmp")
    assert "/tmp: now on disk" in proc.stdout


def test_the_first_boot_leaves_tmp_mounted_without_the_mask(tmp_path):
    proc, calls = _run(tmp_path, _first_boot_block(), fail_chmod=True)
    assert proc.returncode == 0, proc.stderr
    assert "umount -l /tmp" not in calls


def test_the_first_boot_leaves_a_tmp_that_is_no_mount_alone(tmp_path):
    proc, calls = _run(tmp_path, _first_boot_block(), not_mounted=True)
    assert proc.returncode == 0, proc.stderr
    assert "systemctl mask tmp.mount" in calls
    assert "umount -l /tmp" not in calls


def _cleanup_snippet(tmp_path, conf):
    src, dst = tmp_path / "usr-tmp.conf", tmp_path / "etc" / "tmp.conf"
    dst.parent.mkdir()
    if conf is not None:
        src.write_text(conf)
    script = (TMP_ON_DISK_SH.replace("/usr/lib/tmpfiles.d/tmp.conf", str(src))
              .replace("/etc/tmpfiles.d/tmp.conf", str(dst))
              .replace("mkdir -p /etc/tmpfiles.d", f"mkdir -p {dst.parent}"))
    assert str(src) in script and str(dst) in script
    return script, dst


AL2023_TMP_CONF = ("# See tmpfiles.d(5) for details\n"
                   "q /tmp 1777 root root 10d\n"
                   "q /var/tmp 1777 root root 30d\n"
                   "x /tmp/systemd-private-%b-*\n")


def test_the_tmp_cleanup_is_shortened_and_the_other_lines_kept(tmp_path):
    script, dst = _cleanup_snippet(tmp_path, AL2023_TMP_CONF)
    for _ in range(2):  # every update runs it again
        proc, _calls = _run(tmp_path, script)
        assert proc.returncode == 0, proc.stderr
    assert dst.read_text() == AL2023_TMP_CONF.replace("root root 10d", "root root 3d")
    assert "removed" in proc.stdout


@pytest.mark.parametrize("conf", [None, "q /tmp 1777 root root 7d\n"])
def test_an_unknown_tmp_cleanup_is_left_alone(tmp_path, conf):
    script, dst = _cleanup_snippet(tmp_path, conf)
    proc, _calls = _run(tmp_path, script)
    assert proc.returncode == 0, proc.stderr
    assert not dst.exists()
    assert "left as it is" in proc.stdout


def test_a_failed_unmount_at_first_boot_only_warns(tmp_path):
    proc, calls = _run(tmp_path, _first_boot_block(), fail_umount=True)
    assert proc.returncode == 0, proc.stderr
    assert "umount -l /tmp" in calls
    assert "WARN: /tmp stays on tmpfs until the next boot" in proc.stdout


@pytest.mark.parametrize("variant", [{}, {"prebaked": True}, {"origin_secret": "a" * 48}])
def test_the_first_boot_script_moves_tmp_before_anything_is_downloaded_there(variant):
    script = render_user_data(**RENDER, **variant)
    assert script.count(TMP_ON_DISK_SH) == 1
    assert script.index("umount -l /tmp") < script.index("/tmp/hive.tar.gz")
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

    instance = {"id": "inst-1", "name": "test-hive", "account_ref": "acc-1", "region": "us-east-1",
                "ec2_instance_id": "i-abc123", "s3_bucket": "swarmai-hive-test"}
    with patch.object(p, "_get_instance", new_callable=AsyncMock, return_value=instance), \
         patch.object(p, "_get_account", new_callable=AsyncMock, return_value={}), \
         patch.object(p, "_update_instance", new_callable=AsyncMock), \
         patch.object(p, "_sync_release_to_s3", new_callable=AsyncMock), \
         patch.object(p, "_wait_healthy_via_ssm", new_callable=AsyncMock, return_value=True), \
         patch.object(p, "_get_session", return_value=session), \
         patch("hive.provisioner.asyncio.to_thread", side_effect=fake_to_thread):
        asyncio.run(p.update("inst-1", "9.9.9", claimed=True))
    return ssm.send_command.call_args.kwargs["Parameters"]["commands"][0]


def test_an_update_masks_tmp_mount_before_the_restart_and_never_unmounts_tmp():
    script = _update_script()
    assert script.count(TMP_ON_DISK_SH) == 1
    # before the update's own (blocking) restart; "--no-block" is only the rollback's
    assert script.index(TMP_ON_DISK_SH) < script.index("UPDATE_RESTARTED=1")
    assert "umount -l /tmp" not in script
    assert script.index("rm -rf /tmp/hive-new /tmp/hive-update.tar.gz") < script.index("mkdir -p /tmp/hive-new")
    r = subprocess.run(["bash", "-n"], input=script, text=True, capture_output=True)
    assert r.returncode == 0, r.stderr
