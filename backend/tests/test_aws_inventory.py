from unittest.mock import patch

import pytest
from botocore.exceptions import ClientError
from moto import mock_aws
from tests.aws_fixtures import REGION, build_world, make_account

from finops.aws.inventory import InventoryScanner, _parse_stopped_at
from finops.aws.session import AccountSession
from finops.domain import ResourceType


@pytest.fixture(scope="module")
def scanned():
    with mock_aws():
        world = build_world()
        session = AccountSession.assume(make_account())
        result = InventoryScanner(session).scan([REGION, "us-east-1"])
        yield world, result


def by_id(result):
    return {r.resource_id: r for r in result.resources}


def test_scan_finds_every_resource_type(scanned):
    _, result = scanned
    assert result.errors == [] and result.failed == set()
    assert {r.type for r in result.resources} == set(ResourceType)
    assert all(r.account_id == "acc1" for r in result.resources)


def test_ec2_instances(scanned):
    world, result = scanned
    res = by_id(result)
    running = res[world.running_instance]
    assert running.state == "running" and running.region == REGION
    assert running.name == "web-1"
    assert running.tags["Owner"] == "web-team"
    assert running.attributes["instance_type"] == "m5.large"
    assert running.attributes["volume_ids"]  # root volume
    stopped = res[world.stopped_instance]
    assert stopped.state == "stopped"
    assert stopped.attributes["stopped_at"] is not None


def test_ebs_volumes_and_snapshots(scanned):
    world, result = scanned
    res = by_id(result)
    vol = res[world.unattached_volume]
    assert vol.state == "available"
    assert vol.attributes["attached_instance_id"] is None
    assert (vol.attributes["volume_type"], vol.attributes["size_gb"]) == ("gp2", 100)
    attached = [
        r
        for r in result.resources
        if r.type == ResourceType.EBS_VOLUME and r.attributes["attached_instance_id"]
    ]
    assert {r.attributes["attached_instance_id"] for r in attached} >= {world.running_instance}
    snap = res[world.snapshot]
    assert snap.attributes["size_gb"] == 100 and snap.attributes["ami_id"] is None


def test_elastic_ips(scanned):
    world, result = scanned
    res = by_id(result)
    assert res[world.free_eip].state == "unassociated"
    assert res[world.used_eip].state == "associated"
    assert res[world.used_eip].attributes["instance_id"] == world.running_instance


def test_rds_and_lambda(scanned):
    world, result = scanned
    db = next(r for r in result.resources if r.type == ResourceType.RDS_INSTANCE)
    assert db.name == world.db_name
    assert db.attributes["instance_class"] == "db.t3.medium"
    assert db.tags == {"Project": "shop"}
    fn = next(r for r in result.resources if r.type == ResourceType.LAMBDA_FUNCTION)
    assert fn.name == world.function_name
    assert fn.attributes["memory_mb"] == 512
    assert fn.tags == {"Project": "shop"}


def test_s3_buckets_region_lifecycle_and_tags(scanned):
    _, result = scanned
    buckets = {r.resource_id: r for r in result.resources if r.type == ResourceType.S3_BUCKET}
    assert buckets["shop-logs"].region == REGION
    assert buckets["shop-logs"].attributes["has_lifecycle_policy"] is False
    assert buckets["shop-logs"].tags == {"Project": "shop"}
    assert buckets["shop-archive"].region == "us-east-1"
    assert buckets["shop-archive"].attributes["has_lifecycle_policy"] is True
    assert buckets["shop-archive"].tags == {}


def test_one_failing_scan_does_not_stop_the_others(scanned):
    # Runs inside the module's mocked account built by `scanned`.
    denied = ClientError(
        {"Error": {"Code": "AccessDenied", "Message": "nope"}}, "DescribeDBInstances"
    )
    session = AccountSession.assume(make_account())
    with patch.object(InventoryScanner, "rds_instances", side_effect=denied):
        result = InventoryScanner(session).scan([REGION])
    assert result.failed == {(REGION, ResourceType.RDS_INSTANCE)}
    assert "AccessDenied" in result.errors[0]
    types = {r.type for r in result.resources}
    assert ResourceType.RDS_INSTANCE not in types
    assert ResourceType.EC2_INSTANCE in types


def test_parse_stopped_at():
    assert _parse_stopped_at("User initiated (2026-08-01 10:15:00 GMT)") == (
        "2026-08-01T10:15:00+00:00"
    )
    assert _parse_stopped_at("User initiated (2026-08-01 10:15:00 UTC)") is not None
    assert _parse_stopped_at("") is None
    assert _parse_stopped_at(None) is None
