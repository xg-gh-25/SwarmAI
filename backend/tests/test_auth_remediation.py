"""Tests for method-aware auth remediation (AC5).

use_bedrock cannot distinguish ada from sso; both are Bedrock. The persisted
auth_method drives the remediation text so an external SSO user isn't told to
run `mwinit -f` (an Amazon-internal command they don't have).
"""
import pytest
from core.auth_remediation import remediation_for


class TestRemediation:
    def test_ada_mentions_ada_or_mwinit(self):
        r = remediation_for("ada")
        assert r["settings_tab"] == "ai-models"
        assert "ada credentials update" in r["fix_text"].lower() or "mwinit" in r["fix_text"].lower()

    def test_sso_says_aws_sso_login_not_mwinit(self):
        r = remediation_for("sso")
        assert "aws sso login" in r["fix_text"].lower()
        assert "mwinit" not in r["fix_text"].lower()

    def test_apikey_points_to_settings_not_aws(self):
        r = remediation_for("apikey")
        assert "mwinit" not in r["fix_text"].lower()
        assert "aws" not in r["fix_text"].lower()
        # points the user at the in-app key entry
        assert "key" in r["fix_text"].lower()

    def test_iam_role_mentions_iam(self):
        r = remediation_for("iam_role")
        assert "iam" in r["fix_text"].lower()
        assert "mwinit" not in r["fix_text"].lower()

    def test_unknown_method_safe_generic_fallback(self):
        r = remediation_for(None)
        # must not crash, must not hardcode mwinit
        assert isinstance(r["fix_text"], str) and r["fix_text"]
        assert "mwinit" not in r["fix_text"].lower()

    def test_all_methods_have_message_and_tab(self):
        for m in ("ada", "sso", "apikey", "iam_role", None, "garbage"):
            r = remediation_for(m)
            assert r["message"] and r["fix_text"] and "settings_tab" in r
