"""One action list for the permission template and the "can it create" checks
(core/aws_permissions.py; design 2026-10-31-swarmai-aws-account §7.2, §7.4).

Questions answered:
1. is every AWS API the provisioner calls in the action list (a new call without a
   permission would fail half-way through a deploy for template users)?
2. does the template stop privilege escalation: a role only with the SwarmAI boundary,
   only the SSM core policy attachable, no trust-policy change, a role passed only to EC2,
   tags only while creating, stop / start / terminate / SSM only on tagged instances?
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

PROVISIONER = Path(__file__).resolve().parents[1] / "hive" / "provisioner.py"
_SERVICE = {"cf": "cloudfront"}


def test_every_provisioner_call_is_in_the_action_list():
    src = PROVISIONER.read_text()
    calls = set(re.findall(r"\b(ec2|iam|s3|ssm|cf)\.([a-z_]+)\(", src)) - {("ec2", "get_waiter")}
    assert calls, "found no AWS calls: the pattern no longer matches the provisioner"
    missing = sorted(ap.action_for_method(_SERVICE.get(svc, svc), m) for svc, m in calls
                     if ap.action_for_method(_SERVICE.get(svc, svc), m) not in ap.all_actions())
    # S3 object/bucket calls map to their IAM action names
    missing = [a for a in missing if a not in ("s3:HeadObject", "s3:PutPublicAccessBlock")]
    assert not missing, f"provisioner calls without a permission in the list: {missing}"


def _statements(account="123456789012"):
    return ap.template(account)["policy"]["Statement"]


def _actions(st):
    return st["Action"] if isinstance(st["Action"], list) else [st["Action"]]


def test_no_wildcard_actions_and_no_trust_policy_change():
    text = json.dumps(ap.template("123456789012")["policy"])
    assert "UpdateAssumeRolePolicy" not in text
    for st in _statements():
        assert all(not a.endswith(":*") and a != "*" for a in _actions(st)), st["Sid"]


def _with(action):
    return [st for st in _statements() if action in _actions(st)]


def test_roles_only_with_the_boundary_and_only_the_ssm_core_policy():
    create, = _with("iam:CreateRole")
    assert create["Condition"] == {"StringEquals": {
        "iam:PermissionsBoundary": "arn:aws:iam::123456789012:policy/SwarmAI-Hive-Boundary"}}
    assert create["Resource"] == "arn:aws:iam::123456789012:role/SwarmAI-Hive-*"
    attach, = _with("iam:AttachRolePolicy")
    assert attach["Condition"]["ArnEquals"]["iam:PolicyARN"] == ap.SSM_CORE
    passrole, = _with("iam:PassRole")
    assert passrole["Condition"]["StringEquals"]["iam:PassedToService"] == "ec2.amazonaws.com"


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
