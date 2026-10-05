"""Tests for design_lint_guard (PreToolUse Write/Edit/MultiEdit).

WHAT: verifies the advisory CSS design-anti-pattern nudge FLAGS the three A-class
rules (pure-black-text / bounce-easing / gray-on-color) in frontend style writes
while NEVER denying a write, and is correctly SCOPE-GATED (only frontend style
files under desktop/src|hive) and FAIL-OPEN (any malformed/oversized/out-of-scope
input → bare approve, never raise).

METHODOLOGY: behavior tests over the pure/async functions — one assertion per rule.
The load-bearing invariant (mutation-tested below): the guard ALWAYS returns
decision=="approve" and NEVER emits permissionDecision:deny / decision:block —
style is a nudge, not a security block (STEERING #2 / O030).

KEY PROPERTIES:
- flag: color:#000 / color:black (pure-black-text), ease-elastic / bounce /
  overshoot cubic-bezier (bounce-easing), text-gray-400 bg-{color}-N (gray-on-color).
- scope: only .tsx/.jsx/.css/.scss UNDER desktop/src or hive — backend .py, root
  markdown, repo-root .tsx are NOT scanned.
- no-false-positive: legitimate CSS (tinted near-black, in-range cubic-bezier,
  gray-on-neutral) produces zero findings.
- fail-safe: non-Write/Edit/MultiEdit tool, empty content, oversized content, or a
  malformed tool_input → approve untouched, never raise.
- mutation: deleting a rule from _DESIGN_RULES makes its flag test go red (non-vacuous).

NOTE on paths: tests use an ABSOLUTE path containing '/desktop/src/' so _in_scope's
realpath check passes regardless of the test's cwd (the file need not exist — _in_scope
is a pure path-shape check, it does not stat the file).
"""

import asyncio
import inspect

from core.design_hooks import design_lint_guard, _scan_design_rules, _in_scope, _DESIGN_RULES


# An in-scope absolute path (file need not exist — _in_scope is a path-shape check).
_IN_SCOPE = "/Users/x/proj/desktop/src/components/Card.tsx"
_HIVE = "/srv/hive/ui/Panel.css"


def guard(input_data, tool_use_id=None, context=None) -> dict:
    """Drive the hook through an event loop.

    design_lint_guard is a coroutine (the SDK ``await``s every PreToolUse hook).
    Calling it synchronously returns an un-awaited coroutine; this wrapper runs it
    the way the SDK does, so the async contract is under test.
    """
    return asyncio.run(design_lint_guard(input_data, tool_use_id, context))


def _write(content: str, path: str = _IN_SCOPE) -> dict:
    return {"tool_name": "Write", "tool_input": {"file_path": path, "content": content}}


def _edit(new_string: str, path: str = _IN_SCOPE) -> dict:
    return {"tool_name": "Edit", "tool_input": {"file_path": path, "new_string": new_string}}


def _multiedit(new_strings: list[str], path: str = _IN_SCOPE) -> dict:
    return {
        "tool_name": "MultiEdit",
        "tool_input": {"file_path": path, "edits": [{"new_string": s} for s in new_strings]},
    }


def _ctx(result: dict) -> str:
    return result.get("additionalContext", "") or ""


# ── Async / never-deny contract ─────────────────────────────────────────────

def test_hook_is_a_coroutine_function():
    # Locks the async contract: a sync def would TypeError when the SDK awaits it,
    # breaking the whole never-crash invariant (sibling guards are all async def).
    assert inspect.iscoroutinefunction(design_lint_guard)


def test_never_denies_even_when_flagging():
    # The load-bearing invariant: a flagged write is still APPROVED (advisory nudge).
    r = guard(_write("a { color: #000; }"))
    assert r["decision"] == "approve"
    assert "permissionDecision" not in r
    assert r.get("decision") != "block"


# ── Flag: the three A-class rules ───────────────────────────────────────────

def test_flag_pure_black_text_hex():
    r = guard(_write(".title { color: #000; }"))
    assert "pure-black-text" in _ctx(r)


def test_flag_pure_black_text_keyword():
    r = guard(_write(".title { color: black; }"))
    assert "pure-black-text" in _ctx(r)


def test_flag_bounce_easing_keyword():
    r = guard(_write(".x { transition: transform 200ms ease-elastic; }"))
    assert "bounce-easing" in _ctx(r)


def test_flag_bounce_easing_overshoot_cubic_bezier():
    # 4th control point y > 1 → true overshoot/spring.
    r = guard(_write(".x { animation-timing-function: cubic-bezier(0.5, 0, 0.5, 1.6); }"))
    assert "bounce-easing" in _ctx(r)


def test_flag_gray_on_color_tailwind():
    r = guard(_write('<div className="text-gray-400 bg-blue-600">hi</div>'))
    assert "gray-on-color" in _ctx(r)


def test_flag_in_edit_new_string():
    r = guard(_edit(".a { color: #000000; }"))
    assert "pure-black-text" in _ctx(r)


def test_flag_in_multiedit_any_edit():
    r = guard(_multiedit(["clean { color: #1a1a2e; }", ".bad { color: black; }"]))
    assert "pure-black-text" in _ctx(r)


def test_additional_context_names_rule_and_fix():
    r = guard(_write(".t { color: #000; }"))
    ctx = _ctx(r)
    assert "pure-black-text" in ctx          # rule id
    assert "fix:" in ctx                      # the fix hint
    assert "advisory" in ctx.lower()          # advertised as a nudge, not a block


# ── No false positives (legitimate CSS) ─────────────────────────────────────

def test_no_fp_tinted_near_black():
    r = guard(_write(".t { color: #1a1a2e; } .b { background: #000; }"))
    # tinted text color + a #000 BACKGROUND (not color:) → no pure-black-TEXT hit.
    assert _ctx(r) == ""
    assert r["decision"] == "approve"


def test_no_fp_star_color_properties():
    # Gate-2 correctness finding: an unanchored `color:` matched the TAIL of every
    # `*-color: #000` property. The negative-lookbehind must reject these — they are
    # NOT pure-black TEXT (the contract is explicit: "does not touch backgrounds").
    for prop in ("background-color", "border-color", "outline-color", "caret-color"):
        r = guard(_write(f".x {{ {prop}: #000; }}"))
        assert "pure-black-text" not in _ctx(r), f"{prop}: #000 wrongly flagged"
        r2 = guard(_write(f".x {{ {prop}: black; }}"))
        assert "pure-black-text" not in _ctx(r2), f"{prop}: black wrongly flagged"
    # but a REAL `color: #000` directly after a `background-color` line still fires
    r3 = guard(_write(".x { background-color: #fff; color: #000; }"))
    assert "pure-black-text" in _ctx(r3)


def test_no_fp_in_range_cubic_bezier():
    r = guard(_write(".x { transition-timing-function: cubic-bezier(0.4, 0, 0.2, 1); }"))
    assert "bounce-easing" not in _ctx(r)


def test_no_fp_gray_on_neutral():
    r = guard(_write('<div className="text-gray-400 bg-white">hi</div>'))
    assert "gray-on-color" not in _ctx(r)


def test_no_fp_clean_frontend_file():
    clean = '<div className="text-slate-800 bg-slate-50 rounded-lg">\n  content\n</div>'
    r = guard(_write(clean))
    assert _ctx(r) == ""


# ── Scope gate ──────────────────────────────────────────────────────────────

def test_scope_backend_py_not_scanned():
    # A .py file with 'color: #000' in a string must NOT be scanned.
    r = guard(_write('x = "color: #000"', path="/Users/x/proj/backend/core/foo.py"))
    assert _ctx(r) == ""


def test_scope_markdown_not_scanned():
    r = guard(_write("color: black", path="/Users/x/proj/Knowledge/note.md"))
    assert _ctx(r) == ""


def test_scope_root_tsx_not_scanned():
    # .tsx but NOT under desktop/src or hive → out of scope.
    r = guard(_write(".t { color: #000; }", path="/Users/x/proj/scratch/Thing.tsx"))
    assert _ctx(r) == ""


def test_scope_hive_css_is_scanned():
    r = guard(_write(".t { color: black; }", path=_HIVE))
    assert "pure-black-text" in _ctx(r)


def test_in_scope_helper():
    assert _in_scope(_IN_SCOPE) is True
    assert _in_scope(_HIVE) is True
    assert _in_scope("/Users/x/proj/backend/core/foo.py") is False
    assert _in_scope("/Users/x/proj/desktop/src/foo.ts") is False  # .ts not a style file
    assert _in_scope("") is False
    assert _in_scope("/Users/x/proj/scratch/Thing.tsx") is False   # right suffix, wrong tree


# ── Fail-safe ───────────────────────────────────────────────────────────────

def test_non_write_tool_approved_untouched():
    r = guard({"tool_name": "Read", "tool_input": {"file_path": _IN_SCOPE}})
    assert r == {"decision": "approve"}


def test_empty_content_approved():
    r = guard(_write("", path=_IN_SCOPE))
    assert r == {"decision": "approve"}


def test_oversized_content_approved():
    from core.design_hooks import _DESIGN_SCAN_MAX_CHARS
    big = "color: #000;" + ("x" * (_DESIGN_SCAN_MAX_CHARS + 1))
    r = guard(_write(big, path=_IN_SCOPE))
    assert r == {"decision": "approve"}  # skipped the scan, no additionalContext


def test_missing_tool_input_approved():
    r = guard({"tool_name": "Write"})
    assert r["decision"] == "approve"


def test_malformed_tool_input_does_not_raise():
    r = guard({"tool_name": "Write", "tool_input": "not-a-dict"})
    assert r["decision"] == "approve"


def test_malformed_multiedit_does_not_raise():
    r = guard({"tool_name": "MultiEdit", "tool_input": {"file_path": _IN_SCOPE, "edits": "nope"}})
    assert r["decision"] == "approve"


def test_missing_file_path_out_of_scope():
    # No file_path → _in_scope False → bare approve.
    r = guard({"tool_name": "Write", "tool_input": {"content": ".t { color: #000; }"}})
    assert _ctx(r) == ""


# ── Mutation proof (non-vacuous) ────────────────────────────────────────────

def test_scanner_is_nonvacuous_mutation_guard():
    """Proves the flag tests are non-vacuous: if a rule is removed from _DESIGN_RULES,
    its pattern no longer matches. We don't mutate the module here (that would be a
    global side effect); instead we assert each shipped rule id is actually REACHED by
    _scan_design_rules for a known-bad input — so deleting it from _DESIGN_RULES would
    drop the id from the scan result and fail the corresponding flag test above.
    """
    assert _scan_design_rules("color: #000") and _scan_design_rules("color: #000")[0][0] == "pure-black-text"
    assert any(h[0] == "bounce-easing" for h in _scan_design_rules("ease-elastic"))
    assert any(h[0] == "gray-on-color" for h in _scan_design_rules('text-gray-400 bg-blue-600'))
    # every shipped rule id is distinct and reachable
    ids = [r[0] for r in _DESIGN_RULES]
    assert ids == ["pure-black-text", "bounce-easing", "gray-on-color"]
