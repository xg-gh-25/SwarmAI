"""Skill scripts resolve the app data dir one way, the same as backend config.

What is tested:
- A skill script cannot import backend ``config``, so its one allowed form is
  ``_app_data_dir()``, AST-identical to ``SKILL_RESOLVER`` below; that form is checked
  to agree with ``config.get_app_data_dir`` with SWARM_DATA_DIR set and unset.
- Structure: outside that function (and the verbatim ``_swarmws()`` of
  test_swarmws_resolver), no skill script builds ``Path.home() / ".swarm-ai"``,
  expands a ``"~/.swarm-ai..."`` path, or reads SWARM_DATA_DIR / the retired
  SWARM_APP_DATA_DIR (nothing set it). Before this, notify, library, todo_db and five
  Pollinate scripts each had their own resolver, and the Pollinate ones ignored
  SWARM_DATA_DIR, so a sandboxed run wrote the live ~/.swarm-ai files.
- Behaviour: todo_db's data.db and notify's config follow SWARM_DATA_DIR on import.
Help and message strings that mention ``~/.swarm-ai`` are text, not resolvers.
"""
import ast
import importlib.util
import os
from pathlib import Path

from tests.test_swarmws_resolver import _SKILL_RESOLVER_DUMP as _SWARMWS_DUMP

BACKEND = Path(__file__).resolve().parents[1]

SKILL_RESOLVER = '''
def _app_data_dir() -> Path:
    """The app data dir, resolved exactly as backend config.get_app_data_dir (a skill
    script cannot import backend config): $SWARM_DATA_DIR, then ~/.swarm-ai."""
    return Path(os.environ.get("SWARM_DATA_DIR") or Path.home() / ".swarm-ai")
'''
_DUMP = ast.dump(ast.parse(SKILL_RESOLVER).body[0])
_ENV = {"SWARM_DATA_DIR", "SWARM_APP_DATA_DIR"}


def _skill_scripts():
    for p in sorted((BACKEND / "skills").rglob("*.py")):
        if not (p.name.startswith("test_") or p.name.endswith("_test.py") or "tests" in p.parts):
            yield p


def _uses(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in list(ast.walk(tree)):          # the allowed forms are not uses
        body = getattr(node, "body", None)
        for i, child in enumerate(body if isinstance(body, list) else []):
            if isinstance(child, ast.FunctionDef) and ast.dump(child) in (_DUMP, _SWARMWS_DUMP):
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
        if isinstance(n, ast.Constant) and n.value in _ENV:
            out.append(f"{n.lineno}: env {n.value}")
    return out


def test_no_skill_script_resolves_the_data_dir_itself():
    found = {str(p.relative_to(BACKEND)): u for p in _skill_scripts() if (u := _uses(p))}
    assert not found, (found, "use _app_data_dir() exactly as SKILL_RESOLVER")


def test_skill_scripts_using_the_resolver_carry_it_verbatim():
    users = [p for p in _skill_scripts() if "_app_data_dir(" in p.read_text(encoding="utf-8")]
    assert len(users) >= 9, users        # notify, library, todo_db, six Pollinate
    for p in users:
        defs = [n for n in ast.walk(ast.parse(p.read_text(encoding="utf-8")))
                if isinstance(n, ast.FunctionDef) and n.name == "_app_data_dir"]
        assert len(defs) == 1 and ast.dump(defs[0]) == _DUMP, p


def test_skill_resolver_agrees_with_config(monkeypatch, tmp_path):
    from config import get_app_data_dir
    ns = {"os": os, "Path": Path}
    exec(SKILL_RESOLVER, ns)
    monkeypatch.delenv("SWARM_DATA_DIR", raising=False)
    assert ns["_app_data_dir"]() == get_app_data_dir() == Path.home() / ".swarm-ai"
    monkeypatch.setenv("SWARM_DATA_DIR", str(tmp_path / "d"))
    assert ns["_app_data_dir"]() == get_app_data_dir() == tmp_path / "d"
    monkeypatch.setenv("SWARM_DATA_DIR", "")             # empty is unset in both
    assert ns["_app_data_dir"]() == get_app_data_dir()


def _load(rel: str):
    spec = importlib.util.spec_from_file_location(f"_dd_{Path(rel).stem}", BACKEND / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_writers_follow_swarm_data_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("SWARM_DATA_DIR", str(tmp_path))
    assert _load("skills/s_radar-todo/scripts/todo_db.py").DB_PATH == tmp_path / "data.db"
    assert _load("skills/s_notify/notify.py").DEFAULT_CONFIG_PATH == \
        str(tmp_path / "notify-channels.yaml")
    assert _load("skills/s_pollinate/scripts/publish_meta.py").ACCOUNTS_PATH == \
        str(tmp_path / "pollinate-accounts.yaml")
