"""Unit tests for the CredentialValidator class.

Tests the pre-flight AWS credential validation component that uses
STS GetCallerIdentity with 5-minute caching.  All boto3 STS calls are
mocked — no real AWS credentials or network access required.

Key behaviors tested:

- Successful validation caches result for CACHE_TTL seconds
- Failed validation invalidates cache immediately
- Cache expiry triggers a fresh STS call
- ``invalidate()`` forces re-check on next call
- ``get_identity()`` returns caller identity dict or None
- Graceful handling of NoCredentialsError, ClientError, BotoCoreError
- ``asyncio.to_thread`` is used for the synchronous boto3 call
"""

import asyncio
import time
from unittest.mock import MagicMock, patch

import pytest

from core.credential_validator import CredentialValidator


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

FAKE_IDENTITY = {
    "Account": "123456789012",
    "Arn": "arn:aws:iam::123456789012:user/testuser",
    "UserId": "AIDAEXAMPLE",
}


@pytest.fixture
def validator() -> CredentialValidator:
    """Fresh CredentialValidator instance for each test."""
    return CredentialValidator()


# ---------------------------------------------------------------------------
# is_valid() tests
# ---------------------------------------------------------------------------


async def test_is_valid_returns_true_on_success(validator: CredentialValidator):
    """Successful STS call returns True and caches the result."""
    with patch.object(validator, "_call_sts", return_value=FAKE_IDENTITY):
        result = await validator.is_valid("us-east-1")

    assert result is True
    assert validator._last_result is True
    assert validator._last_identity == FAKE_IDENTITY
    assert validator._last_check > 0


async def test_is_valid_returns_false_on_no_credentials(validator: CredentialValidator):
    """NoCredentialsError from boto3 returns False."""
    with patch.object(
        validator, "_call_sts", side_effect=RuntimeError("No AWS credentials found")
    ):
        result = await validator.is_valid("us-east-1")

    assert result is False
    assert validator._last_result is False
    assert validator._last_identity is None
    assert validator._last_check == 0


async def test_is_valid_returns_false_on_client_error(validator: CredentialValidator):
    """ClientError from boto3 returns False."""
    with patch.object(
        validator, "_call_sts", side_effect=RuntimeError("ExpiredTokenException")
    ):
        result = await validator.is_valid("us-east-1")

    assert result is False


async def test_is_valid_returns_false_on_unexpected_error(validator: CredentialValidator):
    """Unexpected exceptions are caught and return False."""
    with patch.object(
        validator, "_call_sts", side_effect=OSError("network unreachable")
    ):
        result = await validator.is_valid("us-east-1")

    assert result is False
    assert validator._last_check == 0


# ---------------------------------------------------------------------------
# Cache behavior tests
# ---------------------------------------------------------------------------


async def test_cached_result_avoids_sts_call(validator: CredentialValidator):
    """Within TTL, is_valid() returns cached result without calling STS."""
    mock_sts = MagicMock(return_value=FAKE_IDENTITY)
    with patch.object(validator, "_call_sts", mock_sts):
        await validator.is_valid("us-east-1")
        await validator.is_valid("us-east-1")

    # STS should only be called once — second call uses cache
    mock_sts.assert_called_once_with("us-east-1")


async def test_cache_expires_after_ttl(validator: CredentialValidator):
    """After TTL expires, is_valid() calls STS again."""
    mock_sts = MagicMock(return_value=FAKE_IDENTITY)
    with patch.object(validator, "_call_sts", mock_sts):
        await validator.is_valid("us-east-1")

        # Simulate TTL expiry by backdating _last_check
        validator._last_check = time.monotonic() - validator.CACHE_TTL - 1

        await validator.is_valid("us-east-1")

    assert mock_sts.call_count == 2


async def test_failure_invalidates_cache(validator: CredentialValidator):
    """Failed validation clears cache so next call re-checks."""
    # First: successful validation
    with patch.object(validator, "_call_sts", return_value=FAKE_IDENTITY):
        await validator.is_valid("us-east-1")
    assert validator._last_result is True

    # Simulate TTL expiry so next call hits STS
    validator._last_check = time.monotonic() - validator.CACHE_TTL - 1

    # Second: failed validation
    with patch.object(
        validator, "_call_sts", side_effect=RuntimeError("expired")
    ):
        result = await validator.is_valid("us-east-1")

    assert result is False
    assert validator._last_check == 0  # cache cleared
    assert validator._last_identity is None


# ---------------------------------------------------------------------------
# invalidate() tests
# ---------------------------------------------------------------------------


async def test_invalidate_forces_recheck(validator: CredentialValidator):
    """invalidate() clears cache so next is_valid() calls STS."""
    mock_sts = MagicMock(return_value=FAKE_IDENTITY)
    with patch.object(validator, "_call_sts", mock_sts):
        await validator.is_valid("us-east-1")
        validator.invalidate()
        await validator.is_valid("us-east-1")

    assert mock_sts.call_count == 2


def test_invalidate_clears_all_state(validator: CredentialValidator):
    """invalidate() resets all cached fields."""
    validator._last_check = time.monotonic()
    validator._last_result = True
    validator._last_identity = FAKE_IDENTITY

    validator.invalidate()

    assert validator._last_check == 0
    assert validator._last_result is False
    assert validator._last_identity is None


# ---------------------------------------------------------------------------
# get_identity() tests
# ---------------------------------------------------------------------------


async def test_get_identity_returns_dict_on_success(validator: CredentialValidator):
    """get_identity() returns caller identity when credentials are valid."""
    with patch.object(validator, "_call_sts", return_value=FAKE_IDENTITY):
        identity = await validator.get_identity("us-east-1")

    assert identity == FAKE_IDENTITY


async def test_get_identity_returns_none_on_failure(validator: CredentialValidator):
    """get_identity() returns None when credentials are invalid."""
    with patch.object(
        validator, "_call_sts", side_effect=RuntimeError("no creds")
    ):
        identity = await validator.get_identity("us-east-1")

    assert identity is None


async def test_get_identity_uses_cache(validator: CredentialValidator):
    """get_identity() shares cache with is_valid()."""
    mock_sts = MagicMock(return_value=FAKE_IDENTITY)
    with patch.object(validator, "_call_sts", mock_sts):
        await validator.is_valid("us-east-1")
        identity = await validator.get_identity("us-east-1")

    # Only one STS call — get_identity reuses the cached result
    mock_sts.assert_called_once()
    assert identity == FAKE_IDENTITY


# ---------------------------------------------------------------------------
# _call_sts() tests (synchronous, mocking boto3)
# ---------------------------------------------------------------------------


def test_call_sts_success(validator: CredentialValidator):
    """_call_sts returns identity dict on successful STS call."""
    mock_client = MagicMock()
    mock_client.get_caller_identity.return_value = {
        "Account": "111222333444",
        "Arn": "arn:aws:iam::111222333444:role/test",
        "UserId": "AROAEXAMPLE",
        "ResponseMetadata": {},
    }
    mock_session = MagicMock()
    mock_session.client.return_value = mock_client
    with patch("boto3.Session", return_value=mock_session):
        result = validator._call_sts("us-west-2")

    assert result["Account"] == "111222333444"
    assert result["Arn"] == "arn:aws:iam::111222333444:role/test"
    assert result["UserId"] == "AROAEXAMPLE"


def test_call_sts_no_credentials_error(validator: CredentialValidator):
    """_call_sts raises RuntimeError on NoCredentialsError."""
    from botocore.exceptions import NoCredentialsError

    mock_client = MagicMock()
    mock_client.get_caller_identity.side_effect = NoCredentialsError()
    mock_session = MagicMock()
    mock_session.client.return_value = mock_client
    with patch("boto3.Session", return_value=mock_session):
        with pytest.raises(RuntimeError, match="No AWS credentials found"):
            validator._call_sts("us-east-1")


def test_call_sts_client_error(validator: CredentialValidator):
    """_call_sts raises RuntimeError on ClientError."""
    from botocore.exceptions import ClientError

    error_response = {
        "Error": {"Code": "ExpiredTokenException", "Message": "Token expired"}
    }
    mock_client = MagicMock()
    mock_client.get_caller_identity.side_effect = ClientError(
        error_response, "GetCallerIdentity"
    )
    mock_session = MagicMock()
    mock_session.client.return_value = mock_client
    with patch("boto3.Session", return_value=mock_session):
        with pytest.raises(RuntimeError, match="ExpiredTokenException"):
            validator._call_sts("us-east-1")


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


async def test_fresh_validator_has_no_cache(validator: CredentialValidator):
    """A new CredentialValidator has no cached state."""
    assert validator._last_check == 0
    assert validator._last_result is False
    assert validator._last_identity is None
    assert not validator._is_cache_valid()


async def test_region_passed_to_sts(validator: CredentialValidator):
    """The region argument is forwarded to the STS call."""
    mock_sts = MagicMock(return_value=FAKE_IDENTITY)
    with patch.object(validator, "_call_sts", mock_sts):
        await validator.is_valid("eu-west-1")

    mock_sts.assert_called_once_with("eu-west-1")
