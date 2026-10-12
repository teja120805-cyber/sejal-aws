from decimal import Decimal

import pytest

from finops.domain import Resource, ResourceType
from finops.pricing import default_catalog, estimate_monthly_cost


def res(type: ResourceType, region="ap-south-1", state=None, **attributes) -> Resource:
    return Resource(
        account_id="a",
        resource_id="x",
        type=type,
        region=region,
        state=state,
        attributes=attributes,
    )


@pytest.mark.parametrize(
    ("resource", "expected"),
    [
        # 0.101 $/h * 730 h
        (res(ResourceType.EC2_INSTANCE, state="running", instance_type="m5.large"), "73.73"),
        (res(ResourceType.EC2_INSTANCE, state="stopped", instance_type="m5.large"), "0"),
        (res(ResourceType.EC2_INSTANCE, state="running", instance_type="unknown.type"), "0"),
        # 100 GB * 0.114
        (res(ResourceType.EBS_VOLUME, volume_type="gp2", size_gb=100), "11.40"),
        (res(ResourceType.EBS_VOLUME, volume_type="gp3", size_gb=100), "9.12"),
        (res(ResourceType.EBS_SNAPSHOT, size_gb=200), "10.00"),
        # 0.005 $/h * 730 h
        (res(ResourceType.ELASTIC_IP), "3.65"),
        # (0.19 * 730 + 0.131 * 200) * 2 for Multi-AZ
        (
            res(
                ResourceType.RDS_INSTANCE,
                state="available",
                instance_class="db.m5.large",
                multi_az=True,
                storage_gb=200,
            ),
            "329.80",
        ),
        (res(ResourceType.S3_BUCKET, size_gb=1000, storage_class="STANDARD"), "25.00"),
        # 1M invocations * 0.2 s * 0.5 GB = 100k GB-s => 1.66667 + 0.20 requests
        (
            res(
                ResourceType.LAMBDA_FUNCTION,
                monthly_invocations=1_000_000,
                avg_duration_ms=200,
                memory_mb=512,
            ),
            "1.8667",
        ),
    ],
)
def test_estimate_monthly_cost(resource, expected):
    assert estimate_monthly_cost(resource) == Decimal(expected)


def test_unknown_region_falls_back_to_us_east_1():
    eu = res(
        ResourceType.EC2_INSTANCE, region="eu-west-1", state="running", instance_type="t3.micro"
    )
    us = res(
        ResourceType.EC2_INSTANCE, region="us-east-1", state="running", instance_type="t3.micro"
    )
    assert estimate_monthly_cost(eu) == estimate_monthly_cost(us)


def test_catalog_has_both_required_regions():
    cat = default_catalog()
    assert {"ap-south-1", "us-east-1"} <= set(cat.regions)
    assert cat.hours_per_month == 730
    # gp3 is cheaper than gp2 everywhere - the gp2->gp3 rule depends on it
    for region in cat.regions:
        assert cat.ebs_gb_month(region, "gp3") < cat.ebs_gb_month(region, "gp2")
