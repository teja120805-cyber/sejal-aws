"""Realistic, deterministic demo data so the whole platform runs without an AWS account.

- 90 days of daily cost across 8 services and 3 regions (incl. "global"), with a weekday/weekend
  pattern, gentle growth, ~4% noise and one clear spike (a forgotten GPU instance) 18 days ago.
- Matching cost-by-tag data for Project / Owner / Environment, including untagged spend.
- ~45 resources with deliberately wasteful ones that each waste rule should catch.

Same `today` + same seed => identical data every run, so demos and tests are reproducible.
"""

import datetime as dt
import hashlib
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import numpy as np

from finops.domain import (
    Account,
    AccountSettings,
    AccountStatus,
    Budget,
    CostForecast,
    DailyCost,
    Resource,
    ResourceType,
    TagCost,
    to_money,
)
from finops.pricing import estimate_monthly_cost
from finops.repositories.base import Repositories

DEMO_ACCOUNT_ID = "demo"
DEMO_AWS_ACCOUNT = "123456789012"
DEMO_EXTERNAL_ID = "d3m0e7a1-0000-4000-8000-f1n0p5000001"
SEED = 42
HISTORY_DAYS = 90
SPIKE_DAYS_AGO = 18
PRIMARY, SECONDARY = "ap-south-1", "us-east-1"


@dataclass(frozen=True)
class ServiceProfile:
    name: str  # Cost Explorer SERVICE dimension value
    base_daily_usd: float
    weekend_factor: float  # 1.0 = flat (storage), <1 = usage drops at weekends
    regions: dict[str, float]  # share of spend per region (sums to 1)


SERVICES = [
    ServiceProfile(
        "Amazon Elastic Compute Cloud - Compute", 38.0, 0.72, {PRIMARY: 0.8, SECONDARY: 0.2}
    ),
    ServiceProfile("EC2 - Other", 7.5, 0.95, {PRIMARY: 0.85, SECONDARY: 0.15}),
    ServiceProfile("Amazon Relational Database Service", 16.0, 1.0, {PRIMARY: 0.9, SECONDARY: 0.1}),
    ServiceProfile("Amazon Simple Storage Service", 4.2, 0.98, {PRIMARY: 0.75, SECONDARY: 0.25}),
    ServiceProfile("Amazon Elastic Load Balancing", 4.8, 1.0, {PRIMARY: 1.0}),
    ServiceProfile("Amazon CloudFront", 3.1, 0.6, {"global": 1.0}),
    ServiceProfile("AmazonCloudWatch", 1.6, 1.0, {PRIMARY: 0.8, SECONDARY: 0.2}),
    ServiceProfile("AWS Lambda", 1.1, 0.55, {PRIMARY: 1.0}),
]

# Share of each day's total cost per tag value ("" = resources missing the tag).
TAG_SPLITS: dict[str, dict[str, float]] = {
    "Project": {"payments-api": 0.41, "web-frontend": 0.22, "data-pipeline": 0.2, "": 0.17},
    "Owner": {"platform-team": 0.38, "data-team": 0.27, "web-team": 0.2, "": 0.15},
    "Environment": {"prod": 0.62, "staging": 0.17, "dev": 0.09, "": 0.12},
}


@dataclass
class DemoDataset:
    account: Account
    daily_costs: list[DailyCost]
    tag_costs: list[TagCost]
    forecast: CostForecast
    resources: list[Resource]
    budget: Budget
    settings: AccountSettings
    summary: dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------------------------------
# Costs
# --------------------------------------------------------------------------------------------


def _generate_costs(rng: np.random.Generator, end: dt.date) -> list[DailyCost]:
    rows: list[DailyCost] = []
    start = end - timedelta(days=HISTORY_DAYS - 1)
    for i in range(HISTORY_DAYS):
        day = start + timedelta(days=i)
        trend = 1 + 0.0015 * i  # ~14% growth over the 90 days
        is_weekend = day.weekday() >= 5
        days_ago = (end - day).days + 1
        for svc in SERVICES:
            amount = svc.base_daily_usd * trend * float(np.clip(rng.normal(1, 0.04), 0.88, 1.12))
            if is_weekend:
                amount *= svc.weekend_factor
            if days_ago == SPIKE_DAYS_AGO:
                # Someone launched GPU instances for load testing and forgot them for a day.
                if svc.name.endswith("Compute"):
                    amount *= 3.4
                elif svc.name == "EC2 - Other":
                    amount *= 1.7  # extra EBS + data transfer
            for region, share in svc.regions.items():
                rows.append(
                    DailyCost(
                        account_id=DEMO_ACCOUNT_ID,
                        date=day,
                        service=svc.name,
                        region=region,
                        amount_usd=to_money(amount * share),
                    )
                )
    return rows


def _generate_tag_costs(costs: list[DailyCost]) -> list[TagCost]:
    totals: dict[dt.date, Decimal] = defaultdict(Decimal)
    for c in costs:
        totals[c.date] += c.amount_usd
    rows: list[TagCost] = []
    for day, total in sorted(totals.items()):
        for tag_key, split in TAG_SPLITS.items():
            for tag_value, share in split.items():
                rows.append(
                    TagCost(
                        account_id=DEMO_ACCOUNT_ID,
                        date=day,
                        tag_key=tag_key,
                        tag_value=tag_value,
                        amount_usd=to_money(total * Decimal(str(share))),
                    )
                )
    return rows


def _ce_style_forecast(costs: list[DailyCost], today: dt.date, now: datetime) -> CostForecast:
    """Stand-in for Cost Explorer's GetCostForecast: month-to-date + recent run-rate."""
    month_start = today.replace(day=1)
    next_month = (month_start + timedelta(days=32)).replace(day=1)
    by_day: dict[dt.date, Decimal] = defaultdict(Decimal)
    for c in costs:
        by_day[c.date] += c.amount_usd
    mtd = sum((v for d, v in by_day.items() if d >= month_start), Decimal(0))
    last_14 = sorted(by_day)[-14:]
    run_rate = sum((by_day[d] for d in last_14), Decimal(0)) / len(last_14)
    remaining_days = (next_month - max(today, month_start)).days
    forecast = mtd + run_rate * remaining_days
    return CostForecast(
        account_id=DEMO_ACCOUNT_ID,
        month=month_start.strftime("%Y-%m"),
        forecast_usd=forecast,
        lower_usd=forecast * Decimal("0.93"),
        upper_usd=forecast * Decimal("1.07"),
        generated_at=now,
    )


# --------------------------------------------------------------------------------------------
# Resources
# --------------------------------------------------------------------------------------------


def _hex_id(prefix: str, seed: str, length: int = 17) -> str:
    return f"{prefix}-{hashlib.sha1(seed.encode()).hexdigest()[:length]}"


def _tags(
    project: str | None, owner: str | None, env: str | None, name: str | None
) -> dict[str, str]:
    tags = {"Name": name, "Project": project, "Owner": owner, "Environment": env}
    return {k: v for k, v in tags.items() if v}


PROD = ("payments-api", "platform-team", "prod")
WEB = ("web-frontend", "web-team", "prod")
DATA = ("data-pipeline", "data-team", "prod")
STAGING = ("payments-api", "platform-team", "staging")

# name, type, region, state, tags(project, owner, env), cpu (avg, max) or None, launched days ago,
# stopped days ago (for stopped instances)
EC2_SPECS: list[
    tuple[str, str, str, str, tuple[str | None, ...], tuple[float, float] | None, int, int | None]
] = [
    ("web-prod-1", "m5.large", PRIMARY, "running", WEB, (38.2, 81.0), 410, None),
    ("web-prod-2", "m5.large", PRIMARY, "running", WEB, (35.6, 77.4), 410, None),
    ("api-prod-1", "c5.xlarge", PRIMARY, "running", PROD, (52.1, 90.3), 300, None),
    ("api-prod-2", "c5.xlarge", PRIMARY, "running", PROD, (47.9, 88.1), 300, None),
    ("worker-prod-1", "r5.large", PRIMARY, "running", DATA, (22.4, 64.0), 220, None),
    (
        "jenkins-old",
        "t3.large",
        PRIMARY,
        "running",
        ("payments-api", None, "dev"),
        (1.8, 9.5),
        700,
        None,
    ),
    (
        "poc-ml-experiment",
        "m5.xlarge",
        SECONDARY,
        "running",
        (None, None, None),
        (0.9, 6.2),
        120,
        None,
    ),
    ("staging-api-1", "t3.medium", PRIMARY, "running", STAGING, (3.1, 14.0), 160, None),
    (
        "bastion",
        "t3.micro",
        PRIMARY,
        "running",
        PROD,
        (2.5, 35.0),
        500,
        None,
    ),  # low avg, but bursts
    ("legacy-reporting", "m5.large", PRIMARY, "stopped", DATA, None, 900, 45),
    ("old-dev-box", "t3.large", SECONDARY, "stopped", (None, "web-team", "dev"), None, 380, 21),
    (
        "dev-sandbox",
        "t3.small",
        PRIMARY,
        "stopped",
        ("web-frontend", "web-team", "dev"),
        None,
        60,
        3,
    ),
]

# name, region, type, size GB, attached-to (EC2 name) or None, tags, created days ago
EBS_SPECS: list[tuple[str, str, str, int, str | None, tuple[str | None, ...], int]] = [
    ("web-prod-1-root", PRIMARY, "gp3", 100, "web-prod-1", WEB, 410),
    ("web-prod-2-root", PRIMARY, "gp3", 100, "web-prod-2", WEB, 410),
    ("api-prod-1-root", PRIMARY, "gp3", 80, "api-prod-1", PROD, 300),
    ("api-prod-2-root", PRIMARY, "gp3", 80, "api-prod-2", PROD, 300),
    ("worker-prod-1-data", PRIMARY, "gp2", 500, "worker-prod-1", DATA, 220),
    ("jenkins-old-root", PRIMARY, "gp2", 200, "jenkins-old", ("payments-api", None, "dev"), 700),
    ("legacy-reporting-root", PRIMARY, "gp2", 300, "legacy-reporting", DATA, 900),
    ("old-dev-box-root", SECONDARY, "gp2", 100, "old-dev-box", (None, "web-team", "dev"), 380),
    ("old-db-backup", PRIMARY, "gp2", 750, None, (None, None, None), 260),
    ("detached-after-migration", PRIMARY, "gp3", 200, None, DATA, 95),
    ("", SECONDARY, "gp2", 50, None, (None, None, None), 140),
]

# description, region, size GB, age days
SNAPSHOT_SPECS = [
    ("daily backup web-prod-1-root", PRIMARY, 100, 6),
    ("daily backup api-prod-1-root", PRIMARY, 80, 30),
    ("pre-upgrade worker-prod-1-data", PRIMARY, 500, 64),
    ("before migration 2025", PRIMARY, 750, 128),
    ("legacy-reporting manual snapshot", PRIMARY, 300, 215),
    ("ami build artefact", SECONDARY, 30, 410),
]

# name, region, associated EC2 name or None, tags
EIP_SPECS = [
    ("web-prod-eip", PRIMARY, "web-prod-1", WEB),
    ("old-nat-eip", PRIMARY, None, (None, None, None)),
    ("poc-eip", SECONDARY, None, ("data-pipeline", None, None)),
]

# name, region, class, engine, multi_az, storage GB, (avg conns, max conns), tags
RDS_SPECS = [
    ("orders-db", PRIMARY, "db.m5.large", "mysql", True, 200, (42.0, 95.0), PROD),
    ("analytics-db", PRIMARY, "db.t3.medium", "postgres", False, 100, (6.0, 14.0), DATA),
    (
        "legacy-crm-db",
        PRIMARY,
        "db.t3.medium",
        "mysql",
        False,
        50,
        (0.0, 0.0),
        (None, "data-team", "prod"),
    ),
]

# bucket, region, size GB, has lifecycle, tags
S3_SPECS = [
    ("demo-finops-app-assets", PRIMARY, 120, True, WEB),
    ("demo-finops-app-logs", PRIMARY, 850, False, PROD),
    ("demo-finops-data-lake-raw", PRIMARY, 2400, True, DATA),
    ("demo-finops-backups-archive", SECONDARY, 1300, False, (None, "platform-team", None)),
    ("demo-finops-cf-templates", SECONDARY, 0.2, False, (None, None, None)),
]

# name, runtime, memory MB, monthly invocations, avg duration ms, tags
LAMBDA_SPECS = [
    ("order-processor", "python3.12", 512, 2_400_000, 180, PROD),
    ("image-resizer", "nodejs20.x", 1024, 650_000, 420, WEB),
    ("nightly-report", "python3.12", 256, 30, 25_000, DATA),
    ("slack-notifier", "python3.11", 128, 1_200, 300, (None, None, None)),
]


def _generate_resources(now: datetime) -> list[Resource]:
    def ago(days: float) -> datetime:
        return now - timedelta(days=days)

    def make(**kwargs: Any) -> Resource:
        r = Resource(account_id=DEMO_ACCOUNT_ID, last_seen_at=now, **kwargs)
        r.est_monthly_cost_usd = estimate_monthly_cost(r)
        return r

    resources: list[Resource] = []
    instance_ids = {spec[0]: _hex_id("i", spec[0]) for spec in EC2_SPECS}
    volume_ids = {
        spec[0] or f"vol{n}": _hex_id("vol", f"vol-{n}-{spec[0]}")
        for n, spec in enumerate(EBS_SPECS)
    }

    for name, itype, region, state, (proj, owner, env), cpu, launched, stopped in EC2_SPECS:
        attrs: dict[str, Any] = {
            "instance_type": itype,
            "platform": "linux",
            "availability_zone": f"{region}a",
            "volume_ids": [volume_ids[v[0]] for v in EBS_SPECS if v[4] == name],
        }
        if stopped is not None:
            attrs["stopped_at"] = ago(stopped).isoformat()
        metrics = (
            {"cpu_avg_7d": cpu[0], "cpu_max_7d": cpu[1], "cpu_datapoints_7d": 168.0} if cpu else {}
        )
        resources.append(
            make(
                resource_id=instance_ids[name],
                type=ResourceType.EC2_INSTANCE,
                region=region,
                name=name,
                state=state,
                tags=_tags(proj, owner, env, name),
                created_at=ago(launched),
                attributes=attrs,
                metrics=metrics,
            )
        )

    for n, (name, region, vtype, size, attached, (proj, owner, env), created) in enumerate(
        EBS_SPECS
    ):
        resources.append(
            make(
                resource_id=volume_ids[name or f"vol{n}"],
                type=ResourceType.EBS_VOLUME,
                region=region,
                name=name or None,
                state="in-use" if attached else "available",
                tags=_tags(proj, owner, env, name or None),
                created_at=ago(created),
                attributes={
                    "volume_type": vtype,
                    "size_gb": size,
                    "iops": 3000 if vtype == "gp3" else max(100, size * 3),
                    "attached_instance_id": instance_ids[attached] if attached else None,
                    "availability_zone": f"{region}a",
                },
            )
        )

    for desc, region, size, age in SNAPSHOT_SPECS:
        resources.append(
            make(
                resource_id=_hex_id("snap", desc),
                type=ResourceType.EBS_SNAPSHOT,
                region=region,
                name=desc,
                state="completed",
                created_at=ago(age),
                attributes={"size_gb": size, "description": desc},
            )
        )

    for i, (name, region, assoc, (proj, owner, env)) in enumerate(EIP_SPECS):
        resources.append(
            make(
                resource_id=_hex_id("eipalloc", name),
                type=ResourceType.ELASTIC_IP,
                region=region,
                name=name,
                state="associated" if assoc else "unassociated",
                tags=_tags(proj, owner, env, name),
                attributes={
                    "public_ip": f"13.232.{10 + i}.{40 + i * 7}",
                    "association_id": _hex_id("eipassoc", name) if assoc else None,
                    "instance_id": instance_ids[assoc] if assoc else None,
                },
            )
        )

    for name, region, iclass, engine, multi_az, storage, (avg, mx), (proj, owner, env) in RDS_SPECS:
        resources.append(
            make(
                resource_id=f"arn:aws:rds:{region}:{DEMO_AWS_ACCOUNT}:db:{name}",
                type=ResourceType.RDS_INSTANCE,
                region=region,
                name=name,
                state="available",
                tags=_tags(proj, owner, env, None),
                created_at=ago(500),
                attributes={
                    "instance_class": iclass,
                    "engine": engine,
                    "multi_az": multi_az,
                    "storage_gb": storage,
                    "storage_type": "gp2",
                },
                metrics={"db_connections_avg_7d": avg, "db_connections_max_7d": mx},
            )
        )

    for bucket, region, size, lifecycle, (proj, owner, env) in S3_SPECS:
        resources.append(
            make(
                resource_id=bucket,
                type=ResourceType.S3_BUCKET,
                region=region,
                name=bucket,
                tags=_tags(proj, owner, env, None),
                created_at=ago(600),
                attributes={
                    "size_gb": size,
                    "storage_class": "STANDARD",
                    "has_lifecycle_policy": lifecycle,
                    "versioning": "Enabled" if lifecycle else "Suspended",
                },
                metrics={"bucket_size_bytes": float(size * 1024**3)},
            )
        )

    for name, runtime, memory, invocations, duration, (proj, owner, env) in LAMBDA_SPECS:
        resources.append(
            make(
                resource_id=f"arn:aws:lambda:{PRIMARY}:{DEMO_AWS_ACCOUNT}:function:{name}",
                type=ResourceType.LAMBDA_FUNCTION,
                region=PRIMARY,
                name=name,
                state="Active",
                tags=_tags(proj, owner, env, None),
                created_at=ago(200),
                attributes={
                    "runtime": runtime,
                    "memory_mb": memory,
                    "monthly_invocations": invocations,
                    "avg_duration_ms": duration,
                },
                metrics={"invocations_30d": float(invocations)},
            )
        )

    return resources


# --------------------------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------------------------


def generate_demo_dataset(today: dt.date | None = None, now: datetime | None = None) -> DemoDataset:
    now = now or datetime.now(UTC)
    today = today or now.date()
    end = today - timedelta(days=1)  # Cost Explorer data for "today" is incomplete
    rng = np.random.default_rng(SEED)

    costs = _generate_costs(rng, end)
    tag_costs = _generate_tag_costs(costs)
    forecast = _ce_style_forecast(costs, today, now)
    resources = _generate_resources(now)

    account = Account(
        id=DEMO_ACCOUNT_ID,
        name="Demo Production",
        role_arn=f"arn:aws:iam::{DEMO_AWS_ACCOUNT}:role/FinOpsReadOnly",
        remediation_role_arn=f"arn:aws:iam::{DEMO_AWS_ACCOUNT}:role/FinOpsRemediation",
        external_id=DEMO_EXTERNAL_ID,
        regions=[PRIMARY, SECONDARY],
        status=AccountStatus.CONNECTED,
        created_at=now - timedelta(days=HISTORY_DAYS),
        last_tested_at=now - timedelta(hours=6),
        last_collected_at=now - timedelta(hours=6),
    )
    # Budget sits just above the typical month, so the forecast crosses the 80% threshold.
    budget = Budget(account_id=DEMO_ACCOUNT_ID, monthly_limit_usd=Decimal("2600"))
    settings = AccountSettings(
        account_id=DEMO_ACCOUNT_ID,
        alert_emails=["finops-demo@example.com"],
        slack_enabled=False,
    )

    summary = {
        "days": HISTORY_DAYS,
        "first_day": (end - timedelta(days=HISTORY_DAYS - 1)).isoformat(),
        "last_day": end.isoformat(),
        "spike_day": (end - timedelta(days=SPIKE_DAYS_AGO - 1)).isoformat(),
        "services": len(SERVICES),
        "daily_cost_rows": len(costs),
        "tag_cost_rows": len(tag_costs),
        "resources": len(resources),
        "total_cost_usd": float(sum((c.amount_usd for c in costs), Decimal(0))),
    }
    return DemoDataset(account, costs, tag_costs, forecast, resources, budget, settings, summary)


def load_dataset(repos: Repositories, ds: DemoDataset) -> None:
    repos.accounts.put(ds.account)
    repos.costs.put_daily_costs(ds.daily_costs)
    repos.costs.put_tag_costs(ds.tag_costs)
    repos.costs.put_forecast(ds.forecast)
    repos.resources.replace_inventory(ds.account.id, ds.resources)
    repos.budgets.put(ds.budget)
    repos.settings.put(ds.settings)
    # Run the waste rules so the demo has recommendations straight away.
    from finops.rules import refresh_findings

    refresh_findings(repos, ds.account.id, ds.account.last_collected_at or ds.forecast.generated_at)
