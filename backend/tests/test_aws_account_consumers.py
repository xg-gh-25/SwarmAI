"""The SwarmAI AWS account, step 2: the daemon's consumers use it (design
docs/design/2026-10-31-swarmai-aws-account.md §5.3, §5.4, §9 step 2).

Questions answered:
1. does the CLI get awsCredentialExport exactly when chat is on Bedrock and the record
   resolves, and nothing otherwise (a key, Anthropic, no record, an unsupported source,
   a Hive) — and is the agent's shell environment left alone?
2. do boto_session / jobs.bedrock / the thinking gateway use the pinned credentials
   (refreshed through resolve), never fall back to the default chain when the account
   cannot be resolved, and keep the default chain when there is no account?
3. do the spawn pre-flight and the readiness sampler stop on an expiry and on another
   account, with the account's own remediation?
4. is the chat path read from the legacy settings by one function, and does a legacy
   sign-in change move the record?
5. headless CLI jobs: the settings flag, no frozen keys, no us-west-2 default.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from core import aws_account as aa
from core.app_config_manager import AppConfigManager

ACCT = "111122223333"
OTHER = "444455556666"


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    mgr = AppConfigManager(config_path=tmp_path / "config.json")
    monkeypatch.setattr(AppConfigManager, "_instance", mgr)
    monkeypatch.delenv("SWARMAI_MODE", raising=False)
    aa._reset_cache()
    yield mgr
    aa._reset_cache()


def _creds(akid="ASIAONE", minutes=60):
    return aa.Creds(akid, "secret-" + akid, "token-" + akid,
                    datetime.now(timezone.utc) + timedelta(minutes=minutes))


@pytest.fixture
def account(cfg, monkeypatch):
    """An ADA record that resolves to ACCT through a fake resolver and STS."""
    cfg.update({aa.RECORD_KEY: {"source": "ada", "account_id": ACCT, "region": "us-east-1",
                                "ada": {"role": "Admin", "provider": "isengard", "bin": "/x/ada"}}})
    answer = {"Account": ACCT, "Arn": f"arn:aws:sts::{ACCT}:assumed-role/Admin/me"}
    keys = {"next": "ASIAONE"}
    monkeypatch.setitem(aa._RESOLVERS, "ada", lambda rec: _creds(keys["next"]))
    monkeypatch.setattr(aa, "_sts_identity", lambda c, r: dict(answer))
    return answer, keys


# ── 1. the CLI's settings ──────────────────────────────────────────────────────

def test_cli_gets_the_credential_export_only_with_a_resolvable_account_on_bedrock(cfg, account):
    s = aa.cli_settings()
    assert list(s) == ["awsCredentialExport"]
    import shlex
    assert shlex.split(s["awsCredentialExport"]) == aa.backend_command()
    cfg.update({"auth_method": "bedrock_api_key"})
    assert aa.cli_settings() == {}
    cfg.update({"auth_method": "ada", "use_bedrock": False})
    assert aa.cli_settings() == {}
    cfg.update({"use_bedrock": True, aa.RECORD_KEY: {"source": "bogus", "account_id": ACCT}})
    assert aa.cli_settings() == {}                      # not a source this version resolves
    cfg.update({aa.RECORD_KEY: None})
    assert aa.cli_settings() == {}


def test_a_hive_keeps_its_instance_role_path(cfg, account, monkeypatch):
    monkeypatch.setenv("SWARMAI_MODE", "hive")
    assert not aa.active() and aa.cli_settings() == {}


def test_the_production_spawn_settings_carry_it_and_never_fail_a_spawn(cfg, account, monkeypatch):
    from core import prompt_builder
    assert prompt_builder._aws_account_cli_settings(cfg) == aa.cli_settings()
    monkeypatch.setattr(aa, "cli_settings", lambda c=None: 1 / 0)
    assert prompt_builder._aws_account_cli_settings(cfg) == {}


def test_the_settings_flag_reaches_the_cli_command(cfg, account):
    from claude_agent_sdk import ClaudeAgentOptions
    from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport
    t = SubprocessCLITransport(prompt="p", options=ClaudeAgentOptions(settings=json.dumps(aa.cli_settings())))
    t._cli_path = "/bin/true"
    cmd = t._build_command()
    assert json.loads(cmd[cmd.index("--settings") + 1])["awsCredentialExport"]


# ── 2. the daemon's own AWS calls ──────────────────────────────────────────────

def test_boto_session_signs_with_the_pinned_key_and_refreshes_through_resolve(cfg, account):
    _, keys = account
    sess = aa.boto_session()
    assert sess.get_credentials().get_frozen_credentials().access_key == "ASIAONE"
    keys["next"] = "ASIATWO"
    aa._cache = _creds("ASIAONE", minutes=1)            # stale: the next refresh resolves again
    creds = sess.get_credentials()
    creds._expiry_time = datetime.now(timezone.utc)     # botocore must refresh now
    assert creds.get_frozen_credentials().access_key == "ASIATWO"


def test_an_unresolvable_account_raises_and_never_uses_the_default_chain(cfg, account, monkeypatch):
    answer, _ = account
    answer.update(Account=OTHER)
    with pytest.raises(aa.AccountMismatch):
        aa.boto_session()
    import jobs.bedrock as jb
    called = []
    monkeypatch.setattr(jb, "_resolve_credentials", lambda: called.append(1) or {})
    with pytest.raises(aa.AccountMismatch):
        jb.get_client(force_new=True)
    assert called == []


def test_without_an_account_the_default_chain_stays(cfg):
    sess = aa.boto_session("us-west-2")
    assert sess.region_name == "us-west-2"
    assert not getattr(sess._session, "_credentials", None)  # botocore resolves it itself


def test_jobs_bedrock_uses_the_pinned_key(cfg, account):
    import jobs.bedrock as jb
    client = jb.get_client(force_new=True)
    assert client._request_signer._credentials.get_frozen_credentials().access_key == "ASIAONE"


def test_the_gateway_verifies_with_the_key_the_cli_signs_with(cfg, account):
    from core import bedrock_thinking_gateway as gw
    frozen, exp = gw._current_credentials(refresh=False)
    assert frozen.access_key == "ASIAONE" and frozen.secret_key == "secret-ASIAONE"
    assert exp is not None
    gw.set_listen_port(18321)
    assert gw.gateway_env_for_session(cfg, channel_context=None, thinking_display="summarized")
    cfg.update({"auth_method": "bedrock_api_key"})
    assert gw.gateway_env_for_session(cfg, channel_context=None, thinking_display="summarized") == {}


# ── 3. pre-flight and readiness ────────────────────────────────────────────────

async def _preflight(cfg):
    from core.session_unit import SessionUnit
    unit = SessionUnit(session_id="aa-preflight", agent_id="default")
    events = []
    with patch.object(unit, "_spawn", new=AsyncMock()) as spawn:
        async for ev in unit._ensure_spawned(object(), None):
            events.append(ev)
    return events, spawn


@pytest.mark.asyncio
async def test_preflight_stops_on_an_expiry_and_on_another_account(cfg, account, monkeypatch):
    monkeypatch.setitem(aa._RESOLVERS, "ada",
                        lambda rec: (_ for _ in ()).throw(aa.NeedsLogin("Midway has expired")))
    events, spawn = await _preflight(cfg)
    ev = next(e for e in events if e.get("code") == "CREDENTIALS_EXPIRED")
    assert "mwinit -f" in ev["suggested_action"] and "ada credentials update" not in ev["suggested_action"]
    spawn.assert_not_called()
    answer, _ = account
    monkeypatch.setitem(aa._RESOLVERS, "ada", lambda rec: _creds())
    answer.update(Account=OTHER)
    events, spawn = await _preflight(cfg)
    ev = next(e for e in events if e.get("code") == "CREDENTIALS_EXPIRED")
    assert "different account" in ev["message"]
    spawn.assert_not_called()


@pytest.mark.asyncio
async def test_readiness_reports_another_account_as_needing_the_user(cfg, account, monkeypatch):
    from core import readiness_sampler as rs
    seen = {}
    monkeypatch.setattr(rs.readiness_cache, "update", lambda db, auth: seen.update(auth=auth))
    answer, _ = account
    answer.update(Account=OTHER)
    with pytest.raises(aa.AccountMismatch):  # e.g. a CLI's credential request
        aa.resolve()
    calls = []
    monkeypatch.setattr(aa, "resolve", lambda force=False: calls.append(1))
    await rs._sample_once()
    assert seen["auth"] == "expired" and calls == []    # the record's state, no network
    cfg.update({aa.RECORD_KEY: {**aa.get_record(), "state": aa.CONNECTED}})
    await rs._sample_once()
    assert seen["auth"] == "valid"


def test_the_view_carries_the_banner_text_for_a_state_that_needs_the_user(cfg, account):
    cfg.update({aa.RECORD_KEY: {**aa.get_record(), "state": aa.MISMATCH}})
    assert "different account" in aa.view()["remediation"]["message"]
    cfg.update({aa.RECORD_KEY: {**aa.get_record(), "state": aa.NEEDS_LOGIN}})
    assert aa.view()["remediation"]["settings_tab"] == "aws-account"
    cfg.update({aa.RECORD_KEY: {**aa.get_record(), "state": aa.CONNECTED}})
    assert aa.view()["remediation"] is None


# ── 4. one reader for the chat path; a legacy sign-in change moves the record ──

def test_the_chat_path_follows_the_legacy_settings_exactly_as_before(cfg):
    assert aa.get_chat_via() == "aws"
    cfg.update({"auth_method": "bedrock_api_key"})
    assert aa.get_chat_via() == "bedrock_key"
    cfg.update({"auth_method": "apikey", "use_bedrock": False})
    assert aa.get_chat_via() == "anthropic"
    cfg.update({"auth_method": "apikey", "use_bedrock": True})   # the CLI still runs Bedrock
    assert aa.get_chat_via() == "aws"
    cfg.update({"auth_method": "ada", "use_bedrock": 0})
    assert aa.get_chat_via() == "anthropic"


def test_a_legacy_sign_in_change_rederives_the_record(cfg, account, monkeypatch):
    new = {"source": "profile", "profile": "corp", "account_id": OTHER, "region": "us-east-1"}
    rec = aa.get_record()
    # a record the person connected (wizard / account page) is never re-derived
    monkeypatch.setattr(aa, "derive_from_legacy", lambda c: (dict(new), "aws"))
    cfg.update({"auth_method": "sso"})
    assert aa.rederive(cfg)["account_id"] == ACCT
    aa.save_record({**rec, "derived": True})            # one derived from the old settings is
    monkeypatch.setattr(aa, "derive_from_legacy", lambda c: (dict(new), "aws"))
    for method in ("apikey", "bedrock_api_key", "iam_role"):   # not an AWS sign-in: no move
        cfg.update({"auth_method": method})
        assert aa.rederive(cfg)["account_id"] == ACCT
    cfg.update({"auth_method": "ada"})                         # a fall-back profile ≠ ada
    assert aa.rederive(cfg)["account_id"] == ACCT
    cfg.update({"auth_method": "sso"})
    monkeypatch.setattr(aa, "derive_from_legacy",
                        lambda c: ({**new, "account_id": None}, "aws"))
    assert aa.rederive(cfg)["account_id"] == ACCT              # never to an unpinned record
    monkeypatch.setattr(aa, "derive_from_legacy", lambda c: (dict(new), "aws"))
    assert aa.rederive(cfg)["account_id"] == OTHER
    assert aa.get_record()["profile"] == "corp"
    monkeypatch.setattr(aa, "derive_from_legacy", lambda c: (None, "aws"))
    assert aa.rederive(cfg)["account_id"] == OTHER             # nothing to derive: keep it


# ── 5. headless CLI jobs ───────────────────────────────────────────────────────

def test_jobs_get_the_settings_flag_and_no_frozen_keys(cfg, account, monkeypatch):
    from jobs import executor
    for method in ("ada", "apikey", "bedrock_api_key"):  # a job runs Bedrock whatever chat uses
        cfg.update({"auth_method": method, "use_bedrock": method == "ada"})
        assert executor._get_aws_credentials() == {}
        args = executor._aws_settings_args()
        assert args[0] == "--settings" and "awsCredentialExport" in json.loads(args[1])
    monkeypatch.delenv("AWS_REGION", raising=False)
    monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)
    cfg.update({"aws_region": "eu-west-1"})
    env = executor._build_cli_env({})
    assert env["AWS_REGION"] == "eu-west-1" and "AWS_ACCESS_KEY_ID" not in env


def test_without_an_account_jobs_keep_their_old_path(cfg, monkeypatch):
    from jobs import executor
    assert executor._aws_settings_args() == []
    monkeypatch.delenv("AWS_REGION", raising=False)
    monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)
    monkeypatch.setattr(executor, "_get_aws_credentials", lambda: {})
    assert executor._build_cli_env({})["AWS_REGION"] == "us-west-2"


def test_the_fail_fast_client_uses_the_pinned_key_too(cfg, account, monkeypatch):
    import jobs.bedrock as jb
    called = []
    monkeypatch.setattr(jb, "_resolve_credentials", lambda: called.append(1) or {})
    c = jb.build_timeout_client(read_timeout=5, max_attempts=1, region="us-east-1")
    assert c._request_signer._credentials.get_frozen_credentials().access_key == "ASIAONE"
    assert called == []


@pytest.mark.asyncio
async def test_preflight_stops_on_a_broken_setup_and_goes_ahead_on_a_slow_check(cfg, account, monkeypatch):
    monkeypatch.setitem(aa._RESOLVERS, "ada", lambda rec: (_ for _ in ()).throw(
        aa.AccountError("ada_not_found", "ada was not found where it was at setup")))
    events, spawn = await _preflight(cfg)
    ev = next(e for e in events if e.get("code") == "CREDENTIALS_EXPIRED")
    assert "can't sign in" in ev["message"]
    spawn.assert_not_called()
    import time
    from core import session_unit
    monkeypatch.setattr(session_unit, "_ACCOUNT_PREFLIGHT_TIMEOUT_S", 0.2)
    monkeypatch.setattr(aa, "auth_check", lambda: time.sleep(2) or "expired")  # too late
    events, spawn = await _preflight(cfg)
    assert not any(e.get("code") == "CREDENTIALS_EXPIRED" for e in events)
    spawn.assert_called()


def test_a_non_owner_channel_cannot_repoint_the_account():
    from core.security_hooks import owner_command_denial
    assert owner_command_denial(
        "curl -X POST http://127.0.0.1:18321/api/system/auth-method -d '{\"method\":\"sso\"}'")


def test_polly_follows_an_account_connected_after_the_first_call(cfg, account, monkeypatch):
    from core import voice_synthesize as vs
    vs._get_polly_client.cache_clear()
    monkeypatch.setattr(aa, "active", lambda: False)
    first = vs._get_polly_client()
    monkeypatch.setattr(aa, "active", lambda: True)
    second = vs._get_polly_client()
    assert first is not second
    assert second._request_signer._credentials.get_frozen_credentials().access_key == "ASIAONE"
    vs._get_polly_client.cache_clear()
