"""Enforcement gate: no unguarded Projects/<name>/ mkdir in the pipeline CLI.

Gate-1 flagged "_ensure_project_dir is the choke, but 4 call sites must REMEMBER to
call it" — an N-places-must-remember invariant. A future mint path that does
`mkdir(parents=True)` on a Projects/-rooted path WITHOUT routing through
_ensure_project_dir silently reintroduces the pseudo-DDD bug (invisible to Brain Hub),
and no stage gate would catch it. This test turns "remember" into "fail the build":
every mkdir(parents=True) in artifact_cli.py must be inside a function that also calls
_ensure_project_dir (or be _ensure_project_dir itself). New mint path forgets the
choke → this test goes RED.

Static, AST-based (not a runtime path) so it holds in a bare checkout.
"""

from __future__ import annotations

import ast
from pathlib import Path

_CLI = Path(__file__).resolve().parent.parent / "scripts" / "artifact_cli.py"

# Functions allowed to mkdir(parents=True) WITHOUT a sibling _ensure_project_dir call:
#  - _ensure_project_dir itself IS the registration choke.
# Any OTHER function doing a parents=True mkdir must call _ensure_project_dir. If you
# add a legitimately-non-Projects mkdir, add its function name here WITH a reason.
_ALLOWLIST = {
    "_ensure_project_dir",
    # Each of these mkdirs only INSIDE an existing run directory
    # (Projects/<P>/.artifacts/runs/<R>/, created by run-create through
    # _ensure_project_dir), in the app state dir, or in the target repo:
    # none can mint a new Projects/<name>/.
    "_generate_review_deliverable",  # <target repo>/.aidlc/deliverables/<run_id>
    "_ledger_add",                   # <app data>/state/pipeline_agent_audit/
    "_write_gate0_prompt",           # run_dir/evidence/prompts
    "_write_gate1_prompt",           # run_dir/evidence/prompts
    "_write_gate_prompt",            # run_dir/evidence/prompts
    "_write_review_inputs",          # run_dir/evidence/prompts
    "_write_review_prompts",         # run_dir/evidence/prompts
    "_write_expert_prompts",         # run_dir/evidence/prompts
    "_snapshot_gate2_round1",        # run_dir/evidence/prompts/round1
    "cmd_gate2_ledger",              # run_dir/evidence
    "cmd_gate2_fixspec",             # run_dir/evidence/fixing-spec-r<N>.json
}


_MODULE_LEVEL = "<module>"


def _is_dir_creating_call(node: ast.Call) -> bool:
    """True if the call mints a directory tree: `X.mkdir(parents=True...)` OR
    `os.makedirs(...)` (makedirs is recursive by nature — always a mint risk)."""
    func = node.func
    # os.makedirs(...) — recursive dir create (covers the os.makedirs blind spot)
    if isinstance(func, ast.Attribute) and func.attr == "makedirs":
        return True
    # X.mkdir(parents=True) — keyword True (the common form)
    if isinstance(func, ast.Attribute) and func.attr == "mkdir":
        for kw in node.keywords:
            if kw.arg == "parents" and isinstance(kw.value, ast.Constant) and kw.value.value is True:
                return True
    return False


def _enclosing_funcs_with_parents_mkdir(tree: ast.AST) -> set[str]:
    """Names of functions containing a dir-minting call. A module-level mint is
    reported as ``<module>`` (NOT silently skipped — that was a false-negative)."""
    hits: set[str] = set()

    class V(ast.NodeVisitor):
        def __init__(self):
            self.stack: list[str] = []

        def visit_FunctionDef(self, node):
            self.stack.append(node.name)
            self.generic_visit(node)
            self.stack.pop()

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_Call(self, node):
            if _is_dir_creating_call(node):
                # innermost enclosing def, or <module> if at module level. NOTE this is a
                # co-location HEURISTIC (a function that mints AND calls _ensure_project_dir
                # is treated as guarded); it does not prove by dataflow that the minted path
                # is the registered one. The current 5 mint sites are all genuinely guarded;
                # a future divergence should be caught in review, and module-level/makedirs
                # mints (the real blind spots) are now flagged rather than skipped.
                hits.add(self.stack[-1] if self.stack else _MODULE_LEVEL)
            self.generic_visit(node)

    V().visit(tree)
    return hits


def _funcs_calling_ensure(tree: ast.AST) -> set[str]:
    """Names of functions that call _ensure_project_dir(...)."""
    hits: set[str] = set()

    class V(ast.NodeVisitor):
        def __init__(self):
            self.stack: list[str] = []

        def visit_FunctionDef(self, node):
            self.stack.append(node.name)
            self.generic_visit(node)
            self.stack.pop()

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_Call(self, node):
            func = node.func
            if isinstance(func, ast.Name) and func.id == "_ensure_project_dir":
                if self.stack:
                    hits.add(self.stack[-1])
            self.generic_visit(node)

    V().visit(tree)
    return hits


def test_every_parents_mkdir_is_registration_guarded():
    tree = ast.parse(_CLI.read_text(encoding="utf-8"))
    minters = _enclosing_funcs_with_parents_mkdir(tree)
    guarded = _funcs_calling_ensure(tree)

    unguarded = {f for f in minters if f not in _ALLOWLIST and f not in guarded}
    assert not unguarded, (
        f"These functions in artifact_cli.py mkdir(parents=True) but do NOT call "
        f"_ensure_project_dir — a new unguarded Projects/<name>/ mint path would be a "
        f"pseudo-DDD (invisible to Brain Hub). Route each through _ensure_project_dir, "
        f"or (if genuinely not a Projects/ mkdir) add it to _ALLOWLIST with a reason. "
        f"Offenders: {sorted(unguarded)}"
    )


def test_the_choke_exists():
    """Guard against the choke being renamed/removed out from under the enforcement."""
    tree = ast.parse(_CLI.read_text(encoding="utf-8"))
    fn_names = {n.name for n in ast.walk(tree)
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    assert "_ensure_project_dir" in fn_names, "the registration choke _ensure_project_dir must exist"
