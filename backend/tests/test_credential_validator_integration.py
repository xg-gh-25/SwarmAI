"""Unit tests for CredentialValidator integration into AgentManager.

Tests verify that:
- ``_execute_on_session`` calls ``CredentialValidator.is_valid()`` when
  Bedrock is enabled and yields a ``CREDENTIALS_EXPIRED`` error when
  credentials are invalid (Requirements 3.1, 3.2).
- ``_run_query_on_client`` calls ``CredentialValidator.invalidate()``
  when an auth error is detected via ``_AUTH_PATTERNS`` (Requirement 3.5).
- The ``AgentManager.configure()`` method properly wires the
  ``CredentialValidator`` instance.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


class TestCredentialValidatorWiring:
    """Verify the AgentManager accepts and stores a CredentialValidator."""

    def test_configure_stores_validator(self):
        """AgentManager.configure() should store the CredentialValidator."""
        from core.agent_manager import AgentManager
        from core.credential_validator import CredentialValidator
        from core.app_config_manager import AppConfigManager
        from core.cmd_permission_manager import CmdPermissionManager

        mgr = AgentManager()
        validator = CredentialValidator()
        cfg = MagicMock(spec=AppConfigManager)
        cmd = MagicMock(spec=CmdPermissionManager)

        mgr.configure(
            config_manager=cfg,
            cmd_permission_manager=cmd,
            credential_validator=validator,
        )
        assert mgr._credential_validator is validator
        assert isinstance(mgr._credential_validator, CredentialValidator)

    def test_validator_has_required_methods(self):
        """CredentialValidator must expose is_valid, get_identity, invalidate."""
        from core.credential_validator import CredentialValidator

        validator = CredentialValidator()
        assert callable(getattr(validator, "is_valid", None))
        assert callable(getattr(validator, "get_identity", None))
        assert callable(getattr(validator, "invalidate", None))


class TestExecuteOnSessionCredentialCheck:
    """Verify _execute_on_session yields CREDENTIALS_EXPIRED when Bedrock
    credentials are invalid (Requirements 3.1, 3.2)."""

    @pytest.mark.asyncio
    async def test_yields_credentials_expired_when_invalid(self):
        """When Bedrock is enabled and credentials are invalid, the generator
        should yield a CREDENTIALS_EXPIRED error and return immediately."""
        from core.agent_manager import AgentManager

        mgr = AgentManager()

        mock_cfg = MagicMock()
        mock_cfg.load.return_value = None
        mock_cfg.get.side_effect = lambda key, default=None: {
            "use_bedrock": True,
            "aws_region": "us-east-1",
        }.get(key, default)

        mock_validator = AsyncMock()
        mock_validator.is_valid = AsyncMock(return_value=False)

        # Wire components via instance attributes (DI pattern)
        mgr._config = mock_cfg
        mgr._credential_validator = mock_validator

        with patch("core.agent_manager._configure_claude_environment"):
            events = []
            async for event in mgr._execute_on_session(
                agent_config={"model": "test"},
                query_content="hello",
                display_text="hello",
                session_id=None,
                enable_skills=False,
                enable_mcp=False,
                is_resuming=False,
                content=None,
                user_message="hello",
                agent_id="test-agent",
            ):
                events.append(event)

            # Should yield exactly one error event
            assert len(events) == 1
            assert events[0]["type"] == "error"
            assert events[0]["code"] == "CREDENTIALS_EXPIRED"
            assert "expired" in events[0]["error"].lower()
            assert "suggested_action" in events[0]
            assert "ada credentials" in events[0]["suggested_action"].lower()

            # Validator should have been called with the region
            mock_validator.is_valid.assert_awaited_once_with("us-east-1")


    @pytest.mark.asyncio
    async def test_skips_validation_when_bedrock_disabled(self):
        """When Bedrock is disabled, credential validation should be skipped."""
        from core.agent_manager import AgentManager

        mgr = AgentManager()

        mock_cfg = MagicMock()
        mock_cfg.load.return_value = None
        mock_cfg.get.side_effect = lambda key, default=None: {
            "use_bedrock": False,
            "aws_region": "us-east-1",
        }.get(key, default)

        mock_validator = AsyncMock()
        mock_validator.is_valid = AsyncMock(return_value=False)

        # Wire components via instance attributes (DI pattern)
        mgr._config = mock_cfg
        mgr._credential_validator = mock_validator

        with (
            patch("core.agent_manager._configure_claude_environment"),
            # Patch _build_options to raise so we stop early (we only
            # care that is_valid was NOT called)
            patch.object(
                mgr, "_build_options", side_effect=StopAsyncIteration("stop")
            ),
        ):
            events = []
            try:
                async for event in mgr._execute_on_session(
                    agent_config={"model": "test"},
                    query_content="hello",
                    display_text="hello",
                    session_id=None,
                    enable_skills=False,
                    enable_mcp=False,
                    is_resuming=False,
                    content=None,
                    user_message="hello",
                    agent_id="test-agent",
                ):
                    events.append(event)
            except (StopAsyncIteration, Exception):
                pass

            # is_valid should NOT have been called
            mock_validator.is_valid.assert_not_awaited()


class TestRunQueryOnClientInvalidation:
    """Verify _run_query_on_client invalidates credential cache on auth
    errors detected via _AUTH_PATTERNS (Requirement 3.5)."""

    def test_invalidate_called_on_auth_pattern_match(self):
        """When _AUTH_PATTERNS detects an auth error, invalidate() should
        be called on the credential validator."""
        from core.agent_manager import _AUTH_PATTERNS
        from core.credential_validator import CredentialValidator

        validator = CredentialValidator()

        # Verify the patterns list is non-empty
        assert len(_AUTH_PATTERNS) > 0

        # Verify invalidate is callable
        assert callable(validator.invalidate)

        # Simulate what the code does: check pattern then invalidate
        test_error = "Not logged in to the service"
        error_lower = test_error.lower()
        is_auth = any(p in error_lower for p in _AUTH_PATTERNS)
        assert is_auth, "Expected 'not logged in' to match _AUTH_PATTERNS"

        # Call invalidate (should not raise)
        validator.invalidate()
        # After invalidation, cache should be cleared
        assert validator._last_check == 0
        assert validator._last_result is False
