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
BASE = ["VERSION", "backend/main.py", "backend/pyproject.toml", "backend/core/a.py",
        "backend/database/a.py", "backend/routers/a.py", "backend/skills/s_x/SKILL.md",
        "backend/context/SOUL.md", "backend/hive/a.py", "desktop/dist/index.html",
        "desktop/dist/assets/a.js", "hive/swarmai-hive.sh", "hive/swarmai-hive.service",
        "hive/Caddyfile", "hive/bin/task-tools", "hive/seed/config-hive.json",
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
    _, uploads = _provision_download(monkeypatch, 200, "GOOD  swarmai-hive-v9.9.9-linux-arm64.tar.gz\n")
    assert len(uploads) == 1 and uploads[0]["Key"] == "v9.9.9/swarmai-hive-v9.9.9-linux-arm64.tar.gz"


@pytest.mark.parametrize("status,body", [(404, "Not Found"), (200, "0" * 64 + "  x"), (200, "")])
def test_a_package_without_a_matching_checksum_is_refused(monkeypatch, status, body):
    with pytest.raises(RuntimeError, match="checksum"):
        _provision_download(monkeypatch, status, body)
