"""The SwarmAI AWS account, step 1 (core/aws_account.py, routers/aws_account.py, the
``aws-credentials`` subcommand; design docs/design/2026-10-31-swarmai-aws-account.md).

Questions answered:
1. pin: are credentials of another account refused (never used, state says so), is a
   new access key re-checked and a known one not, is an unpinned upgrade record pinned
   to the first identity STS confirms?
2. ada: are ada's credentials read, a Midway expiry reported as needs-login, any other
   failure named, and a hung ada ended by the hang guard?
3. upgrade: does every legacy configuration become the record design §5.6 says (and
   nothing is guessed when nothing points at an account)?
4. delivery: is the ``swarmai`` profile file private and does its credential_process
   run this backend's subcommand; does the subcommand print the daemon's credentials
   and fail with one line otherwise; does the route answer only the boot token?
5. ownership: can ``PUT /api/settings`` write the record, the chat path or the fleet;
   does the view hold any secret; is the account refused to a non-owner channel?
"""
from __future__ import annotations

import http.server
import json
import os
import shlex
import stat
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from core import aws_account as aa
from core.app_config_manager import AppConfigManager

ACCT = "111122223333"
OTHER = "444455556666"


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    mgr = AppConfigManager(config_path=tmp_path / "config.json")
    monkeypatch.setattr(AppConfigManager, "_instance", mgr)
    aa._reset_cache()
    yield mgr
    aa._reset_cache()


def _creds(akid="ASIAONE", minutes=60):
    return aa.Creds(akid, "secret-" + akid, "token-" + akid,
                    datetime.now(timezone.utc) + timedelta(minutes=minutes))


def _record(cfg, **over):
    rec = {"source": "profile", "profile": "p", "account_id": ACCT, "region": "us-east-1"}
    rec.update(over)
    cfg.update({aa.RECORD_KEY: rec})


@pytest.fixture
def sts(monkeypatch):
    calls = []
    answer = {"Account": ACCT, "Arn": f"arn:aws:sts::{ACCT}:assumed-role/Admin/me"}

    def fake(creds, region):
        calls.append(creds.access_key_id)
        return dict(answer)
    monkeypatch.setattr(aa, "_sts_identity", fake)
    return calls, answer


# ── 1. pin ─────────────────────────────────────────────────────────────────────

def test_another_accounts_credentials_are_refused_and_never_returned(cfg, sts, monkeypatch):
    calls, answer = sts
    answer.update(Account=OTHER, Arn=f"arn:aws:sts::{OTHER}:assumed-role/Admin/me")
    _record(cfg)
    monkeypatch.setitem(aa._RESOLVERS, "profile", lambda rec: _creds())
    with pytest.raises(aa.AccountMismatch) as exc:
        aa.resolve()
    assert (exc.value.expected, exc.value.actual) == (ACCT, OTHER)
    rec = aa.get_record()
    assert rec["state"] == aa.MISMATCH and rec["account_id"] == ACCT
    assert aa._cache is None
    with pytest.raises(aa.AccountMismatch):  # still refused, not cached as good
        aa.resolve()


def test_a_known_key_is_not_rechecked_and_a_new_key_is(cfg, sts, monkeypatch):
    calls, _ = sts
    _record(cfg)
    keys = iter(["ASIAONE", "ASIATWO"])
    monkeypatch.setitem(aa._RESOLVERS, "profile", lambda rec: _creds(next(keys)))
    assert aa.resolve().access_key_id == "ASIAONE"
    assert aa.resolve().access_key_id == "ASIAONE"          # cached, fresh
    assert calls == ["ASIAONE"]
    aa._cache = _creds("ASIAONE", minutes=1)               # about to expire
    assert aa.resolve().access_key_id == "ASIATWO"
    assert calls == ["ASIAONE", "ASIATWO"]
    assert aa.get_record()["state"] == aa.CONNECTED


def test_an_unpinned_upgrade_record_is_pinned_to_the_first_identity(cfg, sts, monkeypatch):
    _record(cfg, account_id=None)
    monkeypatch.setitem(aa._RESOLVERS, "profile", lambda rec: _creds())
    aa.resolve()
    assert aa.get_record()["account_id"] == ACCT


def test_unsupported_source_and_no_record_are_named(cfg):
    with pytest.raises(aa.AccountError) as exc:
        aa.resolve()
    assert exc.value.kind == aa.UNCONNECTED
    _record(cfg, source="bogus")
    with pytest.raises(aa.AccountError) as exc:
        aa.resolve()
    assert exc.value.kind == "unsupported"


# ── 2. ada ─────────────────────────────────────────────────────────────────────

def _fake_ada(tmp_path, body: str) -> str:
    p = tmp_path / "ada"
    p.write_text("#!/bin/sh\n" + body + "\n")
    p.chmod(0o755)
    return str(p)


@pytest.mark.skipif(sys.platform == "win32", reason="shell script stand-in for ada")
def test_ada_credentials_are_read_and_failures_classified(cfg, tmp_path, monkeypatch):
    exp = "2030-01-01T00:00:00Z"
    good = _fake_ada(tmp_path, "echo '{\"Version\":1,\"AccessKeyId\":\"ASIAADA\","
                     "\"SecretAccessKey\":\"s\",\"SessionToken\":\"t\",\"Expiration\":\"" + exp + "\"}'")
    rec = {"account_id": ACCT, "ada": {"role": "Admin", "provider": "isengard", "bin": good}}
    c = aa._ada_creds(rec)
    assert (c.access_key_id, c.session_token) == ("ASIAADA", "t")
    assert c.expiration == datetime(2030, 1, 1, tzinfo=timezone.utc)

    rec["ada"]["bin"] = _fake_ada(tmp_path, "echo 'Midway cookie expired, run mwinit -f' >&2; exit 1")
    with pytest.raises(aa.NeedsLogin):
        aa._ada_creds(rec)
    rec["ada"]["bin"] = _fake_ada(tmp_path, "echo 'no such role Admin' >&2; exit 1")
    with pytest.raises(aa.AccountError) as exc:
        aa._ada_creds(rec)
    assert exc.value.kind == "ada_failed" and "no such role" in exc.value.message
    rec["ada"]["bin"] = str(tmp_path / "missing-ada")
    with pytest.raises(aa.AccountError) as exc:
        aa._ada_creds(rec)
    assert exc.value.kind == "ada_not_found"


@pytest.mark.skipif(sys.platform == "win32", reason="shell script stand-in for ada")
def test_a_hung_ada_is_ended_by_the_hang_guard(cfg, tmp_path, monkeypatch):
    monkeypatch.setattr(aa, "_ADA_TIMEOUT_S", 1)
    rec = {"account_id": ACCT, "ada": {"role": "Admin", "provider": "isengard",
                                       "bin": _fake_ada(tmp_path, "sleep 30")}}
    with pytest.raises(aa.AccountError) as exc:
        aa._ada_creds(rec)
    assert exc.value.kind == "ada_timeout"


# ── 3. upgrade (design §5.6) ───────────────────────────────────────────────────

@pytest.fixture
def user_aws(tmp_path, monkeypatch):
    conf, creds = tmp_path / "aws-config", tmp_path / "aws-credentials"
    conf.write_text("")
    creds.write_text("")
    monkeypatch.setenv("AWS_CONFIG_FILE", str(conf))
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(creds))
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    monkeypatch.delenv("AWS_DEFAULT_PROFILE", raising=False)
    monkeypatch.delenv("SWARMAI_MODE", raising=False)
    monkeypatch.setattr(aa, "find_ada", lambda: None)
    return conf, creds


def test_legacy_ada_comes_from_ada_profile_print(cfg, user_aws, monkeypatch):
    monkeypatch.setattr(aa, "find_ada", lambda: "/opt/ada")
    monkeypatch.setattr(aa, "_ada_profile_print",
                        lambda b: {"account": ACCT, "role": "PowerUser", "provider": "Conduit"})
    rec, via = aa.derive_from_legacy({"auth_method": "ada", "aws_region": "us-west-2"})
    assert rec["source"] == "ada" and rec["account_id"] == ACCT
    assert rec["ada"] == {"role": "PowerUser", "provider": "conduit", "bin": "/opt/ada"}
    assert rec["region"] == "us-west-2" and rec["state"] == aa.UNVERIFIED and via == "aws"


def test_legacy_default_profile_running_ada_becomes_an_ada_record(cfg, user_aws, monkeypatch):
    conf, _ = user_aws
    conf.write_text("[default]\ncredential_process = /opt/ada credentials print "
                    f"--account={ACCT} --role=Admin --provider=isengard\n")
    monkeypatch.setattr(aa, "find_ada", lambda: "/opt/ada")
    monkeypatch.setattr(aa, "_same_file", lambda a, b: a == b)
    rec, _ = aa.derive_from_legacy({"auth_method": "sso"})
    assert rec["source"] == "ada" and rec["account_id"] == ACCT and rec["ada"]["role"] == "Admin"


def test_legacy_profile_records_take_the_account_from_the_config(cfg, user_aws, monkeypatch):
    conf, creds = user_aws
    conf.write_text(f"[profile corp]\nsso_account_id = {ACCT}\nsso_role_name = Dev\n"
                    f"[profile assumed]\nrole_arn = arn:aws:iam::{OTHER}:role/X\n")
    monkeypatch.setenv("AWS_PROFILE", "corp")
    rec, _ = aa.derive_from_legacy({"auth_method": "sso"})
    assert (rec["source"], rec["profile"], rec["account_id"]) == ("profile", "corp", ACCT)
    monkeypatch.setenv("AWS_PROFILE", "assumed")
    assert aa.derive_from_legacy({})[0]["account_id"] == OTHER
    # static keys: the account is unknown until STS pins it
    creds.write_text("[keysonly]\naws_access_key_id = AKIA\naws_secret_access_key = x\n")
    monkeypatch.setenv("AWS_PROFILE", "keysonly")
    rec, _ = aa.derive_from_legacy({})
    assert rec["source"] == "profile" and rec["account_id"] is None


def test_legacy_keys_become_the_chat_path_and_nothing_is_guessed(cfg, user_aws):
    assert aa.derive_from_legacy({"auth_method": "apikey", "use_bedrock": False}) == (None, "anthropic")
    assert aa.derive_from_legacy({"auth_method": "bedrock_api_key"}) == (None, "bedrock_key")
    assert aa.derive_from_legacy({}) == (None, "aws")


def test_hive_mode_records_the_instance_role(cfg, user_aws, monkeypatch):
    monkeypatch.setenv("SWARMAI_MODE", "hive")
    rec, via = aa.derive_from_legacy({"aws_region": "us-west-2"})
    assert rec["source"] == "instance_role" and rec["account_id"] is None and via == "aws"


def test_ensure_record_keeps_an_existing_record(cfg, user_aws):
    _record(cfg, account_id=OTHER)
    aa.ensure_record(cfg)
    assert aa.get_record()["account_id"] == OTHER


# ── 4. delivery ────────────────────────────────────────────────────────────────

def test_the_profile_file_is_private_and_runs_this_backends_subcommand(cfg, tmp_path):
    path = aa.write_profile_file("us-west-2")
    assert path == Path(os.environ["SWARM_DATA_DIR"]) / "aws" / "config"
    if sys.platform != "win32":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    import configparser
    cp = configparser.RawConfigParser()
    cp.read(path)
    sec = cp["profile swarmai"]
    assert sec["region"] == "us-west-2"
    assert shlex.split(sec["credential_process"]) == aa.backend_command()
    assert aa.backend_command()[-1] == "aws-credentials"
    assert aa.write_profile_file("not a region; rm -rf /").read_text().count("us-east-1") == 1


def test_process_json_is_the_credential_process_format():
    c = aa.Creds("AK", "SK", "TK", datetime(2030, 1, 2, 3, 4, 5, tzinfo=timezone.utc))
    assert c.process_json() == {"Version": 1, "AccessKeyId": "AK", "SecretAccessKey": "SK",
                                "SessionToken": "TK", "Expiration": "2030-01-02T03:04:05Z"}
    assert "Expiration" not in aa.Creds("AK", "SK", None, None).process_json()


def test_the_credentials_route_answers_only_the_boot_token(cfg, sts, monkeypatch):
    from fastapi.testclient import TestClient
    from main import app
    _record(cfg)
    monkeypatch.setitem(aa._RESOLVERS, "profile", lambda rec: _creds("ASIAROUTE"))
    aa.write_daemon_endpoint(18321)
    token = json.loads(aa.daemon_endpoint_file().read_text())["token"]
    client = TestClient(app)
    assert client.get("/api/aws-account/credentials").status_code == 403
    assert client.get("/api/aws-account/credentials",
                      headers={"X-Swarm-Credentials-Token": "x" + token}).status_code == 403
    ok = client.get("/api/aws-account/credentials", headers={"X-Swarm-Credentials-Token": token})
    assert ok.status_code == 200 and ok.json()["AccessKeyId"] == "ASIAROUTE"
    _, answer = sts
    answer.update(Account=OTHER)
    aa._reset_cache()
    aa.write_daemon_endpoint(18321)
    token = json.loads(aa.daemon_endpoint_file().read_text())["token"]
    bad = client.get("/api/aws-account/credentials", headers={"X-Swarm-Credentials-Token": token})
    assert bad.status_code == 409 and bad.json()["error"] == "account_mismatch"


class _Stub(http.server.BaseHTTPRequestHandler):
    reply: "tuple[int, dict]" = (200, {})
    seen: list = []

    def do_GET(self):  # noqa: N802
        type(self).seen.append((self.path, self.headers.get("X-Swarm-Credentials-Token")))
        code, body = type(self).reply
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


@pytest.fixture
def stub_daemon():
    srv = http.server.HTTPServer(("127.0.0.1", 0), _Stub)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    _Stub.seen = []
    (Path(os.environ["SWARM_DATA_DIR"]) / "aws").mkdir(parents=True, exist_ok=True)
    (Path(os.environ["SWARM_DATA_DIR"]) / "aws" / "daemon.json").write_text(
        json.dumps({"port": srv.server_address[1], "token": "tok"}))
    yield _Stub
    srv.shutdown()


def test_the_subcommand_prints_the_daemons_credentials(stub_daemon, capsys):
    from desktop_main import main
    body = {"Version": 1, "AccessKeyId": "AK", "SecretAccessKey": "SK", "SessionToken": "TK"}
    stub_daemon.reply = (200, body)
    assert main(["aws-credentials"]) == 0
    assert json.loads(capsys.readouterr().out) == body
    assert stub_daemon.seen == [("/api/aws-account/credentials", "tok")]


def test_the_subcommand_fails_with_one_line(stub_daemon, capsys, tmp_path, monkeypatch):
    from desktop_main import main
    stub_daemon.reply = (409, {"error": "account_mismatch", "message": "wrong account 444"})
    assert main(["aws-credentials"]) == 1
    err = capsys.readouterr()
    assert err.out == "" and "wrong account 444" in err.err
    monkeypatch.setenv("SWARM_DATA_DIR", str(tmp_path / "nowhere"))
    assert main(["aws-credentials"]) == 1
    assert "not running" in capsys.readouterr().err


# ── 5. ownership ───────────────────────────────────────────────────────────────

def test_settings_put_cannot_write_the_account_chat_or_fleet(cfg):
    from fastapi.testclient import TestClient
    from main import app
    from routers import settings as settings_router
    _record(cfg)
    settings_router._config_manager = cfg
    try:
        r = TestClient(app).put("/api/settings", json={
            aa.RECORD_KEY: {"source": "keys", "account_id": OTHER},
            aa.CHAT_KEY: {"via": "anthropic"}, aa.HIVE_FLEET_KEY: {"desired_count": 99}})
        assert r.status_code == 200
    finally:
        settings_router._config_manager = None
    assert aa.get_record()["account_id"] == ACCT
    assert cfg.get(aa.CHAT_KEY) is None and cfg.get(aa.HIVE_FLEET_KEY) is None
    assert not (aa.PROTECTED_CONFIG_KEYS & settings_router.WRITABLE_KEYS)
    # also once someone gives one of them a default
    with_defaults = {**settings_router.DEFAULT_CONFIG, **{k: {} for k in aa.PROTECTED_CONFIG_KEYS}}
    assert not (aa.PROTECTED_CONFIG_KEYS & settings_router.writable_keys(with_defaults))


def test_the_view_holds_no_secret(cfg, sts, monkeypatch):
    _record(cfg, source="ada", ada={"role": "Admin", "provider": "isengard", "bin": "/opt/ada"})
    monkeypatch.setitem(aa._RESOLVERS, "ada", lambda rec: _creds("ASIAVIEW"))
    aa.resolve()
    text = json.dumps(aa.view())
    assert "secret-ASIAVIEW" not in text and "token-ASIAVIEW" not in text
    assert "/opt/ada" not in text
    v = aa.view()
    assert v["state"] == aa.CONNECTED and v["account"]["account_id"] == ACCT


@pytest.mark.parametrize("cmd", [
    "curl http://127.0.0.1:18321/api/aws-account",
    "curl http://127.0.0.1:18321/api//aws-account/credentials",
    "\"$SWARM_BACKEND_BIN\" aws-credentials",
    "cat ~/.swarm-ai/aws/daemon.json",
])
def test_a_non_owner_channel_is_refused_the_account(cmd):
    from core.security_hooks import owner_command_denial
    assert owner_command_denial(cmd) == "the SwarmAI AWS account is only available to the owner"


# ── review fixes (expert review, round 1) ──────────────────────────────────────

def test_a_failed_attempt_does_not_stick_once_the_same_key_works(cfg, sts, monkeypatch):
    _record(cfg)
    outcomes = iter([_creds("ASIAONE"), aa.AccountError("ada_timeout", "ada did not answer"),
                     _creds("ASIAONE")])

    def resolver(rec):
        o = next(outcomes)
        if isinstance(o, Exception):
            raise o
        return o
    monkeypatch.setitem(aa._RESOLVERS, "profile", resolver)
    aa.resolve()
    aa._cache = None  # the cached key went stale; ada then times out once
    with pytest.raises(aa.AccountError):
        aa.resolve()
    assert aa.get_record()["state"] == aa.UNVERIFIED
    aa.resolve()  # ada answers again with the key verified before
    assert aa.get_record()["state"] == aa.CONNECTED and aa.get_record()["state_detail"] is None


def test_a_changed_record_drops_the_pin_and_rechecks(cfg, sts, monkeypatch):
    calls, answer = sts
    _record(cfg)
    monkeypatch.setitem(aa._RESOLVERS, "profile", lambda rec: _creds("ASIASAME"))
    aa.resolve()
    _record(cfg, account_id=OTHER)  # e.g. a hand edit; the same key keeps coming back
    with pytest.raises(aa.AccountMismatch):
        aa.resolve()
    assert calls == ["ASIASAME", "ASIASAME"]


@pytest.mark.skipif(sys.platform == "win32", reason="shell script stand-in for ada")
def test_ada_refuses_record_values_that_could_be_flags(cfg, tmp_path):
    ada = _fake_ada(tmp_path, "echo should-not-run; exit 3")
    for bad in ({"account_id": "--profile=x"}, {"account_id": "12345"},
                {"ada": {"role": "--profile=x", "provider": "isengard", "bin": ada}},
                {"ada": {"role": "Admin", "provider": "-x", "bin": ada}}):
        rec = {"account_id": ACCT, "ada": {"role": "Admin", "provider": "isengard", "bin": ada}}
        rec.update(bad)
        with pytest.raises(aa.AccountError) as exc:
            aa._ada_creds(rec)
        assert exc.value.kind == "bad_record", bad
    other = tmp_path / "not-ada"
    other.write_text("#!/bin/sh\nexit 0\n")
    other.chmod(0o755)
    with pytest.raises(aa.AccountError) as exc:
        aa._ada_creds({"account_id": ACCT, "ada": {"role": "Admin", "provider": "isengard",
                                                   "bin": str(other)}})
    assert exc.value.kind == "ada_not_found"


@pytest.mark.skipif(sys.platform == "win32", reason="shell script stand-in for ada")
def test_ada_credentials_without_an_expiry_are_refused(cfg, tmp_path):
    ada = _fake_ada(tmp_path, "echo '{\"Version\":1,\"AccessKeyId\":\"A\",\"SecretAccessKey\":\"s\"}'")
    with pytest.raises(aa.AccountError) as exc:
        aa._ada_creds({"account_id": ACCT, "ada": {"role": "Admin", "provider": "isengard", "bin": ada}})
    assert "expiry" in exc.value.message


def test_swarmais_own_profile_is_never_adopted_or_resolved(cfg, user_aws, monkeypatch):
    conf, _ = user_aws
    conf.write_text("[profile swarmai]\ncredential_process = \"/x/python-backend\" \"aws-credentials\"\n"
                    "[profile wrapper]\ncredential_process = /x/python-backend aws-credentials\n")
    for name in ("swarmai", "wrapper"):
        monkeypatch.setenv("AWS_PROFILE", name)
        assert aa.derive_from_legacy({})[0] is None
    with pytest.raises(aa.AccountError) as exc:
        aa._profile_creds({"profile": "swarmai"})
    assert exc.value.kind == "bad_record"


class _Boom(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.response = {"Error": {"Code": code}}


def test_only_a_denial_is_reported_as_missing():
    assert aa._verdict(_Boom("AccessDeniedException"), "bedrock:InvokeModel")["status"] == "missing"
    assert aa._verdict(_Boom("ValidationException"), "bedrock:InvokeModel")["status"] == "unknown"
    assert aa._verdict(ConnectionError("down"), "s3:ListAllMyBuckets")["status"] == "unknown"


def test_no_token_and_odd_tokens_are_refused(cfg):
    from fastapi.testclient import TestClient
    from main import app
    aa._token = None  # the backend that does not own backend.json, or a Hive
    client = TestClient(app)
    assert client.get("/api/aws-account/credentials",
                      headers={"X-Swarm-Credentials-Token": "anything"}).status_code == 403
    aa.write_daemon_endpoint(18321)
    # a header Starlette decodes as latin-1 into non-ASCII text: refused, not a crash
    assert aa.token_ok("caf\u00e9") is False


def test_startup_writes_the_endpoint_even_when_the_record_cannot_be_made(cfg, monkeypatch):
    monkeypatch.delenv("SWARMAI_MODE", raising=False)
    monkeypatch.setattr(aa, "ensure_record", lambda c: (_ for _ in ()).throw(RuntimeError("bad config")))
    aa.startup(18321)
    assert json.loads(aa.daemon_endpoint_file().read_text())["port"] == 18321
    assert aa.profile_file().is_file()


def test_a_word_a_shell_would_expand_is_never_written():
    with pytest.raises(ValueError):
        aa._quote("/tmp/$(id)/python-backend")
    with pytest.raises(ValueError):
        aa._quote('/tmp/a"b')


def test_the_channel_denial_is_not_too_wide():
    from core.security_hooks import owner_command_denial
    assert owner_command_denial("grep configure-aws-credentials .github/workflows/ci.yml") is None
    assert owner_command_denial("cd ~/.swarm-ai && cat aws/daemon.json")
