"""Tests for the shared CredentialValidator singleton in session_registry.

The spawn pre-flight (session_unit) and /health (main) must use ONE
CredentialValidator instance so they share the same check() cache —
otherwise each surface pays its own STS call and caches diverge.
"""
from core.credential_validator import CredentialValidator


def test_get_credential_validator_returns_singleton():
    from core import session_registry

    v1 = session_registry.get_credential_validator()
    v2 = session_registry.get_credential_validator()

    assert isinstance(v1, CredentialValidator)
    assert v1 is v2, "must return the SAME instance (shared cache)"
