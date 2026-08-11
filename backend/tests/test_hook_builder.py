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
        # Last hookSpecificOutput.additionalContext wins
        hso = result.get("hookSpecificOutput", {})
        assert "additionalContext" in hso
        assert hso["additionalContext"] == "from_b"

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


class TestCodeIntelWrapperAsync:
    """AC2 (R1, run_071e54c8): the code_intel wrapper must offload its SYNC hook
    to a thread so it does NOT block the daemon event loop.

    Root cause: the wrapper was `async def` but called the sync `ci_hook(...)`
    inline (no await) — a 41s SQLite JOIN then blocked the whole event loop and
    the 5s asyncio.wait_for guard could not interrupt it (a sync call never
    yields). The fix routes ci_hook through asyncio.to_thread; this test proves
    the loop stays responsive while a slow ci_hook runs.
    """

    @pytest.mark.asyncio
    async def test_slow_sync_hook_does_not_block_event_loop(self):
        import time as _time
        from core.hook_builder import _make_code_intel_wrapper

        started = asyncio.Event()

        def slow_ci_hook(tool_name, tool_input):
            # Blocking sync work (like the 41s SQLite JOIN), scaled down.
            started.set()
            _time.sleep(0.4)
            return {"decision": "approve", "_slow": True}

        wrapper = _make_code_intel_wrapper(slow_ci_hook)

        # Run the wrapper concurrently with a heartbeat coroutine. If the wrapper
        # blocks the loop (sync inline call), the heartbeat cannot tick until it
        # returns. If it offloads to a thread, the heartbeat ticks freely.
        ticks = 0

        async def heartbeat():
            nonlocal ticks
            await started.wait()
            for _ in range(3):
                await asyncio.sleep(0.05)
                ticks += 1

        result, _ = await asyncio.gather(
            wrapper({"tool_name": "Read", "tool_input": {"file_path": "x.py"}}, None, MagicMock()),
            heartbeat(),
        )
        assert result == {"decision": "approve", "_slow": True}
        # Loop remained responsive during the blocking hook → ticks advanced.
        assert ticks == 3, f"event loop was blocked (ticks={ticks}) — hook not offloaded"

    @pytest.mark.asyncio
    async def test_wrapper_extracts_tool_name_and_input(self):
        from core.hook_builder import _make_code_intel_wrapper
        seen = {}

        def capture(tool_name, tool_input):
            seen["tool_name"] = tool_name
            seen["tool_input"] = tool_input
            return {"decision": "approve"}

        wrapper = _make_code_intel_wrapper(capture)
        await wrapper({"tool_name": "Grep", "tool_input": {"pattern": "/api/x"}}, "tuid", MagicMock())
        assert seen["tool_name"] == "Grep"
        assert seen["tool_input"] == {"pattern": "/api/x"}
