"""The installer carries the backend (design 2026-10-24-swarmai-usable-by-anyone §3.1, G2).

What is tested:
- write_install_manifest.py lists every staged backend file plus desktop/resources and
  daemon/ files with sha256, paths relative to Tauri's resource dir, in exactly the
  format scripts/verify_installer.sh C3 checks (``shasum -a 256 -c`` from Resources);
  it refuses a stage without the backend executable;
- every build path bundles the staged backend: tauri.bundle.conf.json maps it to
  python-backend/ and the manifest to install-manifest.json, ad-hoc signs on macOS
  (signingIdentity "-", the S1 finding C13), and tauri-build.sh passes that config in
  both branches; release.yml's macOS job builds through tauri-build.sh; prod.sh no
  longer injects the backend after the build;
- build-backend.sh stages to src-tauri/bundle-backend and writes the manifest; the stage
  dir is gitignored.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DESKTOP = ROOT / "desktop"


def _run_manifest(stage, resources, target="aarch64-apple-darwin"):
    return subprocess.run([sys.executable, str(DESKTOP / "scripts" / "write_install_manifest.py"),
                           str(stage), str(resources), "9.9.9", target],
                          capture_output=True, text=True, timeout=60)


def test_manifest_matches_what_verify_installer_checks(tmp_path):
    stage, res = tmp_path / "stage", tmp_path / "resources"
    (stage / "python-backend" / "_internal" / "lib").mkdir(parents=True)
    (stage / "python-backend" / "python-backend").write_bytes(b"\x7fELF exe")
    (stage / "python-backend" / "_internal" / "lib" / "a.so").write_bytes(b"lib")
    (res / "daemon").mkdir(parents=True)
    (res / "seed.db").write_bytes(b"seed")
    (res / "daemon" / "swarmai_backend.sh").write_text("#!/bin/sh\n")
    (res / ".DS_Store").write_bytes(b"x")
    assert _run_manifest(stage, res).returncode == 0
    m = json.loads((stage / "install-manifest.json").read_text())
    paths = {f["path"] for f in m["files"]}
    assert paths == {"python-backend/python-backend", "python-backend/_internal/lib/a.so",
                     "seed.db", "daemon/swarmai_backend.sh"}
    assert m["executable"] == "python-backend/python-backend" and m["version"] == "9.9.9"
    # lay the files out the way the installer does and check them the way G2 does
    app_res = tmp_path / "installed" / "Resources"
    shutil.copytree(stage / "python-backend", app_res / "python-backend")
    shutil.copytree(res / "daemon", app_res / "daemon")
    shutil.copy(res / "seed.db", app_res / "seed.db")
    lines = "".join(f"{f['sha256']}  {f['path']}\n" for f in m["files"])
    r = subprocess.run(["shasum", "-a", "256", "-c", "--quiet"], input=lines, text=True,
                       cwd=app_res, capture_output=True)
    assert r.returncode == 0, r.stdout + r.stderr


def test_manifest_refuses_a_stage_without_the_backend(tmp_path):
    (tmp_path / "stage" / "python-backend").mkdir(parents=True)
    (tmp_path / "res").mkdir()
    r = _run_manifest(tmp_path / "stage", tmp_path / "res")
    assert r.returncode != 0 and "missing" in (r.stdout + r.stderr)


def test_every_build_path_bundles_the_staged_backend():
    conf = json.loads((DESKTOP / "src-tauri" / "tauri.bundle.conf.json").read_text())
    res = conf["bundle"]["resources"]
    assert res["bundle-backend/python-backend"] == "python-backend/"
    assert res["bundle-backend/install-manifest.json"] == "install-manifest.json"
    base = json.loads((DESKTOP / "src-tauri" / "tauri.conf.json").read_text())["bundle"]["resources"]
    assert all(res.get(k) == v for k, v in base.items())      # the overlay keeps the base map
    assert conf["bundle"]["macOS"]["signingIdentity"] == "-"
    tb = (DESKTOP / "scripts" / "tauri-build.sh").read_text()
    builds = [l for l in tb.splitlines() if "npm run tauri build" in l]
    assert len(builds) == 2 and all("--config src-tauri/tauri.bundle.conf.json" in l for l in builds)
    rel = (ROOT / ".github" / "workflows" / "release.yml").read_text()
    assert "npm run tauri build" not in rel and "scripts/tauri-build.sh" in rel
    prod = (ROOT / "prod.sh").read_text()
    assert "rsync -a --delete \"$backend_src/\"" not in prod
    bb = (DESKTOP / "scripts" / "build-backend.sh").read_text()
    assert "bundle-backend" in bb and "write_install_manifest.py" in bb
    ignored = subprocess.run(["git", "-C", str(ROOT), "check-ignore", "-q",
                              "desktop/src-tauri/bundle-backend/x"])
    assert ignored.returncode == 0
