"""Feature Map runtime: the pinned install refuses a bad archive and installs nothing
(R1.1, R1.2)."""
from __future__ import annotations

import pytest

from core.feature_map import runtime
from tests.feature_map_fixtures import app_data  # noqa: F401 — fixture


def test_checksum_mismatch_is_not_run(app_data, monkeypatch):  # noqa: F811
    monkeypatch.delenv("SWARM_FEATURE_MAP_RUNTIME", raising=False)
    fetched = []

    def tampered(url, dest):
        fetched.append(url)
        dest.write_bytes(b"not the pinned archive")

    with pytest.raises(runtime.RuntimeUnavailable) as exc:
        runtime.ensure(download=tampered)
    assert "checksum mismatch" in exc.value.reason and "nothing installed" in exc.value.reason
    assert fetched and fetched[0].startswith(runtime.NODE_BASE_URL)
    folder = runtime.runtime_dir()
    assert not folder.exists(), "a refused archive leaves no runtime folder"
    assert not any(p.name.startswith(".install-") for p in folder.parent.iterdir())


def test_not_installed_without_install(app_data, monkeypatch):  # noqa: F811
    monkeypatch.delenv("SWARM_FEATURE_MAP_RUNTIME", raising=False)
    with pytest.raises(runtime.RuntimeUnavailable) as exc:
        runtime.ensure(install=False)
    assert "not installed" in exc.value.reason


def test_unsupported_platform(app_data, monkeypatch):  # noqa: F811
    monkeypatch.delenv("SWARM_FEATURE_MAP_RUNTIME", raising=False)
    monkeypatch.setattr(runtime, "_platform", lambda: ("plan9", "mips"))
    with pytest.raises(runtime.RuntimeUnavailable) as exc:
        runtime.ensure(download=lambda u, d: None)
    assert "no pinned Node for plan9/mips" in exc.value.reason


def test_every_supported_platform_has_a_pin():
    for key in [("darwin", "arm64"), ("darwin", "x64"), ("linux", "arm64"), ("linux", "x64"),
                ("win32", "x64")]:
        name, sha = runtime.NODE_SHA256[key]
        assert runtime.NODE_VERSION in name and len(sha) == 64


def test_safe_extract_refuses_escapes_and_extracts_a_good_archive(tmp_path):
    import io
    import tarfile
    import zipfile
    import pytest
    from core.feature_map.runtime import RuntimeUnavailable, _safe_extract

    def tar(name, members):
        path = tmp_path / name
        with tarfile.open(path, "w:gz") as tf:
            for m, data, link in members:
                info = tarfile.TarInfo(m)
                if link:
                    info.type, info.linkname = tarfile.SYMTYPE, link
                    tf.addfile(info)
                else:
                    info.size = len(data)
                    tf.addfile(info, io.BytesIO(data))
        return path

    def zipf(name, members):
        path = tmp_path / name
        with zipfile.ZipFile(path, "w") as zf:
            for m in members:
                zf.writestr(m, b"x")
        return path

    for i, bad in enumerate([tar("a.tar.gz", [("node/ok", b"x", None), ("../evil", b"x", None)]),
                             tar("b.tar.gz", [("node/ln", b"", "../../outside")]),
                             zipf("c.zip", ["node/ok", "../evil"])]):
        dest = tmp_path / f"x{i}"
        with pytest.raises(RuntimeUnavailable, match="escapes"):
            _safe_extract(bad, dest)
        assert not (tmp_path / "evil").exists()
    for good, dest in ((tar("g.tar.gz", [("node-v/bin/node", b"#!", None)]), tmp_path / "gt"),
                       (zipf("g.zip", ["node-v/node.exe"]), tmp_path / "gz")):
        top = _safe_extract(good, dest)
        assert top == dest.resolve() / "node-v" and any(top.rglob("node*"))


def _fake_runtime(tmp_path):
    """A runtime folder of the pinned layout whose headless shell is a stand-in file."""
    folder = tmp_path / "rt"
    (folder / "node" / "bin").mkdir(parents=True)
    (folder / "node" / "bin" / "node").write_text("")
    cli = folder / "pw" / "node_modules" / "@playwright" / "test"
    cli.mkdir(parents=True)
    (cli / "cli.js").write_text("")
    shell = folder / "browsers" / "chromium_headless_shell-1" / "chrome-headless-shell-linux-arm64"
    shell.mkdir(parents=True)
    (shell / "chrome-headless-shell").write_text("")
    return folder


def _fake_ldd(tmp_path, monkeypatch, output):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "ldd").write_text("#!/bin/sh\ncat <<'EOF'\n" + output + "EOF\n")
    (bin_dir / "ldd").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")


def test_missing_system_libraries_are_runtime_unavailable(tmp_path, app_data, monkeypatch):  # noqa: F811
    """On Linux a headless shell that cannot load its system libraries is reported as
    'runtime unavailable' naming them (walks not_run), never handed out to fail every walk."""
    import sys
    if sys.platform == "win32":
        pytest.skip("POSIX fake ldd")
    monkeypatch.setattr(runtime, "_platform", lambda: ("linux", "arm64"))
    monkeypatch.setenv("SWARM_FEATURE_MAP_RUNTIME", str(_fake_runtime(tmp_path)))
    _fake_ldd(tmp_path, monkeypatch,
              "\tlinux-vdso.so.1 (0x0000)\n\tlibatk-1.0.so.0 => not found\n"
              "\tlibgbm.so.1 => not found\n\tlibc.so.6 => /lib64/libc.so.6 (0x0000)\n")
    with pytest.raises(runtime.RuntimeUnavailable) as exc:
        runtime.ensure()
    assert "libatk-1.0.so.0, libgbm.so.1" in exc.value.reason
    assert "hive/replay-host.sh" in exc.value.reason and "install-deps chromium" in exc.value.reason

    # every library found: the runtime is handed out
    (tmp_path / "bin" / "ldd").write_text("#!/bin/sh\necho '\tlibc.so.6 => /lib64/libc.so.6'\n")
    assert runtime.ensure().folder == tmp_path / "rt"


def test_web_walks_are_not_run_when_libraries_are_missing(tmp_path, app_data, monkeypatch):  # noqa: F811
    import sys
    if sys.platform == "win32":
        pytest.skip("POSIX fake ldd")
    from core.feature_map import files
    from core.feature_map.replay import Selection, replay
    from tests.feature_map_fixtures import make_repo
    monkeypatch.setattr(runtime, "_platform", lambda: ("linux", "arm64"))
    monkeypatch.setenv("SWARM_FEATURE_MAP_RUNTIME", str(_fake_runtime(tmp_path)))
    _fake_ldd(tmp_path, monkeypatch, "\tlibX11.so.6 => not found\n")
    root = make_repo(tmp_path / "r", specs={"home.spec.ts": "test('x', async () => {});\n"},
                     features="schema: 1\nfeatures:\n  - {id: home-a1, title: Home, target: web, spec: specs/home.spec.ts}\n")
    fmap = files.load_folder(root / ".swarmai/features")
    results, _sb = replay(fmap, root, Selection(list(fmap.features)))
    assert [(r["result"], r["reason"]) for r in results] == [("not_run", "runtime unavailable")]
    assert "libX11.so.6" in results[0]["error"]


def test_no_check_off_linux_or_without_ldd(tmp_path, monkeypatch):
    rt = runtime._layout(_fake_runtime(tmp_path))
    monkeypatch.setattr(runtime, "_platform", lambda: ("darwin", "arm64"))
    assert runtime.missing_libraries(rt) == []
    monkeypatch.setattr(runtime, "_platform", lambda: ("linux", "arm64"))
    monkeypatch.setattr(runtime.shutil, "which", lambda *_a, **_k: None)
    assert runtime.missing_libraries(rt) == []
