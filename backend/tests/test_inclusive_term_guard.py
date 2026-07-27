"""Tests for inclusive_term_guard (PreToolUse Write/Edit/MultiEdit).

WHAT: verifies the non-inclusive-terminology WARN guard flags the six terms
(master/slave/whitelist/blacklist/whiteday/blackday) in written content while
NEVER denying a write, and honors the three false-positive exemptions.

METHODOLOGY: behavior tests over the pure function — one assertion per rule.
The load-bearing invariant (mutation-tested): the guard ALWAYS returns
decision=="approve" and NEVER emits permissionDecision:deny / decision:block —
wording is a nudge, not a security block (STEERING #2).

KEY PROPERTIES:
- flag: bare whitelist / blacklist / whiteday / blackday, master↔slave adjacency,
  standalone slave; case-insensitive; embedded (_WHITELIST, getWhitelist).
- exempt: whitelist_categories/_characters (hypothesis API), paired
  whitelist+blacklist (technical-contrast), the word "inclusive", bare "master".
- fail-safe: non-Write/Edit/MultiEdit tool, empty content, oversized content,
  or a malformed tool_input → approve untouched, never raise.
"""

import asyncio
import inspect

from backend.core.security_hooks import inclusive_term_guard


def guard(input_data, tool_use_id, context) -> dict:
    """Drive the hook through an event loop.

    inclusive_term_guard is a coroutine (the SDK ``await``s every PreToolUse hook —
    query.py:446). Calling it synchronously returns an un-awaited coroutine, which
    is exactly the bug Gate-2 caught (C1): a sync def would TypeError under await.
    This wrapper runs it the way the SDK does, so the async contract is under test.
    """
    return asyncio.run(inclusive_term_guard(input_data, tool_use_id, context))


def test_hook_is_a_coroutine_function():
    # Locks the async contract (Gate-2 C1: a sync def is awaited → TypeError →
    # the whole never-crash invariant breaks; sibling guards are all async def).
    assert inspect.iscoroutinefunction(inclusive_term_guard)


def _write(content: str, path: str = "foo.py") -> dict:
    return {"tool_name": "Write", "tool_input": {"file_path": path, "content": content}}


def _edit(new_string: str, path: str = "foo.py") -> dict:
    return {"tool_name": "Edit", "tool_input": {"file_path": path, "new_string": new_string}}


def _multiedit(new_strings: list[str], path: str = "foo.py") -> dict:
    return {
        "tool_name": "MultiEdit",
        "tool_input": {"file_path": path, "edits": [{"new_string": s} for s in new_strings]},
    }


def _findings(result: dict) -> str:
    """The additionalContext text (empty string if none)."""
    return result.get("additionalContext", "") or ""


# ── The load-bearing invariant: NEVER deny ──────────────────────────────────

def test_never_denies_even_when_flagging():
    r = guard(_write("use a whitelist here"), None, None)
    assert r["decision"] == "approve"
    hso = r.get("hookSpecificOutput") or {}
    assert hso.get("permissionDecision") != "deny"
    assert r.get("decision") != "block"


def test_flag_emits_additional_context_naming_term_and_alternative():
    r = guard(_write("the whitelist of allowed tools"), None, None)
    ctx = _findings(r).lower()
    assert "whitelist" in ctx
    assert "allowlist" in ctx  # names the alternative, not just "bad word"


# ── Flag cases ───────────────────────────────────────────────────────────────

def test_flag_bare_whitelist():
    assert "whitelist" in _findings(guard(_write("a whitelist"), None, None)).lower()


def test_flag_whitelisted_the_real_cr_term():
    # CR-291472994 used "whitelisted" (a suffix form), not bare "whitelist".
    assert _findings(guard(_write("the whitelisted section"), None, None))


def test_flag_case_insensitive_constant():
    assert _findings(guard(_write("_WHITELIST = []"), None, None))


def test_flag_camelcase_embedded():
    assert _findings(guard(_write("getWhitelist()"), None, None))


def test_flag_master_slave_adjacency():
    assert _findings(guard(_write("master/slave replication"), None, None))


def test_flag_standalone_slave():
    assert _findings(guard(_write("the slave node"), None, None))


def test_flag_whiteday_blackday():
    assert _findings(guard(_write("whiteday and blackday flags"), None, None))


def test_flag_in_edit_new_string():
    assert _findings(guard(_edit("add to blacklist"), None, None))


def test_flag_in_multiedit_any_edit():
    # Skeptic-caught bypass: a flagged term in ANY edits[].new_string must be seen.
    r = guard(_multiedit(["clean line", "a whitelist here"]), None, None)
    assert _findings(r)


# ── Exemptions (false-positive suppression) ──────────────────────────────────

def test_exempt_hypothesis_whitelist_categories():
    r = guard(_write("st.characters(whitelist_categories=('L','N'))"), None, None)
    assert not _findings(r)


def test_exempt_hypothesis_whitelist_characters():
    r = guard(_write("whitelist_characters=' -_'"), None, None)
    assert not _findings(r)


def test_exempt_paired_whitelist_blacklist():
    # Technical-contrast: describing a whitelist-vs-blacklist design decision.
    r = guard(
        _write("switched from an implicit whitelist to a blacklist model"), None, None
    )
    assert not _findings(r)


def test_inclusive_word_never_flagged():
    r = guard(_write("the range is inclusive of both endpoints"), None, None)
    assert not _findings(r)


def test_bare_master_not_flagged():
    # git master / master copy / DB primary — bare master alone is not host-role sense.
    r = guard(_write("git checkout master  # the master copy"), None, None)
    assert not _findings(r)


def test_enslave_not_flagged_as_slave():
    # Gate-2 M1: \bslave\w* must not match inside 'enslave'/'enslaved' (common English).
    r = guard(_write("the enslaved population was enslaved for centuries"), None, None)
    assert not _findings(r)


# ── Fail-safe ────────────────────────────────────────────────────────────────

def test_non_write_tool_approved_untouched():
    r = guard({"tool_name": "Bash", "tool_input": {"command": "grep whitelist x"}}, None, None)
    assert r == {"decision": "approve"}


def test_empty_content_approved():
    assert guard(_write(""), None, None) == {"decision": "approve"}


def test_missing_tool_input_approved():
    assert guard({"tool_name": "Write"}, None, None) == {"decision": "approve"}


def test_malformed_multiedit_does_not_raise():
    # edits is not a list / entries not dicts → must not crash the write path.
    bad = {"tool_name": "MultiEdit", "tool_input": {"edits": "notalist"}}
    assert guard(bad, None, None)["decision"] == "approve"
    bad2 = {"tool_name": "MultiEdit", "tool_input": {"edits": [None, 5, {"new_string": "whitelist"}]}}
    # still scans the valid entry, still approves
    assert guard(bad2, None, None)["decision"] == "approve"


def test_non_string_content_does_not_raise():
    r = guard({"tool_name": "Write", "tool_input": {"content": 12345}}, None, None)
    assert r["decision"] == "approve"


def test_oversized_content_approved_without_scan():
    huge = "whitelist " * 200_000  # ~2MB, above the scan cap
    r = guard(_write(huge), None, None)
    assert r == {"decision": "approve"}
