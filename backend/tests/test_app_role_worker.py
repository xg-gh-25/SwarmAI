"""A worker Hive writes no cognition of its own (design
docs/design/2026-10-29-hive-run-record-and-worker-role.md §3; core/app_role).

What each switch point does on a worker, and that a person's instance and a reference
Hive are unchanged: the role itself, the post-session hooks, the CDF threads (extraction
and its expiry only, no judge or writer), the scheduler's jobs, the per-turn
DailyActivity capture, DailyActivity in the prompt, and the startup replay of sessions.
"""
from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest


@pytest.fixture
def as_role(monkeypatch):
    """Set the instance's mode and role for the test."""
    def _set(mode: str | None, role: str | None = None):
        for k, v in (("SWARMAI_MODE", mode), ("SWARMAI_ROLE", role)):
            if v is None:
                monkeypatch.delenv(k, raising=False)
            else:
                monkeypatch.setenv(k, v)
    return _set


def test_the_role(as_role):
    from core import app_role
    for mode, role, want in ((None, None, "personal"), ("daemon", "worker", "personal"),
                             ("hive", None, "worker"), ("hive", "worker", "worker"),
                             ("hive", " Reference ", "reference"), ("hive", "bogus", "worker")):
        as_role(mode, role)
        assert app_role.role() == want, (mode, role)
    as_role("hive", "reference")
    assert not app_role.is_worker()
    as_role("hive", None)
    assert app_role.is_worker()


def test_a_worker_registers_only_the_extraction_hook(as_role, tmp_path, monkeypatch):
    """...so a worker Hive writes no DailyActivity and no ToDo: the ToDo producer is the
    DailyActivity hook (design 2026-10-31-continue-lookback-todo D2)."""
    monkeypatch.setenv("SWARM_DATA_DIR", str(tmp_path))
    from core.session_hooks import SessionLifecycleHookManager, register_default_hooks
    full = ["daily_activity_extraction", "cdf_v2_session_extraction", "workspace_auto_commit",
            "context_health", "evolution_maintenance", "todo_lifecycle", "skill-metrics"]
    for mode, role, want in ((None, None, full), ("hive", "reference", full),
                             ("hive", None, ["cdf_v2_session_extraction"])):
        as_role(mode, role)
        m = SessionLifecycleHookManager(timeout_seconds=1)
        names = register_default_hooks(m, git_lock=None, summarization_pipeline=None,
                                       compliance_tracker=None)
        assert names == want, (mode, role)
        assert [h.name for h in m._hooks] == want


def _old_inbox_row(days: int, n: int = 0) -> None:
    from core.ingestion.store import connect_rw
    conn = connect_rw()
    try:
        at = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        conn.execute(
            "INSERT INTO ingestion_inbox(id,idem_key,source,source_ref,lane,payload,payload_schema,"
            "content_hash,envelope,project_snapshot,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (f"c{days}" + (f"-{n}" if n else ""), f"k{days}-{n}", "S7", "s", "session", "{}", "x", "h",
             "{}", "{}", at))
        conn.commit()
    finally:
        conn.close()


def _inbox_ids() -> set[str]:
    from core.ingestion.store import connect_rw
    conn = connect_rw()
    try:
        return {r[0] for r in conn.execute("SELECT id FROM ingestion_inbox")}
    finally:
        conn.close()


def _wait(pred, s=10.0):
    end = time.monotonic() + s
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.05)
    return pred()


def test_a_worker_starts_the_extraction_and_expiry_but_no_judge_or_writer(as_role, tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SWARM_WORKSPACE", str(tmp_path / "ws"))
    (tmp_path / "ws").mkdir()
    as_role("hive", None)
    from core.ingestion.owner import acquire
    from core.ingestion.worker import WorkerHandle
    _old_inbox_row(40)
    _old_inbox_row(1)
    owner = acquire()
    assert owner is not None
    handle = WorkerHandle(owner, tmp_path / "ws")
    handle.start()
    try:
        assert handle.extract_only
        assert handle.s2_executor._thread.is_alive() and handle._maint.is_alive()
        assert not handle._thread.is_alive() and handle._thread.ident is None          # no judge
        assert handle.run_executor._thread.ident is None                                 # no run lane
        assert _wait(lambda: "c40" not in _inbox_ids())                                  # expired
        assert "c1" in _inbox_ids()                                                      # kept, unjudged
    finally:
        handle.stop_and_join(10)
    again = acquire()                       # the owner lock was released
    assert again is not None
    again.release()


def test_a_reference_hive_starts_the_judge_and_writer(as_role, tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SWARM_WORKSPACE", str(tmp_path / "ws"))
    (tmp_path / "ws").mkdir()
    as_role("hive", "reference")
    from core.ingestion.owner import acquire
    from core.ingestion.worker import WorkerHandle
    owner = acquire()
    handle = WorkerHandle(owner, tmp_path / "ws")
    handle.start()
    try:
        assert not handle.extract_only
        import time
        deadline = time.monotonic() + 10      # the run lane starts after startup recovery
        while not handle.run_executor._thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert handle._thread.is_alive() and handle.run_executor._thread.is_alive()
        assert handle._maint.ident is None
    finally:
        handle.stop_and_join(10)


def test_a_worker_runs_only_the_operational_jobs(as_role, tmp_path, monkeypatch):
    from jobs import scheduler
    user_jobs = tmp_path / "user-jobs.yaml"
    user_jobs.write_text("jobs:\n  - id: mine\n    name: Mine\n    type: script\n    schedule: '0 1 * * *'\n")
    monkeypatch.setattr(scheduler, "USER_JOBS_FILE", user_jobs)
    as_role("hive", None)
    assert {j.id for j in scheduler.load_jobs()} == set(scheduler.WORKER_SYSTEM_JOBS)
    as_role(None, None)
    ids = {j.id for j in scheduler.load_jobs()}
    assert "mine" in ids and "ddd-refresh" in ids and "session-quality" in ids
    from jobs.system_jobs import SYSTEM_JOB_IDS
    assert scheduler.WORKER_SYSTEM_JOBS <= SYSTEM_JOB_IDS            # no stale name


def test_a_worker_keeps_no_daily_activity_or_crash_checkpoint_per_turn(as_role):
    from core.hook_builder import HookRegistry
    from core.runtime_hooks import register_runtime_hooks
    seen: dict[str, list[str]] = {}
    for mode, role in ((None, None), ("hive", None)):
        as_role(mode, role)
        registry = HookRegistry()
        names: list[str] = []
        real = registry.register

        def spy(event, fn, name, *a, _names=names, **k):
            _names.append(name)
            return real(event, fn, name, *a, **k)
        registry.register = spy
        register_runtime_hooks(registry, {})
        seen[mode or "personal"] = names
    assert {"session_checkpoint"} <= set(seen["personal"])
    assert set(seen["personal"]) - set(seen["hive"]) == {"session_checkpoint"}


def test_a_worker_injects_no_daily_activity(as_role, tmp_path):
    from tests.test_prompt_builder_properties import _make_builder
    ws = tmp_path / "ws"
    (ws / ".context").mkdir(parents=True)
    da = ws / "Knowledge" / "DailyActivity"
    da.mkdir(parents=True)
    today = datetime.now().strftime("%Y-%m-%d")
    (da / f"{today}.md").write_text(f"# {today}\n\n## 09:00 | s1 | EARLIER DISPATCH MARKER\n\n- did a thing\n")
    (da / f"{today}.jsonl").write_text(json.dumps({
        "session_id": "s1", "timestamp": "09:00", "title": "EARLIER DISPATCH MARKER",
        "summary_sentence": "EARLIER DISPATCH MARKER did a thing.", "next_items": [], "resolves": []}) + "\n")

    def full_text() -> str:
        cfg: dict = {}
        asyncio.run(_make_builder().build_system_prompt(agent_config=cfg, working_directory=str(ws)))
        return cfg.get("_system_prompt_metadata", {}).get("full_text", "")
    as_role(None, None)
    assert "EARLIER DISPATCH MARKER" in full_text()
    as_role("hive", None)
    assert "EARLIER DISPATCH MARKER" not in full_text()


def test_the_worker_startup_replay_skips_sessions_with_a_job(as_role, tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_DATA_DIR", str(tmp_path / "data"))
    from core import lifecycle_manager as lm
    from core.ingestion import s2_jobs
    now = datetime.now().isoformat()
    sessions = [SimpleNamespace(id="sess_done_1", created_at=now, title="t", agent_id="default"),
                SimpleNamespace(id="sess_new_22", created_at=now, title="t", agent_id="default")]
    s2_jobs.request("sess_done_1", "session_end")
    import core.session_manager as sm
    import core.initialization_manager as im
    import database
    monkeypatch.setattr(sm.session_manager, "list_sessions", lambda limit=50: _async(sessions))
    monkeypatch.setattr(im.initialization_manager, "get_cached_workspace_path", lambda: str(tmp_path))
    monkeypatch.setattr(database, "db", SimpleNamespace(messages=SimpleNamespace(
        count_by_session=lambda sid: _async(5))))
    monkeypatch.setattr("core.session_inputs.session_input_ids", lambda sid: [])
    fired: list[str] = []
    mgr = SimpleNamespace(_hook_executor=object(), STARTUP_BACKLOG_CAP=5,
                          enqueue_hooks=lambda ctx: fired.append(ctx.session_id))
    as_role("hive", None)
    asyncio.run(lm.LifecycleManager._scan_unprocessed_sessions(mgr))
    assert fired == ["sess_new_22"]
    fired.clear()
    as_role(None, None)
    asyncio.run(lm.LifecycleManager._scan_unprocessed_sessions(mgr))
    assert fired == ["sess_done_1", "sess_new_22"]


async def _async(v):
    return v


def test_a_worker_resume_context_carries_no_earlier_tasks_crash_checkpoint(as_role, tmp_path, monkeypatch):
    from core import context_injector as ci
    cp = tmp_path / "session_checkpoint.json"
    cp.write_text(json.dumps({"tool_count": 7, "files": ["EARLIER_TASK_FILE.py"]}))
    monkeypatch.setattr("jobs.paths.STATE_DIR", tmp_path)
    msgs = [{"role": "user", "content": "go"}, {"role": "assistant", "content": "ok"}]
    as_role(None, None)
    assert "Crash recovery" in ci._build_checkpoint(msgs)
    as_role("hive", None)
    assert "Crash recovery" not in ci._build_checkpoint(msgs)


def test_expired_candidates_without_units_are_counted_one_each(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_DATA_DIR", str(tmp_path))
    from core.ingestion.store import connect_rw
    from core.ingestion.worker import cleanup_expired
    for n in (1, 2, 3):              # one day, one source and lane: one stats row
        _old_inbox_row(40, n)
    conn = connect_rw()
    try:
        assert cleanup_expired(conn) == 3
        rows = conn.execute("SELECT state, SUM(candidates), SUM(units) FROM ingestion_daily_stats "
                            "GROUP BY state").fetchall()
    finally:
        conn.close()
    assert [tuple(r) for r in rows] == [("expired_unprocessed", 3, 0)]


def test_a_worker_handle_that_failed_to_start_stops_cleanly(as_role, tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_DATA_DIR", str(tmp_path / "data"))
    as_role("hive", None)
    from core.ingestion.owner import acquire
    from core.ingestion.worker import WorkerHandle
    owner = acquire()
    handle = WorkerHandle(owner, tmp_path)

    def boom():
        raise RuntimeError("no thread")
    monkeypatch.setattr(handle._maint, "start", boom)
    with pytest.raises(RuntimeError):
        handle.start()
    assert not handle.s2_executor._thread.is_alive()      # what had started was stopped
    handle.stop_and_join(5)                                # the maintenance never ran
    again = acquire()
    assert again is not None
    again.release()


# ── no other dispatch's work reaches a worker's prompt (C1/C2 of the 2026-10-30
#    Hive-vs-Host review) ──────────────────────────────────────────────────────

def test_a_worker_injects_no_governance_audit_hint(as_role, tmp_path, monkeypatch):
    """The audit hint is about the Host's governed files, not a worker's dispatch."""
    from tests.test_prompt_builder_properties import _make_builder
    from core import governance_audit
    ws = tmp_path / "ws"
    (ws / ".context").mkdir(parents=True)
    monkeypatch.setattr(governance_audit, "audit_hint",
                        lambda wd, model: "- EARLIER BRIEFING MARKER")

    def full_text() -> str:
        cfg: dict = {}
        asyncio.run(_make_builder().build_system_prompt(agent_config=cfg, working_directory=str(ws)))
        return cfg.get("_system_prompt_metadata", {}).get("full_text", "")
    as_role(None, None)
    assert "EARLIER BRIEFING MARKER" in full_text()
    as_role("hive", "reference")
    assert "EARLIER BRIEFING MARKER" in full_text()
    as_role("hive", None)
    assert "EARLIER BRIEFING MARKER" not in full_text()


def test_a_worker_recalls_only_its_brain(as_role, monkeypatch):
    """Sessions, Knowledge and memory on a worker belong to other dispatches: only the
    task's own Brain (the DDD leg) is recalled, on the first message and later ones."""
    from unittest.mock import MagicMock, patch
    from core import session_router as sr
    ddd_calls: list[str] = []

    async def fake_ddd(user_message, options, unit, editor_context, mutate):
        ddd_calls.append(user_message)
    monkeypatch.setattr(sr, "_maybe_inject_ddd", fake_ddd)

    def run(msg: str, unit=None):
        unit = unit or MagicMock(_recall_injected=False, is_channel_session=False, _recall_keyword_misses=0,
                                 _ddd_injected=False, _active_project=None)
        opts = MagicMock(system_prompt="## Base")
        with patch.object(sr, "_unified_recall_body", return_value=("OTHER DISPATCH RECALL", [])):
            asyncio.run(sr._maybe_inject_recall(user_message=msg, options=opts, unit=unit))
        return opts.system_prompt, unit
    as_role(None, None)
    sp, _ = run("How does the evolution pipeline work?")
    assert "OTHER DISPATCH RECALL" in sp
    as_role("hive", None)
    sp, unit = run("How does the evolution pipeline work?")
    assert "OTHER DISPATCH RECALL" not in sp and ddd_calls[-1].startswith("How does")
    sp, _ = run("Now explain the recall filter and its budget", unit)       # a later topic
    assert "OTHER DISPATCH RECALL" not in sp


def test_both_startup_paths_set_aside_inherited_cognition():
    """The set-aside ran only on the full-init (dev) path, so no installed Hive, which
    starts on the fast path, ever set its inherited cognition aside. It must be a
    statement of lifespan itself, after the fast/full split, not inside one branch."""
    import ast
    import inspect
    import main
    fn = next(n for n in ast.walk(ast.parse(inspect.getsource(main)))
              if isinstance(n, ast.AsyncFunctionDef) and n.name == "lifespan")

    def calls_it(node) -> bool:
        return any(isinstance(c, ast.Call) and getattr(c.func, "id", None) == "_set_aside_inherited_cognition"
                   for c in ast.walk(node))
    split = next(i for i, st in enumerate(fn.body)
                 if isinstance(st, ast.If) and getattr(st.test, "id", None) == "skip_init_pipeline")
    assert not calls_it(fn.body[split])
    assert any(calls_it(st) for st in fn.body[split + 1:])


def test_the_set_aside_helper_moves_a_workers_memory(as_role, tmp_path, monkeypatch):
    import main
    from core import worker_reset
    ws = tmp_path / "ws"
    (ws / ".context").mkdir(parents=True)
    (ws / ".context" / "MEMORY.md").write_text("EARLIER DISPATCH COE")
    from core.initialization_manager import initialization_manager
    monkeypatch.setattr(initialization_manager, "get_cached_workspace_path", lambda: str(ws))
    as_role("hive", None)
    asyncio.run(main._set_aside_inherited_cognition())
    assert not (ws / ".context" / "MEMORY.md").exists() and (ws / worker_reset.MARKER).exists()
