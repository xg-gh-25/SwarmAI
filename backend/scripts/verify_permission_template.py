"""verify_permission_template — have real IAM evaluate the SwarmAI permission template
(design docs/design/2026-10-31-swarmai-aws-account.md §7.4, §9 11).

The unit tests check the template's shape; this asks IAM itself (``SimulateCustomPolicy``,
read-only: nothing is created or attached) whether the template and the Hive role
boundary allow what SwarmAI does and refuse the privilege escalations §7.4 names. It
needs an AWS identity allowed to call ``iam:SimulateCustomPolicy`` (any profile; it does
not have to be the SwarmAI account) and the network, so it is not part of CI.

    python -m scripts.verify_permission_template [--profile NAME] [--account 123456789012]

Exit 0 when every case comes out as expected; 1 otherwise (each case is printed).
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Optional

from core import aws_permissions as ap

#: (description, which policy, action, resource, context {key: value}, expected allowed)
Case = "tuple[str, str, str, str, dict, bool]"


def cases(acct: str) -> list:
    role = f"arn:aws:iam::{acct}:role/{ap.ROLE_PREFIX}x"
    other_role = f"arn:aws:iam::{acct}:role/Admin"
    boundary = f"arn:aws:iam::{acct}:policy/{ap.BOUNDARY_NAME}"
    instance = f"arn:aws:ec2:us-east-1:{acct}:instance/i-0123456789abcdef0"
    managed_rt = {f"ec2:ResourceTag/{ap.MANAGED_TAG}": "true"}
    return [
        # what SwarmAI does
        ("create a Hive role with the boundary", "policy", "iam:CreateRole", role,
         {"iam:PermissionsBoundary": boundary}, True),
        ("write the Hive role's inline policy (bounded role)", "policy", "iam:PutRolePolicy", role,
         {"iam:PermissionsBoundary": boundary}, True),
        ("attach SSM core to a bounded Hive role", "policy", "iam:AttachRolePolicy", role,
         {"iam:PolicyARN": ap.SSM_CORE, "iam:PermissionsBoundary": boundary}, True),
        ("pass a Hive role to EC2", "policy", "iam:PassRole", role,
         {"iam:PassedToService": "ec2.amazonaws.com"}, True),
        ("launch a tagged instance", "policy", "ec2:RunInstances", instance,
         {f"aws:RequestTag/{ap.MANAGED_TAG}": "true"}, True),
        ("tag while launching", "policy", "ec2:CreateTags", instance,
         {"ec2:CreateAction": "RunInstances"}, True),
        ("stop a SwarmAI instance", "policy", "ec2:StopInstances", instance, managed_rt, True),
        ("run a command on a SwarmAI instance", "policy", "ssm:SendCommand", instance,
         {f"ssm:resourceTag/{ap.MANAGED_TAG}": "true"}, True),
        ("a Hive tags its own instance", "boundary", "ec2:CreateTags", instance,
         {"ec2:ResourceTag/HiveName": "a", "aws:PrincipalTag/HiveName": "a",
          "aws:TagKeys": "HiveStatus"}, True),
        # the escalations §7.4 names
        ("create a role without the boundary", "policy", "iam:CreateRole", role, {}, False),
        ("create a role with another boundary", "policy", "iam:CreateRole", role,
         {"iam:PermissionsBoundary": f"arn:aws:iam::{acct}:policy/Other"}, False),
        ("widen an unbounded Hive role inline", "policy", "iam:PutRolePolicy", role, {}, False),
        ("attach AdministratorAccess", "policy", "iam:AttachRolePolicy", role,
         {"iam:PolicyARN": "arn:aws:iam::aws:policy/AdministratorAccess",
          "iam:PermissionsBoundary": boundary}, False),
        ("change a role's trust policy", "policy", "iam:UpdateAssumeRolePolicy", role, {}, False),
        ("remove a role's boundary", "policy", "iam:DeleteRolePermissionsBoundary", role, {}, False),
        ("set a role's boundary to anything", "policy", "iam:PutRolePermissionsBoundary", role, {}, False),
        ("rewrite the boundary policy", "policy", "iam:CreatePolicyVersion", boundary, {}, False),
        ("touch a role that is not SwarmAI's", "policy", "iam:PutRolePolicy", other_role,
         {"iam:PermissionsBoundary": boundary}, False),
        ("pass a Hive role to Lambda", "policy", "iam:PassRole", role,
         {"iam:PassedToService": "lambda.amazonaws.com"}, False),
        ("launch an untagged instance", "policy", "ec2:RunInstances", instance, {}, False),
        ("tag an existing instance", "policy", "ec2:CreateTags", instance, {}, False),
        ("terminate an instance that is not SwarmAI's", "policy", "ec2:TerminateInstances",
         instance, {}, False),
        ("run a command on an instance that is not SwarmAI's", "policy", "ssm:SendCommand",
         instance, {}, False),
        ("a Hive marks an instance as SwarmAI's", "boundary", "ec2:CreateTags", instance,
         {"ec2:ResourceTag/HiveName": "a", "aws:PrincipalTag/HiveName": "a",
          "aws:TagKeys": ap.MANAGED_TAG}, False),
        ("... in another spelling", "boundary", "ec2:CreateTags", instance,
         {"ec2:ResourceTag/HiveName": "a", "aws:PrincipalTag/HiveName": "a",
          "aws:TagKeys": ap.MANAGED_TAG.upper()}, False),
        ("a Hive tags another Hive's instance", "boundary", "ec2:CreateTags", instance,
         {"ec2:ResourceTag/HiveName": "b", "aws:PrincipalTag/HiveName": "a",
          "aws:TagKeys": "HiveStatus"}, False),
        ("a Hive role creates IAM roles", "boundary", "iam:CreateRole", role, {}, False),
    ]


def _entries(ctx: dict) -> list:
    return [{"ContextKeyName": k, "ContextKeyValues": [v], "ContextKeyType": "string"}
            for k, v in ctx.items()]


def run(iam, acct: str) -> int:
    tpl = ap.template(acct)
    docs = {"policy": json.dumps(tpl["policy"]), "boundary": json.dumps(tpl["boundary"]["document"])}
    failed = 0
    for desc, which, action, resource, ctx, want in cases(acct):
        out = iam.simulate_custom_policy(PolicyInputList=[docs[which]], ActionNames=[action],
                                         ResourceArns=[resource], ContextEntries=_entries(ctx))
        res = out["EvaluationResults"][0]
        got = res["EvalDecision"] == "allowed"
        ok = got == want
        failed += not ok
        print(f"{'ok  ' if ok else 'FAIL'} {'allowed' if got else 'refused'}  {desc}"
              + ("" if ok else f"  (expected {'allowed' if want else 'refused'}; "
                 f"missing context: {res.get('MissingContextValues')})"))
    print(f"\n{len(cases(acct)) - failed}/{len(cases(acct))} as expected")
    return 1 if failed else 0


def main(argv: Optional[list] = None) -> int:
    ap_ = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap_.add_argument("--profile", default=None)
    ap_.add_argument("--account", default=None, help="the account the template is for")
    args = ap_.parse_args(argv)
    import boto3
    sess = boto3.Session(profile_name=args.profile) if args.profile else boto3.Session()
    acct = args.account or sess.client("sts").get_caller_identity()["Account"]
    return run(sess.client("iam"), acct)


if __name__ == "__main__":
    sys.exit(main())
