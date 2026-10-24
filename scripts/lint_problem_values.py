#!/usr/bin/env python3
"""Lint: the problem value of a (value, problem) loader is never dropped.

Some loaders return a pair so a caller can tell "absent" from "present but unusable":
``pipeline_validator._load_evidence_json(path) -> (data, problem)`` gives
``(None, None)`` for an absent file and ``(None, reason)`` for one that exists but
cannot be used (a forged or unreadable runner report). Treating the second as the first
is a fail-open: an unusable report reads as "no report" and a check passes by omission.

The rule was in the loader's docstring, and the same defect
(``_load_evidence_json(...)[0] or {}``) still shipped in two separate changes. So it is
a CI failure now, for each registered loader:

- ``<loader>(...)[0] or <default>`` — the value defaulted: unusable becomes absent;
- ``value, _ = <loader>(...)`` — the problem unpacked into ``_``.
(``evidence_seal.check`` was registered too; it was deleted with the pipeline's test
runner, S6-1b.)

Legal: keeping the problem (``value, why = …``) and reading ``[0]`` with a test of the
value (``is None`` / ``isinstance``), where an unusable file is "no evidence" — the
fail-closed reading. ``pipeline_validator._read_evidence_json`` is the sanctioned
value-only wrapper and is itself checked like any other caller.

Known limits (the shapes the two incidents had; widen when a new one ships): only the
literal loader name is matched (an alias is not), ``_``-unpacking only in assignments (not ``for`` / ``with``), and a two-step
``v = loader(p)[0]`` then ``v or {}`` is not followed.

Scope: every tracked and untracked-unignored ``.py`` under ``SCOPE`` (the trees CI ships
the pipeline from). The scope is a declared constant, pinned by a test, and a run that
reads zero files fails: a clean result over a tree the lint never opened is not clean.

Run from repo root (CI runs it after backend deps install):
    python scripts/lint_problem_values.py

Exit code 0 = clean, 1 = a finding or nothing scanned.
"""
from __future__ import annotations

import ast
import subprocess
import sys
import warnings
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
SCOPE = ("backend", "scripts")

# (callee name, module the attribute must be read from or None for any, also bans [0])
LOADERS: "tuple[tuple[str, str | None, bool], ...]" = (
    ("_load_evidence_json", None, False),
)


def _loader(call: ast.AST) -> "tuple[str, bool] | None":
    """(name, bans_index_0) when *call* calls a registered loader."""
    if not isinstance(call, ast.Call):
        return None
    f = call.func
    for name, module, bans in LOADERS:
        if isinstance(f, ast.Name) and f.id == name and module is None:
            return name, bans
        if isinstance(f, ast.Attribute) and f.attr == name:
            if module is None:
                return name, bans
            if isinstance(f.value, ast.Name) and f.value.id == module:
                return f"{module}.{name}", bans
    return None


def _index0(node: ast.AST) -> "tuple[str, bool] | None":
    if (isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant)
            and node.slice.value == 0):
        return _loader(node.value)
    return None


def lint_source(src: str, rel: str) -> "list[str]":
    """Findings ``path:line: message`` for one file's source."""
    try:
        with warnings.catch_warnings():   # a file's own escape-sequence warnings are not ours
            warnings.simplefilter("ignore", SyntaxWarning)
            tree = ast.parse(src)
    except (SyntaxError, ValueError) as exc:
        return [f"{rel}: does not parse ({type(exc).__name__}) — cannot be checked"]
    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or) and node.values:
            hit = _index0(node.values[0])
            if hit:
                out.append(f"{rel}:{node.lineno}: {hit[0]}(...)[0] or <default> turns an unusable "
                           "value into an absent one — keep the problem: value, why = ...")
        if isinstance(node, ast.Subscript):
            hit = _index0(node)
            if hit and hit[1]:
                out.append(f"{rel}:{node.lineno}: {hit[0]}(...)[0] drops the reason — "
                           "ok, why = ... and pass `why` on")
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            hit = _loader(node.value) if node.value is not None else None
            for t in targets:
                if (hit and isinstance(t, ast.Tuple) and len(t.elts) == 2
                        and isinstance(t.elts[1], ast.Name) and t.elts[1].id == "_"):
                    out.append(f"{rel}:{node.lineno}: {hit[0]}(...) unpacks its problem into `_` — "
                               "name it and act on it")
    return sorted(set(out), key=lambda s: (s.split(":")[0], int(s.split(":")[1]) if s.split(":")[1].isdigit() else 0))


def _files() -> "list[Path]":
    p = subprocess.run(["git", "-C", str(_REPO_ROOT), "ls-files", "--cached", "--others",
                        "--exclude-standard", "-z", "--", *SCOPE],
                       capture_output=True, text=True, timeout=60)
    if p.returncode != 0:
        raise SystemExit(f"problem-value lint: git ls-files failed: {p.stderr.strip()[:200]}")
    return [_REPO_ROOT / f for f in p.stdout.split("\0")
            if f.endswith(".py") and (_REPO_ROOT / f).is_file()]


def main() -> int:
    files = _files()
    if not files:
        print(f"problem-value lint: no .py file found under {', '.join(SCOPE)} — "
              "the scope is wrong, nothing was checked")
        return 1
    findings: list[str] = []
    for f in files:
        rel = str(f.relative_to(_REPO_ROOT))
        try:
            src = f.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            findings.append(f"{rel}: unreadable ({type(exc).__name__})")
            continue
        findings += lint_source(src, rel)
    if findings:
        print(f"\n{len(findings)} problem-value finding(s):")
        for x in findings:
            print(f"  ✗ {x}")
        return 1
    print(f"{len(files)} file(s) under {', '.join(SCOPE)} keep their loaders' problem values ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
