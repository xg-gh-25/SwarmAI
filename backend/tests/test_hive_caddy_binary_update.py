"""An existing Hive's update gives it the pinned Caddy binary first boot installs.

Hives made before Caddy was pinned run a binary first boot downloaded from caddyserver.com
without a check, and first boot never runs again. The update script's step
(user_data.caddy_binary_update_sh) installs the pinned, checksum-checked release only when
/usr/bin/caddy is another build, and on any failure keeps the old binary and stops the
update with the reason. The step runs under bash with ``curl`` / ``systemctl`` stubs and a
fake release archive (its checksums put in place of the pinned ones); no network, no AWS.
"""

import hashlib
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tarfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hive.user_data import (
    CADDY_ARM64_BINARY_SHA512,
    CADDY_ARM64_SHA512,
    CADDY_INSTALL_SH,
    CADDY_VERSION,
    caddy_binary_update_sh,
    health_exempt_fix_sh,
)

pytestmark = pytest.mark.skipif(
    any(shutil.which(t) is None for t in ("bash", "sha512sum", "tar", "install")),
    reason="needs bash, sha512sum, tar and install")

OLD = b"#!/bin/sh\n# the unchecked caddyserver.com build\nexit 0\n"
_URL = (f"https://github.com/caddyserver/caddy/releases/download/v{CADDY_VERSION}/"
        f"caddy_{CADDY_VERSION}_linux_arm64.tar.gz")

# curl: a download (-o <file>) gets the fake archive (or fails while a flag file exists); a
# probe (--path-as-is ... http://127.0.0.1<path>) gets the code forced for that path, else
# what a Hive's Caddy answers: /health open, everything else 401.
_CURL_STUB = r'''import json, shutil, sys
from pathlib import Path
args = sys.argv[1:]
tmp = Path(__TMP__)
with open(tmp / "curl.log", "a") as f:
    f.write(" ".join(args) + "\n")
if "-o" in args and "--path-as-is" not in args:
    if (tmp / "download-fails").exists():
        sys.exit(22)
    shutil.copy(tmp / "release.tar.gz", args[args.index("-o") + 1])
    sys.exit(0)
path = args[-1][len("http://127.0.0.1"):]
forced = tmp / "codes.json"
codes = json.loads(forced.read_text()) if forced.exists() else {}
print(codes.get(path, "200" if path == "/health" else "401"), end="")
'''


class _Hive:
    def __init__(self, tmp: Path, old: bytes | None = OLD):
        self.tmp = tmp
        self.caddy = tmp / "usr-bin-caddy"
        if old is not None:
            self.caddy.write_bytes(old)
            self.caddy.chmod(0o755)
        self.caddyfile = tmp / "Caddyfile"
        self.caddyfile.write_text(":80 {\n    respond 200\n}\n")
        self.bin = tmp / "bin"
        self.bin.mkdir()
        # the release's caddy: `validate` fails while a flag file exists
        self.new = (f'#!/bin/sh\n[ "$1" = validate ] && [ -f "{tmp}/validate-fails" ] && exit 1\n'
                    'exit 0\n').encode()
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as t:
            info = tarfile.TarInfo("caddy")
            info.size, info.mode = len(self.new), 0o755
            t.addfile(info, io.BytesIO(self.new))
            readme = b"not installed"
            info = tarfile.TarInfo("README.md")
            info.size = len(readme)
            t.addfile(info, io.BytesIO(readme))
        self.archive = buf.getvalue()
        (tmp / "release.tar.gz").write_bytes(self.archive)
        self.archive_pin = hashlib.sha512(self.archive).hexdigest()
        self.binary_pin = hashlib.sha512(self.new).hexdigest()
        self._stub("curl", _CURL_STUB.replace("__TMP__", repr(str(tmp))), sys.executable)
        self._stub("systemctl", f'echo "$*" >> "{tmp}/systemctl.log"\n'
                                f'[ "$1" = restart ] && [ -f "{tmp}/restart-fails" ] && exit 1\n'
                                'exit 0\n')

    def _stub(self, name, body, interpreter="/bin/bash"):
        p = self.bin / name
        p.write_text(f"#!{interpreter}\n" + body)
        p.chmod(p.stat().st_mode | stat.S_IEXEC)

    def flag(self, name: str) -> None:
        (self.tmp / name).write_text("")

    def force(self, path: str, code: str) -> None:
        (self.tmp / "codes.json").write_text(json.dumps({path: code}))

    def run(self) -> subprocess.CompletedProcess:
        step = caddy_binary_update_sh()
        for line in ('CADDY_BIN="/usr/bin/caddy"', 'CADDY_CONFIG="/etc/caddy/Caddyfile"'):
            assert step.count(line) == 1, "the step names its files in one variable each"
        step = (step.replace('CADDY_BIN="/usr/bin/caddy"', f'CADDY_BIN="{self.caddy}"')
                .replace('CADDY_CONFIG="/etc/caddy/Caddyfile"', f'CADDY_CONFIG="{self.caddyfile}"')
                .replace(CADDY_ARM64_SHA512, self.archive_pin)
                .replace(CADDY_ARM64_BINARY_SHA512, self.binary_pin))
        env = {**os.environ, "PATH": f"{self.bin}{os.pathsep}{os.environ.get('PATH', '')}",
               "TMPDIR": str(self.tmp)}
        return subprocess.run(["bash", "-c", "set -euo pipefail\n" + step + "\necho STEP-PASSED\n"],
                              capture_output=True, text=True, timeout=60, env=env)

    def restarts(self) -> int:
        log = self.tmp / "systemctl.log"
        return log.read_text().count("restart caddy") if log.exists() else 0

    def downloads(self) -> int:
        log = self.tmp / "curl.log"
        return log.read_text().count(_URL) if log.exists() else 0

    def leftovers(self) -> list[str]:
        mine = {"usr-bin-caddy", "Caddyfile", "bin", "release.tar.gz", "curl.log",
                "systemctl.log", "codes.json", "validate-fails", "restart-fails",
                "download-fails"}
        return sorted(p.name for p in self.tmp.iterdir() if p.name not in mine)


def test_pinned_binary_is_left_alone(tmp_path):
    hive = _Hive(tmp_path)
    hive.caddy.write_bytes(hive.new)
    r = hive.run()
    assert r.returncode == 0, r.stdout + r.stderr
    assert f"Caddy binary: already the pinned v{CADDY_VERSION}" in r.stdout
    assert "STEP-PASSED" in r.stdout
    assert hive.caddy.read_bytes() == hive.new
    assert hive.downloads() == 0 and hive.restarts() == 0
    assert hive.leftovers() == []


@pytest.mark.parametrize("old", [OLD, None], ids=["other-build", "no-binary"])
def test_different_binary_is_replaced_and_checked(tmp_path, old):
    hive = _Hive(tmp_path, old)
    r = hive.run()
    assert r.returncode == 0, r.stdout + r.stderr
    assert f"Caddy binary: now the pinned v{CADDY_VERSION} (sha512 checked)" in r.stdout
    assert "STEP-PASSED" in r.stdout
    assert hive.caddy.read_bytes() == hive.new
    assert stat.S_IMODE(hive.caddy.stat().st_mode) == 0o755
    assert hive.downloads() == 1 and hive.restarts() == 1
    probes = (tmp_path / "curl.log").read_text()
    assert "http://127.0.0.1/health" in probes and "http://127.0.0.1/swarmai-caddy-check" in probes
    assert hive.leftovers() == []


@pytest.mark.parametrize("failure, reason", [
    ("download", "could not be downloaded, did not match its checksum"),
    ("checksum", "could not be downloaded, did not match its checksum"),
    ("binary", "is not the pinned"),
    ("validate", "rejects"),
])
def test_failure_before_the_swap_keeps_the_old_binary(tmp_path, failure, reason):
    """Nothing is swapped or restarted: the old binary stays byte for byte, and the update
    stops (exit 1, nothing after the step runs) with the reason."""
    hive = _Hive(tmp_path)
    if failure == "download":
        hive.flag("download-fails")
    elif failure == "checksum":
        hive.archive_pin = hashlib.sha512(b"another archive").hexdigest()
    elif failure == "binary":
        hive.binary_pin = hashlib.sha512(b"another build").hexdigest()
    else:
        hive.flag("validate-fails")
    r = hive.run()
    assert r.returncode == 1, r.stdout + r.stderr
    assert "STEP-PASSED" not in r.stdout
    assert "ERROR: Caddy binary update:" in r.stdout and reason in r.stdout
    assert "previous Caddy kept, Hive not updated" in r.stdout
    if failure == "checksum":
        assert f"Caddy v{CADDY_VERSION} does not match its release checksum" in r.stdout
    assert hive.caddy.read_bytes() == OLD
    assert hive.restarts() == 0
    assert hive.leftovers() == []


@pytest.mark.parametrize("failure, reason", [
    ("restart", "did not restart"),
    ("health", "/health answered 401"),
    ("open", "a request without credentials answered 200"),
])
def test_failure_after_the_swap_restores_the_old_binary(tmp_path, failure, reason):
    """The new binary was in place: the old one goes back, Caddy is restarted with it, and
    the update stops with the reason."""
    hive = _Hive(tmp_path)
    if failure == "restart":
        hive.flag("restart-fails")
    elif failure == "health":
        hive.force("/health", "401")
    else:
        hive.force("/swarmai-caddy-check", "200")
    r = hive.run()
    assert r.returncode == 1, r.stdout + r.stderr
    assert "STEP-PASSED" not in r.stdout
    assert "ERROR: Caddy binary update:" in r.stdout and reason in r.stdout
    assert hive.caddy.read_bytes() == OLD
    assert stat.S_IMODE(hive.caddy.stat().st_mode) == 0o755
    assert hive.restarts() == 2  # with the new binary, then with the old one
    assert hive.leftovers() == []


def test_a_hive_without_a_binary_gets_none_back_on_failure(tmp_path):
    hive = _Hive(tmp_path, old=None)
    hive.force("/health", "401")
    r = hive.run()
    assert r.returncode == 1, r.stdout + r.stderr
    assert not hive.caddy.exists()
    assert hive.leftovers() == []


async def _update_script() -> str:
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
        await p.update("inst-1", "9.9.9", claimed=True)
    return ssm.send_command.call_args.kwargs["Parameters"]["commands"][0]


@pytest.mark.asyncio
async def test_update_script_runs_the_binary_step_before_the_install():
    """Every update carries the step once, with the pins and the install function first
    boot uses: after the Caddyfile /health step, before the backup and rsync of the install,
    so a failure leaves the Hive on its version with its old Caddy."""
    script = await _update_script()
    step = caddy_binary_update_sh()
    assert script.count(step) == 1
    assert CADDY_INSTALL_SH in step
    for pin in (CADDY_VERSION, CADDY_ARM64_SHA512, CADDY_ARM64_BINARY_SHA512):
        assert pin in step
    assert script.index(health_exempt_fix_sh()) < script.index(step)
    assert script.index(step) < script.index("rsync -a --exclude='backend/.venv' /opt/swarmai/ "
                                             "/opt/swarmai.bak/")
    assert "caddyserver.com/api/download" not in script
    r = subprocess.run(["bash", "-n"], input=script, text=True, capture_output=True)
    assert r.returncode == 0, r.stderr


def test_the_binary_pin_is_a_sha512():
    assert len(CADDY_ARM64_BINARY_SHA512) == 128
    assert set(CADDY_ARM64_BINARY_SHA512) <= set("0123456789abcdef")
    assert CADDY_ARM64_BINARY_SHA512 != CADDY_ARM64_SHA512
