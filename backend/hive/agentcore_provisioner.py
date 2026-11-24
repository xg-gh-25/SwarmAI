"""Elastic Workers: the account's AgentCore Runtime (design
docs/design/2026-11-20-swarm-out-multi-carrier.md §6.8, requirements R6.1-R6.3).

One runtime per account. Turning it on (``ElasticHive.enable``) makes, in the account and
region the Host manages:

- the S3 bridge bucket, public access blocked (the one ``hive/provisioner`` uses, by
  ``hive/bucket_name``'s rule; reused when it exists);
- the execution role ``SwarmAI-ElasticHive-<region>``: trusted only by
  ``bedrock-agentcore.amazonaws.com`` for this account's runtimes in this region
  (``aws:SourceAccount`` and ``aws:SourceArn`` conditions), its policy
  ``core.carriers.agentcore.execution_role_policy`` (reused and refreshed when it exists);
- the runtime ``swarmai_elastic_hive`` (``runtime_spec``: platform V2, network PUBLIC,
  protocol HTTP, ports 8080 and 8081, idle timeout 900 s, lifetime 28800 s, and
  ``resourceConfigurations`` ``minimumMemoryInMiB: 8192``: 2 vCPU / 8 GB baseline, 8 vCPU /
  32 GB peak, 32 GB disk), its image the configured repository at this Host's version
  (``image_location``). A runtime of that name already in the account that SwarmAI made
  (tagged ``swarmai:managed``: an Elastic Workers runtime whose row was lost) is updated to the same
  spec instead of created twice; one SwarmAI did not make is refused, as is a role of the
  role's name that SwarmAI did not make for this or that carries other policies.

The ``hive_instances`` row (``carrier='agentcore'``, ``runtime_arn``, name ``elastic``) is
what ``core.carriers.agentcore`` offers from. It is written before the AWS steps, so a
runtime created by an enable that later failed is still known and ``disable`` deletes it;
the carrier offers a row only once it has a runtime ARN and is not changing, and asks AWS
whether that runtime is READY at this Host's version every time. A transient status left by
a SwarmAI that quit is settled from the runtime's live state (``ElasticHive.status``). The row never takes an EC2 status (``running``,
``stopped``, ``updating``, ``deleting``, ``error``): EC2 offers, idle stop, stale-update release
and the EC2 routes act on those, so the elastic row keeps its own (``STATUSES``). It does not
count toward MAX_HIVES (``hive.accounts.COUNTED_SQL``).

``update(version)`` sends the whole ``runtime_spec`` again, ``resourceConfigurations``
included: an UpdateAgentRuntime without it falls back to the default size (2 vCPU, 8 GB,
10 GB disk) without an error. ``disable`` deletes the runtime and the row; the bucket stays
(it holds the runs' results and the EC2 Hives' packages) and so does the role (reused by the
next enable).

**Where the image comes from** (``image_location``): ``RELEASED_IMAGE``, the public ECR
repository the release pushes to (``.github/workflows/release.yml`` ``agentcore-image``:
``<repo>:<version>``, its ``AGENTCORE_IMAGE_REPO``), unless the config key
``hive_agentcore_image`` names another repository (no tag) — a build of one's own. And
CreateAgentRuntime's ``containerUri`` accepts ``public.ecr.aws/...`` as well as
``<account>.dkr.ecr.<region>.amazonaws.com/...`` (the ``RuntimeContainerUri`` pattern of the
API model, botocore 1.44): a public image is used as it is, and the role gets no ECR
permission; a private one must be this account's, in this region, and the role may pull
exactly that repository. Anything else is refused before anything is created.

Every boto3 call runs off the event loop (``asyncio.to_thread``); ARNs are built with
string formatting only.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import aiosqlite

from core.carriers import agentcore as ac
from core.carriers.base import AGENTCORE

logger = logging.getLogger(__name__)

#: the config key naming another Elastic Workers image repository (no tag) than the released one
IMAGE_SETTING = "hive_agentcore_image"
#: where the release pushes Elastic Workers image, ``<repo>:<version>`` (release.yml
#: ``AGENTCORE_IMAGE_REPO``): what every install uses unless IMAGE_SETTING names another
RELEASED_IMAGE = "public.ecr.aws/q4o1o9d0/swarmai-hive"
#: the runtime's name in the account (AgentRuntimeName: [a-zA-Z][a-zA-Z0-9_]{0,47})
RUNTIME_NAME = "swarmai_elastic_hive"
#: the hive_instances name of Elastic Workers (how a person or agent names it); another
#: account's (legacy mode, several accounts) is ``elastic-<account id>``
ELASTIC_NAME = "elastic"
ROLE_PREFIX = "SwarmAI-ElasticHive-"
ROLE_POLICY_NAME = "SwarmAI-ElasticHive"
PLATFORM_VERSION = "V2"
NETWORK_MODE = "PUBLIC"
SERVER_PROTOCOL = "HTTP"
PORTS = (8080, ac.BACKEND_PORT)
IDLE_TIMEOUT_S = 900
MAX_LIFETIME_S = ac.MAX_RUN_S
#: the product size (design Q3): 2 vCPU / 8 GB baseline, 8 vCPU / 32 GB peak, 32 GB disk
MEMORY_MIB = 8192
SIZE = {"vcpu_base": 2, "memory_gib_base": 8, "vcpu_peak": 8, "memory_gib_peak": 32, "disk_gib": 32}
#: what CreateAgentRuntime / UpdateAgentRuntime must know for the size to hold
SDK_FIELDS = ("platformVersion", "allowedPorts", "resourceConfigurations")

ENABLING, READY, UPGRADING, DISABLING, FAILED = "enabling", "ready", "upgrading", "disabling", "failed"
STATUSES = (ENABLING, READY, UPGRADING, DISABLING, FAILED)
TRANSIENT = (ENABLING, UPGRADING, DISABLING)   # the carrier skips these (ac.CHANGING_ROW_STATUSES)

#: the spike measured about 6 minutes to READY after a create, 3.5 after an update
CREATE_WAIT_S = 20 * 60
UPDATE_WAIT_S = 15 * 60
DELETE_WAIT_S = 10 * 60
POLL_S = 10.0
#: a role just created is not yet assumable everywhere (IAM is eventually consistent)
ROLE_RETRIES = 6
ROLE_RETRY_S = 10.0
LIST_PAGES_MAX = 20

PUBLIC_REGISTRY = "public.ecr.aws"
_PRIVATE_RE = re.compile(r"^(\d{12})\.dkr\.ecr\.([a-z0-9-]+)\.amazonaws\.com(\.cn)?/(.+)$")
_VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:[.-][A-Za-z0-9]+)*$")

#: elastic rows a task in this process is working on (a transient status nobody holds is
#: settled from the runtime's live state by ``status``)
_WORKING: set[str] = set()


class ElasticRefused(Exception):
    """Elastic Workers cannot be turned on, updated or off now; nothing was changed."""


class ElasticTargetError(Exception):
    """No account Elastic Workers can be turned on in (``status`` is the HTTP status the
    routes answer with, the message is safe to show)."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status


#: the config key: False once the person turned Elastic Workers off (Swarm Out › settings), so
#: the Host's standby never turns it on again; True (or missing) keeps it on standby
STANDBY_SETTING = "hive_elastic_standby"
#: how often the Host looks at the standby Elastic Workers (run_standby_loop)
STANDBY_EVERY_S = 300
#: ("repair:<account row id>", Host version) tried in this process: a repair that fails
#: again is not tried until a restart or another version (a failed update leaves the row
#: failed, so it is repaired once too)
_STANDBY_TRIED: set[tuple[str, str]] = set()
#: the standby's last round in this process: when, and what it did or why not (shown to
#: the agent in core/swarm_out.hives)
LAST_STANDBY: dict[str, Optional[str]] = {"at": None, "did": None}
#: one standby round at a time (the loop's and an agent's: run_standby_round)
_ROUND_LOCK = asyncio.Lock()


def standby_on() -> bool:
    """Whether Elastic Workers is kept on standby: True unless the setting is False (the
    person turned it off). An unreadable config counts as off: nothing is created."""
    try:
        from core.app_config_manager import AppConfigManager
        return AppConfigManager.instance().get(STANDBY_SETTING, True) is not False
    except Exception as exc:  # noqa: BLE001 — unreadable: create nothing
        logger.warning("elastic workers: reading %s failed (%s)", STANDBY_SETTING, type(exc).__name__)
        return False


def set_standby(on: bool) -> None:
    """Record the person's turn-on (True) or turn-off (False) for the standby."""
    from core.app_config_manager import AppConfigManager
    AppConfigManager.instance().update({STANDBY_SETTING: bool(on)})


#: the config key naming the account row Elastic Workers uses when several accounts are
#: connected in the old mode (written only by PUT /api/hive/elastic/standby: the person's)
ACCOUNT_SETTING = "hive_elastic_account"


def chosen_account() -> Optional[str]:
    """The account row the person chose for Elastic Workers, or None."""
    try:
        from core.app_config_manager import AppConfigManager
        value = AppConfigManager.instance().get(ACCOUNT_SETTING, None)
    except Exception:  # noqa: BLE001 — unreadable: no choice
        return None
    return value.strip() if isinstance(value, str) and value.strip() else None


def set_chosen_account(account_ref: str) -> None:
    from core.app_config_manager import AppConfigManager
    AppConfigManager.instance().update({ACCOUNT_SETTING: account_ref})


def _image_published(account: str, region: str, version: str) -> bool:
    """Whether the image of *version* can be pulled: a public repository's tag resolves to
    a digest; a private one is left to the provision's own pin. Raises ElasticRefused
    for an image this account cannot use."""
    uri, repo = image_location(configured_image(), account, region, version)
    if repo is not None:
        return True
    path = uri[len(PUBLIC_REGISTRY) + 1:].rsplit(":", 1)[0]
    return _public_manifest_digest(path, version) is not None


def _sleep(seconds: float):
    return asyncio.sleep(seconds)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _partition(region: str) -> str:
    return "aws-cn" if region.startswith("cn-") else "aws-us-gov" if region.startswith("us-gov-") else "aws"


def _host_version() -> str:
    from config import settings
    return str(settings.app_version).removeprefix("v")


def configured_image() -> Optional[str]:
    """The image repository: the ``hive_agentcore_image`` setting when it names one, else
    ``RELEASED_IMAGE``; None only when the config cannot be read."""
    try:
        from core.app_config_manager import AppConfigManager
        value = AppConfigManager.instance().get(IMAGE_SETTING, None)
    except Exception as exc:  # noqa: BLE001 — an unreadable config: no image
        logger.warning("elastic workers: reading %s failed (%s)", IMAGE_SETTING, type(exc).__name__)
        return None
    return value.strip() if isinstance(value, str) and value.strip() else RELEASED_IMAGE


NO_IMAGE = ("SwarmAI's settings could not be read, so Elastic Workers image is not known: "
            "see the daemon log.")


def image_location(configured: Optional[str], account: str, region: str, version: str) -> tuple[str, Optional[str]]:
    """The one place that decides the runtime's image: ``(containerUri, repository pulled)``.

    ``configured`` is a repository without a tag. A public ECR repository
    (``public.ecr.aws/<alias>/<name>``) is used as it is and pulls without credentials: the
    repository is None, and the execution role gets no ECR permission. A private one must
    be this account's, in this region: the repository is its path, the only one the role may
    pull. The tag is *version*, as the release pushes it. Raises ElasticRefused otherwise."""
    if not configured:
        raise ElasticRefused(NO_IMAGE)
    if not isinstance(version, str) or len(version) > 32 or not _VERSION_RE.fullmatch(version):
        raise ElasticRefused(f"Not a release version: {str(version)[:40]!r}.")
    ref = configured.strip().rstrip("/")
    if "://" in ref or "@" in ref or ":" in ref.rsplit("/", 1)[-1]:
        raise ElasticRefused(f"{IMAGE_SETTING} must name a repository without a scheme, tag or digest "
                             f"(it is {ref[:120]!r}); SwarmAI adds this version's tag.")
    host, _, path = ref.partition("/")
    if host == PUBLIC_REGISTRY:
        if "/" not in path or not ac.IMAGE_REPO_RE.fullmatch(path):
            raise ElasticRefused(f"{ref[:120]!r} is not a public ECR repository (public.ecr.aws/<alias>/<name>).")
        return f"{ref}:{version}", None
    m = _PRIVATE_RE.fullmatch(ref)
    if m is None or not ac.IMAGE_REPO_RE.fullmatch(m.group(4)):
        raise ElasticRefused(f"Elastic Workers image must be on public ECR or in this account's ECR "
                             f"({ref[:120]!r} is neither).")
    if m.group(1) != account or m.group(2) != region:
        raise ElasticRefused(f"The image repository is in account {m.group(1)}, region {m.group(2)}: the "
                             f"Elastic Workers pulls only from public ECR or from account {account} in {region}.")
    return f"{ref}:{version}", m.group(4)


_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}")
#: what the registry may answer for a tag: a multi-platform index or one manifest
_MANIFEST_TYPES = ", ".join((
    "application/vnd.oci.image.index.v1+json", "application/vnd.docker.distribution.manifest.list.v2+json",
    "application/vnd.oci.image.manifest.v1+json", "application/vnd.docker.distribution.manifest.v2+json"))
DIGEST_TIMEOUT_S = 20.0


def _public_manifest_digest(path: str, tag: str) -> Optional[str]:
    """The digest public ECR serves for ``public.ecr.aws/<path>:<tag>`` (the registry's
    anonymous pull token, then the manifest's ``Docker-Content-Digest``), or None. Only
    ever asks PUBLIC_REGISTRY; *path* and *tag* are checked by the caller."""
    import httpx
    base = f"https://{PUBLIC_REGISTRY}"
    with httpx.Client(timeout=DIGEST_TIMEOUT_S, follow_redirects=False) as c:
        t = c.get(f"{base}/token/", params={"scope": f"repository:{path}:pull"})
        token = t.json().get("token") if t.status_code == 200 else None
        if not isinstance(token, str) or not token:
            return None
        r = c.head(f"{base}/v2/{path}/manifests/{tag}",
                   headers={"Authorization": f"Bearer {token}", "Accept": _MANIFEST_TYPES})
    d = r.headers.get("docker-content-digest") if r.status_code == 200 else None
    return d if isinstance(d, str) and _DIGEST_RE.fullmatch(d) else None


def pinned_uri(uri: str, repo: Optional[str], session: Any, region: str) -> str:
    """*uri* (``<repository>:<version>``, from ``image_location``) with the digest the
    registry serves for it now: the runtime is made or updated on that content, so a tag
    pushed again later never changes what an Elastic Workers runtime runs. Raises ElasticRefused when
    the registry does not name one (no such tag, or it did not answer)."""
    ref, _, tag = uri.rpartition(":")
    try:
        if repo is None:                                  # public ECR
            digest = _public_manifest_digest(ref.partition("/")[2], tag)
        else:                                             # this account's ECR
            ecr = session.client("ecr", region_name=region)
            got = ecr.describe_images(repositoryName=repo, imageIds=[{"imageTag": tag}])
            details = got.get("imageDetails") or []
            digest = details[0].get("imageDigest") if details else None
    except Exception as exc:  # noqa: BLE001 — named in the refusal
        raise ElasticRefused(f"The image {uri} could not be looked up ({type(exc).__name__}); nothing was "
                             "changed.") from None
    if not isinstance(digest, str) or not _DIGEST_RE.fullmatch(digest):
        raise ElasticRefused(f"The image {uri} was not found in its registry; nothing was changed.")
    return f"{uri}@{digest}"


def runtime_spec(container_uri: str, role_arn: str) -> dict[str, Any]:
    """Everything CreateAgentRuntime and UpdateAgentRuntime send, the same every time (an
    update without ``resourceConfigurations`` silently falls back to the default size)."""
    return {
        "agentRuntimeArtifact": {"containerConfiguration": {"containerUri": container_uri}},
        "roleArn": role_arn,
        "networkConfiguration": {"networkMode": NETWORK_MODE},
        "protocolConfiguration": {"serverProtocol": SERVER_PROTOCOL},
        "lifecycleConfiguration": {"idleRuntimeSessionTimeout": IDLE_TIMEOUT_S, "maxLifetime": MAX_LIFETIME_S},
        "platformVersion": PLATFORM_VERSION,
        "allowedPorts": [{"port": p} for p in PORTS],
        "resourceConfigurations": [{"minimumMemoryInMiB": MEMORY_MIB}],
    }


def trust_policy(account: str, region: str) -> dict[str, Any]:
    """Only AgentCore, for this account's Elastic Workers runtime in this region (runtime ids
    start with their name), may assume the role."""
    return {
        "Version": "2012-10-17",
        "Statement": [{
            "Sid": "AgentCoreRuntime", "Effect": "Allow",
            "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
            "Action": "sts:AssumeRole",
            "Condition": {
                "StringEquals": {"aws:SourceAccount": account},
                "ArnLike": {"aws:SourceArn":
                            f"arn:{_partition(region)}:bedrock-agentcore:{region}:{account}:runtime/{RUNTIME_NAME}-*"},
            },
        }],
    }


def role_name(region: str) -> str:
    return f"{ROLE_PREFIX}{region}"


def sdk_missing() -> list[str]:
    """What this machine's AWS SDK does not know of the runtime spec (empty: all of it).
    A field the SDK does not know is refused by it, or with validation off, left out of
    the request, and the runtime then gets the default size."""
    from core import aws_account
    try:
        model = aws_account.service_model(ac.CONTROL_SERVICE)
    except Exception:  # noqa: BLE001 — no AgentCore in this SDK at all
        return list(SDK_FIELDS)
    missing: list[str] = []
    for op in ("CreateAgentRuntime", "UpdateAgentRuntime"):
        members = model.operation_model(op).input_shape.members
        missing += [f for f in SDK_FIELDS if f not in members and f not in missing]
    return missing


def _check_sdk() -> None:
    missing = sdk_missing()
    if missing:
        from core import aws_account
        raise ElasticRefused(f"This SwarmAI's AWS SDK (botocore {aws_account.sdk_version()}) does not know the "
                             f"Elastic Workers' {', '.join(missing)}; without them the runtime would get the "
                             "default size. Update SwarmAI.")


# ── the blocking AWS steps (run in a thread) ─────────────────────────────────────

def _account_of(session) -> str:
    from core import aws_account
    sts = session.client("sts", config=aws_account.client_config(
        connect_timeout=10, read_timeout=15, retries={"max_attempts": 2}))
    account = str(sts.get_caller_identity()["Account"])
    if not ac.ACCOUNT_RE.fullmatch(account):
        raise RuntimeError("STS answered no account id")
    return account


ROLE_TAG = "swarmai:elastic-hive"


def _check_adopted_role(iam, name: str, region: str) -> None:
    """A role of Elastic Workers' name that already exists is adopted only when SwarmAI
    made it for this (its two tags) and nothing but Elastic Workers' own policy is on
    it: a role someone else made, or one with more permissions attached, would give the
    runtime those permissions (or have its trust rewritten). Raises ElasticRefused."""
    from hive.provisioner import MANAGED_TAG
    role = iam.get_role(RoleName=name).get("Role") or {}
    tags = {t.get("Key"): t.get("Value") for t in role.get("Tags") or []}
    if tags.get(MANAGED_TAG["Key"]) != MANAGED_TAG["Value"] or tags.get(ROLE_TAG) != region:
        raise ElasticRefused(f"An IAM role named {name} exists that SwarmAI did not make for Elastic "
                             "Workers: delete or rename it, then turn Elastic Workers on again.")
    attached = iam.list_attached_role_policies(RoleName=name).get("AttachedPolicies") or []
    inline = [p for p in iam.list_role_policies(RoleName=name).get("PolicyNames") or [] if p != ROLE_POLICY_NAME]
    if attached or inline:
        extra = [str(p.get("PolicyName")) for p in attached] + inline
        raise ElasticRefused(f"The IAM role {name} has other policies on it ({', '.join(extra)[:200]}): remove "
                             "them, then try again. Elastic Workers' role carries only its own policy.")


def _ensure_role(session, account: str, region: str, bucket: str, repo: Optional[str]) -> tuple[str, bool]:
    """The execution role (created, or adopted, its trust and policy refreshed):
    ``(arn, created)``."""
    from hive.provisioner import MANAGED_TAG, _boundary_arn
    iam = session.client("iam")
    name = role_name(region)
    trust = json.dumps(trust_policy(account, region))
    created = False
    boundary = _boundary_arn(session, iam)
    try:
        kwargs: dict[str, Any] = {"PermissionsBoundary": boundary} if boundary else {}
        iam.create_role(RoleName=name, AssumeRolePolicyDocument=trust,
                        Description=f"SwarmAI Elastic Workers (AgentCore Runtime) in {region}",
                        Tags=[MANAGED_TAG, {"Key": ROLE_TAG, "Value": region}], **kwargs)
        created = True
    except Exception as exc:  # noqa: BLE001 — classified
        if ac._error_code(exc) != "EntityAlreadyExists":
            raise
        _check_adopted_role(iam, name, region)
        if boundary:  # the account's boundary, on an adopted role as on a new one
            iam.put_role_permissions_boundary(RoleName=name, PermissionsBoundary=boundary)
        iam.update_assume_role_policy(RoleName=name, PolicyDocument=trust)
    iam.put_role_policy(RoleName=name, PolicyName=ROLE_POLICY_NAME,
                        PolicyDocument=json.dumps(ac.execution_role_policy(account, region, bucket, repo)))
    return f"arn:{_partition(region)}:iam::{account}:role/{name}", created


def _control(session, region: str):
    return session.client(ac.CONTROL_SERVICE, region_name=region, config=ac._boto_config(60))


#: the runtime's tags: SwarmAI made it, and which elastic row turned it on last
MANAGED_RUNTIME_TAG = ("swarmai:managed", "true")
ROW_TAG = "swarmai:elastic-row"


def _runtime_arn(account: str, region: str, runtime_id: str) -> str:
    return f"arn:{_partition(region)}:bedrock-agentcore:{region}:{account}:runtime/{runtime_id}"


def _find_runtime(ctl, account: str, region: str) -> Optional[dict]:
    """The account's runtime named RUNTIME_NAME in this region with its tags
    (``{"agentRuntimeId", "tags"}``), or None."""
    kw: dict[str, Any] = {}
    for _ in range(LIST_PAGES_MAX):
        page = ctl.list_agent_runtimes(**kw)
        for rt in page.get("agentRuntimes") or []:
            if rt.get("agentRuntimeName") == RUNTIME_NAME and rt.get("agentRuntimeId"):
                rid = str(rt["agentRuntimeId"])
                tags = ctl.list_tags_for_resource(resourceArn=_runtime_arn(account, region, rid)).get("tags") or {}
                return {"agentRuntimeId": rid, "tags": tags}
        if not page.get("nextToken"):
            return None
        kw["nextToken"] = page["nextToken"]
    return None


def _ours(found: dict) -> bool:
    return (found.get("tags") or {}).get(MANAGED_RUNTIME_TAG[0]) == MANAGED_RUNTIME_TAG[1]


def _checked_arn(arn: Any, account: str, region: str) -> str:
    parsed = ac.parse_runtime_arn(arn)
    if parsed is None or parsed["account"] != account or parsed["region"] != region:
        raise RuntimeError(f"AgentCore answered an unexpected runtime ARN: {str(arn)[:200]!r}")
    return str(arn)


def _role_not_ready(exc: BaseException) -> bool:
    """A just-created role AgentCore cannot assume yet."""
    if ac._error_code(exc) not in ("ValidationException", "AccessDeniedException"):
        return False
    return "role" in str(exc).lower()


def _wait_status(ctl, runtime_id: str, wait_s: float, want: str = "READY") -> dict:
    """Poll the runtime until it is *want*; raises on a failed state or after *wait_s*."""
    deadline = time.monotonic() + wait_s
    while True:
        rt = ctl.get_agent_runtime(agentRuntimeId=runtime_id)
        status = rt.get("status")
        if status == want:
            return rt
        if status in ("CREATE_FAILED", "UPDATE_FAILED"):
            raise RuntimeError(f"AgentCore says {status}: {str(rt.get('failureReason') or 'no reason given')[:300]}")
        if time.monotonic() >= deadline:
            raise RuntimeError(f"The runtime is still {status} after {int(wait_s // 60)} minutes; "
                               "its state is shown again in Swarm Out › settings.")
        time.sleep(POLL_S)


def _wait_gone(ctl, runtime_id: str, wait_s: float) -> None:
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        try:
            ctl.get_agent_runtime(agentRuntimeId=runtime_id)
        except Exception as exc:  # noqa: BLE001 — classified
            if ac._error_code(exc) in ac.NOT_FOUND_CODES:
                return
            raise
        time.sleep(POLL_S)
    raise RuntimeError(f"The runtime is still being deleted after {int(wait_s // 60)} minutes.")


# ── Elastic Workers ─────────────────────────────────────────────────────────────

class ElasticHive:
    """Turn the account's Elastic Workers on, update its image, turn it off, read its state."""

    def __init__(self, db_path: Path):
        self.db_path = db_path

    async def _conn(self):
        conn = await aiosqlite.connect(str(self.db_path))
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA busy_timeout = 3000")
        await conn.execute("PRAGMA foreign_keys = ON")
        return conn

    async def rows(self, account_ref: Optional[str] = None) -> list[dict]:
        conn = await self._conn()
        try:
            sql = "SELECT * FROM hive_instances WHERE carrier = ?" + (" AND account_ref = ?" if account_ref else "")
            cur = await conn.execute(sql + " ORDER BY created_at", (AGENTCORE, account_ref) if account_ref else (AGENTCORE,))
            return [dict(r) for r in await cur.fetchall()]
        finally:
            await conn.close()

    async def _row(self, location_id: str) -> dict:
        conn = await self._conn()
        try:
            cur = await conn.execute("SELECT * FROM hive_instances WHERE id = ? AND carrier = ?", (location_id, AGENTCORE))
            row = await cur.fetchone()
        finally:
            await conn.close()
        if row is None:
            raise LookupError("no Elastic Workers with this id")
        return dict(row)

    async def _account(self, account_ref: str) -> dict:
        conn = await self._conn()
        try:
            cur = await conn.execute("SELECT * FROM hive_accounts WHERE id = ?", (account_ref,))
            row = await cur.fetchone()
        finally:
            await conn.close()
        if row is None:
            raise LookupError("account not found")
        return dict(row)

    async def _set(self, location_id: str, **fields: Any) -> None:
        allowed = {"status", "error_message", "runtime_arn", "s3_bucket", "iam_role_name", "version"}
        if set(fields) - allowed:
            raise ValueError(f"not an Elastic Workers runtime column: {sorted(set(fields) - allowed)}")
        conn = await self._conn()
        try:
            sets = ", ".join(f"{k} = ?" for k in fields)
            await conn.execute(f"UPDATE hive_instances SET {sets}, updated_at = ? WHERE id = ? AND carrier = ?",
                               (*fields.values(), _now(), location_id, AGENTCORE))
            await conn.commit()
        finally:
            await conn.close()

    async def _claim(self, location_id: str, to: str, from_: tuple[str, ...]) -> dict:
        """Move the row to *to* only from one of *from_* (or from a transient status no task
        here holds: what a quit SwarmAI left); ElasticRefused otherwise."""
        row = await self._row(location_id)
        status = row.get("status")
        if status not in from_ and not (status in TRANSIENT and location_id not in _WORKING):
            raise ElasticRefused(f"Elastic Workers is {status}: try again when that has finished.")
        conn = await self._conn()
        try:
            cur = await conn.execute("UPDATE hive_instances SET status = ?, error_message = NULL, updated_at = ? "
                                     "WHERE id = ? AND carrier = ? AND status = ?",
                                     (to, _now(), location_id, AGENTCORE, status))
            await conn.commit()
            if cur.rowcount != 1:
                raise ElasticRefused("Elastic Workers changed state: refresh and try again.")
        finally:
            await conn.close()
        _WORKING.add(location_id)
        return {**row, "status": to}

    # ── turn on ──

    async def claim(self, account_ref: str, region: str) -> dict:
        """Check what turning on needs and write the row (status ``enabling``). Raises
        ElasticRefused, creating nothing, when there is no usable image, the SDK cannot
        carry the size, or the account already has one. No AWS call."""
        if not ac.REGION_RE.fullmatch(region or ""):
            raise ElasticRefused(f"Not an AWS region: {str(region)[:40]!r}.")
        acct = await self._account(account_ref)
        refusal = await self.refusal(acct.get("account_id"), region)
        if refusal:
            raise ElasticRefused(refusal)
        from hive import accounts as hive_accounts
        now = _now()
        row = {"id": str(uuid.uuid4()), "name": ELASTIC_NAME, "owner_name": None, "hive_type": "shared",
               "account_ref": account_ref, "region": region, "instance_type": AGENTCORE, "status": ENABLING,
               "version": _host_version(), "error_message": None, "created_at": now, "updated_at": now,
               "carrier": AGENTCORE, "runtime_arn": None}
        async with hive_accounts.fleet_lock:
            conn = await self._conn()
            try:
                cur = await conn.execute("SELECT name FROM hive_instances WHERE carrier = ? AND account_ref = ?",
                                         (AGENTCORE, account_ref))
                if await cur.fetchone():
                    raise ElasticRefused("This account's Elastic Workers is already on.")
                # the first Elastic Workers is "elastic"; another account's is named after it
                cur = await conn.execute("SELECT 1 FROM hive_instances WHERE name = ?", (ELASTIC_NAME,))
                if await cur.fetchone():
                    row["name"] = f"{ELASTIC_NAME}-{acct.get('account_id')}"
                    cur = await conn.execute("SELECT 1 FROM hive_instances WHERE name = ?", (row["name"],))
                    if await cur.fetchone():
                        raise ElasticRefused(f"A Hive is already named {row['name']!r}: rename or delete it first.")
                cols = ", ".join(row)
                await conn.execute(f"INSERT INTO hive_instances ({cols}) VALUES ({', '.join('?' for _ in row)})",
                                   tuple(row.values()))
                await conn.commit()
            finally:
                await conn.close()
        _WORKING.add(row["id"])
        return row

    async def provision(self, location_id: str) -> dict:
        """The AWS steps of turning on (bucket, role, runtime, READY); the row ends
        ``ready``, or ``failed`` with why. Returns the row."""
        _WORKING.add(location_id)
        try:
            row = await self._row(location_id)
            acct = await self._account(row["account_ref"])
            region = row["region"]
            from hive.accounts import session_for
            session = await asyncio.to_thread(session_for, acct, region, "manage")
            account = await asyncio.to_thread(_account_of, session)
            if acct.get("account_id") and str(acct["account_id"]) != account:
                raise RuntimeError(f"The credentials reach account {account}, not {acct['account_id']}.")
            uri, repo = image_location(configured_image(), account, region, row.get("version") or _host_version())
            uri = await asyncio.to_thread(pinned_uri, uri, repo, session, region)
            from hive.provisioner import HiveProvisioner
            bucket = await HiveProvisioner(self.db_path)._ensure_s3_bucket(session, region, account)
            if not ac.BUCKET_RE.fullmatch(bucket or ""):
                raise RuntimeError(f"Unexpected bucket name {bucket!r}.")
            role_arn, created = await asyncio.to_thread(_ensure_role, session, account, region, bucket, repo)
            await self._set(location_id, s3_bucket=bucket, iam_role_name=role_name(region))
            spec = runtime_spec(uri, role_arn)
            ctl = await asyncio.to_thread(_control, session, region)
            arn, runtime_id = await self._create_or_reuse(ctl, spec, created, account, region, location_id)
            await self._set(location_id, runtime_arn=arn)
            await asyncio.to_thread(_wait_status, ctl, runtime_id, CREATE_WAIT_S)
            await self._set(location_id, status=READY, error_message=None)
            logger.info("elastic workers: %s is ready (%s)", arn, uri)
            return await self._row(location_id)
        except Exception as exc:  # noqa: BLE001 — the row says why
            logger.warning("elastic workers: turning on failed: %s", exc)
            await self._set(location_id, status=FAILED, error_message=str(exc)[:300])
            raise
        finally:
            _WORKING.discard(location_id)

    async def _create_or_reuse(self, ctl, spec: dict, role_created: bool, account: str,
                               region: str, location_id: str) -> tuple[str, str]:
        """Create the runtime, or update one of the same name SwarmAI made (an Elastic Workers runtime
        whose row was lost); one of that name SwarmAI did not make is refused."""
        found = await asyncio.to_thread(_find_runtime, ctl, account, region)
        if found and not _ours(found):
            raise ElasticRefused(f"An AgentCore runtime named {RUNTIME_NAME} exists in this account and region "
                                 "that SwarmAI did not make: delete or rename it, then try again.")
        if found:
            logger.info("elastic workers: taking over the account's runtime %s", found["agentRuntimeId"])
            await asyncio.to_thread(lambda: ctl.tag_resource(
                resourceArn=_runtime_arn(account, region, found["agentRuntimeId"]), tags={ROW_TAG: location_id}))
        tries = ROLE_RETRIES if role_created else 1
        for attempt in range(tries):
            try:
                if found:
                    r = await asyncio.to_thread(lambda: ctl.update_agent_runtime(
                        agentRuntimeId=found["agentRuntimeId"], **spec))
                else:
                    r = await asyncio.to_thread(lambda: ctl.create_agent_runtime(
                        agentRuntimeName=RUNTIME_NAME, description="SwarmAI Elastic Workers",
                        tags={MANAGED_RUNTIME_TAG[0]: MANAGED_RUNTIME_TAG[1], ROW_TAG: location_id}, **spec))
                return _checked_arn(r.get("agentRuntimeArn"), account, region), str(r["agentRuntimeId"])
            except Exception as exc:  # noqa: BLE001 — classified
                if attempt + 1 >= tries or not _role_not_ready(exc):
                    raise
                logger.info("elastic workers: the new role is not assumable yet; again in %ss", ROLE_RETRY_S)
                await _sleep(ROLE_RETRY_S)
        raise AssertionError("unreachable")

    async def enable(self, account_ref: str, region: str) -> dict:
        """Turn the account's Elastic Workers on and wait until it is READY."""
        row = await self.claim(account_ref, region)
        return await self.provision(row["id"])

    # ── update ──

    async def claim_update(self, location_id: str, version: Optional[str] = None) -> tuple[dict, str]:
        """Check the update can be made (image, SDK) and claim the row (``upgrading``)."""
        version = (version or _host_version()).removeprefix("v")
        row = await self._row(location_id)
        if not row.get("runtime_arn"):
            raise ElasticRefused("Elastic Workers has no runtime yet: turn it off and on again.")
        arn = ac.parse_runtime_arn(row["runtime_arn"])
        if arn is None:
            raise ElasticRefused("Elastic Workers' runtime ARN is not readable.")
        image_location(configured_image(), arn["account"], arn["region"], version)
        await asyncio.to_thread(_check_sdk)
        return await self._claim(location_id, UPGRADING, (READY, FAILED)), version

    async def run_update(self, location_id: str, version: str) -> dict:
        """UpdateAgentRuntime with the whole spec (the size every time), then READY."""
        _WORKING.add(location_id)
        try:
            row = await self._row(location_id)
            arn = ac.parse_runtime_arn(row["runtime_arn"])
            acct = await self._account(row["account_ref"])
            from hive.accounts import session_for
            session = await asyncio.to_thread(session_for, acct, arn["region"], "manage")
            uri, repo = image_location(configured_image(), arn["account"], arn["region"], version)
            uri = await asyncio.to_thread(pinned_uri, uri, repo, session, arn["region"])
            bucket = row.get("s3_bucket") or ""
            if not ac.BUCKET_RE.fullmatch(bucket):
                raise RuntimeError("Elastic Workers has no bridge bucket recorded: turn it off and on again.")
            role_arn, _ = await asyncio.to_thread(_ensure_role, session, arn["account"], arn["region"], bucket, repo)
            ctl = await asyncio.to_thread(_control, session, arn["region"])
            spec = runtime_spec(uri, role_arn)
            await asyncio.to_thread(lambda: ctl.update_agent_runtime(agentRuntimeId=arn["runtime_id"], **spec))
            await asyncio.to_thread(_wait_status, ctl, arn["runtime_id"], UPDATE_WAIT_S)
            await self._set(location_id, status=READY, version=version, error_message=None)
            return await self._row(location_id)
        except Exception as exc:  # noqa: BLE001 — the row says why
            logger.warning("elastic workers: update failed: %s", exc)
            await self._set(location_id, status=FAILED, error_message=f"Update to {version} failed: {exc}"[:300])
            raise
        finally:
            _WORKING.discard(location_id)

    async def update(self, location_id: str, version: Optional[str] = None) -> dict:
        """Update the runtime's image to *version* (default: this Host's) and wait for READY."""
        _, version = await self.claim_update(location_id, version)
        return await self.run_update(location_id, version)

    # ── turn off ──

    async def claim_disable(self, location_id: str) -> dict:
        return await self._claim(location_id, DISABLING, (READY, FAILED))

    async def run_disable(self, location_id: str) -> None:
        """Delete the runtime (one found by name when the row has none recorded), then
        the row. The bucket and the role stay."""
        _WORKING.add(location_id)
        try:
            row = await self._row(location_id)
            acct = await self._account(row["account_ref"])
            region = row["region"]
            from hive.accounts import session_for
            session = await asyncio.to_thread(session_for, acct, region, "manage")
            ctl = await asyncio.to_thread(_control, session, region)
            arn = ac.parse_runtime_arn(row.get("runtime_arn"))
            runtime_id = arn["runtime_id"] if arn else None
            if runtime_id is None:
                # no ARN recorded (the enable stopped between create and record): only the
                # runtime this row turned on, never another one of the same name
                account = await asyncio.to_thread(_account_of, session)
                found = await asyncio.to_thread(_find_runtime, ctl, account, region)
                runtime_id = found["agentRuntimeId"] if found and _ours(found) \
                    and found["tags"].get(ROW_TAG) == location_id else None
            if runtime_id:
                try:
                    await asyncio.to_thread(lambda: ctl.delete_agent_runtime(agentRuntimeId=runtime_id))
                except Exception as exc:  # noqa: BLE001 — classified
                    if ac._error_code(exc) not in ac.NOT_FOUND_CODES:
                        raise
                await asyncio.to_thread(_wait_gone, ctl, runtime_id, DELETE_WAIT_S)
            conn = await self._conn()
            try:
                await conn.execute("DELETE FROM hive_instances WHERE id = ? AND carrier = ?", (location_id, AGENTCORE))
                await conn.commit()
            finally:
                await conn.close()
            logger.info("elastic workers: turned off (%s)", row.get("runtime_arn") or runtime_id)
        except Exception as exc:  # noqa: BLE001 — the row says why
            logger.warning("elastic workers: turning off failed: %s", exc)
            await self._set(location_id, status=FAILED, error_message=f"Turning off failed: {exc}"[:300])
            raise
        finally:
            _WORKING.discard(location_id)

    async def disable(self, location_id: str) -> None:
        """Delete the runtime and the row (not the bucket)."""
        await self.claim_disable(location_id)
        await self.run_disable(location_id)

    # ── standby ──

    async def target(self, account_ref: Optional[str], region: Optional[str]) -> tuple[str, Optional[str], str]:
        """(account row id, its AWS account id, region) Elastic Workers is turned on in,
        chosen as create_instance chooses an account; ElasticTargetError when there is none."""
        from hive import accounts as hive_accounts
        mode = hive_accounts.swarmai_account()
        if mode is not None:
            if account_ref not in (None, hive_accounts.SWARMAI_ROW_ID):
                raise ElasticTargetError(409, "Elastic Workers is turned on in the SwarmAI account.")
            await hive_accounts.ensure_swarmai_row(self.db_path)
            account_ref = hive_accounts.SWARMAI_ROW_ID
        elif account_ref == hive_accounts.SWARMAI_ROW_ID:
            raise ElasticTargetError(409, "The SwarmAI account is not connected: connect it in Settings > SwarmAI account.")
        conn = await self._conn()
        try:
            if account_ref is None:
                cur = await conn.execute("SELECT id FROM hive_accounts WHERE auth_method NOT IN (?, ?, ?)",
                                         (hive_accounts.MERGED_METHOD, hive_accounts.RECORD_METHOD,
                                          hive_accounts.SWARMAI_METHOD))
                ids = [r["id"] for r in await cur.fetchall()]
                if not ids:
                    raise ElasticTargetError(409, "Add an AWS account first.")
                chosen = chosen_account()
                if len(ids) > 1 and chosen not in ids:
                    raise ElasticTargetError(422, "Choose the AWS account for Elastic Workers "
                                                  "in Swarm Out › settings.")
                account_ref = chosen if len(ids) > 1 else ids[0]
            cur = await conn.execute("SELECT account_id, auth_method, default_region FROM hive_accounts WHERE id = ?",
                                     (account_ref,))
            acct = await cur.fetchone()
        finally:
            await conn.close()
        if not acct or acct["auth_method"] in (hive_accounts.MERGED_METHOD, hive_accounts.RECORD_METHOD):
            raise ElasticTargetError(404, "Account not found")
        return account_ref, acct["account_id"], region or (mode or {}).get("region") or acct["default_region"] or "us-east-1"

    def _try_once(self, key: str, version: str) -> bool:
        """True the first time *key* is repaired for *version* in this process."""
        if (key, version) in _STANDBY_TRIED:
            return False
        _STANDBY_TRIED.add((key, version))
        return True

    async def reconcile(self, retry: bool = False) -> str:
        """Keep Elastic Workers on standby (design 2026-11-20-swarm-out-multi-carrier
        §6.8): update every Elastic Workers there is to this Host's version and repair a
        failed one once per process and version (*retry*: once more, an agent's round);
        turn one on when there is none. While the person has turned it off (read again
        before every change: a round waits minutes on AWS) it turns off one that is still
        there: a turn-on or update that was running when the person said off ends ready,
        and only this round turns it off then. Nothing while a change is in progress, or
        while this version has no published image. One round at a time.
        Returns what it did, for the log, the agent and the tests."""
        async with _ROUND_LOCK:
            return await self._round(retry)

    async def _round(self, retry: bool) -> str:
        if not standby_on():
            return await self._off()
        version = _host_version()
        rows = []
        for r in await self.rows():
            if r.get("status") in TRANSIENT and r["id"] not in _WORKING:
                r = await self._settle(r, await self._live(r))   # what a quit SwarmAI left
                if r is None:
                    continue
            rows.append(r)
        if any(r["id"] in _WORKING or r.get("status") in TRANSIENT for r in rows):
            return "busy"
        if not rows:
            # one Elastic Workers is the standby: only when there is none anywhere
            try:
                account_ref, account, region = await self.target(None, None)
            except ElasticTargetError as exc:
                return f"no target: {exc}"
            # an account added with access keys counts once its credentials answered STS
            # in a verify (routers/hive.verify_account writes verified_at then, even when a
            # later permission check fails); the SwarmAI account is verified by
            # core/aws_account and its row never has verified_at
            acct = await self._account(account_ref)
            from hive import accounts as hive_accounts
            if acct.get("auth_method") != hive_accounts.SWARMAI_METHOD and not acct.get("verified_at"):
                return "no target: the AWS account is not verified yet"
            reason = await self.refusal(account, region)
            if reason:
                return f"refused: {reason}"
            if not await asyncio.to_thread(_image_published, str(account), str(region), version):
                return f"no image for {version}"
            if not standby_on():
                return "off"
            await self.enable(account_ref, region)
            return "enabled"
        did = []
        for row in rows:
            did.append(await self._keep(row, version, retry))
        return "; ".join(did)

    async def _off(self) -> str:
        """Standby is off: turn off each Elastic Workers still there that nothing is
        changing (one being changed is turned off by a later round, once it ends). A row
        left changing with no task holding it (SwarmAI quit meanwhile) is first settled
        from the runtime's live state, as a standby round does, so it is turned off too."""
        did = []
        for row in await self.rows():
            if row["id"] in _WORKING:
                continue
            if row.get("status") in TRANSIENT:
                row = await self._settle(row, await self._live(row))
                if row is None:
                    continue
            if row.get("status") not in (READY, FAILED):
                continue
            try:
                await self.claim_disable(row["id"])
            except Exception as exc:  # noqa: BLE001 — changed meanwhile: the next round
                logger.info("elastic workers off: %s not claimed (%s)", row["id"][:8], type(exc).__name__)
                continue
            await self.run_disable(row["id"])
            did.append(row["id"][:8])
        return f"turned off {', '.join(did)}" if did else "off"

    async def _keep(self, row: dict, version: str, retry: bool) -> str:
        """One Elastic Workers kept at *version*, or repaired once."""
        if row.get("status") == READY and (row.get("version") or "").removeprefix("v") == version:
            return "ready"
        arn = ac.parse_runtime_arn(row.get("runtime_arn"))
        account = arn["account"] if arn else None
        region = arn["region"] if arn else row.get("region")
        if row.get("status") == READY:
            if not await asyncio.to_thread(_image_published, str(account), str(region), version):
                return f"no image for {version}"
            if not standby_on():
                return "off"
            await self.update(row["id"], version)
            return "updated"
        if row.get("status") != FAILED:
            return f"left {row.get('status')}"
        # by account, not row: turning on again makes a new row, which must not count as
        # never tried
        key = f"repair:{row['account_ref']}"
        if retry:
            _STANDBY_TRIED.discard((key, version))
        if not self._try_once(key, version):
            return "repair tried"
        if not standby_on():
            return "off"
        if arn is not None:
            await self.update(row["id"], version)
            return "repaired"
        await self.disable(row["id"])
        if not standby_on():        # turned off while the failed row was being removed
            return "off"
        await self.enable(row["account_ref"], row["region"])
        return "enabled again"

    # ── state ──

    async def refusal(self, account: Any, region: Any) -> Optional[str]:
        """Why Elastic Workers cannot be turned on in *account* / *region*, or None: no
        image configured, an image this account cannot pull, an SDK that cannot carry the
        size. No AWS call (the account id is checked against STS before anything is made)."""
        if not ac.ACCOUNT_RE.fullmatch(str(account or "")):
            return "The AWS account's id is not known: verify the account first."
        if not ac.REGION_RE.fullmatch(str(region or "")):
            return f"Not an AWS region: {str(region)[:40]!r}."
        try:
            image_location(configured_image(), str(account), str(region), _host_version())
            await asyncio.to_thread(_check_sdk)
        except ElasticRefused as exc:
            return str(exc)
        return None

    async def status(self, target: Optional[tuple[Any, Any]] = None) -> dict[str, Any]:
        """What Swarm Out › settings shows: whether it can be turned on in *target* (account id,
        region) and why not, and each Elastic Workers with its runtime's live state. A
        transient status no task here holds is settled from that live state."""
        host = _host_version()
        reason = await self.refusal(*target) if target else (None if configured_image() else NO_IMAGE)
        out = []
        for row in await self.rows():
            live = await self._live(row)
            if row.get("status") in TRANSIENT and row["id"] not in _WORKING:
                row = await self._settle(row, live)
                if row is None:
                    continue
            image = live.get("image_version")
            open_runs = await asyncio.to_thread(ac._active_sessions, row["id"])
            out.append({"id": row["id"], "name": row["name"], "account_ref": row["account_ref"],
                        "region": row["region"], "status": row["status"], "error_message": row.get("error_message"),
                        "runtime_status": live.get("runtime_status"), "image_version": image,
                        "up_to_date": image is not None and image.removeprefix("v") == host,
                        "open_runs": open_runs, "working": row["id"] in _WORKING})
        return {"host_version": host, "can_enable": reason is None, "reason": reason, "size": dict(SIZE),
                "hives": out}

    async def _settle(self, row: dict, live: dict) -> Optional[dict]:
        """A transient status no task here holds (SwarmAI quit or the task died), settled
        from the runtime's live state; None when the row is gone (a turn-off that deleted
        the runtime). The write applies only if the row is still in the status read, so a
        claim made meanwhile is never overwritten."""
        rs, status = live.get("runtime_status"), row["status"]
        if status == DISABLING and rs == "NOT_FOUND":
            conn = await self._conn()
            try:
                await conn.execute("DELETE FROM hive_instances WHERE id = ? AND carrier = ? AND status = ?",
                                   (row["id"], AGENTCORE, DISABLING))
                await conn.commit()
            finally:
                await conn.close()
            return None
        if rs == "READY" and status != DISABLING:
            to, why = READY, None
        elif rs in ("CREATE_FAILED", "UPDATE_FAILED"):
            to, why = FAILED, live.get("failure_reason")
        elif rs == "NOT_FOUND":
            to, why = FAILED, "The runtime is no longer in AWS: turn Elastic Workers off and on again."
        elif status == ENABLING and not row.get("runtime_arn"):
            to, why = FAILED, "Turning on was interrupted (SwarmAI stopped): turn it off, then on again."
        elif status == DISABLING and (rs == "READY" or not row.get("runtime_arn")):
            to, why = FAILED, "Turning off was interrupted (SwarmAI stopped): turn it off again."
        else:
            return row          # still changing in AWS, or not readable now: shown as it is
        conn = await self._conn()
        try:
            await conn.execute("UPDATE hive_instances SET status = ?, error_message = ?, updated_at = ? "
                               "WHERE id = ? AND carrier = ? AND status = ?",
                               (to, why, _now(), row["id"], AGENTCORE, status))
            await conn.commit()
        finally:
            await conn.close()
        return await self._row(row["id"])

    async def _live(self, row: dict) -> dict[str, Any]:
        arn = ac.parse_runtime_arn(row.get("runtime_arn"))
        if arn is None:
            return {}
        try:
            acct = await self._account(row["account_ref"])
            from hive.accounts import session_for
            session = await asyncio.to_thread(session_for, acct, arn["region"], "read")
            ctl = await asyncio.to_thread(_control, session, arn["region"])
            rt = await asyncio.to_thread(lambda: ctl.get_agent_runtime(agentRuntimeId=arn["runtime_id"]))
        except Exception as exc:  # noqa: BLE001 — shown as not known
            code = ac._error_code(exc)
            if code in ac.NOT_FOUND_CODES:
                return {"runtime_status": "NOT_FOUND"}
            logger.info("elastic workers: runtime state not read (%s)", code or type(exc).__name__)
            return {}
        uri = ((rt.get("agentRuntimeArtifact") or {}).get("containerConfiguration") or {}).get("containerUri")
        return {"runtime_status": rt.get("status"), "image_version": ac._image_tag(uri),
                "failure_reason": str(rt.get("failureReason") or "")[:300] or None}


async def run_standby_round(db_path: Path) -> str:
    """One standby round now, a failed repair tried once more: the agent's way to turn the
    Elastic Workers on or repair it without waiting for the loop (POST
    /api/swarm-out/elastic/standby). Nothing while the person has turned it off."""
    try:
        did = await ElasticHive(db_path).reconcile(retry=True)
    except Exception as exc:  # noqa: BLE001 — the row says why
        did = f"failed: {type(exc).__name__}"
    LAST_STANDBY.update(at=_now(), did=did)
    return did


async def run_standby_loop(db_path: Path) -> None:
    """``ElasticHive.reconcile`` at once and then every STANDBY_EVERY_S (main.py, the
    desktop Host only). Never raises: a failed round is logged and the next one runs."""
    elastic = ElasticHive(db_path)
    while True:
        try:
            did = await elastic.reconcile()
            LAST_STANDBY.update(at=_now(), did=did)
            if did not in ("ready", "off", "busy"):
                logger.info("elastic workers standby: %s", did)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — the row says why; tried again next round
            LAST_STANDBY.update(at=_now(), did=f"failed: {type(exc).__name__}")
            logger.warning("elastic workers standby: the round failed (%s)", type(exc).__name__)
        await _sleep(STANDBY_EVERY_S)
