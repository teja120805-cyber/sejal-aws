"""Estimated monthly cost of a single resource, from the static price table.

These are list-price estimates for prioritising waste, not a bill: the source of truth for actual
spend is Cost Explorer.
"""

from decimal import Decimal

from finops.domain import Resource, ResourceType, to_money
from finops.pricing.catalog import PriceCatalog, default_catalog

ZERO = Decimal(0)


def _num(value: object, default: float = 0) -> Decimal:
    return Decimal(str(value if value is not None else default))


def estimate_monthly_cost(resource: Resource, catalog: PriceCatalog | None = None) -> Decimal:
    c = catalog or default_catalog()
    a = resource.attributes
    region = resource.region
    hours = c.hours_per_month
    cost: Decimal | None

    match resource.type:
        case ResourceType.EC2_INSTANCE:
            # Stopped instances pay nothing for compute; their EBS volumes are counted separately.
            hourly = c.ec2_hourly(region, a.get("instance_type", ""))
            cost = hourly * hours if resource.state == "running" and hourly else ZERO
        case ResourceType.EBS_VOLUME:
            rate = c.ebs_gb_month(region, a.get("volume_type", "gp2")) or ZERO
            cost = rate * _num(a.get("size_gb"))
        case ResourceType.EBS_SNAPSHOT:
            # Snapshots are incremental; full volume size is an upper bound.
            cost = c.snapshot_gb_month(region) * _num(a.get("size_gb"))
        case ResourceType.ELASTIC_IP:
            # Since Feb 2024 every public IPv4 address is charged, attached or not.
            cost = c.public_ipv4_hourly(region) * hours
        case ResourceType.RDS_INSTANCE:
            hourly = c.rds_hourly(region, a.get("instance_class", "")) or ZERO
            multiplier = 2 if a.get("multi_az") else 1
            storage = c.rds_storage_gb_month(region) * _num(a.get("storage_gb")) * multiplier
            compute = hourly * hours * multiplier if resource.state == "available" else ZERO
            cost = compute + storage
        case ResourceType.S3_BUCKET:
            rate = c.s3_gb_month(region, a.get("storage_class", "STANDARD")) or ZERO
            cost = rate * _num(a.get("size_gb"))
        case ResourceType.LAMBDA_FUNCTION:
            invocations = _num(a.get("monthly_invocations"))
            seconds = invocations * _num(a.get("avg_duration_ms")) / 1000
            gb_seconds = seconds * _num(a.get("memory_mb"), 128) / 1024
            requests = invocations / 1_000_000
            compute = gb_seconds * c.lambda_gb_second(region)
            cost = compute + requests * c.lambda_per_million_requests(region)
        case _:
            cost = ZERO

    return to_money(cost)
