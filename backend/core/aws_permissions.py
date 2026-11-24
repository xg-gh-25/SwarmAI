"""What the SwarmAI AWS account must be allowed to do, from one action list (design
2026-10-31-swarmai-aws-account §7.2, §7.4).

The same list makes:
- ``template(account_id)``: the IAM policy to give an external user (or their account
  admin), plus the permissions boundary every Hive role is created with. Scoped so the
  permissions cannot be turned into an administrator: a role can only be created with
  the SwarmAI boundary, only ``AmazonSSMManagedInstanceCore`` can be attached, the trust
  policy of a Hive role cannot be changed, a Hive role can only be passed to EC2, tags can
  only be written while creating (``ec2:CreateAction``), and stop / start / terminate /
  SSM commands / console output only reach instances tagged ``swarmai:managed``.
  CloudFront distributions cannot be scoped by resource; the template says so.
  Elastic Workers (``elastic``, hive/agentcore_provisioner and core/carriers/agentcore):
  its execution role ``SwarmAI-ElasticHive-<region>`` is created and given its policy only
  with the same boundary and passed only to AgentCore; the runtime actions reach only the
  runtime named ``swarmai_elastic_hive`` (created only tagged ``swarmai:managed``), the
  logs only ``/aws/bedrock-agentcore/*``. The boundary also holds what that role's own
  policy grants (its runtime logs, pulling an image from the account's ECR).
- the "can it create" checks of ``run_hive_checks``: ``RunInstances`` and
  ``CreateSecurityGroup`` with ``DryRun=True`` (nothing is created), the IAM, S3 and
  CloudFront create actions through ``iam:SimulatePrincipalPolicy`` (a simulation that is
  itself refused reads "could not confirm", never "ok"), and the default VPC of the
  region, and VPC Block Public Access: blocking without exclusions allowed cannot build;
  blocking with exclusions allowed also simulates the Hive subnet's create actions.
- ``run_elastic_checks``: the same simulation for every action Elastic Workers needs,
  each on the resource it acts on; hive/agentcore_provisioner runs it before turning
  Elastic Workers on, so a missing permission is named before anything is made.

A test pins that every API the provisioner and the Elastic Workers modules call is in the
list.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

from hive import bucket_name

logger = logging.getLogger(__name__)

MANAGED_TAG = "swarmai:managed"
BOUNDARY_NAME = "SwarmAI-Hive-Boundary"
ROLE_PREFIX = "SwarmAI-Hive-"
#: Elastic Workers' execution role and runtime names (hive/agentcore_provisioner's
#: ROLE_PREFIX and RUNTIME_NAME; a test pins that they are the same)
ELASTIC_ROLE_PREFIX = "SwarmAI-ElasticHive-"
ELASTIC_RUNTIME_NAME = "swarmai_elastic_hive"
AGENTCORE_SERVICE = "bedrock-agentcore.amazonaws.com"
#: where AgentCore writes a runtime's logs
AGENTCORE_LOG_ROOT = "/aws/bedrock-agentcore/"
BUCKET_PREFIX = bucket_name.PREFIX
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
                 # a first boot that never ends: its console goes into the error
                 "ec2:GetConsoleOutput",
                 "ec2:DescribeInstances", "ec2:DescribeVpcs", "ec2:DescribeSecurityGroups",
                 "ec2:DescribeManagedPrefixLists", "ec2:DescribeImages", "ec2:DescribeSnapshots",
                 "ec2:DescribeVolumes", "ec2:DescribeAddresses",
                 # the Hive subnet under VPC Block Public Access (hive/bpa_subnet.py)
                 "ec2:DescribeVpcBlockPublicAccessOptions",
                 "ec2:DescribeVpcBlockPublicAccessExclusions", "ec2:DescribeSubnets",
                 "ec2:DescribeNetworkInterfaces", "ec2:DescribeInstanceTypeOfferings",
                 "ec2:CreateSubnet", "ec2:DeleteSubnet",
                 "ec2:CreateVpcBlockPublicAccessExclusion",
                 "ec2:DeleteVpcBlockPublicAccessExclusion"],
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
    # Elastic Workers (hive/agentcore_provisioner, core/carriers/agentcore), with what
    # AWS checks besides the call (Service Authorization Reference: CreateAgentRuntime
    # needs CreateAgentRuntimeEndpoint and TagResource, DeleteAgentRuntime needs
    # DeleteAgentRuntimeEndpoint and DeleteWorkloadIdentity). Its role also uses
    # hive_iam's CreateRole, GetRole, TagRole, PutRolePolicy, the two List calls and
    # PassRole; its bucket is storage's.
    "elastic": ["bedrock-agentcore:CreateAgentRuntime", "bedrock-agentcore:CreateAgentRuntimeEndpoint",
                "bedrock-agentcore:UpdateAgentRuntime", "bedrock-agentcore:GetAgentRuntime",
                "bedrock-agentcore:DeleteAgentRuntime", "bedrock-agentcore:DeleteAgentRuntimeEndpoint",
                "bedrock-agentcore:DeleteWorkloadIdentity", "bedrock-agentcore:ListAgentRuntimes",
                "bedrock-agentcore:ListTagsForResource", "bedrock-agentcore:TagResource",
                "bedrock-agentcore:InvokeAgentRuntime", "bedrock-agentcore:StopRuntimeSession",
                "iam:UpdateAssumeRolePolicy", "iam:PutRolePermissionsBoundary",
                "logs:FilterLogEvents", "logs:DescribeLogStreams", "logs:DescribeLogGroups",
                "ecr:DescribeImages", "servicequotas:ListServiceQuotas"],
    # and whether the account's boundary is the current document (run_elastic_checks)
    "checks": ["sts:GetCallerIdentity", "iam:SimulatePrincipalPolicy", "iam:GetPolicyVersion"],
}

#: the provisioner method -> action it needs (a test reads the provisioner's calls)
_METHOD_ACTION = {
    "create_distribution": "cloudfront:CreateDistribution",
    "delete_distribution": "cloudfront:DeleteDistribution",
    "get_distribution": "cloudfront:GetDistribution",
    "update_distribution": "cloudfront:UpdateDistribution",
    # HeadBucket has no IAM action of its own: S3 authorizes it as ListBucket
    "head_bucket": "s3:ListBucket",
    "list_objects_v2": "s3:ListBucket",
}


def action_for_method(service: str, method: str) -> str:
    """boto3 ``ec2.run_instances`` -> ``ec2:RunInstances``."""
    if method in _METHOD_ACTION:
        return _METHOD_ACTION[method]
    return f"{service}:" + "".join(w.capitalize() for w in method.split("_"))


def all_actions() -> set:
    return {a for actions in ACTIONS.values() for a in actions}


def boundary_policy(account_id: Optional[str] = None) -> dict:
    """The ceiling of every Hive role (and of the Elastic Workers role): what such a role
    may ever do. *account_id* scopes what is the account's own (its runtime logs, its ECR
    repositories); without a valid one, any account's."""
    acct = account_id if account_id and re.fullmatch(r"\d{12}", account_id) else "*"
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
        # what the Elastic Workers role's own policy grants (core/carriers/agentcore
        # execution_role_policy): its runtime's logs, and pulling its image from the
        # account's ECR when it is a private one
        {"Sid": "ElasticRuntimeLogs", "Effect": "Allow",
         "Action": ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents",
                    "logs:DescribeLogStreams"],
         "Resource": [f"arn:aws:logs:*:{acct}:log-group:{AGENTCORE_LOG_ROOT}runtimes/*"]},
        {"Sid": "ElasticLogGroups", "Effect": "Allow", "Action": ["logs:DescribeLogGroups"], "Resource": "*"},
        {"Sid": "ElasticImagePull", "Effect": "Allow",
         "Action": ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"],
         "Resource": [f"arn:aws:ecr:*:{acct}:repository/*"]},
        {"Sid": "ElasticImageToken", "Effect": "Allow", "Action": ["ecr:GetAuthorizationToken"],
         "Resource": "*"},
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
    elastic_role = f"arn:aws:iam::{acct}:role/{ELASTIC_ROLE_PREFIX}*"
    runtime_arn = f"arn:aws:bedrock-agentcore:*:{acct}:runtime/{ELASTIC_RUNTIME_NAME}-*"
    workload_dir = f"arn:aws:bedrock-agentcore:*:{acct}:workload-identity-directory/default"
    bounded = {"StringEquals": {"iam:PermissionsBoundary": boundary_arn}}
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
        # the Hive subnet under VPC Block Public Access (hive/bpa_subnet.py): created
        # tagged, and only a SwarmAI subnet can be excluded (a VPC never)
        {"Sid": "CreateTaggedHiveSubnet", "Effect": "Allow", "Action": ["ec2:CreateSubnet"],
         "Resource": ["arn:aws:ec2:*:*:subnet/*"], "Condition": tagged},
        {"Sid": "HiveSubnetInVpc", "Effect": "Allow", "Action": ["ec2:CreateSubnet"],
         "Resource": ["arn:aws:ec2:*:*:vpc/*"]},
        {"Sid": "ExcludeOnlyHiveSubnets", "Effect": "Allow",
         "Action": ["ec2:CreateVpcBlockPublicAccessExclusion"],
         "Resource": ["arn:aws:ec2:*:*:subnet/*"], "Condition": managed},
        {"Sid": "CreateTaggedExclusions", "Effect": "Allow",
         "Action": ["ec2:CreateVpcBlockPublicAccessExclusion"],
         "Resource": ["arn:aws:ec2:*:*:vpc-block-public-access-exclusion/*"], "Condition": tagged},
        {"Sid": "TagOnlyWhileCreating", "Effect": "Allow", "Action": ["ec2:CreateTags"],
         "Resource": "*", "Condition": {"StringEquals": {"ec2:CreateAction": [
             "RunInstances", "CreateSecurityGroup", "AllocateAddress", "CreateSnapshot",
             "CreateSubnet", "CreateVpcBlockPublicAccessExclusion"]}}},
        {"Sid": "ManageTaggedOnly", "Effect": "Allow", "Resource": "*", "Condition": managed,
         "Action": ["ec2:StartInstances", "ec2:StopInstances", "ec2:TerminateInstances",
                    "ec2:AuthorizeSecurityGroupIngress", "ec2:DeleteSecurityGroup",
                    "ec2:AssociateAddress", "ec2:ReleaseAddress", "ec2:CreateSnapshot",
                    "ec2:DeleteSubnet", "ec2:DeleteVpcBlockPublicAccessExclusion",
                    "ec2:GetConsoleOutput"]},
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
        {"Sid": "ReadBoundary", "Effect": "Allow", "Action": ["iam:GetPolicy", "iam:GetPolicyVersion"],
         "Resource": boundary_arn},
        # Elastic Workers' execution role: made, given its policy, its boundary and (when an
        # existing one is adopted) its trust only while it carries the SwarmAI boundary, so
        # a role of that name made without it can never be taken over
        {"Sid": "ElasticRoleCreateOnlyWithBoundary", "Effect": "Allow", "Action": ["iam:CreateRole"],
         "Resource": elastic_role, "Condition": bounded},
        {"Sid": "ElasticRoleChangesOnlyWithBoundary", "Effect": "Allow",
         "Action": ["iam:PutRolePolicy", "iam:PutRolePermissionsBoundary", "iam:UpdateAssumeRolePolicy"],
         "Resource": elastic_role, "Condition": bounded},
        {"Sid": "ElasticRole", "Effect": "Allow", "Resource": elastic_role,
         "Action": ["iam:GetRole", "iam:TagRole", "iam:ListRolePolicies", "iam:ListAttachedRolePolicies"]},
        {"Sid": "PassElasticRoleToAgentCoreOnly", "Effect": "Allow", "Action": ["iam:PassRole"],
         "Resource": elastic_role,
         "Condition": {"StringEquals": {"iam:PassedToService": AGENTCORE_SERVICE}}},
        # the runtime: created only tagged as SwarmAI's (CreateAgentRuntime has no resource
        # type to scope); everything after the create only on the runtime of SwarmAI's
        # name (its id starts with it), its endpoints included
        {"Sid": "ElasticRuntimeCreateTaggedOnly", "Effect": "Allow",
         "Action": ["bedrock-agentcore:CreateAgentRuntime"], "Resource": "*", "Condition": tagged},
        {"Sid": "ElasticRuntime", "Effect": "Allow", "Resource": [runtime_arn],
         "Action": ["bedrock-agentcore:CreateAgentRuntimeEndpoint", "bedrock-agentcore:UpdateAgentRuntime",
                    "bedrock-agentcore:GetAgentRuntime", "bedrock-agentcore:DeleteAgentRuntime",
                    "bedrock-agentcore:DeleteAgentRuntimeEndpoint",
                    "bedrock-agentcore:ListTagsForResource", "bedrock-agentcore:TagResource",
                    "bedrock-agentcore:InvokeAgentRuntime", "bedrock-agentcore:StopRuntimeSession"]},
        {"Sid": "ElasticRuntimeIdentity", "Effect": "Allow",
         "Action": ["bedrock-agentcore:DeleteWorkloadIdentity"],
         "Resource": [workload_dir, f"{workload_dir}/workload-identity/{ELASTIC_RUNTIME_NAME}-*"]},
        {"Sid": "ElasticLogs", "Effect": "Allow",
         "Action": ["logs:FilterLogEvents", "logs:DescribeLogStreams"],
         "Resource": [f"arn:aws:logs:*:{acct}:log-group:{AGENTCORE_LOG_ROOT}*"]},
        {"Sid": "ElasticImageLookup", "Effect": "Allow", "Action": ["ecr:DescribeImages"],
         "Resource": [f"arn:aws:ecr:*:{acct}:repository/*"]},
        # no resource type to scope these by (they list)
        {"Sid": "ElasticListsCannotBeScoped", "Effect": "Allow", "Resource": "*",
         "Action": ["bedrock-agentcore:ListAgentRuntimes", "logs:DescribeLogGroups",
                    "servicequotas:ListServiceQuotas"]},
        {"Sid": "CloudFrontCannotBeScoped", "Effect": "Allow", "Action": ACTIONS["hive_cloudfront"],
         "Resource": "*"},
    ]
    return {
        "policy": {"Version": "2012-10-17", "Statement": statements},
        "boundary": {"name": BOUNDARY_NAME, "document": boundary_policy(account_id)},
        "notes": [
            f"Create the boundary policy {BOUNDARY_NAME} in the account first; every Hive role is "
            "created with it as its permissions boundary.",
            "CloudFront distributions cannot be scoped by resource in IAM; neither can "
            "CreateAgentRuntime (allowed only tagged swarmai:managed), ListAgentRuntimes, "
            "DescribeLogGroups and ListServiceQuotas.",
            f"iam:UpdateAssumeRolePolicy is allowed only on the Elastic Workers role "
            f"({ELASTIC_ROLE_PREFIX}*) while it carries {BOUNDARY_NAME}, never on a Hive role.",
            f"An account whose {BOUNDARY_NAME} predates Elastic Workers updates it to this boundary "
            "document: the Elastic Workers role is created with it and needs its runtime logs.",
            "IAM cannot name the default VPC: a swarmai:managed subnet and its VPC Block Public "
            "Access exclusion can be created in any VPC of the account (SwarmAI uses only the "
            "default VPC's); turning BPA off or excluding a whole VPC is not allowed.",
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


def _context(account_id: str, passed_to: str = "ec2.amazonaws.com") -> list:
    """The request values SwarmAI's own create calls carry, so a policy that allows them
    only under a condition (the template) is simulated as it is evaluated for real.
    *passed_to*: the service the role is passed to (EC2 for a Hive role, AgentCore for
    the Elastic Workers role)."""
    def entry(key: str, value: str) -> dict:
        return {"ContextKeyName": key, "ContextKeyValues": [value], "ContextKeyType": "string"}
    return [entry("iam:PermissionsBoundary", f"arn:aws:iam::{account_id}:policy/{BOUNDARY_NAME}"),
            entry("iam:PolicyARN", SSM_CORE),
            entry("iam:PassedToService", passed_to),
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
    # VPC Block Public Access (hive/bpa_subnet.py): blocking with exclusions not allowed
    # means CloudFront can never reach a Hive here; blocking with exclusions allowed needs
    # the Hive subnet's create actions. Unreadable or off: nothing to add.
    from hive import bpa_subnet
    bpa, bpa_problem = bpa_subnet.read_bpa(ec2)
    if bpa_problem:
        # not "ok": BPA may be on and the Hive unreachable; not "missing" either: an account
        # without BPA builds as before (the deploy launches into the default subnet)
        unknown.append(f"ec2:DescribeVpcBlockPublicAccessOptions ({bpa_problem})")
    bpa_blocked = bool(bpa and bpa["blocking"] and not bpa["exclusions_allowed"])
    bpa_subnet_needed = bool(bpa and bpa["blocking"] and bpa["exclusions_allowed"])
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
    if bpa_subnet_needed:
        arn = f"arn:aws:ec2:{region}:{account_id}"
        def ctx(key: str, value: str) -> dict:
            return {"ContextKeyName": key, "ContextKeyValues": [value], "ContextKeyType": "string"}
        managed_rt = ctx(f"ec2:ResourceTag/{MANAGED_TAG}", "true")
        # each create of the Hive subnet's launch, on each resource type it acts on, with
        # the values its call carries (hive/bpa_subnet.py)
        for actions, res, context in (
                (["ec2:CreateSubnet"], f"{arn}:subnet/*", _context(account_id)),
                (["ec2:CreateSubnet"], f"{arn}:vpc/*", []),
                (["ec2:CreateVpcBlockPublicAccessExclusion"], f"{arn}:subnet/*", [managed_rt]),
                (["ec2:CreateVpcBlockPublicAccessExclusion"],
                 f"{arn}:vpc-block-public-access-exclusion/*", _context(account_id)),
                (["ec2:CreateTags"], f"{arn}:subnet/*", [ctx("ec2:CreateAction", "CreateSubnet")]),
                (["ec2:CreateTags"], f"{arn}:vpc-block-public-access-exclusion/*",
                 [ctx("ec2:CreateAction", "CreateVpcBlockPublicAccessExclusion")])):
            denied, problem = _simulate(sess, principal, actions, [res], context or None)
            missing += denied
            if problem:
                unknown.append(f"VPC Block Public Access subnet: {problem}")
                break
    # the bucket a new deployment creates (hive/bucket_name.py)
    bucket = f"arn:aws:s3:::{bucket_name.canonical(account_id, region)}"
    s_denied, s_problem = _simulate(sess, principal, ["s3:CreateBucket", "s3:PutObject",
                                                      "s3:GetObject", "s3:ListBucket"],
                                    [bucket, bucket + "/*"])
    if missing or no_vpc or bpa_blocked:
        detail = "; ".join(([f"no default VPC in {region}"] if no_vpc else []) +
                           ([bpa_subnet.blocked_message(region)] if bpa_blocked else []) +
                           ([f"missing {', '.join(sorted(set(missing)))}"] if missing else []))
        hive = {"status": "missing", "detail": detail, "missing": sorted(set(missing)),
                "no_default_vpc": no_vpc}
        if bpa_blocked:
            hive["bpa_blocked"] = True
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


def run_elastic_checks(sess: Any, region: str, account_id: str, caller_arn: str,
                       image_repo: Optional[str] = None) -> dict:
    """Can this identity turn Elastic Workers on in *region* and run it: every ``elastic``
    action (and the role, bucket and image calls it shares with a Hive) simulated on the
    resource it acts on, with the values SwarmAI's calls carry. Nothing is created.
    *image_repo*: the account's private ECR repository the image comes from (None for
    public ECR, which needs no ECR call). ``missing`` names the denied actions;
    ``unknown`` when the simulation cannot run (never ``ok`` then)."""
    principal = principal_arn(sess, caller_arn)
    base = f"arn:aws:bedrock-agentcore:{region}:{account_id}"
    runtime = f"{base}:runtime/{ELASTIC_RUNTIME_NAME}-check"
    role = f"arn:aws:iam::{account_id}:role/{ELASTIC_ROLE_PREFIX}{region}"
    bucket = f"arn:aws:s3:::{bucket_name.canonical(account_id, region)}"
    runtime_actions = [a for a in ACTIONS["elastic"] if a.startswith("bedrock-agentcore:")
                       and a not in ("bedrock-agentcore:CreateAgentRuntime", "bedrock-agentcore:ListAgentRuntimes",
                                     "bedrock-agentcore:DeleteWorkloadIdentity")]
    checks: list = [
        (["bedrock-agentcore:CreateAgentRuntime"], None, _context(account_id, AGENTCORE_SERVICE)),
        (runtime_actions, [runtime], None),
        (["bedrock-agentcore:DeleteWorkloadIdentity"],
         [f"{base}:workload-identity-directory/default/workload-identity/{ELASTIC_RUNTIME_NAME}-check"], None),
        (["bedrock-agentcore:ListAgentRuntimes", "logs:DescribeLogGroups", "servicequotas:ListServiceQuotas"],
         None, None),
        ([a for a in ACTIONS["elastic"] if a in ("logs:FilterLogEvents", "logs:DescribeLogStreams")],
         [f"arn:aws:logs:{region}:{account_id}:log-group:{AGENTCORE_LOG_ROOT}runtimes/"
          f"{ELASTIC_RUNTIME_NAME}-check-DEFAULT"], None),
        (["iam:CreateRole", "iam:PutRolePolicy", "iam:PutRolePermissionsBoundary", "iam:UpdateAssumeRolePolicy",
          "iam:GetRole", "iam:TagRole", "iam:ListRolePolicies", "iam:ListAttachedRolePolicies", "iam:PassRole"],
         [role], _context(account_id, AGENTCORE_SERVICE)),
        (["s3:CreateBucket", "s3:PutBucketPublicAccessBlock", "s3:ListBucket", "s3:GetObject", "s3:PutObject"],
         [bucket, bucket + "/*"], None),
    ]
    if image_repo:
        checks.append((["ecr:DescribeImages"], [f"arn:aws:ecr:{region}:{account_id}:repository/{image_repo}"], None))
    missing: list = []
    for actions, resources, context in checks:
        denied, problem = _simulate(sess, principal, actions, resources, context)
        missing += denied
        if problem:
            return {"status": "unknown", "detail": f"could not confirm: {problem}"}
    if _boundary_outdated(sess, account_id):
        missing.append(f"{BOUNDARY_NAME} (update it to the template's boundary document)")
    if missing:
        names = sorted(set(missing))
        return {"status": "missing", "detail": f"missing {', '.join(names)}", "missing": names}
    return {"status": "ok", "detail": f"can turn Elastic Workers on in {region}"}


def _document_allows(doc: Any, action: str, resource: str) -> bool:
    from fnmatch import fnmatchcase
    rows = doc.get("Statement") if isinstance(doc, dict) else None
    for st in rows if isinstance(rows, list) else [rows] if isinstance(rows, dict) else []:
        if not isinstance(st, dict) or st.get("Effect") != "Allow":
            continue
        acts, res = st.get("Action") or [], st.get("Resource") or []
        acts, res = [acts] if isinstance(acts, str) else acts, [res] if isinstance(res, str) else res
        if any(fnmatchcase(action, a) for a in acts if isinstance(a, str)) \
                and any(fnmatchcase(resource, r) for r in res if isinstance(r, str)):
            return True
    return False


def _boundary_outdated(sess: Any, account_id: str) -> bool:
    """True when the account's SwarmAI boundary exists but its document does not allow what
    the Elastic Workers role's own policy needs (a boundary made before Elastic Workers:
    the role it caps would lose its logs and image pull). False when it is current, does
    not exist, or cannot be read (nothing to say for sure)."""
    from urllib.parse import unquote
    arn = f"arn:aws:iam::{account_id}:policy/{BOUNDARY_NAME}"
    try:
        iam = sess.client("iam")
        version = iam.get_policy(PolicyArn=arn)["Policy"]["DefaultVersionId"]
        doc = iam.get_policy_version(PolicyArn=arn, VersionId=version)["PolicyVersion"]["Document"]
        if isinstance(doc, str):
            doc = json.loads(unquote(doc))
    except Exception as exc:  # noqa: BLE001 — absent or unreadable: nothing to say
        logger.debug("aws_permissions: boundary not read (%s)", _code(exc))
        return False
    for st in boundary_policy(account_id)["Statement"]:
        if not st["Sid"].startswith("Elastic"):
            continue
        res = st["Resource"] if isinstance(st["Resource"], list) else [st["Resource"]]
        if not all(_document_allows(doc, a, r) for a in st["Action"] for r in res):
            return True
    return False
