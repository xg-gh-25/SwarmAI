"""The Hive subnet under VPC Block Public Access (hive/bpa_subnet.py).

Questions answered:
1. BPA blocking: is the Hive launched into the SwarmAI subnet, and only after that subnet's
   (never the VPC's) allow-bidirectional exclusion is active? Is an existing one reused?
2. Is a new subnet's range free inside the default VPC, in a zone offering the type?
3. BPA off or unreadable: is nothing created and the launch unchanged?
4. BPA blocking without exclusions allowed, or an exclusion that fails or never
   completes: does it stop before the launch with a message naming the cause?
5. Cleanup: are the exclusion and then the subnet deleted only when no network interface
   is left, and does a failure there stay a warning?
"""
from __future__ import annotations

import logging

import pytest

from hive import bpa_subnet as bs

VPC = {"VpcId": "vpc-1", "CidrBlock": "172.31.0.0/16",
       "CidrBlockAssociationSet": [{"CidrBlock": "172.31.0.0/16",
                                    "CidrBlockState": {"State": "associated"}}]}
DEFAULT_SUBNETS = [{"SubnetId": f"subnet-d{i}", "CidrBlock": f"172.31.{16 * i}.0/20",
                    "VpcId": "vpc-1", "State": "available", "AvailabilityZone": f"us-east-1{z}"}
                   for i, z in enumerate("abcdef")]


class _Err(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.response = {"Error": {"Code": code}}


class FakeEc2:
    def __init__(self, *, mode="block-ingress", allowed="allowed", read_error=None,
                 hive_subnet=None, exclusions=None, states=("create-in-progress", "create-complete"),
                 enis=None, offered=("us-east-1b", "us-east-1a"), fail=(), not_found=0):
        self.mode, self.allowed, self.read_error = mode, allowed, read_error
        self.hive_subnet = hive_subnet
        self.exclusions = list(exclusions or [])
        self.states = list(states)
        self.enis = enis or {}
        self.offered = offered
        self.fail = set(fail)
        self.not_found = not_found
        self.calls: list = []

    def _call(self, name, **kw):
        self.calls.append((name, kw))
        if name in self.fail:
            raise _Err("UnauthorizedOperation")

    def names(self):
        return [c[0] for c in self.calls]

    def describe_vpc_block_public_access_options(self):
        self._call("describe_vpc_block_public_access_options")
        if self.read_error:
            raise _Err(self.read_error)
        return {"VpcBlockPublicAccessOptions": {"InternetGatewayBlockMode": self.mode,
                                                "ExclusionsAllowed": self.allowed}}

    def describe_vpcs(self, **kw):
        self._call("describe_vpcs", **kw)
        return {"Vpcs": [VPC]}

    def describe_subnets(self, Filters):
        self._call("describe_subnets", Filters=Filters)
        if any(f["Name"] == "tag:Name" for f in Filters):
            return {"Subnets": [self.hive_subnet] if self.hive_subnet else []}
        return {"Subnets": DEFAULT_SUBNETS + ([self.hive_subnet] if self.hive_subnet else [])}

    def describe_instance_type_offerings(self, **kw):
        self._call("describe_instance_type_offerings", **kw)
        return {"InstanceTypeOfferings": [{"Location": z} for z in self.offered]}

    def create_subnet(self, **kw):
        self._call("create_subnet", **kw)
        self.hive_subnet = {"SubnetId": "subnet-hive", "CidrBlock": kw["CidrBlock"],
                            "VpcId": kw["VpcId"], "State": "available",
                            "AvailabilityZone": kw["AvailabilityZone"]}
        return {"Subnet": {"SubnetId": "subnet-hive"}}

    def describe_vpc_block_public_access_exclusions(self, **kw):
        self._call("describe_vpc_block_public_access_exclusions", **kw)
        if "ExclusionIds" in kw:
            if self.not_found:
                self.not_found -= 1
                raise _Err("InvalidVpcBlockPublicAccessExclusionId.NotFound")
            state = self.states.pop(0) if len(self.states) > 1 else self.states[0]
            return {"VpcBlockPublicAccessExclusions": [
                {"ExclusionId": kw["ExclusionIds"][0], "State": state}]}
        return {"VpcBlockPublicAccessExclusions": self.exclusions}

    def create_vpc_block_public_access_exclusion(self, **kw):
        self._call("create_vpc_block_public_access_exclusion", **kw)
        return {"VpcBlockPublicAccessExclusion": {"ExclusionId": "vpcbpa-exclude-new"}}

    def describe_network_interfaces(self, Filters):
        self._call("describe_network_interfaces", Filters=Filters)
        return {"NetworkInterfaces": self.enis.get(Filters[0]["Values"][0], [])}

    def delete_vpc_block_public_access_exclusion(self, **kw):
        self._call("delete_vpc_block_public_access_exclusion", **kw)

    def delete_subnet(self, **kw):
        self._call("delete_subnet", **kw)


def _ensure(ec2, **kw):
    return bs.ensure_hive_subnet(ec2, "us-east-1", "m7g.xlarge", poll_s=0, **kw)


def test_blocking_creates_subnet_and_exclusion_before_launch():
    ec2 = FakeEc2()
    assert _ensure(ec2) == "subnet-hive"
    names = ec2.names()
    create = dict(ec2.calls)["create_subnet"]
    tags = create["TagSpecifications"][0]["Tags"]
    assert {"Key": "Name", "Value": "SwarmAI-Hive-subnet"} in tags
    assert {"Key": "swarmai:managed", "Value": "true"} in tags
    # the subnet gives out no public IP by itself (only the Hive's interface asks for one)
    assert "modify_subnet_attribute" not in names and "MapPublicIpOnLaunch" not in create
    exc = dict(ec2.calls)["create_vpc_block_public_access_exclusion"]
    # the subnet, never the VPC, in the only mode ingress-only BPA honours
    assert exc["SubnetId"] == "subnet-hive" and "VpcId" not in exc
    assert exc["InternetGatewayExclusionMode"] == "allow-bidirectional"
    # returned (so the launch happens) only once the exclusion read create-complete
    polls = [kw for n, kw in ec2.calls
             if n == "describe_vpc_block_public_access_exclusions" and "ExclusionIds" in kw]
    assert len(polls) == 2 and names.index("create_subnet") < names.index(
        "create_vpc_block_public_access_exclusion")


def test_existing_subnet_and_exclusion_are_reused():
    ec2 = FakeEc2(hive_subnet={"SubnetId": "subnet-hive", "CidrBlock": "172.31.96.0/26",
                               "VpcId": "vpc-1", "State": "available",
                               "AvailabilityZone": "us-east-1a"},
                  exclusions=[{"ExclusionId": "vpcbpa-exclude-old", "State": "create-complete",
                               "InternetGatewayExclusionMode": "allow-bidirectional",
                               "ResourceArn": "arn:aws:ec2:us-east-1:1:subnet/subnet-hive"}],
                  states=("create-complete",))
    assert _ensure(ec2) == "subnet-hive"
    assert "create_subnet" not in ec2.names()
    assert "create_vpc_block_public_access_exclusion" not in ec2.names()


def test_new_subnet_cidr_is_free_and_az_offers_the_type():
    import ipaddress
    ec2 = FakeEc2(offered=("us-east-1f", "us-east-1c"))
    _ensure(ec2)
    create = dict(ec2.calls)["create_subnet"]
    net = ipaddress.ip_network(create["CidrBlock"])
    assert net.prefixlen == 26 and net.subnet_of(ipaddress.ip_network("172.31.0.0/16"))
    assert not any(net.overlaps(ipaddress.ip_network(s["CidrBlock"])) for s in DEFAULT_SUBNETS)
    assert create["AvailabilityZone"] == "us-east-1c"
    offer = dict(ec2.calls)["describe_instance_type_offerings"]
    assert offer["Filters"] == [{"Name": "instance-type", "Values": ["m7g.xlarge"]}]


def test_bpa_off_launches_without_subnet():
    ec2 = FakeEc2(mode="off")
    assert _ensure(ec2) is None
    assert ec2.names() == ["describe_vpc_block_public_access_options"]


def test_bpa_read_denied_launches_as_before(caplog):
    ec2 = FakeEc2(read_error="UnauthorizedOperation")
    with caplog.at_level(logging.WARNING):
        assert _ensure(ec2) is None
    assert ec2.names() == ["describe_vpc_block_public_access_options"]
    assert "ec2:DescribeVpcBlockPublicAccessOptions" in caplog.text


def test_exclusions_not_allowed_stops_before_launch():
    ec2 = FakeEc2(allowed="not-allowed")
    with pytest.raises(bs.BpaError, match="does not allow exclusions"):
        _ensure(ec2)
    assert ec2.names() == ["describe_vpc_block_public_access_options"]


@pytest.mark.parametrize("states,wait_s,match", [
    (("create-in-progress", "create-failed"), 600, "ended in state create-failed"),
    (("create-in-progress",), 0, "was not active after 0 s"),
])
def test_exclusion_that_fails_or_never_completes_raises(states, wait_s, match):
    with pytest.raises(bs.BpaError, match=match):
        _ensure(FakeEc2(states=states), wait_s=wait_s)


def test_release_deletes_exclusion_then_subnet_only_when_empty():
    used = {"SubnetId": "subnet-used", "CidrBlock": "172.31.96.0/26", "State": "available"}
    ec2 = FakeEc2(hive_subnet=used, enis={"subnet-used": [{"NetworkInterfaceId": "eni-1"}]},
                  exclusions=[{"ExclusionId": "x-used", "State": "create-complete",
                               "ResourceArn": "arn:aws:ec2:r:1:subnet/subnet-used"}])
    assert bs.release_unused_hive_subnets(ec2, "us-east-1") == []
    assert "delete_subnet" not in ec2.names()
    assert "delete_vpc_block_public_access_exclusion" not in ec2.names()

    ec2 = FakeEc2(hive_subnet={**used, "SubnetId": "subnet-empty"},
                  exclusions=[{"ExclusionId": "x-empty", "State": "create-complete",
                               "ResourceArn": "arn:aws:ec2:r:1:subnet/subnet-empty"},
                              {"ExclusionId": "x-other", "State": "create-complete",
                               "ResourceArn": "arn:aws:ec2:r:1:subnet/subnet-other"}])
    assert bs.release_unused_hive_subnets(ec2, "us-east-1") == ["subnet-empty"]
    deletes = [(n, kw) for n, kw in ec2.calls if n.startswith("delete_")]
    assert deletes == [("delete_vpc_block_public_access_exclusion", {"ExclusionId": "x-empty"}),
                       ("delete_subnet", {"SubnetId": "subnet-empty"})]


def test_release_failure_is_only_logged(caplog):
    ec2 = FakeEc2(hive_subnet={"SubnetId": "subnet-empty", "CidrBlock": "172.31.96.0/26",
                               "State": "available"}, fail={"delete_subnet"})
    with caplog.at_level(logging.WARNING):
        assert bs.release_unused_hive_subnets(ec2, "us-east-1") == []
    assert "subnet-empty was not released" in caplog.text


def test_a_hive_subnet_in_a_zone_without_the_type_is_not_reused():
    """Hives of different types share a region: a Hive subnet whose zone does not offer
    the new Hive's type gets a second Hive subnet in a zone that does."""
    ec2 = FakeEc2(hive_subnet={"SubnetId": "subnet-old", "CidrBlock": "172.31.96.0/26",
                               "VpcId": "vpc-1", "State": "available",
                               "AvailabilityZone": "us-east-1e"},
                  offered=("us-east-1b",))
    assert _ensure(ec2) == "subnet-hive"
    create = dict(ec2.calls)["create_subnet"]
    assert create["AvailabilityZone"] == "us-east-1b" and create["CidrBlock"] != "172.31.96.0/26"


def test_an_exclusion_not_visible_yet_is_polled_again():
    assert _ensure(FakeEc2(not_found=1)) == "subnet-hive"


def test_launch_placement():
    assert bs.launch_placement(None, "sg-1") == {"SecurityGroupIds": ["sg-1"]}
    assert bs.launch_placement("subnet-hive", "sg-1") == {"NetworkInterfaces": [
        {"DeviceIndex": 0, "SubnetId": "subnet-hive", "Groups": ["sg-1"],
         "AssociatePublicIpAddress": True}]}
