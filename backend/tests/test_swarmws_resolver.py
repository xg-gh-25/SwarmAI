"""One SwarmWS resolver (design docs/design/2026-10-26-run-knowledge.md §3.1, step 1).

What is tested:
- ``config.get_swarmws`` order: SWARM_WORKSPACE, then SWARMWS, then
  ``get_app_data_dir()/SwarmWS``; with none set it is ``~/.swarm-ai/SwarmWS`` as before;
  read on every call.
- ``project_registry.get_swarmws`` (recall, DDD, bindings) and the pipeline CLI's
  workspace agree with it, so a run in a temporary workspace recalls that workspace's
  DDD, not the live one (red before step 1: recall read SWARMWS or ~ only).
- Structure: no product module resolves the workspace itself -- no ``"SwarmWS"`` path
  join, no ``~/.swarm-ai/SwarmWS`` string used as a path, no read of SWARMWS /
  SWARM_WORKSPACE -- outside ``config.get_swarmws``. ALLOWED lists the strings that are
  not resolvers.
- The same scan covers ``backend/scripts/`` and every skill's scripts (design
  2026-10-27-pipeline-hygiene §4). A skill script cannot import backend ``config``, so
  its one allowed form is ``_swarmws()``, AST-identical to ``SKILL_RESOLVER`` below,
  which is checked to agree with ``config.get_swarmws`` under every variable setting.
  Test files are not scanned: setting the variables is how a test points code at a
  sandbox.
"""
import ast
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
PRODUCT = ("core", "jobs", "routers", "hooks", "channels", "database", "middleware",
           "utils", "main.py", "scripts")
# every variable name a workspace was ever read from; only config reads them
ENV_NAMES = {"SWARMWS", "SWARM_WORKSPACE", "SWARMWS_DIR", "SWARM_WS", "SWARMAI_WORKSPACE"}

# (file, why the string is not a workspace resolver)
ALLOWED = {
    "core/canvas_noise.py": "a path pattern matched against changed paths",
    "core/ddd_packager.py": "a regex that scrubs the path out of a package",
    "core/backup_manager.py": "a backup's layout under an explicit swarm_dir argument",
    "database/sqlite.py": "legacy cleanup also keeps the default <app data>/SwarmWS",
    "scripts/artifact_cli.py": "asks whether SWARM_WORKSPACE is explicitly set (a test workspace)",
    "scripts/cdf_cutover_snapshot.py":
        "sets SWARM_WORKSPACE so config.get_swarmws() is the workspace being snapshotted",
    "scripts/cdf_rehearsal_replay.py":
        "sets SWARM_WORKSPACE so config.get_swarmws() is the workspace copy being replayed",
    "scripts/eval_runner.py": "sets SWARM_WORKSPACE/SWARMWS for a check's subprocess",
    "scripts/probe_pipeline.py": "sets SWARM_WORKSPACE/SWARMWS for a sandbox run",
    "skills/s_repo-to-ddd/scripts/ai_ready_helpers.py":
        "asks whether SWARM_WORKSPACE is explicitly set (collect output there)",
}

SKILL_RESOLVER = '''
def _swarmws() -> Path:
    """The SwarmWS root, resolved exactly as backend config.get_swarmws (a skill script
    cannot import backend config): SWARM_WORKSPACE, then SWARMWS, then
    $SWARM_DATA_DIR/SwarmWS, then ~/.swarm-ai/SwarmWS."""
    for var in ("SWARM_WORKSPACE", "SWARMWS"):
        value = os.environ.get(var, "").strip()
        if value:
            return Path(os.path.expanduser(value))
    return Path(os.environ.get("SWARM_DATA_DIR") or Path.home() / ".swarm-ai") / "SwarmWS"
'''
_SKILL_RESOLVER_DUMP = ast.dump(ast.parse(SKILL_RESOLVER).body[0])


def test_order_and_default(monkeypatch, tmp_path):
    from config import get_swarmws
    for var in ("SWARM_WORKSPACE", "SWARMWS", "SWARM_DATA_DIR"):
        monkeypatch.delenv(var, raising=False)
    assert get_swarmws() == Path.home() / ".swarm-ai" / "SwarmWS"
    monkeypatch.setenv("SWARM_DATA_DIR", str(tmp_path / "data"))
    assert get_swarmws() == tmp_path / "data" / "SwarmWS"
    monkeypatch.setenv("SWARMWS", str(tmp_path / "b"))
    assert get_swarmws() == tmp_path / "b"
    monkeypatch.setenv("SWARM_WORKSPACE", str(tmp_path / "a"))
    assert get_swarmws() == tmp_path / "a"


def test_recall_and_pipeline_read_the_same_workspace(monkeypatch, tmp_path):
    ws = tmp_path / "ws"
    doc = ws / "Projects" / "P" / "2-understanding" / "TECH.md"
    doc.parent.mkdir(parents=True)
    doc.write_text("# TECH\n\n## Traps\n\n- **Quokka rule**: never feed quokkas after midnight.\n",
                   encoding="utf-8")
    monkeypatch.delenv("SWARMWS", raising=False)
    monkeypatch.setenv("SWARM_WORKSPACE", str(ws))
    from core import project_registry
    from core.recall_multi import recall_ddd
    from core.run_protocol import cli_commands
    assert project_registry.get_swarmws() == ws
    assert cli_commands._workspace() == ws
    hits = recall_ddd("quokka midnight", "P", "design")
    assert hits and "Quokka rule" in hits[0]["content"]


def _is_test(path: Path) -> bool:
    return path.name.startswith("test_") or path.name.endswith("_test.py") \
        or "tests" in path.parts or path.name == "conftest.py"


def _skill_scripts():
    for p in sorted((BACKEND / "skills").rglob("*.py")):
        if not _is_test(p):
            yield p


def _resolver_uses(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in list(ast.walk(tree)):   # the one allowed skill form is not a use
        body = getattr(node, "body", None)
        for i, child in enumerate(body if isinstance(body, list) else []):
            if isinstance(child, ast.FunctionDef) and child.name == "_swarmws" \
                    and ast.dump(child) == _SKILL_RESOLVER_DUMP:
                node.body[i] = ast.Pass()
    docstrings = {id(n.body[0].value) for n in ast.walk(tree)
                  if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                  and n.body and isinstance(n.body[0], ast.Expr)
                  and isinstance(n.body[0].value, ast.Constant)}
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div) \
                and isinstance(node.right, ast.Constant) and node.right.value == "SwarmWS":
            out.append(f"{node.lineno}: / \"SwarmWS\"")
        if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                and id(node) not in docstrings:
            if ".swarm-ai/SwarmWS" in node.value:
                out.append(f"{node.lineno}: {node.value[:40]!r}")
            if node.value in ENV_NAMES:
                out.append(f"{node.lineno}: env {node.value}")
    return out


def _product_files():
    for root in PRODUCT:
        p = BACKEND / root
        yield from ([p] if p.suffix == ".py" else sorted(p.rglob("*.py")))


def test_no_second_workspace_resolver():
    found = {}
    for path in _product_files():
        rel = str(path.relative_to(BACKEND))
        if _is_test(path) or rel == "config.py" or rel in ALLOWED:
            continue
        uses = _resolver_uses(path)
        if uses:
            found[rel] = uses
    assert not found, found


def test_no_skill_script_resolves_the_workspace_itself():
    found = {}
    for path in _skill_scripts():
        rel = str(path.relative_to(BACKEND))
        if rel in ALLOWED:
            continue
        uses = _resolver_uses(path)
        if uses:
            found[rel] = uses
    assert not found, (found, "use _swarmws() exactly as SKILL_RESOLVER")


def test_skill_resolver_agrees_with_config(monkeypatch, tmp_path):
    import os
    from config import get_swarmws
    ns = {"os": os, "Path": Path}
    exec(SKILL_RESOLVER, ns)
    names = ("SWARM_WORKSPACE", "SWARMWS", "SWARM_DATA_DIR")
    for mask in range(8):
        for i, var in enumerate(names):
            if mask & (1 << i):
                monkeypatch.setenv(var, str(tmp_path / var.lower()))
            else:
                monkeypatch.delenv(var, raising=False)
        assert ns["_swarmws"]() == get_swarmws(), mask
    monkeypatch.setenv("SWARM_WORKSPACE", "  ")    # blank is unset in both
    assert ns["_swarmws"]() == get_swarmws()


def test_skill_scripts_using_the_resolver_carry_it_verbatim():
    users = [p for p in _skill_scripts() if "_swarmws(" in p.read_text(encoding="utf-8")]
    assert users, "the scan above never saw the skill form"
    for p in users:
        defs = [n for n in ast.walk(ast.parse(p.read_text(encoding="utf-8")))
                if isinstance(n, ast.FunctionDef) and n.name == "_swarmws"]
        assert len(defs) == 1 and ast.dump(defs[0]) == _SKILL_RESOLVER_DUMP, p


@pytest.mark.parametrize("rel", sorted(ALLOWED))
def test_allowed_entries_still_need_their_exception(rel):
    assert _resolver_uses(BACKEND / rel), f"{rel} no longer needs to be in ALLOWED"
