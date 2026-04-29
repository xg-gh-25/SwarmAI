"""Hive AWS resource provisioner.

Creates, manages, and cleans up all AWS resources for a Hive instance:
IAM Role, Instance Profile, Security Group, EC2, Elastic IP, CloudFront.

All boto3 calls are sync — wrapped in asyncio.to_thread() for async compat.
Each step updates the local SQLite DB so status is visible in the UI.

Key design decisions (from approved design doc):
- S3 bucket per region: swarmai-hive-releases-{region}
- Security Group: ports 80 + 443 only (no SSH, use SSM)
- EIP: stable IP for CloudFront origin + stop/start resilience
- CloudFront: Authorization header forwarded, CachingDisabled for API/SSE
- SSM for updates: no SSH key management
- User data safety: /opt/swarmai/ (code) vs /home/swarm/.swarm-ai/ (data)
"""

import asyncio
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Optional

import aiosqlite
import httpx

logger = logging.getLogger(__name__)

# GitHub repo for release downloads — configurable for forks
GITHUB_REPO = os.environ.get("SWARMAI_GITHUB_REPO", "xg-gh-25/SwarmAI")

# Allowed instance types — ARM64 Graviton only
ALLOWED_INSTANCE_TYPES = frozenset({
    "m7g.medium", "m7g.large", "m7g.xlarge", "m7g.2xlarge",
    "c7g.medium", "c7g.large", "c7g.xlarge", "c7g.2xlarge",
    "t4g.medium", "t4g.large", "t4g.xlarge", "t4g.2xlarge",
})

# Allowlist of valid column names for _update_instance (PE-review P0-2: prevent SQL injection)
_VALID_INSTANCE_COLUMNS = frozenset({
    "status", "version", "error_message",
    "ec2_instance_id", "ec2_public_ip", "elastic_ip_alloc_id",
    "security_group_id", "iam_role_name", "iam_instance_profile_arn",
    "cloudfront_dist_id", "cloudfront_domain", "s3_bucket",
    "auth_user", "auth_password",
})

# Hive name regex — must match router's _HIVE_NAME_RE
_HIVE_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,62}$")

# IAM policy for Hive instances
HIVE_IAM_POLICY = {
    "Version": "2012-10-17",
    "Statement": [
        {
            "Sid": "BedrockAccess",
            "Effect": "Allow",
            "Action": [
                "bedrock:InvokeModel",
                "bedrock:InvokeModelWithResponseStream",
                "bedrock:ListFoundationModels",
            ],
            "Resource": "*",
        },
        {
            "Sid": "S3HivePackage",
            "Effect": "Allow",
            "Action": ["s3:GetObject", "s3:ListBucket"],
            "Resource": ["arn:aws:s3:::swarmai-hive-releases-*"],
        },
        {
            "Sid": "SelfTagging",
            "Effect": "Allow",
            "Action": ["ec2:CreateTags"],
            "Resource": "*",
            "Condition": {
                "StringEquals": {"ec2:ResourceTag/HiveName": "${aws:PrincipalTag/HiveName}"}
            },
        },
    ],
}

EC2_TRUST_POLICY = {
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Principal": {"Service": "ec2.amazonaws.com"},
            "Action": "sts:AssumeRole",
        }
    ],
}

# SSM parameter for latest Amazon Linux 2023 ARM64 AMI
AL2023_ARM64_SSM_PARAM = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-arm64"


class HiveProvisioner:
    """Manages AWS resources for Hive instances.

    Usage:
        provisioner = HiveProvisioner(db_path)
        await provisioner.deploy(instance_id, account_row, params)
        await provisioner.stop(instance_row, account_row)
        await provisioner.start(instance_row, account_row)
        await provisioner.update(instance_row, account_row, version)
        await provisioner.cleanup(instance_row, account_row)
    """

    def __init__(self, db_path: Path):
        self.db_path = db_path

    # ── boto3 session ──────────────────────────────────────────────

    def _get_session(self, account_row: dict, region: str = "us-east-1"):
        """Create a boto3 Session from hive_accounts auth_config."""
        import boto3

        auth_config = json.loads(account_row.get("auth_config", "{}"))
        auth_method = account_row.get("auth_method", "access_keys")

        kwargs: dict[str, Any] = {"region_name": region}
        if auth_method == "access_keys" and auth_config.get("access_key_id"):
            kwargs["aws_access_key_id"] = auth_config["access_key_id"]
            kwargs["aws_secret_access_key"] = auth_config.get("secret_access_key", "")
        elif auth_method == "sso" and auth_config.get("profile"):
            kwargs["profile_name"] = auth_config["profile"]
        # else: default credential chain (IAM role, env vars, etc.)

        return boto3.Session(**kwargs)

    # ── DB helpers ─────────────────────────────────────────────────

    async def _update_instance(self, instance_id: str, **fields) -> None:
        """Update hive_instances fields in SQLite."""
        if not fields:
            return
        # PE-review P0-2: validate column names against allowlist
        bad_cols = set(fields) - _VALID_INSTANCE_COLUMNS
        if bad_cols:
            raise ValueError(f"Invalid column names: {bad_cols}")
        sets = ", ".join(f"{k} = ?" for k in fields)
        vals = list(fields.values()) + [instance_id]
        async with aiosqlite.connect(str(self.db_path)) as conn:
            await conn.execute("PRAGMA busy_timeout = 500")
            await conn.execute("PRAGMA foreign_keys = ON")
            await conn.execute(
                f"UPDATE hive_instances SET {sets}, updated_at = datetime('now') WHERE id = ?",
                vals,
            )
            await conn.commit()

    async def _get_account(self, account_ref: str) -> dict:
        """Fetch account row by id."""
        async with aiosqlite.connect(str(self.db_path)) as conn:
            conn.row_factory = aiosqlite.Row
            await conn.execute("PRAGMA busy_timeout = 500")
            await conn.execute("PRAGMA foreign_keys = ON")
            cursor = await conn.execute(
                "SELECT * FROM hive_accounts WHERE id = ?", (account_ref,)
            )
            row = await cursor.fetchone()
            if not row:
                raise ValueError(f"Account {account_ref} not found")
            return dict(row)

    async def _get_instance(self, instance_id: str) -> dict:
        """Fetch instance row by id."""
        async with aiosqlite.connect(str(self.db_path)) as conn:
            conn.row_factory = aiosqlite.Row
            await conn.execute("PRAGMA busy_timeout = 500")
            await conn.execute("PRAGMA foreign_keys = ON")
            cursor = await conn.execute(
                "SELECT * FROM hive_instances WHERE id = ?", (instance_id,)
            )
            row = await cursor.fetchone()
            if not row:
                raise ValueError(f"Instance {instance_id} not found")
            return dict(row)

    # ── Version resolution ────────────────────────────────────────

    async def _resolve_version(self, version: str | None) -> str:
        """Resolve version string. If None or 'latest', fetch from GitHub.

        Raises RuntimeError if no release found — never silently falls back.
        """
        if version and version != "latest":
            return version
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
            )
            if resp.status_code == 200:
                tag = resp.json().get("tag_name", "")
                if tag:
                    return tag.lstrip("v")
            raise RuntimeError(
                f"Cannot determine latest version from GitHub ({GITHUB_REPO}). "
                f"HTTP {resp.status_code}. Ensure the repo has at least one release "
                f"with a hive tar.gz asset."
            )

    # ── S3 ─────────────────────────────────────────────────────────

    async def _ensure_s3_bucket(self, session, region: str, account_id: str = "") -> str:
        """Create S3 bucket for hive releases if it doesn't exist.

        Bucket name includes last 4 digits of account ID to prevent collision
        when multiple users deploy in the same region.
        """
        suffix = account_id[-4:] if account_id else ""
        bucket_name = f"swarmai-hive-{suffix}-{region}" if suffix else f"swarmai-hive-releases-{region}"

        def _create():
            s3 = session.client("s3", region_name=region)
            try:
                if region == "us-east-1":
                    s3.create_bucket(Bucket=bucket_name)
                else:
                    s3.create_bucket(
                        Bucket=bucket_name,
                        CreateBucketConfiguration={"LocationConstraint": region},
                    )
                logger.info("Created S3 bucket: %s", bucket_name)
            except s3.exceptions.BucketAlreadyOwnedByYou:
                logger.info("S3 bucket already exists: %s", bucket_name)
            except Exception as e:
                if "BucketAlreadyOwnedByYou" in str(e):
                    pass
                else:
                    raise
            # Block public access
            s3.put_public_access_block(
                Bucket=bucket_name,
                PublicAccessBlockConfiguration={
                    "BlockPublicAcls": True,
                    "IgnorePublicAcls": True,
                    "BlockPublicPolicy": True,
                    "RestrictPublicBuckets": True,
                },
            )
            return bucket_name

        return await asyncio.to_thread(_create)

    async def _sync_release_to_s3(
        self, session, bucket: str, version: str, region: str
    ) -> None:
        """Download hive package from GitHub Release and upload to S3."""
        s3_key = f"v{version}/swarmai-hive-v{version}-linux-arm64.tar.gz"

        def _check_exists():
            s3 = session.client("s3", region_name=region)
            try:
                s3.head_object(Bucket=bucket, Key=s3_key)
                return True
            except Exception:
                return False

        if await asyncio.to_thread(_check_exists):
            logger.info("Release v%s already in S3", version)
            return

        # Download from GitHub
        gh_url = (
            f"https://github.com/{GITHUB_REPO}/releases/download/"
            f"v{version}/swarmai-hive-v{version}-linux-arm64.tar.gz"
        )
        logger.info("Downloading %s", gh_url)

        async with httpx.AsyncClient(follow_redirects=True, timeout=120) as client:
            resp = await client.get(gh_url)
            if resp.status_code != 200:
                raise RuntimeError(
                    f"Failed to download release v{version} from GitHub: "
                    f"HTTP {resp.status_code}"
                )
            data = resp.content

        # Upload to S3
        def _upload():
            s3 = session.client("s3", region_name=region)
            s3.put_object(Bucket=bucket, Key=s3_key, Body=data)
            logger.info("Uploaded %s to s3://%s/%s (%d bytes)", version, bucket, s3_key, len(data))

        await asyncio.to_thread(_upload)

    # ── IAM ────────────────────────────────────────────────────────

    async def _create_iam_role(self, session, name: str) -> str:
        """Create IAM role for Hive instance. Returns role ARN."""
        role_name = f"SwarmAI-Hive-{name}"

        def _create():
            iam = session.client("iam")
            try:
                resp = iam.create_role(
                    RoleName=role_name,
                    AssumeRolePolicyDocument=json.dumps(EC2_TRUST_POLICY),
                    Description=f"SwarmAI Hive instance role for {name}",
                    Tags=[{"Key": "HiveName", "Value": name}],
                )
                role_arn = resp["Role"]["Arn"]
            except iam.exceptions.EntityAlreadyExistsException:
                resp = iam.get_role(RoleName=role_name)
                role_arn = resp["Role"]["Arn"]

            # Attach inline policy
            iam.put_role_policy(
                RoleName=role_name,
                PolicyName="SwarmAI-Hive-Policy",
                PolicyDocument=json.dumps(HIVE_IAM_POLICY),
            )
            # SSM managed policy (for updates via SSM Run Command)
            iam.attach_role_policy(
                RoleName=role_name,
                PolicyArn="arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore",
            )
            return role_arn

        return await asyncio.to_thread(_create)

    async def _create_instance_profile(self, session, name: str) -> str:
        """Create instance profile and attach role. Returns profile ARN."""
        profile_name = f"SwarmAI-Hive-{name}"
        role_name = f"SwarmAI-Hive-{name}"

        def _create():
            iam = session.client("iam")
            try:
                resp = iam.create_instance_profile(InstanceProfileName=profile_name)
                profile_arn = resp["InstanceProfile"]["Arn"]
            except iam.exceptions.EntityAlreadyExistsException:
                resp = iam.get_instance_profile(InstanceProfileName=profile_name)
                profile_arn = resp["InstanceProfile"]["Arn"]

            # Attach role (may already be attached)
            try:
                iam.add_role_to_instance_profile(
                    InstanceProfileName=profile_name, RoleName=role_name
                )
            except iam.exceptions.LimitExceededException:
                pass  # Role already attached
            except Exception as e:
                if "Cannot exceed quota" in str(e) or "already" in str(e).lower():
                    pass
                else:
                    raise
            return profile_arn

        profile_arn = await asyncio.to_thread(_create)
        # Wait for propagation (IAM is eventually consistent)
        logger.info("Waiting 15s for IAM instance profile propagation...")
        await asyncio.sleep(15)
        return profile_arn

    # ── Security Group ─────────────────────────────────────────────

    async def _create_security_group(
        self, session, name: str, region: str
    ) -> str:
        """Create security group. Returns SG ID."""
        sg_name = f"SwarmAI-Hive-{name}-sg"

        def _create():
            ec2 = session.client("ec2", region_name=region)

            # Get default VPC
            vpcs = ec2.describe_vpcs(Filters=[{"Name": "isDefault", "Values": ["true"]}])
            vpc_id = vpcs["Vpcs"][0]["VpcId"] if vpcs["Vpcs"] else None

            try:
                kwargs: dict[str, Any] = {
                    "GroupName": sg_name,
                    "Description": f"SwarmAI Hive {name} - HTTP from CloudFront only",
                }
                if vpc_id:
                    kwargs["VpcId"] = vpc_id
                resp = ec2.create_security_group(**kwargs)
                sg_id = resp["GroupId"]

                # Inbound rules: port 80 from CloudFront ONLY (NO SSH, no 443)
                #
                # CloudFront connects to origin via HTTP (OriginProtocolPolicy:
                # http-only), so only port 80 is needed.  Port 443 is NOT opened
                # because all HTTPS terminates at CloudFront.
                #
                # We use the AWS-managed prefix list for CloudFront origin-facing
                # IPs instead of 0.0.0.0/0.  This prevents DyePack alerts
                # (palisade_dyepack_ec2_ip_authentication) by making the EC2
                # unreachable from the public internet — only CloudFront can
                # connect.  Defense-in-depth: Caddy still enforces bcrypt basic
                # auth on ALL paths.
                #
                # Prefix list lookup: com.amazonaws.global.cloudfront.origin-facing
                # This is a global AWS-managed prefix list containing all CF IPs.
                cf_prefix_lists = ec2.describe_managed_prefix_lists(
                    Filters=[{
                        "Name": "prefix-list-name",
                        "Values": ["com.amazonaws.global.cloudfront.origin-facing"],
                    }]
                )
                if cf_prefix_lists["PrefixLists"]:
                    cf_pl_id = cf_prefix_lists["PrefixLists"][0]["PrefixListId"]
                    ec2.authorize_security_group_ingress(
                        GroupId=sg_id,
                        IpPermissions=[
                            {
                                "IpProtocol": "tcp",
                                "FromPort": 80,
                                "ToPort": 80,
                                "PrefixListIds": [{
                                    "PrefixListId": cf_pl_id,
                                    "Description": "HTTP from CloudFront only",
                                }],
                            },
                        ],
                    )
                else:
                    # PE-review P1-7: fail instead of falling back to 0.0.0.0/0.
                    # If the prefix list isn't found, the region likely has other
                    # issues. Failing here is safer than opening port 80 to the internet.
                    raise RuntimeError(
                        f"CloudFront origin-facing prefix list not found in {region}. "
                        f"Cannot create secure security group — aborting deploy."
                    )
                ec2.create_tags(
                    Resources=[sg_id],
                    Tags=[
                        {"Key": "Name", "Value": sg_name},
                        {"Key": "HiveName", "Value": name},
                    ],
                )
                return sg_id
            except Exception as e:
                if "InvalidGroup.Duplicate" in str(e):
                    # SG exists from prior failed deploy
                    sgs = ec2.describe_security_groups(
                        Filters=[{"Name": "group-name", "Values": [sg_name]}]
                    )
                    return sgs["SecurityGroups"][0]["GroupId"]
                raise

        return await asyncio.to_thread(_create)

    # ── EC2 ────────────────────────────────────────────────────────

    async def _get_latest_ami(self, session, region: str) -> str:
        """Get latest Amazon Linux 2023 ARM64 AMI via SSM parameter."""
        def _lookup():
            ssm = session.client("ssm", region_name=region)
            try:
                resp = ssm.get_parameter(Name=AL2023_ARM64_SSM_PARAM)
                return resp["Parameter"]["Value"]
            except Exception:
                # Fallback: known-good AMI (us-east-1, Apr 2026)
                logger.warning("SSM AMI lookup failed, using fallback")
                return "ami-0f935a2ecd3a7bd5c"

        return await asyncio.to_thread(_lookup)

    async def _launch_ec2(
        self,
        session,
        name: str,
        instance_profile_arn: str,
        sg_id: str,
        user_data: str,
        instance_type: str,
        region: str,
        version: str,
    ) -> str:
        """Launch EC2 instance. Returns instance ID."""
        if instance_type not in ALLOWED_INSTANCE_TYPES:
            raise ValueError(
                f"Instance type '{instance_type}' not allowed. "
                f"Permitted: {', '.join(sorted(ALLOWED_INSTANCE_TYPES))}"
            )

        ami_id = await self._get_latest_ami(session, region)

        def _launch():
            ec2 = session.client("ec2", region_name=region)
            resp = ec2.run_instances(
                ImageId=ami_id,
                InstanceType=instance_type,
                MinCount=1,
                MaxCount=1,
                IamInstanceProfile={"Arn": instance_profile_arn},
                SecurityGroupIds=[sg_id],
                UserData=user_data,
                BlockDeviceMappings=[
                    {
                        "DeviceName": "/dev/xvda",
                        "Ebs": {
                            "VolumeSize": 50,
                            "VolumeType": "gp3",
                            "DeleteOnTermination": True,
                        },
                    }
                ],
                TagSpecifications=[
                    {
                        "ResourceType": "instance",
                        "Tags": [
                            {"Key": "Name", "Value": f"SwarmAI-Hive-{name}"},
                            {"Key": "HiveName", "Value": name},
                            {"Key": "HiveStatus", "Value": "provisioning"},
                            {"Key": "HiveVersion", "Value": version},
                        ],
                    }
                ],
                MetadataOptions={
                    "HttpTokens": "required",  # IMDSv2 only
                    "HttpEndpoint": "enabled",
                },
            )
            instance_id = resp["Instances"][0]["InstanceId"]
            logger.info("Launched EC2: %s (type=%s, ami=%s)", instance_id, instance_type, ami_id)

            # Wait until running
            ec2.get_waiter("instance_running").wait(InstanceIds=[instance_id])
            return instance_id

        return await asyncio.to_thread(_launch)

    # ── Elastic IP ─────────────────────────────────────────────────

    async def _allocate_elastic_ip(
        self, session, instance_id: str, name: str, region: str
    ) -> tuple[str, str]:
        """Allocate and associate EIP. Returns (alloc_id, public_ip)."""
        def _allocate():
            ec2 = session.client("ec2", region_name=region)
            alloc = ec2.allocate_address(
                Domain="vpc",
                TagSpecifications=[
                    {
                        "ResourceType": "elastic-ip",
                        "Tags": [
                            {"Key": "Name", "Value": f"SwarmAI-Hive-{name}"},
                            {"Key": "HiveName", "Value": name},
                        ],
                    }
                ],
            )
            alloc_id = alloc["AllocationId"]
            public_ip = alloc["PublicIp"]

            ec2.associate_address(
                AllocationId=alloc_id, InstanceId=instance_id
            )
            logger.info("EIP %s (%s) associated with %s", alloc_id, public_ip, instance_id)
            return alloc_id, public_ip

        return await asyncio.to_thread(_allocate)

    # ── Health check ───────────────────────────────────────────────

    async def _wait_healthy(self, ip: str, timeout: int = 300) -> bool:
        """Poll http://<ip>/health until 200 or timeout."""
        url = f"http://{ip}/health"
        start = time.monotonic()
        async with httpx.AsyncClient(timeout=5) as client:
            while time.monotonic() - start < timeout:
                try:
                    resp = await client.get(url)
                    if resp.status_code == 200:
                        data = resp.json()
                        logger.info("Hive healthy: %s", data.get("version", "unknown"))
                        return True
                except Exception:
                    pass
                await asyncio.sleep(5)
        logger.warning("Hive at %s did not become healthy within %ds", ip, timeout)
        return False

    # ── CloudFront ─────────────────────────────────────────────────

    async def _get_ec2_public_dns(
        self, session, instance_id: str, region: str
    ) -> str:
        """Get EC2 instance's public DNS name (required for CloudFront origin)."""
        def _lookup():
            ec2 = session.client("ec2", region_name=region)
            resp = ec2.describe_instances(InstanceIds=[instance_id])
            return resp["Reservations"][0]["Instances"][0].get("PublicDnsName", "")

        dns = await asyncio.to_thread(_lookup)
        if not dns:
            raise RuntimeError(f"EC2 {instance_id} has no public DNS name")
        return dns

    async def _create_cloudfront(
        self, session, origin_domain: str, name: str
    ) -> tuple[str, str]:
        """Create CloudFront distribution. Returns (dist_id, domain_name).

        origin_domain must be a DNS name (not an IP) — CloudFront rejects IPs.
        Use _get_ec2_public_dns() to resolve the EC2 public DNS name.
        """
        import uuid as _uuid

        def _create():
            cf = session.client("cloudfront")
            caller_ref = f"hive-{name}-{_uuid.uuid4().hex[:8]}"

            dist_config = {
                "CallerReference": caller_ref,
                "Comment": f"SwarmAI Hive: {name}",
                "Enabled": True,
                "PriceClass": "PriceClass_100",
                "HttpVersion": "http2and3",
                "DefaultRootObject": "",
                "Origins": {
                    "Quantity": 1,
                    "Items": [
                        {
                            "Id": "hive-origin",
                            "DomainName": origin_domain,
                            "CustomOriginConfig": {
                                "HTTPPort": 80,
                                "HTTPSPort": 443,
                                "OriginProtocolPolicy": "http-only",
                                "OriginReadTimeout": 60,
                                "OriginKeepaliveTimeout": 30,
                            },
                        }
                    ],
                },
                "DefaultCacheBehavior": {
                    "TargetOriginId": "hive-origin",
                    "ViewerProtocolPolicy": "redirect-to-https",
                    "AllowedMethods": {
                        "Quantity": 7,
                        "Items": ["GET", "HEAD", "OPTIONS", "PUT", "POST", "PATCH", "DELETE"],
                        "CachedMethods": {"Quantity": 2, "Items": ["GET", "HEAD"]},
                    },
                    "CachePolicyId": "4135ea2d-6df8-44a3-9df3-4b5a84be39ad",  # CachingDisabled
                    "OriginRequestPolicyId": "216adef6-5c7f-47e4-b989-5492eafa07d3",  # AllViewer
                    "Compress": True,
                    "FunctionAssociations": {"Quantity": 0},
                },
                # No separate /assets/* behavior — all traffic uses default
                # (CachingDisabled + AllViewer which forwards Authorization).
                # Caddy enforces basic auth on ALL paths including assets,
                # so a CachingOptimized behavior without auth forwarding = 401.
                # Browser-side caching via hashed filenames is sufficient.
                "CacheBehaviors": {"Quantity": 0, "Items": []},
            }

            resp = cf.create_distribution(DistributionConfig=dist_config)
            dist_id = resp["Distribution"]["Id"]
            domain = resp["Distribution"]["DomainName"]
            logger.info("CloudFront created: %s (%s)", dist_id, domain)
            return dist_id, domain

        return await asyncio.to_thread(_create)

    async def _wait_cloudfront_deployed(
        self, session, dist_id: str, timeout: int = 1200
    ) -> bool:
        """Wait for CloudFront distribution to reach Deployed status."""
        def _check():
            cf = session.client("cloudfront")
            resp = cf.get_distribution(Id=dist_id)
            return resp["Distribution"]["Status"]

        start = time.monotonic()
        while time.monotonic() - start < timeout:
            status = await asyncio.to_thread(_check)
            if status == "Deployed":
                logger.info("CloudFront %s deployed", dist_id)
                return True
            logger.info("CloudFront %s status: %s (waiting...)", dist_id, status)
            await asyncio.sleep(30)

        logger.warning("CloudFront %s not deployed within %ds", dist_id, timeout)
        return False

    # ── Deploy orchestration ───────────────────────────────────────

    async def deploy(self, instance_id: str) -> None:
        """Full deploy: S3 -> IAM -> SG -> EC2 -> EIP -> health -> CF.

        Called as a background task via asyncio.create_task().
        Updates DB status at each step. On failure, cleans up partial resources.
        """
        created_resources: dict[str, str] = {}  # track for cleanup on failure

        try:
            # PE-review P0-4: atomic status gate prevents TOCTOU race.
            # UPDATE WHERE returns rowcount=0 if another task already claimed it.
            async with aiosqlite.connect(str(self.db_path)) as conn:
                await conn.execute("PRAGMA busy_timeout = 500")
                cursor = await conn.execute(
                    "UPDATE hive_instances SET status = 'provisioning', updated_at = datetime('now') "
                    "WHERE id = ? AND status IN ('pending', 'error')",
                    (instance_id,),
                )
                await conn.commit()
                if cursor.rowcount == 0:
                    logger.warning("Deploy skipped — instance %s not in pending/error (concurrent deploy?)", instance_id)
                    return
            instance = await self._get_instance(instance_id)

            account = await self._get_account(instance["account_ref"])
            name = instance["name"]
            # PE-review P1-8: defensive name validation (not just router-level)
            if not _HIVE_NAME_RE.match(name):
                raise ValueError(f"Invalid hive name: {name!r}")
            region = instance["region"]
            version = await self._resolve_version(instance.get("version"))
            instance_type = instance.get("instance_type", "m7g.xlarge")
            # Persist resolved version
            await self._update_instance(instance_id, version=version)

            session = self._get_session(account, region)
            aws_account_id = account.get("account_id", "")

            # Step 1: S3
            bucket = await self._ensure_s3_bucket(session, region, aws_account_id)
            await self._update_instance(instance_id, s3_bucket=bucket)

            # Step 2: Sync release to S3
            await self._sync_release_to_s3(session, bucket, version, region)

            # Step 3: IAM
            role_arn = await self._create_iam_role(session, name)
            created_resources["iam_role"] = f"SwarmAI-Hive-{name}"
            await self._update_instance(instance_id, iam_role_name=f"SwarmAI-Hive-{name}")

            # Step 4: Instance Profile
            profile_arn = await self._create_instance_profile(session, name)
            created_resources["instance_profile"] = f"SwarmAI-Hive-{name}"
            await self._update_instance(instance_id, iam_instance_profile_arn=profile_arn)

            # Step 5: Security Group
            sg_id = await self._create_security_group(session, name, region)
            created_resources["security_group"] = sg_id
            await self._update_instance(instance_id, security_group_id=sg_id)

            # Step 6: Generate user-data
            from hive.user_data import generate_password, caddy_hash_password, render_user_data

            password = generate_password()
            auth_hash = caddy_hash_password(password)
            user_data = render_user_data(
                s3_bucket=bucket,
                version=version,
                auth_user="admin",
                auth_hash=auth_hash,
                region=region,
            )
            await self._update_instance(
                instance_id, auth_user="admin", auth_password=password
            )

            # Step 7: Launch EC2
            await self._update_instance(instance_id, status="installing")
            ec2_id = await self._launch_ec2(
                session, name, profile_arn, sg_id, user_data, instance_type, region, version
            )
            created_resources["ec2_instance"] = ec2_id
            await self._update_instance(instance_id, ec2_instance_id=ec2_id)

            # Step 8: Elastic IP
            alloc_id, public_ip = await self._allocate_elastic_ip(
                session, ec2_id, name, region
            )
            created_resources["elastic_ip"] = alloc_id
            await self._update_instance(
                instance_id,
                elastic_ip_alloc_id=alloc_id,
                ec2_public_ip=public_ip,
            )

            # Step 9: Wait for healthy
            healthy = await self._wait_healthy(public_ip, timeout=300)
            if not healthy:
                await self._update_instance(
                    instance_id,
                    status="error",
                    error_message="EC2 setup timeout — check /var/log/hive-setup.log",
                )
                # Cleanup partial resources on health timeout
                try:
                    await self._cleanup_resources(
                        session, region, created_resources,
                    )
                except Exception as cleanup_err:
                    logger.error("Cleanup on health timeout also failed: %s", cleanup_err)
                return

            await self._update_instance(instance_id, status="running")

            # Step 10: CloudFront (needs DNS name, not IP — CF rejects IPs)
            origin_dns = await self._get_ec2_public_dns(session, ec2_id, region)
            dist_id, cf_domain = await self._create_cloudfront(session, origin_dns, name)
            created_resources["cloudfront"] = dist_id
            await self._update_instance(
                instance_id,
                cloudfront_dist_id=dist_id,
                cloudfront_domain=cf_domain,
            )

            # Step 11: Wait for CloudFront
            cf_deployed = await self._wait_cloudfront_deployed(session, dist_id, timeout=1200)
            if not cf_deployed:
                logger.warning("CloudFront not deployed in time, but Hive is running via HTTP")
                # Don't fail the deploy — Hive is already accessible via HTTP

            logger.info(
                "Hive %s fully deployed: https://%s (EC2: %s, IP: %s)",
                name, cf_domain, ec2_id, public_ip,
            )

        except Exception as e:
            logger.exception("Deploy failed for instance %s", instance_id)
            await self._update_instance(
                instance_id,
                status="error",
                error_message=str(e)[:500],
            )
            # Cleanup partial resources
            try:
                instance = await self._get_instance(instance_id)
                account = await self._get_account(instance["account_ref"])
                await self._cleanup_resources(
                    self._get_session(account, instance["region"]),
                    instance["region"],
                    created_resources,
                )
            except Exception as cleanup_err:
                logger.error("Cleanup also failed: %s", cleanup_err)

    # ── Lifecycle operations ───────────────────────────────────────

    async def stop(self, instance_id: str) -> None:
        """Stop a running Hive (EC2 stop)."""
        instance = await self._get_instance(instance_id)
        account = await self._get_account(instance["account_ref"])
        session = self._get_session(account, instance["region"])
        ec2_id = instance.get("ec2_instance_id")
        if not ec2_id:
            raise ValueError("No EC2 instance ID")

        def _stop():
            ec2 = session.client("ec2", region_name=instance["region"])
            ec2.stop_instances(InstanceIds=[ec2_id])
            ec2.get_waiter("instance_stopped").wait(InstanceIds=[ec2_id])

        await asyncio.to_thread(_stop)
        await self._update_instance(instance_id, status="stopped")

    async def start(self, instance_id: str) -> None:
        """Start a stopped Hive (EC2 start + wait healthy)."""
        instance = await self._get_instance(instance_id)
        account = await self._get_account(instance["account_ref"])
        session = self._get_session(account, instance["region"])
        ec2_id = instance.get("ec2_instance_id")
        ip = instance.get("ec2_public_ip")
        if not ec2_id:
            raise ValueError("No EC2 instance ID")

        def _start():
            ec2 = session.client("ec2", region_name=instance["region"])
            ec2.start_instances(InstanceIds=[ec2_id])
            ec2.get_waiter("instance_running").wait(InstanceIds=[ec2_id])

        await asyncio.to_thread(_start)
        await self._update_instance(instance_id, status="installing")

        if ip:
            healthy = await self._wait_healthy(ip, timeout=180)
            if healthy:
                await self._update_instance(instance_id, status="running")
            else:
                await self._update_instance(
                    instance_id, status="error",
                    error_message="Failed to become healthy after start",
                )
        else:
            await self._update_instance(instance_id, status="running")

    async def update(self, instance_id: str, version: str) -> None:
        """Update a Hive to a new version via SSM Run Command."""
        instance = await self._get_instance(instance_id)
        account = await self._get_account(instance["account_ref"])
        session = self._get_session(account, instance["region"])
        ec2_id = instance.get("ec2_instance_id")
        bucket = instance.get("s3_bucket", f"swarmai-hive-releases-{instance['region']}")
        region = instance["region"]

        if not ec2_id:
            raise ValueError("No EC2 instance ID")

        import re
        if not re.match(r'^[a-zA-Z0-9._\-]+$', version):
            raise ValueError(f"Invalid version string: {version!r}")

        # Ensure new version is in S3
        await self._sync_release_to_s3(session, bucket, version, region)

        # SSM Run Command
        update_script = f"""#!/bin/bash
set -euo pipefail
echo "=== Updating to v{version} ==="
aws s3 cp s3://{bucket}/v{version}/swarmai-hive-v{version}-linux-arm64.tar.gz /tmp/hive-update.tar.gz --region {region}
mkdir -p /tmp/hive-new
tar xzf /tmp/hive-update.tar.gz --strip-components=1 -C /tmp/hive-new/
rsync -a --delete /tmp/hive-new/backend/ /opt/swarmai/backend/ --exclude='.venv'
rsync -a --delete /tmp/hive-new/desktop/dist/ /opt/swarmai/desktop/dist/
rsync -a /tmp/hive-new/hive/ /opt/swarmai/hive/
cd /opt/swarmai/backend && sudo -u swarm .venv/bin/pip install -q -e .
# Reload Caddy if Caddyfile changed, restart backend
if diff -q /tmp/hive-new/hive/Caddyfile /etc/caddy/Caddyfile >/dev/null 2>&1; then
  echo "Caddyfile unchanged"
else
  echo "Caddyfile changed — updating and reloading Caddy"
  cp /tmp/hive-new/hive/Caddyfile /etc/caddy/Caddyfile
  caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null 2>&1 || true
fi
systemctl restart swarmai-hive
rm -rf /tmp/hive-new /tmp/hive-update.tar.gz
echo "=== Update complete ==="
"""

        def _run_command():
            ssm = session.client("ssm", region_name=region)
            resp = ssm.send_command(
                InstanceIds=[ec2_id],
                DocumentName="AWS-RunShellScript",
                Parameters={"commands": [update_script]},
                TimeoutSeconds=300,
                Comment=f"SwarmAI Hive update to v{version}",
            )
            command_id = resp["Command"]["CommandId"]

            # Wait for completion
            import time as _time
            for _ in range(60):
                result = ssm.get_command_invocation(
                    CommandId=command_id, InstanceId=ec2_id
                )
                status = result["Status"]
                if status in ("Success", "Failed", "Cancelled", "TimedOut"):
                    return status, result.get("StandardOutputContent", "")
                _time.sleep(5)
            return "TimedOut", ""

        status, output = await asyncio.to_thread(_run_command)
        if status == "Success":
            await self._update_instance(instance_id, version=version)
            logger.info("Hive %s updated to v%s", instance["name"], version)
        else:
            await self._update_instance(
                instance_id,
                error_message=f"Update failed: {status}. {output[:200]}",
            )
            raise RuntimeError(f"SSM update failed: {status}")

    async def reset_password(self, instance_id: str) -> str:
        """Reset Hive auth password via SSM.  Returns the new passphrase.

        Steps:
        1. Generate new passphrase + bcrypt hash locally
        2. SSM Run Command: overwrite HIVE_PASS_HASH in /etc/caddy/.env
        3. Reload Caddy (zero-downtime — graceful config reload)
        4. Update local DB with new plaintext password
        """
        from hive.user_data import generate_password, caddy_hash_password

        instance = await self._get_instance(instance_id)
        account = await self._get_account(instance["account_ref"])
        session = self._get_session(account, instance["region"])
        ec2_id = instance.get("ec2_instance_id")
        region = instance["region"]

        if not ec2_id:
            raise ValueError("No EC2 instance ID")

        new_password = generate_password()
        new_hash = caddy_hash_password(new_password)

        # Caddy basicauth hash is inline in Caddyfile:
        #   basicauth * {
        #       admin $2b$14$...
        #   }
        #
        # PE-review N1: bcrypt hashes contain $ signs (e.g. $2b$14$...).
        # If interpolated into a bash script via f-string, bash expands
        # $2b, $14, etc. as variable references → corrupted hash → auth
        # permanently broken.
        #
        # Fix: base64-encode the hash, decode on the instance. base64
        # output is [A-Za-z0-9+/=] — no shell-special characters.
        import base64
        hash_b64 = base64.b64encode(new_hash.encode()).decode()

        reset_script = f"""#!/bin/bash
set -euo pipefail
CADDYFILE="/etc/caddy/Caddyfile"
if [ ! -f "$CADDYFILE" ]; then
  echo "ERROR: $CADDYFILE not found" >&2
  exit 1
fi
# Decode the bcrypt hash from base64 (avoids $ expansion in bash)
NEW_HASH=$(echo '{hash_b64}' | base64 -d)
# Backup current Caddyfile
cp "$CADDYFILE" "$CADDYFILE.bak"
# Replace the bcrypt hash line (matches: "    admin $2b$..." or "    admin $2a$...")
# Use | as sed delimiter (bcrypt never contains |). The hash is in a
# variable, not inline, so $ signs are not expanded during sed parsing.
sed -i "s|        admin \\$2[ab]\\$.*|        admin $NEW_HASH|" "$CADDYFILE"
# Validate and reload Caddy
# Note: systemctl reload is not supported for Caddy — use caddy reload
# which hot-swaps the config via Caddy's admin API (zero downtime).
if caddy validate --config "$CADDYFILE" --adapter caddyfile >/dev/null 2>&1; then
  caddy reload --config "$CADDYFILE" --adapter caddyfile >/dev/null 2>&1
  rm -f "$CADDYFILE.bak"
  echo "Password reset complete"
else
  # Rollback on validation failure
  mv "$CADDYFILE.bak" "$CADDYFILE"
  echo "ERROR: Caddy validation failed, rolled back" >&2
  exit 1
fi
"""

        def _run_command():
            ssm = session.client("ssm", region_name=region)
            resp = ssm.send_command(
                InstanceIds=[ec2_id],
                DocumentName="AWS-RunShellScript",
                Parameters={"commands": [reset_script]},
                TimeoutSeconds=30,
                Comment=f"SwarmAI Hive password reset for {instance['name']}",
            )
            command_id = resp["Command"]["CommandId"]

            import time as _time
            for _ in range(12):  # 12 × 5s = 60s max
                result = ssm.get_command_invocation(
                    CommandId=command_id, InstanceId=ec2_id
                )
                status = result["Status"]
                if status in ("Success", "Failed", "Cancelled", "TimedOut"):
                    return status, result.get("StandardOutputContent", "")
                _time.sleep(5)
            return "TimedOut", ""

        status, output = await asyncio.to_thread(_run_command)
        if status == "Success":
            await self._update_instance(
                instance_id, auth_password=new_password
            )
            logger.info("Hive %s password reset", instance["name"])
            return new_password
        else:
            raise RuntimeError(f"Password reset failed: {status}. {output[:200]}")

    # ── Cleanup ────────────────────────────────────────────────────

    async def cleanup(self, instance_id: str) -> None:
        """Delete all AWS resources for a Hive instance."""
        instance = await self._get_instance(instance_id)
        account = await self._get_account(instance["account_ref"])
        session = self._get_session(account, instance["region"])
        region = instance["region"]

        await self._update_instance(instance_id, status="deleting")

        resources = {
            "ec2_instance": instance.get("ec2_instance_id"),
            "cloudfront": instance.get("cloudfront_dist_id"),
            "elastic_ip": instance.get("elastic_ip_alloc_id"),
            "security_group": instance.get("security_group_id"),
            "iam_role": instance.get("iam_role_name"),
            "instance_profile": instance.get("iam_role_name"),  # same name
        }

        await self._cleanup_resources(session, region, resources)

    async def _cleanup_resources(
        self, session, region: str, resources: dict[str, Optional[str]]
    ) -> None:
        """Clean up AWS resources in correct dependency order."""

        def _do_cleanup():
            ec2 = session.client("ec2", region_name=region)
            iam = session.client("iam")
            cf = session.client("cloudfront")

            # 1. Terminate EC2
            ec2_id = resources.get("ec2_instance")
            if ec2_id:
                try:
                    ec2.terminate_instances(InstanceIds=[ec2_id])
                    ec2.get_waiter("instance_terminated").wait(
                        InstanceIds=[ec2_id],
                        WaiterConfig={"Delay": 10, "MaxAttempts": 30},
                    )
                    logger.info("Terminated EC2: %s", ec2_id)
                except Exception as e:
                    logger.warning("Failed to terminate EC2 %s: %s", ec2_id, e)

            # 2. Disable + delete CloudFront
            cf_id = resources.get("cloudfront")
            if cf_id:
                try:
                    dist = cf.get_distribution(Id=cf_id)
                    etag = dist["ETag"]
                    config = dist["Distribution"]["DistributionConfig"]
                    if config["Enabled"]:
                        config["Enabled"] = False
                        cf.update_distribution(
                            Id=cf_id, DistributionConfig=config, IfMatch=etag
                        )
                        logger.info("Disabled CloudFront %s, waiting to settle...", cf_id)
                        import time as _time
                        # Poll until disabled (up to 10 min), then delete
                        for _poll in range(20):
                            _time.sleep(30)
                            try:
                                _d = cf.get_distribution(Id=cf_id)
                                if _d["Distribution"]["Status"] == "Deployed":
                                    break
                            except Exception:
                                break
                    # Attempt delete
                    dist = cf.get_distribution(Id=cf_id)
                    cf.delete_distribution(Id=cf_id, IfMatch=dist["ETag"])
                    logger.info("Deleted CloudFront: %s", cf_id)
                except Exception as e:
                    logger.warning(
                        "CloudFront %s cleanup partial (may need manual): %s", cf_id, e
                    )

            # 3. Release Elastic IP
            alloc_id = resources.get("elastic_ip")
            if alloc_id:
                try:
                    ec2.release_address(AllocationId=alloc_id)
                    logger.info("Released EIP: %s", alloc_id)
                except Exception as e:
                    logger.warning("Failed to release EIP %s: %s", alloc_id, e)

            # 4. Delete Security Group
            sg_id = resources.get("security_group")
            if sg_id:
                try:
                    ec2.delete_security_group(GroupId=sg_id)
                    logger.info("Deleted SG: %s", sg_id)
                except Exception as e:
                    logger.warning("Failed to delete SG %s: %s", sg_id, e)

            # 5. Delete IAM (instance profile first, then role)
            profile_name = resources.get("instance_profile")
            role_name = resources.get("iam_role")

            if profile_name:
                try:
                    if role_name:
                        iam.remove_role_from_instance_profile(
                            InstanceProfileName=profile_name, RoleName=role_name
                        )
                    iam.delete_instance_profile(InstanceProfileName=profile_name)
                    logger.info("Deleted instance profile: %s", profile_name)
                except Exception as e:
                    logger.warning("Failed to delete instance profile %s: %s", profile_name, e)

            if role_name:
                try:
                    # Detach managed policies
                    policies = iam.list_attached_role_policies(RoleName=role_name)
                    for p in policies.get("AttachedPolicies", []):
                        iam.detach_role_policy(
                            RoleName=role_name, PolicyArn=p["PolicyArn"]
                        )
                    # Delete inline policies
                    inline = iam.list_role_policies(RoleName=role_name)
                    for pname in inline.get("PolicyNames", []):
                        iam.delete_role_policy(RoleName=role_name, PolicyName=pname)
                    iam.delete_role(RoleName=role_name)
                    logger.info("Deleted IAM role: %s", role_name)
                except Exception as e:
                    logger.warning("Failed to delete IAM role %s: %s", role_name, e)

        await asyncio.to_thread(_do_cleanup)
