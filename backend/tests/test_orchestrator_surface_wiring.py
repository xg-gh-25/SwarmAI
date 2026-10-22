"""Integration tests for EVENT-DRIVEN Canvas write-surfacing WIRING in the
streaming orchestrator (run_cce6f4b9 — the root-fix that REPLACED the per-turn
git-snapshot sweep, run_4de279ca, with a per-tool-result emit).

These drive the REAL `_read_formatted_response` generator against a scripted SDK
message stream and FORCE the write-emit path — the meta-lesson (GC17/R28): every
emit/guard branch needs a test that ENTERS it.

The event-driven model (symmetric with the surviving DELETE emit):
  - a Write/Edit/NotebookEdit ToolUseBlock (AssistantMessage) registers the path in
    `_pending_file_changes[block.id]`;
  - the matching ToolResultBlock(success) (UserMessage) pops it and emits ONE
    file_changed(operation="written") per resolved path, MID-STREAM (before result).
  - the file_changed carries the file's TRUE `kind` (content/knowledge/source) from
    `needs_human_review`; the FRONTEND gates rail-vs-pop by kind (backend emits all
    review-worthy). This mirrors `_build_file_delete_events` exactly.

Paths covered:
  (a) a parent Write → tool_result(success) EMITS file_changed(written), before result
  (b) a FAILED tool_result (is_error) does NOT emit (a failed write never surfaced)
  (c) a NON-review-worthy write (needs_human_review → not review_worthy) is dropped
  (d) source kind is emitted with kind="source" (frontend suppresses; backend does NOT gate)
  (e) the deleted git-sweep path is GONE: no porcelain_snapshot / sweep_turn_delta call

Boundary mocks ONLY: `needs_human_review` (git check-ignore leaf) + `resolve_path_to_physical`
(filesystem leaf). The orchestrator emit logic itself is exercised for real (no self-mock).
"""
from __future__ import annotations

import asyncio

import pytest

# Reuse the proven harness (fake SDK client + parent stub) from the leak-guard suite.
from tests.test_tool_call_leak_guard import (
    _drive,
    _make_orchestrator,
    _make_result_message,
)


@pytest.fixture(autouse=True)
def _isolate_permission_queue():
    """Each test drives the orchestrator under its OWN event loop (asyncio.run —
    RP62 fix: run_until_complete borrowed a prior test's loop and went RED once a
    sibling suite closed it). But _read_formatted_response fetches a per-session
    asyncio.Queue from the module-level `permission_manager` singleton, cached by
    session_id ("sess-test" — hardcoded in _make_orchestrator). Once test N's loop
    closes, that cached Queue is bound to a DEAD loop; test N+1's `perm_queue.get()`
    then hangs forever. Clear the cached queue between tests so each fresh loop gets
    a fresh queue. (Test-only: in production a session maps to one immortal daemon
    loop, so this cross-loop hazard cannot arise.)

    NOTE for future extension: `ask_question_manager._answer_events` is the SAME
    class of loop-bound singleton cache. These tests never enter the
    ask_user_question path, so it is not cleared here — but if you add a test that
    does, clear it in this fixture too or it will hang identically."""
    from core.permission_manager import permission_manager
    permission_manager.remove_session_queue("sess-test")
    yield
    permission_manager.remove_session_queue("sess-test")


def _write_tool_use(tool_use_id: str = "tu-w1", file_path: str = "Knowledge/Designs/x.md"):
    """An AssistantMessage carrying a single Write ToolUseBlock — registers the path
    in _pending_file_changes[id]."""
    from claude_agent_sdk import AssistantMessage, ToolUseBlock

    return AssistantMessage(
        content=[ToolUseBlock(id=tool_use_id, name="Write", input={"file_path": file_path})],
        model="test-model",
    )


def _tool_result(tool_use_id: str = "tu-w1", is_error: bool = False):
    """A UserMessage carrying the matching ToolResultBlock — where the write emit fires
    (mirrors how sub-agent + parent tool results arrive: UserMessage, per SDK)."""
    from claude_agent_sdk import UserMessage, ToolResultBlock

    return UserMessage(
        content=[ToolResultBlock(tool_use_id=tool_use_id, content="ok", is_error=is_error)]
    )


def _patch_boundaries(monkeypatch, *, review_worthy: bool = True, kind: str = "content"):
    """Stub the two LEAF boundaries the emit calls: resolve_path_to_physical (fs) and
    needs_human_review (git check-ignore). NOT the orchestrator logic."""
    monkeypatch.setattr("core.project_registry.get_swarmws", lambda: "/tmp/ws")

    def _resolve(raw, _ws_root):
        # a just-written file always resolves; return a workspace-relative + absolute pair
        return {"relative": raw, "absolute": f"/tmp/ws/{raw}"}

    monkeypatch.setattr("routers.workspace_api.resolve_path_to_physical", _resolve)

    from core.needs_human_review import ReviewVerdict

    def _verdict(_path, _op="written"):
        return ReviewVerdict(review_worthy, kind, None)

    monkeypatch.setattr("core.needs_human_review.needs_human_review", _verdict)


# ── (a) a Write → result(success) EMITS file_changed(written), before result ────

def test_write_tool_result_emits_file_changed(monkeypatch):
    _patch_boundaries(monkeypatch, review_worthy=True, kind="content")
    orch = _make_orchestrator()
    events, raised = asyncio.run(
        _drive(orch, [_write_tool_use(file_path="Knowledge/Designs/x.md"),
                      _tool_result(), _make_result_message()])
    )
    assert raised is None
    fc = [e for e in events if e.get("type") == "file_changed" and e.get("operation") == "written"]
    assert len(fc) == 1, f"expected 1 written file_changed, got {fc}"
    assert fc[0]["path"] == "Knowledge/Designs/x.md"
    assert fc[0]["absolutePath"] == "/tmp/ws/Knowledge/Designs/x.md"
    assert fc[0]["kind"] == "content"
    # emitted mid-stream, BEFORE the result event (OT01 torn-state ordering)
    types_seq = [e["type"] for e in events]
    assert types_seq.index("file_changed") < types_seq.index("result")


# ── (b) a FAILED tool_result does NOT emit (a failed write never surfaced) ──────

def test_failed_write_tool_result_does_not_emit(monkeypatch):
    _patch_boundaries(monkeypatch, review_worthy=True, kind="content")
    orch = _make_orchestrator()
    events, raised = asyncio.run(
        _drive(orch, [_write_tool_use(), _tool_result(is_error=True), _make_result_message()])
    )
    assert raised is None
    assert not [e for e in events if e.get("type") == "file_changed"], \
        "a FAILED write tool_result must NOT surface a file_changed"


# ── (c) a NON-review-worthy write is dropped ────────────────────────────────────

def test_non_review_worthy_write_is_dropped(monkeypatch):
    _patch_boundaries(monkeypatch, review_worthy=False, kind="process")
    orch = _make_orchestrator()
    events, raised = asyncio.run(
        _drive(orch, [_write_tool_use(), _tool_result(), _make_result_message()])
    )
    assert raised is None
    assert not [e for e in events if e.get("type") == "file_changed"], \
        "a non-review-worthy (process/gitignored) write must not surface"


# ── (d) source kind is emitted with true kind (backend does NOT gate; frontend does) ─

def test_source_write_emits_with_source_kind(monkeypatch):
    _patch_boundaries(monkeypatch, review_worthy=True, kind="source")
    orch = _make_orchestrator()
    events, raised = asyncio.run(
        _drive(orch, [_write_tool_use(file_path="backend/foo.py"),
                      _tool_result(), _make_result_message()])
    )
    assert raised is None
    fc = [e for e in events if e.get("type") == "file_changed" and e.get("operation") == "written"]
    assert len(fc) == 1, "a review-worthy source write IS emitted (frontend suppresses it, backend does not gate)"
    assert fc[0]["kind"] == "source", "the true kind must be carried so the frontend can gate rail-vs-pop"


# ── (d2) a Bash rm → result emits operation=deleted (symmetric w/ write) ────────

def _bash_rm_tool_use(tool_use_id: str = "tu-rm1", path: str = "Knowledge/Designs/old.md"):
    from claude_agent_sdk import AssistantMessage, ToolUseBlock

    return AssistantMessage(
        content=[ToolUseBlock(id=tool_use_id, name="Bash", input={"command": f"rm {path}"})],
        model="test-model",
    )


def test_bash_rm_tool_result_emits_deleted(monkeypatch):
    # delete path verdict now runs off-loop (RP53 fix) — same boundary stub as writes.
    _patch_boundaries(monkeypatch, review_worthy=True, kind="content")
    orch = _make_orchestrator()
    events, raised = asyncio.run(
        _drive(orch, [_bash_rm_tool_use(path="Knowledge/Designs/old.md"),
                      _tool_result(tool_use_id="tu-rm1"), _make_result_message()])
    )
    assert raised is None
    deleted = [e for e in events if e.get("type") == "file_changed" and e.get("operation") == "deleted"]
    assert len(deleted) == 1, f"expected 1 deleted file_changed, got {deleted}"
    assert "old.md" in deleted[0]["path"]


# ── (e) the deleted git-sweep live path is GONE ─────────────────────────────────

def test_live_git_sweep_symbols_removed():
    """The turn-START porcelain_snapshot baseline + turn-END sweep_turn_delta live
    path must be DELETED from the orchestrator (root-fix run_cce6f4b9). Assert the
    CALLS are gone (not mere mentions — the deletion comment names them on purpose),
    AND the functions themselves no longer exist in run_surface_changes. The
    finish-time sweep_run_changes STAYS (AC4)."""
    import re
    import inspect
    from core.streaming_orchestrator import StreamingOrchestrator

    src = inspect.getsource(StreamingOrchestrator)
    # A CALL/import of the deleted sweep symbols — not a bare mention in a comment.
    # `import sweep_turn_delta`, `sweep_turn_delta(`, `porcelain_snapshot(` would all
    # be live uses; a `# ... sweep_turn_delta ...` comment is allowed (documents the delete).
    for sym in ("sweep_turn_delta", "porcelain_snapshot"):
        assert not re.search(rf"\b{sym}\s*\(", src), f"{sym}(...) call must be removed from the orchestrator"
        assert not re.search(rf"import\s+[^\n]*\b{sym}\b", src), f"{sym} import must be removed from the orchestrator"

    # The functions themselves must be GONE from run_surface_changes (not just uncalled).
    import core.run_surface_changes as rsc
    assert not hasattr(rsc, "sweep_turn_delta"), "sweep_turn_delta must be deleted from run_surface_changes"
    assert not hasattr(rsc, "porcelain_snapshot"), "porcelain_snapshot must be deleted from run_surface_changes"
    # the finish-time fallback must still be importable (AC4)
    from core.run_surface_changes import sweep_run_changes  # noqa: F401


# ── (f) surface_run_outputs tool_use AWAITS ensure_report_for_run BEFORE the batch ─
#    (run_f1fbf37d — the ordering root-fix wiring). The orchestrator must guarantee
#    REPORT.md exists (consumer-side) before build_surface_events emits, so the
#    report row can never be dropped by a too-early surface call.

def _surface_tool_use(run_id: str = "run_abc123", tool_use_id: str = "tu-s1"):
    """An AssistantMessage carrying the surface_run_outputs ToolUseBlock — the block
    the orchestrator observes BY NAME at the SURFACE_OUTPUTS branch."""
    from claude_agent_sdk import AssistantMessage, ToolUseBlock
    from core.ui_actions import SURFACE_OUTPUTS_FULL_TOOL_NAME

    return AssistantMessage(
        content=[ToolUseBlock(id=tool_use_id, name=SURFACE_OUTPUTS_FULL_TOOL_NAME,
                              input={"run_id": run_id})],
        model="test-model",
    )


def test_surface_tool_awaits_ensure_report_before_batch(monkeypatch):
    """The orchestrator MUST await ensure_report_for_run(run_id) BEFORE calling
    build_surface_events — proving the consumer guarantees its own input (REPORT.md)
    before emitting. Spy on both; assert ensure ran, with the run_id, first."""
    order: list[str] = []

    async def _spy_ensure(run_id):
        order.append(f"ensure:{run_id}")
        return True

    def _spy_build(run_id, workspace_root=None):
        order.append(f"build:{run_id}")
        return [{"type": "file_changed", "path": "b.py", "operation": "written",
                 "relevance": "deliverable", "kind": "source-final"}]

    # patch at the orchestrator's import site (it imported the names into its module)
    monkeypatch.setattr("core.streaming_orchestrator.ensure_report_for_run", _spy_ensure)
    monkeypatch.setattr("core.ui_actions.build_surface_events",_spy_build)

    orch = _make_orchestrator()
    events, raised = asyncio.run(
        _drive(orch, [_surface_tool_use(run_id="run_xyz"), _make_result_message()])
    )
    assert raised is None
    # ensure ran, with the right run_id, and BEFORE build (consumer-guarantees-input).
    assert order == ["ensure:run_xyz", "build:run_xyz"], f"wrong order/args: {order}"
    # the surface batch still emits (fall-through preserved).
    fc = [e for e in events if e.get("type") == "file_changed"]
    assert len(fc) == 1 and fc[0]["kind"] == "source-final"


def test_surface_tool_survives_ensure_failure(monkeypatch):
    """AC4 wiring: if ensure_report_for_run itself raised (it shouldn't — it's
    fail-safe — but belt-and-suspenders), the orchestrator turn must not die. Here we
    assert the normal fail-safe contract: ensure returns False, batch still emits."""
    async def _ensure_false(run_id):
        return False  # e.g. report-gen failed; degrade to source-only

    monkeypatch.setattr("core.streaming_orchestrator.ensure_report_for_run", _ensure_false)

    def _build(run_id, workspace_root=None):
        return [{"type": "file_changed", "path": "b.py", "operation": "written",
                 "relevance": "deliverable", "kind": "source-final"}]

    monkeypatch.setattr("core.ui_actions.build_surface_events",_build)

    orch = _make_orchestrator()
    events, raised = asyncio.run(
        _drive(orch, [_surface_tool_use(), _make_result_message()])
    )
    assert raised is None, "ensure returning False must not break the turn"
    assert [e for e in events if e.get("type") == "file_changed"], \
        "source rows must still emit even when ensure could not produce a report"


def test_surface_tool_builds_off_loop(monkeypatch):
    """REGRESSION (audit Finding 2): the explicit surface_run_outputs branch MUST run
    build_surface_events (glob+read+stat) OFF the event loop via asyncio.to_thread.
    Running it inline stalls the shared daemon loop (+/health)
    on cold-cache/slow-disk mid-streaming-turn.

    Assert build_surface_events executed in a DIFFERENT thread than the event loop —
    the observable signature of to_thread offloading."""
    import threading

    loop_thread = threading.get_ident()
    build_thread: dict[str, int] = {}

    async def _ensure(run_id):
        return True

    def _build(run_id, workspace_root=None):
        build_thread["tid"] = threading.get_ident()
        return [{"type": "file_changed", "path": "b.py", "operation": "written",
                 "relevance": "deliverable", "kind": "source-final"}]

    monkeypatch.setattr("core.streaming_orchestrator.ensure_report_for_run", _ensure)
    monkeypatch.setattr("core.ui_actions.build_surface_events",_build)

    orch = _make_orchestrator()
    events, raised = asyncio.run(
        _drive(orch, [_surface_tool_use(run_id="run_offloop"), _make_result_message()])
    )
    assert raised is None
    assert "tid" in build_thread, "build_surface_events was never called"
    assert build_thread["tid"] != loop_thread, (
        "build_surface_events ran ON the event-loop thread — the glob+read+stat is "
        "NOT off-loop (Finding 2 regression: use await asyncio.to_thread)"
    )
    # the batch still emits (offloading preserves the fall-through).
    fc = [e for e in events if e.get("type") == "file_changed"]
    assert len(fc) == 1 and fc[0]["kind"] == "source-final"


def _patch_surface_helpers(monkeypatch, *, build_calls=None):
    """Stub the two leaves the explicit surface branch touches: ensure_report_for_run
    and build_surface_events. Records build_surface_events invocations into
    build_calls (a list) so idempotency can be asserted."""
    async def _ensure(run_id):
        return True

    def _build(run_id, workspace_root=None):
        if build_calls is not None:
            build_calls.append(run_id)
        return [{"type": "file_changed", "path": f"Projects/P/.artifacts/runs/{run_id}/REPORT.md",
                 "operation": "written", "relevance": "deliverable", "kind": "knowledge"}]

    monkeypatch.setattr("core.streaming_orchestrator.ensure_report_for_run", _ensure)
    monkeypatch.setattr("core.ui_actions.build_surface_events", _build)


def test_layer4_frontend_gate_accepts_the_emitted_kind():
    """Cross-language contract binding: the kind a REPORT.md surface emits
    ('knowledge', from build_surface_events) MUST be in the frontend auto-pop accepted
    set parsed from useCanvasAutoSurface.ts — else Canvas would never open on it. Binds
    the backend emit to the REAL frontend gate source (a divergence goes RED)."""
    import re as _re
    from pathlib import Path as _P
    ts = (_P(__file__).resolve().parents[2] / "desktop" / "src" / "hooks"
          / "useCanvasAutoSurface.ts").read_text()
    # run_5d9178bf: the auto-pop whitelist moved from an inline chain of
    # `kind !== 'x'` comparisons to a named `AUTO_POP_KINDS = [...]` array (so the
    # external-diff/external-nodiff kinds could join without a 5-clause line). Parse
    # that array — still binds to the REAL frontend source, still goes RED if the
    # gate ever drops 'knowledge' (the REPORT.md surface emit).
    arr_line = next(l for l in ts.splitlines() if "AUTO_POP_KINDS" in l and "[" in l)
    accepted = set(_re.findall(r"'([a-z-]+)'", arr_line))
    assert "knowledge" in accepted, (
        f"a REPORT.md surface emits kind=knowledge; frontend AUTO_POP_KINDS are "
        f"{accepted} — if 'knowledge' is missing, Canvas would never auto-open on it")


# ── run_a87bfe3f Gate-2: the marker attests only rows this session EMITTED ──────
def _marker_run(tmp_path, monkeypatch, commits):
    import json as _json
    d = tmp_path / "Projects" / "P" / ".artifacts" / "runs" / "run_mk"
    d.mkdir(parents=True)
    (d / "run.json").write_text(_json.dumps({"id": "run_mk", "commits": commits}), encoding="utf-8")
    monkeypatch.setattr("core.ui_actions.get_swarmws", lambda: str(tmp_path))

    async def _ensure(run_id):
        return True
    monkeypatch.setattr("core.streaming_orchestrator.ensure_report_for_run", _ensure)
    monkeypatch.setattr("core.ui_actions.build_surface_events", lambda rid, workspace_root=None: [
        {"type": "file_changed", "path": "b.py", "operation": "written",
         "relevance": "deliverable", "kind": "source-final"}])
    return d


def test_explicit_surface_writes_marker_after_emitting(tmp_path, monkeypatch):
    import json as _json
    from core.surface_marker import MARKER_NAME
    d = _marker_run(tmp_path, monkeypatch, [{"sha": "c1", "files": ["b.py"]}])
    orch = _make_orchestrator()
    events, raised = asyncio.run(_drive(orch, [_surface_tool_use(run_id="run_mk"), _make_result_message()]))
    assert raised is None
    assert [e for e in events if e.get("type") == "file_changed"]
    assert _json.loads((d / MARKER_NAME).read_text())["commits"] == ["c1"]


def test_second_surface_after_new_commit_re_emits_and_updates_marker(tmp_path, monkeypatch):
    """A commit landing during the first build is left out of that marker (snapshot
    taken before the build), and the next explicit surface re-emits and covers it."""
    import json as _json
    from core.surface_marker import MARKER_NAME
    d = _marker_run(tmp_path, monkeypatch, [{"sha": "c1", "files": ["b.py"]}])
    calls = []

    def _build(rid, workspace_root=None):
        calls.append(rid)
        if len(calls) == 1:  # run-commit c2 lands while batch 1 is being built
            (d / "run.json").write_text(_json.dumps({"id": "run_mk", "commits": [
                {"sha": "c1", "files": ["b.py"]}, {"sha": "c2", "files": ["b.py"]}]}),
                encoding="utf-8")
        return [{"type": "file_changed", "path": "b.py", "operation": "written",
                 "relevance": "deliverable", "kind": "source-final"}]
    monkeypatch.setattr("core.ui_actions.build_surface_events", _build)
    orch = _make_orchestrator()
    events, raised = asyncio.run(_drive(orch, [
        _surface_tool_use(run_id="run_mk", tool_use_id="tu-a"),
        _surface_tool_use(run_id="run_mk", tool_use_id="tu-b"),
        _make_result_message()]))
    assert raised is None
    assert calls == ["run_mk", "run_mk"], "the uncovered commit must re-emit"
    assert _json.loads((d / MARKER_NAME).read_text())["commits"] == ["c1", "c2"]


def test_second_surface_without_new_commit_does_not_re_emit(tmp_path, monkeypatch):
    _marker_run(tmp_path, monkeypatch, [{"sha": "c1", "files": ["b.py"]}])
    calls = []

    def _build(rid, workspace_root=None):
        calls.append(rid)
        return [{"type": "file_changed", "path": "b.py", "operation": "written",
                 "relevance": "deliverable", "kind": "source-final"}]
    monkeypatch.setattr("core.ui_actions.build_surface_events", _build)
    orch = _make_orchestrator()
    events, raised = asyncio.run(_drive(orch, [
        _surface_tool_use(run_id="run_mk", tool_use_id="tu-a"),
        _surface_tool_use(run_id="run_mk", tool_use_id="tu-b"),
        _make_result_message()]))
    assert raised is None
    assert calls == ["run_mk"]


def test_docs_only_repeated_explicit_surface_emits_once(tmp_path, monkeypatch):
    """A batch with no source rows writes no marker; repeated explicit calls must still
    emit once (the in-memory commit set, not the marker, decides)."""
    import json as _json
    d = tmp_path / "Projects" / "P" / ".artifacts" / "runs" / "run_doc"
    d.mkdir(parents=True)
    (d / "run.json").write_text(_json.dumps({"id": "run_doc", "commits": []}), encoding="utf-8")
    monkeypatch.setattr("core.ui_actions.get_swarmws", lambda: str(tmp_path))
    build_calls = []
    _patch_surface_helpers(monkeypatch, build_calls=build_calls)
    orch = _make_orchestrator()
    asyncio.run(_drive(orch, [
        _surface_tool_use(run_id="run_doc", tool_use_id="tu-a"),
        _surface_tool_use(run_id="run_doc", tool_use_id="tu-b"),
        _make_result_message()]))
    assert build_calls == ["run_doc"], build_calls


def test_surface_without_snapshot_then_with_new_commit_re_emits(tmp_path, monkeypatch):
    """Recheck LOW: a first call that could not read run.json must not make a later
    call (that can) look already emitted for its commits."""
    import json as _json
    d = tmp_path / "Projects" / "P" / ".artifacts" / "runs" / "run_ns"
    monkeypatch.setattr("core.ui_actions.get_swarmws", lambda: str(tmp_path))
    calls = []

    def _build(rid, workspace_root=None):
        calls.append(rid)
        if len(calls) == 1:  # run.json appears only after the first call
            d.mkdir(parents=True)
            (d / "run.json").write_text(_json.dumps({"id": "run_ns", "commits": [{"sha": "c1"}]}),
                                        encoding="utf-8")
        return [{"type": "file_changed", "path": "b.py", "operation": "written",
                 "relevance": "deliverable", "kind": "source-final"}]

    async def _ensure(run_id):
        return True
    monkeypatch.setattr("core.streaming_orchestrator.ensure_report_for_run", _ensure)
    monkeypatch.setattr("core.ui_actions.build_surface_events", _build)
    orch = _make_orchestrator()
    asyncio.run(_drive(orch, [
        _surface_tool_use(run_id="run_ns", tool_use_id="tu-a"),
        _surface_tool_use(run_id="run_ns", tool_use_id="tu-b"),
        _make_result_message()]))
    assert calls == ["run_ns", "run_ns"]
