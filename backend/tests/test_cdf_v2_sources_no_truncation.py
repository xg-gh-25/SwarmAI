"""AST guard for CDF v2 source semantic completeness.

The scan rejects integer slices and shortening helpers applied directly to any
contracts_v2 semantic field in source modules or the S1 correction hook.
"""
from __future__ import annotations

import ast
from pathlib import Path

from core.ingestion.contracts_v2 import SEMANTIC_FIELDS

ROOT = Path(__file__).parents[1]
FILES = sorted((ROOT / "core/ingestion/sources").glob("*.py")) + [
    ROOT / "core/runtime_hooks.py",
    ROOT / "core/ingestion/writer.py",
    ROOT / "core/ingestion/units.py",
]
FIELDS = {field for values in SEMANTIC_FIELDS.values() for field in values}


def _name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant):
        return str(node.slice.value)
    return None


def test_semantic_fields_are_never_sliced_or_shortened():
    violations = []
    for path in FILES:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Slice):
                if isinstance(node.slice.upper, ast.Constant) and _name(node.value) in FIELDS:
                    violations.append((path.name, node.lineno, "slice"))
            if isinstance(node, ast.Call):
                called = _name(node.func)
                if called in {"shorten", "truncate", "textwrap.shorten"}:
                    if node.args and _name(node.args[0]) in FIELDS:
                        violations.append((path.name, node.lineno, called))
    assert violations == []


def test_correction_path_keeps_the_whole_user_prompt():
    source = (ROOT / "core/runtime_hooks.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    detector = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                    and node.name == "create_user_correction_detector")
    slices = [node for node in ast.walk(detector)
              if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Slice)
              and _name(node.value) == "prompt"]
    assert slices == []


# Write-side producers whose text becomes a source's semantic content (a DailyActivity
# line, a distillation candidate, a backflow title): no constant slice at all inside
# them, whatever the variable is called -- the field-name scan above cannot see
# ``entry[:200]`` or ``prompt[:500]``. A slice of an id or a hash is a locator, allowed.
PRODUCERS = {
    "hooks/distillation_hook.py": {"_extract_decisions", "_extract_lessons",
                                   "_extract_corrections", "_extract_competence"},
    "core/summarization.py": {"_extract_topics", "_extract_first_sentence",
                              "_extract_decisions", "_extract_open_questions"},
    "hooks/knowledge_backflow_hook.py": {"_extract_title"},
    "core/runtime_hooks.py": {"create_high_signal_capture", "create_session_checkpoint"},
}
_LOCATOR = ("id", "hash", "digest", "sha")


def _is_locator(node: ast.AST) -> bool:
    if isinstance(node, ast.Call):
        return _is_locator(node.func)
    name = (_name(node) or "").lower()
    return any(part in name for part in _LOCATOR)


def _list_names(fn: ast.AST) -> set[str]:
    """Names bound to a list in *fn* (a list display or comprehension, ``sorted(...)`` or
    ``list(...)``): slicing one keeps whole elements, a count cap, not a cut."""
    out = set()
    for node in ast.walk(fn):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            value = node.value
            is_list = isinstance(value, (ast.List, ast.ListComp)) or (
                isinstance(value, ast.Call) and _name(value.func) in {"sorted", "list"})
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if is_list:
                out |= {t.id for t in targets if isinstance(t, ast.Name)}
    return out


def test_write_side_producers_never_cut_text():
    violations = []
    for rel, names in PRODUCERS.items():
        tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
        found = {n.name: n for n in ast.walk(tree)
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in names}
        assert set(found) == names, (rel, names - set(found))
        for fn in found.values():
            lists = _list_names(fn)
            for node in ast.walk(fn):
                if (isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Slice)
                        and isinstance(node.slice.upper, ast.Constant)
                        and isinstance(node.slice.upper.value, int)
                        and not _is_locator(node.value)
                        and _name(node.value) not in lists):
                    violations.append((rel, fn.name, node.lineno))
    assert violations == []
