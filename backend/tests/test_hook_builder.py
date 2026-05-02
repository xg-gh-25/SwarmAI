"""Tests for hook_builder.py HookRegistry pattern.

Verifies:
- Registry pattern: register hooks by event, chain execution, 5s timeout
- Backward compatibility: existing hooks wire correctly through registry
- Hook chaining: multiple hooks per event, first 'block' wins
"""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


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
            return {"additionalContext": "from_a"}

        async def hook_b(input_data, tool_use_id, context):
            call_order.append("b")
            return {"additionalContext": "from_b"}

        registry = HookRegistry()
        registry.register("PostToolUse", hook_a, "a")
        registry.register("PostToolUse", hook_b, "b")
        sdk_hooks = registry.build_sdk_hooks()

        # Extract the chained hook function from the HookMatcher
        chained = sdk_hooks["PostToolUse"][0].hooks[0]
        result = await chained({}, None, MagicMock())

        assert call_order == ["a", "b"]
        # Last additionalContext wins (or merged — depends on implementation)
        assert "additionalContext" in result

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
    async def test_hook_timeout_5s(self):
        """Hooks that exceed 5s are killed, chain continues."""
        from core.hook_builder import HookRegistry

        async def slow_hook(input_data, tool_use_id, context):
            await asyncio.sleep(10)
            return {"additionalContext": "should not appear"}

        async def fast_hook(input_data, tool_use_id, context):
            return {"additionalContext": "fast"}

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
        assert result.get("additionalContext") == "fast"

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
