"""Wiring-truth guard for MEMORY injection (run_b2d62f47 -> run_710a2582 -> CDF cutover).

This is NOT a behavior test — it is an executable assertion of a PRODUCTION
WIRING FACT: MEMORY.md reaches the prompt only through
``core.memory_injection.select_context_file``, whole when it fits, otherwise by
QUERY-FREE whole-entry selection (CDF r16.6 §12). There is NO embedding, NO
hybrid/vector scorer and NO per-query selection on the injection path (recall is
pure FTS5+BM25 — PRI11). The vector scorer and the old pass-through
``select_memory_sections`` are deleted.

Why lock this as a test: it is a forcing function. The day someone wires an
embedding or the user's query into MEMORY injection, this test goes RED.
(``test_cdf_injection_selection`` holds the selector's own behaviour.)
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

import core.memory_index as memory_index
from core.memory_injection import select_context_file


def test_memory_injection_has_no_embedding_and_no_query():
    # 1 — the dead scorers stay deleted.
    for gone in ("_hybrid_section_scores", "FULL_INJECTION_THRESHOLD", "select_memory_sections"):
        assert not hasattr(memory_index, gone), (
            f"{gone} reappeared — MEMORY injection goes through "
            "memory_injection.select_context_file only.")
    # 2 — the selector cannot see the turn's query.
    params = set(inspect.signature(select_context_file).parameters)
    assert not params & {"query", "user_message", "message"}, params
    # 3 — the loader wires no embedding/hybrid call, and does call the selector.
    loader_src = (
        Path(__file__).resolve().parents[1] / "core" / "context_directory_loader.py"
    ).read_text()
    called = {
        node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id
        for node in ast.walk(ast.parse(loader_src))
        if isinstance(node, ast.Call)
        and isinstance(node.func, (ast.Name, ast.Attribute))
    }
    leaked = {"_hybrid_section_scores", "hybrid_memory_search", "embed_text"} & called
    assert not leaked, f"context_directory_loader wires a banned call {leaked}"
    assert "select_context_file" in called
