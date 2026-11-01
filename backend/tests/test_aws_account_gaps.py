"""The last gaps of the SwarmAI account design (2026-10-31-swarmai-aws-account §5.4, §7.2,
§9 6).

Questions answered:
1. one main region: the account's sessions, its capability checks, voice (Polly and
   Transcribe), the eval judges and a job's sign-in pre-check all use the region the
   person chose (``aws_region``), not the record's own copy or a hard-coded one; without
   an account each keeps its old default.
2. jobs never guess credentials from ~/.aws/sso/cache: without an account and without
   a default chain they get nothing.
3. the voice check confirms Polly and transcription; a refused Transcribe is named.
4. the default VPC is created only when the region has none, and a refusal is "missing".
5. a bucket name another account took is reported as such.
6. no SwarmAI sign-in, key or AWS login cache goes into a backup.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from core import aws_account as aa
from core.app_config_manager import AppConfigManager

ACCT = "111122223333"


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    mgr = AppConfigManager(config_path=tmp_path / "config.json")
    monkeypatch.setattr(AppConfigManager, "_instance", mgr)
    monkeypatch.delenv("SWARMAI_MODE", raising=False)
    aa._reset_cache()
    yield mgr
    aa._reset_cache()


@pytest.fixture
def account(cfg, monkeypatch):
    cfg.update({aa.RECORD_KEY: {"source": "ada", "account_id": ACCT, "region": "us-east-1",
                                "caller_arn": f"arn:aws:sts::{ACCT}:assumed-role/Admin/me",
                                "ada": {"role": "Admin", "provider": "isengard", "bin": "/x/ada"}},
                "aws_region": "eu-west-1"})            # the person changed it in AI & Models
    monkeypatch.setitem(aa._RESOLVERS, "ada", lambda rec: aa.Creds(
        "ASIAONE", "s", "t", datetime.now(timezone.utc) + timedelta(hours=1)))
    monkeypatch.setattr(aa, "_sts_identity", lambda c, r: {"Account": ACCT, "Arn": "arn"})


# ── 1. one main region ────────────────────────────────────────────────────────

def test_the_accounts_session_uses_the_main_region(account):
    assert aa.session().region_name == "eu-west-1"
    assert aa.session("ap-south-1").region_name == "ap-south-1"   # an explicit region wins


def test_the_capability_checks_run_in_the_main_region(account, monkeypatch):
    seen = []
    real = aa.session
    monkeypatch.setattr(aa, "session", lambda region=None: seen.append(region) or real(region))
    from core import aws_permissions
    monkeypatch.setattr(aws_permissions, "run_hive_checks",
                        lambda sess, region, a, c: ({"status": "ok"}, {"status": "ok"}))
    monkeypatch.setattr(aa, "_voice_check", lambda sess, rec: {"status": "ok"})
    monkeypatch.setattr(aa, "get_chat_via", lambda config=None: "anthropic")
    out = aa.run_capability_checks(AppConfigManager.instance())
    assert seen[0] == "eu-west-1" and out["hive"]["region"] == "eu-west-1"


def test_voice_and_the_eval_judges_use_the_main_region(account, monkeypatch):
    from core import voice_synthesize as vs
    from scripts import eval_runner
    monkeypatch.setenv("AWS_REGION", "us-west-2")
    assert vs.voice_region() == "eu-west-1"
    assert eval_runner._judge_region() == "eu-west-1"
    vs._polly_client_for.cache_clear()
    assert vs._get_polly_client().meta.region_name == "eu-west-1"
    vs._polly_client_for.cache_clear()


def test_transcribe_streams_in_the_main_region(account, monkeypatch):
    from core import voice_transcribe as vt
    seen = {}

    class Stop(Exception):
        pass

    class Client:
        def __init__(self, region, credential_resolver=None):
            seen["region"] = region
            raise Stop()
    monkeypatch.setattr(vt, "TranscribeStreamingClient", Client)
    with pytest.raises(Stop):
        asyncio.run(vt._transcribe_with_streaming(b"\0\0", language="en-US"))
    assert seen["region"] == "eu-west-1"


def test_without_an_account_each_keeps_its_old_default(cfg, monkeypatch):
    from core import voice_synthesize as vs
    from scripts import eval_runner
    monkeypatch.setenv("AWS_REGION", "us-west-2")
    assert vs.voice_region() == vs.DEFAULT_REGION
    assert eval_runner._judge_region() == "us-west-2"


def test_a_jobs_sign_in_pre_check_uses_the_main_region(account, monkeypatch):
    from jobs import executor
    seen = {}

    class R:
        returncode = 0
        stdout = '{"loggedIn": true}'
        stderr = ""
    monkeypatch.delenv("AWS_REGION", raising=False)
    monkeypatch.setattr(executor.subprocess, "run",
                        lambda *a, **kw: seen.update(region=kw["env"].get("AWS_REGION")) or R())
    executor._check_claude_auth("/bin/claude")
    assert seen["region"] == "eu-west-1"


# ── 2. no guessing from the SSO cache ─────────────────────────────────────────

def test_jobs_never_read_the_sso_cache(cfg, tmp_path, monkeypatch):
    import boto3

    import jobs.bedrock as jb
    monkeypatch.setattr(boto3, "Session", lambda *a, **kw: type("S", (), {"get_credentials": lambda self: None})())
    read = []
    from pathlib import Path
    cache = tmp_path / ".aws" / "sso" / "cache"
    cache.mkdir(parents=True)
    (cache / "token.json").write_text('{"accessToken": "t", "expiresAt": "2999-01-01T00:00:00Z"}')
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    real_glob = Path.glob

    def spy(self, pattern):
        read.append(str(self))
        return real_glob(self, pattern)
    monkeypatch.setattr(Path, "glob", spy)
    assert jb._resolve_credentials() == {}
    assert not any("sso" in p for p in read)


# ── 3. voice ──────────────────────────────────────────────────────────────────

class _Err(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.response = {"Error": {"Code": code, "Message": code}}


def _sess(denied=(), simulate_error=None, polly_error=None):
    class C:
        def describe_voices(self, **kw):
            if polly_error:
                raise _Err(polly_error)
            return {}

        def get_role(self, RoleName):
            return {"Role": {"Arn": f"arn:aws:iam::{ACCT}:role/{RoleName}"}}

        def simulate_principal_policy(self, **kw):
            if simulate_error:
                raise _Err(simulate_error)
            return {"EvaluationResults": [
                {"EvalActionName": a, "EvalDecision": "implicitDeny" if a in denied else "allowed"}
                for a in kw["ActionNames"]]}

    class S:
        def client(self, *a, **kw):
            return C()
    return S()


REC = {"caller_arn": f"arn:aws:sts::{ACCT}:assumed-role/Admin/me"}


def test_voice_confirms_polly_and_transcription():
    assert aa._voice_check(_sess(), REC)["status"] == "ok"
    v = aa._voice_check(_sess(denied=["transcribe:StartStreamTranscription"]), REC)
    assert v["status"] == "missing" and v["missing"] == ["transcribe:StartStreamTranscription"]
    assert aa._voice_check(_sess(simulate_error="AccessDenied"), REC)["status"] == "unknown"
    assert aa._voice_check(_sess(polly_error="AccessDeniedException"), REC)["status"] == "missing"


# ── 4. the default VPC ────────────────────────────────────────────────────────

def test_the_default_vpc_is_created_only_when_there_is_none(account, monkeypatch):
    calls = []

    class Ec2:
        def __init__(self, existing):
            self.existing = existing

        def describe_vpcs(self, **kw):
            return {"Vpcs": [{"VpcId": "vpc-old"}] if self.existing else []}

        def create_default_vpc(self):
            calls.append("create")
            return {"Vpc": {"VpcId": "vpc-new"}}

    def sess(existing):
        class S:
            def client(self, *a, **kw):
                return Ec2(existing)
        return lambda region=None: S()
    monkeypatch.setattr(aa, "session", sess(True))
    assert aa.create_default_vpc("eu-west-1") == {"vpc_id": "vpc-old", "created": False}
    assert calls == []
    monkeypatch.setattr(aa, "session", sess(False))
    assert aa.create_default_vpc("eu-west-1") == {"vpc_id": "vpc-new", "created": True}
    with pytest.raises(aa.AccountError):
        aa.create_default_vpc("mars-1")


def test_a_refused_default_vpc_is_missing(account, monkeypatch):
    class Ec2:
        def describe_vpcs(self, **kw):
            return {"Vpcs": []}

        def create_default_vpc(self):
            raise _Err("UnauthorizedOperation")

    class S:
        def client(self, *a, **kw):
            return Ec2()
    monkeypatch.setattr(aa, "session", lambda region=None: S())
    with pytest.raises(aa.AccountError) as exc:
        aa.create_default_vpc("eu-west-1")
    assert exc.value.kind == "missing"

    class Ec2NoRead(Ec2):
        def describe_vpcs(self, **kw):
            raise _Err("UnauthorizedOperation")
    monkeypatch.setattr(aa, "session", lambda region=None: type("S2", (), {"client": lambda self, *a, **kw: Ec2NoRead()})())
    with pytest.raises(aa.AccountError) as exc:
        aa.create_default_vpc("eu-west-1")
    assert exc.value.kind == "missing"


@pytest.mark.asyncio
async def test_the_route_is_the_persons_and_needs_the_app_header():
    from httpx import ASGITransport, AsyncClient

    from core import swarm_out_ops
    from main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        assert (await c.post("/api/aws-account/default-vpc", json={"region": "eu-west-1"})).status_code == 403
    op = next(o for o in swarm_out_ops._STATIC if o.name == "aws_account.default_vpc")
    assert op.weight == "off_machine" and op.callers == frozenset({swarm_out_ops.PERSON})


@pytest.mark.asyncio
async def test_the_route_is_refused_on_a_hive_and_to_the_agent(monkeypatch):
    from fastapi import HTTPException

    from core.run_owner_gate import _user_only_deny
    from routers import aws_account as route
    monkeypatch.setenv("SWARMAI_MODE", "hive")
    called = []
    monkeypatch.setattr(aa, "create_default_vpc", lambda r: called.append(r))
    with pytest.raises(HTTPException) as exc:     # the route itself, behind the Hive's own auth
        await route.default_vpc(route.DefaultVpc(region="eu-west-1"), user_action="1")
    assert exc.value.status_code == 403 and called == []
    assert _user_only_deny("curl -X POST -H 'X-Swarm-User-Action: 1' "
                           "http://127.0.0.1:18321/api/aws-account/default-vpc -d '{}'")


# ── 5. a bucket name another account took ─────────────────────────────────────

@pytest.mark.asyncio
async def test_a_bucket_name_taken_elsewhere_is_reported(tmp_path):
    from unittest.mock import MagicMock

    from hive.provisioner import HiveProvisioner

    class Taken(Exception):
        pass
    s3 = MagicMock()
    s3.exceptions.BucketAlreadyOwnedByYou = Taken
    s3.create_bucket.side_effect = Exception("An error occurred (BucketAlreadyExists)")
    sess = MagicMock()
    sess.client.return_value = s3
    with pytest.raises(RuntimeError, match="taken by another AWS account"):
        await HiveProvisioner(tmp_path / "x.db")._ensure_s3_bucket(sess, "eu-west-1", ACCT)
    s3.put_public_access_block.assert_not_called()


# ── 6. backups ────────────────────────────────────────────────────────────────

def test_no_sign_in_material_goes_into_a_backup():
    from core import backup_manager
    from core.aws_signin import KEYS_SECRET, SSO_SECRET
    assert not {"config.json", "secrets.json"} & set(backup_manager.CONFIG_FILES)
    assert not any(f.startswith("aws") for f in backup_manager.CONFIG_FILES)   # aws/ login cache
    from core.app_config_manager import SECRET_KEYS
    assert {SSO_SECRET, KEYS_SECRET} <= set(SECRET_KEYS)          # never in config.json


def test_the_permission_template_lets_the_person_create_the_default_vpc():
    from core import aws_permissions as ap
    stmts = ap.template(ACCT)["policy"]["Statement"]
    assert any("ec2:CreateDefaultVpc" in st["Action"] for st in stmts if st["Effect"] == "Allow")


def test_a_default_vpc_made_meanwhile_is_not_an_error(account, monkeypatch):
    seen = []

    class Ec2:
        def describe_vpcs(self, **kw):
            seen.append("describe")
            return {"Vpcs": [] if len(seen) == 1 else [{"VpcId": "vpc-console"}]}

        def create_default_vpc(self):
            raise _Err("DefaultVpcAlreadyExists")
    monkeypatch.setattr(aa, "session", lambda region=None: type("S", (), {"client": lambda self, *a, **kw: Ec2()})())
    assert aa.create_default_vpc("eu-west-1") == {"vpc_id": "vpc-console", "created": False}


@pytest.mark.asyncio
async def test_the_route_answers_a_sign_in_problem_with_its_own_status(monkeypatch):
    from fastapi import HTTPException

    from routers import aws_account as route
    monkeypatch.delenv("SWARMAI_MODE", raising=False)

    def expired(region):
        raise aa.AccountError("needs_login", "Midway has expired")
    monkeypatch.setattr(aa, "create_default_vpc", expired)
    with pytest.raises(HTTPException) as exc:
        await route.default_vpc(route.DefaultVpc(region="eu-west-1"), user_action="1")
    assert exc.value.status_code == 401


def test_bedrock_jobs_use_the_main_region(account, cfg):
    import jobs.bedrock as jb
    assert jb._load_config()[0] == "eu-west-1"
    # no aws_region yet: the record's region, as everywhere else (not a us-east-1 default)
    cfg.update({"aws_region": None, aa.RECORD_KEY: {**aa.get_record(), "region": "ap-northeast-1"}})
    assert jb._load_config()[0] == "ap-northeast-1"


def test_bedrock_jobs_without_an_account_keep_their_region(cfg):
    import jobs.bedrock as jb
    cfg.update({"aws_region": "ap-south-1"})
    assert jb._load_config()[0] == "ap-south-1"
