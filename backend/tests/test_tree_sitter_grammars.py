"""The backend loads only tree-sitter grammars already on disk, never a download, in
every mode (packaged app and source mode).

tree-sitter-language-pack downloads a grammar bundle at first use, inside its Rust
extension and without releasing the GIL: every thread of the process stopped for the
whole download (a fresh-HOME startup froze 192 s, 2026-10-28; a stalled one never ends).
core.code_intel.grammars is the only door to the library; these tests hold that door
shut. Most mock the library at its boundary (configure / get_language /
downloaded_languages / download), so they run anywhere, with no network and no grammar
files; one drives the real library on an empty HOME behind a proxy that never answers.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import socket
import subprocess
import sys
import threading
import time
import types
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from core.code_intel import grammars as g  # noqa: E402

_ALLOWED = {"core/code_intel/grammars.py", "scripts/bundle_tree_sitter_grammars.py"}
_LOADERS = {"get_language", "get_parser", "download", "download_all", "init", "process"}


class FakePack(types.ModuleType):
    """tree_sitter_language_pack at its boundary: what was asked, in order."""

    def __init__(self, shipped=()):
        super().__init__("tree_sitter_language_pack")
        self.calls: list[tuple] = []
        self._shipped = list(shipped)
        self.PackConfig = lambda cache_dir=None: {"cache_dir": cache_dir}

    def configure(self, config):
        self.calls.append(("configure", config["cache_dir"]))

    def downloaded_languages(self):
        self.calls.append(("downloaded_languages",))
        return list(self._shipped)

    def get_language(self, name):
        self.calls.append(("get_language", name))
        return f"<Language {name}>"

    def download(self, names):
        self.calls.append(("download", tuple(names)))
        return len(names)

    def download_all(self):
        self.calls.append(("download_all",))
        return 0

    def prefetch(self, names):
        self.calls.append(("prefetch", tuple(names)))

    def init(self, config=None):
        self.calls.append(("init",))


_FETCHING = {"get_language", "download", "download_all", "prefetch", "init"}


@pytest.fixture
def pack(monkeypatch):
    def make(shipped=(), frozen=True, directory=None):
        fake = FakePack(shipped)
        monkeypatch.setitem(sys.modules, "tree_sitter_language_pack", fake)
        monkeypatch.setattr("utils.backend_bin.is_frozen", lambda: frozen)
        monkeypatch.setattr(g, "grammar_dir", lambda: directory)
        monkeypatch.setattr(g, "_configured", False)
        monkeypatch.setattr(g, "_shipped_cache", {})
        return fake
    return make


def test_the_packaged_app_without_a_grammar_dir_loads_nothing(pack) -> None:
    fake = pack(frozen=True, directory=None)
    with pytest.raises(LookupError):
        g.get_language("python")
    assert fake.calls == []        # the library was never asked: no download possible


def test_the_shipped_dir_is_configured_before_the_first_load(pack, tmp_path) -> None:
    fake = pack(shipped=["python", "csharp"], directory=tmp_path)
    assert g.get_language("python") == "<Language python>"
    assert fake.calls[0] == ("configure", str(tmp_path))
    assert ("get_language", "python") in fake.calls
    g.get_language("csharp")
    assert [c for c in fake.calls if c[0] == "configure"] == [("configure", str(tmp_path))]


def test_a_language_not_shipped_is_refused_without_asking_the_library(pack, tmp_path) -> None:
    fake = pack(shipped=["python"], directory=tmp_path)
    with pytest.raises(LookupError):
        g.get_language("zig")
    assert ("get_language", "zig") not in fake.calls


def test_source_mode_with_an_empty_cache_loads_nothing_and_never_downloads(
        pack, monkeypatch) -> None:
    # A source backend on a fresh HOME: no generated directory, nothing in the
    # library's cache. The library is only asked what it has; nothing that can fetch.
    fake = pack(shipped=[], frozen=False, directory=None)
    for lang in ("python", "typescript", "javascript", "go"):
        with pytest.raises(LookupError):
            g.get_language(lang)
    assert {c[0] for c in fake.calls} == {"downloaded_languages"}
    from core.code_intel import parser as P
    if not P._ts_available:
        pytest.skip("tree-sitter is not installed here")
    monkeypatch.setattr(P, "grammars", g)
    if hasattr(P._parser_cache_tls, "cache"):
        P._parser_cache_tls.cache.clear()
    assert P._get_cached_parser("python") is None      # the regex path
    assert not [c for c in fake.calls if c[0] in _FETCHING]


def test_source_mode_loads_what_the_librarys_own_cache_holds(pack) -> None:
    fake = pack(shipped=["python"], frozen=False, directory=None)
    assert g.get_language("python") == "<Language python>"
    assert not [c for c in fake.calls if c[0] == "configure"]   # its default cache
    with pytest.raises(LookupError):
        g.get_language("zig")
    assert ("get_language", "zig") not in fake.calls
    assert not [c for c in fake.calls if c[0] in _FETCHING - {"get_language"}]


class _StallingProxy:
    """An HTTP(S) proxy that accepts connections and never answers: a stalled GitHub."""

    def __init__(self):
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.port = self.sock.getsockname()[1]
        self.connections: list[socket.socket] = []
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            self.connections.append(conn)

    def close(self):
        for c in self.connections:
            c.close()
        self.sock.close()


_CHILD = r"""
import sys, time
sys.path.insert(0, sys.argv[1])
from core.code_intel import grammars, parser
t0 = time.time()
try:
    grammars.get_language("python")
    print("LOADED")
except LookupError:
    print("REFUSED")
print("PARSER", parser._get_cached_parser("python"))
import main
main._prewarm_tree_sitter_body()
print("SECONDS", round(time.time() - t0, 2))
"""


def test_the_real_library_on_an_empty_home_is_refused_without_a_connection(tmp_path) -> None:
    """The real library, a temp HOME (empty cache), no generated directory, and GitHub
    stalled behind a proxy that never answers: a grammar load is refused at once, the
    parser takes its regex path, the startup pre-warm returns, and nothing connects.
    Before the fix the load went to the library, which blocked on the download."""
    pytest.importorskip("tree_sitter_language_pack")
    if g.grammar_dir() is not None:
        pytest.skip("backend/generated/tree_sitter_grammars exists here: the grammars are on disk")
    proxy = _StallingProxy()
    url = f"http://127.0.0.1:{proxy.port}"
    env = {k: v for k, v in os.environ.items()
           if k not in ("XDG_CACHE_HOME", "NO_PROXY", "no_proxy")}
    home = tmp_path / "home"
    home.mkdir()
    env.update(HOME=str(home), HTTPS_PROXY=url, https_proxy=url, HTTP_PROXY=url,
               http_proxy=url, ALL_PROXY=url, all_proxy=url)
    try:
        t0 = time.time()
        try:
            r = subprocess.run([sys.executable, "-c", _CHILD, str(BACKEND)], cwd=tmp_path,
                               env=env, capture_output=True, text=True, timeout=60)
        except subprocess.TimeoutExpired:
            pytest.fail("the grammar load blocked (a download behind the stalled proxy)")
        elapsed = time.time() - t0
    finally:
        proxy.close()
    assert r.returncode == 0, r.stderr[-2000:]
    assert "REFUSED" in r.stdout and "PARSER None" in r.stdout, r.stdout
    assert proxy.connections == [], "the backend process opened a connection to download"
    assert elapsed < 45, f"took {elapsed:.0f}s"
    assert not list((home / ".cache").glob("tree-sitter-language-pack/*/bundles/*"))


def test_a_refused_language_takes_the_regex_path(pack, tmp_path, monkeypatch) -> None:
    from core.code_intel import parser as P
    if not P._ts_available:
        pytest.skip("tree-sitter is not installed here")
    pack(shipped=[], directory=tmp_path)
    monkeypatch.setattr(P, "grammars", g)
    if hasattr(P._parser_cache_tls, "cache"):
        P._parser_cache_tls.cache.clear()
    assert P._get_cached_parser("python") is None


def test_the_shipped_set_is_every_language_code_intel_parses() -> None:
    from core.code_intel import parser as P
    assert P.tree_sitter_languages() == sorted({P.grammar_for(s) for s in P.LANGUAGE_MAP} - {None})
    assert "sql" not in P.tree_sitter_languages()
    assert {"typescript", "tsx"} <= set(P.tree_sitter_languages())


def _pack_aliases(tree: ast.AST) -> tuple[set[str], list[int]]:
    """Names bound to the library module, and lines that import from it by name."""
    aliases, from_imports = set(), []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name == "tree_sitter_language_pack":
                    aliases.add(a.asname or a.name)
        elif isinstance(node, ast.ImportFrom) and node.module == "tree_sitter_language_pack":
            from_imports.append(node.lineno)
    return aliases, from_imports


def test_no_module_but_grammars_loads_through_the_library() -> None:
    bad = []
    for path in BACKEND.rglob("*.py"):
        rel = path.relative_to(BACKEND).as_posix()
        if rel.startswith((".venv/", "tests/")) or "/tests/" in rel or rel in _ALLOWED:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if "tree_sitter_language_pack" not in text:
            continue
        tree = ast.parse(text)
        aliases, from_imports = _pack_aliases(tree)
        bad += [f"{rel}:{n} imports from the library" for n in from_imports]
        for node in ast.walk(tree):
            if (isinstance(node, ast.Attribute) and node.attr in _LOADERS
                    and isinstance(node.value, ast.Name) and node.value.id in aliases):
                bad.append(f"{rel}:{node.lineno} calls the library's {node.attr}")
    assert bad == [], "load grammars through core.code_intel.grammars only: " + "; ".join(bad)


def test_the_scan_sees_each_way_of_calling_the_library() -> None:
    src = ("import tree_sitter_language_pack as tslp\n"
           "from tree_sitter_language_pack import get_language\n"
           "tslp.get_language('x')\n")
    aliases, from_imports = _pack_aliases(ast.parse(src))
    assert aliases == {"tslp"} and from_imports == [2]


# ── the build step ──────────────────────────────────────────────────────────

class FakeBuildPack(FakePack):
    def __init__(self, own: Path, langs_after: list[str], bundle_bytes: bytes | None):
        super().__init__(langs_after)
        self.own, self.libs = own, None
        self.bundle_bytes = bundle_bytes

    def cache_dir(self):
        return str(self.own / "libs")

    def configure(self, config):
        super().configure(config)
        self.libs = Path(config["cache_dir"])

    def download(self, names):
        self.calls.append(("download", tuple(names)))
        for n in self._shipped:
            (self.libs / f"libtree_sitter_{n}.dylib").write_text(n)
        if self.bundle_bytes is not None and not (self.libs.parent / "bundles").exists():
            (self.libs.parent / "bundles").mkdir()
            (self.libs.parent / "bundles" / "p.tar.zst").write_bytes(self.bundle_bytes)
            (self.libs.parent / "manifest.json").write_text(json.dumps(
                {"platforms": {"p": {"sha256": hashlib.sha256(b"good").hexdigest()}}}))
        return len(names)


def _run_bundle(monkeypatch, tmp_path, langs_after, bundle_bytes=b"good"):
    from core.code_intel.parser import tree_sitter_languages
    from scripts import bundle_tree_sitter_grammars as b
    fake = FakeBuildPack(tmp_path / "own", langs_after(tree_sitter_languages()), bundle_bytes)
    monkeypatch.setitem(sys.modules, "tree_sitter_language_pack", fake)
    monkeypatch.setattr(b, "_library_version", lambda: "9.9.9")
    out = tmp_path / "out"
    return b.bundle(out), out, fake


def test_the_build_ships_every_language_and_nothing_else(monkeypatch, tmp_path) -> None:
    from scripts import bundle_tree_sitter_grammars as b
    langs, out, fake = _run_bundle(monkeypatch, tmp_path, lambda want: want)
    assert sorted(p.name for p in out.iterdir()) == sorted(
        [f"libtree_sitter_{n}.dylib" for n in langs] + [b.STAMP])
    assert (out / b.STAMP).read_text().strip() == "9.9.9"
    assert ("download", tuple(langs)) in fake.calls


def _complete_out(tmp_path, stamp):
    from core.code_intel.parser import tree_sitter_languages
    from scripts import bundle_tree_sitter_grammars as b
    out = tmp_path / "out"
    out.mkdir()
    for n in tree_sitter_languages():
        (out / f"libtree_sitter_{n}.dylib").write_text("old " + n)
    (out / b.STAMP).write_text(stamp + "\n")
    return out


def test_the_build_skips_a_complete_dir_for_this_version(monkeypatch, tmp_path) -> None:
    out = _complete_out(tmp_path, "9.9.9")
    langs, out2, fake = _run_bundle(monkeypatch, tmp_path, lambda want: want)
    assert out2 == out
    assert not [c for c in fake.calls if c[0] == "download"]      # nothing fetched
    assert (out / "libtree_sitter_python.dylib").read_text() == "old python"


def test_the_build_rebuilds_when_the_stamp_names_another_version(monkeypatch, tmp_path) -> None:
    from scripts import bundle_tree_sitter_grammars as b
    out = _complete_out(tmp_path, "0.0.1")
    langs, _, fake = _run_bundle(monkeypatch, tmp_path, lambda want: want)
    assert ("download", tuple(langs)) in fake.calls
    assert (out / "libtree_sitter_python.dylib").read_text() == "python"
    assert (out / b.STAMP).read_text().strip() == "9.9.9"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["out"]   # swapped, nothing left


def test_the_build_rebuilds_a_dir_the_library_does_not_list_complete(monkeypatch, tmp_path) -> None:
    # Right version, but the library (on this platform) does not list every language:
    # another platform's files, or a language added to code intel since.
    out = _complete_out(tmp_path, "9.9.9")
    calls = {"n": 0}
    real = FakeBuildPack.downloaded_languages

    def first_incomplete(self):
        calls["n"] += 1
        langs = real(self)
        return langs[:-1] if calls["n"] == 1 else langs
    monkeypatch.setattr(FakeBuildPack, "downloaded_languages", first_incomplete)
    langs, _, fake = _run_bundle(monkeypatch, tmp_path, lambda want: want)
    assert ("download", tuple(langs)) in fake.calls
    assert (out / "libtree_sitter_python.dylib").read_text() == "python"


def test_a_stalled_build_download_is_ended_by_its_deadline(tmp_path) -> None:
    # The library's download holds the GIL, so no Python timer could stop it: the
    # deadline is SIGALRM with its default action, which the OS carries out.
    if not hasattr(__import__("signal"), "SIGALRM"):
        pytest.skip("no SIGALRM on this platform")
    child = r"""
import sys, time, types
sys.path.insert(0, sys.argv[1])
fake = types.ModuleType("tree_sitter_language_pack")
fake.PackConfig = lambda cache_dir=None: {"cache_dir": cache_dir}
fake.configure = lambda config: None
fake.cache_dir = lambda: sys.argv[2] + "/own/libs"
fake.downloaded_languages = lambda: []
def download(names):
    while True:
        time.sleep(1)      # stands in for a stalled native download
fake.download = download
sys.modules["tree_sitter_language_pack"] = fake
from scripts import bundle_tree_sitter_grammars as b
b.main([sys.argv[2] + "/out", "--deadline", "1"])
"""
    t0 = time.time()
    r = subprocess.run([sys.executable, "-c", child, str(BACKEND), str(tmp_path)],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == -14, (r.returncode, r.stderr[-1000:])     # killed by SIGALRM
    assert time.time() - t0 < 30
    assert not (tmp_path / "out").exists()                           # never a partial one
    assert (tmp_path / "out.build").is_dir()      # left where the next run removes it


def test_an_up_to_date_run_still_removes_what_a_killed_one_left(monkeypatch, tmp_path) -> None:
    # killed after the swap, while the replaced directory was being removed
    out = _complete_out(tmp_path, "9.9.9")
    (tmp_path / "out.old").mkdir()
    (tmp_path / "out.old" / "libtree_sitter_python.dylib").write_text("older")
    _, _, fake = _run_bundle(monkeypatch, tmp_path, lambda want: want)
    assert not [c for c in fake.calls if c[0] == "download"]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["out"]
    assert (out / "libtree_sitter_python.dylib").read_text() == "old python"


def test_a_run_ended_between_the_renames_gets_the_previous_dir_back(monkeypatch, tmp_path) -> None:
    # out was renamed aside, the new set not yet renamed in: the previous complete set
    # comes back (and, being up to date, is kept) instead of being deleted
    _complete_out(tmp_path, "9.9.9").rename(tmp_path / "out.old")
    (tmp_path / "out.build" / "libs").mkdir(parents=True)
    _, out, fake = _run_bundle(monkeypatch, tmp_path, lambda want: want)
    assert not [c for c in fake.calls if c[0] == "download"]
    assert (out / "libtree_sitter_python.dylib").read_text() == "old python"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["out"]


def test_the_new_dir_is_renamed_in_whole_never_copied(monkeypatch, tmp_path) -> None:
    # a rename keeps the files the download wrote (same inode); a copy would make new
    # ones, and a run ended midway through a copy would leave a stamped partial set
    _complete_out(tmp_path, "0.0.1")
    written = {}
    real = FakeBuildPack.download

    def spy(self, names):
        n = real(self, names)
        written["ino"] = (self.libs / "libtree_sitter_python.dylib").stat().st_ino
        return n
    monkeypatch.setattr(FakeBuildPack, "download", spy)
    _, out, _ = _run_bundle(monkeypatch, tmp_path, lambda want: want)
    assert (out / "libtree_sitter_python.dylib").stat().st_ino == written["ino"]


def test_a_failed_swap_puts_the_previous_dir_back(monkeypatch, tmp_path) -> None:
    from scripts import bundle_tree_sitter_grammars as b
    out = _complete_out(tmp_path, "0.0.1")
    real_rename = Path.rename

    def rename(self, target):
        if self.name == "libs":
            raise OSError("disk full")
        return real_rename(self, target)
    monkeypatch.setattr(Path, "rename", rename)
    with pytest.raises(OSError, match="disk full"):
        _run_bundle(monkeypatch, tmp_path, lambda want: want)
    assert (out / "libtree_sitter_python.dylib").read_text() == "old python"
    assert (out / b.STAMP).read_text().strip() == "0.0.1"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["out"]


def test_the_next_build_removes_what_a_killed_one_left(monkeypatch, tmp_path) -> None:
    for name in ("out.build", "out.old"):
        (tmp_path / name / "libs").mkdir(parents=True)
        (tmp_path / name / "libs" / "partial.so").write_text("x")
    langs, out, _ = _run_bundle(monkeypatch, tmp_path, lambda want: want)
    assert not (tmp_path / "out.build").exists() and not (tmp_path / "out.old").exists()
    assert {p.name for p in out.iterdir()} >= {f"libtree_sitter_{n}.dylib" for n in langs}


def test_the_build_stops_when_a_language_is_missing(monkeypatch, tmp_path) -> None:
    with pytest.raises(SystemExit, match="missing"):
        _run_bundle(monkeypatch, tmp_path, lambda want: want[:-1])
    assert not (tmp_path / "out").exists()


def test_the_build_stops_on_a_bundle_the_manifest_does_not_know(monkeypatch, tmp_path) -> None:
    with pytest.raises(SystemExit, match="does not match"):
        _run_bundle(monkeypatch, tmp_path, lambda want: want, bundle_bytes=b"tampered")


def test_the_build_reuses_this_machines_cached_bundle(monkeypatch, tmp_path) -> None:
    own = tmp_path / "own"
    (own / "bundles").mkdir(parents=True)
    (own / "bundles" / "p.tar.zst").write_bytes(b"good")
    (own / "manifest.json").write_text(json.dumps(
        {"platforms": {"p": {"sha256": hashlib.sha256(b"good").hexdigest()}}}))
    seen = {}
    real = FakeBuildPack.download

    def spy(self, names):
        seen["staged"] = (self.libs.parent / "bundles" / "p.tar.zst").is_file()
        return real(self, names)
    monkeypatch.setattr(FakeBuildPack, "download", spy)
    _run_bundle(monkeypatch, tmp_path, lambda want: want, bundle_bytes=None)
    assert seen["staged"] is True       # the bundle was in place before download ran


def test_the_build_script_runs_and_the_spec_packages_the_grammars() -> None:
    text = (BACKEND.parent / "desktop" / "scripts" / "build-backend.sh").read_text()
    assert "python scripts/bundle_tree_sitter_grammars.py generated/tree_sitter_grammars" in text
    assert f"('generated/{g.GRAMMAR_DIRNAME}', '{g.GRAMMAR_DIRNAME}')" in text
    assert text.index("bundle_tree_sitter_grammars.py") < text.index("cat > backend.spec")
