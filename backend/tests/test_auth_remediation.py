"""Tests for method-aware auth remediation (AC5).

use_bedrock cannot distinguish ada from sso; both are Bedrock. The persisted
auth_method drives the remediation text so an external SSO user isn't told to
run `mwinit -f` (an Amazon-internal command they don't have).
"""
from core.auth_remediation import remediation_for


class TestRemediation:
    def test_ada_mentions_ada_or_mwinit(self):
        r = remediation_for("ada")
        assert r["settings_tab"] == "aws-account"   # sign-in lives on the account page now
        assert "ada credentials update" in r["fix_text"].lower() or "mwinit" in r["fix_text"].lower()

    def test_sso_says_aws_sso_login_not_mwinit(self):
        r = remediation_for("sso")
        assert "aws sso login" in r["fix_text"].lower()
        assert "mwinit" not in r["fix_text"].lower()

    def test_an_old_apikey_method_gets_the_generic_fix(self):
        # Anthropic keys are not supported (design 2026-11-01-settings-account-merge §3):
        # the old method name is unknown now and gets the method-agnostic text
        assert remediation_for("apikey") == remediation_for(None)

    def test_iam_role_mentions_iam(self):
        r = remediation_for("iam_role")
        assert "iam" in r["fix_text"].lower()
        assert "mwinit" not in r["fix_text"].lower()

    def test_bedrock_api_key_points_to_settings_not_mwinit(self):
        r = remediation_for("bedrock_api_key")
        assert r["settings_tab"] == "ai-models"
        assert "mwinit" not in r["fix_text"].lower()
        # points the user at the in-app Bedrock key entry
        assert "key" in r["fix_text"].lower()
        # must NOT be the generic fallback — it names Bedrock specifically
        assert "bedrock" in r["message"].lower() or "bedrock" in r["fix_text"].lower()

    def test_unknown_method_safe_generic_fallback(self):
        r = remediation_for(None)
        # must not crash, must not hardcode mwinit
        assert isinstance(r["fix_text"], str) and r["fix_text"]
        assert "mwinit" not in r["fix_text"].lower()

    def test_all_methods_have_message_and_tab(self):
        for m in ("ada", "sso", "apikey", "iam_role", "bedrock_api_key", None, "garbage"):
            r = remediation_for(m)
            assert r["message"] and r["fix_text"] and "settings_tab" in r


def test_the_account_states_have_their_own_text():
    """A profile account and another account than the SwarmAI one (core/aws_account)."""
    a = remediation_for("ada_account")
    assert "mwinit -f" in a["fix_text"] and "ada credentials update" not in a["fix_text"]
    p = remediation_for("profile")
    assert "aws sso login --profile" in p["fix_text"] and p["settings_tab"] == "aws-account"
    m = remediation_for("account_mismatch")
    assert "different account" in m["message"] and "mwinit" not in m["fix_text"]
