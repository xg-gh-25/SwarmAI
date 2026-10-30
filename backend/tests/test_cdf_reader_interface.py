"""Readers of the main files use CDF's published interface only (design
docs/design/2026-10-30-cdf-fixing-spec.md §2 C2, F4): no ingestion table name, no
``core.ingestion.store`` connection, no private ``core.ingestion`` function. When the
tables change meaning, ``core.ingestion.standing`` changes with them; the readers do not."""
from __future__ import annotations

import ast
import re
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
READERS = ("core/memory_injection.py", "core/hive_lessons.py", "core/usage_import.py",
           "core/knowledge_cli.py")


def _tables() -> set[str]:
    text = "".join((BACKEND / f).read_text(encoding="utf-8")
                   for f in ("core/ingestion/store.py", "core/ingestion/api.py", "core/ingestion/api_v2.py"))
    return set(re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", text)) | set(re.findall(r"CREATE TABLE (\w+)", text))


def _code(path: Path) -> str:
    """The module without docstrings and comments: a table named in prose is fine."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)) and node.body \
                and isinstance(node.body[0], ast.Expr) and isinstance(getattr(node.body[0], "value", None), ast.Constant):
            node.body[0].value.value = ""
    return ast.unparse(tree)


def test_readers_name_no_ingestion_table_and_open_no_store():
    tables = _tables()
    assert {"ingestion_units", "evidence_roots", "unit_usage", "injection_selection"} <= tables
    hits = []
    for rel in READERS:
        code = _code(BACKEND / rel)
        hits += [f"{rel}: table {t}" for t in sorted(tables) if re.search(rf"\b{t}\b", code)]
        if "core.ingestion.store" in code:
            hits.append(f"{rel}: opens the CDF store directly")
    assert hits == []


def test_readers_import_no_private_ingestion_function():
    hits = []
    for rel in READERS:
        for node in ast.walk(ast.parse((BACKEND / rel).read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("core.ingestion"):
                hits += [f"{rel}: {node.module}.{a.name}" for a in node.names if a.name.startswith("_")]
    assert hits == []
