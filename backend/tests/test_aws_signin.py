"""The sign-ins SwarmAI runs itself (core/aws_signin.py; design 2026-10-31-swarmai-aws-account
§6.3, §6.3b, §5.1).

Questions answered:
1. SSO device flow: is a start URL only ever an AWS one, is the sign-in link checked
   before the app opens it, does a pending poll stay pending and a finished one store the
   token (never in the record) and list accounts with Admin roles first?
2. SSO credentials: is a near-expired token refreshed, does a failed refresh or a missing
   token mean "sign in again" (NeedsLogin), is an expired or rejected client registered
   again instead of being reported as a sign-in problem, and does a token from another
   Identity Center never serve this record?
3. Access keys: only long-term keys are kept (temporary ASIA keys are refused), and the
   resolver serves them; none stored is an error.
4. AWS login: offered only with AWS CLI 2.32+, run with SwarmAI's own config file and
   cache (never the person's ~/.aws), and an ended session reads "sign in again".
5. One slot per identity: a sign-in for another start URL, another key pair or a new
   AWS login (finished or not) never changes what the current account uses; ``prune``
   keeps exactly what the given records use; the first stored shapes still read.
6. Hosts and endpoints: only AWS access-portal and device-sign-in hosts pass (a Lambda
   URL or API Gateway under ``.aws`` / ``.amazonaws.com`` does not), and an endpoint
   configured in ~/.aws or the environment never receives the Identity Center calls.
"""
from __future__ import annotations

import json
import os
import stat
import time
from datetime import datetime, timezone

import pytest

from core import aws_account as aa
from core import aws_signin as si
from core.app_config_manager import AppConfigManager

START = "https://d-1234567890.awsapps.com/start"
START2 = "https://d-2222222222.awsapps.com/start"


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    mgr = AppConfigManager(config_path=tmp_path / "config.json")
    monkeypatch.setattr(AppConfigManager, "_instance", mgr)
    monkeypatch.setattr(aa, "aws_dir", lambda: tmp_path / "aws")
    si._flows.clear()
    yield mgr


class _Err(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.response = {"Error": {"Code": code}}


class _Pager:
    def __init__(self, pages):
        self.pages = pages

    def paginate(self, **kw):
        return self.pages(kw)


class FakeAws:
    """sso-oidc + sso, enough for the device flow."""

    def __init__(self):
        self.registered = 0
        self.pending = 1
        self.refreshes = []
        self.link = "https://device.sso.us-east-1.amazonaws.com/?user_code=WDJB-MJHT"
        self.fail_refresh = False
        self.start_error = None

    def client(self, service, region):
        outer = self

        class Oidc:
            def register_client(self, **kw):
                outer.registered += 1
                assert kw["issuerUrl"] in (START, START2) and "refresh_token" in kw["grantTypes"]
                return {"clientId": f"cid{outer.registered}", "clientSecret": "csecret",
                        "clientSecretExpiresAt": int(time.time()) + 86400 * 90}

            def start_device_authorization(self, **kw):
                if outer.start_error:
                    err, outer.start_error = outer.start_error, None
                    raise _Err(err)
                return {"deviceCode": "dev", "userCode": "WDJB-MJHT",
                        "verificationUriComplete": outer.link, "expiresIn": 600, "interval": 5}

            def create_token(self, **kw):
                if kw["grantType"] == "refresh_token":
                    outer.refreshes.append(kw["refreshToken"])
                    if outer.fail_refresh:
                        raise _Err("InvalidGrantException")
                    return {"accessToken": "access2", "expiresIn": 3600}
                if outer.pending:
                    outer.pending -= 1
                    raise _Err("AuthorizationPendingException")
                return {"accessToken": "access1", "expiresIn": 3600, "refreshToken": "refresh1"}

        class Sso:
            def get_paginator(self, name):
                if name == "list_accounts":
                    return _Pager(lambda kw: [{"accountList": [
                        {"accountId": "111111111111", "accountName": "prod"}]}])
                return _Pager(lambda kw: [{"roleList": [
                    {"roleName": "ReadOnly"}, {"roleName": "AdministratorAccess"}]}])

            def get_role_credentials(self, **kw):
                assert kw["accessToken"] in ("access1", "access2")
                return {"roleCredentials": {
                    "accessKeyId": "ASIAROLEKEY0000001", "secretAccessKey": "s" * 40,
                    "sessionToken": "t", "expiration": int((time.time() + 3600) * 1000)}}
        return Oidc() if service == "sso-oidc" else Sso()


@pytest.fixture
def fake(monkeypatch):
    f = FakeAws()
    monkeypatch.setattr(si, "_unsigned", f.client)
    return f


@pytest.mark.parametrize("url", ["http://d-1.awsapps.com/start", "https://evil.example/start",
                                 "https://user@d-1.awsapps.com/start", "",
                                 "https://x.lambda-url.us-east-1.on.aws/start",
                                 "https://abc.execute-api.us-east-1.amazonaws.com/start",
                                 "https://d-1.awsapps.com:8443/start"])
def test_only_an_aws_start_url(url):
    with pytest.raises(si.SigninError):
        si.check_start_url(url)


@pytest.mark.parametrize("url", [START, "https://ssoins-1a2b.portal.us-east-1.app.aws/",
                                 "https://d-1.awsapps.cn/start"])
def test_real_access_portals_pass(url):
    assert si.check_start_url(url) == url


@pytest.mark.parametrize("link,ok", [
    ("https://device.sso.us-east-1.amazonaws.com/?user_code=X", True),
    ("https://d-1.awsapps.com/start/#/device?user_code=X", True),
    ("https://x.lambda-url.us-east-1.on.aws/device", False),
    ("https://abc.execute-api.us-east-1.amazonaws.com/device", False),
    ("https://evil.example/device", False)])
def test_only_aws_sign_in_links_are_opened(link, ok):
    assert si._verification_url_ok(link) is ok


def test_a_configured_endpoint_never_receives_identity_center_calls(monkeypatch):
    monkeypatch.setenv("AWS_ENDPOINT_URL_SSO_OIDC", "https://x.lambda-url.us-east-1.on.aws")
    monkeypatch.setenv("AWS_ENDPOINT_URL_STS", "https://evil.example")
    assert si._unsigned("sso-oidc", "us-east-1").meta.endpoint_url == "https://oidc.us-east-1.amazonaws.com"
    import boto3
    seen = {}
    real = boto3.client

    def spy(*a, **kw):
        c = real(*a, **kw)
        seen["url"] = c.meta.endpoint_url
        c.get_caller_identity = lambda: {"Account": "111111111111"}
        return c
    monkeypatch.setattr(boto3, "client", spy)
    aa._sts_identity(aa.Creds("AKIAX", "s", None, None), "us-east-1")
    assert seen["url"] == "https://sts.us-east-1.amazonaws.com"


def test_device_flow_stores_the_token_and_lists_accounts(cfg, fake):
    started = si.sso_start(START, "us-east-1")
    assert started["user_code"] == "WDJB-MJHT" and started["verification_url"] == fake.link
    assert si.sso_poll(started["flow_id"]) == {"status": "pending"}
    done = si.sso_poll(started["flow_id"])
    assert done["status"] == "done"
    assert done["accounts"] == [{"account_id": "111111111111", "name": "prod",
                                 "roles": ["AdministratorAccess", "ReadOnly"]}]
    stored = json.loads(cfg.get(si.SSO_SECRET))["sessions"][START]
    assert stored["token"]["refresh"] == "refresh1" and stored["start_url"] == START
    assert "refresh1" not in json.dumps(aa.view())  # never in the record or the view


def test_a_sign_in_link_that_is_not_aws_is_never_returned(cfg, fake):
    fake.link = "https://evil.example/device"
    with pytest.raises(si.SigninError):
        si.sso_start(START, "us-east-1")


def test_a_finished_or_unknown_flow_must_start_again(cfg, fake):
    with pytest.raises(si.SigninError) as exc:
        si.sso_poll("nope")
    assert exc.value.kind == "expired"


def _signed_in(cfg, fake, expires_in=3600, url=START):
    f = si.sso_start(url, "us-east-1")["flow_id"]
    fake.pending = 0
    si.sso_poll(f)
    stored = json.loads(cfg.get(si.SSO_SECRET))
    stored["sessions"][url]["token"]["expires_at"] = int(time.time()) + expires_in
    cfg.set_secret(si.SSO_SECRET, json.dumps(stored))


def _session(cfg, url=START):
    return json.loads(cfg.get(si.SSO_SECRET))["sessions"][url]


REC = {"source": "sso", "account_id": "111111111111",
       "sso": {"start_url": START, "sso_region": "us-east-1", "role": "AdministratorAccess"}}


def test_sso_credentials_and_refresh(cfg, fake):
    _signed_in(cfg, fake)
    c = si.sso_creds(REC)
    assert c.access_key_id == "ASIAROLEKEY0000001" and c.expiration > datetime.now(timezone.utc)
    assert fake.refreshes == []
    _signed_in(cfg, fake, expires_in=60)          # about to expire: refreshed first
    si.sso_creds(REC)
    assert fake.refreshes == ["refresh1"]
    assert _session(cfg)["token"]["access"] == "access2"


def test_a_failed_refresh_or_no_token_means_sign_in_again(cfg, fake):
    with pytest.raises(aa.NeedsLogin):
        si.sso_creds(REC)                          # never signed in
    _signed_in(cfg, fake, expires_in=60)
    fake.fail_refresh = True
    with pytest.raises(aa.NeedsLogin):
        si.sso_creds(REC)


def test_another_identity_centers_token_never_serves_this_record(cfg, fake):
    _signed_in(cfg, fake)
    other = {**REC, "sso": {**REC["sso"], "start_url": "https://d-999.awsapps.com/start"}}
    with pytest.raises(aa.NeedsLogin):
        si.sso_creds(other)


def test_an_expired_or_rejected_client_is_registered_again(cfg, fake):
    si.sso_start(START, "us-east-1")
    si.sso_start(START, "us-east-1")
    assert fake.registered == 1                    # reused while valid
    stored = json.loads(cfg.get(si.SSO_SECRET))
    stored["pending_clients"][START]["expires_at"] = int(time.time()) + 10
    cfg.set_secret(si.SSO_SECRET, json.dumps(stored))
    si.sso_start(START, "us-east-1")
    assert fake.registered == 2                    # expired: registered, not "sign in again"
    fake.start_error = "InvalidClientException"
    si.sso_start(START, "us-east-1")
    assert fake.registered == 3


def test_keys(cfg):
    with pytest.raises(si.SigninError):
        si.store_keys("not-a-key", "x")
    with pytest.raises(si.SigninError) as exc:
        si.store_keys("ASIAABCDEFGHIJKLMNOP", "a" * 40)
    assert exc.value.kind == "temporary_keys"
    with pytest.raises(aa.AccountError):
        si.keys_creds({})
    kid = si.store_keys("AKIAABCDEFGHIJKLMNOP", "b" * 40)
    c = si.keys_creds({"keys_id": kid})
    assert c.access_key_id == "AKIAABCDEFGHIJKLMNOP" and c.expiration is None
    assert "b" * 40 not in (cfg._config_path.read_text() if cfg._config_path.exists() else "")


# ── one slot per identity ─────────────────────────────────────────────────────

REC2 = {**REC, "sso": {**REC["sso"], "start_url": START2}}


def test_another_start_url_never_changes_the_current_sign_in(cfg, fake):
    _signed_in(cfg, fake)
    before = _session(cfg)
    si.sso_start(START2, "us-east-1")              # started and abandoned
    assert _session(cfg) == before
    _signed_in(cfg, fake, url=START2)              # finished: sits next to it
    assert _session(cfg) == before
    assert si.sso_creds(REC).access_key_id and si.sso_creds(REC2).access_key_id


def test_a_new_client_for_the_same_portal_waits_for_its_token(cfg, fake):
    _signed_in(cfg, fake)
    before = _session(cfg)
    si._client(START, "us-east-1", fresh=True)     # e.g. the old one was rejected
    assert _session(cfg)["client"] == before["client"]   # refresh still uses the old one
    si.sso_creds(REC)


def test_a_second_key_pair_never_replaces_the_first(cfg):
    a = si.store_keys("AKIAAAAAAAAAAAAAAAAA", "a" * 40)
    b = si.store_keys("AKIABBBBBBBBBBBBBBBB", "b" * 40)
    assert si.keys_creds({"keys_id": a}).secret_access_key == "a" * 40
    assert si.keys_creds({"keys_id": b}).secret_access_key == "b" * 40


def test_prune_keeps_exactly_what_the_records_use(cfg, fake, tmp_path):
    _signed_in(cfg, fake)
    _signed_in(cfg, fake, url=START2)
    a = si.store_keys("AKIAAAAAAAAAAAAAAAAA", "a" * 40)
    si.store_keys("AKIABBBBBBBBBBBBBBBB", "b" * 40)
    si._login_env("us-east-1", "swarmai-login-0000000a")
    si._login_env("us-east-1", "swarmai-login-0000000b")
    si.prune([REC2, {"source": "keys", "keys_id": a},
              {"source": "login", "login": {"profile": "swarmai-login-0000000b"}}])
    assert set(json.loads(cfg.get(si.SSO_SECRET))["sessions"]) == {START2}
    assert set(si._keys_store()["keys"]) == {a}
    assert si.login_profiles() == {"swarmai-login-0000000b"}
    si.prune([])
    assert not cfg.get(si.SSO_SECRET) and not cfg.get(si.KEYS_SECRET)


def test_the_first_stored_shapes_still_read(cfg, fake):
    cfg.set_secret(si.SSO_SECRET, json.dumps({
        "start_url": START, "region": "us-east-1", "client": {"id": "c", "secret": "s"},
        "token": {"access": "access1", "refresh": "r", "expires_at": int(time.time()) + 3600}}))
    cfg.set_secret(si.KEYS_SECRET, json.dumps({"id": "AKIAOLDOLDOLDOLDOLD0", "secret": "o" * 40}))
    assert si.sso_creds(REC).access_key_id
    assert si.keys_creds({"keys_id": "AKIAOLDOLDOLDOLDOLD0"}).secret_access_key == "o" * 40


def _fake_cli(tmp_path, version="2.33.1", export=None, export_rc=0, err=""):
    log = tmp_path / "calls.log"
    (tmp_path / "bin").mkdir(exist_ok=True)
    p = tmp_path / "bin" / "aws"
    body = f'''#!/bin/sh
echo "$@ | $AWS_CONFIG_FILE | $AWS_LOGIN_CACHE_DIRECTORY" >> {log}
if [ "$1" = "--version" ]; then echo "aws-cli/{version} Python/3.12 Darwin"; exit 0; fi
if [ "$1" = "login" ]; then exit 0; fi
if [ "$1" = "configure" ]; then
  echo '{json.dumps(export or {})}'
  echo "{err}" >&2
  exit {export_rc}
fi
'''
    p.write_text(body)
    p.chmod(0o755)
    return str(p), log


def test_aws_login_needs_a_recent_cli(cfg, tmp_path, monkeypatch):
    cli, _ = _fake_cli(tmp_path, version="2.24.10")
    monkeypatch.setattr(si, "find_aws_cli", lambda: cli)
    assert si.login_support() == {"available": False, "reason": "old_cli", "version": "2.24.10"}
    with pytest.raises(si.SigninError):
        si.login_start("us-east-1")
    monkeypatch.setattr(si, "find_aws_cli", lambda: None)
    assert si.login_support()["reason"] == "no_cli"


def test_aws_login_runs_with_swarmais_own_config(cfg, tmp_path, monkeypatch):
    exp = {"Version": 1, "AccessKeyId": "ASIALOGINKEY000001", "SecretAccessKey": "s" * 40,
           "SessionToken": "t", "Expiration": "2099-01-01T00:00:00Z"}
    cli, log = _fake_cli(tmp_path, export=exp)
    monkeypatch.setattr(si, "find_aws_cli", lambda: cli)
    started = si.login_start("eu-west-1")
    profile = started["profile"]
    assert started["status"] == "running" and si.LOGIN_PROFILE_RE.fullmatch(profile)
    assert profile != si.LOGIN_PROFILE                       # never the current account's
    for _ in range(50):
        if si.login_status()["status"] != "running":
            break
        time.sleep(0.1)
    assert si.login_status() == {"status": "done", "profile": profile}
    assert si.login_status()["status"] == "done"             # reading it does not consume it
    c = si.login_creds({"region": "eu-west-1", "login": {"profile": profile}})
    assert c.access_key_id == "ASIALOGINKEY000001"
    lines = log.read_text().splitlines()
    login = next(l for l in lines if l.startswith("login"))
    assert f"--profile {profile} --region eu-west-1" in login
    export = next(l for l in lines if l.startswith("configure"))
    assert f"--profile {profile} " in export
    assert si.login_start("eu-west-1")["profile"] != profile   # each sign-in its own profile
    aws = str(tmp_path / "aws")
    assert f"| {aws}/login-config | {aws}/login-cache" in login   # not the person's ~/.aws
    assert stat.S_IMODE(os.stat(tmp_path / "aws" / "login-cache").st_mode) == 0o700


def test_an_ended_login_session_means_sign_in_again(cfg, tmp_path, monkeypatch):
    cli, _ = _fake_cli(tmp_path, export_rc=255, err="Your login session has expired")
    monkeypatch.setattr(si, "find_aws_cli", lambda: cli)
    with pytest.raises(aa.NeedsLogin):
        si.login_creds({"region": "us-east-1"})
