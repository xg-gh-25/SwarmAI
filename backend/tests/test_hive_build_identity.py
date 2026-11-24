"""The build identity a Hive package and a Host build carry (scripts/build_identity.py,
core/build_identity), and what a Hive reports of it.

Questions: (1) does one checkout get one identity whichever entry computes it (the CLI the
build scripts run, the Host's live compute), does a changed tracked source file change it and
a build output (untracked) or the desktop shell's own source not; (2) do release.sh, the
desktop backend build and the package check stamp, keep and require it, and does the release
publish its sidecar; (3) is a stamped BUILD.json read, and a forged one refused; (4) does
``admission-state`` carry the identity and the features schema this install reads.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from core import build_identity as bi

REPO = Path(__file__).resolve().parents[2]
TOOL = REPO / "backend" / "scripts" / "build_identity.py"

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="no git")


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True,
                   env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                        "GIT_COMMITTER_EMAIL": "t@t", "PATH": "/usr/bin:/bin", "HOME": str(root)})


def _checkout(tmp_path: Path) -> Path:
    root = tmp_path / "src"
    for rel, text in {"VERSION": "9.9.9\n", "backend/main.py": "print(1)\n", "hive/setup.sh": "echo\n",
                      "desktop/src/app.ts": "export {}\n", "desktop/src-tauri/main.rs": "fn main(){}\n",
                      "README.md": "not source\n"}.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "c1")
    return root


def _cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-I", str(TOOL), *args], capture_output=True, text=True, timeout=60)


def _write(root: Path, out: Path) -> dict:
    r = _cli("write", str(root), str(out), "--version", "9.9.9")
    assert r.returncode == 0, r.stderr
    return json.loads(out.read_text())


# ── (1) one identity per source ──

def test_one_identity_for_one_checkout_from_every_entry(tmp_path, monkeypatch):
    root = _checkout(tmp_path)
    stamped = _write(root, tmp_path / "BUILD.json")
    assert stamped["version"] == "9.9.9" and len(stamped["commit"]) == 40
    assert stamped["id"] == bi._tool().make_id(stamped["commit"], stamped["digest"])
    # the Host started from that checkout computes the same one (no BUILD.json beside VERSION)
    monkeypatch.setattr(bi, "_ROOT", root)
    bi._CACHE[:] = [False, None]
    assert bi.current_id() == stamped["id"]

    # a build output (untracked) and the desktop shell's own source do not change it
    (root / "backend" / "generated.json").write_text("{}")
    (root / "desktop" / "src-tauri" / "main.rs").write_text("fn main(){ changed() }\n")
    (root / "README.md").write_text("changed\n")
    assert _write(root, tmp_path / "b2.json")["id"] == stamped["id"]
    # a changed tracked source file does, committed or not
    (root / "backend" / "main.py").write_text("print(2)\n")
    dirty = _write(root, tmp_path / "b3.json")
    assert dirty["commit"] == stamped["commit"] and dirty["digest"] != stamped["digest"]
    assert dirty["id"] != stamped["id"]
    # the same content outside git: the same digest (git's own blob ids), no commit
    plain = tmp_path / "plain"
    shutil.copytree(root, plain, ignore=shutil.ignore_patterns(".git", "generated.json"))
    out = bi._tool().compute(plain)
    assert out["commit"] is None and out["digest"] == dirty["digest"]


def test_a_crlf_checkout_has_the_identity_of_the_lf_one(tmp_path):
    """A Windows builder (core.autocrlf) of the same commit stamps the same identity."""
    root = _checkout(tmp_path)
    want = _write(root, tmp_path / "lf.json")["id"]
    _git(root, "config", "core.autocrlf", "true")
    (root / "backend" / "main.py").write_bytes(b"print(1)\r\n")
    assert _write(root, tmp_path / "crlf.json")["id"] == want


# ── (2) the builds stamp it and the release carries it ──

def test_release_desktop_build_and_package_check_carry_the_identity(tmp_path):
    release = (REPO / "hive" / "release.sh").read_text()
    assert 'build_identity.py" write "${PROJECT_ROOT}" "${STAGING}/BUILD.json"' in release
    # before the archive is made, and the sidecar after it, then both go to the bucket
    assert release.index("${STAGING}/BUILD.json") < release.index('tar czf "${PACKAGE_NAME}.tar.gz"') \
        < release.index('build_identity.py" sidecar')
    assert '--key "v${VERSION}/build.json" --body "${BUILD_INFO}"' in release
    desktop = (REPO / "desktop" / "scripts" / "build-backend.sh").read_text()
    assert 'build_identity.py" write "$BACKEND_DIR/.." "$BUILD_DIR/BUILD.json"' in desktop
    assert "datas += [('BUILD.json', '.')]" in desktop
    assert 'check "^BUILD.json"' in (REPO / "hive" / "verify_package.sh").read_text()
    wf = (REPO / ".github" / "workflows" / "release.yml").read_text()
    assert "dist/swarmai-hive-*.build.json" in wf
    for script in ("hive/release.sh", "desktop/scripts/build-backend.sh", "hive/verify_package.sh"):
        assert subprocess.run(["bash", "-n", str(REPO / script)]).returncode == 0, script

    # the sidecar is BUILD.json plus the package's sha256
    root = _checkout(tmp_path)
    stamped = _write(root, tmp_path / "BUILD.json")
    pkg = tmp_path / "pkg.tar.gz"
    pkg.write_bytes(b"package bytes")
    r = _cli("sidecar", str(tmp_path / "BUILD.json"), str(pkg), str(tmp_path / "pkg.build.json"))
    assert r.returncode == 0, r.stderr
    import hashlib
    side = json.loads((tmp_path / "pkg.build.json").read_text())
    assert side["id"] == stamped["id"] and side["package_sha256"] == hashlib.sha256(b"package bytes").hexdigest()


# ── (3) reading it ──

def test_a_stamped_build_json_is_read_and_a_forged_one_refused(tmp_path, monkeypatch):
    root = tmp_path / "opt"
    root.mkdir()
    good = {"commit": "a" * 40, "digest": "b" * 64, "version": "9.9.9"}
    good["id"] = bi._tool().make_id(good["commit"], good["digest"])
    (root / "BUILD.json").write_text(json.dumps(good))
    monkeypatch.setattr(bi, "_ROOT", root)
    bi._CACHE[:] = [False, None]
    assert bi.current_id() == good["id"]
    assert bi.reported_id(good) == good["id"]
    forged = {**good, "id": "0" * 16}
    (root / "BUILD.json").write_text(json.dumps(forged))
    bi._CACHE[:] = [False, None]
    assert bi.current() is None
    assert bi.reported_id(forged) is None and bi.reported_id(None) is None and bi.reported_id("x") is None
    # the Elastic image tag of a build, and back
    assert bi.image_tag("v2.5.5", "0123456789abcdef") == "2.5.5-b0123456789abcdef"
    assert bi.image_tag("2.5.5", None) == "2.5.5"
    assert bi.display_version("2.5.5-b0123456789abcdef") == "2.5.5"


# ── (4) what a Hive reports ──

def test_admission_state_carries_the_build_and_the_features_schema(monkeypatch):
    from fastapi.testclient import TestClient

    from core.feature_map.files import SCHEMA_MAJOR
    from main import app
    ident = {"commit": "c" * 40, "digest": "d" * 64}
    ident["id"] = bi._tool().make_id(ident["commit"], ident["digest"])
    bi._CACHE[:] = [True, ident]
    body = TestClient(app).get("/api/chat/sessions/admission-state").json()
    assert body["build"] == ident and body["features_schema"] == SCHEMA_MAJOR
    bi._CACHE[:] = [True, None]
    body = TestClient(app).get("/api/chat/sessions/admission-state").json()
    assert "build" in body and body["build"] is None
