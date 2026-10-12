from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from finops.demo import generate_demo_dataset, load_dataset
from finops.domain import AccountSettings, FindingStatus, Resource, ResourceType
from finops.pricing import default_catalog, estimate_monthly_cost
from finops.repositories.memory import create_memory_repositories
from finops.rules import ALL_RULES, RULES_BY_ID, RuleContext, run_rules, sync_findings
from finops.rules.definitions import smaller_instance_type
from finops.rules.engine import refresh_findings, total_savings

NOW = datetime(2026, 10, 11, 9, 0, tzinfo=UTC)
REGION = "ap-south-1"


def res(type: ResourceType, rid: str = "r-1", state=None, metrics=None, created_days=None, **attrs):
    r = Resource(
        account_id="a",
        resource_id=rid,
        type=type,
        region=REGION,
        state=state,
        metrics=metrics or {},
        attributes=attrs,
        created_at=NOW - timedelta(days=created_days) if created_days is not None else None,
    )
    r.est_monthly_cost_usd = estimate_monthly_cost(r)
    return r


def ctx(resources=(), thresholds=None) -> RuleContext:
    return RuleContext(
        account_id="a",
        now=NOW,
        catalog=default_catalog(),
        settings=AccountSettings(account_id="a", rule_thresholds=thresholds or {}),
        resources={r.resource_id: r for r in resources},
    )


def cpu(avg, peak, points=168):
    return {"cpu_avg_7d": avg, "cpu_max_7d": peak, "cpu_datapoints_7d": points}


# ---------------------------------------------------------------- registry


def test_eight_rules_with_complete_metadata():
    assert len(ALL_RULES) == 8 and len(RULES_BY_ID) == 8
    for rule in ALL_RULES:
        meta = rule.metadata()
        assert meta["id"] and meta["title"] and meta["description"]
        assert meta["severity"] in {"low", "medium", "high"}


# ---------------------------------------------------------------- ec2-idle


@pytest.mark.parametrize(
    ("metrics", "state", "expected"),
    [
        (cpu(3, 15), "running", True),
        (cpu(3, 25), "running", False),  # bursts above 20% peak
        (cpu(6, 15), "running", False),  # average too high
        (cpu(1, 5, points=10), "running", False),  # too little data (new instance)
        (cpu(1, 5), "stopped", False),
        ({}, "running", False),
    ],
)
def test_ec2_idle_detection(metrics, state, expected):
    r = res(ResourceType.EC2_INSTANCE, state=state, metrics=metrics, instance_type="m5.large")
    assert RULES_BY_ID["ec2-idle"].detect(r, ctx()) is expected


def test_ec2_idle_savings_stop_vs_downsize():
    rule = RULES_BY_ID["ec2-idle"]
    very_idle = res(
        ResourceType.EC2_INSTANCE, state="running", metrics=cpu(1, 5), instance_type="m5.xlarge"
    )
    assert rule.estimated_monthly_savings(very_idle, ctx()) == very_idle.est_monthly_cost_usd
    assert "Stop" in rule.explain(very_idle, ctx())[1]

    low = res(
        ResourceType.EC2_INSTANCE, state="running", metrics=cpu(3.5, 12), instance_type="m5.xlarge"
    )
    # m5.xlarge (0.202) -> m5.large (0.101): saves 0.101 * 730
    assert rule.estimated_monthly_savings(low, ctx()) == Decimal("73.73")
    assert "m5.large" in rule.explain(low, ctx())[1]


def test_thresholds_are_configurable_per_account():
    r = res(
        ResourceType.EC2_INSTANCE, state="running", metrics=cpu(8, 15), instance_type="t3.large"
    )
    rule = RULES_BY_ID["ec2-idle"]
    assert not rule.detect(r, ctx())
    assert rule.detect(r, ctx(thresholds={"ec2-idle": {"cpu_avg_pct": 10}}))


@pytest.mark.parametrize(
    ("itype", "smaller"),
    [("m5.xlarge", "m5.large"), ("t3.medium", "t3.small"), ("t3.nano", None), ("weird", None)],
)
def test_smaller_instance_type(itype, smaller):
    assert smaller_instance_type(itype) == smaller


# ---------------------------------------------------------------- storage & network rules


def test_unattached_volume():
    rule = RULES_BY_ID["ebs-unattached"]
    vol = res(
        ResourceType.EBS_VOLUME, state="available", created_days=30, volume_type="gp3", size_gb=100
    )
    assert rule.detect(vol, ctx())
    assert rule.estimated_monthly_savings(vol, ctx()) == Decimal("9.12")
    fresh = res(ResourceType.EBS_VOLUME, state="available", created_days=0, size_gb=10)
    assert not rule.detect(fresh, ctx())
    attached = res(ResourceType.EBS_VOLUME, state="in-use", created_days=30, size_gb=10)
    assert not rule.detect(attached, ctx())


def test_unassociated_eip():
    rule = RULES_BY_ID["eip-unassociated"]
    assert rule.detect(res(ResourceType.ELASTIC_IP, state="unassociated"), ctx())
    assert not rule.detect(res(ResourceType.ELASTIC_IP, state="associated"), ctx())
    eip = res(ResourceType.ELASTIC_IP, state="unassociated", public_ip="1.2.3.4")
    assert rule.estimated_monthly_savings(eip, ctx()) == Decimal("3.65")


def test_old_snapshot_and_ami_hint():
    rule = RULES_BY_ID["snapshot-old"]
    old = res(ResourceType.EBS_SNAPSHOT, created_days=120, size_gb=100, ami_id="ami-123")
    assert rule.detect(old, ctx())
    assert "ami-123" in rule.explain(old, ctx())[1]
    assert not rule.detect(res(ResourceType.EBS_SNAPSHOT, created_days=30, size_gb=1), ctx())
    assert not rule.detect(res(ResourceType.EBS_SNAPSHOT, size_gb=1), ctx())  # unknown age


def test_stopped_instance_paying_for_ebs():
    rule = RULES_BY_ID["ec2-stopped-ebs"]
    inst = res(
        ResourceType.EC2_INSTANCE,
        "i-1",
        state="stopped",
        instance_type="m5.large",
        stopped_at=(NOW - timedelta(days=30)).isoformat(),
        volume_ids=["vol-1"],
    )
    vol = res(ResourceType.EBS_VOLUME, "vol-1", state="in-use", volume_type="gp2", size_gb=100)
    c = ctx([inst, vol])
    assert rule.detect(inst, c)
    assert rule.estimated_monthly_savings(inst, c) == Decimal("11.40")
    assert rule.current_cost(inst, c) == Decimal("11.40")

    recent = inst.model_copy(
        update={"attributes": inst.attributes | {"stopped_at": NOW.isoformat()}}
    )
    assert not rule.detect(recent, c)
    assert not rule.detect(inst, ctx([inst]))  # no volumes -> nothing to save


def test_gp2_to_gp3():
    rule = RULES_BY_ID["ebs-gp2-to-gp3"]
    vol = res(ResourceType.EBS_VOLUME, state="in-use", volume_type="gp2", size_gb=500)
    assert rule.detect(vol, ctx())
    # (0.114 - 0.0912) * 500
    assert rule.estimated_monthly_savings(vol, ctx()) == Decimal("11.40")
    assert not rule.detect(res(ResourceType.EBS_VOLUME, volume_type="gp3", size_gb=500), ctx())


def test_s3_without_lifecycle():
    rule = RULES_BY_ID["s3-no-lifecycle"]
    bucket = res(ResourceType.S3_BUCKET, size_gb=1000, has_lifecycle_policy=False)
    assert rule.detect(bucket, ctx())
    # 1000 GB * 40% * (0.025 - 0.019)
    assert rule.estimated_monthly_savings(bucket, ctx()) == Decimal("2.40")
    assert not rule.detect(
        res(ResourceType.S3_BUCKET, size_gb=0.1, has_lifecycle_policy=False), ctx()
    )
    assert not rule.detect(
        res(ResourceType.S3_BUCKET, size_gb=1000, has_lifecycle_policy=True), ctx()
    )
    custom = ctx(thresholds={"s3-no-lifecycle": {"ia_share": 0.8}})
    assert rule.estimated_monthly_savings(bucket, custom) == Decimal("4.80")


def test_idle_rds():
    rule = RULES_BY_ID["rds-idle"]
    idle = res(
        ResourceType.RDS_INSTANCE,
        state="available",
        metrics={"db_connections_max_7d": 0},
        instance_class="db.t3.medium",
        storage_gb=50,
    )
    assert rule.detect(idle, ctx())
    assert rule.estimated_monthly_savings(idle, ctx()) == idle.est_monthly_cost_usd > 0
    busy = idle.model_copy(update={"metrics": {"db_connections_max_7d": 12}})
    assert not rule.detect(busy, ctx())
    assert not rule.detect(idle.model_copy(update={"metrics": {}}), ctx())  # no data


# ---------------------------------------------------------------- engine


def test_no_double_counting_for_volumes_being_deleted():
    unattached_gp2 = res(
        ResourceType.EBS_VOLUME,
        "vol-a",
        state="available",
        created_days=30,
        volume_type="gp2",
        size_gb=100,
    )
    findings = run_rules("a", [unattached_gp2], AccountSettings(account_id="a"), NOW)
    assert [f.rule_id for f in findings] == ["ebs-unattached"]


def test_demo_data_triggers_every_rule_and_spares_edge_cases():
    ds = generate_demo_dataset(today=NOW.date(), now=NOW)
    findings = run_rules("demo", ds.resources, ds.settings, NOW)
    assert {f.rule_id for f in findings} == set(RULES_BY_ID)
    flagged_names = {f.resource_name for f in findings}
    assert "bastion" not in flagged_names  # low average but bursts to 35%
    assert "dev-sandbox" not in flagged_names  # stopped only 3 days
    assert all(f.est_monthly_savings_usd > 0 for f in findings)
    assert 300 < total_savings(findings) < 900


def test_sync_findings_keeps_history_and_dismissals():
    repos = create_memory_repositories()
    ds = generate_demo_dataset(today=NOW.date(), now=NOW)
    load_dataset(repos, ds)  # runs the rules once
    first = {f.id: f for f in repos.findings.list("demo")}
    some_id = next(iter(first))
    repos.findings.update_status("demo", some_id, FindingStatus.DISMISSED)

    # Remove one flagged resource (as if it was cleaned up in AWS) and re-run later.
    eip_id = next(f.resource_id for f in first.values() if f.rule_id == "eip-unassociated")
    repos.resources.replace_inventory(
        "demo", [r for r in repos.resources.list("demo") if r.resource_id != eip_id]
    )
    later = NOW + timedelta(days=1)
    refresh_findings(repos, "demo", later)
    after = {f.id: f for f in repos.findings.list("demo")}

    assert after[some_id].status == FindingStatus.DISMISSED  # user choice survives re-runs
    assert after[f"eip-unassociated#{eip_id}"].status == FindingStatus.RESOLVED
    kept = next(i for i in after if i not in (some_id, f"eip-unassociated#{eip_id}"))
    assert after[kept].first_seen == first[kept].first_seen
    assert after[kept].status == FindingStatus.OPEN


def test_resolved_finding_reopens_when_detected_again():
    repos = create_memory_repositories()
    vol = res(ResourceType.EBS_VOLUME, "vol-x", state="available", created_days=10, size_gb=10)
    settings = AccountSettings(account_id="a")
    sync_findings(repos, "a", run_rules("a", [vol], settings, NOW), NOW)
    sync_findings(repos, "a", [], NOW)
    assert repos.findings.list("a")[0].status == FindingStatus.RESOLVED
    sync_findings(repos, "a", run_rules("a", [vol], settings, NOW), NOW)
    assert repos.findings.list("a")[0].status == FindingStatus.OPEN
