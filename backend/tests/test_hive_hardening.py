"""Hive hardening: CloudFront stays on across stop/start, what is installed is checked,
the role's credentials work only from the Hive's Elastic IP, and the resume flag is set.

Questions: (1) do stop and start leave an enabled distribution alone, and does start
enable one an older version disabled; (2) does first boot unpack the package only when
checksums.txt names its hash, and stop before unpacking on a mismatch, a missing file
or a missing line; does the update script do the same, and show the ERROR line on the
card; (3) does _sync_release_to_s3 write checksums.txt (with a GitHub package, and for a
package already there without one); (4) is Caddy the pinned GitHub release, installed
only when its SHA-512 matches; (5) does the role policy carry the Deny outside the
Elastic IP when one is known, written after the EIP exists at deploy and with the stored
one at update, and never with an address that is not a public IPv4; (6) does the Hive's
unit set SWARM_RESUME_VIA_QUERY.

The shell tests run the rendered bash with ``aws`` and ``curl`` replaced by small
scripts that serve files from a temporary directory.
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import tarfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hive import provisioner as prov
from hive.provisioner import HiveProvisioner
from hive.user_data import CADDY_ARM64_SHA512, CADDY_INSTALL_SH, CADDY_VERSION, PACKAGE_CHECK_SH, render_user_data

REPO = Path(__file__).resolve().parents[2]
_TOOLS = ("bash", "sha256sum", "sha512sum", "awk", "tar")
needs_tools = pytest.mark.skipif(any(shutil.which(t) is None for t in _TOOLS),
                                 reason="needs " + ", ".join(_TOOLS))


# ── (1) CloudFront ──

def _cf(enabled: bool):
    cf = MagicMock()
    cf.get_distribution.return_value = {"ETag": "E1", "Distribution": {"DistributionConfig": {"Enabled": enabled}}}
    return cf


def _lifecycle(cf):
    from tests.test_hive_provisioner import _make_provisioner_and_mocks
    p, inst, acc, conn = _make_provisioner_and_mocks()
    ec2 = MagicMock()
    session = MagicMock()
    session.client.side_effect = lambda name, **kw: cf if name == "cloudfront" else ec2

    async def inline(fn, *a, **kw):
        return fn(*a, **kw)
    patches = [patch.object(p, "_get_instance", new_callable=AsyncMock, return_value=inst),
               patch.object(p, "_get_account", new_callable=AsyncMock, return_value=acc),
               patch.object(p, "_update_instance", new_callable=AsyncMock),
               patch.object(p, "_wait_healthy_via_ssm", new_callable=AsyncMock, return_value=True),
               patch.object(p, "_get_session", return_value=session),
               patch("hive.provisioner.aiosqlite.connect", return_value=conn),
               patch("hive.provisioner.asyncio.to_thread", side_effect=inline)]
    return p, patches


def _run_with(patches, coro_fn):
    for x in patches:
        x.start()
    try:
        return asyncio.run(coro_fn())
    finally:
        for x in reversed(patches):
            x.stop()


def test_stop_and_start_leave_cloudfront_alone():
    cf = _cf(enabled=True)
    p, patches = _lifecycle(cf)

    async def go():
        await p.stop("inst-1")
        await p.start("inst-1")
    _run_with(patches, go)
    cf.update_distribution.assert_not_called()


def test_stop_does_not_read_or_change_the_distribution():
    cf = _cf(enabled=True)
    p, patches = _lifecycle(cf)
    _run_with(patches, lambda: p.stop("inst-1"))
    cf.get_distribution.assert_not_called()
    cf.update_distribution.assert_not_called()


def test_start_enables_a_distribution_an_older_version_disabled():
    cf = _cf(enabled=False)
    p, patches = _lifecycle(cf)
    _run_with(patches, lambda: p.start("inst-1"))
    cf.update_distribution.assert_called_once()
    assert cf.update_distribution.call_args.kwargs["DistributionConfig"]["Enabled"] is True
    assert cf.update_distribution.call_args.kwargs["IfMatch"] == "E1"


# ── (2) the package check ──

PKG = "swarmai-hive-v1.2.3-linux-arm64.tar.gz"


def _fake_aws(tmp: Path) -> dict:
    """A bin dir whose ``aws s3 cp s3://<bucket>/<key> <dest>`` copies <s3>/<bucket>/<key>
    (to stdout for ``-``)."""
    bindir, s3 = tmp / "bin", tmp / "s3"
    bindir.mkdir()
    (bindir / "aws").write_text(
        '#!/bin/bash\n'
        '[ "$1 $2" = "s3 cp" ] || exit 2\n'
        'src="${3#s3://}"; f="$FAKE_S3/$src"\n'
        '[ -f "$f" ] || { echo "fatal error: 404" >&2; exit 1; }\n'
        'if [ "$4" = "-" ]; then cat "$f"; else cp "$f" "$4"; fi\n')
    (bindir / "aws").chmod(0o755)
    return {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}", "FAKE_S3": str(s3)}


def _package(tmp: Path) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        data = b"print('hive')\n"
        info = tarfile.TarInfo("swarmai-hive-v1.2.3-linux-arm64/backend/main.py")
        info.size = len(data)
        t.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _bucket(tmp: Path, pkg: bytes, sums: str | None) -> None:
    d = tmp / "s3" / "bkt" / "v1.2.3"
    d.mkdir(parents=True)
    (d / PKG).write_bytes(pkg)
    if sums is not None:
        (d / "checksums.txt").write_text(sums)


def _check(tmp: Path, env: dict, file: Path) -> subprocess.CompletedProcess:
    script = PACKAGE_CHECK_SH + f'\nverify_hive_package "{file}" bkt 1.2.3 us-east-1\n'
    return subprocess.run(["bash", "-c", "set -euo pipefail\n" + script], env=env,
                          capture_output=True, text=True, timeout=30)


@needs_tools
def test_package_check_accepts_the_package_checksums_names(tmp_path):
    env, pkg = _fake_aws(tmp_path), _package(tmp_path)
    _bucket(tmp_path, pkg, f"{hashlib.sha256(pkg).hexdigest()}  {PKG}\n")
    f = tmp_path / "pkg.tar.gz"
    f.write_bytes(pkg)
    r = _check(tmp_path, env, f)
    assert r.returncode == 0, r.stderr
    assert "matches checksums.txt" in r.stdout


@needs_tools
def test_package_check_refuses_a_mismatch(tmp_path):
    env, pkg = _fake_aws(tmp_path), _package(tmp_path)
    _bucket(tmp_path, pkg, f"{hashlib.sha256(b'another package').hexdigest()}  {PKG}\n")
    f = tmp_path / "pkg.tar.gz"
    f.write_bytes(pkg)
    r = _check(tmp_path, env, f)
    assert r.returncode == 1
    assert r.stdout.startswith("ERROR: the Hive package v1.2.3 does not match its checksum")


@needs_tools
@pytest.mark.parametrize("sums,says", [
    (None, "could not be read"),
    (f"{'a' * 64}  swarmai-hive-v9.9.9-linux-arm64.tar.gz\n", "has no sha256 line"),
    ("not a checksum file\n", "has no sha256 line")])
def test_package_check_refuses_a_missing_or_foreign_checksum(tmp_path, sums, says):
    env, pkg = _fake_aws(tmp_path), _package(tmp_path)
    _bucket(tmp_path, pkg, sums)
    f = tmp_path / "pkg.tar.gz"
    f.write_bytes(pkg)
    r = _check(tmp_path, env, f)
    assert r.returncode == 1 and says in r.stdout and r.stdout.startswith("ERROR:")


def _first_boot_download(tmp: Path) -> str:
    """Step [3/9] of the rendered first-boot script, aimed at *tmp*: download, check,
    unpack."""
    ud = render_user_data(s3_bucket="bkt", version="1.2.3", auth_user="admin",
                          auth_hash="$2b$14$" + "a" * 53, region="us-east-1")
    begin = ud.index('echo "[3/9] Downloading hive package from S3..."')
    step = ud[begin:ud.index('chown -R "$SWARM_USER:$SWARM_USER" "$INSTALL_DIR"', begin)]
    assert "tar xzf" in step and "verify_hive_package /tmp/hive.tar.gz" in step
    return ("set -euo pipefail\n" + f'HIVE_S3_BUCKET=bkt; HIVE_VERSION=1.2.3; HIVE_REGION=us-east-1\n'
            + step.replace("/tmp/hive.tar.gz", str(tmp / "hive.tar.gz"))
            .replace('mkdir -p "$INSTALL_DIR"', f'INSTALL_DIR="{tmp / "opt"}"; mkdir -p "$INSTALL_DIR"'))


@needs_tools
@pytest.mark.parametrize("good", [True, False])
def test_first_boot_unpacks_only_a_package_that_matches(tmp_path, good):
    env, pkg = _fake_aws(tmp_path), _package(tmp_path)
    digest = hashlib.sha256(pkg if good else b"other").hexdigest()
    _bucket(tmp_path, pkg, f"{digest}  {PKG}\n")
    r = subprocess.run(["bash", "-c", _first_boot_download(tmp_path)], env=env,
                       capture_output=True, text=True, timeout=30)
    unpacked = (tmp_path / "opt" / "backend" / "main.py").exists()
    if good:
        assert r.returncode == 0 and unpacked, r.stderr
    else:
        assert r.returncode != 0 and not unpacked
        assert "ERROR: the Hive package v1.2.3 does not match its checksum" in r.stdout


async def _update_script(version: str = "1.2.3", ssm=("Success", ""), ec2=None,
                         **row) -> tuple[str, AsyncMock]:
    p = HiveProvisioner(Path("/tmp/test.db"))
    instance = {"id": "inst-1", "name": "test-hive", "account_ref": "acc-1", "region": "us-east-1",
                "ec2_instance_id": "i-abc", "s3_bucket": "bkt", "ec2_public_ip": "3.80.1.2", **row}
    sent: list = []
    ssm_client = MagicMock()
    ssm_client.send_command.side_effect = lambda **kw: sent.append(kw["Parameters"]["commands"][0]) or {
        "Command": {"CommandId": "c-1"}}
    ssm_client.get_command_invocation.return_value = {"Status": ssm[0], "StandardOutputContent": ssm[1]}
    session = MagicMock()
    iam = MagicMock()
    session.client.side_effect = lambda name, **kw: iam if name == "iam" else (
        ec2 if name == "ec2" and ec2 is not None else ssm_client)
    update = AsyncMock()

    async def inline(fn, *a, **kw):
        return fn(*a, **kw)
    cursor = MagicMock(rowcount=1)
    conn = AsyncMock()
    conn.execute = AsyncMock(return_value=cursor)
    conn.__aenter__ = AsyncMock(return_value=conn)
    conn.__aexit__ = AsyncMock(return_value=False)
    with patch.object(p, "_get_instance", new_callable=AsyncMock, return_value=instance), \
         patch.object(p, "_get_account", new_callable=AsyncMock, return_value={}), \
         patch.object(p, "_update_instance", update), \
         patch.object(p, "_sync_release_to_s3", new_callable=AsyncMock), \
         patch.object(p, "_wait_healthy_via_ssm", new_callable=AsyncMock, return_value=True), \
         patch.object(p, "_get_session", return_value=session), \
         patch("hive.provisioner.aiosqlite.connect", return_value=conn), \
         patch("hive.provisioner.asyncio.to_thread", side_effect=inline):
        try:
            await p.update("inst-1", version)
        except RuntimeError:
            pass
    update.iam = iam
    return sent[0], update


def _update_download(script: str, tmp: Path) -> str:
    """The update script's download → check → unpack, aimed at *tmp*."""
    start = script.index("rm -rf /tmp/hive-new /tmp/hive-update.tar.gz")
    end = script.index("tar xzf /tmp/hive-update.tar.gz --strip-components=1 -C /tmp/hive-new/")
    end = script.index("\n", end)
    part = script[start:end].replace("/tmp/hive-update.tar.gz", str(tmp / "u.tar.gz")) \
        .replace("/tmp/hive-new", str(tmp / "new"))
    return "set -euo pipefail\n" + part


@needs_tools
@pytest.mark.parametrize("good", [True, False])
def test_the_update_unpacks_only_a_package_that_matches(tmp_path, good):
    script, _ = asyncio.run(_update_script())
    env, pkg = _fake_aws(tmp_path), _package(tmp_path)
    digest = hashlib.sha256(pkg if good else b"other").hexdigest()
    _bucket(tmp_path, pkg, f"{digest}  {PKG}\n")
    r = subprocess.run(["bash", "-c", _update_download(script, tmp_path)], env=env,
                       capture_output=True, text=True, timeout=30)
    unpacked = (tmp_path / "new" / "backend" / "main.py").exists()
    if good:
        assert r.returncode == 0 and unpacked, r.stderr
    else:
        assert r.returncode != 0 and not unpacked
        assert "does not match its checksum" in r.stdout


def test_the_update_checks_the_package_before_it_touches_the_install():
    script, _ = asyncio.run(_update_script())
    lines = [ln.strip() for ln in script.splitlines()]
    check = lines.index("verify_hive_package /tmp/hive-update.tar.gz bkt 1.2.3 us-east-1")
    unpack = next(i for i, ln in enumerate(lines) if ln.startswith("tar xzf /tmp/hive-update.tar.gz"))
    # the install's rsync (the one that keeps .hive-bucket), not the backup's
    install = next(i for i, ln in enumerate(lines)
                   if ln.startswith("rsync -a --delete") and ".hive-bucket" in ln)
    assert check < unpack < install


def test_update_failure_shows_the_error_line():
    out = ("=== Updating to v1.2.3 ===\nBacking up current installation...\n"
           "ERROR: the Hive package v1.2.3 does not match its checksum (sha256 ab, checksums.txt says cd); "
           "nothing was installed\n")
    _, update = asyncio.run(_update_script(ssm=("Failed", out)))
    msgs = [c.kwargs.get("error_message") for c in update.call_args_list if c.kwargs.get("error_message")]
    assert msgs and msgs[-1].startswith("Update failed: Failed. ERROR: the Hive package v1.2.3 does not match")


def test_script_error_falls_back_to_the_output():
    assert prov._script_error("no error line here") == "no error line here"
    assert prov._script_error("ERROR: one\nok\nERROR: two\n") == "ERROR: two"


# ── (3) checksums.txt in the bucket ──

class _S3:
    def __init__(self, present=()):
        self.present, self.puts = set(present), []

    def head_object(self, Bucket, Key, **kw):
        if Key not in self.present:
            raise Exception("404")

    def put_object(self, Bucket, Key, Body, **kw):
        self.puts.append((Key, Body))


def _sync(monkeypatch, s3, sha=(200, None)):
    import httpx
    pkg = b"pkg"

    def handler(request):
        if request.url.path.endswith(".tar.gz"):
            return httpx.Response(200, content=pkg)
        if request.url.path.endswith(".sha256"):
            return httpx.Response(sha[0], text=sha[1] if sha[1] is not None else hashlib.sha256(pkg).hexdigest())
        return httpx.Response(404)
    real = httpx.AsyncClient
    monkeypatch.setattr(prov.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    session = MagicMock()
    session.client.return_value = s3
    p = HiveProvisioner.__new__(HiveProvisioner)
    asyncio.run(p._sync_release_to_s3(session, "swarmai-hive-123456789012-us-east-1", "1.2.3", "us-east-1"))
    return hashlib.sha256(pkg).hexdigest()


def test_a_package_already_in_the_bucket_without_checksums_gets_the_published_one(monkeypatch):
    s3 = _S3(present={f"v1.2.3/{PKG}"})
    digest = _sync(monkeypatch, s3)
    assert s3.puts == [("v1.2.3/checksums.txt", f"{digest}  {PKG}\n".encode())]


def test_a_package_with_its_checksums_is_left_alone(monkeypatch):
    s3 = _S3(present={f"v1.2.3/{PKG}", "v1.2.3/checksums.txt"})
    _sync(monkeypatch, s3)
    assert s3.puts == []


def test_a_package_without_any_checksum_is_refused(monkeypatch):
    s3 = _S3(present={f"v1.2.3/{PKG}"})
    with pytest.raises(RuntimeError, match="no checksums.txt"):
        _sync(monkeypatch, s3, sha=(404, "Not Found"))
    assert s3.puts == []


# ── (4) Caddy ──

def _fake_curl(tmp: Path, archive: bytes) -> dict:
    bindir = tmp / "cbin"
    bindir.mkdir()
    (tmp / "caddy.tgz").write_bytes(archive)
    (bindir / "curl").write_text(
        '#!/bin/bash\n'
        'echo "$@" >> "$FAKE_LOG"\n'
        'out=""; while [ $# -gt 0 ]; do [ "$1" = "-o" ] && { out="$2"; shift; }; shift; done\n'
        'cp "$FAKE_TGZ" "$out"\n')
    (bindir / "curl").chmod(0o755)
    return {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}", "FAKE_TGZ": str(tmp / "caddy.tgz"),
            "FAKE_LOG": str(tmp / "curl.log")}


def _caddy_archive() -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        data = b"#!/bin/sh\necho caddy\n"
        info = tarfile.TarInfo("caddy")
        info.size, info.mode = len(data), 0o755
        t.addfile(info, io.BytesIO(data))
    return buf.getvalue()


@needs_tools
@pytest.mark.parametrize("good", [True, False])
def test_caddy_is_installed_only_when_its_checksum_matches(tmp_path, good):
    archive = _caddy_archive()
    env = _fake_curl(tmp_path, archive)
    want = hashlib.sha512(archive).hexdigest() if good else hashlib.sha512(b"x").hexdigest()
    sh = CADDY_INSTALL_SH.replace(CADDY_ARM64_SHA512, want)
    dest = tmp_path / "usr-bin-caddy"
    r = subprocess.run(["bash", "-c", "set -euo pipefail\n" + sh + f'\ninstall_caddy "{dest}"\n'],
                       env=env, capture_output=True, text=True, timeout=30)
    url = (tmp_path / "curl.log").read_text()
    assert (f"https://github.com/caddyserver/caddy/releases/download/v{CADDY_VERSION}/"
            f"caddy_{CADDY_VERSION}_linux_arm64.tar.gz") in url
    if good:
        assert r.returncode == 0 and dest.exists() and os.access(dest, os.X_OK), r.stderr
    else:
        assert r.returncode != 0 and not dest.exists()
        assert r.stdout.startswith(f"ERROR: Caddy v{CADDY_VERSION} does not match its release checksum")


def test_first_boot_installs_the_pinned_caddy_and_nothing_else():
    ud = render_user_data(s3_bucket="bkt", version="1.2.3", auth_user="admin",
                          auth_hash="$2b$14$" + "a" * 53, region="us-east-1")
    assert "caddyserver.com/api/download" not in ud
    assert CADDY_INSTALL_SH in ud and "\ninstall_caddy /usr/bin/caddy\n" in ud
    assert re.fullmatch(r"\d+\.\d+\.\d+", CADDY_VERSION) and re.fullmatch(r"[0-9a-f]{128}", CADDY_ARM64_SHA512)
    assert ud.index("install_caddy /usr/bin/caddy") < ud.index("cat > /etc/caddy/Caddyfile")


# ── (5) the role policy ──

def _denied(policy: dict, ctx: dict) -> list[str]:
    """The Deny statements of *policy* that match request context *ctx*, evaluated the way
    IAM evaluates these operators (every operator and key of a Condition must hold; a
    negated operator holds on a missing key; Null tests presence)."""
    import ipaddress

    def holds(op: str, key: str, want: str) -> bool:
        have = ctx.get(key)
        if op == "Null":
            return (have is None) == (want == "true")
        if op == "Bool":
            return have is not None and str(have).lower() == want
        if op == "NotIpAddress":
            return have is None or ipaddress.ip_address(have) not in ipaddress.ip_network(want)
        if op == "StringNotEquals":
            return have is None or have != want
        raise AssertionError(f"operator {op} not modelled")
    out = []
    for st in policy["Statement"]:
        if st.get("Effect") != "Deny":
            continue
        if all(holds(op, k, v) for op, kv in st["Condition"].items() for k, v in kv.items()):
            out.append(st["Sid"])
    return out


EIP, VPC = "3.80.1.2", "vpc-0abc1234def567890"


@pytest.mark.parametrize("ctx,denied", [
    ({"aws:SourceIp": EIP, "aws:ViaAWSService": "false"}, []),                         # from the Hive
    ({"aws:SourceIp": "198.51.100.7", "aws:ViaAWSService": "false"}, ["DenyOutsideHiveElasticIp"]),
    ({"aws:SourceIp": "198.51.100.7", "aws:ViaAWSService": "true"}, []),               # a service on its behalf
    ({"aws:SourceVpc": VPC, "aws:VpcSourceIp": "172.31.5.9", "aws:ViaAWSService": "false"}, []),  # its endpoint
    ({"aws:SourceVpc": "vpc-0ffffffffffffffff", "aws:ViaAWSService": "false"}, ["DenyOutsideHiveVpc"]),
])
def test_policy_denies_use_outside_the_elastic_ip(ctx, denied):
    assert _denied(prov.hive_iam_policy(EIP, VPC), ctx) == denied


def test_without_a_known_vpc_endpoint_requests_are_refused():
    # closed, never open: credentials taken to someone else's VPC endpoint stay useless
    ctx = {"aws:SourceVpc": "vpc-0ffffffffffffffff", "aws:ViaAWSService": "false"}
    assert _denied(prov.hive_iam_policy(EIP), ctx) == ["DenyOutsideHiveElasticIp"]
    assert _denied(prov.hive_iam_policy(EIP, "not-a-vpc"), ctx) == ["DenyOutsideHiveElasticIp"]
    assert _denied(prov.hive_iam_policy(EIP), {"aws:SourceIp": EIP, "aws:ViaAWSService": "false"}) == []


def test_an_unreadable_vpc_is_logged(caplog):
    import logging
    caplog.set_level(logging.WARNING, logger="hive.provisioner")
    ec2 = MagicMock()
    ec2.describe_instances.return_value = {"Reservations": []}
    session = MagicMock()
    session.client.return_value = ec2
    assert prov.hive_network(session, "us-east-1", "i-abc") == (None, None, True)
    assert any("VPC of i-abc is not known" in r.getMessage() for r in caplog.records)


def test_a_vpc_read_that_keeps_failing_is_reported_as_not_read():
    ec2 = MagicMock()
    ec2.describe_instances.side_effect = [Exception("Throttling"), Exception("Throttling"),
                                          Exception("Throttling")]
    session = MagicMock()
    session.client.return_value = ec2
    with patch("hive.provisioner.time.sleep"):
        assert prov.hive_network(session, "us-east-1", "i-abc") == (None, None, False)
    assert ec2.describe_instances.call_count == 3


def test_a_vpc_read_that_fails_once_is_retried():
    ec2 = MagicMock()
    ec2.describe_instances.side_effect = [Exception("Throttling"),
                                          {"Reservations": [{"Instances": [{"VpcId": VPC}]}]}]
    session = MagicMock()
    session.client.return_value = ec2
    with patch("hive.provisioner.time.sleep"):
        assert prov.hive_network(session, "us-east-1", "i-abc") == (None, VPC, True)


def test_a_known_vpc_without_an_elastic_ip_still_refuses_other_endpoints():
    policy = prov.hive_iam_policy(None, VPC)
    assert _denied(policy, {"aws:SourceVpc": "vpc-0ffffffffffffffff", "aws:ViaAWSService": "false"}) == [
        "DenyOutsideHiveVpc"]
    assert _denied(policy, {"aws:SourceVpc": VPC, "aws:ViaAWSService": "false"}) == []


def test_policy_statements_and_the_shared_constant():
    policy = prov.hive_iam_policy(EIP, VPC)
    deny = {s["Sid"]: s for s in policy["Statement"] if s.get("Effect") == "Deny"}
    assert deny["DenyOutsideHiveElasticIp"]["Condition"] == {
        "NotIpAddress": {"aws:SourceIp": "3.80.1.2/32"}, "Null": {"aws:SourceVpc": "true"},
        "Bool": {"aws:ViaAWSService": "false"}}
    assert deny["DenyOutsideHiveVpc"]["Condition"] == {
        "StringNotEquals": {"aws:SourceVpc": VPC}, "Null": {"aws:SourceVpc": "false"},
        "Bool": {"aws:ViaAWSService": "false"}}
    # every Allow is the same as without them, and the shared constant is not changed
    assert [s for s in policy["Statement"] if s.get("Effect") == "Allow"] == prov.HIVE_IAM_POLICY["Statement"]
    assert all(s.get("Effect") == "Allow" for s in prov.HIVE_IAM_POLICY["Statement"])
    # every Allow is the same as without it, and the shared constant is not changed
    assert [s for s in policy["Statement"] if s.get("Effect") == "Allow"] == prov.HIVE_IAM_POLICY["Statement"]
    assert all(s.get("Effect") == "Allow" for s in prov.HIVE_IAM_POLICY["Statement"])


@pytest.mark.parametrize("ip", [None, "", "not-an-ip", "10.0.0.5", "192.168.1.1", "3.80.1.2/24", "::1",
                                "2600:1f18::1"])
def test_policy_without_a_public_ipv4_has_no_ip_statement(ip):
    policy = prov.hive_iam_policy(ip)
    assert all(s.get("Effect") == "Allow" for s in policy["Statement"])


def test_put_role_policy_writes_the_ip_statement():
    iam = MagicMock()
    prov._put_hive_role_policy(iam, "SwarmAI-Hive-demo", "3.80.1.2")
    doc = json.loads(iam.put_role_policy.call_args.kwargs["PolicyDocument"])
    assert doc["Statement"][-1]["Condition"]["NotIpAddress"]["aws:SourceIp"] == "3.80.1.2/32"


def _ec2(ip=None, vpc=VPC):
    ec2 = MagicMock()
    ec2.describe_addresses.return_value = {"Addresses": [{"PublicIp": ip}] if ip else []}
    ec2.describe_instances.return_value = {"Reservations": [{"Instances": [{"VpcId": vpc}]}]}
    return ec2


def _sids(update) -> dict:
    doc = json.loads(update.iam.put_role_policy.call_args.kwargs["PolicyDocument"])
    return {s["Sid"]: s for s in doc["Statement"] if s.get("Effect") == "Deny"}


def test_the_update_writes_the_policy_with_the_stored_elastic_ip():
    ec2 = _ec2()
    _, update = asyncio.run(_update_script(ec2=ec2))
    deny = _sids(update)
    assert deny["DenyOutsideHiveElasticIp"]["Condition"]["NotIpAddress"]["aws:SourceIp"] == "3.80.1.2/32"
    assert deny["DenyOutsideHiveVpc"]["Condition"]["StringNotEquals"]["aws:SourceVpc"] == VPC
    ec2.describe_addresses.assert_not_called()           # the stored one is used


def test_the_update_reads_the_elastic_ip_back_when_the_row_has_none(caplog):
    ec2 = _ec2(ip="3.80.1.9")
    _, update = asyncio.run(_update_script(ec2=ec2, ec2_public_ip=None, elastic_ip_alloc_id="eipalloc-1"))
    assert _sids(update)["DenyOutsideHiveElasticIp"]["Condition"]["NotIpAddress"]["aws:SourceIp"] == "3.80.1.9/32"
    ec2.describe_addresses.assert_called_once_with(AllocationIds=["eipalloc-1"])


def test_the_update_keeps_the_policy_when_the_vpc_cannot_be_read(caplog):
    import logging
    caplog.set_level(logging.WARNING, logger="hive.provisioner")
    ec2 = _ec2()
    ec2.describe_instances.side_effect = Exception("Throttling")
    with patch("hive.provisioner.time.sleep"):
        _, update = asyncio.run(_update_script(ec2=ec2))
    update.iam.put_role_policy.assert_not_called()
    assert any("VPC could not be read" in r.getMessage() for r in caplog.records)


def test_the_update_says_so_when_no_elastic_ip_is_known(caplog):
    import logging
    caplog.set_level(logging.WARNING, logger="hive.provisioner")
    _, update = asyncio.run(_update_script(ec2=_ec2(ip=None), ec2_public_ip=None, elastic_ip_alloc_id="eipalloc-1"))
    assert "DenyOutsideHiveElasticIp" not in _sids(update)
    assert any("no Elastic IP known" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("vpc_read", [True, False])
def test_deploy_writes_the_ip_policy_once_the_elastic_ip_exists(vpc_read):
    from tests.test_hive_origin_secret import FAKE_HASH, FakeCloudFront, FakeSession, _db_conn
    events: list = []
    p = HiveProvisioner(Path("/tmp/unused.db"))
    instance = {"id": "inst-1", "name": "demo", "account_ref": "acc-1",
                "region": "us-east-1", "version": "2.4.4", "instance_type": "m7g.xlarge"}
    p._get_instance = AsyncMock(return_value=instance)
    p._get_account = AsyncMock(return_value={"account_id": "123456789012"})
    p._update_instance = AsyncMock()
    p._get_session = MagicMock(return_value=FakeSession(FakeCloudFront(events)))
    p._resolve_version = AsyncMock(return_value="2.4.4")
    p._ensure_s3_bucket = AsyncMock(return_value="swarmai-hive-123456789012-us-east-1")
    p._sync_release_to_s3 = AsyncMock()
    p._create_iam_role = AsyncMock(side_effect=lambda *a: events.append("role") or "arn:role")
    p._create_instance_profile = AsyncMock(return_value="arn:profile")
    p._create_security_group = AsyncMock(return_value="sg-1")
    p._launch_ec2 = AsyncMock(side_effect=lambda *a, **kw: events.append("launch") or "i-1")
    p._allocate_elastic_ip = AsyncMock(side_effect=lambda *a: events.append("eip") or ("eipalloc-1", "3.80.1.2"))
    p._wait_healthy_via_tag = AsyncMock(return_value=True)
    p._get_ec2_public_dns = AsyncMock(return_value="ec2-3-80-1-2.compute-1.amazonaws.com")
    p._cleanup_resources = AsyncMock()
    with patch("hive.provisioner.aiosqlite.connect", return_value=_db_conn()), \
         patch("hive.access.deploy_access", new=AsyncMock(return_value=("key", FAKE_HASH, []))), \
         patch("hive.provisioner.asyncio.sleep", new=AsyncMock()), \
         patch("hive.provisioner.hive_network", return_value=(None, VPC if vpc_read else None, vpc_read)), \
         patch("hive.provisioner._put_hive_role_policy",
               side_effect=lambda iam, role, ip=None, vpc=None: events.append(("policy", role, ip, vpc))):
        asyncio.run(p.deploy("inst-1"))
    policy = [e for e in events if isinstance(e, tuple) and e[0] == "policy"]
    # an unread VPC still gets the closed policy (the Elastic IP Deny refusing endpoint
    # traffic too), never step 3's, which has no Deny
    assert policy == [("policy", "SwarmAI-Hive-demo", "3.80.1.2", VPC if vpc_read else None)]
    assert events.index("eip") < events.index(policy[0])


@pytest.mark.parametrize("vpc_read", [True, False])
def test_a_setup_timeout_after_an_unread_vpc_says_so(vpc_read):
    from tests.test_hive_origin_secret import FAKE_HASH, FakeCloudFront, FakeSession, _db_conn
    p = HiveProvisioner(Path("/tmp/unused.db"))
    instance = {"id": "inst-1", "name": "demo", "account_ref": "acc-1",
                "region": "us-east-1", "version": "2.4.4", "instance_type": "m7g.xlarge"}
    p._get_instance = AsyncMock(return_value=instance)
    p._get_account = AsyncMock(return_value={"account_id": "123456789012"})
    p._update_instance = AsyncMock()
    p._get_session = MagicMock(return_value=FakeSession(FakeCloudFront([])))
    p._resolve_version = AsyncMock(return_value="2.4.4")
    p._ensure_s3_bucket = AsyncMock(return_value="swarmai-hive-123456789012-us-east-1")
    p._sync_release_to_s3 = AsyncMock()
    p._create_iam_role = AsyncMock(return_value="arn:role")
    p._create_instance_profile = AsyncMock(return_value="arn:profile")
    p._create_security_group = AsyncMock(return_value="sg-1")
    p._launch_ec2 = AsyncMock(return_value="i-1")
    p._allocate_elastic_ip = AsyncMock(return_value=("eipalloc-1", "3.80.1.2"))
    p._wait_healthy_via_tag = AsyncMock(return_value=False)          # the setup never ends
    p._cleanup_resources = AsyncMock()
    p._forget_terminated_instance = AsyncMock()
    with patch("hive.provisioner.aiosqlite.connect", return_value=_db_conn()), \
         patch("hive.access.deploy_access", new=AsyncMock(return_value=("key", FAKE_HASH, []))), \
         patch("hive.provisioner.asyncio.sleep", new=AsyncMock()), \
         patch("hive.provisioner.hive_network", return_value=(None, VPC if vpc_read else None, vpc_read)), \
         patch("hive.provisioner._put_hive_role_policy"):
        asyncio.run(p.deploy("inst-1"))
    msg = [c.kwargs["error_message"] for c in p._update_instance.call_args_list if c.kwargs.get("error_message")][-1]
    assert msg.startswith("EC2 setup timeout")
    assert ("VPC could not be read" in msg) is (not vpc_read)


def test_the_refresh_says_what_it_did():
    with patch("hive.provisioner.hive_network", return_value=(None, VPC, True)), \
         patch("hive.provisioner._put_hive_role_policy"):
        assert prov._refresh_role_policy(MagicMock(), "us-east-1", "r", "i-1", EIP) == "written"
    with patch("hive.provisioner.hive_network", return_value=(None, None, False)), \
         patch("hive.provisioner._put_hive_role_policy") as put:
        assert prov._refresh_role_policy(MagicMock(), "us-east-1", "r", "i-1", EIP) == "kept"
        put.assert_not_called()
        assert prov._refresh_role_policy(MagicMock(), "us-east-1", "r", "i-1", EIP,
                                         keep_on_failed_read=False) == "closed"
        put.assert_called_once()
        # no valid Elastic IP: nothing can be denied, so it is not reported as closed
        assert prov._refresh_role_policy(MagicMock(), "us-east-1", "r", "i-1", "10.0.0.5",
                                         keep_on_failed_read=False) == "open"


# ── (6) the resume flag ──

def test_the_hive_unit_passes_a_resumed_history_in_the_query():
    unit = (REPO / "hive" / "swarmai-hive.service").read_text(encoding="utf-8")
    service = unit[unit.index("[Service]"):unit.index("[Install]")]
    env = [ln.strip() for ln in service.splitlines() if ln.strip().startswith("Environment=")]
    assert "Environment=SWARM_RESUME_VIA_QUERY=true" in env


# ── first boot: a wait long enough for it, and its console kept when it gives up ──

def test_the_first_boot_wait_covers_its_deadlines():
    """The deploy's wait is a hang guard: it outlasts every bounded step of the first boot
    (each step's own deadline) plus room for the unbounded ones, so a slow but working
    boot is never cut off. The boot's output also reaches the console, which is all that
    is left once a timed-out instance is gone."""
    from hive import user_data as ud
    bounded = ud.TREE_SITTER_SETUP_DEADLINE_S + ud.REPLAY_HOST_DEADLINE_S + ud.BACKEND_HEALTH_WAIT_S
    assert prov.FIRST_BOOT_WAIT_S >= bounded + prov.FIRST_BOOT_UNBOUNDED_S
    assert prov.FIRST_BOOT_WAIT_S == 900
    script = render_user_data(s3_bucket="b", version="2.5.5", auth_user="admin", auth_hash="$2b$14$" + "a" * 53,
                              region="us-east-1")
    assert f"for i in $(seq 1 {ud.BACKEND_HEALTH_WAIT_S}); do" in script
    assert "exec > >(tee -a /var/log/hive-setup.log) 2>&1" in script


def _timed_out_deploy(console):
    """A deploy whose first boot never tags ready; *console* is what GetConsoleOutput answers
    (an exception: it is refused). Returns (provisioner, events)."""
    import base64
    from tests.test_hive_origin_secret import FAKE_HASH, FakeCloudFront, FakeSession, _db_conn
    events: list = []

    class Ec2:
        def get_console_output(self, **kw):
            events.append(("console", kw))
            if isinstance(console, Exception):
                raise console
            return {"InstanceId": "i-1", "Output": base64.b64encode(console.encode()).decode()}

    class Session(FakeSession):
        def client(self, name, **kw):
            return Ec2() if name == "ec2" else super().client(name, **kw)
    p = HiveProvisioner(Path("/tmp/unused.db"))
    instance = {"id": "inst-1", "name": "demo", "account_ref": "acc-1",
                "region": "us-east-1", "version": "2.4.4", "instance_type": "m7g.xlarge"}
    p._get_instance = AsyncMock(return_value=instance)
    p._get_account = AsyncMock(return_value={"account_id": "123456789012"})
    p._update_instance = AsyncMock()
    p._get_session = MagicMock(return_value=Session(FakeCloudFront([])))
    p._resolve_version = AsyncMock(return_value="2.4.4")
    p._ensure_s3_bucket = AsyncMock(return_value="swarmai-hive-123456789012-us-east-1")
    p._sync_release_to_s3 = AsyncMock()
    p._create_iam_role = AsyncMock(return_value="arn:role")
    p._create_instance_profile = AsyncMock(return_value="arn:profile")
    p._create_security_group = AsyncMock(return_value="sg-1")
    p._launch_ec2 = AsyncMock(return_value="i-1")
    p._allocate_elastic_ip = AsyncMock(return_value=("eipalloc-1", "3.80.1.2"))
    p._wait_healthy_via_tag = AsyncMock(return_value=False)
    p._cleanup_resources = AsyncMock(side_effect=lambda *a, **kw: events.append("cleanup"))
    p._forget_terminated_instance = AsyncMock()
    with patch("hive.provisioner.aiosqlite.connect", return_value=_db_conn()), \
         patch("hive.access.deploy_access", new=AsyncMock(return_value=("key", FAKE_HASH, []))), \
         patch("hive.provisioner.asyncio.sleep", new=AsyncMock()), \
         patch("hive.provisioner.hive_network", return_value=(None, VPC, True)), \
         patch("hive.provisioner._put_hive_role_policy"):
        asyncio.run(p.deploy("inst-1"))
    return p, events


def _error_message(p):
    return [c.kwargs["error_message"] for c in p._update_instance.call_args_list if c.kwargs.get("error_message")][-1]


def test_a_setup_timeout_keeps_the_console_output():
    lines = [f"line {i}" for i in range(400)] + ["[4/9] Setting up Python environment...", "\x1b[0mstuck here"]
    p, events = _timed_out_deploy("\r\n".join(lines))
    assert p._wait_healthy_via_tag.await_args.kwargs["timeout"] == prov.FIRST_BOOT_WAIT_S
    msg = _error_message(p)
    assert msg.startswith("EC2 setup timeout") and "[4/9] Setting up Python environment..." in msg
    assert "stuck here" in msg and "\x1b" not in msg and "line 0\n" not in msg   # the tail, printable
    assert len(msg) <= 400 + prov.CONSOLE_TAIL_CHARS
    # read before the instance it comes from is cleaned up
    console = [i for i, e in enumerate(events) if isinstance(e, tuple) and e[0] == "console"]
    assert console and console[0] < events.index("cleanup")
    assert events[console[0]][1] == {"InstanceId": "i-1", "Latest": True}


def test_a_console_that_cannot_be_read_still_cleans_up():
    p, events = _timed_out_deploy(RuntimeError("UnauthorizedOperation"))
    assert _error_message(p).startswith("EC2 setup timeout — check /var/log/hive-setup.log.")
    assert "cleanup" in events
