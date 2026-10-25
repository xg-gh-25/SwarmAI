"""Chat-path pipeline hooks never scan run files on the event loop (design
left-nav-backend-and-visual-system.md B1 / T1).

What is tested:
- the PostToolUse REFLECT trigger returns while the run lookup is still blocked
  (the lookup runs on a worker thread, never on the loop);
- an approval's required-approval count is handed to a pool: the hook-side helper
  returns at once and the count runs off the loop thread;
- no ``async def`` in core/security_hooks.py calls ``_count_required_approval``
  directly (structural: a new call site cannot put it back on the loop);
- ``pipeline_human.owned_runs`` reads through ``core.run_index`` (a second call
  parses nothing) and still finds exactly the owned non-terminal v1 runs.
"""
from __future__ import annotations

import ast
import asyncio
import json
import threading
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]


@pytest.mark.asyncio
async def test_reflect_trigger_returns_before_the_run_lookup(monkeypatch):
    from core import pipeline_human
    from core.run_protocol import reflect_job

    release = threading.Event()
    seen: dict = {}

    def owned_runs(session_id, status=None):
        seen["thread"] = threading.get_ident()
        release.wait(5)
        return []

    monkeypatch.setattr(pipeline_human, "owned_runs", owned_runs)
    hook = reflect_job.create_reflect_trigger({"sdk_session_id": "s-1"})
    loop_thread = threading.get_ident()

    await asyncio.wait_for(
        hook({"tool_input": {"command": "python-backend pipeline-v1 land"}}, "t", None), 1.0)
    release.set()
    for _ in range(100):
        if "thread" in seen:
            break
        await asyncio.sleep(0.02)
    assert seen.get("thread") not in (None, loop_thread)


def test_required_approval_count_is_handed_off(monkeypatch):
    from core import security_hooks

    release = threading.Event()
    done: dict = {}

    def count(sid):
        release.wait(5)
        done["thread"] = threading.get_ident()

    monkeypatch.setattr(security_hooks, "_count_required_approval", count)
    future = security_hooks._count_required_approval_soon("s-1")
    assert not future.done()          # returned while the count is still blocked
    release.set()
    future.result(5)
    assert done["thread"] != threading.get_ident()


def test_no_async_hook_counts_approvals_inline():
    tree = ast.parse((BACKEND / "core" / "security_hooks.py").read_text())
    bad = []
    for fn in ast.walk(tree):
        if isinstance(fn, ast.AsyncFunctionDef):
            for node in ast.walk(fn):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                        and node.func.id == "_count_required_approval":
                    bad.append(f"{fn.name}:{node.lineno}")
    assert bad == []


def _run(ws: Path, project: str, run_id: str, **data) -> Path:
    d = ws / "Projects" / project / ".artifacts" / "runs" / run_id
    d.mkdir(parents=True)
    (d / "run.json").write_text(json.dumps(data))
    return d / "run.json"


def test_owned_runs_reads_through_the_run_index(tmp_path, monkeypatch):
    from core import pipeline_human, run_index

    run_index._clear_for_tests()
    monkeypatch.setattr(pipeline_human, "_workspace", lambda: tmp_path)
    _run(tmp_path, "P", "run_a", session_id="s-1", status="active", decision_cap=3)
    _run(tmp_path, "P", "run_b", session_id="s-1", status="completed", decision_cap=3)
    _run(tmp_path, "P", "run_c", session_id="s-2", status="active", decision_cap=3)
    _run(tmp_path, "P", "run_d", session_id="s-1", status="running")   # legacy, not v1
    _run(tmp_path, "Q", "run_e", session_id="s-1", status="reflecting", decision_cap=3)

    assert pipeline_human.owned_runs("s-1") == ["run_a", "run_e"]
    assert pipeline_human.owned_runs("s-1", status="reflecting") == ["run_e"]

    loads = []
    real = json.load
    monkeypatch.setattr(run_index.json, "load", lambda f: loads.append(1) or real(f))
    monkeypatch.setattr(json, "loads", lambda *a, **k: pytest.fail("parsed outside the index"))
    assert pipeline_human.owned_runs("s-1") == ["run_a", "run_e"]
    assert loads == []                 # unchanged files are not parsed again
    run_index._clear_for_tests()
