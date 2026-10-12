"""The eight waste rules. Savings use the static price table (see finops/pricing/prices.json)."""

import re
from decimal import Decimal

from finops.domain import Resource, ResourceType, Severity
from finops.rules.base import Rule, RuleContext, age_days

ZERO = Decimal(0)

# Downsizing one step within an instance family roughly halves the price.
_SIZES = ["nano", "micro", "small", "medium", "large", "xlarge", "2xlarge", "4xlarge", "8xlarge"]


def smaller_instance_type(instance_type: str) -> str | None:
    match = re.fullmatch(r"([a-z0-9]+)\.([a-z0-9]+)", instance_type)
    if not match or match.group(2) not in _SIZES:
        return None
    index = _SIZES.index(match.group(2))
    return f"{match.group(1)}.{_SIZES[index - 1]}" if index > 0 else None


def _money(value: object) -> str:
    return f"${Decimal(str(value)):,.2f}"


class Ec2IdleRule(Rule):
    id = "ec2-idle"
    title = "Idle EC2 instance"
    severity = Severity.HIGH
    resource_type = ResourceType.EC2_INSTANCE
    description = (
        "Running instance whose average CPU is below cpu_avg_pct and peak CPU below cpu_max_pct "
        "over the last 7 days. Very idle instances (avg below stop_below_avg_pct) should be "
        "stopped; others downsized one size. Instances with fewer than min_datapoints hourly "
        "samples (e.g. just launched) are skipped."
    )
    default_thresholds = {
        "cpu_avg_pct": 5,
        "cpu_max_pct": 20,
        "stop_below_avg_pct": 2,
        "min_datapoints": 120,
    }

    def detect(self, r: Resource, ctx: RuleContext) -> bool:
        t = self.thresholds(ctx)
        m = r.metrics
        return (
            r.state == "running"
            and m.get("cpu_datapoints_7d", 0) >= t["min_datapoints"]
            and "cpu_avg_7d" in m
            and m["cpu_avg_7d"] < t["cpu_avg_pct"]
            and m.get("cpu_max_7d", 100) < t["cpu_max_pct"]
        )

    def _should_stop(self, r: Resource, ctx: RuleContext) -> bool:
        return r.metrics["cpu_avg_7d"] < self.thresholds(ctx)["stop_below_avg_pct"]

    def estimated_monthly_savings(self, r: Resource, ctx: RuleContext) -> Decimal:
        current = r.est_monthly_cost_usd
        smaller = smaller_instance_type(r.attributes.get("instance_type", ""))
        if self._should_stop(r, ctx) or smaller is None:
            return current
        hourly = ctx.catalog.ec2_hourly(r.region, smaller)
        if hourly is None:
            return current / 2
        return max(ZERO, current - hourly * ctx.catalog.hours_per_month)

    def explain(self, r: Resource, ctx: RuleContext) -> tuple[str, str]:
        m = r.metrics
        itype = r.attributes.get("instance_type", "?")
        detail = (
            f"{itype} averaged {m['cpu_avg_7d']:.1f}% CPU (peak {m.get('cpu_max_7d', 0):.1f}%) "
            "over the last 7 days."
        )
        smaller = smaller_instance_type(itype)
        if self._should_stop(r, ctx) or smaller is None:
            return detail, "Stop the instance (or terminate it if no longer needed)."
        return detail, f"Downsize from {itype} to {smaller}, or stop it if unused."


class EbsUnattachedRule(Rule):
    id = "ebs-unattached"
    title = "Unattached EBS volume"
    severity = Severity.MEDIUM
    resource_type = ResourceType.EBS_VOLUME
    description = (
        "Volume not attached to any instance (state 'available') and older than min_age_days. "
        "It is billed in full while doing nothing."
    )
    default_thresholds = {"min_age_days": 1}

    def detect(self, r: Resource, ctx: RuleContext) -> bool:
        age = age_days(r.created_at, ctx.now)
        return r.state == "available" and (
            age is None or age >= self.thresholds(ctx)["min_age_days"]
        )

    def estimated_monthly_savings(self, r: Resource, ctx: RuleContext) -> Decimal:
        return r.est_monthly_cost_usd

    def explain(self, r: Resource, ctx: RuleContext) -> tuple[str, str]:
        a = r.attributes
        return (
            f"{a.get('size_gb')} GB {a.get('volume_type')} volume is not attached to any instance.",
            "Snapshot it if the data may be needed, then delete the volume.",
        )


class EipUnassociatedRule(Rule):
    id = "eip-unassociated"
    title = "Unassociated Elastic IP"
    severity = Severity.LOW
    resource_type = ResourceType.ELASTIC_IP
    description = (
        "Elastic IP not associated with a running resource. AWS charges for every public IPv4 "
        "address, so an idle one is pure waste."
    )

    def detect(self, r: Resource, ctx: RuleContext) -> bool:
        return r.state == "unassociated"

    def estimated_monthly_savings(self, r: Resource, ctx: RuleContext) -> Decimal:
        return r.est_monthly_cost_usd

    def explain(self, r: Resource, ctx: RuleContext) -> tuple[str, str]:
        ip = r.attributes.get("public_ip", r.resource_id)
        return f"{ip} is allocated but not associated.", "Release the Elastic IP."


class SnapshotOldRule(Rule):
    id = "snapshot-old"
    title = "Old EBS snapshot"
    severity = Severity.LOW
    resource_type = ResourceType.EBS_SNAPSHOT
    description = "EBS snapshot older than max_age_days. Review retention and delete if obsolete."
    default_thresholds = {"max_age_days": 90}

    def detect(self, r: Resource, ctx: RuleContext) -> bool:
        age = age_days(r.created_at, ctx.now)
        return age is not None and age > self.thresholds(ctx)["max_age_days"]

    def estimated_monthly_savings(self, r: Resource, ctx: RuleContext) -> Decimal:
        return r.est_monthly_cost_usd

    def explain(self, r: Resource, ctx: RuleContext) -> tuple[str, str]:
        age = age_days(r.created_at, ctx.now) or 0
        detail = f"{r.attributes.get('size_gb')} GB snapshot is {age:.0f} days old."
        if r.attributes.get("ami_id"):
            return detail, f"Backs {r.attributes['ami_id']}: deregister the AMI first, then delete."
        return detail, "Delete it if it is outside your retention policy (or archive it)."


class Ec2StoppedWithEbsRule(Rule):
    id = "ec2-stopped-ebs"
    title = "Stopped instance still paying for EBS"
    severity = Severity.MEDIUM
    resource_type = ResourceType.EC2_INSTANCE
    description = (
        "Instance stopped for more than stopped_days. Compute is free while stopped, but its EBS "
        "volumes are still billed."
    )
    default_thresholds = {"stopped_days": 7}

    def detect(self, r: Resource, ctx: RuleContext) -> bool:
        stopped = age_days(r.attributes.get("stopped_at"), ctx.now)
        return (
            r.state == "stopped"
            and stopped is not None
            and stopped > self.thresholds(ctx)["stopped_days"]
            and bool(ctx.volumes_of(r))
        )

    def current_cost(self, r: Resource, ctx: RuleContext) -> Decimal:
        return sum((v.est_monthly_cost_usd for v in ctx.volumes_of(r)), ZERO)

    def estimated_monthly_savings(self, r: Resource, ctx: RuleContext) -> Decimal:
        return self.current_cost(r, ctx)

    def explain(self, r: Resource, ctx: RuleContext) -> tuple[str, str]:
        days = age_days(r.attributes.get("stopped_at"), ctx.now) or 0
        volumes = ctx.volumes_of(r)
        gb = sum(int(v.attributes.get("size_gb", 0)) for v in volumes)
        return (
            f"Stopped for {days:.0f} days; {len(volumes)} volume(s), {gb} GB, still billed.",
            "Create an AMI/snapshot, then terminate the instance and delete its volumes.",
        )


class EbsGp2ToGp3Rule(Rule):
    id = "ebs-gp2-to-gp3"
    title = "gp2 volume can move to gp3"
    severity = Severity.LOW
    resource_type = ResourceType.EBS_VOLUME
    description = (
        "gp3 is ~20% cheaper per GB than gp2 with equal or better baseline performance "
        "(3,000 IOPS). Migration is online with no downtime."
    )
    default_thresholds = {"min_size_gb": 1}

    def detect(self, r: Resource, ctx: RuleContext) -> bool:
        return (
            r.attributes.get("volume_type") == "gp2"
            and float(r.attributes.get("size_gb", 0)) >= self.thresholds(ctx)["min_size_gb"]
        )

    def estimated_monthly_savings(self, r: Resource, ctx: RuleContext) -> Decimal:
        gp2 = ctx.catalog.ebs_gb_month(r.region, "gp2") or ZERO
        gp3 = ctx.catalog.ebs_gb_month(r.region, "gp3") or ZERO
        return max(ZERO, (gp2 - gp3) * Decimal(str(r.attributes.get("size_gb", 0))))

    def explain(self, r: Resource, ctx: RuleContext) -> tuple[str, str]:
        size = r.attributes.get("size_gb")
        return (
            f"{size} GB gp2 volume.",
            "Modify the volume type to gp3 (online, no downtime).",
        )


class S3NoLifecycleRule(Rule):
    id = "s3-no-lifecycle"
    title = "S3 bucket without lifecycle policy"
    severity = Severity.LOW
    resource_type = ResourceType.S3_BUCKET
    description = (
        "Bucket of at least min_size_gb with no enabled lifecycle rule. Savings assume ia_share of "
        "the data could move to Standard-IA after 30 days (configurable estimate)."
    )
    default_thresholds = {"min_size_gb": 1, "ia_share": 0.4}

    def detect(self, r: Resource, ctx: RuleContext) -> bool:
        return (
            not r.attributes.get("has_lifecycle_policy", False)
            and float(r.attributes.get("size_gb", 0)) >= self.thresholds(ctx)["min_size_gb"]
        )

    def estimated_monthly_savings(self, r: Resource, ctx: RuleContext) -> Decimal:
        standard = ctx.catalog.s3_gb_month(r.region, "STANDARD") or ZERO
        ia = ctx.catalog.s3_gb_month(r.region, "STANDARD_IA") or standard
        share = Decimal(str(self.thresholds(ctx)["ia_share"]))
        return max(ZERO, Decimal(str(r.attributes.get("size_gb", 0))) * share * (standard - ia))

    def explain(self, r: Resource, ctx: RuleContext) -> tuple[str, str]:
        size = float(r.attributes.get("size_gb", 0))
        return (
            f"{size:,.0f} GB in S3 Standard with no lifecycle rule.",
            "Add a lifecycle rule: transition to Standard-IA after 30 days, expire old versions "
            "and incomplete multipart uploads.",
        )


class RdsIdleRule(Rule):
    id = "rds-idle"
    title = "Idle RDS instance"
    severity = Severity.HIGH
    resource_type = ResourceType.RDS_INSTANCE
    description = (
        "Available RDS instance whose peak connection count over 7 days is at or below "
        "max_connections (~0)."
    )
    default_thresholds = {"max_connections": 0.5}

    def detect(self, r: Resource, ctx: RuleContext) -> bool:
        peak = r.metrics.get("db_connections_max_7d")
        return (
            r.state == "available"
            and peak is not None
            and peak <= self.thresholds(ctx)["max_connections"]
        )

    def estimated_monthly_savings(self, r: Resource, ctx: RuleContext) -> Decimal:
        # Snapshot + delete removes compute and provisioned storage (snapshot cost is minor).
        return r.est_monthly_cost_usd

    def explain(self, r: Resource, ctx: RuleContext) -> tuple[str, str]:
        a = r.attributes
        return (
            f"{a.get('instance_class')} ({a.get('engine')}) had no connections in 7 days.",
            "Take a final snapshot and delete the instance (or stop it, max 7 days at a time).",
        )


ALL_RULES: list[Rule] = [
    Ec2IdleRule(),
    Ec2StoppedWithEbsRule(),
    EbsUnattachedRule(),
    EbsGp2ToGp3Rule(),
    EipUnassociatedRule(),
    SnapshotOldRule(),
    S3NoLifecycleRule(),
    RdsIdleRule(),
]
RULES_BY_ID: dict[str, Rule] = {rule.id: rule for rule in ALL_RULES}
