"""What the SwarmAI AWS account must be allowed to do, from one action list (design
2026-10-31-swarmai-aws-account §7.2, §7.4).

The same list makes:
- ``template(account_id)``: the IAM policy to give an external user (or their account
  admin), plus the permissions boundary every Hive role is created with. Scoped so the
  permissions cannot be turned into an administrator: a role can only be created with
  the SwarmAI boundary, only ``AmazonSSMManagedInstanceCore`` can be attached, the trust
  policy cannot be changed, a role can only be passed to EC2, tags can only be written
  while creating (``ec2:CreateAction``), and stop / start / terminate / SSM commands
  only reach instances tagged ``swarmai:managed``. CloudFront distributions cannot be
  scoped by resource; the template says so.
- the "can it create" checks of ``run_hive_checks``: ``RunInstances`` and
  ``CreateSecurityGroup`` with ``DryRun=True`` (nothing is created), the IAM, S3 and
  CloudFront create actions through ``iam:SimulatePrincipalPolicy`` (a simulation that is
  itself refused reads "could not confirm", never "ok"), and the default VPC of the
  region.

A test pins that every API the provisioner calls is in the list.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

logger = logging.getLogger(__name__)

MANAGED_TAG = "swarmai:managed"
BOUNDARY_NAME = "SwarmAI-Hive-Boundary"
ROLE_PREFIX = "SwarmAI-Hive-"
BUCKET_PREFIX = "swarmai-hive-"
SSM_CORE = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
AL2023_ARM64 = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-arm64"

#: capability -> the actions it needs (the source for the template and the checks)
ACTIONS: dict[str, list[str]] = {
    "bedrock": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream",
                "bedrock:ListFoundationModels", "bedrock:ListInferenceProfiles"],
    "voice": ["polly:SynthesizeSpeech", "polly:DescribeVoices",
              "transcribe:StartStreamTranscription", "transcribe:StartStreamTranscriptionWebSocket"],
    # done by the person from the capability line, only when the region has none
    "account_setup": ["ec2:CreateDefaultVpc"],
    "storage": ["s3:CreateBucket", "s3:PutBucketPublicAccessBlock", "s3:ListBucket",
                "s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:ListAllMyBuckets"],
    "hive_ec2": ["ec2:RunInstances", "ec2:CreateSecurityGroup", "ec2:AuthorizeSecurityGroupIngress",
                 "ec2:CreateTags", "ec2:AllocateAddress", "ec2:AssociateAddress",
                 "ec2:ReleaseAddress", "ec2:StartInstances", "ec2:StopInstances",
                 "ec2:TerminateInstances", "ec2:DeleteSecurityGroup", "ec2:CreateSnapshot",
                 "ec2:DescribeInstances", "ec2:DescribeVpcs", "ec2:DescribeSecurityGroups",
                 "ec2:DescribeManagedPrefixLists", "ec2:DescribeImages", "ec2:DescribeSnapshots",
                 "ec2:DescribeVolumes", "ec2:DescribeAddresses"],
    "hive_iam": ["iam:CreateRole", "iam:GetRole", "iam:DeleteRole", "iam:TagRole",
                 "iam:PutRolePolicy", "iam:DeleteRolePolicy", "iam:AttachRolePolicy",
                 "iam:DetachRolePolicy", "iam:ListRolePolicies", "iam:ListAttachedRolePolicies",
                 "iam:CreateInstanceProfile", "iam:GetInstanceProfile",
                 "iam:AddRoleToInstanceProfile", "iam:RemoveRoleFromInstanceProfile",
                 "iam:DeleteInstanceProfile", "iam:PassRole", "iam:GetPolicy"],
    "hive_ssm": ["ssm:SendCommand", "ssm:GetCommandInvocation", "ssm:GetParameter"],
    "hive_cloudfront": ["cloudfront:CreateDistribution", "cloudfront:GetDistribution",
                        "cloudfront:GetDistributionConfig", "cloudfront:UpdateDistribution",
                        "cloudfront:DeleteDistribution", "cloudfront:ListDistributions"],
    "checks": ["sts:GetCallerIdentity", "iam:SimulatePrincipalPolicy"],
}

#: the provisioner method -> action it needs (a test reads the provisioner's calls)
_METHOD_ACTION = {
    "create_distribution": "cloudfront:CreateDistribution",
    "delete_distribution": "cloudfront:DeleteDistribution",
    "get_distribution": "cloudfront:GetDistribution",
    "update_distribution": "cloudfront:UpdateDistribution",
}


def action_for_method(service: str, method: str) -> str:
    """boto3 ``ec2.run_instances`` -> ``ec2:RunInstances``."""
    if method in _METHOD_ACTION:
        return _METHOD_ACTION[method]
    return f"{service}:" + "".join(w.capitalize() for w in method.split("_"))


def all_actions() -> set:
    return {a for actions in ACTIONS.values() for a in actions}


def boundary_policy() -> dict:
    """The ceiling of every Hive role: what a Hive's own role may ever do."""
    return {"Version": "2012-10-17", "Statement": [
        {"Sid": "Model", "Effect": "Allow", "Resource": "*",
         "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream",
                    "bedrock:ListFoundationModels"]},
        {"Sid": "SwarmAIBuckets", "Effect": "Allow",
         "Action": ["s3:GetObject", "s3:PutObject", "s3:ListBucket"],
         "Resource": [f"arn:aws:s3:::{BUCKET_PREFIX}*", f"arn:aws:s3:::{BUCKET_PREFIX}*/*"]},
        {"Sid": "SsmAgent", "Effect": "Allow", "Resource": "*",
         "Action": ["ssm:UpdateInstanceInformation", "ssm:ListInstanceAssociations",
                    "ssm:DescribeAssociation", "ssm:GetDocument", "ssm:DescribeDocument",
                    "ssm:UpdateInstanceAssociationStatus", "ssm:PutInventory",
                    "ssm:GetParameter", "ssm:GetParameters", "ssmmessages:*", "ec2messages:*"]},
        {"Sid": "DescribeTags", "Effect": "Allow", "Action": ["ec2:DescribeTags"], "Resource": "*"},
        # a Hive tags only its own resources (same HiveName as its role), and never with
        # swarmai:managed: that tag is what lets the SwarmAI identity stop, terminate and
        # run commands on an instance (ManageTaggedOnly, SsmOnTaggedInstances)
        {"Sid": "SelfTagging", "Effect": "Allow", "Action": ["ec2:CreateTags"], "Resource": "*",
         "Condition": {"StringEquals": {"ec2:ResourceTag/HiveName": "${aws:PrincipalTag/HiveName}"},
                       # tag keys match without case in conditions: refuse every spelling
                       "ForAllValues:StringNotEqualsIgnoreCase": {"aws:TagKeys": [MANAGED_TAG]}}},
    ]}


def template(account_id: Optional[str] = None) -> dict:
    """The IAM policy for the SwarmAI account's identity (an Identity Center permission
    set, a role or a user), and the boundary policy to create next to it."""
    acct = account_id if account_id and re.fullmatch(r"\d{12}", account_id) else "*"
    role_arn = f"arn:aws:iam::{acct}:role/{ROLE_PREFIX}*"
    profile_arn = f"arn:aws:iam::{acct}:instance-profile/{ROLE_PREFIX}*"
    boundary_arn = f"arn:aws:iam::{acct}:policy/{BOUNDARY_NAME}"
    tagged = {"StringEquals": {f"aws:RequestTag/{MANAGED_TAG}": "true"}}
    managed = {"StringEquals": {f"ec2:ResourceTag/{MANAGED_TAG}": "true"}}
    statements: list = [
        {"Sid": "Identity", "Effect": "Allow", "Action": ACTIONS["checks"], "Resource": "*"},
        {"Sid": "Bedrock", "Effect": "Allow", "Action": ACTIONS["bedrock"], "Resource": "*"},
        {"Sid": "Voice", "Effect": "Allow", "Action": ACTIONS["voice"], "Resource": "*"},
        {"Sid": "ListBuckets", "Effect": "Allow", "Action": ["s3:ListAllMyBuckets"], "Resource": "*"},
        {"Sid": "SwarmAIBuckets", "Effect": "Allow",
         "Action": [a for a in ACTIONS["storage"] if a != "s3:ListAllMyBuckets"],
         "Resource": [f"arn:aws:s3:::{BUCKET_PREFIX}*", f"arn:aws:s3:::{BUCKET_PREFIX}*/*"]},
        {"Sid": "DefaultVpc", "Effect": "Allow", "Action": ACTIONS["account_setup"], "Resource": "*"},
        {"Sid": "Ec2Read", "Effect": "Allow", "Resource": "*",
         "Action": [a for a in ACTIONS["hive_ec2"] if ":Describe" in a]},
        {"Sid": "RunTaggedInstances", "Effect": "Allow", "Action": ["ec2:RunInstances"],
         "Resource": ["arn:aws:ec2:*:*:instance/*", "arn:aws:ec2:*:*:volume/*",
                      "arn:aws:ec2:*:*:network-interface/*"], "Condition": tagged},
        {"Sid": "RunWithExisting", "Effect": "Allow", "Action": ["ec2:RunInstances"],
         "Resource": ["arn:aws:ec2:*::image/*", "arn:aws:ec2:*:*:subnet/*",
                      "arn:aws:ec2:*:*:security-group/*", "arn:aws:ec2:*:*:key-pair/*"]},
        {"Sid": "CreateTaggedGroupsAndAddresses", "Effect": "Allow",
         "Action": ["ec2:CreateSecurityGroup", "ec2:AllocateAddress"],
         "Resource": ["arn:aws:ec2:*:*:security-group/*", "arn:aws:ec2:*:*:elastic-ip/*"],
         "Condition": tagged},
        {"Sid": "GroupsInVpc", "Effect": "Allow", "Action": ["ec2:CreateSecurityGroup"],
         "Resource": ["arn:aws:ec2:*:*:vpc/*"]},
        {"Sid": "TagOnlyWhileCreating", "Effect": "Allow", "Action": ["ec2:CreateTags"],
         "Resource": "*", "Condition": {"StringEquals": {"ec2:CreateAction": [
             "RunInstances", "CreateSecurityGroup", "AllocateAddress", "CreateSnapshot"]}}},
        {"Sid": "ManageTaggedOnly", "Effect": "Allow", "Resource": "*", "Condition": managed,
         "Action": ["ec2:StartInstances", "ec2:StopInstances", "ec2:TerminateInstances",
                    "ec2:AuthorizeSecurityGroupIngress", "ec2:DeleteSecurityGroup",
                    "ec2:AssociateAddress", "ec2:ReleaseAddress", "ec2:CreateSnapshot"]},
        {"Sid": "SsmOnTaggedInstances", "Effect": "Allow", "Action": ["ssm:SendCommand"],
         "Resource": ["arn:aws:ec2:*:*:instance/*"],
         "Condition": {"StringEquals": {f"ssm:resourceTag/{MANAGED_TAG}": "true"}}},
        {"Sid": "SsmDocument", "Effect": "Allow", "Action": ["ssm:SendCommand"],
         "Resource": ["arn:aws:ssm:*::document/AWS-RunShellScript"]},
        {"Sid": "SsmRead", "Effect": "Allow", "Action": ["ssm:GetCommandInvocation"], "Resource": "*"},
        {"Sid": "AmiLookup", "Effect": "Allow", "Action": ["ssm:GetParameter"],
         "Resource": ["arn:aws:ssm:*::parameter/aws/service/ami-amazon-linux-latest/*"]},
        {"Sid": "CreateRoleOnlyWithBoundary", "Effect": "Allow", "Action": ["iam:CreateRole"],
         "Resource": role_arn,
         "Condition": {"StringEquals": {"iam:PermissionsBoundary": boundary_arn}}},
        # granting a role permissions needs the role to carry the boundary: a Hive role
        # made without it (before the template, or by someone else) cannot be widened
        {"Sid": "AttachOnlySsmCoreToBoundedRoles", "Effect": "Allow",
         "Action": ["iam:AttachRolePolicy"], "Resource": role_arn,
         "Condition": {"ArnEquals": {"iam:PolicyARN": SSM_CORE},
                       "StringEquals": {"iam:PermissionsBoundary": boundary_arn}}},
        {"Sid": "DetachOnlySsmCore", "Effect": "Allow", "Action": ["iam:DetachRolePolicy"],
         "Resource": role_arn, "Condition": {"ArnEquals": {"iam:PolicyARN": SSM_CORE}}},
        {"Sid": "InlinePolicyOnlyOnBoundedRoles", "Effect": "Allow", "Action": ["iam:PutRolePolicy"],
         "Resource": role_arn,
         "Condition": {"StringEquals": {"iam:PermissionsBoundary": boundary_arn}}},
        {"Sid": "HiveRoles", "Effect": "Allow", "Resource": [role_arn, profile_arn],
         "Action": ["iam:GetRole", "iam:DeleteRole", "iam:TagRole",
                    "iam:DeleteRolePolicy", "iam:ListRolePolicies",
                    "iam:ListAttachedRolePolicies", "iam:CreateInstanceProfile",
                    "iam:GetInstanceProfile", "iam:AddRoleToInstanceProfile",
                    "iam:RemoveRoleFromInstanceProfile", "iam:DeleteInstanceProfile"]},
        {"Sid": "PassHiveRolesToEc2Only", "Effect": "Allow", "Action": ["iam:PassRole"],
         "Resource": role_arn,
         "Condition": {"StringEquals": {"iam:PassedToService": "ec2.amazonaws.com"}}},
        {"Sid": "ReadBoundary", "Effect": "Allow", "Action": ["iam:GetPolicy"], "Resource": boundary_arn},
        {"Sid": "CloudFrontCannotBeScoped", "Effect": "Allow", "Action": ACTIONS["hive_cloudfront"],
         "Resource": "*"},
    ]
    return {
        "policy": {"Version": "2012-10-17", "Statement": statements},
        "boundary": {"name": BOUNDARY_NAME, "document": boundary_policy()},
        "notes": [
            f"Create the boundary policy {BOUNDARY_NAME} in the account first; every Hive role is "
            "created with it as its permissions boundary.",
            "CloudFront distributions cannot be scoped by resource in IAM.",
            "iam:UpdateAssumeRolePolicy is deliberately absent.",
        ],
    }


def template_text(account_id: Optional[str] = None) -> str:
    return json.dumps(template(account_id), indent=2)


# ── the "can it create" checks ────────────────────────────────────────────────

_DENIED = {"AccessDenied", "AccessDeniedException", "UnauthorizedOperation",
           "UnauthorizedException", "AuthorizationError", "AuthFailure"}


def _code(exc: Exception) -> str:
    resp = getattr(exc, "response", None)
    if isinstance(resp, dict):
        return (resp.get("Error") or {}).get("Code", "") or type(exc).__name__
    return type(exc).__name__


def _dry_run(call) -> Optional[str]:
    """``None`` = allowed (DryRunOperation), ``"missing"`` = refused, else the code."""
    try:
        call()
    except Exception as exc:  # noqa: BLE001
        code = _code(exc)
        if code == "DryRunOperation":
            return None
        return "missing" if code in _DENIED else code
    return None  # a call that succeeded with DryRun=True cannot happen; treat as allowed


def principal_arn(sess: Any, caller_arn: str) -> Optional[str]:
    """The IAM ARN to simulate for the caller: the role behind an assumed-role session
    (looked up for its path), the user ARN as is."""
    m = re.match(r"arn:(aws[\w-]*):sts::(\d{12}):assumed-role/([\w+=,.@-]+)/", caller_arn or "")
    if not m:
        return caller_arn if ":user/" in (caller_arn or "") else None
    try:
        return sess.client("iam").get_role(RoleName=m.group(3))["Role"]["Arn"]
    except Exception as exc:  # noqa: BLE001 — no iam:GetRole: try the role at the root path
        logger.debug("aws_permissions: get_role(%s) failed (%s): simulating the root path",
                     m.group(3), _code(exc))
        return f"arn:{m.group(1)}:iam::{m.group(2)}:role/{m.group(3)}"


def _context(account_id: str) -> list:
    """The request values SwarmAI's own create calls carry, so a policy that allows them
    only under a condition (the template) is simulated as it is evaluated for real."""
    def entry(key: str, value: str) -> dict:
        return {"ContextKeyName": key, "ContextKeyValues": [value], "ContextKeyType": "string"}
    return [entry("iam:PermissionsBoundary", f"arn:aws:iam::{account_id}:policy/{BOUNDARY_NAME}"),
            entry("iam:PolicyARN", SSM_CORE),
            entry("iam:PassedToService", "ec2.amazonaws.com"),
            entry(f"aws:RequestTag/{MANAGED_TAG}", "true")]


def _simulate(sess: Any, principal: Optional[str], actions: list,
              resources: Optional[list] = None,
              context: Optional[list] = None) -> "tuple[list, Optional[str]]":
    """(denied actions, problem). A simulation that cannot run returns its problem; an
    action refused only because a condition needs a value not given is "could not
    confirm", never "missing"."""
    if not principal:
        return [], "could not tell which IAM identity this is"
    try:
        kwargs: dict = {"PolicySourceArn": principal, "ActionNames": actions}
        if resources:
            kwargs["ResourceArns"] = resources
        if context:
            kwargs["ContextEntries"] = context
        out = sess.client("iam").simulate_principal_policy(**kwargs)
    except Exception as exc:  # noqa: BLE001
        return [], f"could not simulate the policy ({_code(exc)})"
    denied, unsure = [], []
    for r in out.get("EvaluationResults", []):
        if r.get("EvalDecision") == "allowed":
            continue
        (unsure if r.get("MissingContextValues") else denied).append(r["EvalActionName"])
    return denied, (f"needs a condition value to confirm {', '.join(unsure)}" if unsure else None)


def run_hive_checks(sess: Any, region: str, account_id: str, caller_arn: str) -> "tuple[dict, dict]":
    """(hive verdict, storage verdict): can this identity CREATE a Hive and its bucket
    in *region*. Nothing is created (DryRun, simulation, reads)."""
    ec2 = sess.client("ec2", region_name=region)
    missing: list = []
    unknown: list = []
    vpc_id = None
    try:
        vpcs = ec2.describe_vpcs(Filters=[{"Name": "isDefault", "Values": ["true"]}]).get("Vpcs", [])
        vpc_id = vpcs[0]["VpcId"] if vpcs else None
    except Exception as exc:  # noqa: BLE001
        (missing if _code(exc) in _DENIED else unknown).append(f"ec2:DescribeVpcs ({_code(exc)})")
    no_vpc = vpc_id is None and not missing and not unknown
    tags = [{"Key": MANAGED_TAG, "Value": "true"}]
    try:
        ami = sess.client("ssm", region_name=region).get_parameter(Name=AL2023_ARM64)["Parameter"]["Value"]
    except Exception as exc:  # noqa: BLE001
        ami = None
        (missing if _code(exc) in _DENIED else unknown).append(f"ssm:GetParameter ({_code(exc)})")
    if ami:
        # tagged exactly like the provisioner's launch (instance, volume, network interface)
        r = _dry_run(lambda: ec2.run_instances(
            DryRun=True, ImageId=ami, InstanceType="m7g.xlarge", MinCount=1, MaxCount=1,
            TagSpecifications=[{"ResourceType": t, "Tags": tags}
                               for t in ("instance", "volume", "network-interface")]))
        if r == "missing":
            missing.append("ec2:RunInstances")
        elif r:
            unknown.append(f"ec2:RunInstances ({r})")
    if vpc_id:
        r = _dry_run(lambda: ec2.create_security_group(
            DryRun=True, GroupName="swarmai-check", Description="SwarmAI permission check",
            VpcId=vpc_id, TagSpecifications=[{"ResourceType": "security-group", "Tags": tags}]))
        if r == "missing":
            missing.append("ec2:CreateSecurityGroup")
        elif r:
            unknown.append(f"ec2:CreateSecurityGroup ({r})")
    principal = principal_arn(sess, caller_arn)
    # each action is simulated on the resource type it acts on (a role action on an
    # instance profile ARN reads as denied: measured)
    for actions, arn in (
            (["iam:CreateRole", "iam:PutRolePolicy", "iam:AttachRolePolicy", "iam:PassRole"],
             f"arn:aws:iam::{account_id}:role/{ROLE_PREFIX}check"),
            (["iam:CreateInstanceProfile", "iam:AddRoleToInstanceProfile"],
             f"arn:aws:iam::{account_id}:instance-profile/{ROLE_PREFIX}check")):
        denied, problem = _simulate(sess, principal, actions, [arn], _context(account_id))
        missing += denied
        if problem:
            unknown.append(f"IAM: {problem}")
            break
    denied, problem = _simulate(sess, principal, ["cloudfront:CreateDistribution",
                                                  "cloudfront:UpdateDistribution"])
    missing += denied
    if problem:
        unknown.append(f"CloudFront: {problem}")
    bucket = f"arn:aws:s3:::{BUCKET_PREFIX}{account_id}-{region}"
    s_denied, s_problem = _simulate(sess, principal, ["s3:CreateBucket", "s3:PutObject",
                                                      "s3:GetObject", "s3:ListBucket"],
                                    [bucket, bucket + "/*"])
    if missing or no_vpc:
        detail = "; ".join(([f"no default VPC in {region}"] if no_vpc else []) +
                           ([f"missing {', '.join(sorted(set(missing)))}"] if missing else []))
        hive = {"status": "missing", "detail": detail, "missing": sorted(set(missing)),
                "no_default_vpc": no_vpc}
    elif unknown:
        hive = {"status": "unknown", "detail": "could not confirm: " + "; ".join(unknown)}
    else:
        hive = {"status": "ok", "detail": f"can create a Hive in {region}"}
    if s_denied:
        storage = {"status": "missing", "detail": f"missing {', '.join(s_denied)}", "missing": s_denied}
    elif s_problem:
        storage = {"status": "unknown", "detail": f"could not confirm: {s_problem}"}
    else:
        storage = {"status": "ok", "detail": "can create and use the SwarmAI bucket"}
    return hive, storage
