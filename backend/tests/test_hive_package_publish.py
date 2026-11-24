"""The Hive package published with each release carries no workspace content.

Questions:
- does hive/verify_package.sh --public fail (exit 1) a package whose seed holds anything
  beyond config-hive.json and the 5 public context files (a DDD, any other file), and
  pass (exit 0) one that does not;
- does it fail a private context file in the seed, with or without --public;
- does hive/release.sh refuse --with-live-ddd under CI, and refuse an unknown option;
- does every read of this machine's workspace in release.sh sit inside the
  --with-live-ddd branch;
- does release.yml build the published package without --with-live-ddd, verify it
  with --public and install-and-start it (smoke_package.sh) before uploading it, and
  publish only after that job;
- does the provisioner refuse a downloaded package whose .sha256 is missing or differs.
"""
import json
import os
import re
import subprocess
import tarfile
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
VERIFY = REPO / "hive" / "verify_package.sh"
RELEASE = REPO / "hive" / "release.sh"


def _tar(tmp_path: Path, files: list[str]) -> Path:
    top = tmp_path / "swarmai-hive-v0.0.0-linux-arm64"
    for rel in files:
        f = top / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("x")
    out = tmp_path / "pkg.tar.gz"
    with tarfile.open(out, "w:gz") as t:
        t.add(top, arcname=top.name)
    return out


def _verify(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", str(VERIFY), *args], capture_output=True, text=True, timeout=60)


# the smallest package every structure check accepts
BASE = ["VERSION", "BUILD.json", "backend/main.py", "backend/pyproject.toml", "backend/core/a.py",
        "backend/database/a.py", "backend/routers/a.py", "backend/skills/s_x/SKILL.md",
        "backend/context/SOUL.md", "backend/hive/a.py", "desktop/dist/index.html",
        "desktop/dist/assets/a.js", "hive/swarmai-hive.sh", "hive/swarmai-hive.service",
        "hive/Caddyfile", "hive/bin/task-tools", "hive/replay-host.sh", "hive/seed/config-hive.json",
        *[f"hive/seed/context/{n}.md" for n in ("SWARMAI", "IDENTITY", "SOUL", "AGENT", "SELF")]]


def test_a_clean_package_passes_the_public_check(tmp_path):
    r = _verify("--public", str(_tar(tmp_path, BASE)))
    assert r.returncode == 0, r.stdout
    assert "✅ Seed holds only config + public context" in r.stdout


@pytest.mark.parametrize("extra", [
    "hive/seed/Projects/SwarmAI/2-understanding/TECH.md",
    "hive/seed/Knowledge/notes.md",
    "hive/seed/context/NOTES.md",
])
def test_the_public_check_fails_anything_else_in_the_seed(tmp_path, extra):
    pkg = _tar(tmp_path, BASE + [extra])
    r = _verify("--public", str(pkg))
    assert r.returncode == 1 and "❌ Seed holds more than config + public context" in r.stdout
    assert _verify(str(pkg)).returncode == 0  # a local package may carry its own DDD


@pytest.mark.parametrize("public", [True, False])
def test_a_private_context_file_in_the_seed_fails(tmp_path, public):
    r = _verify(*(["--public"] if public else []), str(_tar(tmp_path, BASE + ["hive/seed/context/MEMORY.md"])))
    assert r.returncode == 1 and "❌ No private MEMORY.md in the seed" in r.stdout


def test_release_refuses_live_ddd_in_ci_and_unknown_options():
    env = {**os.environ, "CI": "true"}
    r = subprocess.run(["bash", str(RELEASE), "--with-live-ddd", "0.0.0"], capture_output=True,
                       text=True, timeout=60, env=env)
    assert r.returncode == 2 and "never runs in CI" in r.stderr
    r = subprocess.run(["bash", str(RELEASE), "--bogus"], capture_output=True, text=True, timeout=60)
    assert r.returncode == 2 and "unknown option" in r.stderr


def test_the_default_package_does_not_seed_the_live_ddd():
    # flipping this default is the one edit no CI check before verify --public would see
    assert re.search(r"^WITH_LIVE_DDD=0$", RELEASE.read_text(), re.M)


def test_workspace_reads_are_inside_the_live_ddd_branch():
    text = RELEASE.read_text()
    start = text.index('if [ "${WITH_LIVE_DDD}" = "1" ]; then\necho')
    end = text.index('else\n    echo "[release] Building hive seed (config', start)
    for m in re.finditer(r"SWARM_DATA_DIR|\.swarm-ai|LIVE_DDD", text):
        if text[m.start() - 1] == "_" or text[:m.start()].endswith("WITH_"):
            continue  # WITH_LIVE_DDD, the flag itself
        line_start = text.rfind("\n", 0, m.start()) + 1
        if text[line_start:].lstrip().startswith("#"):
            continue
        assert start < m.start() < end, f"workspace read outside --with-live-ddd: {text[line_start:m.end() + 40]!r}"


def test_release_workflow_publishes_only_a_verified_public_package():
    wf = yaml.safe_load((REPO / ".github" / "workflows" / "release.yml").read_text())
    jobs = wf["jobs"]
    steps = jobs["build-hive"]["steps"]
    runs = [s.get("run", "") for s in steps]
    package = [r for r in runs if "hive/release.sh" in r]
    assert package and all("--with-live-ddd" not in r for r in package)
    verify = next(i for i, r in enumerate(runs) if "verify_package.sh --public" in r)
    smoke = next(i for i, r in enumerate(runs) if "hive/smoke_package.sh" in r)
    upload = next(i for i, s in enumerate(steps) if "upload-artifact" in s.get("uses", ""))
    assert runs.index(package[0]) < verify < smoke < upload
    assert "build-hive" in jobs["publish"]["needs"]
    pub = " ".join(s.get("run", "") for s in jobs["publish"]["steps"])
    assert "hive-package" in pub


# ── the provisioner checks the published checksum before a package reaches S3 ──

def _provision_download(monkeypatch, sha_status: int, sha_body: str):
    import asyncio
    import hashlib
    import httpx
    from hive import provisioner as prov

    pkg = b"package-bytes"
    good = hashlib.sha256(pkg).hexdigest()

    def handler(request: httpx.Request):
        if request.url.path.endswith(".tar.gz"):
            return httpx.Response(200, content=pkg)
        if request.url.path.endswith(".sha256"):
            return httpx.Response(sha_status, text=sha_body.replace("GOOD", good))
        return httpx.Response(404)

    real = httpx.AsyncClient
    monkeypatch.setattr(prov.httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    uploads = []

    class S3:
        def head_object(self, **kw):
            raise Exception("absent")

        def put_object(self, **kw):
            uploads.append(kw)

    class Session:
        def client(self, *a, **kw):
            return S3()

    p = prov.HiveProvisioner.__new__(prov.HiveProvisioner)
    coro = p._sync_release_to_s3(Session(), "swarmai-hive-1234-us-east-1", "9.9.9", "us-east-1")
    return asyncio.run(coro), uploads


def test_a_package_matching_its_checksum_is_uploaded(monkeypatch):
    import hashlib
    _, uploads = _provision_download(monkeypatch, 200, "GOOD  swarmai-hive-v9.9.9-linux-arm64.tar.gz\n")
    # the package, then the checksums.txt a Hive checks it against (hive/user_data)
    assert [u["Key"] for u in uploads] == ["v9.9.9/swarmai-hive-v9.9.9-linux-arm64.tar.gz",
                                          "v9.9.9/checksums.txt"]
    good = hashlib.sha256(b"package-bytes").hexdigest()
    assert uploads[1]["Body"] == f"{good}  swarmai-hive-v9.9.9-linux-arm64.tar.gz\n".encode()


@pytest.mark.parametrize("status,body", [(404, "Not Found"), (200, "0" * 64 + "  x"), (200, "")])
def test_a_package_without_a_matching_checksum_is_refused(monkeypatch, status, body):
    with pytest.raises(RuntimeError, match="checksum"):
        _provision_download(monkeypatch, status, body)


# ── the bucket holds the package of the Host's build (core/build_identity) ──

import httpx as _httpx  # noqa: E402

_REAL_ASYNC_CLIENT = _httpx.AsyncClient

def _ident(commit_char: str) -> dict:
    from core import build_identity as bi
    d = {"commit": commit_char * 40, "digest": "e" * 64}
    d["id"] = bi._tool().make_id(d["commit"], d["digest"])
    return d


class _Bucket:
    """S3 with objects in a dict: head_object, get_object, put_object."""

    def __init__(self, objects=None):
        self.objects = dict(objects or {})
        self.puts: list[str] = []

    def head_object(self, Bucket, Key, **kw):
        if Key not in self.objects:
            raise Exception("404")
        return {}

    def get_object(self, Bucket, Key, **kw):
        import io
        if Key not in self.objects:
            raise Exception("NoSuchKey")
        return {"Body": io.BytesIO(self.objects[Key])}

    def put_object(self, Bucket, Key, Body, **kw):
        self.puts.append(Key)
        self.objects[Key] = Body


def _bucket_with(pkg: bytes, ident) -> _Bucket:
    import hashlib
    name = "swarmai-hive-v9.9.9-linux-arm64.tar.gz"
    sha = hashlib.sha256(pkg).hexdigest()
    objs = {f"v9.9.9/{name}": pkg, "v9.9.9/checksums.txt": f"{sha}  {name}\n".encode()}
    if ident is not None:
        objs["v9.9.9/build.json"] = json.dumps({**ident, "package_sha256": sha}).encode()
    return _Bucket(objs)


def _sync(monkeypatch, bucket: _Bucket, build, strict: bool, dist=None, github=None):
    """Run _sync_release_to_s3 against *bucket*, with *dist* as the local package folder and
    *github* ({path suffix: (status, bytes)}) as the release."""
    import asyncio

    import httpx
    from hive import provisioner as prov

    def handler(request: httpx.Request):
        for suffix, (status, body) in (github or {}).items():
            if request.url.path.endswith(suffix):
                return httpx.Response(status, content=body)
        return httpx.Response(404)

    real = _REAL_ASYNC_CLIENT                 # a second _sync in one test wraps the real one too
    monkeypatch.setattr(prov.httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr(prov, "_local_package_dirs", lambda: [dist] if dist else [])

    class Session:
        def client(self, *a, **kw):
            return bucket

    p = prov.HiveProvisioner.__new__(prov.HiveProvisioner)
    return asyncio.run(p._sync_release_to_s3(Session(), "swarmai-hive-1234-us-east-1", "9.9.9", "us-east-1",
                                             build=build, strict=strict))


def _local(tmp_path, pkg: bytes, ident, sha=None):
    import hashlib
    dist = tmp_path / "dist"
    dist.mkdir(exist_ok=True)
    name = "swarmai-hive-v9.9.9-linux-arm64"
    (dist / f"{name}.tar.gz").write_bytes(pkg)
    (dist / f"{name}.build.json").write_text(json.dumps(
        {**ident, "version": "9.9.9", "package_sha256": sha or hashlib.sha256(pkg).hexdigest()}))
    return dist


def test_same_version_other_build_in_the_bucket_is_replaced_by_the_hosts_package(monkeypatch, tmp_path):
    import hashlib
    host, other = _ident("a"), _ident("b")
    bucket = _bucket_with(b"old package", other)
    got = _sync(monkeypatch, bucket, host, strict=True, dist=_local(tmp_path, b"new package", host))
    assert got == host["id"]
    key = "v9.9.9/swarmai-hive-v9.9.9-linux-arm64.tar.gz"
    # the package, then its checksum, then the identity that names both
    assert bucket.puts == [key, "v9.9.9/checksums.txt", "v9.9.9/build.json"]
    assert bucket.objects[key] == b"new package"
    sha = hashlib.sha256(b"new package").hexdigest()
    assert bucket.objects["v9.9.9/checksums.txt"].decode().split()[0] == sha
    side = json.loads(bucket.objects["v9.9.9/build.json"])
    assert side["id"] == host["id"] and side["package_sha256"] == sha
    # a bucket package without any build.json (an older SwarmAI put it there) is replaced too
    bucket = _bucket_with(b"old package", None)
    assert _sync(monkeypatch, bucket, host, strict=True, dist=_local(tmp_path, b"new package", host)) == host["id"]
    assert bucket.objects[key] == b"new package"


def test_a_bucket_package_of_the_hosts_build_is_reused(monkeypatch, tmp_path):
    host = _ident("a")
    bucket = _bucket_with(b"pkg", host)
    assert _sync(monkeypatch, bucket, host, strict=True) == host["id"]
    assert bucket.puts == []
    # a build.json whose package_sha256 is not the checksum beside it names another package
    bucket.objects["v9.9.9/checksums.txt"] = f"{'0' * 64}  swarmai-hive-v9.9.9-linux-arm64.tar.gz\n".encode()
    with pytest.raises(RuntimeError, match=host["id"]):
        _sync(monkeypatch, bucket, host, strict=True)


def test_a_published_package_of_the_hosts_build_replaces_the_bucket_one(monkeypatch):
    import hashlib
    host, other = _ident("a"), _ident("b")
    bucket = _bucket_with(b"old package", other)
    pkg = b"released package"
    sha = hashlib.sha256(pkg).hexdigest()
    name = "swarmai-hive-v9.9.9-linux-arm64.tar.gz"
    github = {".build.json": (200, json.dumps({**host, "package_sha256": sha}).encode()),
              ".tar.gz.sha256": (200, f"{sha}  {name}\n".encode()), ".tar.gz": (200, pkg)}
    assert _sync(monkeypatch, bucket, host, strict=True, github=github) == host["id"]
    assert bucket.objects[f"v9.9.9/{name}"] == pkg and bucket.puts[-1] == "v9.9.9/build.json"


def test_same_version_without_a_package_of_the_host_build_changes_nothing(monkeypatch, tmp_path):
    host, other = _ident("a"), _ident("b")
    bucket = _bucket_with(b"old package", other)
    # a local package that is not what its sidecar says is no package of that build
    dist = _local(tmp_path, b"new package", host, sha="0" * 64)
    with pytest.raises(RuntimeError, match=f"build {host['id']}"):
        _sync(monkeypatch, bucket, host, strict=True, dist=dist)
    assert bucket.puts == []
    # a Hive a version behind still gets the version's package (progress), unchanged
    assert _sync(monkeypatch, bucket, host, strict=False) == other["id"]
    assert bucket.puts == []


def test_update_names_the_hosts_build_and_sends_nothing_when_the_bucket_cannot_hold_it():
    import asyncio
    from unittest.mock import AsyncMock, MagicMock, patch

    from core import build_identity as bi
    from hive.provisioner import HiveProvisioner
    host = _ident("a")
    bi._CACHE[:] = [True, host]
    p = HiveProvisioner(Path("/tmp/test.db"))
    ssm = MagicMock()
    session = MagicMock()
    session.client.return_value = ssm
    for row_version, strict in (("9.9.9", True), ("9.9.8", False)):
        instance = {"id": "inst-1", "name": "test-hive", "account_ref": "acc-1", "version": row_version,
                    "region": "us-east-1", "ec2_instance_id": "i-abc123", "s3_bucket": "swarmai-hive-test"}
        sync = AsyncMock(side_effect=RuntimeError("no package of build x"))
        with patch.object(p, "_get_instance", new_callable=AsyncMock, return_value=instance), \
             patch.object(p, "_get_account", new_callable=AsyncMock, return_value={}), \
             patch.object(p, "_update_instance", new_callable=AsyncMock) as upd, \
             patch.object(p, "_sync_release_to_s3", sync), \
             patch.object(p, "_get_session", return_value=session), \
             patch("hive.provisioner._refresh_role_policy"):
            with pytest.raises(RuntimeError, match="no package of build x"):
                asyncio.run(p.update("inst-1", "9.9.9", claimed=True))
        assert sync.call_args.kwargs == {"build": host, "strict": strict}
        ssm.send_command.assert_not_called()
        assert "the Hive was not changed" in upd.call_args.kwargs["error_message"]


def test_a_release_download_that_fails_falls_back_or_refuses(monkeypatch):
    """The release names the Host's build, then its download fails: a Hive a version behind
    still gets the version's package (deploy, update), a Hive at the version is refused."""
    import hashlib

    from hive import provisioner as prov
    host, other = _ident("a"), _ident("b")
    pkg = b"released package"
    sha = hashlib.sha256(pkg).hexdigest()

    def broken(request):
        if request.url.path.endswith(".build.json"):
            return _httpx.Response(200, content=json.dumps({**host, "package_sha256": sha}).encode())
        raise _httpx.ConnectError("network down")
    real = _REAL_ASYNC_CLIENT
    bucket = _bucket_with(b"old package", other)
    monkeypatch.setattr(prov, "_local_package_dirs", lambda: [])
    monkeypatch.setattr(prov.httpx, "AsyncClient",
                        lambda **kw: real(transport=_httpx.MockTransport(broken), **kw))

    class Session:
        def client(self, *a, **kw):
            return bucket
    import asyncio
    p = prov.HiveProvisioner.__new__(prov.HiveProvisioner)
    got = asyncio.run(p._sync_release_to_s3(Session(), "swarmai-hive-1234-us-east-1", "9.9.9", "us-east-1",
                                            build=host, strict=False))
    assert got == other["id"] and bucket.puts == []
    with pytest.raises(prov.BuildPackageMissing):
        asyncio.run(p._sync_release_to_s3(Session(), "swarmai-hive-1234-us-east-1", "9.9.9", "us-east-1",
                                          build=host, strict=True))
