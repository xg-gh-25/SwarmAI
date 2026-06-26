"""Wiring-truth guard for MEMORY selective-injection (run_b2d62f47).

This is NOT a behavior test — it is an executable assertion of a PRODUCTION
WIRING FACT that documentation kept getting wrong: the MEMORY hybrid scorer
(``_hybrid_section_scores``, the 0.6·vector+0.4·keyword path) is BUILT but
UNWIRED on the live injection path. ``select_memory_sections`` defaults
``memory_embeddings=False`` and the sole production caller
(``context_directory_loader.py``) omits the flag — so MEMORY injection is
KEYWORD-ONLY in production today.

Why lock this as a test (Gate-1 skeptic, run_b2d62f47): it is a deliberate
forcing function. The day someone correctly wires the hybrid leg in prod, this
test goes RED — forcing a conscious update of the test AND the TECH.md/MEMORY.md
docs that describe the wiring, instead of letting the "formula in code ≠ behavior
in prod" gap drift silently again (the exact confusion this run started from).

Both assertions must hold; flipping EITHER (change the default, or wire the flag
at the caller) makes this RED.
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

from core.memory_index import select_memory_sections


def test_memory_injection_keyword_only_in_prod():
    # Assertion 1 — the default is keyword-only (memory_embeddings=False).
    sig = inspect.signature(select_memory_sections)
    assert sig.parameters["memory_embeddings"].default is False, (
        "select_memory_sections default changed — MEMORY hybrid may now be "
        "wired. Update this guard AND the TECH.md/MEMORY.md wiring docs."
    )

    # Assertion 2 — the SOLE production caller omits the flag (so the default
    # keyword-only path is what actually runs). Parse the caller's source and
    # confirm no select_memory_sections(...) call passes memory_embeddings.
    loader_src = (
        Path(__file__).resolve().parents[1] / "core" / "context_directory_loader.py"
    ).read_text()
    tree = ast.parse(loader_src)

    calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and (
            (isinstance(node.func, ast.Name) and node.func.id == "select_memory_sections")
            or (isinstance(node.func, ast.Attribute) and node.func.attr == "select_memory_sections")
        )
    ]
    assert calls, "select_memory_sections call not found in context_directory_loader.py"
    for call in calls:
        kwargs = {kw.arg for kw in call.keywords if kw.arg}
        assert "memory_embeddings" not in kwargs, (
            "context_directory_loader now passes memory_embeddings — MEMORY hybrid "
            "is being wired in prod. This guard fired as designed: update it AND "
            "the wiring docs (TECH.md § Recall Architecture, MEMORY.md MOD entry)."
        )
