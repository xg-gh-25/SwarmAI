"""One action list for the permission template and the "can it create" checks
(core/aws_permissions.py; design 2026-10-31-swarmai-aws-account §7.2, §7.4).

Questions answered:
1. is every AWS API the provisioner calls in the action list (a new call without a
   permission would fail half-way through a deploy for template users)?
2. does the template stop privilege escalation: a role only with the SwarmAI boundary,
   only the SSM core policy attachable, a trust policy changed only on a role carrying the
   boundary, a Hive role passed only to EC2 and Roles Anywhere, tags only while creating,
   stop / start / terminate / SSM only on tagged instances, Roles Anywhere anchors and
   profiles created only tagged and changed only while tagged; and is every call an SSH
   machine's identity makes (hive/ssh_identity, hive/ssh_provisioner) in the list?
3. checks: a DryRun that would succeed is "ok", refused is "missing", a simulation that
   cannot run is "could not confirm" (never ok), no default VPC is "missing", and each IAM
   action is simulated on the resource type it acts on.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from core import aws_permissions as ap

BACKEND = Path(__file__).resolve().parents[1]
PROVISIONER = BACKEND / "hive" / "provisioner.py"
#: every module whose AWS calls deploy or manage an EC2 Hive with the SwarmAI identity.
#: Not routers/hive.py: its account verify probes a legacy access-key account, and each
#: probe's refusal is that probe's result, not a step that fails half-way through.
DEPLOY_MODULES = (PROVISIONER, PROVISIONER.with_name("bpa_subnet.py"), PROVISIONER.with_name("fleet.py"),
                  PROVISIONER.with_name("accounts.py"), BACKEND / "core" / "hive_diagnostics.py")
_SERVICE = {"cf": "cloudfront"}
#: every module whose AWS calls Elastic Workers makes with the SwarmAI identity (turning
#: it on, updating, turning off, starting and stopping a round, reading its bridge and logs)
ELASTIC_MODULES = (BACKEND / "hive" / "agentcore_provisioner.py", BACKEND / "core" / "carriers" / "agentcore.py")
#: the name a boto3 client has in the Elastic modules -> its botocore service
_ELASTIC_CLIENTS = {"iam": "iam", "sts": "sts", "ecr": "ecr", "s3": "s3", "logs": "logs",
                    "sq": "service-quotas", "ctl": "bedrock-agentcore-control",
                    "client": "bedrock-agentcore"}
#: ``session.client(<constant>, ...).<call>(`` in the carrier
_CLIENT_CONSTANTS = {"SERVICE": "bedrock-agentcore", "CONTROL_SERVICE": "bedrock-agentcore-control"}
#: botocore service -> its IAM action prefix, where they differ
_IAM_PREFIX = {"bedrock-agentcore-control": "bedrock-agentcore", "service-quotas": "servicequotas"}


def test_every_provisioner_call_is_in_the_action_list():
    src = "\n".join(m.read_text() for m in DEPLOY_MODULES)
    calls = set(re.findall(r"\b(ec2|iam|s3|ssm|cf)\.([a-z_]+)\(", src)) - {("ec2", "get_waiter")}
    assert ("ec2", "create_vpc_block_public_access_exclusion") in calls
    assert calls, "found no AWS calls: the pattern no longer matches the provisioner"
    missing = sorted(ap.action_for_method(_SERVICE.get(svc, svc), m) for svc, m in calls
                     if ap.action_for_method(_SERVICE.get(svc, svc), m) not in ap.all_actions())
    # S3 object/bucket calls map to their IAM action names
    missing = [a for a in missing if a not in ("s3:HeadObject", "s3:PutPublicAccessBlock")]
    assert not missing, f"provisioner calls without a permission in the list: {missing}"


def _elastic_calls() -> set:
    """(botocore service, method) of every AWS call in the Elastic modules: a call on a
    client name (``ctl.create_agent_runtime(``) or on ``session.client(SERVICE, ...)``,
    kept only when the method is an operation of that service (``client.put(`` of the
    httpx round client is not)."""
    import botocore.session
    from botocore import xform_name
    bs = botocore.session.get_session()
    ops = {svc: {xform_name(o) for o in bs.get_service_model(svc).operation_names}
           for svc in set(_ELASTIC_CLIENTS.values())}
    calls: set = set()
    for module in ELASTIC_MODULES:
        src = module.read_text()
        for var, method in re.findall(r"\b([a-z0-9]+)\.([a-z0-9_]+)\(", src):
            svc = _ELASTIC_CLIENTS.get(var)
            if svc and method in ops[svc]:
                calls.add((svc, method))
        for const, method in re.findall(r"\.client\(([A-Z_]+)(?:[^()]|\([^()]*\))*\)\.([a-z0-9_]+)\(", src):
            svc = _CLIENT_CONSTANTS.get(const)
            if svc and method in ops[svc]:
                calls.add((svc, method))
    return calls


def test_every_elastic_call_is_in_the_action_list():
    """A template user turns Elastic Workers on: every call it makes is allowed, or it
    fails half-way through with AccessDenied."""
    calls = _elastic_calls()
    assert {("bedrock-agentcore-control", "create_agent_runtime"),
            ("bedrock-agentcore-control", "delete_agent_runtime"),
            ("bedrock-agentcore", "invoke_agent_runtime"), ("bedrock-agentcore", "stop_runtime_session"),
            ("iam", "update_assume_role_policy"), ("iam", "put_role_permissions_boundary"),
            ("logs", "filter_log_events"), ("ecr", "describe_images")} <= calls, \
        "the pattern no longer finds the Elastic modules' AWS calls"
    missing = sorted({ap.action_for_method(_IAM_PREFIX.get(svc, svc), m) for svc, m in calls}
                     - ap.all_actions())
    assert not missing, f"Elastic Workers calls without a permission in the list: {missing}"
    # what AWS checks besides the call itself (Service Authorization Reference: the
    # dependent actions of CreateAgentRuntime and DeleteAgentRuntime)
    assert {"bedrock-agentcore:CreateAgentRuntimeEndpoint", "bedrock-agentcore:TagResource",
            "bedrock-agentcore:DeleteAgentRuntimeEndpoint",
            "bedrock-agentcore:DeleteWorkloadIdentity", "iam:PassRole"} <= set(ap.ACTIONS["elastic"]) | ap.all_actions()


def test_every_listed_action_is_in_the_template():
    granted = {a for st in _statements() for a in _actions(st)}
    assert not ap.all_actions() - granted, sorted(ap.all_actions() - granted)


def test_the_elastic_names_are_the_provisioners():
    from hive import agentcore_provisioner as ep
    assert ap.ELASTIC_ROLE_PREFIX == ep.ROLE_PREFIX and ap.ELASTIC_RUNTIME_NAME == ep.RUNTIME_NAME


def _statements(account="123456789012"):
    return ap.template(account)["policy"]["Statement"]


def _actions(st):
    return st["Action"] if isinstance(st["Action"], list) else [st["Action"]]


HIVE_ROLE = "arn:aws:iam::123456789012:role/SwarmAI-Hive-*"
ELASTIC_ROLE = "arn:aws:iam::123456789012:role/SwarmAI-ElasticHive-*"
BOUNDARY = "arn:aws:iam::123456789012:policy/SwarmAI-Hive-Boundary"


def _resources(st):
    return st["Resource"] if isinstance(st["Resource"], list) else [st["Resource"]]


def test_no_wildcard_actions_and_trust_changes_only_with_the_boundary():
    """No action wildcard; a role's trust is rewritten only on SwarmAI's roles (the Elastic
    Workers role's adopt path, an SSH machine's role moving to its own certificate) and only
    while the role carries the SwarmAI boundary, so a changed trust gives no more than the
    boundary; a boundary is only set on the Elastic Workers role, never removed."""
    for st in _statements():
        assert all(not a.endswith(":*") and a != "*" for a in _actions(st)), st["Sid"]
    trust = _with("iam:UpdateAssumeRolePolicy")
    # only while the role carries the SwarmAI boundary: a role of that name made without it
    # (by someone else, or before the template) cannot be taken over
    assert sorted(r for st in trust for r in _resources(st)) == [ELASTIC_ROLE, HIVE_ROLE]
    for st in trust:
        assert st["Condition"] == {"StringEquals": {"iam:PermissionsBoundary": BOUNDARY}}, st["Sid"]
    st, = _with("iam:PutRolePermissionsBoundary")
    assert _resources(st) == [ELASTIC_ROLE]
    assert st["Condition"] == {"StringEquals": {"iam:PermissionsBoundary": BOUNDARY}}
    assert not _with("iam:DeleteRolePermissionsBoundary")


def _with(action):
    return [st for st in _statements() if action in _actions(st)]


def test_roles_only_with_the_boundary_and_only_the_ssm_core_policy():
    creates = {json.dumps(_resources(st)): st["Condition"] for st in _with("iam:CreateRole")}
    assert creates == {json.dumps([HIVE_ROLE]): {"StringEquals": {"iam:PermissionsBoundary": BOUNDARY}},
                       json.dumps([ELASTIC_ROLE]): {"StringEquals": {"iam:PermissionsBoundary": BOUNDARY}}}
    attach, = _with("iam:AttachRolePolicy")
    assert attach["Condition"]["ArnEquals"]["iam:PolicyARN"] == ap.SSM_CORE
    assert _resources(attach) == [HIVE_ROLE]
    # each role is passed only to the service that runs it
    passes = {(json.dumps(_resources(st)), st["Condition"]["StringEquals"]["iam:PassedToService"])
              for st in _with("iam:PassRole")}
    assert passes == {(json.dumps([HIVE_ROLE]), "ec2.amazonaws.com"),
                      (json.dumps([HIVE_ROLE]), "rolesanywhere.amazonaws.com"),
                      (json.dumps([ELASTIC_ROLE]), "bedrock-agentcore.amazonaws.com")}


#: actions IAM cannot scope by resource (no resource type in the Service Authorization
#: Reference): the only Elastic ones a "*" resource is allowed for
UNSCOPABLE = {"bedrock-agentcore:CreateAgentRuntime", "bedrock-agentcore:ListAgentRuntimes",
              "logs:DescribeLogGroups", "servicequotas:ListServiceQuotas"}


def test_the_elastic_statements_are_scoped():
    elastic = set(ap.ACTIONS["elastic"])
    for st in _statements():
        acts = set(_actions(st)) & elastic
        if not acts:
            continue
        res = _resources(st)
        if res == ["*"]:
            assert acts <= UNSCOPABLE, (st["Sid"], sorted(acts - UNSCOPABLE))
        for r in res:
            assert r == "*" or ":123456789012:" in r or r.startswith("arn:aws:iam::123456789012:"), (st["Sid"], r)
        if any(a.startswith("iam:") for a in acts):
            # or a Hive role's trust (an SSH machine's, test_no_wildcard_actions_and_trust_...)
            assert res == [ELASTIC_ROLE] or (res == [HIVE_ROLE] and acts == {"iam:UpdateAssumeRolePolicy"}), st["Sid"]
    create, = _with("bedrock-agentcore:CreateAgentRuntime")
    assert create["Condition"] == {"StringEquals": {"aws:RequestTag/swarmai:managed": "true"}}
    runtime = "arn:aws:bedrock-agentcore:*:123456789012:runtime/swarmai_elastic_hive-*"
    for action in ("bedrock-agentcore:UpdateAgentRuntime", "bedrock-agentcore:DeleteAgentRuntime",
                   "bedrock-agentcore:GetAgentRuntime", "bedrock-agentcore:TagResource",
                   "bedrock-agentcore:InvokeAgentRuntime", "bedrock-agentcore:StopRuntimeSession"):
        st, = _with(action)
        assert _resources(st) == [runtime], action
    for action in ("logs:FilterLogEvents", "logs:DescribeLogStreams"):
        st, = _with(action)
        assert _resources(st) == ["arn:aws:logs:*:123456789012:log-group:/aws/bedrock-agentcore/*"], action
    st, = _with("ecr:DescribeImages")
    assert _resources(st) == ["arn:aws:ecr:*:123456789012:repository/*"]


#: the modules whose AWS calls give an SSH machine its own identity; the name each boto3 client
#: has there -> its IAM action prefix
SSH_MODULES = (BACKEND / "hive" / "ssh_identity.py", BACKEND / "hive" / "ssh_provisioner.py")
_SSH_CLIENTS = {"ra": "rolesanywhere", "iam": "iam"}


def test_every_ssh_identity_call_is_in_the_action_list():
    """A template user adds an SSH machine: every Roles Anywhere and IAM call its identity
    makes is allowed, or the install fails half-way through with AccessDenied."""
    src = "\n".join(m.read_text() for m in SSH_MODULES)
    # a call (iam.create_role(...)), a call handed to the refusal namer (_aws(..., ra.create_profile, ...)),
    # or a call on a client made in place (session.client("iam").update_assume_role_policy(...))
    calls = set(re.findall(r"\b(ra|iam)\.([a-z_]+)[(,)]", src))
    calls |= {("iam", m) for m in re.findall(r"session\.client\(\"iam\"\)\.([a-z_]+)\(", src)}
    assert {("ra", "create_trust_anchor"), ("ra", "create_profile"), ("ra", "delete_profile"),
            ("ra", "list_profiles"), ("iam", "update_assume_role_policy"), ("iam", "create_role"),
            ("iam", "get_role")} <= calls, "the pattern no longer finds the SSH modules' calls"
    listed = ap.all_actions()
    missing = sorted({ap.action_for_method(_SSH_CLIENTS[svc], m) for svc, m in calls} - listed)
    assert not missing, f"SSH identity calls without a permission in the list: {missing}"
    # what AWS checks besides the call (Service Authorization Reference): tagging on create,
    # passing the role on CreateProfile, UpdateProfile and EnableProfile
    assert {"rolesanywhere:TagResource", "iam:PassRole"} <= set(ap.ACTIONS["ssh_identity"])


def test_roles_anywhere_grants_no_anchor_change():
    """IAM cannot limit TagResource to SwarmAI's own resources, so a tag can be put on any
    trust anchor or profile: nothing a tag unlocks may change an anchor (its CA decides which
    certificates become the roles that trust it). Anchors and profiles are created tagged and
    listed; a profile is deleted only while tagged; nothing else."""
    granted = {}
    for st in _statements():
        for a in _actions(st):
            if a.startswith("rolesanywhere:"):
                granted[a] = st
    assert set(granted) == {a for a in ap.ACTIONS["ssh_identity"] if a.startswith("rolesanywhere:")} == {
        "rolesanywhere:CreateTrustAnchor", "rolesanywhere:ListTrustAnchors", "rolesanywhere:CreateProfile",
        "rolesanywhere:ListProfiles", "rolesanywhere:DeleteProfile", "rolesanywhere:TagResource"}
    policy = ap.template("123456789012")["policy"]
    anchor = "arn:aws:rolesanywhere:us-east-1:123456789012:trust-anchor/00000001-0000-4000-8000-000000000000"
    for action in ("UpdateTrustAnchor", "EnableTrustAnchor", "DeleteTrustAnchor", "DisableTrustAnchor",
                   "ImportCrl", "UpdateProfile", "EnableProfile", "PutAttributeMapping"):
        assert not _allows(policy, f"rolesanywhere:{action}", anchor), action
    for action in ("rolesanywhere:CreateTrustAnchor", "rolesanywhere:CreateProfile", "rolesanywhere:TagResource"):
        assert granted[action]["Condition"] == {"StringEquals": {"aws:RequestTag/swarmai:managed": "true"}}
    delete = granted["rolesanywhere:DeleteProfile"]
    assert delete["Condition"] == {"StringEquals": {"aws:ResourceTag/swarmai:managed": "true"}}
    assert _resources(delete) == ["arn:aws:rolesanywhere:*:123456789012:profile/*"]
    from hive import ssh_identity
    assert ssh_identity.TAG == {"key": ap.MANAGED_TAG, "value": "true"}


def _allows(policy: dict, action: str, resource: str) -> bool:
    from fnmatch import fnmatchcase
    for st in policy["Statement"]:
        if st["Effect"] != "Allow" or not any(fnmatchcase(action, a) for a in _actions(st)):
            continue
        if any(fnmatchcase(resource, r) for r in _resources(st)):
            return True
    return False


def test_the_boundary_allows_the_elastic_execution_role():
    """The Elastic Workers role is created with the SwarmAI boundary when the account has
    it (template users do): everything its own policy grants must be inside the boundary,
    or the runtime loses its logs or its image pull."""
    from core.carriers import agentcore as ac
    boundary = ap.boundary_policy()
    for repo in (None, "swarmai-hive"):
        own = ac.execution_role_policy("123456789012", "us-east-1", "swarmai-hive-123456789012-us-east-1", repo)
        for st in own["Statement"]:
            if st["Effect"] != "Allow":
                continue
            for action in _actions(st):
                for res in _resources(st):
                    assert _allows(boundary, action, res), (st["Sid"], action, res)


def test_tags_only_while_creating_and_management_only_on_tagged_resources():
    tag, = _with("ec2:CreateTags")
    assert "ec2:CreateAction" in tag["Condition"]["StringEquals"]
    for action in ("ec2:StopInstances", "ec2:StartInstances", "ec2:TerminateInstances"):
        st, = _with(action)
        assert st["Condition"] == {"StringEquals": {"ec2:ResourceTag/swarmai:managed": "true"}}
    run = [st for st in _with("ec2:RunInstances") if "instance/*" in json.dumps(st["Resource"])]
    assert run[0]["Condition"] == {"StringEquals": {"aws:RequestTag/swarmai:managed": "true"}}
    ssm = [st for st in _with("ssm:SendCommand") if "instance/*" in json.dumps(st["Resource"])]
    assert "ssm:resourceTag/swarmai:managed" in json.dumps(ssm[0]["Condition"])


class _Err(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.response = {"Error": {"Code": code}}


class FakeSess:
    def __init__(self, *, run="DryRunOperation", sg="DryRunOperation", vpc=True,
                 denied=(), simulate_error=None):
        self.run, self.sg, self.vpc, self.denied = run, sg, vpc, set(denied)
        self.simulate_error = simulate_error
        self.simulated = []

    def client(self, name, region_name=None):
        outer = self

        class C:
            def describe_vpc_block_public_access_options(self):
                return {"VpcBlockPublicAccessOptions": {"InternetGatewayBlockMode": "off"}}

            def describe_vpcs(self, **kw):
                return {"Vpcs": [{"VpcId": "vpc-1"}] if outer.vpc else []}

            def get_parameter(self, **kw):
                return {"Parameter": {"Value": "ami-123"}}

            def run_instances(self, **kw):
                assert kw["DryRun"] is True
                outer.run_kw = kw
                raise _Err(outer.run)

            def create_security_group(self, **kw):
                assert kw["DryRun"] is True
                raise _Err(outer.sg)

            def get_role(self, RoleName):
                return {"Role": {"Arn": f"arn:aws:iam::123456789012:role/aws-reserved/{RoleName}"}}

            def simulate_principal_policy(self, **kw):
                if outer.simulate_error:
                    raise _Err(outer.simulate_error)
                outer.simulated.append(kw)
                given = {e["ContextKeyName"] for e in kw.get("ContextEntries") or []}
                out = []
                for a in kw["ActionNames"]:
                    need = [k for k in getattr(outer, "conditions", {}).get(a, []) if k not in given]
                    if need:   # the policy allows it only under a condition whose value is missing
                        out.append({"EvalActionName": a, "EvalDecision": "implicitDeny",
                                    "MissingContextValues": need})
                    else:
                        out.append({"EvalActionName": a,
                                    "EvalDecision": "implicitDeny" if a in outer.denied else "allowed"})
                return {"EvaluationResults": out}
        return C()


ARN = "arn:aws:sts::123456789012:assumed-role/Admin/me"


def test_all_allowed_is_ok_and_nothing_is_created():
    sess = FakeSess()
    hive, storage = ap.run_hive_checks(sess, "us-east-1", "123456789012", ARN)
    assert hive["status"] == "ok" and storage["status"] == "ok"
    by_res = {tuple(s.get("ResourceArns") or ()): s["ActionNames"] for s in sess.simulated}
    profile = [a for r, a in by_res.items() if r and ":instance-profile/" in r[0]]
    assert profile == [["iam:CreateInstanceProfile", "iam:AddRoleToInstanceProfile"]]
    assert all(s["PolicySourceArn"].endswith("role/aws-reserved/Admin") for s in sess.simulated)


@pytest.mark.parametrize("kw,missing", [
    ({"run": "UnauthorizedOperation"}, ["ec2:RunInstances"]),
    ({"sg": "UnauthorizedOperation"}, ["ec2:CreateSecurityGroup"]),
    ({"denied": ["iam:PassRole", "cloudfront:CreateDistribution"]},
     ["cloudfront:CreateDistribution", "iam:PassRole"]),
])
def test_what_is_refused_is_named(kw, missing):
    hive, _ = ap.run_hive_checks(FakeSess(**kw), "us-east-1", "123456789012", ARN)
    assert hive["status"] == "missing" and hive["missing"] == missing


def test_no_default_vpc_is_missing():
    hive, _ = ap.run_hive_checks(FakeSess(vpc=False), "eu-west-1", "123456789012", ARN)
    assert hive["status"] == "missing" and hive["no_default_vpc"] is True
    assert "no default VPC in eu-west-1" in hive["detail"]


def test_a_simulation_that_cannot_run_is_never_ok():
    hive, storage = ap.run_hive_checks(FakeSess(simulate_error="AccessDenied"), "us-east-1",
                                       "123456789012", ARN)
    assert hive["status"] == "unknown" and storage["status"] == "unknown"


def test_storage_refusal():
    _, storage = ap.run_hive_checks(FakeSess(denied=["s3:CreateBucket"]), "us-east-1",
                                    "123456789012", ARN)
    assert storage == {"status": "missing", "detail": "missing s3:CreateBucket",
                       "missing": ["s3:CreateBucket"]}


def test_principal_arn_falls_back_to_the_root_path_without_get_role():
    """No iam:GetRole: the role is simulated at the root path (a logged fallback, never
    an error that stops the checks)."""
    from core import aws_permissions as ap

    class Iam:
        def get_role(self, **kw):
            raise RuntimeError("AccessDenied")

    class Sess:
        def client(self, name):
            return Iam()
    arn = "arn:aws:sts::111111111111:assumed-role/Admin/me"
    assert ap.principal_arn(Sess(), arn) == "arn:aws:iam::111111111111:role/Admin"
    assert ap.principal_arn(Sess(), "arn:aws:iam::111111111111:user/bob") == "arn:aws:iam::111111111111:user/bob"


def test_a_check_that_breaks_still_names_its_region(monkeypatch):
    """The page asks for a region's check again only while the stored verdict is for
    another region: a broken check must carry its region too, or it is asked forever."""
    from core import aws_account as acct
    from core import aws_permissions as ap

    class Any:
        def client(self, name):
            raise RuntimeError("offline")
    monkeypatch.setattr(acct, "get_record", lambda: {"region": "us-east-1", "account_id": "111111111111"})
    monkeypatch.setattr(acct, "session", lambda region: Any())
    monkeypatch.setattr(acct, "get_chat_via", lambda config=None: "bedrock_key")
    monkeypatch.setattr(acct, "is_hive_mode", lambda: False)
    monkeypatch.setattr(acct, "_update_record", lambda **kw: None)

    def boom(*a):
        raise RuntimeError("simulate failed")
    monkeypatch.setattr(ap, "run_hive_checks", boom)
    out = acct.run_capability_checks({}, "eu-west-1")
    assert out["hive"]["status"] == "unknown" and out["hive"]["region"] == "eu-west-1"
    assert "region" not in out["storage"]


def test_conditions_of_the_template_are_simulated_with_their_values():
    """The template allows CreateRole, PutRolePolicy, AttachRolePolicy and PassRole only
    under conditions: they are simulated with the values SwarmAI's calls carry, so the
    template itself passes."""
    sess = FakeSess()
    sess.conditions = {"iam:CreateRole": ["iam:PermissionsBoundary"],
                       "iam:PutRolePolicy": ["iam:PermissionsBoundary"],
                       "iam:AttachRolePolicy": ["iam:PolicyARN", "iam:PermissionsBoundary"],
                       "iam:PassRole": ["iam:PassedToService"]}
    hive, _ = ap.run_hive_checks(sess, "us-east-1", "123456789012", ARN)
    assert hive["status"] == "ok"
    ctx = {e["ContextKeyName"]: e["ContextKeyValues"][0]
           for s_ in sess.simulated for e in s_.get("ContextEntries") or []}
    assert ctx["iam:PermissionsBoundary"] == "arn:aws:iam::123456789012:policy/SwarmAI-Hive-Boundary"
    assert ctx["iam:PassedToService"] == "ec2.amazonaws.com"


def test_a_condition_without_its_value_is_unknown_never_missing():
    sess = FakeSess()
    sess.conditions = {"iam:PassRole": ["iam:SomethingSwarmAIDoesNotSend"]}
    hive, _ = ap.run_hive_checks(sess, "us-east-1", "123456789012", ARN)
    assert hive["status"] == "unknown" and "iam:PassRole" in hive["detail"]


def test_the_launch_check_tags_every_resource_it_creates():
    sess = FakeSess()
    ap.run_hive_checks(sess, "us-east-1", "123456789012", ARN)
    types = {t["ResourceType"] for t in sess.run_kw["TagSpecifications"]}
    assert types == {"instance", "volume", "network-interface"}


def _stmt(policy, sid):
    return next(s_ for s_ in policy["Statement"] if s_["Sid"] == sid)


def test_permissions_can_be_granted_only_to_roles_with_the_boundary():
    pol = ap.template("123456789012")["policy"]
    boundary = "arn:aws:iam::123456789012:policy/SwarmAI-Hive-Boundary"
    granting = [s_ for s_ in pol["Statement"]
                if {"iam:PutRolePolicy", "iam:AttachRolePolicy"} & set(s_["Action"])]
    assert granting and all(
        s_["Condition"]["StringEquals"]["iam:PermissionsBoundary"] == boundary for s_ in granting)


def test_a_hive_tags_only_its_own_resources_and_never_as_managed():
    st = _stmt(ap.boundary_policy(), "SelfTagging")
    assert st["Action"] == ["ec2:CreateTags"]
    assert st["Condition"]["StringEquals"] == {"ec2:ResourceTag/HiveName": "${aws:PrincipalTag/HiveName}"}
    assert st["Condition"]["ForAllValues:StringNotEqualsIgnoreCase"] == {"aws:TagKeys": [ap.MANAGED_TAG]}


def test_subnet_and_exclusion_actions_only_on_managed_resources():
    """The Hive subnet's actions (hive/bpa_subnet.py): a subnet is created only tagged,
    changed or deleted only when SwarmAI's, excluded from BPA only when SwarmAI's (a VPC
    never), and BPA itself cannot be turned off. Real IAM evaluates the same cases in
    scripts/verify_permission_template.py."""
    managed = {"StringEquals": {"ec2:ResourceTag/swarmai:managed": "true"}}
    tagged = {"StringEquals": {"aws:RequestTag/swarmai:managed": "true"}}
    create = _with("ec2:CreateSubnet")
    assert {json.dumps(st["Resource"]): st.get("Condition") for st in create} == {
        json.dumps(["arn:aws:ec2:*:*:subnet/*"]): tagged, json.dumps(["arn:aws:ec2:*:*:vpc/*"]): None}
    for action in ("ec2:DeleteSubnet", "ec2:DeleteVpcBlockPublicAccessExclusion"):
        st, = _with(action)
        assert st["Condition"] == managed, action
    excl = {json.dumps(st["Resource"]): st["Condition"]
            for st in _with("ec2:CreateVpcBlockPublicAccessExclusion")}
    assert excl == {json.dumps(["arn:aws:ec2:*:*:subnet/*"]): managed,
                    json.dumps(["arn:aws:ec2:*:*:vpc-block-public-access-exclusion/*"]): tagged}
    tag, = _with("ec2:CreateTags")
    assert {"CreateSubnet", "CreateVpcBlockPublicAccessExclusion"} <= set(
        tag["Condition"]["StringEquals"]["ec2:CreateAction"])
    assert not _with("ec2:ModifyVpcBlockPublicAccessOptions")
    assert not _with("ec2:ModifySubnetAttribute")


class BpaSess(FakeSess):
    def __init__(self, *, mode="block-ingress", allowed="allowed", **kw):
        super().__init__(**kw)
        self.mode, self.allowed = mode, allowed

    def client(self, name, region_name=None):
        c = super().client(name, region_name)
        outer = self
        c.describe_vpc_block_public_access_options = lambda: {"VpcBlockPublicAccessOptions": {
            "InternetGatewayBlockMode": outer.mode, "ExclusionsAllowed": outer.allowed}}
        return c


def test_bpa_without_exclusions_cannot_build():
    hive, storage = ap.run_hive_checks(BpaSess(allowed="not-allowed"), "us-west-2",
                                       "123456789012", ARN)
    assert hive["status"] == "missing" and hive["bpa_blocked"] is True
    assert "VPC Block Public Access" in hive["detail"] and "us-west-2" in hive["detail"]
    assert storage["status"] == "ok"


def test_bpa_with_exclusions_checks_the_subnet_actions():
    sess = BpaSess(denied=["ec2:CreateVpcBlockPublicAccessExclusion"])
    hive, _ = ap.run_hive_checks(sess, "us-east-1", "123456789012", ARN)
    assert hive["status"] == "missing"
    assert hive["missing"] == ["ec2:CreateVpcBlockPublicAccessExclusion"]
    # each create on each resource type it acts on (subnet, vpc, exclusion), and its tags
    pairs = {(a, s["ResourceArns"][0].split(":")[-1].split("/")[0])
             for s in sess.simulated for a in s["ActionNames"] if a.startswith("ec2:")}
    assert {("ec2:CreateSubnet", "subnet"), ("ec2:CreateSubnet", "vpc"),
            ("ec2:CreateVpcBlockPublicAccessExclusion", "subnet"),
            ("ec2:CreateVpcBlockPublicAccessExclusion", "vpc-block-public-access-exclusion"),
            ("ec2:CreateTags", "subnet"),
            ("ec2:CreateTags", "vpc-block-public-access-exclusion")} <= pairs
    # BPA off: no subnet action is simulated, the verdict is unchanged
    sess = BpaSess(mode="off")
    hive, _ = ap.run_hive_checks(sess, "us-east-1", "123456789012", ARN)
    assert hive["status"] == "ok"
    assert "ec2:CreateSubnet" not in [a for s in sess.simulated for a in s["ActionNames"]]


def test_bpa_that_cannot_be_read_is_never_ok():
    """An identity on the older template cannot read BPA: the verdict says it could not
    confirm (it may be on), never "ok" and never "missing" (without BPA it builds)."""
    sess = FakeSess()
    real = sess.client

    def client(name, region_name=None):
        c = real(name, region_name)

        def refused():
            raise _Err("UnauthorizedOperation")
        c.describe_vpc_block_public_access_options = refused
        return c
    sess.client = client
    hive, _ = ap.run_hive_checks(sess, "us-east-1", "123456789012", ARN)
    assert hive["status"] == "unknown"
    assert "ec2:DescribeVpcBlockPublicAccessOptions (UnauthorizedOperation)" in hive["detail"]


ACCT = "123456789012"


def test_the_elastic_check_names_what_is_missing():
    sess = FakeSess(denied=["bedrock-agentcore:CreateAgentRuntime", "iam:PassRole"])
    v = ap.run_elastic_checks(sess, "us-east-1", ACCT, ARN)
    assert v["status"] == "missing"
    assert v["missing"] == ["bedrock-agentcore:CreateAgentRuntime", "iam:PassRole"]
    assert "bedrock-agentcore:CreateAgentRuntime" in v["detail"]
    simulated = {a for s_ in sess.simulated for a in s_["ActionNames"]}
    assert set(ap.ACTIONS["elastic"]) - {"ecr:DescribeImages"} <= simulated
    # each on the resource it acts on, with the values SwarmAI's calls carry
    on = {a: (s_.get("ResourceArns") or ["*"])[0] for s_ in sess.simulated for a in s_["ActionNames"]}
    assert on["iam:PassRole"] == f"arn:aws:iam::{ACCT}:role/SwarmAI-ElasticHive-us-east-1"
    assert on["bedrock-agentcore:InvokeAgentRuntime"].startswith(
        f"arn:aws:bedrock-agentcore:us-east-1:{ACCT}:runtime/swarmai_elastic_hive-")
    assert on["logs:FilterLogEvents"].startswith(
        f"arn:aws:logs:us-east-1:{ACCT}:log-group:/aws/bedrock-agentcore/runtimes/")
    ctx = {e["ContextKeyName"]: e["ContextKeyValues"][0] for s_ in sess.simulated
           if "iam:PassRole" in s_["ActionNames"] for e in s_.get("ContextEntries") or []}
    assert ctx["iam:PassedToService"] == "bedrock-agentcore.amazonaws.com"
    assert ctx["iam:PermissionsBoundary"] == f"arn:aws:iam::{ACCT}:policy/SwarmAI-Hive-Boundary"


def test_the_elastic_check_is_ok_under_the_templates_conditions_and_looks_up_a_private_image():
    sess = FakeSess()
    sess.conditions = {"bedrock-agentcore:CreateAgentRuntime": ["aws:RequestTag/swarmai:managed"],
                       "iam:PassRole": ["iam:PassedToService"], "iam:CreateRole": ["iam:PermissionsBoundary"],
                       "iam:PutRolePermissionsBoundary": ["iam:PermissionsBoundary"]}
    assert ap.run_elastic_checks(sess, "us-east-1", ACCT, ARN)["status"] == "ok"
    sess = FakeSess(denied=["ecr:DescribeImages"])
    assert ap.run_elastic_checks(sess, "us-east-1", ACCT, ARN)["status"] == "ok"   # public image: no ECR call
    v = ap.run_elastic_checks(sess, "us-east-1", ACCT, ARN, image_repo="swarmai-hive")
    assert v["status"] == "missing" and v["missing"] == ["ecr:DescribeImages"]


def test_an_elastic_check_that_cannot_simulate_is_never_ok():
    v = ap.run_elastic_checks(FakeSess(simulate_error="AccessDenied"), "us-east-1", ACCT, ARN)
    assert v["status"] == "unknown" and "could not confirm" in v["detail"]


def test_the_capability_checks_store_the_elastic_verdict(monkeypatch):
    from core import aws_account as acct

    class Any:
        def client(self, name, **kw):
            raise RuntimeError("offline")
    stored = {}
    monkeypatch.setattr(acct, "get_record", lambda: {"region": "us-east-1", "account_id": ACCT,
                                                     "caller_arn": ARN})
    monkeypatch.setattr(acct, "session", lambda region: Any())
    monkeypatch.setattr(acct, "get_chat_via", lambda config=None: "bedrock_key")
    monkeypatch.setattr(acct, "is_hive_mode", lambda: False)
    monkeypatch.setattr(acct, "_update_record", lambda **kw: stored.update(kw))
    monkeypatch.setattr(acct, "_voice_check", lambda sess, rec: {"status": "ok"})
    monkeypatch.setattr(ap, "run_hive_checks", lambda *a: ({"status": "ok"}, {"status": "ok"}))
    seen = []
    monkeypatch.setattr(ap, "run_elastic_checks", lambda sess, region, account, arn, image_repo=None:
                        seen.append((region, account, arn)) or {"status": "missing", "missing": ["x:Y"]})
    out = acct.run_capability_checks({}, "eu-west-1")
    assert out["elastic"]["status"] == "missing" and out["elastic"]["region"] == "eu-west-1"
    assert seen == [("eu-west-1", ACCT, ARN)] and stored["capabilities"]["elastic"]["missing"] == ["x:Y"]

    def boom(*a, **kw):
        raise RuntimeError("simulate failed")
    monkeypatch.setattr(ap, "run_elastic_checks", boom)
    out = acct.run_capability_checks({}, "eu-west-1")
    assert out["elastic"]["status"] == "unknown" and out["hive"]["status"] == "ok"


def test_the_template_fits_an_inline_policy():
    """IAM counts a policy without whitespace: a role's or a permission set's inline policy
    holds 10,240 characters (a customer managed policy only 6,144, which the template
    already passed before Elastic Workers)."""
    text = json.dumps(ap.template(ACCT)["policy"], separators=(",", ":"))
    assert len("".join(text.split())) <= 10240


def test_the_boundary_scopes_the_elastic_needs_to_the_account():
    b = ap.template(ACCT)["boundary"]["document"]
    pull = _stmt(b, "ElasticImagePull")
    assert _resources(pull) == [f"arn:aws:ecr:*:{ACCT}:repository/*"]
    logs = _stmt(b, "ElasticRuntimeLogs")
    assert _resources(logs) == [f"arn:aws:logs:*:{ACCT}:log-group:/aws/bedrock-agentcore/runtimes/*"]


class BoundarySess(FakeSess):
    """An account whose SwarmAI boundary is *doc* (None: there is none)."""

    def __init__(self, doc, **kw):
        super().__init__(**kw)
        self.doc = doc

    def client(self, name, region_name=None):
        c = super().client(name, region_name)
        outer = self

        def get_policy(PolicyArn):
            assert PolicyArn == f"arn:aws:iam::{ACCT}:policy/SwarmAI-Hive-Boundary"
            if outer.doc is None:
                raise _Err("NoSuchEntity")
            return {"Policy": {"DefaultVersionId": "v3"}}

        def get_policy_version(PolicyArn, VersionId):
            assert VersionId == "v3"
            return {"PolicyVersion": {"Document": outer.doc}}
        c.get_policy, c.get_policy_version = get_policy, get_policy_version
        return c


def test_a_boundary_older_than_elastic_workers_is_named():
    """The Elastic Workers role is created under the account's boundary: one made before
    Elastic Workers would take its runtime logs away, so the check names it."""
    current = ap.boundary_policy(ACCT)
    old = {"Version": "2012-10-17",
           "Statement": [st for st in current["Statement"] if not st["Sid"].startswith("Elastic")]}
    v = ap.run_elastic_checks(BoundarySess(old), "us-east-1", ACCT, ARN)
    assert v["status"] == "missing" and any("SwarmAI-Hive-Boundary" in m for m in v["missing"])
    # the current document, the one the template made without an account id, or none at all
    from urllib.parse import quote
    for doc in (current, quote(json.dumps(ap.boundary_policy())), None):
        assert ap.run_elastic_checks(BoundarySess(doc), "us-east-1", ACCT, ARN)["status"] == "ok"
