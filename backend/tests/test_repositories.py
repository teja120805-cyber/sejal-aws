"""Repository contract tests: every test runs against BOTH the in-memory and DynamoDB (moto)
implementations via the parametrised `repos` fixture."""

import datetime as dt
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from finops.domain import (
    Account,
    AccountSettings,
    Alert,
    AlertType,
    Budget,
    CollectionRun,
    CostForecast,
    DailyCost,
    Finding,
    FindingStatus,
    Resource,
    ResourceType,
    RunTrigger,
    Severity,
    TagCost,
)
from finops.repositories import NotFoundError, Repositories

NOW = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)
ARN = "arn:aws:iam::123456789012:role/FinOpsReadOnly"


def account(id: str = "acc1", name: str = "Prod") -> Account:
    return Account(id=id, name=name, role_arn=ARN, created_at=NOW)


def cost(day: str, service: str = "AWS Lambda", region: str = "ap-south-1", amount="1.25"):
    return DailyCost(
        account_id="acc1", date=day, service=service, region=region, amount_usd=Decimal(amount)
    )


def resource(rid: str, type=ResourceType.EBS_VOLUME, **kw) -> Resource:
    return Resource(account_id="acc1", resource_id=rid, type=type, region="ap-south-1", **kw)


def finding(rid: str, rule: str = "ebs-unattached") -> Finding:
    return Finding(
        account_id="acc1",
        rule_id=rule,
        resource_id=rid,
        resource_type=ResourceType.EBS_VOLUME,
        region="ap-south-1",
        severity=Severity.MEDIUM,
        title="Unattached volume",
        detail="d",
        recommendation="Delete it",
        est_monthly_savings_usd=Decimal("11.40"),
        first_seen=NOW,
        last_seen=NOW,
    )


# ---------------------------------------------------------------- accounts


def test_account_put_get_list(repos: Repositories):
    assert repos.accounts.get("acc1") is None
    zeta = account("acc1", "Zeta")
    repos.accounts.put(zeta)
    repos.accounts.put(account("acc2", "Alpha"))
    assert repos.accounts.get("acc1") == zeta
    assert [a.name for a in repos.accounts.list()] == ["Alpha", "Zeta"]


def test_account_delete_cascades(repos: Repositories):
    repos.accounts.put(account())
    repos.costs.put_daily_costs([cost("2026-10-01")])
    repos.resources.replace_inventory("acc1", [resource("vol-1")])
    repos.budgets.put(Budget(account_id="acc1", monthly_limit_usd=100))
    repos.accounts.delete("acc1")
    assert repos.accounts.get("acc1") is None
    assert repos.costs.query_daily_costs("acc1", dt.date(2026, 1, 1), dt.date(2026, 12, 31)) == []
    assert repos.resources.list("acc1") == []
    assert repos.budgets.get("acc1") is None


def test_refresh_cooldown_is_enforced(repos: Repositories):
    repos.accounts.put(account())
    hour = timedelta(hours=1)
    assert repos.accounts.try_start_refresh("acc1", NOW, hour) is True
    assert repos.accounts.try_start_refresh("acc1", NOW + timedelta(minutes=59), hour) is False
    assert repos.accounts.try_start_refresh("acc1", NOW + timedelta(minutes=61), hour) is True
    assert repos.accounts.get("acc1").last_refresh_at == NOW + timedelta(minutes=61)


def test_refresh_unknown_account_raises(repos: Repositories):
    with pytest.raises(NotFoundError):
        repos.accounts.try_start_refresh("missing", NOW, timedelta(hours=1))


# ---------------------------------------------------------------- costs


def test_daily_costs_range_query_is_inclusive_and_sorted(repos: Repositories):
    repos.costs.put_daily_costs(
        [
            cost("2026-09-30"),
            cost("2026-10-02", "Amazon S3"),
            cost("2026-10-01", "AWS Lambda", "us-east-1"),
            cost("2026-10-01", "AWS Lambda", "ap-south-1", "3.3333"),
            cost("2026-10-03"),
        ]
    )
    rows = repos.costs.query_daily_costs("acc1", dt.date(2026, 10, 1), dt.date(2026, 10, 2))
    assert [(str(r.date), r.region) for r in rows] == [
        ("2026-10-01", "ap-south-1"),
        ("2026-10-01", "us-east-1"),
        ("2026-10-02", "ap-south-1"),
    ]
    assert rows[0].amount_usd == Decimal("3.3333")


def test_daily_cost_put_is_idempotent(repos: Repositories):
    repos.costs.put_daily_costs([cost("2026-10-01", amount="1")])
    repos.costs.put_daily_costs([cost("2026-10-01", amount="2")])  # Cost Explorer restated it
    rows = repos.costs.query_daily_costs("acc1", dt.date(2026, 10, 1), dt.date(2026, 10, 1))
    assert len(rows) == 1 and rows[0].amount_usd == Decimal("2")


def test_tag_costs_filtered_by_key_and_range(repos: Repositories):
    def tc(key, day, value, amount):
        return TagCost(account_id="acc1", date=day, tag_key=key, tag_value=value, amount_usd=amount)

    repos.costs.put_tag_costs(
        [
            tc("Project", "2026-10-01", "web", 5),
            tc("Project", "2026-10-01", "", 2),
            tc("Project", "2026-10-05", "web", 9),
            tc("Owner", "2026-10-01", "team-a", 7),
        ]
    )
    rows = repos.costs.query_tag_costs(
        "acc1", "Project", dt.date(2026, 10, 1), dt.date(2026, 10, 2)
    )
    assert sorted((r.tag_value, r.amount_usd) for r in rows) == [("", 2), ("web", 5)]


def test_forecast_round_trip(repos: Repositories):
    assert repos.costs.get_forecast("acc1", "2026-10") is None
    f = CostForecast(account_id="acc1", month="2026-10", forecast_usd=2500, generated_at=NOW)
    repos.costs.put_forecast(f)
    assert repos.costs.get_forecast("acc1", "2026-10") == f


# ---------------------------------------------------------------- resources


def test_resource_round_trip_preserves_nested_attributes(repos: Repositories):
    r = resource(
        "i-1",
        ResourceType.EC2_INSTANCE,
        name="web",
        state="running",
        tags={"Project": "web"},
        created_at=NOW,
        attributes={"instance_type": "m5.large", "volume_ids": ["vol-1"], "x": None, "ok": True},
        metrics={"cpu_avg_7d": 3.25, "cpu_max_7d": 12.0},
        est_monthly_cost_usd=Decimal("73.73"),
        last_seen_at=NOW,
    )
    repos.resources.replace_inventory("acc1", [r])
    assert repos.resources.list("acc1") == [r]


def test_replace_inventory_removes_deleted_resources(repos: Repositories):
    repos.resources.replace_inventory("acc1", [resource("vol-1"), resource("vol-2")])
    repos.resources.replace_inventory(
        "acc1", [resource("vol-2"), resource("i-9", ResourceType.EC2_INSTANCE)]
    )
    assert sorted(r.resource_id for r in repos.resources.list("acc1")) == ["i-9", "vol-2"]
    ebs = repos.resources.list("acc1", type=ResourceType.EBS_VOLUME)
    assert [r.resource_id for r in ebs] == ["vol-2"]


# ---------------------------------------------------------------- findings


def test_findings_put_list_and_status(repos: Repositories):
    repos.findings.put_many([finding("vol-1"), finding("vol-2")])
    repos.findings.put_many([finding("vol-1")])  # re-detected: same id, no duplicate
    assert len(repos.findings.list("acc1")) == 2

    updated = repos.findings.update_status("acc1", "ebs-unattached#vol-1", FindingStatus.DISMISSED)
    assert updated.status == FindingStatus.DISMISSED
    assert repos.findings.get("acc1", "ebs-unattached#vol-1").status == FindingStatus.DISMISSED
    open_ids = [f.id for f in repos.findings.list("acc1", status=FindingStatus.OPEN)]
    assert open_ids == ["ebs-unattached#vol-2"]


def test_update_unknown_finding_raises(repos: Repositories):
    assert repos.findings.get("acc1", "nope") is None
    with pytest.raises(NotFoundError):
        repos.findings.update_status("acc1", "nope", FindingStatus.RESOLVED)


# ---------------------------------------------------------------- budgets & settings


def test_budget_round_trip(repos: Repositories):
    assert repos.budgets.get("acc1") is None
    b = Budget(account_id="acc1", monthly_limit_usd=Decimal("2600"), thresholds=[50, 80, 100])
    repos.budgets.put(b)
    assert repos.budgets.get("acc1") == b


def test_settings_default_then_saved(repos: Repositories):
    assert repos.settings.get("acc1") == AccountSettings(account_id="acc1")
    s = AccountSettings(
        account_id="acc1",
        required_tags=["Team"],
        rule_thresholds={"ec2-idle": {"cpu_avg_pct": 3.5}},
        alert_emails=["ops@example.com"],
        slack_enabled=True,
        slack_webhook_ssm_param="/finops/slack",
    )
    repos.settings.put(s)
    assert repos.settings.get("acc1") == s


# ---------------------------------------------------------------- alerts & runs


def test_alerts_newest_first_with_limit(repos: Repositories):
    for i in range(3):
        repos.alerts.put(
            Alert(
                account_id="acc1",
                type=AlertType.ANOMALY,
                severity=Severity.HIGH,
                title=f"a{i}",
                message="m",
                dedup_key=f"k{i}",
                created_at=NOW + timedelta(minutes=i),
            )
        )
    assert [a.title for a in repos.alerts.list("acc1")] == ["a2", "a1", "a0"]
    assert [a.title for a in repos.alerts.list("acc1", limit=2)] == ["a2", "a1"]


def test_dedup_claim_only_succeeds_once(repos: Repositories):
    key = "budget#2026-10#80#forecast"
    assert repos.alerts.claim_dedup("acc1", key, expires_at=NOW + timedelta(days=60)) is True
    assert repos.alerts.claim_dedup("acc1", key) is False
    assert repos.alerts.claim_dedup("acc1", "budget#2026-10#100#forecast") is True
    assert repos.alerts.claim_dedup("acc2", key) is True  # scoped per account


def test_runs_newest_first(repos: Repositories):
    for i in range(3):
        repos.runs.put(
            CollectionRun(
                account_id="acc1", trigger=RunTrigger.SCHEDULE, started_at=NOW + timedelta(hours=i)
            )
        )
    runs = repos.runs.list("acc1", limit=2)
    assert [r.started_at for r in runs] == [NOW + timedelta(hours=2), NOW + timedelta(hours=1)]


def test_run_update_overwrites_same_item(repos: Repositories):
    run = CollectionRun(account_id="acc1", trigger=RunTrigger.MANUAL, started_at=NOW)
    repos.runs.put(run)
    run.cost_explorer_calls = 4
    run.finished_at = NOW + timedelta(minutes=2)
    repos.runs.put(run)
    assert repos.runs.list("acc1") == [run]
