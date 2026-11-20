"""The host side of Feature Map replay on a Hive (hive/replay-host.sh): put in place as root by
every path that sets a Hive up or updates it, bounded, and only warning on failure.

Questions: does every setup and update path run the step after the package is in place, under
a deadline, with a failure that only warns; is the package set the one measured on an AL2023
arm64 Hive (ldd on the pinned headless shell, then a launch that renders text); do the rendered
sudoers drop-in, firewall ruleset and unit say what the boundary needs (only the replay user's
traffic to the metadata service rejected; sudo to that user only, checked by visudo); does the
provisioner wait long enough and keep the update from looking stale; and does the package
smoke test start the browser, with CI installing its libraries first. What root does on a real
Hive (dnf, useradd, nft) is checked after the next update; the ruleset itself is loaded and
probed in a network namespace on a Hive (the run's manual check).
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
from hive.user_data import (REPLAY_HOST_BIN, REPLAY_HOST_DEADLINE_S, REPLAY_HOST_DNF_DEADLINE_S,
                            REPLAY_HOST_SH, TREE_SITTER_GRAMMARS_SH, TREE_SITTER_SETUP_DEADLINE_S,
                            render_user_data)
from tests.test_tree_sitter_bundle_setup import _RENDER, _update_script

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "hive" / "replay-host.sh"

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="no bash")


def _sh(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", str(SCRIPT), *args], capture_output=True, text=True, timeout=30)


def _logical(text: str) -> list[str]:
    return re.sub(r"\\\n\s*", " ", text).splitlines()


def _step_line(text: str) -> str:
    """The one line that runs replay-host.sh (bash <path>)."""
    lines = [ln for ln in _logical(text) if re.search(r"\bbash \S*replay-host\.sh|bash \"\$REPLAY_HOST_BIN\"", ln)
             and not ln.lstrip().startswith("#")]
    assert len(lines) == 1, lines
    return lines[0]


def _timeout_shim(tmp_path: Path) -> str:
    """PATH with a stand-in `timeout` when this machine has none (a macOS dev machine; the
    step runs on Amazon Linux, coreutils timeout), so a test stays about the step."""
    shim = tmp_path / "bin"
    shim.mkdir(exist_ok=True)
    (shim / "timeout").write_text("#!/bin/sh\nshift\nexec \"$@\"\n")
    (shim / "timeout").chmod(0o755)
    path = os.environ.get("PATH", "")
    return f"{shim}{os.pathsep}{path}" if shutil.which("timeout") is None else path


@pytest.mark.parametrize("variant", [{}, {"prebaked": True}, {"origin_secret": "a" * 48}])
def test_every_setup_path_runs_the_step_bounded_and_warn_only(variant):
    # first boot: after the package is unpacked (the script is in it) and the grammar step,
    # before the services start; a prebaked AMI runs it too (outside the stripped segments)
    script = render_user_data(**_RENDER, **variant)
    assert script.count(REPLAY_HOST_SH) == 1
    at = script.index(REPLAY_HOST_SH)
    assert script.index("tar xzf /tmp/hive.tar.gz") < script.index(TREE_SITTER_GRAMMARS_SH) < at
    assert at < script.index("# ── 5. Install Caddy ──") < script.index("systemctl start swarmai-hive")
    line = _step_line(script)
    assert f"timeout {REPLAY_HOST_DEADLINE_S} env " in line and 'bash "$REPLAY_HOST_BIN"' in line
    assert re.search(r"\|\|\s*echo \"WARN:", line), line
    # root runs its own copy; the first boot takes it from the tree it has just unpacked
    assert f'REPLAY_HOST_BIN="${{REPLAY_HOST_BIN:-{REPLAY_HOST_BIN}}}"' in REPLAY_HOST_SH
    assert REPLAY_HOST_BIN.startswith("/usr/local/libexec/")
    assert "REPLAY_HOST_TARBALL=" not in script
    r = subprocess.run(["bash", "-n"], input=script, text=True, capture_output=True)
    assert r.returncode == 0, r.stderr

    # update: after the new package is synced into /opt/swarmai, before the restart
    update = _update_script()
    assert update.count(REPLAY_HOST_SH) == 1
    at = update.index(REPLAY_HOST_SH)
    assert update.index("/tmp/hive-new/ /opt/swarmai/") < update.index(TREE_SITTER_GRAMMARS_SH) < at
    assert at < update.index("systemctl restart swarmai-hive --no-block")
    # the update takes the script from the checked package, not from the synced tree
    assert update.index("verify_hive_package /tmp/hive-update.tar.gz") \
        < update.index("REPLAY_HOST_TARBALL=/tmp/hive-update.tar.gz\n") < at
    assert update.index("rm -rf /opt/swarmai.bak /tmp/hive-new /tmp/hive-update.tar.gz") > at

    # the manual reference scripts run the same step
    for path, before, after in (("hive/setup.sh", "bundle_tree_sitter_grammars.py", "[5/10]"),
                                ("hive/update-hive.sh", "bundle_tree_sitter_grammars.py",
                                 "systemctl restart swarmai-hive")):
        text = (REPO / path).read_text()
        line = _step_line(text)
        assert re.search(r"timeout \d+ env ", line) and re.search(r"\|\|\s*echo \"", line), line
        if path == "hive/update-hive.sh":
            assert "bash /usr/local/libexec/swarmai/replay-host.sh" in line
        assert text.index(before) < text.index(line.split(" bash ")[0].strip()) < text.index(after)
        assert subprocess.run(["bash", "-n", str(REPO / path)]).returncode == 0


def test_the_step_only_warns_when_the_script_fails(tmp_path):
    """Under the first boot's `set -euo pipefail` the step fails (a replay-host.sh that exits 1,
    in a temp INSTALL_DIR) and the setup goes on with a warning."""
    (tmp_path / "hive").mkdir()
    (tmp_path / "hive" / "replay-host.sh").write_text("echo \"as $SWARM_USER\"; exit 1\n")
    script = "set -euo pipefail\n" + REPLAY_HOST_SH + "echo AFTER\n"
    r = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=30,
                       env={**os.environ, "PATH": _timeout_shim(tmp_path), "INSTALL_DIR": str(tmp_path),
                            "SWARM_USER": "someone",
                            "REPLAY_HOST_BIN": str(tmp_path / "libexec" / "replay-host.sh")})
    assert r.returncode == 0, r.stderr
    assert "as someone" in r.stdout and "WARN: Feature Map replay host step incomplete" in r.stdout
    assert r.stdout.rstrip().endswith("AFTER")


def _gnu_tar() -> bool:
    try:
        return "GNU tar" in subprocess.run(["tar", "--version"], capture_output=True, text=True).stdout
    except OSError:
        return False


@pytest.mark.skipif(not _gnu_tar(), reason="the update step uses GNU tar (Amazon Linux)")
def test_the_update_runs_the_packages_script_not_the_synced_tree(tmp_path):
    """The Hive user owns /opt/swarmai: a replay-host.sh changed there is never what root
    runs on an update; the copy root runs comes from the checked package, is root's own file
    (mode 0700) and replaces what was there."""
    pkg = tmp_path / "pkg" / "swarmai-hive-v9.9.9"
    (pkg / "hive").mkdir(parents=True)
    (pkg / "hive" / "replay-host.sh").write_text('echo "from the package"\n')
    tarball = tmp_path / "hive-update.tar.gz"
    subprocess.run(["tar", "czf", str(tarball), "-C", str(pkg.parent), pkg.name], check=True)
    install = tmp_path / "opt"
    (install / "hive").mkdir(parents=True)
    (install / "hive" / "replay-host.sh").write_text('echo "changed by the Hive user"\n')
    bin_ = tmp_path / "libexec" / "replay-host.sh"
    bin_.parent.mkdir()
    bin_.write_text('echo "an older copy"\n')
    r = subprocess.run(["bash", "-c", "set -euo pipefail\nREPLAY_HOST_TARBALL=" + str(tarball) + "\n"
                        + REPLAY_HOST_SH],
                       capture_output=True, text=True, timeout=60,
                       env={**os.environ, "PATH": _timeout_shim(tmp_path), "INSTALL_DIR": str(install),
                            "REPLAY_HOST_BIN": str(bin_)})
    assert r.returncode == 0, r.stderr
    assert "from the package" in r.stdout and "changed by the Hive user" not in r.stdout, r.stdout
    assert (bin_.stat().st_mode & 0o777) == 0o700
    assert not bin_.with_name(bin_.name + ".new").exists()


def test_the_deadlines_fit_inside_the_update_wait():
    # dnf gets most of the step's deadline, the rest of the step the remainder
    assert REPLAY_HOST_DNF_DEADLINE_S < REPLAY_HOST_DEADLINE_S
    assert f"REPLAY_HOST_DNF_DEADLINE_S={REPLAY_HOST_DNF_DEADLINE_S} " in REPLAY_HOST_SH
    wait = UPDATE_WAIT_POLLS * UPDATE_WAIT_INTERVAL_S
    assert 2 * (TREE_SITTER_SETUP_DEADLINE_S + REPLAY_HOST_DEADLINE_S) <= wait


def test_a_long_update_wait_refreshes_the_row_so_it_never_looks_stale():
    """The wait can pass UPDATE_STALE_S: every poll while the script still runs refreshes the
    row (status updating, so updated_at moves)."""
    from hive.provisioner import HiveProvisioner
    p = HiveProvisioner(Path("/tmp/test.db"))
    ssm = MagicMock()
    ssm.send_command.return_value = {"Command": {"CommandId": "c-1"}}
    ssm.get_command_invocation.side_effect = [{"Status": "InProgress"}, {"Status": "InProgress"},
                                              {"Status": "Success", "StandardOutputContent": ""}]
    session = MagicMock()
    session.client.return_value = ssm
    instance = {"id": "inst-1", "name": "test-hive", "account_ref": "acc-1",
                "region": "us-east-1", "ec2_instance_id": "i-abc123", "s3_bucket": "swarmai-hive-test"}
    touch = AsyncMock()
    with patch.object(p, "_get_instance", new_callable=AsyncMock, return_value=instance), \
         patch.object(p, "_get_account", new_callable=AsyncMock, return_value={}), \
         patch.object(p, "_update_instance", new_callable=AsyncMock), \
         patch.object(p, "_touch_updating", touch), \
         patch.object(p, "_sync_release_to_s3", new_callable=AsyncMock), \
         patch.object(p, "_wait_healthy_via_ssm", new_callable=AsyncMock, return_value=True), \
         patch.object(p, "_get_session", return_value=session), \
         patch("hive.provisioner._refresh_role_policy"), \
         patch("hive.provisioner.time.sleep"):
        asyncio.run(p.update("inst-1", "9.9.9", claimed=True))
    # one beat per poll that found the script still running; the beat itself only refreshes
    # a row still 'updating' (test_feature_map_replay_user::test_touch_updating_...)
    assert [c.args for c in touch.call_args_list] == [("inst-1",), ("inst-1",)]


def test_the_package_set_is_the_measured_one():
    r = _sh("packages")
    assert r.returncode == 0
    assert set(r.stdout.split()) == {
        # libraries the shell's ldd reported missing on AL2023 arm64, by package
        "atk", "at-spi2-core", "alsa-lib", "libX11", "libxcb", "libXcomposite", "libXdamage",
        "libXext", "libXfixes", "libXrandr", "libxkbcommon", "mesa-libgbm",
        # text in screenshots (without them every glyph renders blank)
        "fontconfig", "liberation-sans-fonts", "liberation-serif-fonts", "liberation-mono-fonts",
        "google-noto-sans-cjk-ttc-fonts", "google-noto-emoji-color-fonts",
        # the firewall rule
        "nftables"}
    text = SCRIPT.read_text()
    # installed only when one is missing, under the dnf deadline
    assert re.search(r"if rpm -q \$pkgs", text)
    assert 'timeout "$DNF_DEADLINE_S" dnf install -y' in text


def test_nft_ruleset_rejects_only_the_replay_uid_to_the_metadata_service():
    r = _sh("render-nft", "1002")
    assert r.returncode == 0, r.stderr
    rules = [ln.strip() for ln in r.stdout.splitlines() if ln.strip()]
    # declare, delete, define: loading it again replaces the table, never doubles its rules
    assert rules[:3] == ["table inet swarmai_replay", "delete table inet swarmai_replay",
                         "table inet swarmai_replay {"]
    assert "type filter hook output priority filter; policy accept;" in rules
    matches = [ln for ln in rules if ln.startswith("meta ")]
    assert matches == ["meta skuid 1002 ip daddr 169.254.169.254 counter reject",
                       "meta skuid 1002 ip6 daddr fd00:ec2::254 counter reject"]
    for bad in ("", "swarm-replay", "1002; flush ruleset"):
        assert _sh("render-nft", bad).returncode == 2


def test_sudoers_lets_only_the_hive_user_become_only_the_replay_user(tmp_path):
    r = _sh("render-sudoers", "swarm")
    assert r.returncode == 0, r.stderr
    rules = [ln for ln in r.stdout.splitlines() if ln and not ln.startswith("#")]
    assert rules == ["Defaults>swarm-replay !use_pty, !requiretty, !pam_session",
                     "swarm ALL=(swarm-replay) NOPASSWD: ALL"]
    assert _sh("render-sudoers", "x y").returncode == 2
    visudo = shutil.which("visudo") or ("/usr/sbin/visudo" if Path("/usr/sbin/visudo").exists() else None)
    if visudo:
        f = tmp_path / "sudoers"
        f.write_text(r.stdout)
        check = subprocess.run([visudo, "-cf", str(f)], capture_output=True, text=True, timeout=30)
        assert check.returncode == 0, check.stdout + check.stderr
    text = SCRIPT.read_text()
    # checked before it is put in place, never installed when visudo refuses it
    assert text.index('visudo -cf "$tmp"') < text.index('install -m 0440 -o root -g root "$tmp"')


def test_the_ruleset_loads_at_every_boot_before_the_hive():
    r = _sh("render-unit", "/usr/sbin/nft")
    assert "ExecStart=/usr/sbin/nft -f /etc/swarmai/replay-imds.nft" in r.stdout
    assert "Before=network-pre.target swarmai-hive.service" in r.stdout
    assert "WantedBy=multi-user.target" in r.stdout and "Type=oneshot" in r.stdout
    text = SCRIPT.read_text()
    assert "systemctl enable swarmai-replay-imds.service" in text
    assert "systemctl restart swarmai-replay-imds.service" in text   # applied now, too


def test_the_auth_folder_is_never_loosened():
    # /etc/swarmai holds hive-auth.env and is 0700 (user_data.py step 6b); `install -d -m` on an
    # existing folder would change its mode on every update
    text = SCRIPT.read_text()
    assert "[ -d /etc/swarmai ] || install -d -m 0700 /etc/swarmai" in text
    assert not re.search(r"install -d[^\n]*-m 07[0-7][1-7] /etc/swarmai", text)
    assert not re.search(r"chmod [0-7]*[1-7] /etc/swarmai\b", text)


def test_the_user_and_its_home_access():
    text = SCRIPT.read_text()
    assert 'useradd --system --user-group --home-dir "$REPLAY_HOME" --create-home' in text
    assert "--shell /sbin/nologin" in text
    # traverse only, never list or write, on the Hive user's home
    assert 'setfacl -m "u:${REPLAY_USER}:x" "$home"' in text


@pytest.mark.skipif(os.geteuid() == 0, reason="checks the non-root refusal")
def test_setup_refuses_without_root():
    r = _sh()
    assert r.returncode == 1 and "run as root" in r.stderr


def test_the_package_ships_the_script():
    text = (REPO / "hive" / "verify_package.sh").read_text()
    assert 'check "^hive/replay-host.sh$"' in text
    release = (REPO / "hive" / "release.sh").read_text()
    excluded = re.search(r"grep -v -E '\^hive/\(([^)]*)\)", release).group(1)
    assert "replay-host" not in excluded


def test_smoke_launches_the_browser_and_ci_installs_its_dependencies():
    smoke = (REPO / "hive" / "smoke_package.sh").read_text()
    at = smoke.index("-m core.feature_map.runtime --launch")
    assert smoke.index("non-public routes refuse without credentials (401)") < at
    launch = next(ln for ln in _logical(smoke) if "-m core.feature_map.runtime --launch" in ln)
    assert 'SWARM_DATA_DIR="${HOME_DIR}/.swarm-ai"' in launch     # the temp data folder
    assert 'cd "${APP}/backend"' in launch                        # the package's own backend
    assert re.search(r"\|\|\s*fail ", launch), launch
    assert subprocess.run(["bash", "-n", str(REPO / "hive" / "smoke_package.sh")]).returncode == 0

    import yaml
    jobs = yaml.safe_load((REPO / ".github" / "workflows" / "release.yml").read_text())["jobs"]
    steps = [s.get("name") or "" for s in jobs["build-hive"]["steps"]]
    deps = steps.index("Install the browser's system libraries")
    assert deps < steps.index("Install and start the package (smoke)")
    run = jobs["build-hive"]["steps"][deps]["run"]
    assert "PLAYWRIGHT_VERSION" in run and "install-deps chromium" in run
