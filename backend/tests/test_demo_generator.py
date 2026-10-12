import datetime as dt
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import numpy as np
import pytest

from finops.demo import DEMO_ACCOUNT_ID, generate_demo_dataset, load_dataset
from finops.domain import ResourceType
from finops.repositories.dynamodb import create_dynamodb_repositories
from finops.repositories.memory import create_memory_repositories

NOW = datetime(2026, 10, 11, 9, 0, tzinfo=UTC)
TODAY = NOW.date()


@pytest.fixture(scope="module")
def ds():
    return generate_demo_dataset(today=TODAY, now=NOW)


def daily_totals(ds) -> dict[dt.date, float]:
    totals: dict[dt.date, float] = defaultdict(float)
    for c in ds.daily_costs:
        totals[c.date] += float(c.amount_usd)
    return dict(sorted(totals.items()))


def test_is_deterministic(ds):
    again = generate_demo_dataset(today=TODAY, now=NOW)
    assert again.daily_costs == ds.daily_costs
    assert again.resources == ds.resources


def test_90_days_ending_yesterday_across_8_services(ds):
    days = sorted({c.date for c in ds.daily_costs})
    assert len(days) == 90
    assert days[-1] == TODAY - timedelta(days=1)
    assert len({c.service for c in ds.daily_costs}) == 8
    assert {c.region for c in ds.daily_costs} == {"ap-south-1", "us-east-1", "global"}


def test_weekly_pattern_weekends_are_cheaper(ds):
    totals = daily_totals(ds)
    weekday = [v for d, v in totals.items() if d.weekday() < 5]
    weekend = [v for d, v in totals.items() if d.weekday() >= 5]
    assert np.mean(weekend) < 0.9 * np.mean(weekday)


def test_exactly_one_spike_above_mean_plus_3_sigma(ds):
    totals = list(daily_totals(ds).items())
    flagged = []
    for i in range(30, len(totals)):
        window = np.array([v for _, v in totals[i - 30 : i]])
        if totals[i][1] > window.mean() + 3 * window.std():
            flagged.append(totals[i][0])
    assert flagged == [dt.date.fromisoformat(ds.summary["spike_day"])]


def test_monthly_spend_is_realistic_and_forecast_near_budget(ds):
    monthly = sum(daily_totals(ds).values()) / 3
    assert 1800 < monthly < 3000
    ratio = ds.forecast.forecast_usd / ds.budget.monthly_limit_usd
    assert Decimal("0.8") < ratio < Decimal("1.0")  # crosses the 80% alert, not 100%
    assert ds.forecast.month == "2026-10"


def test_tag_costs_add_up_to_daily_totals_and_include_untagged(ds):
    totals = daily_totals(ds)
    by_key_day: dict[tuple[str, dt.date], float] = defaultdict(float)
    for t in ds.tag_costs:
        by_key_day[(t.tag_key, t.date)] += float(t.amount_usd)
    for (_, day), value in by_key_day.items():
        assert value == pytest.approx(totals[day], abs=0.01)
    assert any(t.tag_value == "" for t in ds.tag_costs)


def test_about_40_resources_of_every_type(ds):
    assert 35 <= len(ds.resources) <= 50
    assert {r.type for r in ds.resources} == set(ResourceType)
    assert len({r.key for r in ds.resources}) == len(ds.resources)
    assert all(r.est_monthly_cost_usd >= 0 for r in ds.resources)


def test_contains_waste_for_every_rule(ds):
    by_type = defaultdict(list)
    for r in ds.resources:
        by_type[r.type].append(r)
    ec2 = by_type[ResourceType.EC2_INSTANCE]

    idle = [
        r
        for r in ec2
        if r.state == "running" and r.metrics["cpu_avg_7d"] < 5 and r.metrics["cpu_max_7d"] < 20
    ]
    assert len(idle) >= 2
    long_stopped = [
        r
        for r in ec2
        if r.state == "stopped"
        and NOW - datetime.fromisoformat(r.attributes["stopped_at"]) > timedelta(days=7)
    ]
    assert len(long_stopped) >= 2
    assert any(r.state == "available" for r in by_type[ResourceType.EBS_VOLUME])
    assert any(r.attributes["volume_type"] == "gp2" for r in by_type[ResourceType.EBS_VOLUME])
    assert any(r.state == "unassociated" for r in by_type[ResourceType.ELASTIC_IP])
    assert any(NOW - r.created_at > timedelta(days=90) for r in by_type[ResourceType.EBS_SNAPSHOT])
    assert any(not r.attributes["has_lifecycle_policy"] for r in by_type[ResourceType.S3_BUCKET])
    assert any(r.metrics["db_connections_max_7d"] == 0 for r in by_type[ResourceType.RDS_INSTANCE])
    required = {"Project", "Owner", "Environment"}
    assert any(not required <= r.tags.keys() for r in ds.resources)  # tag non-compliance


def test_attached_volumes_reference_real_instances(ds):
    instance_ids = {r.resource_id for r in ds.resources if r.type == ResourceType.EC2_INSTANCE}
    for r in ds.resources:
        if r.type == ResourceType.EBS_VOLUME and r.state == "in-use":
            assert r.attributes["attached_instance_id"] in instance_ids


@pytest.mark.parametrize("backend", ["memory", "dynamodb"])
def test_load_dataset_into_repositories(ds, backend, request):
    if backend == "memory":
        repos = create_memory_repositories()
    else:
        repos = create_dynamodb_repositories(request.getfixturevalue("ddb_table"))
    load_dataset(repos, ds)
    assert repos.accounts.get(DEMO_ACCOUNT_ID).status == "connected"
    first, last = ds.daily_costs[0].date, ds.daily_costs[-1].date
    assert len(repos.costs.query_daily_costs(DEMO_ACCOUNT_ID, first, last)) == len(ds.daily_costs)
    assert sorted(repos.resources.list(DEMO_ACCOUNT_ID), key=lambda r: r.key) == sorted(
        ds.resources, key=lambda r: r.key
    )
    assert repos.budgets.get(DEMO_ACCOUNT_ID) == ds.budget
