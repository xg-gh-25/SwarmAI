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
