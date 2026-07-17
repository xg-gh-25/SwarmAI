"""Tests for parser.py — multi-language AST parsing + name resolution."""

from pathlib import Path

import pytest

from core.code_intel.parser import (
    CodeEdge,
    CodeNode,
    LANGUAGE_MAP,
    ParseResult,
    QUALIFIED_SEPARATOR,
    _build_file_scope_regex,
    _is_entry_point,
    _is_exported,
    _regex_fallback,
    _sanitize_name,
    _should_skip_dir,
    parse_file,
    parse_repo,
)


class TestSanitizeName:
    """Test prompt injection defense."""

    def test_normal_name(self):
        assert _sanitize_name("my_function") == "my_function"

    def test_strips_control_chars(self):
        result = _sanitize_name("func\x00name\x01bad")
        assert "\x00" not in result
        assert "\x01" not in result

    def test_caps_length(self):
        long_name = "a" * 500
        assert len(_sanitize_name(long_name)) == 256

    def test_preserves_tabs_newlines(self):
        assert "\t" in _sanitize_name("a\tb")
        assert "\n" in _sanitize_name("a\nb")

    def test_empty_string(self):
        assert _sanitize_name("") == ""


class TestShouldSkipDir:
    """Test directory skip logic."""

    def test_skips_known_dirs(self):
        assert _should_skip_dir("node_modules")
        assert _should_skip_dir(".git")
        assert _should_skip_dir("__pycache__")
        assert _should_skip_dir("venv")
        assert _should_skip_dir(".venv")

    def test_skips_target_build_dir(self):
        # `target` = Rust/Cargo (also Maven) build output — a tool-reserved name,
        # safe as a bare component skip at any depth. It polluted the SwarmAI graph
        # with build-artifact nodes (run_f64f6031).
        assert _should_skip_dir("target")

    def test_does_not_bare_skip_generic_dir_names(self):
        # Gate-2 MED (run_f64f6031): `binaries` and `_internal` are NOT bare-component
        # skips — both are plausible LEGIT source-dir names in an arbitrary repo (this
        # parser is the ai-ready-repo ENGINE running on ANY repo), and a bare skip
        # would silently drop real source (`pydantic/_internal`, a repo's top-level
        # `binaries/` utilities). The SwarmAI PyInstaller bundle is skipped PATH-scoped
        # (`src-tauri/binaries`, see TestSkipPathSuffixes), not by name.
        assert not _should_skip_dir("binaries")
        assert not _should_skip_dir("_internal")

    def test_allows_normal_dirs(self):
        assert not _should_skip_dir("core")
        assert not _should_skip_dir("src")
        assert not _should_skip_dir("tests")

    def test_skips_egg_info(self):
        assert _should_skip_dir("mypackage.egg-info")


class TestIsEntryPoint:
    """Test entry point detection."""

    def test_python_test_function(self):
        assert _is_entry_point("test_foo", "tests/test_bar.py", "python")

    def test_python_test_file(self):
        assert _is_entry_point("setup", "test_main.py", "python")

    def test_python_conftest(self):
        assert _is_entry_point("fixture", "conftest.py", "python")

    def test_python_main(self):
        assert _is_entry_point("main", "cli.py", "python")

    def test_python_normal_function(self):
        assert not _is_entry_point("helper", "utils.py", "python")

    def test_typescript_test_file(self):
        assert _is_entry_point("describe", "foo.test.ts", "typescript")
        assert _is_entry_point("it", "bar.spec.ts", "typescript")

    def test_go_test_function(self):
        assert _is_entry_point("TestFoo", "foo_test.go", "go")
        assert _is_entry_point("BenchmarkBar", "bench_test.go", "go")

    def test_go_main(self):
        assert _is_entry_point("main", "main.go", "go")
        assert _is_entry_point("init", "init.go", "go")


class TestIsExported:
    """Test export detection."""

    def test_python_public(self):
        assert _is_exported("public_func", "python")

    def test_python_private(self):
        assert not _is_exported("_private_func", "python")
        assert not _is_exported("__dunder__", "python")

    def test_go_exported(self):
        assert _is_exported("PublicFunc", "go")

    def test_go_unexported(self):
        assert not _is_exported("privateFunc", "go")

    def test_typescript_with_export(self):
        assert _is_exported("MyClass", "typescript", "export class MyClass {")

    def test_typescript_without_export(self):
        assert not _is_exported("MyClass", "typescript", "class MyClass {")


class TestBuildFileScopeRegex:
    """Test Layer 1 scope building via regex."""

    def test_python_from_import(self):
        code = "from os.path import join, exists\n"
        imports, defs = _build_file_scope_regex(code, "python")
        assert "join" in imports
        assert "exists" in imports

    def test_python_import(self):
        code = "import os.path\n"
        imports, defs = _build_file_scope_regex(code, "python")
        assert "path" in imports

    def test_python_definitions(self):
        code = "def foo():\n    pass\nclass Bar:\n    pass\n"
        imports, defs = _build_file_scope_regex(code, "python")
        assert "foo" in defs
        assert "Bar" in defs

    def test_typescript_named_import(self):
        code = "import { useState, useEffect } from 'react';\n"
        imports, defs = _build_file_scope_regex(code, "typescript")
        assert "useState" in imports
        assert "useEffect" in imports

    def test_typescript_default_import(self):
        code = "import React from 'react';\n"
        imports, defs = _build_file_scope_regex(code, "typescript")
        assert "React" in imports

    def test_java_import(self):
        code = "import com.example.MyClass;\n"
        imports, defs = _build_file_scope_regex(code, "java")
        assert "MyClass" in imports

    def test_go_import(self):
        code = 'import "fmt"\n'
        imports, defs = _build_file_scope_regex(code, "go")
        assert "fmt" in imports


class TestRegexFallback:
    """Test regex-based parsing (no tree-sitter required)."""

    def test_python_extraction(self, tmp_path):
        src = tmp_path / "module.py"
        src.write_text(
            "def foo():\n    bar()\n\n"
            "def bar():\n    pass\n\n"
            "class MyClass:\n    pass\n"
        )
        result = _regex_fallback(src, tmp_path)
        assert result.language == "python"
        names = {n.name for n in result.nodes}
        assert "foo" in names
        assert "bar" in names
        assert "MyClass" in names

    def test_confidence_is_06(self, tmp_path):
        src = tmp_path / "module.py"
        src.write_text("def foo():\n    bar()\n\ndef bar():\n    pass\n")
        result = _regex_fallback(src, tmp_path)
        for edge in result.edges:
            assert edge.confidence == 0.6

    def test_qualified_names(self, tmp_path):
        src = tmp_path / "utils.py"
        src.write_text("def helper():\n    pass\n")
        result = _regex_fallback(src, tmp_path)
        assert any(n.id == f"utils.py{QUALIFIED_SEPARATOR}helper" for n in result.nodes)

    def test_typescript_extraction(self, tmp_path):
        src = tmp_path / "app.ts"
        src.write_text(
            "export function render(): void {\n  console.log('hi');\n}\n\n"
            "export class App {\n  run() {}\n}\n"
        )
        result = _regex_fallback(src, tmp_path)
        names = {n.name for n in result.nodes}
        assert "render" in names
        assert "App" in names

    def test_unreadable_file(self, tmp_path):
        src = tmp_path / "bad.py"
        src.write_bytes(b"\x80\x81\x82")
        result = _regex_fallback(src, tmp_path)
        # Should not crash, may produce empty result
        assert isinstance(result, ParseResult)


class TestParseFile:
    """Test the main parse_file function."""

    def test_python_file(self, tmp_path):
        src = tmp_path / "demo.py"
        src.write_text(
            "from os import path\n\n"
            "def process(data):\n"
            "    return path.join(data, 'suffix')\n\n"
            "class Handler:\n"
            "    def handle(self):\n"
            "        self.process()\n"
        )
        result = parse_file(src, tmp_path)
        assert result.language == "python"
        assert len(result.nodes) >= 2  # at least process + Handler

    def test_unsupported_extension(self, tmp_path):
        src = tmp_path / "readme.md"
        src.write_text("# Hello")
        result = parse_file(src, tmp_path)
        assert result.nodes == []

    def test_nonexistent_file(self, tmp_path):
        result = parse_file(tmp_path / "missing.py", tmp_path)
        assert result.nodes == []


class TestParseRepo:
    """Test repo-wide parsing."""

    def test_basic_repo(self, tmp_path):
        (tmp_path / "main.py").write_text("def main():\n    pass\n")
        (tmp_path / "utils.py").write_text("def helper():\n    return 1\n")
        results = parse_repo(tmp_path)
        all_nodes = [n for r in results for n in r.nodes]
        names = {n.name for n in all_nodes}
        assert "main" in names
        assert "helper" in names

    def test_skips_node_modules(self, tmp_path):
        nm = tmp_path / "node_modules" / "pkg"
        nm.mkdir(parents=True)
        (nm / "index.js").write_text("function internal() {}\n")
        (tmp_path / "app.py").write_text("def app():\n    pass\n")
        results = parse_repo(tmp_path)
        all_nodes = [n for r in results for n in r.nodes]
        names = {n.name for n in all_nodes}
        assert "internal" not in names
        assert "app" in names

    def test_skips_tauri_sidecar_binaries_path_scoped(self, tmp_path):
        # run_f64f6031: the PyInstaller bundle at src-tauri/binaries/... polluted
        # the graph. It is skipped PATH-scoped (src-tauri/binaries), not by a bare
        # `binaries` component — so the Tauri sidecar bundle is skipped in ANY Tauri
        # app while a repo's top-level binaries/ source stays visible (next test).
        bundle = tmp_path / "desktop" / "src-tauri" / "binaries" / "python-backend" / "_internal"
        bundle.mkdir(parents=True)
        (bundle / "vendored.py").write_text("def bundled_dep():\n    return 1\n")
        (tmp_path / "app.py").write_text("def app():\n    pass\n")
        results = parse_repo(tmp_path)
        names = {n.name for r in results for n in r.nodes}
        assert "bundled_dep" not in names   # sidecar bundle skipped
        assert "app" in names

    def test_does_not_skip_toplevel_binaries_source(self, tmp_path):
        # Gate-2 MED guard: a repo's OWN top-level binaries/ (real source, not a
        # Tauri sidecar) must NOT be skipped — bare `binaries` was removed from
        # SKIP_DIRS precisely to avoid silently dropping arbitrary-repo source.
        binz = tmp_path / "binaries"
        binz.mkdir()
        (binz / "tool.py").write_text("def real_tool():\n    return 42\n")
        (tmp_path / "app.py").write_text("def app():\n    pass\n")
        results = parse_repo(tmp_path)
        names = {n.name for r in results for n in r.nodes}
        assert "real_tool" in names   # top-level binaries/ source PRESERVED
        assert "app" in names

    def test_skips_pycache(self, tmp_path):
        cache = tmp_path / "__pycache__"
        cache.mkdir()
        (cache / "mod.cpython-312.pyc").write_bytes(b"\x00")
        (tmp_path / "real.py").write_text("def real_func():\n    pass\n")
        results = parse_repo(tmp_path)
        all_nodes = [n for r in results for n in r.nodes]
        assert all("__pycache__" not in n.file_path for n in all_nodes)

    def test_language_filter(self, tmp_path):
        (tmp_path / "code.py").write_text("def py_func():\n    pass\n")
        (tmp_path / "code.ts").write_text("function ts_func(): void {}\n")
        results = parse_repo(tmp_path, languages=["python"])
        all_nodes = [n for r in results for n in r.nodes]
        languages = {n.language for n in all_nodes}
        assert "python" in languages
        assert "typescript" not in languages

    def test_empty_repo(self, tmp_path):
        results = parse_repo(tmp_path)
        assert results == []

    def test_nonexistent_dir(self, tmp_path):
        results = parse_repo(tmp_path / "missing")
        assert results == []

    def test_nested_directories(self, tmp_path):
        pkg = tmp_path / "pkg" / "sub"
        pkg.mkdir(parents=True)
        (pkg / "mod.py").write_text("def nested():\n    pass\n")
        results = parse_repo(tmp_path)
        all_nodes = [n for r in results for n in r.nodes]
        assert any(n.name == "nested" for n in all_nodes)


# ═══════════════════════════════════════════════════════════════════════
# Coverage-correctness (Run AB): parse_repo_with_coverage — never silently
# under-report. A file that is SEEN but not turned into nodes must be
# accounted for as a coverage-hole {ref, kind, reason}, never silently dropped.
# ═══════════════════════════════════════════════════════════════════════

from core.code_intel.parser import (  # noqa: E402
    parse_repo_with_coverage,
    ParseRepoResult,
)


class TestParseRepoWithCoverage:
    """A1/A2/A3: deterministic parse never silently under-reports."""

    def test_returns_results_and_holes(self, tmp_path):
        (tmp_path / "main.py").write_text("def main():\n    pass\n")
        out = parse_repo_with_coverage(tmp_path)
        assert isinstance(out, ParseRepoResult)
        assert any(n.name == "main" for r in out.results for n in r.nodes)
        assert isinstance(out.coverage_holes, list)
        assert out.status in ("complete", "partial")

    # --- back-compat: parse_repo (the list API) is UNCHANGED ---
    def test_parse_repo_still_returns_bare_list(self, tmp_path):
        (tmp_path / "main.py").write_text("def main():\n    pass\n")
        results = parse_repo(tmp_path)
        assert isinstance(results, list)  # NOT a tuple/dataclass — 3 callers depend on this
        assert all(isinstance(r, ParseResult) for r in results)

    # --- A1: unknown-extension source-like file is a coverage-hole, not silent ---
    def test_a1_unknown_extension_recorded_as_hole(self, tmp_path):
        (tmp_path / "app.py").write_text("def app():\n    pass\n")
        (tmp_path / "legacy.cbl").write_text("IDENTIFICATION DIVISION.\n")  # COBOL, not in LANGUAGE_MAP
        out = parse_repo_with_coverage(tmp_path)
        file_holes = [h for h in out.coverage_holes if h["kind"] == "file"]
        assert any("legacy.cbl" in h["ref"] for h in file_holes), \
            f"unknown-ext file must be a coverage-hole, got {out.coverage_holes}"
        # reason must be substantive (names the extension / why)
        cbl = next(h for h in file_holes if "legacy.cbl" in h["ref"])
        assert ".cbl" in cbl["reason"] or "cbl" in cbl["reason"].lower()

    def test_a1_unknown_extensions_deduped_by_ext_bounded(self, tmp_path):
        # 50 .cbl files must NOT create 50 noisy holes on the same reason-class;
        # bounded reporting (dedupe by extension OR cap) keeps the ledger readable.
        (tmp_path / "app.py").write_text("def app():\n    pass\n")
        for i in range(50):
            (tmp_path / f"f{i}.cbl").write_text("X.\n")
        out = parse_repo_with_coverage(tmp_path)
        cbl_holes = [h for h in out.coverage_holes if h["kind"] == "file" and ".cbl" in h.get("ref", "") + h.get("reason", "")]
        # bounded: either one aggregate hole for the extension, or a capped list — never all 50
        assert 1 <= len(cbl_holes) <= 10, f"unbounded unknown-ext noise: {len(cbl_holes)} holes"

    # --- A1: unreadable / decode-failure file is a hole (parse FAILURE) ---
    def test_a1_unreadable_file_recorded_as_hole(self, tmp_path, monkeypatch):
        bad = tmp_path / "bad.py"
        bad.write_text("def ok(): pass\n")
        (tmp_path / "good.py").write_text("def good():\n    pass\n")
        real_read_bytes = Path.read_bytes
        def boom(self, *a, **k):
            if self.name == "bad.py":
                raise OSError("simulated unreadable")
            return real_read_bytes(self, *a, **k)
        monkeypatch.setattr(Path, "read_bytes", boom)
        out = parse_repo_with_coverage(tmp_path)
        assert any("bad.py" in h["ref"] and h["kind"] == "file" for h in out.coverage_holes), \
            f"unreadable file must be a hole, got {out.coverage_holes}"

    # --- A3: a file that parses FINE but legitimately has 0 nodes is NOT a hole ---
    def test_a3_clean_empty_file_is_not_a_hole(self, tmp_path):
        (tmp_path / "app.py").write_text("def app():\n    pass\n")
        (tmp_path / "__init__.py").write_text("# re-exports only\n")  # 0 nodes, legit
        out = parse_repo_with_coverage(tmp_path)
        # __init__.py must NOT be flagged — clean parse, legitimately empty (Gate-1 Check-3)
        assert not any("__init__.py" in h.get("ref", "") for h in out.coverage_holes), \
            f"clean 0-node file wrongly flagged: {out.coverage_holes}"

    # --- A3: serial and parallel paths must behave identically (Gate-1 Check-3) ---
    def test_a3_serial_and_parallel_paths_consistent(self, tmp_path):
        # < _SERIAL_THRESHOLD (8) files → serial; >= 8 → parallel. Both must
        # treat a clean-empty file the same way (neither drops it as a hole).
        for i in range(3):  # serial path
            (tmp_path / f"m{i}.py").write_text("# comment only\n")
        out_serial = parse_repo_with_coverage(tmp_path)
        for i in range(3, 20):  # push over threshold → parallel path
            (tmp_path / f"m{i}.py").write_text("# comment only\n")
        out_parallel = parse_repo_with_coverage(tmp_path)
        serial_holes = {h.get("ref") for h in out_serial.coverage_holes}
        parallel_holes = {h.get("ref") for h in out_parallel.coverage_holes}
        # comment-only files are clean-empty → neither path should flag them
        assert not any("m0.py" in (r or "") for r in serial_holes)
        assert not any("m10.py" in (r or "") for r in parallel_holes)

    # --- A2: non-dir → explicit signal, not silent [] ---
    def test_a2_nonexistent_dir_signals_partial(self, tmp_path):
        out = parse_repo_with_coverage(tmp_path / "missing")
        assert out.results == []
        assert out.status == "partial"
        assert any(h["kind"] == "repo" for h in out.coverage_holes), \
            "non-dir must emit an explicit repo-level signal, not silent []"

    # --- A2: empty repo → explicit signal ---
    def test_a2_empty_repo_signals(self, tmp_path):
        out = parse_repo_with_coverage(tmp_path)
        assert out.results == []
        # empty repo is a coverage-relevant fact — must be signalled, not silent
        assert any(h["kind"] == "repo" for h in out.coverage_holes)

    # --- A2: oversized repo → explicit signal + status partial (bounded) ---
    def test_a2_oversized_repo_signals_partial(self, tmp_path, monkeypatch):
        import core.code_intel.parser as P
        monkeypatch.setattr(P, "_MAX_REPO_FILES", 3, raising=False)
        for i in range(10):
            (tmp_path / f"f{i}.py").write_text(f"def f{i}(): pass\n")
        out = parse_repo_with_coverage(tmp_path)
        assert out.status == "partial", "oversized repo must be flagged partial, not silently truncated as complete"
        assert any(h["kind"] == "repo" for h in out.coverage_holes)


class TestGate2AdversarialFixes:
    """Run AB Gate-2 adversarial fixes (F3 reachable-failed, F4 fidelity signal)."""

    def test_f4_dead_treesitter_emits_repo_fidelity_hole(self, tmp_path, monkeypatch):
        # Force tree-sitter "not live" and assert the repo-level fidelity hole fires.
        import core.code_intel.parser as P
        monkeypatch.setattr(P, "_tree_sitter_live", lambda lang: False)
        P._ts_live_cache.clear()
        (tmp_path / "a.py").write_text("def a():\n    pass\n")
        out = P.parse_repo_with_coverage(tmp_path)
        fidelity = [h for h in out.coverage_holes
                    if h["kind"] == "repo" and "tree-sitter" in h["reason"].lower()]
        assert fidelity, f"dead tree-sitter must emit a repo fidelity hole, got {out.coverage_holes}"
        assert out.status == "partial"

    def test_f4_live_treesitter_no_fidelity_hole(self, tmp_path, monkeypatch):
        import core.code_intel.parser as P
        monkeypatch.setattr(P, "_tree_sitter_live", lambda lang: True)
        P._ts_live_cache.clear()
        (tmp_path / "a.py").write_text("def a():\n    pass\n")
        out = P.parse_repo_with_coverage(tmp_path)
        fidelity = [h for h in out.coverage_holes if "tree-sitter" in h.get("reason", "").lower()]
        assert not fidelity, f"live tree-sitter must NOT emit a fidelity hole, got {fidelity}"

    def test_f3_failed_status_is_reachable(self, tmp_path, monkeypatch):
        # A LIVE tree-sitter that returns a has_error tree on a non-empty file → "failed".
        import core.code_intel.parser as P
        monkeypatch.setattr(P, "_tree_sitter_live", lambda lang: True)
        P._ts_live_cache.clear()

        class _ErrRoot:
            type = "module"; child_count = 0; has_error = True
        class _ErrTree:
            root_node = _ErrRoot()
        fake_parser = type("FP", (), {"parse": lambda self, b: _ErrTree()})()
        monkeypatch.setattr(P, "_get_cached_parser", lambda lang: fake_parser)

        f = tmp_path / "broken.py"
        f.write_text("def (((\n")  # non-empty, broken
        res, status = P.parse_file_with_status(f, tmp_path)
        assert status == "failed", f"has_error tree on non-empty file must be 'failed', got {status}"

    def test_f3_failed_recorded_as_hole_in_repo(self, tmp_path, monkeypatch):
        import core.code_intel.parser as P
        monkeypatch.setattr(P, "_tree_sitter_live", lambda lang: True)
        P._ts_live_cache.clear()
        class _ErrRoot:
            type = "module"; child_count = 0; has_error = True
        class _ErrTree:
            root_node = _ErrRoot()
        monkeypatch.setattr(P, "_get_cached_parser",
                            lambda lang: type("FP", (), {"parse": lambda self, b: _ErrTree()})())
        (tmp_path / "broken.py").write_text("def (((\n")
        out = P.parse_repo_with_coverage(tmp_path)
        assert any("broken.py" in h.get("ref", "") and h["kind"] == "file"
                   for h in out.coverage_holes), f"failed parse must be a hole, got {out.coverage_holes}"
        assert out.status == "partial"


class TestTreeSitterLiveAST:
    """Revival of the real tree-sitter AST path (run_2e46f2af).

    These tests drive the REAL _get_cached_parser construction — NO monkeypatch
    of the parser/liveness — so they FAIL on the old `tslp.get_parser()` (old-ABI
    builtins.Parser that rejects bytes) and PASS once the constructor is switched
    to the standard `tree_sitter.Parser(tslp.get_language(...))` API. This is the
    RED→GREEN guard for the fix; mutation-verified (revert the constructor → RED).
    """

    def test_tree_sitter_live_for_all_active_languages(self):
        """AC1: _tree_sitter_live must be True for every active language.

        RED on old get_parser (parse(bytes) raises → probe False for all langs).
        """
        import core.code_intel.parser as P
        P._ts_live_cache.clear()
        for lang in ("python", "javascript", "typescript", "go", "rust"):
            assert P._tree_sitter_live(lang) is True, (
                f"tree-sitter must be LIVE for {lang} — got False (AST path dead, "
                f"regex fallback). The parser constructor is broken.")

    def test_real_ast_extracts_python_symbols_with_line_spans(self, tmp_path):
        """AC2: a real Python file parses via AST → correct symbol names + line spans.

        This exercises _extract_from_tree against a genuine tree-sitter Tree — the
        path that has NEVER run live in this env (regex was the only live path).
        Asserts precise line numbers, which regex approximation would not get right.
        """
        import core.code_intel.parser as P
        P._ts_live_cache.clear()
        src = (
            "import os\n"                 # line 1
            "\n"                          # line 2
            "def known_top_fn():\n"       # line 3
            "    return os.getpid()\n"    # line 4
            "\n"                          # line 5
            "class KnownClass:\n"         # line 6
            "    def known_method(self):\n"  # line 7
            "        return 42\n"         # line 8
        )
        f = tmp_path / "sample.py"
        f.write_text(src)
        result, status = P.parse_file_with_status(f, tmp_path)
        assert status == "ok", f"clean python file must parse ok, got {status}"
        names = {n.name: n for n in result.nodes}
        # The function/class/method must be discovered by name...
        assert "known_top_fn" in names, f"AST must find top-level fn, got {sorted(names)}"
        assert "KnownClass" in names, f"AST must find class, got {sorted(names)}"
        assert "known_method" in names, f"AST must find method, got {sorted(names)}"
        # ...at their EXACT source lines (regex fallback would not be span-accurate).
        assert names["known_top_fn"].line_start == 3, names["known_top_fn"].line_start
        assert names["KnownClass"].line_start == 6, names["KnownClass"].line_start
        assert names["known_method"].line_start == 7, names["known_method"].line_start

    def test_real_ast_extracts_javascript_symbols(self, tmp_path):
        """AC2 (multi-language): a real JS file parses via AST.

        Asserts a CLASS METHOD (deepMethod) — a construct the ^-anchored regex
        fallback CANNOT extract (verified: regex finds only the class, not the
        method). This makes the test AST-discriminating: it goes RED on revert
        (dead binding → regex fallback → deepMethod absent), so it genuinely
        guards the DEFINITION_TYPES['javascript'] key this fix added — NOT a
        vacuous assertion that regex would also satisfy (Gate-2 correctness MED,
        run_2e46f2af: the old `function jsKnownFn` assertion passed on revert).
        """
        import core.code_intel.parser as P
        P._ts_live_cache.clear()
        f = tmp_path / "sample.js"
        f.write_text("class JsCls {\n  deepMethod() {\n    return 1;\n  }\n}\n")
        result, status = P.parse_file_with_status(f, tmp_path)
        assert status == "ok", f"clean js file must parse ok, got {status}"
        names = {n.name for n in result.nodes}
        assert "JsCls" in names, f"AST must find js class, got {sorted(names)}"
        # deepMethod is the AST-only discriminator — regex fallback misses methods.
        assert "deepMethod" in names, (
            f"AST must find js class METHOD (regex fallback cannot — this is the "
            f"non-vacuous guard for the javascript DEFINITION_TYPES key), got {sorted(names)}")

    def test_fallback_intact_when_get_language_raises(self, tmp_path, monkeypatch):
        """AC4: fail-safe fallback — if the binding is dead (get_language raises),
        _get_cached_parser returns None → _tree_sitter_live False → regex, never a
        crash or silent vanish. Mutation-forces the dead-binding path."""
        import core.code_intel.parser as P
        P._ts_live_cache.clear()
        # Clear any thread-local cached parser so construction is re-attempted.
        if hasattr(P._parser_cache_tls, "cache"):
            P._parser_cache_tls.cache.clear()

        def _boom(_lang):
            raise RuntimeError("simulated dead tree-sitter binding")

        # Force the construction dependency to raise (the real failure mode a
        # broken/missing grammar would exhibit).
        monkeypatch.setattr(P.tslp, "get_language", _boom)
        assert P._get_cached_parser("python") is None, (
            "a raising get_language must be caught → None (fail-safe), not propagate")
        P._ts_live_cache.clear()
        assert P._tree_sitter_live("python") is False, (
            "dead binding must make _tree_sitter_live False → regex fallback")
        # And a real file still parses (via regex) rather than crashing.
        f = tmp_path / "still_works.py"
        f.write_text("def survives():\n    return 1\n")
        result, status = P.parse_file_with_status(f, tmp_path)
        assert status in ("ok", "failed"), f"must not crash on dead binding, got {status}"


# ── Value-reference edges (Python module-scope const consumers) ────────────


class TestValueRefEdges:
    """reader-symbol -> module-scope-const `references` edges (Python).

    Closes the "change this const/table, break its readers" impact hole: static
    extraction edged calls/imports but never edged a constant to the symbols that
    read it, so a config-const change looked like "nothing depends on this".
    """

    _SRC = (
        "MAX_RETRIES = 3\n"                 # distinctive (uppercase) module const
        "DB_CONFIG = {'h': 'x'}\n"          # distinctive
        "counter = 1\n"                    # NOT distinctive (no uppercase, no _) -> no edge
        "SHADOWED = 1\n"                    # distinctive BUT reassigned in a func -> shadow-pruned
        "\n"
        "def do_work():\n"
        "    n = MAX_RETRIES\n"            # reader of MAX_RETRIES
        "    cfg = DB_CONFIG\n"            # reader of DB_CONFIG
        "    y = counter\n"                # reads a non-distinctive name -> no edge
        "    return n, cfg, y\n"
        "\n"
        "def override():\n"
        "    SHADOWED = 2\n"               # inner rebinding -> SHADOWED is shadowed
        "    return SHADOWED\n"
    )

    def _parse(self, tmp_path):
        pytest.importorskip("tree_sitter_language_pack")
        import core.code_intel.parser as P
        if not P._tree_sitter_live("python"):
            pytest.skip("tree-sitter python grammar not live in this env")
        src = tmp_path / "mod.py"
        src.write_text(self._SRC)
        return P.parse_file(src, tmp_path)

    def _ref_edges(self, result):
        return [e for e in result.edges if e.edge_type == "references"]

    def test_reference_edge_present_for_distinctive_const(self, tmp_path):
        result = self._parse(tmp_path)
        targets = {e.target_id.split(QUALIFIED_SEPARATOR)[-1] for e in self._ref_edges(result)}
        assert "MAX_RETRIES" in targets, f"expected MAX_RETRIES ref edge, got {targets}"
        assert "DB_CONFIG" in targets, f"expected DB_CONFIG ref edge, got {targets}"

    def test_reference_edges_absent_when_feature_disabled(self, tmp_path, monkeypatch):
        """Mutation: with LANG_VALUE_SPEC emptied, zero references edges."""
        pytest.importorskip("tree_sitter_language_pack")
        import core.code_intel.parser as P
        if not P._tree_sitter_live("python"):
            pytest.skip("tree-sitter python grammar not live in this env")
        monkeypatch.setattr(P, "LANG_VALUE_SPEC", {})
        src = tmp_path / "mod.py"
        src.write_text(self._SRC)
        result = P.parse_file(src, tmp_path)
        assert self._ref_edges(result) == [], "no references edges when feature disabled"

    def test_non_distinctive_name_produces_no_edge(self, tmp_path):
        result = self._parse(tmp_path)
        targets = {e.target_id.split(QUALIFIED_SEPARATOR)[-1] for e in self._ref_edges(result)}
        assert "counter" not in targets, "non-distinctive name (no uppercase/underscore) must not produce a ref edge"

    def test_shadowed_const_produces_no_edge(self, tmp_path):
        result = self._parse(tmp_path)
        targets = {e.target_id.split(QUALIFIED_SEPARATOR)[-1] for e in self._ref_edges(result)}
        assert "SHADOWED" not in targets, "a const reassigned in an inner scope must be shadow-pruned"

    def _parse_src(self, tmp_path, src):
        pytest.importorskip("tree_sitter_language_pack")
        import core.code_intel.parser as P
        if not P._tree_sitter_live("python"):
            pytest.skip("tree-sitter python grammar not live in this env")
        f = tmp_path / "m.py"
        f.write_text(src)
        return P.parse_file(f, tmp_path)

    def test_attribute_access_produces_no_edge(self, tmp_path):
        """`obj.MAX_RETRIES` is an ATTRIBUTE read of obj, NOT a read of the module
        const — must not emit a spurious references edge (false-positive impact)."""
        r = self._parse_src(
            tmp_path,
            "MAX_RETRIES = 30\n\ndef getter(config):\n    return config.MAX_RETRIES\n",
        )
        targets = {e.target_id.split(QUALIFIED_SEPARATOR)[-1] for e in self._ref_edges(r)}
        assert "MAX_RETRIES" not in targets, (
            "attribute access obj.MAX_RETRIES must NOT emit a reference edge to the module const")

    def test_parameter_shadow_produces_no_edge(self, tmp_path):
        """A function PARAMETER named like a const shadows it — a read inside that
        function reads the param, not the module const. No false edge."""
        r = self._parse_src(
            tmp_path,
            "MAX_RETRIES = 30\n\ndef helper(MAX_RETRIES=None):\n    return MAX_RETRIES + 1\n",
        )
        targets = {e.target_id.split(QUALIFIED_SEPARATOR)[-1] for e in self._ref_edges(r)}
        assert "MAX_RETRIES" not in targets, (
            "a parameter shadowing a const name must prune the const (no false edge to the reader)")

    def test_const_node_is_not_exported(self, tmp_path):
        """const nodes get is_export=0 so find_dead_code (is_export=1 filter) is clean."""
        result = self._parse(tmp_path)
        const_nodes = [n for n in result.nodes if n.node_type == "constant"]
        assert const_nodes, "expected at least one constant node (MAX_RETRIES/DB_CONFIG)"
        assert all(not n.is_export for n in const_nodes), "constant nodes must be is_export=0"

    def test_callable_node_count_invariant(self, tmp_path, monkeypatch):
        """function/class/method node COUNT is identical feature on/off (additive)."""
        pytest.importorskip("tree_sitter_language_pack")
        import core.code_intel.parser as P
        if not P._tree_sitter_live("python"):
            pytest.skip("tree-sitter python grammar not live in this env")
        src = tmp_path / "mod.py"
        src.write_text(self._SRC)

        result_on = P.parse_file(src, tmp_path)
        monkeypatch.setattr(P, "LANG_VALUE_SPEC", {})
        result_off = P.parse_file(src, tmp_path)

        def _callables(r):
            return sorted(
                n.id for n in r.nodes if n.node_type in ("function", "class", "method")
            )
        assert _callables(result_on) == _callables(result_off), (
            "value-ref feature must not change function/class/method nodes")

    def test_impact_via_blast_radius(self, tmp_path):
        """E2E: a const node id as a blast_radius seed surfaces its reader."""
        result = self._parse(tmp_path)
        from core.code_intel.graph_store import GraphStore
        gs = GraphStore(tmp_path / "g.db")
        try:
            gs.upsert_nodes([n.__dict__ for n in result.nodes])
            gs.upsert_edges([e.__dict__ for e in result.edges])
            const_id = next(
                n.id for n in result.nodes
                if n.node_type == "constant" and n.name == "MAX_RETRIES"
            )
            affected = {nid for nid, _depth in gs.blast_radius([const_id], max_depth=2)}
            reader_ids = {e.source_id for e in self._ref_edges(result)
                          if e.target_id == const_id}
            assert reader_ids, "test setup: expected a reader of MAX_RETRIES"
            assert reader_ids & affected, (
                f"blast_radius from const {const_id} must surface its reader "
                f"{reader_ids}, got affected={affected}")
        finally:
            gs.close()


# ── Value-ref language expansion (per-language validation harness) ─────────


class TestValueRefLanguages:
    """Per-language validation gate for value-reference edges (run_13667da9).

    Each Tier-A language must pass ALL assertions on a live-AST sample:
    (1) present — a distinctive module-scope const read by a function emits a
        reader->const `references` edge; (2) attribute-FP — a member access of the
        same name (obj.CONST) emits NO edge; (3) node-count invariant — enabling the
        language adds no function/class/method nodes. A language that cannot pass is
        NOT in LANG_VALUE_SPEC (disabled), so this harness enumerates the SHIPPED set
        and every member must be green — a broken descriptor fails the suite.

    Deferred languages (java/csharp — no module scope; c — preproc path) are
    asserted to emit NOTHING. php/swift/kotlin were enabled run_d021ce39 (base
    function extraction fixed via per-language _get_name name types).
    """

    # (ext, source): a module-scope distinctive const read by a function BOTH bare
    # (→ one edge) AND via a member access of the same name (obj.CONST → no edge).
    # Samples deliberately do NOT add a param-shadow function: shadow-prune is GLOBAL
    # (a name shadowed in ANY nested scope drops the module const everywhere —
    # precision-over-recall, inherited from the Python design, covered by
    # TestValueRefEdges::test_shadowed/parameter_*). This harness tests present +
    # attribute-FP per language; shadow-prune is language-agnostic once the param
    # container node type is in the descriptor.
    _SAMPLES = {
        "go": (".go",
               "package m\n"
               "const MaxRetries = 3\n"
               "func f(o Obj) int { return o.MaxRetries + MaxRetries }\n",
               "MaxRetries"),
        "rust": (".rs",
                 "const MAX_RETRIES: u32 = 3;\n"
                 "fn f(o: Obj) -> u32 { o.MAX_RETRIES + MAX_RETRIES }\n",
                 "MAX_RETRIES"),
        "typescript": (".ts",
                       "const MAX_RETRIES = 3;\n"
                       "function f(o) { return o.MAX_RETRIES + MAX_RETRIES; }\n",
                       "MAX_RETRIES"),
        "javascript": (".js",
                       "const MAX_RETRIES = 3;\n"
                       "function f(o) { return o.MAX_RETRIES + MAX_RETRIES; }\n",
                       "MAX_RETRIES"),
        "ruby": (".rb",
                 "MAX_RETRIES = 3\n"
                 "def f(o)\n  o::MAX_RETRIES\n  MAX_RETRIES\nend\n",
                 "MAX_RETRIES"),
        # php/swift/kotlin — enabled run_d021ce39 after fixing base function
        # extraction (_get_name per-language name types: php `name`,
        # swift/kotlin `simple_identifier`). Samples use MULTILINE bodies: a whole
        # kotlin class body on ONE physical line trips a grammar has_error quirk
        # (real code is never formatted that way) — pinned by
        # test_kotlin_multiline_class_parses_clean below.
        "php": (".php",
                "<?php\nconst MAX_RETRIES = 3;\n"
                "function f($o) { return $o->MAX_RETRIES + MAX_RETRIES; }\n",
                "MAX_RETRIES"),
        "swift": (".swift",
                  "let MAX_RETRIES = 3\n"
                  "func f(o: Obj) -> Int {\n  return o.MAX_RETRIES + MAX_RETRIES\n}\n",
                  "MAX_RETRIES"),
        "kotlin": (".kt",
                   "const val MAX_RETRIES = 3\n"
                   "fun f(o: Obj): Int {\n  return o.MAX_RETRIES + MAX_RETRIES\n}\n",
                   "MAX_RETRIES"),
        # c — module-scope `static const`, enabled run_078cf907. Bare read → edge;
        # the member shape here is `s->MAX_RETRIES` (field_expression). Uses a struct
        # param so the member access is valid C.
        "c": (".c",
              "static const int MAX_RETRIES = 3;\n"
              "int f(struct S* s) { return s->MAX_RETRIES + MAX_RETRIES; }\n",
              "MAX_RETRIES"),
    }

    def _parse(self, tmp_path, lang):
        pytest.importorskip("tree_sitter_language_pack")
        import core.code_intel.parser as P
        if lang not in P.LANG_VALUE_SPEC:
            pytest.skip(f"{lang} not enabled in LANG_VALUE_SPEC")
        if not P._tree_sitter_live(lang):
            pytest.skip(f"tree-sitter {lang} grammar not live")
        ext, src, nm = self._SAMPLES[lang]
        f = tmp_path / f"m{ext}"
        f.write_text(src)
        return P.parse_file(f, tmp_path), nm

    def _ref_targets(self, result):
        from core.code_intel.parser import QUALIFIED_SEPARATOR
        return [e.target_id.split(QUALIFIED_SEPARATOR)[-1]
                for e in result.edges if e.edge_type == "references"]

    @pytest.mark.parametrize("lang", list(_SAMPLES.keys()))
    def test_present_and_attribute_fp(self, tmp_path, lang):
        """A distinctive module const read by a function emits exactly ONE
        reader->const edge (the bare read), NOT the obj.CONST member access."""
        result, nm = self._parse(tmp_path, lang)
        consts = [n.name for n in result.nodes if n.node_type == "constant"]
        assert nm in consts, f"{lang}: expected constant node {nm}, got {consts}"
        targets = self._ref_targets(result)
        rc = sum(1 for t in targets if t == nm)
        assert rc == 1, (
            f"{lang}: expected exactly 1 reference edge to {nm} (the bare read, "
            f"NOT the member access obj.{nm}), got {rc} — targets={targets}")

    @pytest.mark.parametrize("lang", list(_SAMPLES.keys()))
    def test_node_count_invariant(self, tmp_path, lang, monkeypatch):
        """Enabling a language adds const nodes + references edges but leaves
        function/class/method node counts byte-identical (feature is additive)."""
        import core.code_intel.parser as P
        if lang not in P.LANG_VALUE_SPEC or not P._tree_sitter_live(lang):
            pytest.skip(f"{lang} not enabled/live")
        ext, src, _nm = self._SAMPLES[lang]
        f = tmp_path / f"m{ext}"
        f.write_text(src)

        r_on = P.parse_file(f, tmp_path)
        monkeypatch.setattr(P, "LANG_VALUE_SPEC", {})
        r_off = P.parse_file(f, tmp_path)

        def _callables(r):
            return sorted(n.id for n in r.nodes
                          if n.node_type in ("function", "class", "method"))
        assert _callables(r_on) == _callables(r_off), (
            f"{lang}: value-ref must not change function/class/method nodes")

    def test_cross_language_no_contamination(self, tmp_path):
        """A language's descriptor is applied ONLY to files of that language.
        A Python file with a name that would be a member access in Go/TS syntax
        is handled by the PYTHON descriptor only — no other language's node types
        are consulted (lookup is keyed on the file's detected language)."""
        pytest.importorskip("tree_sitter_language_pack")
        import core.code_intel.parser as P
        if not P._tree_sitter_live("python"):
            pytest.skip("python grammar not live")
        # Python file: MAX_R read bare (edge) + as an attribute (no edge). If a Go
        # 'selector_expression' guard leaked in, it would not match python's
        # 'attribute' node and the FP guard would break — assert it does NOT.
        f = tmp_path / "m.py"
        f.write_text("MAX_R = 3\ndef f(o):\n    return o.MAX_R + MAX_R\n")
        r = P.parse_file(f, tmp_path)
        rc = sum(1 for t in self._ref_targets(r) if t == "MAX_R")
        assert rc == 1, f"python attribute-guard must hold under multi-lang spec, got {rc}"

    @pytest.mark.parametrize("lang,ext,src", [
        ("java", ".java",
         "class C {\n  static final int MAX_RETRIES = 3;\n"
         "  int f() { return MAX_RETRIES; }\n}\n"),
        ("csharp", ".cs",
         "class C {\n  const int MAX_RETRIES = 3;\n"
         "  int F() { return MAX_RETRIES; }\n}\n"),
    ])
    def test_deferred_languages_emit_nothing(self, tmp_path, lang, ext, src):
        """Tier-B / deferred languages (java/csharp: no module scope — class-scope
        value-ref is a future run) are NOT in LANG_VALUE_SPEC → they emit 0 constant
        nodes and 0 references edges (feature-absent, never broken). php/swift/kotlin
        (run_d021ce39) and c static-const (run_078cf907) were REMOVED from this list
        as they were enabled. C `#define` remains permanently deferred (NO-GO), tested
        separately in TestValueRefCStaticConst::test_c_define_emits_nothing."""
        pytest.importorskip("tree_sitter_language_pack")
        import core.code_intel.parser as P
        assert lang not in P.LANG_VALUE_SPEC, (
            f"{lang} must be DEFERRED (not in LANG_VALUE_SPEC) this run")
        if not P._tree_sitter_live(lang):
            pytest.skip(f"{lang} grammar not live")
        f = tmp_path / f"m{ext}"
        f.write_text(src)
        r = P.parse_file(f, tmp_path)
        consts = [n for n in r.nodes if n.node_type == "constant"]
        refs = [e for e in r.edges if e.edge_type == "references"]
        assert not consts and not refs, (
            f"{lang} (deferred) must emit no value-ref nodes/edges, "
            f"got {len(consts)} consts, {len(refs)} refs")


class TestValueRefGate2Fixes:
    """Gate-2 adversarial findings (run_13667da9), both verified against live AST."""

    def _parse(self, tmp_path, lang, ext, src):
        pytest.importorskip("tree_sitter_language_pack")
        import core.code_intel.parser as P
        if lang not in P.LANG_VALUE_SPEC or not P._tree_sitter_live(lang):
            pytest.skip(f"{lang} not enabled/live")
        f = tmp_path / f"m{ext}"
        f.write_text(src)
        return P.parse_file(f, tmp_path)

    def _ref_targets(self, r):
        from core.code_intel.parser import QUALIFIED_SEPARATOR
        return [e.target_id.split(QUALIFIED_SEPARATOR)[-1]
                for e in r.edges if e.edge_type == "references"]

    def test_ruby_class_method_call_receiver_no_edge(self, tmp_path):
        """Gate-2 HIGH: `Foo.new` — a `constant` that is the RECEIVER of a call is a
        class reference, not a value read. Must NOT emit a references edge, while a
        genuine bare const read (BAR) still does."""
        r = self._parse(tmp_path, "ruby", ".rb",
                        "Foo = 100\nBAR = 5\ndef test\n  x = Foo.new\n  y = BAR\nend\n")
        targets = self._ref_targets(r)
        assert "Foo" not in targets, "Foo.new (call receiver) must NOT emit a value-ref edge"
        assert "BAR" in targets, "a genuine bare const read (BAR) must still emit an edge"

    def test_go_grouped_const_all_names_extracted(self, tmp_path):
        """Gate-2 MED: `const ( A=1; B=2 )` (grouped) must extract BOTH names + edges,
        not silently drop all but the first (recall gap)."""
        r = self._parse(tmp_path, "go", ".go",
                        "package m\nconst (\n  MaxRetries = 3\n  MinRetries = 1\n)\n"
                        "func f() int { return MaxRetries + MinRetries }\n")
        consts = {n.name for n in r.nodes if n.node_type == "constant"}
        targets = set(self._ref_targets(r))
        assert {"MaxRetries", "MinRetries"} <= consts, f"both grouped consts must be nodes, got {consts}"
        assert {"MaxRetries", "MinRetries"} <= targets, f"both grouped consts must have ref edges, got {targets}"

    def test_ts_multi_declarator_all_names_extracted(self, tmp_path):
        """Gate-2 MED sibling: `const A=1, B=2` (multi-declarator) must extract BOTH."""
        r = self._parse(tmp_path, "typescript", ".ts",
                        "const MAX_A = 1, MAX_B = 2;\n"
                        "function f() { return MAX_A + MAX_B; }\n")
        targets = set(self._ref_targets(r))
        assert {"MAX_A", "MAX_B"} <= targets, f"both declarators must have ref edges, got {targets}"


class TestBaseFunctionExtractionPhpSwiftKotlin:
    """run_d021ce39 — the base function-extraction fix that UNBLOCKS value-ref for
    php/swift/kotlin. Before: _get_name hardcoded {identifier,property_identifier,
    type_identifier}; php names are `name` nodes and swift/kotlin function names are
    `simple_identifier`, so their functions/methods were SILENTLY DROPPED (php=0
    nodes, swift/kotlin=class-only). These tests exercise the REAL entry point
    (parse_file / parse_file_with_status), NOT _extract_from_tree — the M3-skeptic
    lesson that a kotlin file can extract nodes yet be discarded by the coverage gate.
    """

    _CASES = {
        "php": (".php",
                "<?php\nfunction do_thing($x) { return $x; }\n"
                "class Foo { public function bar() { return 1; } }\n"),
        "swift": (".swift",
                  "func doThing(x: Int) -> Int {\n  return x\n}\n"
                  "class Foo {\n  func bar() -> Int {\n    return 1\n  }\n}\n"),
        "kotlin": (".kt",
                   "fun doThing(x: Int): Int {\n  return x\n}\n"
                   "class Foo {\n  fun bar(): Int {\n    return 1\n  }\n}\n"),
    }

    def _parse(self, tmp_path, lang):
        pytest.importorskip("tree_sitter_language_pack")
        import core.code_intel.parser as P
        if not P._tree_sitter_live(lang):
            pytest.skip(f"tree-sitter {lang} grammar not live")
        ext, src = self._CASES[lang]
        f = tmp_path / f"m{ext}"
        f.write_text(src)
        return P.parse_file_with_status(f, tmp_path)

    @pytest.mark.parametrize("lang", ["php", "swift", "kotlin"])
    def test_function_and_method_extracted_through_parse_file(self, tmp_path, lang):
        """A top-level function + a class-with-method yields BOTH a `function` and a
        `method` node (plus the `class`), through the REAL parse_file entry point."""
        result, status = self._parse(tmp_path, lang)
        assert status == "ok", f"{lang}: parse_file status must be ok, got {status}"
        by_type = {}
        for n in result.nodes:
            by_type.setdefault(n.node_type, []).append(n.name)
        assert "function" in by_type, f"{lang}: top-level function dropped — got {by_type}"
        assert "method" in by_type, f"{lang}: class method dropped — got {by_type}"
        assert "class" in by_type, f"{lang}: class dropped — got {by_type}"
        assert "doThing" in by_type["function"] or "do_thing" in by_type["function"]
        assert "bar" in by_type["method"]

    def test_kotlin_multiline_class_parses_clean(self, tmp_path):
        """Kotlin coverage-gate regression: a multiline class body (how real code is
        written) must parse clean (_tree_has_error False) so its nodes survive the
        gate. A whole class body on ONE physical line trips a known grammar has_error
        quirk — we deliberately do NOT loosen the banking-grade coverage gate for that
        unrealistic shape (Gate-1 verdict), so the harness uses multiline samples."""
        pytest.importorskip("tree_sitter_language_pack")
        import core.code_intel.parser as P
        if not P._tree_sitter_live("kotlin"):
            pytest.skip("kotlin grammar not live")
        parser = P._get_cached_parser("kotlin")
        tree = parser.parse(
            b"const val MAX = 3\nfun f(): Int {\n  return MAX\n}\n"
            b"class Foo {\n  fun bar(): Int {\n    return 1\n  }\n}\n")
        assert not P._tree_has_error(tree), (
            "multiline kotlin must parse clean so its nodes survive the coverage gate")


class TestGetNamePerLanguage:
    """run_d021ce39 — _get_name resolves the definition name using per-language node
    types (NAME_NODE_TYPES). Directly unit-tests the fix + its non-regression: the 6
    pre-existing languages MUST still resolve via the default 3 types (widening for
    php/swift/kotlin cannot add spurious names elsewhere — the map is keyed)."""

    def _first_def(self, lang, src, def_type):
        pytest.importorskip("tree_sitter_language_pack")
        import core.code_intel.parser as P
        if not P._tree_sitter_live(lang):
            pytest.skip(f"{lang} grammar not live")
        parser = P._get_cached_parser(lang)
        tree = parser.parse(bytes(src, "utf8"))

        found = []

        def walk(n):
            if n.type == def_type:
                found.append(n)
            for c in n.children:
                walk(c)
        walk(tree.root_node)
        assert found, f"{lang}: no {def_type} node in sample"
        return P._get_name(found[0], lang)

    def test_php_name_node_resolved(self):
        import core.code_intel.parser as P
        assert self._first_def("php", "<?php\nfunction hello() {}\n",
                               "function_definition") == "hello"
        # regression: without the fix _get_name returned None (name is a `name` node)
        assert "name" in P.NAME_NODE_TYPES["php"]

    def test_swift_simple_identifier_resolved(self):
        assert self._first_def("swift", "func hello() {}\n",
                               "function_declaration") == "hello"

    def test_kotlin_simple_identifier_resolved(self):
        assert self._first_def("kotlin", "fun hello() {}\n",
                               "function_declaration") == "hello"

    def test_default_languages_unaffected(self):
        """The 6 pre-existing langs resolve names via the default types — the map
        does NOT widen them (keyed lookup, default fallback)."""
        import core.code_intel.parser as P
        assert P.NAME_NODE_TYPES.get("python") is None  # falls to default
        assert self._first_def("python", "def hello():\n    pass\n",
                               "function_definition") == "hello"
        assert self._first_def("go", "package m\nfunc Hello() {}\n",
                               "function_declaration") == "Hello"


class TestValueRefFalsePositiveGuardsNewLangs:
    """run_d021ce39 Gate-1 findings — php/swift/kotlin value-ref must suppress the
    per-language false-positive shapes (verified against live AST before coding)."""

    def _refs(self, tmp_path, lang, ext, src):
        pytest.importorskip("tree_sitter_language_pack")
        import core.code_intel.parser as P
        if lang not in P.LANG_VALUE_SPEC or not P._tree_sitter_live(lang):
            pytest.skip(f"{lang} not enabled/live")
        f = tmp_path / f"m{ext}"
        f.write_text(src)
        r = P.parse_file(f, tmp_path)
        return [e.target_id.split(P.QUALIFIED_SEPARATOR)[-1]
                for e in r.edges if e.edge_type == "references"]

    def test_php_static_access_no_edge(self, tmp_path):
        """`Foo::MAX` (class_constant_access_expression) is NOT a value read."""
        t = self._refs(tmp_path, "php", ".php",
                       "<?php\nconst MAX = 3;\nfunction f() { return Foo::MAX; }\n")
        assert "MAX" not in t, f"Foo::MAX must not emit a value-ref edge, got {t}"

    def test_php_new_instantiation_no_edge(self, tmp_path):
        """`new MAX()` (object_creation_expression) is a class instantiation, not a
        value read — CONST is not the first child (the `new` keyword is), so the
        member guard (last identifier-ish child), not the receiver guard, catches it."""
        t = self._refs(tmp_path, "php", ".php",
                       "<?php\nconst MAX = 3;\nfunction f() { return new MAX(); }\n")
        assert "MAX" not in t, f"new MAX() must not emit a value-ref edge, got {t}"

    def test_php_bare_read_still_emits(self, tmp_path):
        t = self._refs(tmp_path, "php", ".php",
                       "<?php\nconst MAX = 3;\nfunction f() { return MAX; }\n")
        assert "MAX" in t, f"bare const read must emit an edge, got {t}"

    @pytest.mark.parametrize("lang,ext,bind", [
        ("swift", ".swift", "let MAX = 3\n"),
        ("kotlin", ".kt", "const val MAX = 3\n"),
    ])
    def test_swift_kotlin_call_receiver_no_edge(self, tmp_path, lang, ext, bind):
        """`MAX()` (call_expression, MAX is the first child) is a call/construction,
        not a value read — receiver guard."""
        body = ("func f() -> Int {\n  return MAX()\n}\n" if lang == "swift"
                else "fun f(): Int {\n  return MAX()\n}\n")
        t = self._refs(tmp_path, lang, ext, bind + body)
        assert "MAX" not in t, f"{lang}: MAX() call must not emit a value-ref edge, got {t}"

    @pytest.mark.parametrize("lang,ext,bind", [
        ("swift", ".swift", "let MAX = 3\n"),
        ("kotlin", ".kt", "const val MAX = 3\n"),
    ])
    def test_swift_kotlin_member_access_no_edge(self, tmp_path, lang, ext, bind):
        """`o.MAX` (member under navigation_suffix) is a member access, not a bare
        module-const read."""
        body = ("func f(o: Obj) -> Int {\n  return o.MAX\n}\n" if lang == "swift"
                else "fun f(o: Obj): Int {\n  return o.MAX\n}\n")
        t = self._refs(tmp_path, lang, ext, bind + body)
        assert "MAX" not in t, f"{lang}: o.MAX member access must not emit an edge, got {t}"

    # ── Gate-2 findings (run_d021ce39): php reuses `name` for const reads AND the
    # inner leaf of $variable_name / qualified_name → three false-positive shapes. ──

    def test_php_local_variable_no_edge(self, tmp_path):
        """Gate-2 F1 (HIGH): a php LOCAL `$MAX` shares the const's node type (`name`
        under `variable_name`). Using the local must NOT emit a value-ref edge to a
        same-named module const."""
        t = self._refs(tmp_path, "php", ".php",
                       "<?php\nconst MAX_TIMEOUT = 3;\n"
                       "function f() { $MAX_TIMEOUT = 99; return $MAX_TIMEOUT; }\n")
        assert "MAX_TIMEOUT" not in t, f"php local $var must not emit a const edge, got {t}"

    def test_php_parameter_shadows_const(self, tmp_path):
        """Gate-2 F2 (HIGH): a php parameter `$MAX` (nested simple_parameter >
        variable_name > name) must shadow-prune the module const — no const node,
        no edge — matching swift/kotlin behavior."""
        import core.code_intel.parser as P
        if "php" not in P.LANG_VALUE_SPEC or not P._tree_sitter_live("php"):
            pytest.skip("php not enabled/live")
        f = tmp_path / "m.php"
        f.write_text("<?php\nconst MAX_RETRIES = 3;\n"
                     "function f($MAX_RETRIES) { return $MAX_RETRIES; }\n")
        r = P.parse_file(f, tmp_path)
        consts = [n.name for n in r.nodes if n.node_type == "constant"]
        refs = [e.target_id.split(P.QUALIFIED_SEPARATOR)[-1]
                for e in r.edges if e.edge_type == "references"]
        assert "MAX_RETRIES" not in consts, f"php param must shadow-prune the const, got consts={consts}"
        assert "MAX_RETRIES" not in refs, f"php param must emit no edge, got {refs}"

    def test_php_typed_parameter_shadows_const(self, tmp_path):
        """Gate-2 F2 variant: a TYPED php param `Foo $MAX` must resolve the PARAM name
        (not the type name) and shadow-prune the const."""
        import core.code_intel.parser as P
        if "php" not in P.LANG_VALUE_SPEC or not P._tree_sitter_live("php"):
            pytest.skip("php not enabled/live")
        f = tmp_path / "m.php"
        f.write_text("<?php\nconst MAX_RETRIES = 3;\n"
                     "function f(int $MAX_RETRIES) { return $MAX_RETRIES; }\n")
        r = P.parse_file(f, tmp_path)
        refs = [e.target_id.split(P.QUALIFIED_SEPARATOR)[-1]
                for e in r.edges if e.edge_type == "references"]
        assert "MAX_RETRIES" not in refs, f"typed php param must shadow-prune, got {refs}"

    def test_php_namespaced_const_no_edge(self, tmp_path):
        """Gate-2 F3 (MEDIUM): `App\\MAX` (qualified_name) is a namespaced reference,
        not a bare read of the local module const `MAX`."""
        t = self._refs(tmp_path, "php", ".php",
                       "<?php\nconst MAX = 3;\nfunction f() { return App\\MAX; }\n")
        assert "MAX" not in t, f"php namespaced App\\MAX must not emit a local-const edge, got {t}"


class TestCCppBaseFunctionExtraction:
    """run_88512360 — c/cpp function/method NAMES are nested in a `declarator` field
    (not a direct child), so the flat _get_name scan dropped them: a C function
    extracted 0 nodes, a C++ function/method was dropped (only the class survived),
    and a C `struct` was MISLABELED node_type='function'. Fix: c/cpp-scoped
    declarator descent (codegraph pattern) + struct_specifier→class classification.
    Exercises the REAL parse_file entry point.
    """

    def _nodes(self, tmp_path, lang, ext, src):
        pytest.importorskip("tree_sitter_language_pack")
        import core.code_intel.parser as P
        if not P._tree_sitter_live(lang):
            pytest.skip(f"tree-sitter {lang} grammar not live")
        f = tmp_path / f"m{ext}"
        f.write_text(src)
        result, status = P.parse_file_with_status(f, tmp_path)
        assert status == "ok", f"{lang}: parse_file status must be ok, got {status}"
        by_type = {}
        for n in result.nodes:
            by_type.setdefault(n.node_type, []).append(n.name)
        return by_type

    def test_c_function_extracted(self, tmp_path):
        """C top-level function: name nested in function_declarator."""
        bt = self._nodes(tmp_path, "c", ".c", "int add(int x) { return x + 1; }\n")
        assert "add" in bt.get("function", []), f"C function 'add' dropped — got {bt}"

    def test_c_pointer_return_function_extracted(self, tmp_path):
        """C function with pointer return: descent through pointer_declarator wrapper."""
        bt = self._nodes(tmp_path, "c", ".c", "char* dup(char* s) { return s; }\n")
        assert "dup" in bt.get("function", []), f"C pointer-return function 'dup' dropped — got {bt}"

    def test_c_struct_classified_as_class_not_function(self, tmp_path):
        """C struct: was MISLABELED node_type='function' (struct_specifier matched no
        type keyword). Must be a type node (class), never a function."""
        bt = self._nodes(tmp_path, "c", ".c", "struct Foo { int a; };\n")
        assert "Foo" in bt.get("class", []), f"C struct 'Foo' must be a class node — got {bt}"
        assert "Foo" not in bt.get("function", []), f"C struct 'Foo' must NOT be a function — got {bt}"

    def test_cpp_free_function_and_method_and_class(self, tmp_path):
        """C++: free function (declarator descent), class method (name=field_identifier,
        nested inside class body), and the class itself — all extracted."""
        bt = self._nodes(tmp_path, "cpp", ".cpp",
                         "int add(int x) { return x + 1; }\n"
                         "class Foo {\npublic:\n  int bar() { return 1; }\n};\n")
        assert "add" in bt.get("function", []), f"C++ free function 'add' dropped — got {bt}"
        assert "bar" in bt.get("method", []), f"C++ method 'bar' dropped — got {bt}"
        assert "Foo" in bt.get("class", []), f"C++ class 'Foo' dropped — got {bt}"

    def test_cpp_destructor_keeps_tilde(self, tmp_path):
        """C++ destructor name is destructor_name(~ + identifier); the wrapper's full
        text (~S) must be returned, NOT the inner identifier S (Gate-1 correction)."""
        bt = self._nodes(tmp_path, "cpp", ".cpp",
                         "class S {\npublic:\n  ~S() {}\n};\n")
        assert "~S" in bt.get("method", []), f"C++ destructor must keep the tilde (~S) — got {bt}"

    def test_cpp_operator_overload_extracted(self, tmp_path):
        """C++ operator overload name is operator_name (no inner identifier) — the
        wrapper's full text (operator+) must be returned, else it's dropped (Gate-1)."""
        bt = self._nodes(tmp_path, "cpp", ".cpp",
                         "class V {\npublic:\n  V operator+(int x) { return *this; }\n};\n")
        assert "operator+" in bt.get("method", []), f"C++ operator+ dropped — got {bt}"

    def test_cpp_conversion_operator_extracted(self, tmp_path):
        """C++ user-defined conversion operator: name node is operator_cast whose text
        is `operator int()`; the name is rebuilt from non-parameter children →
        `operator int` (Gate-2 LOW run_88512360; without the handler it was dropped)."""
        bt = self._nodes(tmp_path, "cpp", ".cpp",
                         "struct C { operator int() { return 1; } };\n")
        assert "operator int" in bt.get("method", []), \
            f"C++ conversion operator dropped — got {bt}"

    def test_cpp_param_type_not_mistaken_for_name(self, tmp_path):
        """codegraph's documented bug: without skipping parameter_list, a function
        `int compute(const char* name)` was named after its parameter's type. The BFS
        must skip parameter_list → name is 'compute'."""
        bt = self._nodes(tmp_path, "cpp", ".cpp",
                         "int compute(const char* name) { return 0; }\n")
        assert "compute" in bt.get("function", []), f"C++ function must be named 'compute' — got {bt}"
        # nothing should be named after the param/type
        allnames = [n for names in bt.values() for n in names]
        assert "name" not in allnames and "char" not in allnames, \
            f"param name/type leaked as a definition name — got {bt}"


class TestGetNameCCppDeclaratorDescent:
    """run_88512360 — unit tests for _c_family_declarator_name + _get_name c/cpp path,
    incl the cross-language SCOPE guarantee: python/php (which also use
    function_definition) must NOT go through declarator descent."""

    def _get_name_of(self, lang, src, def_type):
        pytest.importorskip("tree_sitter_language_pack")
        import core.code_intel.parser as P
        if not P._tree_sitter_live(lang):
            pytest.skip(f"{lang} grammar not live")
        tree = P._get_cached_parser(lang).parse(bytes(src, "utf8"))
        found = []

        def walk(n):
            if n.type == def_type:
                found.append(n)
            for c in n.children:
                walk(c)
        walk(tree.root_node)
        assert found, f"{lang}: no {def_type} node in sample"
        return P._get_name(found[0], lang)

    def test_c_function_name_via_declarator(self):
        assert self._get_name_of("c", "int add(int x) { return x; }\n",
                                 "function_definition") == "add"

    def test_cpp_qualified_out_of_line_method(self):
        """`int Foo::bar() {...}` → qualified_identifier → last :: segment = bar."""
        assert self._get_name_of("cpp", "int Foo::bar() { return 1; }\n",
                                 "function_definition") == "bar"

    def test_c_scope_gate_does_not_touch_python(self):
        """python shares function_definition but its name is a DIRECT child and it
        has no `declarator` field — must resolve via the flat scan, unchanged."""
        assert self._get_name_of("python", "def foo(x):\n    return x\n",
                                 "function_definition") == "foo"

    def test_c_scope_gate_does_not_touch_php(self):
        """php shares function_definition; name is a direct `name` node — unchanged."""
        assert self._get_name_of("php", "<?php\nfunction greet() {}\n",
                                 "function_definition") == "greet"

    def test_anonymous_struct_no_crash(self):
        """An anonymous struct has no name field — _get_name returns None (dropped),
        never crashes."""
        import core.code_intel.parser as P
        if not P._tree_sitter_live("c"):
            pytest.skip("c grammar not live")
        tree = P._get_cached_parser("c").parse(bytes("struct { int a; } x;\n", "utf8"))
        sp = [n for n in tree.root_node.children[0].children
              if n.type == "struct_specifier"]
        if sp:
            assert P._get_name(sp[0], "c") is None


# ─── DoD2 (run_fe26ed6c): parser honors the target repo's .gitignore ───

class TestGitignoreHonoring:
    """parse_repo_with_coverage drops files the repo's .gitignore ignores (batched
    git check-ignore), records them as coverage_holes kind='gitignored' (O030),
    and NEVER drops a tracked source file. Fail-open on non-git / git error."""

    def _git_repo(self, tmp_path):
        import subprocess
        repo = tmp_path / "repo"; repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
        return repo

    def test_gitignored_build_dir_skipped(self, tmp_path):
        from core.code_intel.parser import parse_repo_with_coverage
        repo = self._git_repo(tmp_path)
        (repo / ".gitignore").write_text("out/\n", encoding="utf-8")
        (repo / "src").mkdir(); (repo / "out").mkdir()
        (repo / "src" / "app.py").write_text("def a(): pass\n", encoding="utf-8")
        (repo / "out" / "gen.py").write_text("def g(): pass\n", encoding="utf-8")
        res = parse_repo_with_coverage(repo)
        parsed = {r.file_path for r in res.results}
        assert "src/app.py" in parsed, "tracked source must be parsed"
        assert "out/gen.py" not in parsed, "gitignored file must be skipped"
        # O030: recorded, not silent
        holes = {h["ref"] for h in res.coverage_holes if h.get("kind") == "gitignored"}
        assert "out/gen.py" in holes

    def test_tracked_source_never_dropped(self, tmp_path):
        # A .py that matches a gitignore pattern but is TRACKED must NOT be skipped
        # (git check-ignore does not report tracked files).
        import subprocess
        from core.code_intel.parser import parse_repo_with_coverage
        repo = self._git_repo(tmp_path)
        (repo / ".gitignore").write_text("*.py\n", encoding="utf-8")  # ignore ALL .py
        (repo / "keep.py").write_text("def k(): pass\n", encoding="utf-8")
        subprocess.run(["git", "add", "-f", "keep.py"], cwd=repo, check=True)  # force-track
        res = parse_repo_with_coverage(repo)
        parsed = {r.file_path for r in res.results}
        assert "keep.py" in parsed, "a TRACKED .py must survive even if pattern matches"

    def test_non_git_repo_unchanged(self, tmp_path):
        # No .git → gitignore path is skipped entirely, SKIP_DIRS still applies.
        from core.code_intel.parser import parse_repo_with_coverage
        repo = tmp_path / "plain"; repo.mkdir()
        (repo / "node_modules").mkdir()
        (repo / "src").mkdir()
        (repo / "src" / "app.py").write_text("def a(): pass\n", encoding="utf-8")
        (repo / "node_modules" / "dep.py").write_text("x=1\n", encoding="utf-8")
        res = parse_repo_with_coverage(repo)
        parsed = {r.file_path for r in res.results}
        assert "src/app.py" in parsed
        assert "node_modules/dep.py" not in parsed  # SKIP_DIRS still works
        # no gitignored holes on a non-git repo
        assert not any(h.get("kind") == "gitignored" for h in res.coverage_holes)

    def test_git_error_fails_open(self, tmp_path, monkeypatch):
        # git check-ignore raising → treat as 'nothing extra ignored', never crash.
        from core.code_intel import parser as P
        repo = self._git_repo(tmp_path)
        (repo / "app.py").write_text("def a(): pass\n", encoding="utf-8")
        def _boom(*a, **k):
            raise OSError("git exploded")
        monkeypatch.setattr(P.subprocess, "run", _boom)
        res = P.parse_repo_with_coverage(repo)
        parsed = {r.file_path for r in res.results}
        assert "app.py" in parsed  # fail-open: file still parsed


class TestValueRefCStaticConst:
    """run_078cf907 — C `static const` value-ref (RUN 1 of the run_0f977b9f research).
    C consts are MODULE-SCOPE (declaration>init_declarator>identifier), enabled via a
    `c` descriptor + the first use of qualifier_gate (text=='const'). The reader is a
    bare `identifier` (node-type-reuse trap) so the php-family FP guards apply. C
    `#define` is permanently deferred (NO-GO — scopeless textual reads)."""

    def _refs(self, tmp_path, src):
        pytest.importorskip("tree_sitter_language_pack")
        import core.code_intel.parser as P
        if "c" not in P.LANG_VALUE_SPEC or not P._tree_sitter_live("c"):
            pytest.skip("c not enabled/live")
        f = tmp_path / "m.c"
        f.write_text(src)
        r = P.parse_file(f, tmp_path)
        return sorted(e.target_id.split(P.QUALIFIED_SEPARATOR)[-1]
                      for e in r.edges if e.edge_type == "references")

    def test_static_const_bare_read_emits_edge(self, tmp_path):
        assert self._refs(tmp_path,
            "static const int MAX_R=3;\nint f(){ return MAX_R; }\n") == ["MAX_R"]

    def test_const_without_static_emits_edge(self, tmp_path):
        assert self._refs(tmp_path,
            "const int MAX_R=3;\nint f(){ return MAX_R; }\n") == ["MAX_R"]

    def test_east_const_emits_edge(self, tmp_path):
        """`int const MAX=3` (east-const) — type_qualifier is still a direct child."""
        assert self._refs(tmp_path,
            "int const MAX_R=3;\nint f(){ return MAX_R; }\n") == ["MAX_R"]

    def test_mutable_global_no_edge(self, tmp_path):
        """A mutable top-level `int x=3` has NO const type_qualifier → not a const."""
        assert self._refs(tmp_path,
            "int MUT_GLOBAL=3;\nint f(){ return MUT_GLOBAL; }\n") == []

    def test_static_noncosnt_no_edge(self, tmp_path):
        """`static int x` has storage_class_specifier but NO type_qualifier."""
        assert self._refs(tmp_path,
            "static int STAT_X=3;\nint f(){ return STAT_X; }\n") == []

    def test_volatile_no_edge(self, tmp_path):
        """THE M3 trap: volatile is ALSO a type_qualifier — the gate is text=='const',
        so a `volatile int` must NOT be collected as a const."""
        assert self._refs(tmp_path,
            "volatile int VOL_X=3;\nint f(){ return VOL_X; }\n") == []

    def test_leading_const_member_access_no_edge(self, tmp_path):
        """`CFG.x` — CFG is a leading `identifier` (reader), so member_access_types=
        {field_expression} is load-bearing here (Gate-1 verified)."""
        assert self._refs(tmp_path,
            "static const int CFG=3;\nint f(){ return CFG.x; }\n") == []

    def test_arrow_member_no_edge(self, tmp_path):
        """`s->MAX_R` — the member is a field_identifier, excluded by reader_types."""
        assert self._refs(tmp_path,
            "struct S{int MAX_R;};\nstatic const int MAX_R=3;\n"
            "int g(struct S* s){ return s->MAX_R; }\n") == []

    def test_local_var_shadows_const(self, tmp_path):
        assert self._refs(tmp_path,
            "static const int MAX_R=3;\nint f(){ int MAX_R=9; return MAX_R; }\n") == []

    def test_param_shadows_const(self, tmp_path):
        assert self._refs(tmp_path,
            "static const int MAX_R=3;\nint f(int MAX_R){ return MAX_R; }\n") == []

    def test_c_define_emits_nothing(self, tmp_path):
        """C `#define` is permanently deferred (NO-GO): preproc_def is not a binding
        spec, so a #define const emits no node and no edge."""
        import core.code_intel.parser as P
        if "c" not in P.LANG_VALUE_SPEC or not P._tree_sitter_live("c"):
            pytest.skip("c not enabled/live")
        f = tmp_path / "m.c"
        f.write_text("#define K 1\nint f(){ return K; }\n")
        r = P.parse_file(f, tmp_path)
        consts = [n for n in r.nodes if n.node_type == "constant"]
        assert not consts, f"#define must emit no const node, got {consts}"
        assert not self._refs(tmp_path, "#define K 1\nint f(){ return K; }\n")

    def test_pointer_const_documented_gap(self, tmp_path):
        """DOCUMENTED RECALL GAP (Gate-1): a pointer const `const char *NAME` has its
        name under pointer_declarator (not init_declarator) → NOT collected. Asserted
        so the gap is intentional, not accidental (conservative: drop, never
        false-emit). If pointer-consts are needed later, extend the binding path."""
        assert self._refs(tmp_path,
            'const char *NAME="x";\nint f(){ return NAME==0; }\n') == []
