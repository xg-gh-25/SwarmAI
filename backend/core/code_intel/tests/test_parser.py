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
