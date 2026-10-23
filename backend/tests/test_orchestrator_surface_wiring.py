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
    permission_manager.clear_session("sess-test")
    yield
    permission_manager.clear_session("sess-test")


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
