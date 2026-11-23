"""The elastic Hive's provisioning (design docs/design/2026-11-20-swarm-out-multi-carrier.md
§6.8; requirements R6.1-R6.3 of run_fd94fed6). No network: boto3 clients are fakes.

Questions:
- do CreateAgentRuntime and UpdateAgentRuntime both carry the size (minimumMemoryInMiB
  8192), platform V2, network PUBLIC, protocol HTTP, ports 8080/8081 and the lifecycle, and
  is that request one this SDK's model accepts;
- is turning on refused, with nothing created, when no image is configured, the image is
  not one this account can pull, or the SDK cannot carry the size;
- is the image a public ECR one used as it is (no ECR permission on the role), a private
  one only this account's in this region (the role pulls exactly that repository);
- does the role trust only AgentCore for this account's runtimes in this region;
- is a runtime of the same name reused, a not-yet-assumable new role waited for, and does
  turning off delete the runtime and the row but not the bucket;
- does the elastic row stay out of MAX_HIVES, the EC2 Hive list and the EC2 delete;
- does the carrier registry offer the elastic Hive.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path

import pytest
from botocore.exceptions import ClientError

from core.carriers import agentcore as ac
from hive import accounts as hive_accounts
from hive import agentcore_provisioner as ep

ACCOUNT = "123456789012"
REGION = "us-east-1"
PUBLIC_IMAGE = "public.ecr.aws/swarmai/swarmai-hive"
PRIVATE_IMAGE = f"{ACCOUNT}.dkr.ecr.{REGION}.amazonaws.com/swarmai-hive"
RUNTIME_ID = "swarmai_elastic_hive-AbC123"
RUNTIME_ARN = f"arn:aws:bedrock-agentcore:{REGION}:{ACCOUNT}:runtime/{RUNTIME_ID}"
BUCKET = f"swarmai-hive-{ACCOUNT}-{REGION}"


def _err(code, msg=None, op="Op"):
    return ClientError({"Error": {"Code": code, "Message": msg or code}}, op)


class FakeSts:
    def get_caller_identity(self):
        return {"Account": ACCOUNT}


class FakeIam:
    def __init__(self, world):
        self.w = world
        self.roles: dict[str, dict] = {}
        self.policies: dict[tuple, dict] = {}

    def get_policy(self, PolicyArn):
        raise _err("NoSuchEntity")

    def create_role(self, RoleName, AssumeRolePolicyDocument, **kw):
        self.w.calls.append(("create_role", RoleName))
        if RoleName in self.roles:
            raise _err("EntityAlreadyExists")
        self.roles[RoleName] = {"trust": json.loads(AssumeRolePolicyDocument), **kw}
        return {"Role": {"Arn": f"arn:aws:iam::{ACCOUNT}:role/{RoleName}"}}

    def get_role(self, RoleName):
        r = self.roles[RoleName]
        return {"Role": {"RoleName": RoleName, "Tags": r.get("Tags") or []}}

    def list_attached_role_policies(self, RoleName):
        return {"AttachedPolicies": [{"PolicyName": n} for n in self.roles[RoleName].get("attached", [])]}

    def list_role_policies(self, RoleName):
        return {"PolicyNames": [n for (r, n) in self.policies if r == RoleName] + self.roles[RoleName].get("inline", [])}

    def update_assume_role_policy(self, RoleName, PolicyDocument):
        self.w.calls.append(("update_trust", RoleName))
        self.roles[RoleName]["trust"] = json.loads(PolicyDocument)

    def put_role_policy(self, RoleName, PolicyName, PolicyDocument):
        self.w.calls.append(("put_role_policy", RoleName))
        self.policies[(RoleName, PolicyName)] = json.loads(PolicyDocument)


class FakeS3:
    def __init__(self, world):
        self.w = world
        self.buckets: set[str] = set()

    def head_bucket(self, Bucket, ExpectedBucketOwner=None):
        if Bucket not in self.buckets:
            raise _err("404")
        return {}

    def create_bucket(self, Bucket, **kw):
        self.w.calls.append(("create_bucket", Bucket))
        self.buckets.add(Bucket)

    def put_public_access_block(self, Bucket, PublicAccessBlockConfiguration, **kw):
        self.w.calls.append(("block_public", Bucket))
        assert all(PublicAccessBlockConfiguration.values())

    def __getattr__(self, name):   # any other S3 call (a delete) is a failure of the test
        raise AssertionError(f"unexpected S3 call {name}")


class FakeControl:
    def __init__(self, world):
        self.w = world
        self.runtimes: dict[str, dict] = {}
        self.creates: list[dict] = []
        self.updates: list[dict] = []
        self.deletes: list[str] = []
        self.create_errors: list = []

    def list_agent_runtimes(self, **kw):
        return {"agentRuntimes": [{"agentRuntimeName": r["name"], "agentRuntimeId": rid}
                                  for rid, r in self.runtimes.items()]}

    def list_tags_for_resource(self, resourceArn):
        rid = resourceArn.rsplit("/", 1)[1]
        assert resourceArn == f"arn:aws:bedrock-agentcore:{REGION}:{ACCOUNT}:runtime/{rid}"
        return {"tags": dict(self.runtimes[rid].get("tags") or {})}

    def tag_resource(self, resourceArn, tags):
        self.runtimes[resourceArn.rsplit("/", 1)[1]].setdefault("tags", {}).update(tags)

    def create_agent_runtime(self, **kw):
        self.creates.append(kw)
        if self.create_errors:
            raise self.create_errors.pop(0)
        self.runtimes[RUNTIME_ID] = {"name": kw["agentRuntimeName"], "spec": kw, "status": "READY",
                                     "tags": dict(kw.get("tags") or {})}
        return {"agentRuntimeArn": RUNTIME_ARN, "agentRuntimeId": RUNTIME_ID, "status": "CREATING"}

    def update_agent_runtime(self, agentRuntimeId, **kw):
        self.updates.append({"agentRuntimeId": agentRuntimeId, **kw})
        self.runtimes[agentRuntimeId]["spec"] = kw
        return {"agentRuntimeArn": RUNTIME_ARN, "agentRuntimeId": agentRuntimeId, "status": "UPDATING"}

    def get_agent_runtime(self, agentRuntimeId):
        rt = self.runtimes.get(agentRuntimeId)
        if rt is None:
            raise _err("ResourceNotFoundException")
        return {"status": rt["status"], "agentRuntimeArtifact": rt["spec"]["agentRuntimeArtifact"],
                "roleArn": rt["spec"]["roleArn"]}

    def delete_agent_runtime(self, agentRuntimeId):
        self.deletes.append(agentRuntimeId)
        self.runtimes.pop(agentRuntimeId, None)


class FakeSession:
    def __init__(self, world):
        self.w = world

    def client(self, service, region_name=None, config=None):
        self.w.calls.append(("client", service))
        return {"sts": self.w.sts, "iam": self.w.iam, "s3": self.w.s3, "ecr": self.w.ecr,
                ac.CONTROL_SERVICE: self.w.control}[service]


#: what the fake registries serve for a tag (the image is pinned by it)
DIGEST = "sha256:" + "ab" * 32


class FakeEcr:
    def __init__(self, world):
        self.w = world

    def describe_images(self, repositoryName, imageIds):
        tag = imageIds[0]["imageTag"]
        if tag in self.w.missing_tags:
            return {"imageDetails": []}
        return {"imageDetails": [{"imageDigest": DIGEST, "imageTags": [tag]}]}


class World:
    def __init__(self):
        self.calls: list[tuple] = []
        self.sessions: list[tuple] = []
        self.sts, self.iam, self.s3, self.control = FakeSts(), FakeIam(self), FakeS3(self), FakeControl(self)
        self.ecr, self.missing_tags = FakeEcr(self), set()


def _make_db(path: Path):
    from database.sqlite import SQLiteDatabase, close_all_pools

    async def go():
        await SQLiteDatabase(db_path=str(path)).initialize()
        await close_all_pools()
    asyncio.run(go())


@pytest.fixture
def world(tmp_path, monkeypatch):
    import database
    from core import swarm_out
    w = World()
    db = tmp_path / "data.db"
    _make_db(db)
    with sqlite3.connect(db) as c:
        c.execute("INSERT INTO hive_accounts (id, account_id, label, auth_method, created_at, default_region) "
                  "VALUES ('acct1', ?, '', 'access_keys', 't', ?)", (ACCOUNT, REGION))
    w.db = db
    monkeypatch.setattr(database.db, "db_path", db)
    monkeypatch.setattr(swarm_out, "_read_log", lambda: [])
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setattr(swarm_out, "_state_dir", lambda: state)
    monkeypatch.setattr(ep, "_host_version", lambda: "2.5.5")
    monkeypatch.setattr(ep, "configured_image", lambda: w.image)
    monkeypatch.setattr(ep, "POLL_S", 0)
    monkeypatch.setattr(ep, "ROLE_RETRY_S", 0)
    monkeypatch.setattr(ep, "_public_manifest_digest",
                        lambda path, tag: None if tag in w.missing_tags else DIGEST)
    w.image = PUBLIC_IMAGE

    def session_for(acct, region, purpose):
        w.sessions.append((acct["id"], region, purpose))
        return FakeSession(w)
    monkeypatch.setattr(hive_accounts, "session_for", session_for)
    monkeypatch.setattr(hive_accounts, "swarmai_account", lambda: None)
    ep._WORKING.clear()
    w.elastic = ep.ElasticHive(db)
    return w


def _rows(db):
    with sqlite3.connect(db) as c:
        c.row_factory = sqlite3.Row
        return [dict(r) for r in c.execute("SELECT * FROM hive_instances")]


def _assert_size(call: dict, uri: str):
    assert call["resourceConfigurations"] == [{"minimumMemoryInMiB": 8192}]
    assert call["platformVersion"] == "V2"
    assert call["networkConfiguration"] == {"networkMode": "PUBLIC"}
    assert call["protocolConfiguration"] == {"serverProtocol": "HTTP"}
    assert call["allowedPorts"] == [{"port": 8080}, {"port": 8081}]
    assert call["lifecycleConfiguration"] == {"idleRuntimeSessionTimeout": 900, "maxLifetime": 28800}
    assert call["agentRuntimeArtifact"] == {"containerConfiguration": {"containerUri": uri}}
    assert call["roleArn"] == f"arn:aws:iam::{ACCOUNT}:role/SwarmAI-ElasticHive-{REGION}"


# ── the size on every create and update ─────────────────────────────────────────

def test_create_and_update_carry_size(world):
    row = asyncio.run(world.elastic.enable("acct1", REGION))
    assert len(world.control.creates) == 1
    _assert_size(world.control.creates[0], f"{PUBLIC_IMAGE}:2.5.5@{DIGEST}")
    assert world.control.creates[0]["agentRuntimeName"] == "swarmai_elastic_hive"
    assert row["status"] == "ready" and row["carrier"] == "agentcore" and row["runtime_arn"] == RUNTIME_ARN
    assert row["s3_bucket"] == BUCKET and row["name"] == "elastic"

    row = asyncio.run(world.elastic.update(row["id"], "2.5.6"))
    assert len(world.control.updates) == 1
    _assert_size(world.control.updates[0], f"{PUBLIC_IMAGE}:2.5.6@{DIGEST}")
    assert world.control.updates[0]["agentRuntimeId"] == RUNTIME_ID
    assert row["status"] == "ready" and row["version"] == "2.5.6"


def test_the_spec_is_a_request_this_sdk_accepts():
    """The fakes take anything: the real model says whether the shape is right."""
    import botocore.session
    from botocore.validate import ParamValidator
    model = botocore.session.get_session().get_service_model(ac.CONTROL_SERVICE)
    spec = ep.runtime_spec(f"{PUBLIC_IMAGE}:2.5.5", f"arn:aws:iam::{ACCOUNT}:role/SwarmAI-ElasticHive-{REGION}")
    for op, extra in (("CreateAgentRuntime", {"agentRuntimeName": ep.RUNTIME_NAME}),
                      ("UpdateAgentRuntime", {"agentRuntimeId": RUNTIME_ID})):
        report = ParamValidator().validate({**spec, **extra}, model.operation_model(op).input_shape)
        assert not report.has_errors(), report.generate_report()
    assert ep.sdk_missing() == []
    import re
    pattern = model.shape_for("RuntimeContainerUri").metadata["pattern"]
    assert re.fullmatch(pattern, f"{PUBLIC_IMAGE}:2.5.5") and re.fullmatch(pattern, f"{PRIVATE_IMAGE}:2.5.5")


# ── refusals create nothing ─────────────────────────────────────────────────────

@pytest.mark.parametrize("image", [None, "docker.io/library/ubuntu", f"999999999999.dkr.ecr.{REGION}.amazonaws.com/x",
                                   f"{PUBLIC_IMAGE}:2.5.5"])
def test_refused_without_a_usable_image_creates_nothing(world, image):
    world.image = image
    with pytest.raises(ep.ElasticRefused) as e:
        asyncio.run(world.elastic.enable("acct1", REGION))
    if image is None:
        assert "settings could not be read" in str(e.value)
    assert world.calls == [] and world.sessions == [] and _rows(world.db) == []


def test_refused_when_the_sdk_cannot_carry_the_size(world, monkeypatch):
    monkeypatch.setattr(ep, "sdk_missing", lambda: ["resourceConfigurations"])
    with pytest.raises(ep.ElasticRefused, match="resourceConfigurations"):
        asyncio.run(world.elastic.enable("acct1", REGION))
    assert world.calls == [] and _rows(world.db) == []


def test_only_one_per_account(world):
    asyncio.run(world.elastic.enable("acct1", REGION))
    with pytest.raises(ep.ElasticRefused, match="already on"):
        asyncio.run(world.elastic.claim("acct1", REGION))
    assert len(world.control.creates) == 1 and len(_rows(world.db)) == 1


# ── the image and the role ──────────────────────────────────────────────────────

def test_image_location():
    assert ep.image_location(PUBLIC_IMAGE, ACCOUNT, REGION, "2.5.5") == (f"{PUBLIC_IMAGE}:2.5.5", None)
    assert ep.image_location(PRIVATE_IMAGE, ACCOUNT, REGION, "2.5.5") == (f"{PRIVATE_IMAGE}:2.5.5", "swarmai-hive")
    for bad, region in ((PRIVATE_IMAGE, "us-west-2"), (PRIVATE_IMAGE + "@sha256:ab", REGION),
                        ("https://" + PUBLIC_IMAGE, REGION), ("public.ecr.aws/onlyalias", REGION)):
        with pytest.raises(ep.ElasticRefused):
            ep.image_location(bad, ACCOUNT, region, "2.5.5")
    with pytest.raises(ep.ElasticRefused):
        ep.image_location(PUBLIC_IMAGE, ACCOUNT, REGION, "2.5.5; rm -rf /")


def _sids(policy):
    return {s["Sid"]: s for s in policy["Statement"]}


def test_role_trusts_agentcore_only_and_pulls_only_its_image(world):
    asyncio.run(world.elastic.enable("acct1", REGION))
    role = world.iam.roles[f"SwarmAI-ElasticHive-{REGION}"]
    (stmt,) = role["trust"]["Statement"]
    assert stmt["Principal"] == {"Service": "bedrock-agentcore.amazonaws.com"}
    assert stmt["Condition"] == {"StringEquals": {"aws:SourceAccount": ACCOUNT},
                                 "ArnLike": {"aws:SourceArn":
                                             f"arn:aws:bedrock-agentcore:{REGION}:{ACCOUNT}:runtime/swarmai_elastic_hive-*"}}
    policy = world.iam.policies[(f"SwarmAI-ElasticHive-{REGION}", "SwarmAI-ElasticHive")]
    assert "ImagePull" not in _sids(policy) and "ImageToken" not in _sids(policy)   # public: no ECR at all
    assert _sids(policy)["BridgeList"]["Resource"] == [f"arn:aws:s3:::{BUCKET}"]

    world.image = PRIVATE_IMAGE      # the update moves to a private repository: the role follows
    asyncio.run(world.elastic.update(_rows(world.db)[0]["id"]))
    policy = world.iam.policies[(f"SwarmAI-ElasticHive-{REGION}", "SwarmAI-ElasticHive")]
    assert _sids(policy)["ImagePull"]["Resource"] == [f"arn:aws:ecr:{REGION}:{ACCOUNT}:repository/swarmai-hive"]
    _assert_size(world.control.updates[-1], f"{PRIVATE_IMAGE}:2.5.5@{DIGEST}")


def test_a_runtime_of_the_same_name_is_reused_only_when_swarmai_made_it(world):
    world.control.runtimes[RUNTIME_ID] = {"name": ep.RUNTIME_NAME, "status": "READY", "tags": {"swarmai:managed": "true"},
                                          "spec": {"agentRuntimeArtifact": {}, "roleArn": "x"}}
    row = asyncio.run(world.elastic.enable("acct1", REGION))
    assert world.control.creates == [] and len(world.control.updates) == 1
    _assert_size(world.control.updates[0], f"{PUBLIC_IMAGE}:2.5.5@{DIGEST}")
    assert row["runtime_arn"] == RUNTIME_ARN and row["status"] == "ready"
    assert world.control.runtimes[RUNTIME_ID]["tags"]["swarmai:elastic-row"] == row["id"]


def test_a_runtime_of_the_same_name_swarmai_did_not_make_is_left_alone(world):
    world.control.runtimes[RUNTIME_ID] = {"name": ep.RUNTIME_NAME, "status": "READY", "tags": {},
                                          "spec": {"agentRuntimeArtifact": {}, "roleArn": "theirs"}}
    with pytest.raises(ep.ElasticRefused, match="did not make"):
        asyncio.run(world.elastic.enable("acct1", REGION))
    assert world.control.creates == [] and world.control.updates == []
    (row,) = _rows(world.db)
    assert row["status"] == "failed"
    asyncio.run(world.elastic.disable(row["id"]))      # no ARN recorded: never deletes theirs
    assert world.control.deletes == [] and RUNTIME_ID in world.control.runtimes and _rows(world.db) == []


def test_turning_off_without_an_arn_deletes_only_the_runtime_this_row_made(world, monkeypatch):
    real = ep.ElasticHive._set

    async def lose_the_arn(self, location_id, **fields):   # the enable stopped between create and record
        if "runtime_arn" in fields:
            raise RuntimeError("SwarmAI stopped")
        return await real(self, location_id, **fields)
    monkeypatch.setattr(ep.ElasticHive, "_set", lose_the_arn)
    with pytest.raises(RuntimeError):
        asyncio.run(world.elastic.enable("acct1", REGION))
    monkeypatch.setattr(ep.ElasticHive, "_set", real)
    (row,) = _rows(world.db)
    assert row["runtime_arn"] is None and RUNTIME_ID in world.control.runtimes
    asyncio.run(world.elastic.disable(row["id"]))
    assert world.control.deletes == [RUNTIME_ID] and _rows(world.db) == []


@pytest.mark.parametrize("role", [{"Tags": []},                                   # someone else's
                                  {"Tags": [{"Key": "swarmai:managed", "Value": "true"},
                                            {"Key": "swarmai:elastic-hive", "Value": REGION}],
                                   "attached": ["AdministratorAccess"]}])          # ours, but widened
def test_a_role_of_that_name_is_adopted_only_when_it_is_ours_and_bare(world, role):
    world.iam.roles[f"SwarmAI-ElasticHive-{REGION}"] = {"trust": {"theirs": True}, **role}
    with pytest.raises(ep.ElasticRefused):
        asyncio.run(world.elastic.enable("acct1", REGION))
    assert world.iam.roles[f"SwarmAI-ElasticHive-{REGION}"]["trust"] == {"theirs": True}
    assert world.iam.policies == {} and world.control.creates == []


def test_our_own_role_is_adopted(world):
    world.iam.roles[f"SwarmAI-ElasticHive-{REGION}"] = {"trust": {}, "Tags": [
        {"Key": "swarmai:managed", "Value": "true"}, {"Key": "swarmai:elastic-hive", "Value": REGION}]}
    row = asyncio.run(world.elastic.enable("acct1", REGION))
    assert row["status"] == "ready"
    assert world.iam.roles[f"SwarmAI-ElasticHive-{REGION}"]["trust"]["Statement"][0]["Principal"] == \
        {"Service": "bedrock-agentcore.amazonaws.com"}


def test_credentials_reaching_another_account_create_nothing(world, monkeypatch):
    monkeypatch.setattr(world.sts, "get_caller_identity", lambda: {"Account": "999999999999"})
    with pytest.raises(RuntimeError, match="999999999999"):
        asyncio.run(world.elastic.enable("acct1", REGION))
    assert world.control.creates == [] and world.iam.roles == {} and world.s3.buckets == set()


def test_a_foreign_runtime_arn_is_not_recorded(world, monkeypatch):
    real = world.control.create_agent_runtime

    def foreign(**kw):
        r = real(**kw)
        return {**r, "agentRuntimeArn": RUNTIME_ARN.replace(ACCOUNT, "999999999999")}
    monkeypatch.setattr(world.control, "create_agent_runtime", foreign)
    with pytest.raises(RuntimeError, match="unexpected runtime ARN"):
        asyncio.run(world.elastic.enable("acct1", REGION))
    assert _rows(world.db)[0]["runtime_arn"] is None


def test_a_new_role_not_yet_assumable_is_waited_for(world):
    world.control.create_errors = [_err("ValidationException", "Role validation failed for roleArn")] * 2
    row = asyncio.run(world.elastic.enable("acct1", REGION))
    assert len(world.control.creates) == 3 and row["status"] == "ready"


def test_a_failed_create_leaves_a_failed_row_that_turns_off(world):
    world.control.create_errors = [_err("ServiceQuotaExceededException", "too many runtimes")]
    with pytest.raises(ClientError):
        asyncio.run(world.elastic.enable("acct1", REGION))
    (row,) = _rows(world.db)
    assert row["status"] == "failed" and "too many runtimes" in row["error_message"]
    asyncio.run(world.elastic.disable(row["id"]))
    assert _rows(world.db) == []


# ── turning off ─────────────────────────────────────────────────────────────────

def test_disable_deletes_runtime_and_row_not_bucket(world):
    row = asyncio.run(world.elastic.enable("acct1", REGION))
    asyncio.run(world.elastic.disable(row["id"]))
    assert world.control.deletes == [RUNTIME_ID]
    assert _rows(world.db) == []
    assert BUCKET in world.s3.buckets     # FakeS3 fails on any delete call


# ── the elastic row is not an EC2 Hive ──────────────────────────────────────────

def _insert_ec2(db, n, status="running"):
    with sqlite3.connect(db) as c:
        for i in range(n):
            c.execute("INSERT INTO hive_instances (id, name, account_ref, region, status, ec2_instance_id, "
                      "created_at, updated_at) VALUES (?, ?, 'acct1', ?, ?, ?, 't', 't')",
                      (f"h{i}", f"hive-{i}", REGION, status, f"i-{i}"))


async def _counted(db):
    import aiosqlite
    async with aiosqlite.connect(db) as c:
        return await hive_accounts.counted(c)


def test_elastic_row_is_not_counted_toward_max_hives(world):
    _insert_ec2(world.db, hive_accounts.MAX_HIVES - 1)
    asyncio.run(world.elastic.enable("acct1", REGION))
    assert asyncio.run(_counted(world.db)) == hive_accounts.MAX_HIVES - 1
    with sqlite3.connect(world.db) as c:       # every reader of COUNTED_SQL sees the same
        n = c.execute(f"SELECT COUNT(*) FROM hive_instances WHERE {hive_accounts.COUNTED_SQL}").fetchone()[0]
    assert n == hive_accounts.MAX_HIVES - 1


def test_ec2_routes_leave_the_elastic_row_alone(world):
    from fastapi import HTTPException
    from routers import hive as hive_router
    _insert_ec2(world.db, 1)
    row = asyncio.run(world.elastic.enable("acct1", REGION))
    listed = asyncio.run(hive_router.list_instances())
    assert [i.id for i in listed] == ["h0"]
    with pytest.raises(HTTPException) as e:
        asyncio.run(hive_router.delete_instance(row["id"]))
    assert e.value.status_code == 409
    with pytest.raises(HTTPException) as e:
        asyncio.run(hive_router.replace_instance(row["id"]))
    assert e.value.status_code == 409
    assert len(_rows(world.db)) == 2 and world.control.deletes == []


# ── the routes ──────────────────────────────────────────────────────────────────

def test_routes_refuse_without_image_and_report_the_reason(world):
    from fastapi import HTTPException
    from routers import hive as hive_router
    world.image = None
    got = asyncio.run(hive_router.elastic_status())
    assert got["can_enable"] is False and "settings could not be read" in got["reason"] and got["hives"] == []
    assert got["size"] == {"vcpu_base": 2, "memory_gib_base": 8, "vcpu_peak": 8, "memory_gib_peak": 32, "disk_gib": 32}
    with pytest.raises(HTTPException) as e:
        asyncio.run(hive_router.elastic_enable(None))
    assert e.value.status_code == 409 and "settings could not be read" in e.value.detail
    assert _rows(world.db) == [] and world.calls == []


def test_routes_turn_on_show_status_and_turn_off(world):
    from routers import hive as hive_router

    async def go():
        got = await hive_router.elastic_status()
        assert got["can_enable"] is True and got["reason"] is None
        started = await hive_router.elastic_enable(None)
        assert started["status"] == "enabling"
        for _ in range(200):
            await asyncio.sleep(0.01)
            if not ep._WORKING:
                break
        got = await hive_router.elastic_status()
        (h,) = got["hives"]
        assert h["status"] == "ready" and h["runtime_status"] == "READY" and h["up_to_date"] is True
        off = await hive_router.elastic_disable(h["id"])
        assert off["status"] == "disabling"
        for _ in range(200):
            await asyncio.sleep(0.01)
            if not ep._WORKING:
                break
        assert (await hive_router.elastic_status())["hives"] == []
    asyncio.run(go())
    assert world.control.deletes == [RUNTIME_ID]


def _force(db, row_id, **cols):
    with sqlite3.connect(db) as c:
        c.execute(f"UPDATE hive_instances SET {', '.join(f'{k} = ?' for k in cols)} WHERE id = ?",
                  (*cols.values(), row_id))


def test_a_transient_status_left_by_a_quit_host_is_settled(world):
    row = asyncio.run(world.elastic.enable("acct1", REGION))
    _force(world.db, row["id"], status="upgrading")
    assert asyncio.run(world.elastic.status((ACCOUNT, REGION)))["hives"][0]["status"] == "ready"
    # turning off stopped after the runtime was deleted: the row goes too
    _force(world.db, row["id"], status="disabling")
    world.control.runtimes.clear()
    assert asyncio.run(world.elastic.status((ACCOUNT, REGION)))["hives"] == []


def test_an_enable_stopped_before_any_runtime_settles_failed_and_turns_off(world):
    row = asyncio.run(world.elastic.claim("acct1", REGION))
    ep._WORKING.clear()                                    # the task died with SwarmAI
    (h,) = asyncio.run(world.elastic.status((ACCOUNT, REGION)))["hives"]
    assert h["status"] == "failed" and "interrupted" in h["error_message"] and h["working"] is False
    asyncio.run(world.elastic.disable(row["id"]))
    assert _rows(world.db) == [] and world.control.deletes == []


def test_the_settle_never_overwrites_a_claim_made_meanwhile(world, monkeypatch):
    row = asyncio.run(world.elastic.enable("acct1", REGION))
    _force(world.db, row["id"], status="upgrading")
    real_live = ep.ElasticHive._live

    async def live_then_claim(self, r):
        got = await real_live(self, r)
        _force(world.db, row["id"], status="disabling")       # a turn-off claimed it in between
        return got
    monkeypatch.setattr(ep.ElasticHive, "_live", live_then_claim)
    asyncio.run(world.elastic.status((ACCOUNT, REGION)))
    assert _rows(world.db)[0]["status"] == "disabling"


def test_a_second_account_gets_its_own_name(world):
    with sqlite3.connect(world.db) as c:
        c.execute("INSERT INTO hive_accounts (id, account_id, label, auth_method, created_at, default_region) "
                  "VALUES ('acct2', '210987654321', '', 'access_keys', 't', ?)", (REGION,))
    asyncio.run(world.elastic.claim("acct1", REGION))
    row = asyncio.run(world.elastic.claim("acct2", REGION))
    assert row["name"] == "elastic-210987654321"


def test_the_carrier_offers_no_row_that_is_changing(world, monkeypatch):
    row = asyncio.run(world.elastic.enable("acct1", REGION))
    monkeypatch.setattr(ac, "_session_for", lambda acct, region, purpose: FakeSession(world))
    monkeypatch.setattr(ac, "_host_version", lambda: "2.5.5")
    monkeypatch.setattr(ac, "_session_quota", lambda *a: 10)
    from core.carriers.base import Needs
    car = ac.AgentCoreCarrier()
    (offer,) = asyncio.run(car.offers(Needs()))
    assert offer.blocked is None and offer.name == "elastic"
    assert ep.TRANSIENT == ac.CHANGING_ROW_STATUSES
    for status in ep.TRANSIENT:
        _force(world.db, row["id"], status=status)
        (offer,) = asyncio.run(car.offers(Needs()))
        assert offer.blocked == "not_ready", status


def test_write_routes_refuse_on_a_hive(world, monkeypatch):
    from fastapi import HTTPException
    from routers import hive as hive_router
    monkeypatch.setenv("SWARMAI_MODE", "hive")
    for call in (lambda: hive_router.elastic_enable(None), lambda: hive_router.elastic_update("x", None),
                 lambda: hive_router.elastic_disable("x")):
        with pytest.raises(HTTPException) as e:
            asyncio.run(call())
        assert e.value.status_code == 403
    assert _rows(world.db) == [] and world.calls == []


# ── the registry ────────────────────────────────────────────────────────────────

def test_registry_offers_the_elastic_hive():
    from core.carriers import registry
    from core.carriers.base import AGENTCORE
    car = registry.carrier(AGENTCORE)
    assert isinstance(car, ac.AgentCoreCarrier) and car in registry.carriers()


def test_the_image_is_pinned_by_the_digest_its_registry_serves(world):
    # a tag pushed again later never changes what the runtime runs; a tag the registry
    # does not serve creates nothing
    row = asyncio.run(world.elastic.enable("acct1", REGION))
    uri = world.control.creates[-1]["agentRuntimeArtifact"]["containerConfiguration"]["containerUri"]
    assert uri == f"{PUBLIC_IMAGE}:2.5.5@{DIGEST}" and ac._image_tag(uri) == "2.5.5"
    world.missing_tags.add("2.5.6")
    with pytest.raises(ep.ElasticRefused):
        asyncio.run(world.elastic.update(row["id"], "2.5.6"))
    assert world.control.updates == []
    assert _rows(world.db)[0]["status"] == "failed"


class _Config:
    def __init__(self, value):
        self.value = value

    def get(self, key, default=None):
        return self.value if key == ep.IMAGE_SETTING else default


@pytest.mark.parametrize("value", [None, "", "   "])
def test_with_no_image_setting_the_released_image_is_used(monkeypatch, value):
    # every install can turn the elastic Hive on: the release pushes the image to one public
    # repository, so nobody has to find and type it before the Hive page lets them
    from core import app_config_manager
    monkeypatch.setattr(app_config_manager.AppConfigManager, "instance", lambda: _Config(value))
    assert ep.configured_image() == ep.RELEASED_IMAGE == "public.ecr.aws/q4o1o9d0/swarmai-hive"


def test_the_image_setting_overrides_the_released_image(monkeypatch):
    from core import app_config_manager
    monkeypatch.setattr(app_config_manager.AppConfigManager, "instance",
                        lambda: _Config(" public.ecr.aws/someone/hive-dev "))
    assert ep.configured_image() == "public.ecr.aws/someone/hive-dev"


def test_the_image_setting_can_be_written_through_settings():
    # a key outside DEFAULT_CONFIG is dropped by PUT /api/settings, and a hand edit of
    # config.json is not read by a running daemon: the override needs the settings route
    from routers.settings import WRITABLE_KEYS
    assert ep.IMAGE_SETTING in WRITABLE_KEYS
