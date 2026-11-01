"""The one-click fix behind a sign-in problem (design docs/design/2026-11-01-settings-account-merge.md
§4.2).

Questions answered:
1. does every remediation carry an action of a known kind, and does each sign-in method
   get the action the design's table names (a terminal command only for ADA / legacy SSO /
   an SSO profile, the in-app sign-in for App SSO and AWS login, a settings tab otherwise)?
2. is a profile's ``aws sso login --profile <name>`` built only for a name that is one safe
   shell word and an SSO profile — anything else falls back to the account page?
"""
from __future__ import annotations

import pytest

from core import auth_remediation as ar
from core import aws_account as aa
from core.app_config_manager import AppConfigManager


def test_every_remediation_has_a_known_action():
    for method in list(ar._REMEDIATION) + [None, "bogus"]:
        act = ar.remediation_for(method)["action"]
        assert act["kind"] in ("terminal", "sign_in", "settings"), method
        if act["kind"] == "terminal":
            assert act["command"] and act["title"]
        if act["kind"] == "settings":
            assert act["tab"] in ("aws-account", "ai-models")


@pytest.mark.parametrize("method,kind,command", [
    ("ada_account", "terminal", "mwinit -f"),
    ("ada", "terminal", "mwinit -f"),
    ("sso", "terminal", "aws sso login"),
    ("sso_app", "sign_in", None),
    ("aws_login", "sign_in", None),
    ("access_keys", "settings", None),
    ("account_broken", "settings", None),
    ("account_mismatch", "settings", None),
    ("bedrock_api_key", "settings", None),
])
def test_each_method_gets_the_designed_action(method, kind, command):
    act = ar.remediation_for(method)["action"]
    assert act["kind"] == kind and act.get("command") == command


@pytest.fixture
def profile_account(tmp_path, monkeypatch):
    mgr = AppConfigManager(config_path=tmp_path / "config.json")
    monkeypatch.setattr(AppConfigManager, "_instance", mgr)
    monkeypatch.delenv("SWARMAI_MODE", raising=False)
    conf = tmp_path / "aws-config"
    conf.write_text("[profile corp]\nsso_session = s\n[profile keys]\nregion = us-east-1\n"
                    "[profile a;b]\nsso_session = s\n[profile -x]\nsso_session = s\n"
                    "[profile x $(id)]\nsso_session = s\n[profile a`id`]\nsso_session = s\n")
    monkeypatch.setenv("AWS_CONFIG_FILE", str(conf))
    aa._reset_cache()

    def use(profile):
        mgr.update({aa.RECORD_KEY: {"source": "profile", "profile": profile, "account_id": "111122223333",
                                    "state": aa.NEEDS_LOGIN}})
    yield use
    aa._reset_cache()


def test_an_sso_profile_gets_its_login_command(profile_account):
    profile_account("corp")
    act = aa.view()["remediation"]["action"]
    assert act == {"kind": "terminal", "command": "aws sso login --profile corp", "title": "AWS sign-in"}


@pytest.mark.parametrize("name", ["keys", "a;b", "x $(id)", "a`id`", "-x", "corp\n", "missing", "", "c" * 65])
def test_any_other_profile_opens_the_account_page(profile_account, name):
    profile_account(name)
    act = aa.view()["remediation"]["action"]
    assert act == {"kind": "settings", "tab": "aws-account"}


def test_without_the_account_the_legacy_method_keeps_its_fix(tmp_path, monkeypatch):
    mgr = AppConfigManager(config_path=tmp_path / "config.json")
    monkeypatch.setattr(AppConfigManager, "_instance", mgr)
    monkeypatch.delenv("SWARMAI_MODE", raising=False)
    aa._reset_cache()
    for method, kind in (("ada", "terminal"), ("sso", "terminal"), ("apikey", "settings"), (None, "settings")):
        mgr.update({"auth_method": method})
        assert aa.view()["remediation"]["action"]["kind"] == kind, method
    assert aa.view()["remediation"]["id"] == "fallback"        # no method: not known to be unset-up
    from core import session_registry
    v = session_registry.get_credential_validator()
    monkeypatch.setattr(v, "last_expired_reason", "no_credentials")
    assert aa.view()["remediation"]["id"] == "not_connected"   # no credentials at all
    monkeypatch.setenv("SWARMAI_MODE", "hive")
    mgr.update({"auth_method": "iam_role"})
    assert aa.view()["remediation"]["action"] == {"kind": "settings", "tab": "ai-models"}
    aa._reset_cache()


def test_a_hive_is_told_about_its_instance_role_whatever_auth_method_says(tmp_path, monkeypatch):
    mgr = AppConfigManager(config_path=tmp_path / "config.json")
    monkeypatch.setattr(AppConfigManager, "_instance", mgr)
    monkeypatch.setenv("SWARMAI_MODE", "hive")
    aa._reset_cache()
    for method in (None, "sso", "ada"):
        mgr.update({"auth_method": method})
        r = aa.view()["remediation"]
        assert r["action"] == {"kind": "settings", "tab": "ai-models"} and "IAM" in r["message"], method
    aa._reset_cache()
