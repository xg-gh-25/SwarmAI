"""Wiring-truth guard for MEMORY injection (run_b2d62f47 → updated run_710a2582).

This is NOT a behavior test — it is an executable assertion of a PRODUCTION
WIRING FACT: MEMORY.md injection is pure keyword full-injection. There is NO
selective mode, NO hybrid/vector scorer, NO embedding on the injection path
(recall is pure FTS5+BM25 — PRI11). The vector scorer (`_hybrid_section_scores`,
the old 0.6·vector+0.4·keyword path) and the selective-injection machinery were
DELETED 2026-08-14; the loader full-injects MEMORY.md via
`extract_body_without_index` (whole body minus any stale index block).

Why lock this as a test: it is a forcing function. The day someone reintroduces
an embedding/selective path on MEMORY injection, this test goes RED — forcing a
conscious update instead of letting the "formula in code ≠ behavior in prod" gap
drift back (the confusion the original run started from).
"""
from __future__ import annotations

import ast
from pathlib import Path

import core.memory_index as memory_index


def test_memory_injection_is_pure_fulltext_no_embedding():
    # 1 — the dead vector scorer stays deleted (no reintroduction).
    assert not hasattr(memory_index, "_hybrid_section_scores"), (
        "_hybrid_section_scores reappeared — the vector/hybrid MEMORY scorer was "
        "deleted 2026-08-14 (PRI11: FTS5-only). Do NOT reintroduce an embedding "
        "path on the injection line."
    )
    # 2 — no selective-threshold constant (full-injection has no threshold).
    assert not hasattr(memory_index, "FULL_INJECTION_THRESHOLD"), (
        "FULL_INJECTION_THRESHOLD reappeared — MEMORY.md is ALWAYS full-injected; "
        "there is no selective-mode threshold."
    )
    # 3 — select_memory_sections, if still present, must return the FULL body
    #     (inert params only, no scoring). Assert it's a thin full-body passthrough.
    if hasattr(memory_index, "select_memory_sections"):
        body = "## A\nalpha\n\n## B\nbeta\n"
        out = memory_index.select_memory_sections(body)
        assert "alpha" in out and "beta" in out, (
            "select_memory_sections dropped content — it must return the WHOLE "
            "body (full-injection), not a selected subset."
        )
    # 4 — the loader must NOT wire any embedding/selective call on the MEMORY path.
    loader_src = (
        Path(__file__).resolve().parents[1] / "core" / "context_directory_loader.py"
    ).read_text()
    tree = ast.parse(loader_src)
    banned = {"_hybrid_section_scores", "hybrid_memory_search", "embed_text"}
    called = {
        node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, (ast.Name, ast.Attribute))
    }
    leaked = banned & called
    assert not leaked, (
        f"context_directory_loader wires a banned embedding/hybrid call {leaked} — "
        "MEMORY injection must stay pure keyword full-injection (PRI11)."
    )
