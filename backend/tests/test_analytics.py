import datetime as dt
from datetime import timedelta
from decimal import Decimal

import pytest

from finops.analytics import costs as cost_analytics
from finops.analytics.anomaly import TOTAL, detect_anomalies, detect_cost_anomalies
from finops.analytics.budgets import evaluate_budget
from finops.analytics.forecast import linear_forecast
from finops.analytics.tags import tag_compliance
from finops.demo import generate_demo_dataset
from finops.domain import Budget, DailyCost, Resource, ResourceType, TagCost

TODAY = dt.date(2026, 10, 11)


def series(values, end=TODAY - timedelta(days=1)):
    start = end - timedelta(days=len(values) - 1)
    return {start + timedelta(days=i): v for i, v in enumerate(values)}


# ---------------------------------------------------------------- forecast


def test_forecast_flat_run_rate():
    totals = {d: Decimal(100) for d in series([0] * 30)}
    f = linear_forecast(totals, TODAY)
    # 10 days of October actual (1st-10th) + 21 remaining days (11th-31st) at 100/day
    assert f.month == "2026-10"
    assert f.month_to_date == Decimal(1000)
    assert f.remaining_days == 21
    assert f.forecast_total == Decimal(3100)
    assert f.slope_per_day == pytest.approx(0, abs=1e-6)


def test_forecast_follows_a_trend():
    totals = {d: Decimal(v) for d, v in series([10 + i for i in range(30)]).items()}
    f = linear_forecast(totals, TODAY)
    assert f.slope_per_day == pytest.approx(1.0)
    assert f.r_squared == pytest.approx(1.0)
    # next predicted day continues the line: last actual 39 -> today 40
    assert f.predictions[0] == (TODAY, Decimal(40))


def test_forecast_ignores_excluded_spike_but_counts_it_in_actuals():
    totals = {d: Decimal(100) for d in series([0] * 30)}
    spike = TODAY - timedelta(days=5)
    totals[spike] = Decimal(1000)
    with_spike = linear_forecast(totals, TODAY)
    without = linear_forecast(totals, TODAY, exclude={spike})
    assert without.forecast_total == Decimal(4000)  # 1900 actual + 21 * 100
    assert with_spike.forecast_total > without.forecast_total


def test_forecast_never_predicts_negative_days():
    totals = {d: Decimal(v) for d, v in series([300 - 10 * i for i in range(30)]).items()}
    f = linear_forecast(totals, TODAY)
    assert all(p >= 0 for _, p in f.predictions)


def test_forecast_with_no_or_little_history():
    empty = linear_forecast({}, TODAY)
    assert empty.forecast_total == 0 and empty.days_used == 0
    one = linear_forecast({TODAY - timedelta(days=1): Decimal(50)}, TODAY)
    assert one.forecast_total == Decimal(50 + 21 * 50)


# ---------------------------------------------------------------- anomaly


def test_spike_is_detected_and_normal_noise_is_not():
    values = [100 + (i % 7) for i in range(40)]
    values[35] = 300
    found = detect_anomalies(series(values))
    assert [a.actual for a in found] == [300]
    a = found[0]
    assert a.excess == pytest.approx(a.actual - a.mean)
    assert a.threshold == pytest.approx(a.mean + 3 * a.std, abs=1e-3)


def test_needs_enough_history():
    values = [10] * 10 + [100]
    assert detect_anomalies(series(values)) == []


def test_tiny_or_small_relative_increases_are_ignored():
    flat = [10.0] * 30 + [10.5]  # +5%: above 3 sigma of a perfectly flat series, but trivial
    assert detect_anomalies(series(flat)) == []
    cents = [0.01] * 30 + [0.5]  # huge relative jump, but under $1
    assert detect_anomalies(series(cents)) == []


def test_demo_data_flags_only_the_spike_day():
    ds = generate_demo_dataset(today=TODAY)
    found = detect_cost_anomalies(ds.daily_costs)
    spike = dt.date.fromisoformat(ds.summary["spike_day"])
    assert {a.date for a in found} == {spike}
    assert any(a.scope == TOTAL for a in found)
    assert any("Compute" in a.scope for a in found)
    assert found[0].scope == TOTAL  # total listed first for the same day


# ---------------------------------------------------------------- budgets


def test_budget_thresholds_actual_vs_forecast():
    b = Budget(account_id="a", monthly_limit_usd=1000, thresholds=[50, 80, 100])
    s = evaluate_budget(b, "2026-10", actual=Decimal(600), forecast=Decimal(1100))
    assert (s.actual_pct, s.forecast_pct) == (60.0, 110.0)
    assert [(c.threshold, c.kind) for c in s.crossed] == [
        (50, "actual"),
        (80, "forecast"),
        (100, "forecast"),
    ]
    calm = evaluate_budget(b, "2026-10", actual=Decimal(100), forecast=Decimal(400))
    assert calm.crossed == []


# ---------------------------------------------------------------- cost aggregations


def cost(day, service, region, amount):
    return DailyCost(account_id="a", date=day, service=service, region=region, amount_usd=amount)


def test_breakdowns_series_and_month_maths():
    d1, d2 = dt.date(2026, 10, 1), dt.date(2026, 10, 2)
    rows = [
        cost(d1, "EC2", "ap-south-1", 60),
        cost(d1, "S3", "us-east-1", 20),
        cost(d2, "EC2", "ap-south-1", 15),
        cost(d2, "Lambda", "ap-south-1", 5),
    ]
    by_service = cost_analytics.breakdown(rows, "service")
    assert by_service[0] == {"key": "EC2", "amount_usd": 75.0, "share_pct": 75.0}
    top1 = cost_analytics.breakdown(rows, "service", limit=1)
    assert [r["key"] for r in top1] == ["EC2", "Other"] and top1[1]["amount_usd"] == 25.0
    by_region = cost_analytics.breakdown(rows, "region")
    assert by_region[0]["key"] == "ap-south-1"

    s = cost_analytics.daily_series(rows, d1, dt.date(2026, 10, 3), top_services=2)
    assert s["services"] == ["EC2", "S3", "Other"]
    assert s["days"][0] == {
        "date": "2026-10-01",
        "EC2": 60.0,
        "S3": 20.0,
        "Other": 0.0,
        "total": 80.0,
    }
    assert s["days"][2]["total"] == 0.0  # zero-filled day

    assert cost_analytics.previous_month(dt.date(2026, 3, 15)) == (
        dt.date(2026, 2, 1),
        dt.date(2026, 2, 28),
    )
    assert cost_analytics.pct_change(Decimal(110), Decimal(100)) == 10.0
    assert cost_analytics.pct_change(Decimal(1), Decimal(0)) is None
    assert cost_analytics.total(rows, d2, d2) == 20


def test_tag_breakdown_labels_untagged():
    rows = [
        TagCost(account_id="a", date=TODAY, tag_key="Project", tag_value="web", amount_usd=3),
        TagCost(account_id="a", date=TODAY, tag_key="Project", tag_value="", amount_usd=1),
    ]
    assert [r["key"] for r in cost_analytics.tag_breakdown(rows)] == ["web", "(untagged)"]


# ---------------------------------------------------------------- tags


def test_tag_compliance():
    def r(rid, tags, cost):
        return Resource(
            account_id="a",
            resource_id=rid,
            type=ResourceType.EC2_INSTANCE,
            region="ap-south-1",
            tags=tags,
            est_monthly_cost_usd=cost,
        )

    full = {"Project": "x", "Owner": "y", "Environment": "prod"}
    report = tag_compliance(
        [
            r("ok", full, 10),
            r("no-owner", {"Project": "x", "Environment": "prod", "Owner": " "}, 50),
            r("bare", {}, 5),
        ],
        ["Project", "Owner", "Environment"],
    )
    assert report.compliant_resources == 1 and report.total_resources == 3
    assert report.compliance_pct == pytest.approx(33.3)
    assert report.missing_by_tag == {"Project": 1, "Owner": 2, "Environment": 1}
    assert [n["resource_id"] for n in report.noncompliant] == ["no-owner", "bare"]  # by cost
    assert report.untagged_monthly_cost_usd == 55
    assert tag_compliance([], ["Project"]).compliance_pct == 100.0
