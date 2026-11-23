"""The Hive subnet under VPC Block Public Access (BPA).

When an account turns on VPC BPA in a region (Amazon turns it on, ingress-only, on every
personal Isengard account), traffic through an internet gateway is blocked both ways: the
CloudFront origin requests that reach a Hive, and the Hive's own outbound calls from its
public IP. CloudFront VPC origins, load balancers and Global Accelerator are blocked too
(AWS's BPA documentation lists them), so a Hive reached through CloudFront needs a BPA
exclusion. The rule for exclusions is: exclude a subnet, never the VPC, keep it as small
as the workload, and keep the security group restrictive.

So, when BPA blocks a region, Hives launch into a SwarmAI subnet in the default VPC
(tagged ``Name=SwarmAI-Hive-subnet`` and ``swarmai:managed=true``, a /26; one per zone
the Hives' instance types need) that carries the only exclusion (``allow-bidirectional``:
in ingress-only mode no other kind applies). The subnet gives out no public IP by itself;
the Hive's network interface asks for one (``launch_placement``). The security group is
unchanged: port 80 from the CloudFront origin-facing prefix list.

Residual exposure, accepted: anything else launched into that subnet WITH a public IP
(any principal of the account allowed to run instances there) is outside BPA too, held
only by its own security group; and IAM cannot name "the default VPC", so the permission
template lets the SwarmAI identity create a swarmai:managed subnet and its exclusion in
any VPC of the account (core/aws_permissions.py notes).

- ``bpa_state``: the region's BPA setting, or None when it cannot be read (an older
  permission template). None and mode ``off`` both mean "launch as before": not reading
  BPA weakens nothing, BPA itself is the control.
- ``ensure_hive_subnet``: the subnet id to launch into (None: launch as before). Creates
  the subnet and its exclusion when missing and waits until the exclusion is active.
  Raises ``BpaError`` when BPA blocks and the account does not allow exclusions (an
  Organizations declarative policy), or when the exclusion fails or never completes.
- ``release_unused_hive_subnets``: deletes the exclusion, then the subnet, of every
  SwarmAI Hive subnet in the region that no network interface uses any more. Failures
  are logged, never raised: a Hive's own cleanup does not depend on them.
- ``region_lock``: held from ``ensure_hive_subnet`` through ``run_instances``, and by the
  release, so a cleanup cannot delete the subnet a deploy is about to launch into.

Blocking boto3 calls throughout: callers run them in a worker thread.
"""
from __future__ import annotations

import ipaddress
import logging
import threading
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)

SUBNET_NAME = "SwarmAI-Hive-subnet"
MANAGED_TAG = {"Key": "swarmai:managed", "Value": "true"}
#: 64 addresses (59 usable): every Hive of a region (MAX_HIVES) plus its replacements
SUBNET_PREFIX = 26
EXCLUSION_MODE = "allow-bidirectional"
#: hang guard only: an exclusion became active in about 100 s when measured
EXCLUSION_WAIT_S = 600
POLL_S = 10

_ACTIVE = {"create-in-progress", "create-complete", "update-in-progress", "update-complete"}
_DONE = {"create-complete", "update-complete"}
_FAILED = {"create-failed", "update-failed"}

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


class BpaError(RuntimeError):
    """A Hive cannot be launched in this region because of VPC Block Public Access. The
    message is written here, carries no secret, and is shown to the person."""


def region_lock(region: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(region, threading.Lock())


def _code(exc: Exception) -> str:
    resp = getattr(exc, "response", None)
    if isinstance(resp, dict):
        return (resp.get("Error") or {}).get("Code", "") or type(exc).__name__
    return type(exc).__name__


def read_bpa(ec2: Any) -> "tuple[Optional[dict], Optional[str]]":
    """(state, problem): ``{"mode", "blocking", "exclusions_allowed"}`` for the client's
    region, or (None, why it could not be read). The capability check reports the problem;
    a deploy launches as before (bpa_state)."""
    try:
        opts = ec2.describe_vpc_block_public_access_options().get(
            "VpcBlockPublicAccessOptions") or {}
    except Exception as e:  # noqa: BLE001 — returned as the problem
        return None, _code(e)
    mode = opts.get("InternetGatewayBlockMode", "off") if isinstance(opts, dict) else None
    if not isinstance(mode, str):
        return None, "an answer without a mode"
    allowed = opts.get("ExclusionsAllowed", "allowed")
    # any mode but "off" blocks (block-ingress, block-bidirectional, or one added later)
    return {"mode": mode, "blocking": mode != "off",
            # absent on an older API answer: AWS refuses the exclusion itself if not allowed
            "exclusions_allowed": not isinstance(allowed, str) or allowed == "allowed"}, None


def bpa_state(ec2: Any) -> Optional[dict]:
    """The region's BPA state, or None when it cannot be read (logged): the deploy then
    launches as before."""
    state, problem = read_bpa(ec2)
    if state is None:
        logger.warning("VPC Block Public Access could not be read (%s); the Hive is launched "
                       "as before. Grant ec2:DescribeVpcBlockPublicAccessOptions to let SwarmAI "
                       "prepare a subnet when BPA is on.", problem)
    return state


def blocked_message(region: str) -> str:
    return (f"VPC Block Public Access blocks internet traffic in {region}, and this account's "
            "policy does not allow exclusions, so CloudFront cannot reach a Hive there. Use a "
            "region where exclusions are allowed, or ask the account owner to allow one.")


def _default_vpc(ec2: Any) -> dict:
    vpcs = ec2.describe_vpcs(Filters=[{"Name": "isDefault", "Values": ["true"]}]).get("Vpcs") or []
    if not vpcs:
        raise BpaError("VPC Block Public Access is on and this region has no default VPC for "
                       "the Hive subnet.")
    return vpcs[0]


def _hive_subnets(ec2: Any, vpc_id: Optional[str] = None) -> list:
    filters = [{"Name": "tag:Name", "Values": [SUBNET_NAME]},
               {"Name": f"tag:{MANAGED_TAG['Key']}", "Values": [MANAGED_TAG["Value"]]}]
    if vpc_id:
        filters.append({"Name": "vpc-id", "Values": [vpc_id]})
    return ec2.describe_subnets(Filters=filters).get("Subnets") or []


def _free_cidr(vpc: dict, taken: list) -> str:
    used = [ipaddress.ip_network(c, strict=False) for c in taken]
    blocks = [a.get("CidrBlock") for a in vpc.get("CidrBlockAssociationSet") or []
              if (a.get("CidrBlockState") or {}).get("State", "associated") == "associated"]
    for block in blocks or [vpc["CidrBlock"]]:
        net = ipaddress.ip_network(block, strict=False)
        if net.prefixlen > SUBNET_PREFIX:
            continue
        for cand in net.subnets(new_prefix=SUBNET_PREFIX):
            if not any(cand.overlaps(u) for u in used):
                return str(cand)
    raise BpaError("VPC Block Public Access is on and the default VPC has no free address "
                   f"range for a /{SUBNET_PREFIX} Hive subnet.")


def _offered_zones(ec2: Any, instance_type: str) -> list:
    offers = ec2.describe_instance_type_offerings(
        LocationType="availability-zone",
        Filters=[{"Name": "instance-type", "Values": [instance_type]}]).get(
            "InstanceTypeOfferings") or []
    zones = sorted({o["Location"] for o in offers if o.get("Location")})
    if not zones:
        raise BpaError(f"{instance_type} is offered in no Availability Zone of this region.")
    return zones


def _create_subnet(ec2: Any, vpc: dict, az: str) -> str:
    """A new SwarmAI Hive subnet in *az*. It gives out no public IP by itself: the Hive's
    own network interface asks for one at launch (``launch_placement``), so nothing else
    launched into this excluded subnet gets an internet address by default."""
    taken = [s["CidrBlock"] for s in ec2.describe_subnets(
        Filters=[{"Name": "vpc-id", "Values": [vpc["VpcId"]]}]).get("Subnets") or []]
    cidr = _free_cidr(vpc, taken)
    subnet_id = ec2.create_subnet(
        VpcId=vpc["VpcId"], CidrBlock=cidr, AvailabilityZone=az,
        TagSpecifications=[{"ResourceType": "subnet", "Tags": [
            {"Key": "Name", "Value": SUBNET_NAME}, MANAGED_TAG]}])["Subnet"]["SubnetId"]
    logger.info("Hive subnet %s (%s, %s) created for VPC Block Public Access", subnet_id, cidr, az)
    return subnet_id


def launch_placement(subnet_id: Optional[str], sg_id: str) -> dict:
    """The run_instances arguments that place a Hive: its security group in the default
    subnet (no subnet), or one network interface in the Hive subnet with a public IP of its
    own (user data downloads the release at first boot, before the Elastic IP is
    associated)."""
    if not subnet_id:
        return {"SecurityGroupIds": [sg_id]}
    return {"NetworkInterfaces": [{"DeviceIndex": 0, "SubnetId": subnet_id, "Groups": [sg_id],
                                   "AssociatePublicIpAddress": True}]}


def _exclusions(ec2: Any) -> list:
    out: list = []
    kwargs: dict = {"MaxResults": 100}
    while True:
        page = ec2.describe_vpc_block_public_access_exclusions(**kwargs)
        out += page.get("VpcBlockPublicAccessExclusions") or []
        token = page.get("NextToken")
        if not token:
            return out
        kwargs["NextToken"] = token


def _of_subnet(exclusions: list, subnet_id: str) -> list:
    return [e for e in exclusions if (e.get("ResourceArn") or "").endswith(f":subnet/{subnet_id}")]


def _ensure_exclusion(ec2: Any, subnet_id: str, wait_s: float, poll_s: float) -> str:
    active = [e for e in _of_subnet(_exclusions(ec2), subnet_id) if e.get("State") in _ACTIVE]
    if active:
        exc = active[0]
        if exc.get("InternetGatewayExclusionMode") != EXCLUSION_MODE:
            raise BpaError(f"The Hive subnet {subnet_id} has a VPC Block Public Access exclusion "
                           f"in {exc.get('InternetGatewayExclusionMode')} mode; inbound traffic "
                           f"from CloudFront needs {EXCLUSION_MODE}.")
        exc_id = exc["ExclusionId"]
    else:
        exc_id = ec2.create_vpc_block_public_access_exclusion(
            SubnetId=subnet_id, InternetGatewayExclusionMode=EXCLUSION_MODE,
            TagSpecifications=[{"ResourceType": "vpc-block-public-access-exclusion", "Tags": [
                {"Key": "Name", "Value": SUBNET_NAME}, MANAGED_TAG]}],
        )["VpcBlockPublicAccessExclusion"]["ExclusionId"]
        logger.info("VPC Block Public Access exclusion %s created for Hive subnet %s",
                    exc_id, subnet_id)
    deadline = time.monotonic() + wait_s
    while True:
        try:
            found = ec2.describe_vpc_block_public_access_exclusions(ExclusionIds=[exc_id]).get(
                "VpcBlockPublicAccessExclusions") or []
        except Exception as e:  # noqa: BLE001 — a just-created id may not be visible yet
            if not _code(e).endswith("NotFound"):
                raise
            found = []
        state = found[0].get("State") if found else None
        if state in _DONE:
            return exc_id
        if state in _FAILED or (state is not None and state not in _ACTIVE):
            raise BpaError(f"The VPC Block Public Access exclusion {exc_id} of the Hive subnet "
                           f"ended in state {state}.")
        if time.monotonic() >= deadline:
            raise BpaError(f"The VPC Block Public Access exclusion {exc_id} of the Hive subnet "
                           f"was not active after {int(wait_s)} s (state {state}).")
        time.sleep(poll_s)


def ensure_hive_subnet(ec2: Any, region: str, instance_type: str, *,
                       wait_s: float = EXCLUSION_WAIT_S, poll_s: float = POLL_S) -> Optional[str]:
    """The subnet a Hive in *region* launches into, or None to launch as before. Call it
    under ``region_lock(region)`` and keep the lock through ``run_instances``."""
    state = bpa_state(ec2)
    if state is None or not state["blocking"]:
        return None
    if not state["exclusions_allowed"]:
        raise BpaError(blocked_message(region))
    vpc = _default_vpc(ec2)
    zones = _offered_zones(ec2, instance_type)
    # Hives of different types share the region: reuse a Hive subnet only in a zone that
    # offers this type, else add one (release handles several)
    usable = sorted((s for s in _hive_subnets(ec2, vpc["VpcId"])
                     if s.get("State") != "pending" and s.get("AvailabilityZone") in zones),
                    key=lambda s: s["SubnetId"])
    subnet_id = usable[0]["SubnetId"] if usable else _create_subnet(ec2, vpc, zones[0])
    _ensure_exclusion(ec2, subnet_id, wait_s, poll_s)
    return subnet_id


def release_unused_hive_subnets(ec2: Any, region: str) -> list:
    """Delete the exclusion, then the subnet, of each SwarmAI Hive subnet in *region* that no
    network interface uses. Returns the deleted subnet ids. Never raises."""
    deleted: list = []
    try:
        subnets = _hive_subnets(ec2)
    except Exception as e:  # noqa: BLE001
        logger.warning("Hive subnets in %s could not be listed: %s", region, type(e).__name__)
        return deleted
    for subnet in subnets:
        subnet_id = subnet["SubnetId"]
        try:
            enis = ec2.describe_network_interfaces(
                Filters=[{"Name": "subnet-id", "Values": [subnet_id]}]).get("NetworkInterfaces") or []
            if enis:
                continue
            for exc in _of_subnet(_exclusions(ec2), subnet_id):
                if exc.get("State") in _ACTIVE:
                    ec2.delete_vpc_block_public_access_exclusion(ExclusionId=exc["ExclusionId"])
                    logger.info("Deleted VPC Block Public Access exclusion %s", exc["ExclusionId"])
            ec2.delete_subnet(SubnetId=subnet_id)
            logger.info("Deleted Hive subnet %s: no Hive uses it", subnet_id)
            deleted.append(subnet_id)
        except Exception as e:  # noqa: BLE001 — the Hive's own cleanup does not depend on it
            logger.warning("Hive subnet %s was not released: %s", subnet_id, e)
    return deleted
