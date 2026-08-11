"""Unit tests for the TSCC security-scan endpoint.

Tests ``GET /api/chat/{session_id}/security-scan`` in ``routers/tscc.py``:

- clean prompt        → grade "A", credentials finding "pass", 0 critical
- prompt with AWS key → 1 critical, "warn", raw key NOT present in response
- prompt with email   → info finding, email masked in detail
- no metadata         → neutral empty result (grade "n/a"), no exception

Follows the conventions in ``test_tscc_router.py``: uses the shared
``client`` TestClient fixture and seeds ``session_registry.system_prompt_metadata``
directly, cleaning up in a ``finally`` block.
"""

from fastapi.testclient import TestClient


def _seed(session_id: str, full_text: str, files=None):
    from core import session_registry

    session_registry.system_prompt_metadata[session_id] = {
        "files": files or [],
        "total_tokens": 0,
        "full_text": full_text,
    }


def _cleanup(session_id: str):
    from core import session_registry

    session_registry.system_prompt_metadata.pop(session_id, None)


class TestSecurityScan:
    """Tests for the GET security-scan endpoint."""

    def test_clean_prompt_grades_a(self, client: TestClient):
        _seed(
            "sec-clean",
            "## SwarmAI\nJust some ordinary prose with no secrets here.\n",
            files=[{"filename": "SWARMAI.md", "tokens": 50, "truncated": False}],
        )
        try:
            resp = client.get("/api/chat/sec-clean/security-scan")
            assert resp.status_code == 200
            body = resp.json()
            assert body["grade"] == "A"
            assert body["critical"] == 0
            assert body["scanned_files"] == 1
            cred = next(f for f in body["findings"] if f["detector"] == "credentials")
            assert cred["status"] == "pass"
            assert cred["count"] == 0
        finally:
            _cleanup("sec-clean")

    def test_aws_key_is_critical_and_masked(self, client: TestClient):
        raw_key = "AKIAIOSFODNN7EXAMPLE"
        _seed("sec-aws", f"Here is a leaked key: {raw_key} oops\n")
        try:
            resp = client.get("/api/chat/sec-aws/security-scan")
            assert resp.status_code == 200
            body = resp.json()
            assert body["critical"] == 1
            assert body["grade"] == "C"
            cred = next(f for f in body["findings"] if f["detector"] == "credentials")
            assert cred["status"] == "warn"
            assert cred["severity"] == "critical"
            assert cred["count"] >= 1
            # Masking: the raw key must NOT appear verbatim anywhere in the response.
            assert raw_key not in resp.text
        finally:
            _cleanup("sec-aws")

    def test_email_is_info_and_masked(self, client: TestClient):
        _seed("sec-email", "Contact me at alice@example.com for details.\n")
        try:
            resp = client.get("/api/chat/sec-email/security-scan")
            assert resp.status_code == 200
            body = resp.json()
            assert body["info"] == 1
            email_finding = next(
                f for f in body["findings"] if f["detector"] == "pii_email"
            )
            assert email_finding["status"] == "warn"
            assert email_finding["severity"] == "info"
            assert email_finding["count"] == 1
            # Local part masked; raw local part not echoed, domain preserved.
            assert "alice@example.com" not in resp.text
            assert "al***@example.com" in email_finding["detail"]
            # No credentials → grade is A- (info present, no critical/high).
            assert body["grade"] == "A-"
        finally:
            _cleanup("sec-email")

    def test_secret_assignment_is_critical_and_masked(self, client: TestClient):
        # Regression for the adversarial finding (run_a5a101b9): a `password=`/
        # `api_key:` assignment must be caught, not scored a false "A". This is the
        # _SECRET_ASSIGN class that _CREDENTIAL_PATTERNS excludes.
        secret = "hunter2SuperSecret"
        _seed("sec-assign", f"config:\n  password: {secret}\n")
        try:
            resp = client.get("/api/chat/sec-assign/security-scan")
            assert resp.status_code == 200
            body = resp.json()
            assert body["critical"] == 1
            assert body["grade"] == "C"
            cred = next(f for f in body["findings"] if f["detector"] == "credentials")
            assert cred["status"] == "warn"
            assert cred["count"] >= 1
            # The raw secret value must NOT appear verbatim (masked).
            assert secret not in resp.text
        finally:
            _cleanup("sec-assign")

    def test_missing_metadata_returns_neutral_result(self, client: TestClient):
        resp = client.get("/api/chat/no-such-session/security-scan")
        assert resp.status_code == 200
        body = resp.json()
        assert body["grade"] == "n/a"
        assert body["findings"] == []
        assert body["critical"] == 0
        assert body["scanned_files"] == 0
