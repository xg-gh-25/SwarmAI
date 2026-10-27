"""Product code finds the app data dir in one place: config.get_app_data_dir().

What is tested:
- Structure: in backend product code and ``backend/scripts`` no file builds
  ``Path.home() / ".swarm-ai"``, expands ``"~/.swarm-ai..."``, or uses a
  ``"${HOME}/.swarm-ai"`` string, outside ``config.py``. Before this, about 15 sites
  did, several behind a "config import failed" fallback that never happens (config
  imports only the standard library and model_registry), so they ignored
  SWARM_DATA_DIR and a test or second install read or wrote the live files.
  A standalone script that cannot import config carries the verbatim
  ``_app_data_dir()`` of test_skill_data_dir_resolver. ALLOWED lists the exceptions
  with the reason; each must still need its exception.
- Behaviour: the moved readers follow SWARM_DATA_DIR.
Skill scripts are covered by test_skill_data_dir_resolver; tests are not scanned.
Replaces scripts/lint_hardcoded_paths.py, which nothing ran.
"""
import ast
from pathlib import Path

import pytest

from tests.test_skill_data_dir_resolver import _DUMP as _STANDALONE_DUMP

BACKEND = Path(__file__).resolve().parents[1]
ROOTS = ("core", "jobs", "routers", "hooks", "channels", "database", "middleware",
         "utils", "main.py", "scripts", "model_registry.py", "config_settings.py")

ALLOWED = {
    "core/daemon_guard.py": "shared with swarmai_guardian.sh (${HOME}/.swarm-ai) and copied "
                            "standalone to the guardian dir; the launchd guardian has no "
                            "SWARM_DATA_DIR",
    "scripts/verify_build.py": "sets SWARM_DATA_DIR for the probe's sandbox HOME, the layout "
                               "a fresh install would have",
}


def _files():
    for root in ROOTS:
        p = BACKEND / root
        for f in ([p] if p.suffix == ".py" else sorted(p.rglob("*.py"))):
            if not (f.name.startswith("test_") or f.name.endswith("_test.py")
                    or "tests" in f.parts or f.name == "conftest.py"):
                yield f


def _uses(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in list(ast.walk(tree)):          # the standalone form is not a use
        body = getattr(node, "body", None)
        for i, child in enumerate(body if isinstance(body, list) else []):
            if isinstance(child, ast.FunctionDef) and ast.dump(child) == _STANDALONE_DUMP:
                node.body[i] = ast.Pass()
    out = []
    for n in ast.walk(tree):
        if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Div) \
                and isinstance(n.right, ast.Constant) and n.right.value == ".swarm-ai":
            out.append(f"{n.lineno}: / '.swarm-ai'")
        if isinstance(n, ast.Call) and getattr(n.func, "attr", None) == "expanduser" and n.args:
            a = n.args[0]
            parts = [a.value] if isinstance(a, ast.Constant) else [
                v.value for v in getattr(a, "values", []) if isinstance(v, ast.Constant)]
            if any(isinstance(s, str) and s.startswith("~/.swarm-ai") for s in parts):
                out.append(f"{n.lineno}: expanduser('~/.swarm-ai...')")
        if isinstance(n, ast.Constant) and isinstance(n.value, str) \
                and n.value.startswith(("${HOME}/.swarm-ai", "$HOME/.swarm-ai")):
            out.append(f"{n.lineno}: {n.value[:30]!r}")
    return out


def test_no_product_code_resolves_the_app_data_dir_itself():
    found = {}
    for f in _files():
        rel = str(f.relative_to(BACKEND))
        if rel == "config.py" or rel in ALLOWED:
            continue
        if u := _uses(f):
            found[rel] = u
    assert not found, (found, "use config.get_app_data_dir()")


@pytest.mark.parametrize("rel", sorted(ALLOWED))
def test_allowed_entries_still_need_their_exception(rel):
    assert _uses(BACKEND / rel), f"{rel} no longer needs to be in ALLOWED"


def test_moved_readers_follow_swarm_data_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("SWARM_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("SWARM_GATE_LOG", raising=False)
    from core import gate_decision_log, kill_audit, sel, tool_risk
    from core.backup_manager import _file_set_token, _file_get_token
    from jobs.handlers import log_patrol
    from utils import runtime_paths
    assert gate_decision_log.log_path() == tmp_path / "logs" / "gate_decisions.jsonl"
    assert kill_audit._path() == tmp_path / "state" / "kills.jsonl"
    # permission_manager._default_persist_path is replaced per test by conftest
    # (_isolate_permission_queues); the structural scan covers its body.
    assert tool_risk._overrides_path() == tmp_path / "tool_risk_overrides.json"
    assert sel._app_data_dir() == tmp_path
    assert runtime_paths._app_data() == tmp_path
    assert log_patrol._log_dir() == tmp_path / "logs"
    assert _file_set_token("t0k") and (tmp_path / ".backup-token").read_text().strip() == "t0k"
    assert _file_get_token() == "t0k"


def test_system_jobs_run_in_the_app_data_dir():
    from jobs.system_jobs import SYSTEM_JOBS
    assert all("cwd" not in j.config for j in SYSTEM_JOBS if j.type == "script")
