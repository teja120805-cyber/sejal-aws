"""End-to-end collection: moto-backed AWS account + stubbed Cost Explorer -> repositories."""

import datetime as dt
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from botocore.exceptions import ClientError
from botocore.stub import ANY, Stubber
from moto import mock_aws
from tests.aws_fixtures import REGION, build_world, make_account

from finops.aws.inventory import InventoryScanner
from finops.aws.session import AccountSession
from finops.collection import (
    CollectionOptions,
    RefreshTooSoonError,
    collect_account,
    lambda_invoker,
    refresh_account,
    start_async_refresh,
)
from finops.config import Settings
from finops.domain import (
    AccountStatus,
    CollectionRun,
    Resource,
    ResourceType,
    RunStatus,
    RunTrigger,
    utcnow,
)
from finops.repositories import NotFoundError
from finops.repositories.memory import create_memory_repositories

REGIONS = [REGION, "us-east-1"]


@pytest.fixture
def env(monkeypatch):
    """Mocked AWS account with resources, an assumed-role session, and empty repositories."""
    monkeypatch.setattr(AccountSession, "enabled_regions", lambda self, home="": list(REGIONS))
    with mock_aws():
        world = build_world()
        repos = create_memory_repositories()
        account = make_account()
        repos.accounts.put(account)
        session = AccountSession.assume(account)
        ce = session.client("ce", "us-east-1")
        with Stubber(ce) as stub:
            yield world, repos, account, session, stub


def ce_period(day: dt.date, amount: str = "10"):
    return {
        "TimePeriod": {"Start": day.isoformat(), "End": (day + timedelta(days=1)).isoformat()},
        "Groups": [
            {
                "Keys": ["Amazon Elastic Compute Cloud - Compute", REGION],
                "Metrics": {"UnblendedCost": {"Amount": amount, "Unit": "USD"}},
            }
        ],
        "Estimated": False,
    }


def stub_cost_explorer(stub: Stubber, today: dt.date, days: int, forecast_remaining="500"):
    end = today - timedelta(days=1)
    start = end - timedelta(days=days - 1)
    stub.add_response(
        "get_cost_and_usage",
        {"ResultsByTime": [ce_period(start), ce_period(end)]},
        expected_params={
            "TimePeriod": {"Start": start.isoformat(), "End": today.isoformat()},
            "Granularity": "DAILY",
            "Metrics": ["UnblendedCost"],
            "GroupBy": ANY,
        },
    )
    for key in ("Project", "Owner", "Environment"):
        stub.add_response(
            "get_cost_and_usage",
            {"ResultsByTime": []},
            expected_params={
                "TimePeriod": ANY,
                "Granularity": "DAILY",
                "Metrics": ["UnblendedCost"],
                "GroupBy": [{"Type": "TAG", "Key": key}],
            },
        )
    stub.add_response("get_cost_forecast", {"Total": {"Amount": forecast_remaining, "Unit": "USD"}})


def test_first_run_backfills_90_days_and_stores_everything(env):
    world, repos, account, session, stub = env
    now = utcnow()
    today = now.date()
    stub_cost_explorer(stub, today, days=90)

    run = collect_account(account, repos, session, trigger=RunTrigger.SCHEDULE, now=now)

    assert run.status == RunStatus.SUCCEEDED, run.errors
    assert run.cost_explorer_calls == 5  # 1 daily + 3 tag keys + 1 forecast
    stub.assert_no_pending_responses()

    resources = repos.resources.list(account.id)
    assert run.resources_collected == len(resources) > 8
    ids = {r.resource_id for r in resources}
    assert {world.running_instance, world.unattached_volume, world.free_eip} <= ids
    running = next(r for r in resources if r.resource_id == world.running_instance)
    assert running.est_monthly_cost_usd == Decimal("73.73")  # m5.large in Mumbai
    assert "cpu_datapoints_7d" in running.metrics

    costs = repos.costs.query_daily_costs(account.id, today - timedelta(days=100), today)
    assert len(costs) == 2

    month_start = today.replace(day=1)
    mtd = sum((c.amount_usd for c in costs if c.date >= month_start), Decimal(0))
    forecast = repos.costs.get_forecast(account.id, month_start.strftime("%Y-%m"))
    assert forecast.forecast_usd == mtd + 500

    saved = repos.accounts.get(account.id)
    assert saved.regions == REGIONS
    assert saved.last_collected_at == run.finished_at
    assert repos.runs.list(account.id)[0].status == RunStatus.SUCCEEDED


def test_later_runs_only_reread_the_last_7_days(env):
    _, repos, account, session, stub = env
    now = utcnow()
    today = now.date()
    stub_cost_explorer(stub, today, days=90)
    collect_account(account, repos, session, trigger=RunTrigger.SCHEDULE, now=now)
    stub_cost_explorer(stub, today, days=7)  # expected_params enforce the 7-day window
    run = collect_account(account, repos, session, trigger=RunTrigger.SCHEDULE, now=now)
    assert run.status == RunStatus.SUCCEEDED, run.errors
    stub.assert_no_pending_responses()


def test_cost_explorer_failure_marks_run_partial_but_keeps_inventory(env):
    _, repos, account, session, stub = env
    stub.add_client_error("get_cost_and_usage", "AccessDeniedException", "ce:GetCostAndUsage")
    run = collect_account(account, repos, session, trigger=RunTrigger.SCHEDULE)
    assert run.status == RunStatus.PARTIAL
    assert run.cost_explorer_calls == 1
    assert any("cost explorer" in e for e in run.errors)
    assert repos.resources.list(account.id)
    assert repos.accounts.get(account.id).status == AccountStatus.CONNECTED


def test_failed_scan_keeps_previous_resources_of_that_type(env):
    _, repos, account, session, _ = env
    old_db = Resource(
        account_id=account.id,
        resource_id="arn:aws:rds:ap-south-1:123456789012:db:legacy",
        type=ResourceType.RDS_INSTANCE,
        region=REGION,
        name="legacy",
    )
    stale_volume = Resource(
        account_id=account.id,
        resource_id="vol-deleted",
        type=ResourceType.EBS_VOLUME,
        region=REGION,
    )
    repos.resources.replace_inventory(account.id, [old_db, stale_volume])
    denied = ClientError({"Error": {"Code": "AccessDenied", "Message": "x"}}, "DescribeDBInstances")
    with patch.object(InventoryScanner, "rds_instances", side_effect=denied):
        run = collect_account(
            account,
            repos,
            session,
            trigger=RunTrigger.SCHEDULE,
            options=CollectionOptions(collect_costs=False),
        )
    assert run.status == RunStatus.PARTIAL
    ids = {r.resource_id for r in repos.resources.list(account.id)}
    assert old_db.resource_id in ids  # RDS scan failed -> keep last known DBs
    assert "vol-deleted" not in ids  # EBS scan succeeded -> deleted volume removed


def test_unexpected_error_fails_run_and_flags_account(env, monkeypatch):
    _, repos, account, session, _ = env

    def boom(self, home=""):
        raise ClientError({"Error": {"Code": "AccessDenied", "Message": "x"}}, "DescribeRegions")

    monkeypatch.setattr(AccountSession, "enabled_regions", boom)
    run = collect_account(account, repos, session, trigger=RunTrigger.SCHEDULE)
    assert run.status == RunStatus.FAILED
    saved = repos.accounts.get(account.id)
    assert saved.status == AccountStatus.ERROR
    assert "AccessDenied" in saved.status_message
    assert saved.last_collected_at is None


# ---------------------------------------------------------------- manual refresh


@pytest.fixture
def refresh_env():
    repos = create_memory_repositories()
    repos.accounts.put(make_account())
    calls = []

    def fake_collect(account, repos, session, *, trigger, options, now):
        calls.append((account.id, trigger, options))
        return CollectionRun(account_id=account.id, trigger=trigger, status=RunStatus.SUCCEEDED)

    with patch("finops.collection.service.collect_account", side_effect=fake_collect):
        yield repos, calls


def test_refresh_runs_collection_then_enforces_cooldown(refresh_env):
    repos, calls = refresh_env
    settings = Settings(demo_mode=False, refresh_cooldown_minutes=60)
    now = utcnow()
    run = refresh_account("acc1", repos, settings, session_factory=lambda a: None, now=now)
    assert run.trigger == RunTrigger.MANUAL
    assert calls[0][1] == RunTrigger.MANUAL

    with pytest.raises(RefreshTooSoonError) as exc:
        refresh_account(
            "acc1", repos, settings, session_factory=lambda a: None, now=now + timedelta(minutes=20)
        )
    assert exc.value.retry_after == timedelta(minutes=40)
    assert len(calls) == 1  # no Cost Explorer spend on the rejected attempt

    refresh_account(
        "acc1", repos, settings, session_factory=lambda a: None, now=now + timedelta(minutes=61)
    )
    assert len(calls) == 2


def test_refresh_unknown_account(refresh_env):
    repos, _ = refresh_env
    with pytest.raises(NotFoundError):
        refresh_account("nope", repos, Settings(demo_mode=False), session_factory=lambda a: None)


def test_async_refresh_claims_slot_then_invokes_collector():
    repos = create_memory_repositories()
    repos.accounts.put(make_account())
    settings = Settings(demo_mode=False, refresh_cooldown_minutes=60)
    payloads = []
    now = utcnow()
    run = start_async_refresh("acc1", repos, settings, payloads.append, now=now)
    assert run.status == RunStatus.RUNNING and run.trigger == RunTrigger.MANUAL
    assert payloads == [{"account_id": "acc1", "trigger": "manual"}]
    with pytest.raises(RefreshTooSoonError):
        start_async_refresh("acc1", repos, settings, payloads.append, now=now)
    assert len(payloads) == 1  # rejected refresh never reaches the collector


def test_lambda_invoker_uses_async_event_invocation():
    with patch("boto3.client") as client:
        lambda_invoker("finops-collector", "ap-south-1")({"account_id": "a", "trigger": "manual"})
    kwargs = client.return_value.invoke.call_args.kwargs
    assert kwargs["FunctionName"] == "finops-collector"
    assert kwargs["InvocationType"] == "Event"
    assert '"account_id": "a"' in kwargs["Payload"]
