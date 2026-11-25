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


def _gnu_find() -> bool:
    try:
        return "GNU findutils" in subprocess.run(["find", "--version"], capture_output=True, text=True).stdout
    except OSError:
        return False


_GNU_FIND = pytest.mark.skipif(not _gnu_find(), reason="the tightening step uses GNU find (Amazon Linux)")


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
    # no access of any kind on the Hive user's home: what a replay runs lives outside it
    assert not re.search(r"setfacl[^\n]*-m[^\n]*\$home", text)


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


def _stub_host(tmp_path: Path, missing_user: bool = False, home: Path | None = None) -> tuple[dict, Path]:
    """A PATH whose system commands only log (root's id, rpm, dnf, useradd, install,
    getent, setfacl, visudo, systemctl, nft), so the script's steps run without root;
    runuser logs and runs its command as this same user. The state folder (root's
    /var/lib/swarmai on a Hive) is a temp one."""
    bin_ = tmp_path / "stubs"
    bin_.mkdir(parents=True)
    log = tmp_path / "calls.log"
    if home is None:
        home = tmp_path / "home-swarm"
        home.mkdir()
    state = tmp_path / "state"
    state.mkdir()
    stubs = {
        "id": 'if [ "$1" = -u ] && [ -z "$2" ]; then echo 0; exit 0; fi\n'
              # the Hive user owns the test's files: it is this user here
              + 'if [ "$1" = -u ] && [ "$2" = swarm ]; then exec /usr/bin/id -u; fi\n'
              + ('echo "id: no such user" >&2; exit 1\n' if missing_user else 'echo 990\n'),
        "getent": f'echo "swarm:x:1000:1000::{home}:/bin/bash"\n',
        "useradd": "exit 1\n" if missing_user else "exit 0\n",
        "runuser": '[ "$1" = -u ] && [ "$3" = -- ] || exit 9\nshift 3\nexec "$@"\n',
    }
    for name in ("id", "rpm", "dnf", "useradd", "install", "getent", "setfacl", "visudo", "systemctl", "nft",
                 "runuser"):
        (bin_ / name).write_text(f'#!/bin/sh\necho "{name} $*" >> "{log}"\n' + stubs.get(name, "exit 0\n"))
        (bin_ / name).chmod(0o755)
    return {"PATH": f"{bin_}:/usr/bin:/bin", "SWARM_USER": "swarm", "HOME": str(tmp_path),
            "REPLAY_HOST_STATE_DIR": str(state)}, log


def test_the_image_mode_sets_up_everything_but_the_firewall(tmp_path):
    """An image (Elastic Workers) is built as root but runs as the Hive user, with no systemd
    and no way to load an nftables rule: its mode installs the packages, the replay user and
    sudoers, loads no firewall, and fails the build when one of those fails. Replay there then
    finds the metadata service open to the replay user and reports not_run."""
    env, log = _stub_host(tmp_path)
    r = subprocess.run(["bash", str(SCRIPT), "image"], capture_output=True, text=True, timeout=30, env=env)
    assert r.returncode == 0, r.stdout + r.stderr
    calls = log.read_text()
    assert "rpm -q" in calls and "visudo -cf" in calls
    assert "setfacl -m" not in calls and "setfacl -x u:swarm-replay" in calls    # no way into the home
    assert f"install -d -o swarm -g swarm-replay -m 0750 {tmp_path / 'state' / 'replay'}" in calls
    assert "systemctl" not in calls and "nft " not in calls
    assert "no firewall rule in an image" in r.stdout
    # (the default mode loads the firewall: test_the_ruleset_loads_at_every_boot_before_the_hive;
    # it writes /etc, so it is not run here)
    # a step that fails fails the image build
    env, log = _stub_host(tmp_path / "x", missing_user=True)
    r = subprocess.run(["bash", str(SCRIPT), "image"], capture_output=True, text=True, timeout=30, env=env)
    assert r.returncode != 0 and "useradd swarm-replay failed" in r.stdout


def _mode(p: Path) -> int:
    return p.lstat().st_mode & 0o7777


def test_no_home_traverse_and_an_old_one_is_removed(tmp_path):
    """The replay user gets no ACL entry on the Hive user's home: the entry an older version
    set (traverse) is removed and the home made 0700, once the replay user exists. Run here in
    image mode (setup mode also loads the firewall, which writes /etc); both modes run the same
    steps before it."""
    env, log = _stub_host(tmp_path)
    home = tmp_path / "home-swarm"
    home.chmod(0o711)
    r = subprocess.run(["bash", str(SCRIPT), "image"], capture_output=True, text=True, timeout=60, env=env)
    assert r.returncode == 0, r.stdout + r.stderr
    calls = log.read_text().splitlines()
    assert f"setfacl -x u:swarm-replay {home}" in calls, calls
    assert not any(c.startswith("setfacl -m") for c in calls), calls
    made = "install -d -o swarm-replay -g swarm-replay -m 0700 /var/lib/swarm-replay"
    assert calls.index(made) < calls.index(f"setfacl -x u:swarm-replay {home}")
    assert _mode(home) == 0o700, (oct(_mode(home)), r.stdout)
    assert "traverse on" not in r.stdout
    text = SCRIPT.read_text()
    main = text[text.index('status=0\ninstall_packages'):]
    assert main.index("private_home") < main.index('if [ "${1:-setup}" = image ]')


def test_a_linked_home_is_left_alone(tmp_path):
    """root never changes the mode or ACL through a link it found where the home should be."""
    target = tmp_path / "elsewhere"
    target.mkdir()
    target.chmod(0o755)
    link = tmp_path / "home-link"
    link.symlink_to(target)
    env, log = _stub_host(tmp_path, home=link)
    r = subprocess.run(["bash", str(SCRIPT), "image"], capture_output=True, text=True, timeout=60, env=env)
    assert r.returncode != 0 and "symbolic link" in r.stdout, r.stdout
    assert _mode(target) == 0o755
    assert str(link) not in "".join(ln for ln in log.read_text().splitlines() if ln.startswith(("setfacl", "runuser")))


def test_the_shared_location_is_root_made_and_hive_user_writable_only(tmp_path):
    """The pinned runtime and the task tools live in <state>/replay, outside the Hive user's
    home: made by root in a root-owned folder (so the Hive user cannot swap it for a link),
    owned by the Hive user with the replay user's group, 0750: only the Hive user writes it,
    the replay user reads it, nobody else enters."""
    env, log = _stub_host(tmp_path)
    r = subprocess.run(["bash", str(SCRIPT), "image"], capture_output=True, text=True, timeout=60, env=env)
    assert r.returncode == 0, r.stdout + r.stderr
    calls = log.read_text().splitlines()
    state = tmp_path / "state"
    parent = f"install -d -o root -g root -m 0755 {state}"
    child = f"install -d -o swarm -g swarm-replay -m 0750 {state / 'replay'}"
    assert parent in calls and child in calls and calls.index(parent) < calls.index(child)
    assert calls.index("install -d -o swarm-replay -g swarm-replay -m 0700 /var/lib/swarm-replay") \
        < calls.index(child)                       # the replay user's group exists by then
    text = SCRIPT.read_text()
    assert 'STATE_DIR="${REPLAY_HOST_STATE_DIR:-/var/lib/swarmai}"' in text
    from core.feature_map.replay_user import SHARED_DIR
    assert str(SHARED_DIR) == "/var/lib/swarmai/replay"
    assert 'SHARED_DIR="$STATE_DIR/replay"' in text
    task_tools = (REPO / "hive" / "bin" / "task-tools").read_text()
    assert "SWARM_REPLAY_SHARED_DIR-" + str(SHARED_DIR) + "}" in task_tools


@_GNU_FIND
def test_the_tree_is_tightened_once_without_following_a_link(tmp_path):
    """The update makes the Hive user's existing ~/.swarm-ai and ~/.claude private once:
    group and other bits removed, the owner's kept (a script stays runnable), run as the Hive
    user (runuser), never through a link: a link's target outside keeps its mode. Done once
    (a root-owned marker), so a later update does not walk the tree again."""
    env, log = _stub_host(tmp_path)
    home = tmp_path / "home-swarm"
    data = home / ".swarm-ai"
    (data / "SwarmWS" / ".context").mkdir(parents=True)
    for d in (data, data / "SwarmWS", data / "SwarmWS" / ".context"):
        d.chmod(0o755)
    db = data / "data.db"
    db.write_text("rows")
    db.chmod(0o644)
    script = data / "SwarmWS" / "hook.sh"
    script.write_text("#!/bin/sh\n")
    script.chmod(0o755)
    claude = home / ".claude" / "shell-snapshots"
    claude.mkdir(parents=True)
    (claude / "snap.sh").write_text("export A=1\n")
    (claude / "snap.sh").chmod(0o644)
    outside = tmp_path / "outside"
    outside.mkdir()
    outside.chmod(0o755)
    (outside / "f").write_text("x")
    (outside / "f").chmod(0o644)
    (data / "link-file").symlink_to(outside / "f")
    (data / "link-dir").symlink_to(outside)
    r = subprocess.run(["bash", str(SCRIPT), "image"], capture_output=True, text=True, timeout=60, env=env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert _mode(data) == _mode(data / "SwarmWS") == _mode(data / "SwarmWS" / ".context") == 0o700
    assert _mode(db) == 0o600 and _mode(script) == 0o700
    assert _mode(claude) == 0o700 and _mode(claude / "snap.sh") == 0o600
    assert _mode(outside) == 0o755 and _mode(outside / "f") == 0o644
    calls = log.read_text().splitlines()
    assert any(c.startswith(f"runuser -u swarm -- find -P {data} ") for c in calls), calls
    assert (tmp_path / "state" / "home-private.v1").is_file()
    # once: a file made 0644 later is not walked again
    db.chmod(0o644)
    r = subprocess.run(["bash", str(SCRIPT), "image"], capture_output=True, text=True, timeout=60, env=env)
    assert r.returncode == 0 and _mode(db) == 0o644


@_GNU_FIND
@pytest.mark.skipif(os.geteuid() == 0, reason="root passes every folder")
def test_a_failed_walk_still_tightens_the_next_folder(tmp_path):
    """A folder the walk cannot enter (or a file gone before its chmod) does not stop the
    tightening of the other folders; the marker is written only when nothing of the Hive
    user's is left open (here: the shut folder is private already)."""
    env, _log = _stub_host(tmp_path)
    home = tmp_path / "home-swarm"
    shut = home / ".swarm-ai" / "shut"
    shut.mkdir(parents=True)
    (home / ".swarm-ai").chmod(0o755)
    snap = home / ".claude" / "snap.sh"
    snap.parent.mkdir()
    snap.write_text("export A=1\n")
    snap.chmod(0o644)
    shut.chmod(0o000)
    try:
        r = subprocess.run(["bash", str(SCRIPT), "image"], capture_output=True, text=True, timeout=60, env=env)
    finally:
        shut.chmod(0o700)
    assert r.returncode == 0, r.stdout + r.stderr
    assert _mode(snap) == 0o600 and _mode(home / ".swarm-ai") == 0o700
    assert (tmp_path / "state" / "home-private.v1").is_file()


@_GNU_FIND
def test_no_marker_while_something_is_left_open(tmp_path):
    """When the walk could not close a file (here every chmod fails), the marker is not
    written: the step warns and the next update walks again."""
    env, _log = _stub_host(tmp_path)
    stubs = Path(env["PATH"].split(":")[0])
    (stubs / "chmod").write_text("#!/bin/sh\nexit 1\n")
    (stubs / "chmod").chmod(0o755)
    data = tmp_path / "home-swarm" / ".swarm-ai"
    data.mkdir()
    (data / "data.db").write_text("rows")
    (data / "data.db").chmod(0o644)
    r = subprocess.run(["bash", str(SCRIPT), "image"], capture_output=True, text=True, timeout=60, env=env)
    assert r.returncode != 0 and "still readable by others" in r.stdout, r.stdout
    assert not (tmp_path / "state" / "home-private.v1").exists()
