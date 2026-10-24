"""Release targets and their installer gate (design 2026-10-24-swarmai-usable-by-anyone
§3.1, §5.1, §7 r14).

- release.yml builds macOS arm64 / x64, Windows x64 and Linux x64 (AppImage) through the one
  developer build path (build-backend.sh -> npm run build -> tauri-build.sh), runs
  scripts/verify_installer.sh on each installer in its own job, and publishes only when every
  build and every verification succeeded (no `always()`, no partial release).
- collect-installer.sh gives each target's files unique names (both macOS builds would ship a
  SwarmAI.app.tar.gz).
- verify_installer.sh reads install-manifest.json without python/jq (awk) the same way the
  build writes it, and deploys the way runtime_deploy.rs does (pointer file `current`).
- utils/runtime_paths uses the Rust runtime root on Windows.
"""
import json
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
WF = ROOT / ".github" / "workflows" / "release.yml"
VERIFY = ROOT / "scripts" / "verify_installer.sh"
COLLECT = ROOT / ".github" / "scripts" / "collect-installer.sh"
TARGETS = {"macos-arm64", "macos-x64", "windows-x64", "linux-x64"}


def _wf():
    return yaml.safe_load(WF.read_text())


def test_four_targets_build_through_the_developer_path():
    build = _wf()["jobs"]["build"]
    legs = {i["name"]: i for i in build["strategy"]["matrix"]["include"]}
    assert set(legs) == TARGETS
    assert legs["linux-x64"]["bundles"] == "appimage"
    assert legs["macos-x64"]["target"] == "x86_64-apple-darwin"
    runs = "\n".join(s.get("run", "") for s in build["steps"])
    for cmd in ("bash scripts/build-backend.sh", "npm run build",
                "bash scripts/tauri-build.sh --target ${{ matrix.target }}"):
        assert cmd in runs
    assert "build:all" not in runs   # one path for every target


def test_every_installer_is_verified_and_publish_needs_all_of_it():
    jobs = _wf()["jobs"]
    verify = jobs["verify"]
    assert {i["name"] for i in verify["strategy"]["matrix"]["include"]} == TARGETS
    assert "scripts/verify_installer.sh" in "\n".join(s.get("run", "") for s in verify["steps"])
    pub = jobs["publish"]
    assert set(pub["needs"]) >= {"build", "verify"}
    assert "if" not in pub   # no always(): a failed target or verification blocks the release
    assert "gen-latest-json.sh" in "\n".join(s.get("run", "") for s in pub["steps"])


def test_collect_renames_macos_bundles_per_arch(tmp_path):
    for name, arch in (("macos-arm64", "aarch64"), ("macos-x64", "x64")):
        b = tmp_path / name / "bundle"
        (b / "dmg").mkdir(parents=True)
        (b / "macos").mkdir()
        (b / "dmg" / f"SwarmAI_1.0.0_{arch}.dmg").write_text("d")
        (b / "macos" / "SwarmAI.app.tar.gz").write_text("t")
        (b / "macos" / "SwarmAI.app.tar.gz.sig").write_text("s")
        out = tmp_path / name / "out"
        subprocess.run(["bash", str(COLLECT), name, "1.0.0", str(b), str(out)], check=True,
                       capture_output=True)
        assert sorted(p.name for p in out.iterdir()) == sorted([
            f"SwarmAI_1.0.0_{arch}.dmg", f"SwarmAI_1.0.0_{arch}.app.tar.gz",
            f"SwarmAI_1.0.0_{arch}.app.tar.gz.sig"])


def test_collect_fails_without_the_installer(tmp_path):
    (tmp_path / "appimage").mkdir()
    r = subprocess.run(["bash", str(COLLECT), "linux-x64", "1.0.0", str(tmp_path), str(tmp_path / "o")],
                       capture_output=True, text=True)
    assert r.returncode != 0 and "AppImage" in r.stderr


def _manifest_lines_fn() -> str:
    text = VERIFY.read_text()
    start = text.index("manifest_lines() {")
    return text[start:text.index("\n}\n", start) + 3]


def test_verify_reads_the_manifest_the_build_writes(tmp_path):
    stage = tmp_path / "stage" / "python-backend"
    (stage / "_internal").mkdir(parents=True)
    (stage / "python-backend").write_text("bin")
    (stage / "_internal" / "lib.so").write_text("lib")
    res = tmp_path / "res"
    res.mkdir()
    (res / "seed.db").write_text("db")
    subprocess.run([sys.executable, str(ROOT / "desktop" / "scripts" / "write_install_manifest.py"),
                    str(tmp_path / "stage"), str(res), "1.2.3", "x86_64-unknown-linux-gnu"], check=True)
    manifest = tmp_path / "stage" / "install-manifest.json"
    want = {f"{f['sha256']}  {f['path']}" for f in json.loads(manifest.read_text())["files"]}
    out = subprocess.run(["bash", "-c", _manifest_lines_fn() + f'\nmanifest_lines "{manifest}"'],
                         capture_output=True, text=True, check=True).stdout
    assert set(out.split("\n")) - {""} == want and len(want) == 3


def test_verify_deploys_with_a_pointer_file_like_the_app():
    text = VERIFY.read_text()
    assert "ln -s" not in text
    assert "printf '%s' \"$VERSION\" > \"$RT_ROOT/current\"" in text
    assert 'RT_ROOT="$TEST_LOCALAPPDATA/SwarmAI/runtime"' in text   # Windows root, as in Rust


def test_windows_runtime_root_matches_rust(monkeypatch, tmp_path):
    from utils import runtime_paths as rp
    monkeypatch.setattr(rp.sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert rp.runtime_root() == tmp_path / "SwarmAI" / "runtime"
