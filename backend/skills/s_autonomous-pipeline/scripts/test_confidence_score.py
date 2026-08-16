"""Tests for confidence_score language classification (AC5, run_66d5c01e).

Purpose: the ext-set heuristics in `_has_backend_files` / `_has_frontend_files`
must classify the common languages a NON-SwarmAI project may use, not just
SwarmAI's own Python/TypeScript stack. Before this change the backend set was
only {.py,.go,.rs,.java} — a Kotlin/C++/Ruby/C#/PHP/Scala/TS-backend project
scored wrong. These tests pin the widened classification and are mutation-proof
(removing a newly-added suffix must turn its assertion RED).
"""
import os
import sys

# The module under test lives next to this file (a skill scripts/ dir, not on
# the default test path when pytest is invoked from the repo root). Add the
# sibling dir so the import works regardless of cwd.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from confidence_score import _has_backend_files, _has_frontend_files  # noqa: E402


class TestBackendClassification:
    def test_mainstream_backend_langs_still_detected(self):
        # Non-regression: the original set must keep working.
        for f in ["svc.py", "main.go", "lib.rs", "App.java"]:
            assert _has_backend_files([f]), f"{f} should classify as backend"

    def test_widened_backend_langs_detected(self):
        # AC5: languages a non-SwarmAI project may use.
        for f in ["engine.cpp", "core.c", "Svc.cs", "app.rb", "Main.kt",
                  "job.scala", "server.ts"]:
            assert _has_backend_files([f]), f"{f} should classify as backend"

    def test_non_code_not_backend(self):
        for f in ["README.md", "data.json", "logo.png"]:
            assert not _has_backend_files([f]), f"{f} must not be backend"


class TestFrontendClassification:
    def test_frontend_langs_detected(self):
        for f in ["App.tsx", "index.ts", "main.jsx", "style.css",
                  "page.html", "Widget.svelte", "View.vue"]:
            assert _has_frontend_files([f]), f"{f} should classify as frontend"

    def test_backend_only_not_frontend(self):
        assert not _has_frontend_files(["main.go", "lib.rs", "app.rb"])

    def test_ts_double_classification_ok(self):
        # .ts is intentionally in BOTH sets (Node/Deno backends) — the scoring
        # logic uses each as an independent presence signal (AND), so a file
        # matching both is by design, not a bug. Pin it so a future maintainer
        # can't silently break the dual-classification.
        assert _has_frontend_files(["index.ts"]) and _has_backend_files(["index.ts"])
