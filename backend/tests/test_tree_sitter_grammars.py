"""The packaged app loads only the tree-sitter grammars it ships, never a download.

tree-sitter-language-pack downloads a grammar bundle at first use, inside its Rust
extension and without releasing the GIL: every thread of the process stopped for the
whole download (a fresh-HOME startup froze 192 s, 2026-10-28). core.code_intel.grammars
is the only door to the library; these tests hold that door shut. The library is mocked
at its boundary (configure / get_language / downloaded_languages / download), so the
tests run anywhere, with no network and no grammar files.
"""
from __future__ import annotations

import ast
import hashlib
import json
import sys
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


def test_source_mode_without_a_shipped_set_uses_the_librarys_own_cache(pack) -> None:
    fake = pack(frozen=False, directory=None)
    assert g.get_language("python") == "<Language python>"
    assert not [c for c in fake.calls if c[0] == "configure"]


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
    out = tmp_path / "out"
    return b.bundle(out), out, fake


def test_the_build_ships_every_language_and_nothing_else(monkeypatch, tmp_path) -> None:
    langs, out, fake = _run_bundle(monkeypatch, tmp_path, lambda want: want)
    assert sorted(p.name for p in out.iterdir()) == sorted(f"libtree_sitter_{n}.dylib" for n in langs)
    assert ("download", tuple(langs)) in fake.calls


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
