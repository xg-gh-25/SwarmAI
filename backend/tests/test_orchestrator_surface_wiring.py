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
    build_surface_events (glob+read+stat) OFF the event loop via asyncio.to_thread —
    SYMMETRIC with the two completion branches (8103fc37 moved those off-loop but
    missed this third one). Running it inline stalls the shared daemon loop (+/health)
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


# ── (g) BACKEND-AUTO surface on pipeline COMPLETION (run_beff6754) ──────────────
#    The regression this run fixes: a docs-only pipeline (0 source commits) never
#    calls surface_run_outputs (complete.md tells it to skip), so the REPORT.md
#    kind=knowledge event was never emitted and Canvas never auto-opened on
#    completion. The orchestrator must OBSERVE the `run-update --status completed`
#    Bash command and auto-fire build_surface_events — WITHOUT the agent calling any
#    surface tool. Authority is run.json status=="completed" (a blocked completion
#    exits 0, so the command string is only a pre-filter). These drive the REAL
#    generator; the only mocks are the two leaf helpers (ensure/build) + a run.json
#    status reader, exactly like the surface_run_outputs tests above.

def _bash_complete_tool_use(run_id="run_done1", tool_use_id="tu-c1",
                            status="completed"):
    """AssistantMessage carrying the Bash `run-update --status <status>` command the
    pipeline emits at COMPLETE (INSTRUCTIONS.md step 6)."""
    from claude_agent_sdk import AssistantMessage, ToolUseBlock
    cmd = (f"python backend/scripts/artifact_cli.py run-update "
           f"--project SwarmAI --run-id {run_id} --status {status}")
    return AssistantMessage(
        content=[ToolUseBlock(id=tool_use_id, name="Bash", input={"command": cmd})],
        model="test-model",
    )


def _patch_completion_helpers(monkeypatch, *, status="completed", run_id_seen=None,
                              build_calls=None):
    """Stub the three leaves the completion branch touches: ensure_report_for_run,
    build_surface_events, and the run.json status read. Records build_surface_events
    invocations into build_calls (a list) so idempotency can be asserted."""
    async def _ensure(run_id):
        return True

    def _build(run_id, workspace_root=None):
        if build_calls is not None:
            build_calls.append(run_id)
        return [{"type": "file_changed", "path": f"Projects/P/.artifacts/runs/{run_id}/REPORT.md",
                 "operation": "written", "relevance": "deliverable", "kind": "knowledge"}]

    def _status(run_id):
        return status  # authority: the orchestrator trusts run.json, not the cmd string

    monkeypatch.setattr("core.streaming_orchestrator.ensure_report_for_run", _ensure)
    monkeypatch.setattr("core.ui_actions.build_surface_events",_build)
    # The status reader is a new helper the orchestrator uses; patch it at the
    # orchestrator import site (it imports the name).
    monkeypatch.setattr("core.streaming_orchestrator.read_run_status", _status,
                        raising=False)


def test_docs_only_completion_auto_emits_knowledge_event(monkeypatch):
    """THE golden case. A docs-only run completes via `run-update --status completed`
    (no surface_run_outputs call at all). The orchestrator must auto-emit the
    kind=knowledge REPORT.md file_changed event — the exact event the frontend needs
    to auto-open Canvas. RED on current code (no completion-observation branch)."""
    build_calls = []
    _patch_completion_helpers(monkeypatch, status="completed", build_calls=build_calls)
    orch = _make_orchestrator()
    events, raised = asyncio.run(
        _drive(orch, [_bash_complete_tool_use(run_id="run_docs1"),
                      _tool_result(tool_use_id="tu-c1"), _make_result_message()])
    )
    assert raised is None
    fc = [e for e in events if e.get("type") == "file_changed"]
    assert len(fc) == 1, f"expected 1 auto-surfaced event on completion, got {fc}"
    assert fc[0]["kind"] == "knowledge", "must be the REPORT.md knowledge event (frontend auto-pop)"
    assert str(fc[0]["path"]).endswith("REPORT.md")
    assert build_calls == ["run_docs1"], "build_surface_events must run for the completed run"


def test_completion_not_completed_status_does_not_emit(monkeypatch):
    """AC4: run.json status is the AUTHORITY. A `--status completed` command whose
    run.json status is NOT 'completed' (e.g. the gate BLOCKED it — CLI still exits 0)
    must NOT surface. Guards the exit-code-is-useless finding (Gate-1 FLAW 2)."""
    build_calls = []
    _patch_completion_helpers(monkeypatch, status="paused", build_calls=build_calls)
    orch = _make_orchestrator()
    events, raised = asyncio.run(
        _drive(orch, [_bash_complete_tool_use(run_id="run_blocked"),
                      _tool_result(tool_use_id="tu-c1"), _make_result_message()])
    )
    assert raised is None
    assert not [e for e in events if e.get("type") == "file_changed"], \
        "a completion command that did NOT actually complete (run.json != completed) must not surface"
    assert build_calls == [], "build_surface_events must NOT run when run.json status != completed"


def test_failed_completion_tool_result_does_not_emit(monkeypatch):
    """A FAILED completion tool_result (is_error) must not surface — mirrors the
    write/delete emit gating on `not is_error`."""
    build_calls = []
    _patch_completion_helpers(monkeypatch, status="completed", build_calls=build_calls)
    orch = _make_orchestrator()
    events, raised = asyncio.run(
        _drive(orch, [_bash_complete_tool_use(run_id="run_err"),
                      _tool_result(tool_use_id="tu-c1", is_error=True), _make_result_message()])
    )
    assert raised is None
    assert not [e for e in events if e.get("type") == "file_changed"]
    assert build_calls == []


def test_completion_idempotent_after_explicit_surface(monkeypatch):
    """AC2: if the agent ALSO called surface_run_outputs for the same run earlier this
    session, the completion observation must NOT double-emit. Drive surface THEN
    completion for the same run_id; build_surface_events runs exactly once."""
    build_calls = []
    _patch_completion_helpers(monkeypatch, status="completed", build_calls=build_calls)
    orch = _make_orchestrator()
    events, raised = asyncio.run(
        _drive(orch, [
            _surface_tool_use(run_id="run_dup", tool_use_id="tu-s1"),
            _bash_complete_tool_use(run_id="run_dup", tool_use_id="tu-c1"),
            _tool_result(tool_use_id="tu-c1"),
            _make_result_message(),
        ])
    )
    assert raised is None
    assert build_calls == ["run_dup"], \
        f"build_surface_events must run exactly once (explicit surface then completion), got {build_calls}"


def test_completion_first_then_explicit_surface_no_double_emit(monkeypatch):
    """AC2 (symmetric idempotency — Gate-2 correctness finding): if the backend-auto
    completion surfaces FIRST, a subsequent explicit surface_run_outputs for the SAME
    run must NOT re-emit. Drives completion THEN surface; build_surface_events runs
    exactly once. Guards the asymmetry the adversarial reviewer caught."""
    build_calls = []
    _patch_completion_helpers(monkeypatch, status="completed", build_calls=build_calls)
    orch = _make_orchestrator()
    events, raised = asyncio.run(
        _drive(orch, [
            _bash_complete_tool_use(run_id="run_ce", tool_use_id="tu-c1"),
            _tool_result(tool_use_id="tu-c1"),
            _surface_tool_use(run_id="run_ce", tool_use_id="tu-s1"),
            _make_result_message(),
        ])
    )
    assert raised is None
    assert build_calls == ["run_ce"], \
        f"completion-first then explicit surface must emit exactly once, got {build_calls}"


def test_completion_build_failure_does_not_break_turn(monkeypatch):
    """AC4: build_surface_events raising in the completion branch must be swallowed —
    the streaming turn survives (fail-safe on the hot path)."""
    async def _ensure(run_id):
        return True

    def _build_boom(run_id, workspace_root=None):
        raise RuntimeError("build exploded")

    monkeypatch.setattr("core.streaming_orchestrator.ensure_report_for_run", _ensure)
    monkeypatch.setattr("core.ui_actions.build_surface_events",_build_boom)
    monkeypatch.setattr("core.streaming_orchestrator.read_run_status",
                        lambda run_id: "completed", raising=False)
    orch = _make_orchestrator()
    events, raised = asyncio.run(
        _drive(orch, [_bash_complete_tool_use(run_id="run_boom"),
                      _tool_result(tool_use_id="tu-c1"), _make_result_message()])
    )
    assert raised is None, "a build_surface_events crash must not kill the streaming turn"


# ── (h) Layer-4 CROSS-BOUNDARY E2E — drive the REAL seam end-to-end ─────────────
#    cross_boundary.value == true (SSE/event-bus/ACT-SENSE). The seam is:
#      completion command → orchestrator observation → REAL build_surface_events
#      → file_changed(kind=knowledge) SSE → (frontend useCanvasAutoSurface gate).
#    This test does NOT stub build_surface_events or read_run_status — it runs the
#    REAL functions against a REAL on-disk run.json + REPORT.md for a DOCS-ONLY run
#    (0 commits). Only get_swarmws (the workspace-root leaf) is redirected to tmp.
#    Mutation-verified: reverting the completion-emit branch → this goes RED (proven
#    manually during BUILD; the wiring tests above are the automated mutation guards).

def test_layer4_docs_only_completion_drives_real_surface_e2e(tmp_path, monkeypatch):
    """E2E: a docs-only run (commits=[]) with a real REPORT.md on disk, completed via
    the real `run-update --status completed` observation, drives the REAL
    build_surface_events + read_run_status (NOT mocked) and emits the kind=knowledge
    REPORT.md event the frontend needs. This is the true seam, unmocked."""
    import json
    # Real docs-only run on disk: 0 commits, status=completed, healthy REPORT.md.
    run_dir = tmp_path / "Projects" / "SwarmAI" / ".artifacts" / "runs" / "run_e2e_docs"
    run_dir.mkdir(parents=True)
    (run_dir / "run.json").write_text(json.dumps({"commits": [], "status": "completed"}))
    (run_dir / "REPORT.md").write_text("# Pipeline Report\n" + ("x" * 600))

    # Redirect BOTH resolvers that the real functions use to the tmp workspace.
    monkeypatch.setattr("core.ui_actions.get_swarmws", lambda: str(tmp_path))
    # ensure_report_for_run early-returns for 0-commit runs (correct — no source batch
    # to pair); the report already exists, so build_surface_events appends it anyway.

    orch = _make_orchestrator()
    events, raised = asyncio.run(
        _drive(orch, [_bash_complete_tool_use(run_id="run_e2e_docs"),
                      _tool_result(tool_use_id="tu-c1"), _make_result_message()])
    )
    assert raised is None
    fc = [e for e in events if e.get("type") == "file_changed"]
    assert len(fc) == 1, f"real seam must emit exactly the REPORT.md event, got {fc}"
    assert fc[0]["kind"] == "knowledge"
    assert fc[0]["relevance"] == "deliverable"
    assert str(fc[0]["path"]).endswith("REPORT.md")


def test_layer4_real_status_authority_blocks_incomplete_run(tmp_path, monkeypatch):
    """E2E mutation-companion: the SAME real path, but run.json status is NOT
    'completed' (a gate-BLOCKED completion — the CLI still exited 0). The REAL
    read_run_status must return the real status and the orchestrator must NOT surface.
    Proves the status-authority gate is load-bearing against real data, not the mock."""
    import json
    run_dir = tmp_path / "Projects" / "SwarmAI" / ".artifacts" / "runs" / "run_e2e_blocked"
    run_dir.mkdir(parents=True)
    # status=running: the completion gate blocked it, but the command string said
    # --status completed. Real read_run_status returns 'running' → no surface.
    (run_dir / "run.json").write_text(json.dumps({"commits": [], "status": "running"}))
    (run_dir / "REPORT.md").write_text("# Pipeline Report\n" + ("x" * 600))
    monkeypatch.setattr("core.ui_actions.get_swarmws", lambda: str(tmp_path))

    orch = _make_orchestrator()
    events, raised = asyncio.run(
        _drive(orch, [_bash_complete_tool_use(run_id="run_e2e_blocked"),
                      _tool_result(tool_use_id="tu-c1"), _make_result_message()])
    )
    assert raised is None
    assert not [e for e in events if e.get("type") == "file_changed"], \
        "real run.json status != completed must NOT surface (authority is run.json, not the cmd)"


def test_layer4_frontend_gate_accepts_the_emitted_kind():
    """Cross-language contract binding: the kind the completion path emits
    ('knowledge', from build_surface_events) MUST be in the frontend auto-pop accepted
    set parsed from useCanvasAutoSurface.ts — else Canvas would never open on it. Binds
    the backend emit to the REAL frontend gate source (a divergence goes RED)."""
    import re as _re
    from pathlib import Path as _P
    ts = (_P(__file__).resolve().parents[2] / "desktop" / "src" / "hooks"
          / "useCanvasAutoSurface.ts").read_text()
    kind_line = next(l for l in ts.splitlines()
                     if "kind !== undefined" in l and "return" in l)
    accepted = set(_re.findall(r"kind !== '([a-z-]+)'", kind_line))
    assert "knowledge" in accepted, (
        f"the completion path emits kind=knowledge; frontend accepted kinds are "
        f"{accepted} — if 'knowledge' is missing, Canvas would never auto-open on "
        f"pipeline completion (the whole point of this fix)")
