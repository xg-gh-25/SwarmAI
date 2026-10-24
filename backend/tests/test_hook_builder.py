"""Tests for hook_builder.py HookRegistry pattern.

Verifies:
- Registry pattern: register hooks by event, chain execution, 5s timeout
- Backward compatibility: existing hooks wire correctly through registry
- Hook chaining: multiple hooks per event, first 'block' wins
"""
import asyncio
import pytest
from unittest.mock import MagicMock


# ---------------------------------------------------------------------------
# Tracer bullet: HookRegistry chains two hooks for the same event
# ---------------------------------------------------------------------------

class TestHookRegistry:
    """HookRegistry core functionality."""

    def test_register_and_build_single_hook(self):
        """Registry with one hook per event produces correct SDK dict."""
        from core.hook_builder import HookRegistry

        async def my_hook(input_data, tool_use_id, context):
            return {"additionalContext": "test"}

        registry = HookRegistry()
        registry.register("PostToolUse", my_hook, "test_hook")
        sdk_hooks = registry.build_sdk_hooks()

        assert "PostToolUse" in sdk_hooks
        # Should be a list of HookMatcher
        assert len(sdk_hooks["PostToolUse"]) == 1

    def test_register_multiple_hooks_same_event(self):
        """Two hooks on same event both get registered."""
        from core.hook_builder import HookRegistry

        async def hook_a(input_data, tool_use_id, context):
            return {}

        async def hook_b(input_data, tool_use_id, context):
            return {"additionalContext": "from b"}

        registry = HookRegistry()
        registry.register("PostToolUseFailure", hook_a, "hook_a")
        registry.register("PostToolUseFailure", hook_b, "hook_b")
        sdk_hooks = registry.build_sdk_hooks()

        assert "PostToolUseFailure" in sdk_hooks
        # Should have a single HookMatcher with a chained hook
        assert len(sdk_hooks["PostToolUseFailure"]) == 1

    @pytest.mark.asyncio
    async def test_chained_hooks_execute_sequentially(self):
        """Chained hooks run in registration order, results merged."""
        from core.hook_builder import HookRegistry

        call_order = []

        async def hook_a(input_data, tool_use_id, context):
            call_order.append("a")
            return {"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": "from_a"}}

        async def hook_b(input_data, tool_use_id, context):
            call_order.append("b")
            return {"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": "from_b"}}

        registry = HookRegistry()
        registry.register("PostToolUse", hook_a, "a")
        registry.register("PostToolUse", hook_b, "b")
        sdk_hooks = registry.build_sdk_hooks()

        # Extract the chained hook function from the HookMatcher
        chained = sdk_hooks["PostToolUse"][0].hooks[0]
        result = await chained({}, None, MagicMock())

        assert call_order == ["a", "b"]
        # Both hooks' hookSpecificOutput.additionalContext reach the model, in order
        # (the CLI reads only this field on PostToolUse; a later hook used to overwrite it)
        hso = result.get("hookSpecificOutput", {})
        assert hso["additionalContext"] == "from_a\nfrom_b"
        assert hso["hookEventName"] == "PostToolUse"

    @pytest.mark.asyncio
    async def test_toplevel_additional_context_merged(self):
        """Top-level additionalContext from an advisory hook must survive the
        chain (regression: it was silently dropped — governance_file_gate's
        advisory reminders never reached the agent. adversarial run_123a6530)."""
        from core.hook_builder import HookRegistry

        async def advisory(input_data, tool_use_id, context):
            return {"decision": "approve", "additionalContext": "REMEMBER THIS"}

        async def plain(input_data, tool_use_id, context):
            return {"decision": "approve"}

        registry = HookRegistry()
        registry.register("PreToolUse", advisory, "advisory")
        registry.register("PreToolUse", plain, "plain")
        chained = registry.build_sdk_hooks()["PreToolUse"][0].hooks[0]
        result = await chained({"tool_name": "Bash"}, None, MagicMock())

        assert result.get("additionalContext") == "REMEMBER THIS"

    @pytest.mark.asyncio
    async def test_multiple_advisory_contexts_accumulate(self):
        """Two advisory hooks each contribute their context (accumulated)."""
        from core.hook_builder import HookRegistry

        async def a(input_data, tool_use_id, context):
            return {"decision": "approve", "additionalContext": "ctx_a"}

        async def b(input_data, tool_use_id, context):
            return {"decision": "approve", "additionalContext": "ctx_b"}

        registry = HookRegistry()
        registry.register("PreToolUse", a, "a")
        registry.register("PreToolUse", b, "b")
        chained = registry.build_sdk_hooks()["PreToolUse"][0].hooks[0]
        result = await chained({"tool_name": "Bash"}, None, MagicMock())

        assert "ctx_a" in result["additionalContext"]
        assert "ctx_b" in result["additionalContext"]

    @pytest.mark.asyncio
    async def test_block_decision_short_circuits(self):
        """First hook returning 'block' stops chain execution."""
        from core.hook_builder import HookRegistry

        call_order = []

        async def blocker(input_data, tool_use_id, context):
            call_order.append("blocker")
            return {"decision": "block", "reason": "dangerous"}

        async def never_reached(input_data, tool_use_id, context):
            call_order.append("never")
            return {}

        registry = HookRegistry()
        registry.register("PreToolUse", blocker, "blocker")
        registry.register("PreToolUse", never_reached, "never")
        sdk_hooks = registry.build_sdk_hooks()

        chained = sdk_hooks["PreToolUse"][0].hooks[0]
        result = await chained({}, None, MagicMock())

        assert call_order == ["blocker"]
        assert result["decision"] == "block"

    @pytest.mark.asyncio
    async def test_deny_short_circuits_chain(self):
        """A hook returning permissionDecision==deny stops the chain (run_7da67105).

        Mirrors the block short-circuit: deny is terminal. Without this, a later
        hook in the chain keeps running and can clobber the deny.
        """
        from core.hook_builder import HookRegistry

        call_order = []

        async def denier(input_data, tool_use_id, context):
            call_order.append("denier")
            return {"hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": "blocked by guard",
            }}

        async def never_reached(input_data, tool_use_id, context):
            call_order.append("never")
            return {"hookSpecificOutput": {
                "hookEventName": "PreToolUse", "permissionDecision": "allow",
            }}

        registry = HookRegistry()
        registry.register("PreToolUse", denier, "denier")
        registry.register("PreToolUse", never_reached, "never")
        chained = registry.build_sdk_hooks()["PreToolUse"][0].hooks[0]
        result = await chained({"tool_name": "Bash"}, None, MagicMock())

        assert call_order == ["denier"]  # later hook never ran
        assert result["hookSpecificOutput"]["permissionDecision"] == "deny"

    @pytest.mark.asyncio
    async def test_later_allow_cannot_clobber_earlier_deny(self):
        """The CRITICAL bug: a deny must survive a later allow-emitting hook."""
        from core.hook_builder import HookRegistry

        async def denier(input_data, tool_use_id, context):
            return {"hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": "guard says no",
            }}

        async def allower(input_data, tool_use_id, context):
            return {"hookSpecificOutput": {
                "hookEventName": "PreToolUse", "permissionDecision": "allow",
            }}

        registry = HookRegistry()
        registry.register("PreToolUse", denier, "denier")
        registry.register("PreToolUse", allower, "allower")
        chained = registry.build_sdk_hooks()["PreToolUse"][0].hooks[0]
        result = await chained({"tool_name": "Bash"}, None, MagicMock())

        # The deny must NOT be overwritten by the later allow.
        assert result["hookSpecificOutput"]["permissionDecision"] == "deny"

    @pytest.mark.asyncio
    async def test_allow_payloads_still_merge_when_no_deny(self):
        """AC3: allow-emitting hooks (carrying additionalContext / updatedInput)
        must still merge normally when NO deny precedes them — the deny
        short-circuit must not break the legitimate allow path."""
        from core.hook_builder import HookRegistry

        async def warner(input_data, tool_use_id, context):
            return {"hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "allow",
                "additionalContext": "hint-A",
            }}

        async def answerer(input_data, tool_use_id, context):
            return {"hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "allow",
                "updatedInput": {"answered": True},
            }}

        registry = HookRegistry()
        registry.register("PreToolUse", warner, "warner")
        registry.register("PreToolUse", answerer, "answerer")
        chained = registry.build_sdk_hooks()["PreToolUse"][0].hooks[0]
        result = await chained({"tool_name": "AskUserQuestion"}, None, MagicMock())

        hso = result["hookSpecificOutput"]
        # Both allow-hooks ran and merged (no short-circuit on allow).
        assert hso["permissionDecision"] == "allow"
        assert hso.get("updatedInput") == {"answered": True}
        assert hso.get("additionalContext") == "hint-A"

    @pytest.mark.asyncio
    async def test_ask_and_defer_also_short_circuit(self):
        """Gate-2 hardening (run_7da67105): ask/defer are terminal too — a later
        allow must not clobber them (same class as the deny clobber). No hook
        emits these today; the guard closes the gap pre-emptively."""
        from core.hook_builder import HookRegistry

        for terminal in ("ask", "defer"):
            async def asker(input_data, tool_use_id, context, _t=terminal):
                return {"hookSpecificOutput": {
                    "hookEventName": "PreToolUse", "permissionDecision": _t,
                }}

            async def allower(input_data, tool_use_id, context):
                return {"hookSpecificOutput": {
                    "hookEventName": "PreToolUse", "permissionDecision": "allow",
                }}

            registry = HookRegistry()
            registry.register("PreToolUse", asker, "asker")
            registry.register("PreToolUse", allower, "allower")
            chained = registry.build_sdk_hooks()["PreToolUse"][0].hooks[0]
            result = await chained({"tool_name": "Bash"}, None, MagicMock())
            assert result["hookSpecificOutput"]["permissionDecision"] == terminal

    @pytest.mark.asyncio
    async def test_hook_timeout_5s(self):
        """Hooks that exceed 5s are killed, chain continues."""
        from core.hook_builder import HookRegistry

        async def slow_hook(input_data, tool_use_id, context):
            await asyncio.sleep(10)
            return {"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": "should not appear"}}

        async def fast_hook(input_data, tool_use_id, context):
            return {"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": "fast"}}

        registry = HookRegistry()
        registry.register("PostToolUse", slow_hook, "slow")
        registry.register("PostToolUse", fast_hook, "fast")
        sdk_hooks = registry.build_sdk_hooks()

        chained = sdk_hooks["PostToolUse"][0].hooks[0]
        result = await asyncio.wait_for(
            chained({}, None, MagicMock()),
            timeout=8.0  # generous outer timeout
        )
        # fast_hook should have run despite slow_hook timeout
        assert result.get("hookSpecificOutput", {}).get("additionalContext") == "fast"

    def test_register_with_matcher(self):
        """Registry supports matcher string (e.g., 'Bash' for PreToolUse)."""
        from core.hook_builder import HookRegistry

        async def bash_hook(input_data, tool_use_id, context):
            return {}

        registry = HookRegistry()
        registry.register("PreToolUse", bash_hook, "bash_gate", matcher="Bash")
        sdk_hooks = registry.build_sdk_hooks()

        assert "PreToolUse" in sdk_hooks
        # HookMatcher should have matcher="Bash"
        hm = sdk_hooks["PreToolUse"][0]
        assert hm.matcher == "Bash"

    # ── no_timeout exemption (run_6e780e00 — approve-into-void root cause) ──

    @pytest.mark.asyncio
    async def test_no_timeout_hook_survives_past_5s(self):
        """AC1: a no_timeout=True hook in a CHAIN is NOT cancelled at 5s.

        Reproduces the fix: dangerous_command_gate blocks >5s awaiting the user;
        with the exemption it must complete, not get guillotined at HOOK_TIMEOUT_SECONDS.
        Sleeps 5.4s (just past the 5.0s threshold) — bounded, proves survival."""
        from core.hook_builder import HookRegistry

        async def slow_approval(input_data, tool_use_id, context):
            await asyncio.sleep(5.4)  # > HOOK_TIMEOUT_SECONDS (5.0)
            return {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                    "permissionDecision": "deny", "permissionDecisionReason": "survived"}}

        async def fast_guard(input_data, tool_use_id, context):
            return {}

        registry = HookRegistry()
        # 2 hooks on the SAME slot → chained (this is the Bash-slot condition).
        registry.register("PreToolUse", fast_guard, "fast_guard", matcher="Bash")
        registry.register("PreToolUse", slow_approval, "slow_approval",
                          matcher="Bash", no_timeout=True)
        chained = registry.build_sdk_hooks()["PreToolUse"][0].hooks[0]

        result = await asyncio.wait_for(chained({}, None, MagicMock()), timeout=8.0)
        # The exempt slow hook COMPLETED (its deny survived) — not cancelled at 5s.
        assert result.get("hookSpecificOutput", {}).get("permissionDecisionReason") == "survived", \
            "no_timeout hook must survive past the 5s chain timeout"

    @pytest.mark.asyncio
    async def test_non_exempt_slow_hook_still_cancelled_at_5s(self):
        """AC2: a NON-exempt slow hook sharing the chain IS still cancelled at 5s.

        The exemption must NOT weaken the 5s guard for the fast guards that need it."""
        from core.hook_builder import HookRegistry

        async def slow_guard(input_data, tool_use_id, context):
            await asyncio.sleep(10)
            return {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                    "permissionDecisionReason": "should-not-appear"}}

        async def fast_guard(input_data, tool_use_id, context):
            return {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                    "permissionDecisionReason": "fast-ran"}}

        registry = HookRegistry()
        registry.register("PreToolUse", slow_guard, "slow_guard", matcher="Bash")  # no_timeout defaults False
        registry.register("PreToolUse", fast_guard, "fast_guard", matcher="Bash")
        chained = registry.build_sdk_hooks()["PreToolUse"][0].hooks[0]

        result = await asyncio.wait_for(chained({}, None, MagicMock()), timeout=8.0)
        # slow_guard was cancelled at 5s (its output absent), fast_guard still ran.
        assert result.get("hookSpecificOutput", {}).get("permissionDecisionReason") == "fast-ran", \
            "non-exempt slow hook must still be cancelled at 5s (fast hook's result survives)"

    @pytest.mark.asyncio
    async def test_no_timeout_preserves_deny_short_circuit(self):
        """AC3: exemption does not regress the terminal permissionDecision
        short-circuit (dc9fba77) — a deny from an exempt hook still short-circuits."""
        from core.hook_builder import HookRegistry
        ran_after = {"v": False}

        async def exempt_deny(input_data, tool_use_id, context):
            return {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                    "permissionDecision": "deny", "permissionDecisionReason": "blocked"}}

        async def later_hook(input_data, tool_use_id, context):
            ran_after["v"] = True
            return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow"}}

        registry = HookRegistry()
        registry.register("PreToolUse", exempt_deny, "exempt_deny", matcher="Bash", no_timeout=True)
        registry.register("PreToolUse", later_hook, "later_hook", matcher="Bash")
        chained = registry.build_sdk_hooks()["PreToolUse"][0].hooks[0]

        result = await chained({}, None, MagicMock())
        assert result.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"
        assert ran_after["v"] is False, "deny must short-circuit — later hook must NOT run (dc9fba77)"


# ---------------------------------------------------------------------------
# HITL matcher timeout — align CLI hook-cancel with our 4h HITL window
# (run_1141ea02). The CLI cancels a blocking PreToolUse hook at its ~600s
# default unless we forward a per-matcher `timeout`; SDK HookMatcher.timeout
# is that field. A (event,matcher) group containing ANY no_timeout hook must
# carry timeout=PERMISSION_ANSWER_TIMEOUT_SECONDS; a fast-guard-only group
# must NOT (keep the CLI default).
# ---------------------------------------------------------------------------

class TestHITLMatcherTimeout:
    """AC1/AC2: no_timeout-containing matcher groups get the 4h CLI timeout."""

    def test_no_timeout_group_gets_hitl_timeout_solo(self):
        """AC1: a SOLO no_timeout hook's HookMatcher carries the 4h timeout."""
        from core.hook_builder import HookRegistry
        from core.permission_manager import PERMISSION_ANSWER_TIMEOUT_SECONDS

        async def hitl_gate(input_data, tool_use_id, context):
            return {}

        registry = HookRegistry()
        registry.register("PreToolUse", hitl_gate, "ask_gate",
                          matcher="AskUserQuestion", no_timeout=True)
        hm = registry.build_sdk_hooks()["PreToolUse"][0]
        assert hm.timeout == PERMISSION_ANSWER_TIMEOUT_SECONDS, (
            "solo no_timeout matcher must carry the 4h CLI timeout")

    def test_no_timeout_group_gets_hitl_timeout_chained(self):
        """AC2: a CHAINED group (fast guards + one no_timeout gate) carries 4h."""
        from core.hook_builder import HookRegistry
        from core.permission_manager import PERMISSION_ANSWER_TIMEOUT_SECONDS

        async def fast_guard(input_data, tool_use_id, context):
            return {}

        async def danger_gate(input_data, tool_use_id, context):
            return {}

        registry = HookRegistry()
        registry.register("PreToolUse", fast_guard, "bash_syntax", matcher="Bash")
        registry.register("PreToolUse", danger_gate, "dangerous_command_gate",
                          matcher="Bash", no_timeout=True)
        hm = registry.build_sdk_hooks()["PreToolUse"][0]
        assert hm.timeout == PERMISSION_ANSWER_TIMEOUT_SECONDS, (
            "chained group with a no_timeout gate must carry the 4h CLI timeout")

    def test_fast_only_group_has_no_hitl_timeout(self):
        """AC1: a group with ONLY fast guards must NOT get the 4h timeout
        (keep the CLI default — a hung fast guard must not sit for 4h)."""
        from core.hook_builder import HookRegistry

        async def fast_a(input_data, tool_use_id, context):
            return {}

        async def fast_b(input_data, tool_use_id, context):
            return {}

        registry = HookRegistry()
        registry.register("PreToolUse", fast_a, "pytest_guard", matcher="Bash")
        registry.register("PreToolUse", fast_b, "eval_guard", matcher="Bash")
        hm = registry.build_sdk_hooks()["PreToolUse"][0]
        assert hm.timeout is None, (
            "fast-guard-only matcher must keep the CLI default (timeout=None)")

    def test_real_build_hooks_bash_and_ask_carry_timeout(self):
        """AC2 (integration): the REAL build_hooks() output gives the Bash and
        AskUserQuestion matchers the 4h timeout, and no other matcher does."""
        from core.hook_builder import build_hooks
        from core.permission_manager import (
            permission_manager, PERMISSION_ANSWER_TIMEOUT_SECONDS,
        )

        # Use asyncio.run (not get_event_loop().run_until_complete): the latter
        # raises "There is no current event loop" on Py3.12+ in a thread with no
        # running loop (CI's non-DB isolated run had no pytest-asyncio loop
        # installed → green locally, red on CI). asyncio.run creates + closes its
        # own loop, so it works regardless of the ambient loop state.
        hooks, _skills, _allow_all = asyncio.run(
            build_hooks(
                agent_config={"id": "default"},
                enable_skills=False,
                enable_mcp=False,
                resume_session_id="test-sess",
                session_context={"sdk_session_id": "test-sess"},
                permission_manager=permission_manager,
            )
        )
        pre = hooks.get("PreToolUse", [])
        by_matcher = {hm.matcher: hm for hm in pre}
        assert by_matcher["Bash"].timeout == PERMISSION_ANSWER_TIMEOUT_SECONDS
        assert by_matcher["AskUserQuestion"].timeout == PERMISSION_ANSWER_TIMEOUT_SECONDS
        # A fast-only matcher (e.g. Read image-dedup) must NOT carry the 4h timeout
        if "Read" in by_matcher:
            assert by_matcher["Read"].timeout is None


class TestCodeIntelHookNotRegistered:
    """Regression: the code_intel PreToolUse hook was REMOVED from the hook chain
    (its per-Read/Grep synchronous SQLite blast_radius query occupied the default
    thread pool). build_hooks() must NOT register it.

    Behavioral assertion (Gate-1): build_sdk_hooks() strips the registration NAME,
    so a string search for 'code_intel_context' in the output is vacuous (never
    RED). Instead we assert build_hooks() never CONSTRUCTS the hook — patch
    create_code_intel_hook and assert it is not called. Reverting the removal makes
    build_hooks call it → this test goes RED.
    """

    def test_build_hooks_does_not_register_code_intel(self):
        from unittest.mock import patch
        from core.hook_builder import build_hooks
        from core.permission_manager import permission_manager

        # If build_hooks still registers code_intel, it calls create_code_intel_hook()
        # (hook_builder.py old line 583). Patch it so any call is observable.
        with patch(
            "core.code_intel.code_intel_hook.create_code_intel_hook"
        ) as mock_factory:
            asyncio.run(
                build_hooks(
                    agent_config={"id": "default", "code_intel_enabled": True},
                    enable_skills=False,
                    enable_mcp=False,
                    resume_session_id="test-sess",
                    session_context={"sdk_session_id": "test-sess"},
                    permission_manager=permission_manager,
                )
            )
        assert not mock_factory.called, (
            "code_intel hook must NOT be registered — build_hooks called "
            "create_code_intel_hook(), meaning the removed registration block "
            "was reintroduced (see hook_builder.py)"
        )


# ---------------------------------------------------------------------------
# Phase-1 EQUIVALENCE (back-to-back): the Verdict-based _build_chain must
# produce byte-identical SDK output to the legacy hand-merge, for every
# disposition and mixed ordering. This is the Gate-1 release condition —
# the frozen legacy reference below is the oracle. If this ever diverges,
# the refactor changed an observable security decision (regression).
# ---------------------------------------------------------------------------

async def _legacy_chain(hooks_results, input_data=None):
    """Frozen snapshot of the PRE-refactor _build_chain merge logic (the oracle).

    Takes a list of dicts (what each hook would return) and reproduces the exact
    legacy merge: block short-circuit, terminal permissionDecision short-circuit,
    hso later-override, decision/reason/systemMessage last-wins, additionalContext
    accumulate. Deliberately a verbatim copy of the old code so the test proves
    the NEW code matches the OLD behavior, not that both match a shared helper.
    """
    combined = {}
    for result in hooks_results:
        if not result:
            continue
        if result.get("decision") == "block":
            return result
        hso = result.get("hookSpecificOutput")
        if isinstance(hso, dict) and hso.get("permissionDecision") in ("deny", "ask", "defer"):
            return result
        if "hookSpecificOutput" in result:
            combined.setdefault("hookSpecificOutput", {})
            combined["hookSpecificOutput"].update(result["hookSpecificOutput"])
        for key in ("decision", "reason", "systemMessage"):
            if key in result and result[key] is not None:
                combined[key] = result[key]
        if result.get("additionalContext"):
            prior = combined.get("additionalContext", "")
            combined["additionalContext"] = (
                (prior + "\n" + result["additionalContext"]).strip()
                if prior else result["additionalContext"]
            )
    return combined


# Each case: a list of dicts the chained hooks return, in order. Covers every
# disposition (allow/warn/hitl/deny) via its real SDK carrier + mixed orderings,
# including the clobber case (deny then allow) and alt carriers (behavior / block).
_EQUIV_CASES = [
    # name, [hook return dicts]
    ("empty", []),
    ("single_allow", [{"decision": "approve"}]),
    ("single_block", [{"decision": "block", "reason": "dangerous"}]),
    ("single_advisory", [{"decision": "approve", "additionalContext": "ctx"}]),
    ("two_advisory_accumulate", [
        {"decision": "approve", "additionalContext": "ctx_a"},
        {"decision": "approve", "additionalContext": "ctx_b"},
    ]),
    ("deny_short_circuits", [
        {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                "permissionDecisionReason": "guard"}},
        {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow"}},
    ]),
    ("block_short_circuits", [
        {"decision": "block", "reason": "no"},
        {"decision": "approve", "additionalContext": "unreached"},
    ]),
    ("ask_short_circuits", [
        {"hookSpecificOutput": {"permissionDecision": "ask"}},
        {"hookSpecificOutput": {"permissionDecision": "allow"}},
    ]),
    ("allow_payloads_merge", [
        {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow",
                                "additionalContext": "hint-A"}},
        {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow",
                                "updatedInput": {"answered": True}}},
    ]),
    ("advisory_then_allow_hso", [
        {"decision": "approve", "additionalContext": "top-level"},
        {"hookSpecificOutput": {"permissionDecision": "allow", "additionalContext": "hso-level"}},
    ]),
    ("empty_then_allow", [{}, {"decision": "approve"}]),
    # Gate-2 adversarial finding: {"behavior":"deny"} must NOT short-circuit — the
    # legacy chain never inspected `behavior`, so it falls through to merge (no-op)
    # and the trailing hook still runs. Locks the zero-change boundary.
    ("behavior_deny_is_not_terminal", [
        {"behavior": "deny", "message": "x"},
        {"decision": "approve", "additionalContext": "still-runs"},
    ]),
    ("deny_wins_over_earlier_allow_payload", [
        {"hookSpecificOutput": {"permissionDecision": "allow", "additionalContext": "pre"}},
        {"hookSpecificOutput": {"permissionDecision": "deny", "permissionDecisionReason": "x"}},
    ]),
]


class TestPhase1Equivalence:
    """New Verdict-based _build_chain == frozen legacy merge, byte-for-byte."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("name,returns", _EQUIV_CASES, ids=[c[0] for c in _EQUIV_CASES])
    async def test_new_chain_equals_legacy(self, name, returns):
        from core.hook_builder import HookRegistry

        # Build a registry whose N hooks return the scripted dicts in order.
        registry = HookRegistry()
        for i, r in enumerate(returns):
            async def h(input_data, tool_use_id, context, _r=r):
                return _r
            registry.register("PreToolUse", h, f"h{i}")
        # A chain needs >=2 hooks (solo bypasses _build_chain); pad with a no-op
        # trailing allow for single/empty cases so they still exercise the chain.
        if len(returns) < 2:
            async def noop(input_data, tool_use_id, context):
                return {}
            registry.register("PreToolUse", noop, "noop_pad")

        chained = registry.build_sdk_hooks()["PreToolUse"][0].hooks[0]
        new_result = await chained({"tool_name": "Bash"}, None, MagicMock())

        # Oracle gets the SAME returns + the same pad, in the same order.
        oracle_returns = list(returns) + ([{}] if len(returns) < 2 else [])
        legacy_result = await _legacy_chain(oracle_returns)

        assert new_result == legacy_result, (
            f"[{name}] Verdict chain diverged from legacy:\n"
            f"  new   ={new_result!r}\n  legacy={legacy_result!r}"
        )


# ---------------------------------------------------------------------------
# Phase-1 LAYER-4 cross-boundary E2E: drive the REAL seam — a real advisory
# gate + a real deny-shaped hook through the REAL _build_chain → assert the
# dict handed to the SDK is correct. This exercises the gate-dict ↔ SDK hook
# wire contract end-to-end (not a mock). Mutation: break the terminal
# short-circuit → the deny leaks an allow-clobber → assertion goes RED
# (proven separately in the equivalence suite's mutation run).
# ---------------------------------------------------------------------------

class TestLayer4RealSeam:
    """Real gate(s) → real _build_chain → real SDK dict (the wire contract)."""

    @pytest.mark.asyncio
    async def test_real_advisory_plus_deny_through_chain(self):
        """A real-shaped advisory (approve+additionalContext) followed by a
        real-shaped deny: SDK must see the DENY verbatim (reason preserved), and
        the advisory must NOT have clobbered it. Drives the actual chain the CLI
        consumes via build_sdk_hooks()."""
        from core.hook_builder import HookRegistry

        async def real_governance_advisory(input_data, tool_use_id, context):
            # mirrors create_governance_file_gate's advisory carrier
            return {"decision": "approve",
                    "additionalContext": "⚠️ GOVERNANCE GATE [Tier 1]: classify first"}

        async def real_deny(input_data, tool_use_id, context):
            # mirrors dangerous_command_gate / adversarial_commit_gate deny carrier
            return {"hookSpecificOutput": {
                "hookEventName": "PreToolUse", "permissionDecision": "deny",
                "permissionDecisionReason": "Matches dangerous command pattern"}}

        registry = HookRegistry()
        registry.register("PreToolUse", real_governance_advisory, "gov")
        registry.register("PreToolUse", real_deny, "deny")
        chained = registry.build_sdk_hooks()["PreToolUse"][0].hooks[0]
        sdk_dict = await chained({"tool_name": "Bash"}, None, MagicMock())

        # The wire contract the CLI reads: a terminal deny, with its reason intact.
        assert sdk_dict["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert sdk_dict["hookSpecificOutput"]["permissionDecisionReason"] == \
            "Matches dangerous command pattern"

    @pytest.mark.asyncio
    async def test_real_two_advisories_accumulate_through_chain(self):
        """Two real advisory gates (governance + inclusive-style) → SDK sees BOTH
        reminders accumulated (non-terminal merge path, the wire contract for the
        Write|Edit|MultiEdit group)."""
        from core.hook_builder import HookRegistry

        async def gov(input_data, tool_use_id, context):
            return {"decision": "approve", "additionalContext": "GOVERNANCE reminder"}

        async def inclusive(input_data, tool_use_id, context):
            return {"decision": "approve", "additionalContext": "INCLUSIVE-TERM reminder"}

        registry = HookRegistry()
        registry.register("PreToolUse", gov, "gov")
        registry.register("PreToolUse", inclusive, "inclusive")
        chained = registry.build_sdk_hooks()["PreToolUse"][0].hooks[0]
        sdk_dict = await chained({"tool_name": "Write"}, None, MagicMock())

        assert "GOVERNANCE reminder" in sdk_dict["additionalContext"]
        assert "INCLUSIVE-TERM reminder" in sdk_dict["additionalContext"]


@pytest.mark.asyncio
async def test_pretooluse_allow_contexts_accumulate_and_other_fields_later_win():
    from core.hook_builder import HookRegistry

    async def a(i, t, c):
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                       "permissionDecision": "allow", "additionalContext": "A"}}

    async def b(i, t, c):
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                       "updatedInput": {"command": "x"}, "additionalContext": "B"}}

    registry = HookRegistry()
    registry.register("PreToolUse", a, "a", matcher="Bash")
    registry.register("PreToolUse", b, "b", matcher="Bash")
    chained = registry.build_sdk_hooks()["PreToolUse"][0].hooks[0]
    hso = (await chained({}, None, MagicMock()))["hookSpecificOutput"]
    assert hso["additionalContext"] == "A\nB"
    assert hso["updatedInput"] == {"command": "x"} and hso["permissionDecision"] == "allow"


@pytest.mark.asyncio
async def test_compose_gates_keeps_a_members_allow_note():
    """A compose_gates member that allows with a note (active_run_commit_gate) must not
    lose it; the first deny still wins."""
    from core.hook_builder import compose_gates

    async def note(i, t, c):
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                       "permissionDecision": "allow", "additionalContext": "N"}}

    async def deny(i, t, c):
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                       "permissionDecision": "deny",
                                       "permissionDecisionReason": "no"}}

    async def ok(i, t, c):
        return {"decision": "approve"}

    out = await compose_gates([("n", note), ("o", ok)])({}, None, None)
    assert out["hookSpecificOutput"]["additionalContext"] == "N"
    out = await compose_gates([("n", note), ("d", deny)])({}, None, None)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


@pytest.mark.asyncio
async def test_the_sdk_hands_both_contexts_to_the_cli_unchanged():
    """The SDK's own conversion of a hook result to the CLI payload keeps the joined
    additionalContext (claude_agent_sdk._internal.query._convert_hook_output_for_cli)."""
    from claude_agent_sdk._internal.query import _convert_hook_output_for_cli
    from core.hook_builder import HookRegistry

    async def a(i, t, c):
        return {"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": "A"}}

    async def b(i, t, c):
        return {"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": "B"}}

    registry = HookRegistry()
    registry.register("PostToolUse", a, "a", matcher="Read")
    registry.register("PostToolUse", b, "b", matcher="Read")
    chained = registry.build_sdk_hooks()["PostToolUse"][0].hooks[0]
    cli = _convert_hook_output_for_cli(await chained({}, None, MagicMock()))
    assert cli["hookSpecificOutput"]["additionalContext"] == "A\nB"
