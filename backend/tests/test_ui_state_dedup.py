"""The UI-state block does not pile up in the model's conversation (canvas-proprioception.md §10).

What is tested:
- A: on a warm-reuse turn the full "## Current UI State" block is sent only when it
  differs from the last one this subprocess received; an identical one becomes a
  single "unchanged" line. A cold turn (UI state rides the system prompt) sends no
  prefix and forgets what was sent (the next warm turn sends the full block once).
  A Canvas that went from something to nothing says so once, then stays silent.
  A real compaction (compact_boundary) forgets it too — the old block may have
  been summarized away. The decided block is what the prefix builder uses (one
  decision for the delivered prefix and the TSCC capture). The one-line form is
  removed by strip_injected_blocks like the full block.
- B: the whole section stays ≤ 1,500 characters on the worst input, keeping the
  output total ("+N more").
"""
from core.injected_blocks import strip_injected_blocks
from core.prompt_builder import UI_SECTION_BUDGET, _render_ui_context_section
from core.session_router import (
    UI_STATE_NOTHING,
    UI_STATE_UNCHANGED,
    _build_dynamic_prefix_block,
    _decide_ui_block,
    _prepend_dynamic_context_to_query,
)


class _Unit:
    _last_ui_block = None


def _ctx(path="a.md", count=1):
    return {"file_path": path, "file_name": path, "canvas": {
        "open": True, "visible": "open", "tab": "artifacts", "output_count": count,
        "file": {"path": path, "name": path}, "outputs": [{"path": "r.md"}]}}


def test_warm_full_then_unchanged_then_changed():
    u = _Unit()
    first = _decide_ui_block(u, _ctx(), warm=True)
    assert first.startswith("## Current UI State") and "`a.md`" in first
    assert _decide_ui_block(u, _ctx(), warm=True) == UI_STATE_UNCHANGED
    changed = _decide_ui_block(u, _ctx("b.md"), warm=True)
    assert "`b.md`" in changed
    assert _decide_ui_block(u, _ctx("b.md"), warm=True) == UI_STATE_UNCHANGED


def test_cold_sends_no_prefix_and_forgets():
    u = _Unit()
    _decide_ui_block(u, _ctx(), warm=True)
    assert _decide_ui_block(u, _ctx(), warm=False) is None
    assert u._last_ui_block is None
    assert _decide_ui_block(u, _ctx(), warm=True).startswith("## Current UI State\n")


def test_going_empty_is_said_once():
    u = _Unit()
    assert _decide_ui_block(u, None, warm=True) is None  # never had anything
    _decide_ui_block(u, _ctx(), warm=True)
    assert _decide_ui_block(u, None, warm=True) == UI_STATE_NOTHING
    assert _decide_ui_block(u, None, warm=True) == UI_STATE_UNCHANGED


def test_compaction_forgets_the_last_block():
    from core.session_unit import SessionState, SessionUnit
    from core.streaming_orchestrator import StreamingOrchestrator
    unit = SessionUnit(session_id="sess-ui", agent_id="default")
    unit.state = SessionState.STREAMING
    unit._hook_session_context = {}
    _decide_ui_block(unit, _ctx(), warm=True)
    assert unit._last_ui_block
    StreamingOrchestrator(unit)._compaction_events(
        "compact_boundary", {"compact_metadata": {"trigger": "auto"}})
    assert unit._last_ui_block is None


def test_the_decided_block_is_what_gets_prefixed():
    block = _build_dynamic_prefix_block(_ctx(), None, ui_block=UI_STATE_UNCHANGED)
    assert block == UI_STATE_UNCHANGED
    q = _prepend_dynamic_context_to_query("hi", _ctx(), None, True, ui_block=UI_STATE_UNCHANGED)
    assert q == f"{UI_STATE_UNCHANGED}\n\nhi"
    # nothing decided → nothing prefixed, even with UI state present
    assert _prepend_dynamic_context_to_query("hi", _ctx(), None, True, ui_block=None) == "hi"
    # old callers (no ui_block) keep rendering it themselves
    assert "`a.md`" in _prepend_dynamic_context_to_query("hi", _ctx(), None, True)


def test_one_line_forms_are_stripped():
    for line in (UI_STATE_UNCHANGED, UI_STATE_NOTHING):
        assert strip_injected_blocks(f"{line}\n\n帮我看看") == "帮我看看"


def test_worst_case_section_fits_the_budget():
    worst = {"file_path": "p", "file_name": "p", "active_overlay": "swarm:show-todo", "canvas": {
        "open": True, "visible": "open", "tab": "changes", "list_folded": True, "pinned": True,
        "output_count": 40,
        "file": {"path": "x/" * 70 + "a.md", "name": "a" * 150, "opened_by": "user",
                 "dirty": True, "view": "preview", "review": True},
        "pending_open": "y/" * 70,
        "outputs": [{"path": "z/" * 70 + f"{i}.md", "new": True} for i in range(8)],
        "commands": [{"cmd": "open-canvas-file", "result": "ignored", "reason": "r" * 120}] * 3}}
    out = _render_ui_context_section(worst)
    assert len(out) <= UI_SECTION_BUDGET, len(out)
    assert "40 listed" in out and "more" in out
