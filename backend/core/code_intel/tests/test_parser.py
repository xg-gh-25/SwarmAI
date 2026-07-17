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
        """Mutation: with VALUE_DEFINITION_TYPES emptied, zero references edges."""
        pytest.importorskip("tree_sitter_language_pack")
        import core.code_intel.parser as P
        if not P._tree_sitter_live("python"):
            pytest.skip("tree-sitter python grammar not live in this env")
        monkeypatch.setattr(P, "VALUE_DEFINITION_TYPES", {})
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
        monkeypatch.setattr(P, "VALUE_DEFINITION_TYPES", {})
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
