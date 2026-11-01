"""The installer bundles exactly the git-tracked files of desktop/resources (P1).

A glob such as "../resources/*" also picks up files a developer has locally but git ignores
(a user-mcp-servers.json with their own paths), so a local build would ship different files
from a CI build. tauri.conf.json therefore lists every tracked file by name. The release
overlay tauri.bundle.conf.json is merged into it key by key (`tauri build --config`, JSON
merge patch), so a glob left in either file would still be bundled.
"""
import json
import subprocess
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[2]
TAURI = ROOT / "desktop" / "src-tauri"
CONFIGS = [TAURI / "tauri.conf.json", TAURI / "tauri.bundle.conf.json"]
RESOURCES = PurePosixPath("desktop/resources")
GLOB_CHARS = set("*?[{")


def _tracked() -> set[str]:
    out = subprocess.run(["git", "-C", str(ROOT), "ls-files", str(RESOURCES)],
                         capture_output=True, text=True, check=True).stdout
    return {line for line in out.splitlines() if line}


def _resources(conf: Path) -> dict[str, str]:
    return json.loads(conf.read_text())["bundle"].get("resources", {})


def _repo_path(key: str) -> str:
    """A resources key (relative to desktop/src-tauri) as a repo-relative path."""
    parts: list[str] = list(PurePosixPath("desktop/src-tauri").parts)
    for part in PurePosixPath(key).parts:
        if part == "..":
            parts.pop()
        elif part != ".":
            parts.append(part)
    return "/".join(parts)


def _mapped_resource_files() -> dict[str, str]:
    merged: dict[str, str] = {}
    for conf in CONFIGS:
        merged.update(_resources(conf))
    return {_repo_path(k): v for k, v in merged.items()
            if PurePosixPath(_repo_path(k)).is_relative_to(RESOURCES)}


def test_no_resource_key_is_a_glob():
    for conf in CONFIGS:
        globs = [k for k in _resources(conf) if GLOB_CHARS & set(k)]
        assert not globs, f"{conf.name}: {globs}"


#: desktop/resources files a build step writes before the release bundle is made; they
#: are gitignored, so only the release overlay maps them (a fresh checkout's
#: `cargo check` would refuse a missing resource named in tauri.conf.json).
GENERATED = {"desktop/resources/seed.db": "npm run prebuild -> backend/scripts/generate_seed_db.py"}


def _in_resources(conf: Path) -> set[str]:
    return {_repo_path(k) for k in _resources(conf)
            if PurePosixPath(_repo_path(k)).is_relative_to(RESOURCES)}


def test_mapped_resources_equal_git_tracked_files():
    tracked = _tracked()
    assert tracked, "git ls-files desktop/resources returned nothing"
    assert _in_resources(CONFIGS[0]) == tracked
    assert _in_resources(CONFIGS[1]) == tracked | set(GENERATED)


def test_generated_resources_are_written_by_the_build():
    pkg = json.loads((ROOT / "desktop" / "package.json").read_text())["scripts"]
    assert "generate-seed-db" in pkg["prebuild"] and "generate_seed_db.py" in pkg["generate-seed-db"]
    gen = (ROOT / "backend" / "scripts" / "generate_seed_db.py").read_text()
    assert '"desktop" / "resources" / "seed.db"' in gen


def test_each_resource_keeps_its_place_in_the_bundle():
    # the same target the globs produced: top-level files at the root, daemon/ files in daemon/
    for path, target in _mapped_resource_files().items():
        assert target == str(PurePosixPath(path).relative_to(RESOURCES)), (path, target)


def test_base_and_overlay_agree_on_resources():
    base = {k: v for k, v in _resources(CONFIGS[0]).items()
            if PurePosixPath(_repo_path(k)).is_relative_to(RESOURCES)}
    overlay = {k: v for k, v in _resources(CONFIGS[1]).items()
               if PurePosixPath(_repo_path(k)).is_relative_to(RESOURCES)}
    assert base, "tauri.conf.json maps no desktop/resources files (tauri dev needs them)"
    extra = {k: v for k, v in overlay.items() if k not in base}
    assert {k: v for k, v in overlay.items() if k in base} == base, \
        "the release overlay maps desktop/resources differently"
    assert {_repo_path(k) for k in extra} == set(GENERATED)


def test_install_manifest_lists_only_mapped_resources(tmp_path):
    """The G2 manifest names exactly the resource files the bundle maps: a gitignored
    file left in desktop/resources by a local build is not in the installer, so it must
    not be in the manifest either (the installer check would report it missing)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "write_install_manifest", ROOT / "desktop" / "scripts" / "write_install_manifest.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    desktop = tmp_path / "desktop"
    (desktop / "src-tauri").mkdir(parents=True)
    (desktop / "src-tauri" / "tauri.conf.json").write_text(json.dumps({"bundle": {"resources": {
        "../resources/a.json": "a.json", "../resources/daemon/b.sh": "daemon/b.sh"}}}))
    res = desktop / "resources"
    (res / "daemon").mkdir(parents=True)
    for rel in ("a.json", "daemon/b.sh", "user-mcp-servers.json", "seed.db"):
        (res / rel).write_text("x")
    stage = tmp_path / "stage"
    (stage / "python-backend").mkdir(parents=True)
    (stage / "python-backend" / "python-backend").write_text("exe")
    paths = {f["path"] for f in mod.build(stage, res, "1.0", "aarch64-apple-darwin")["files"]}
    assert {p for p in paths if not p.startswith("python-backend/")} == {"a.json", "daemon/b.sh"}
