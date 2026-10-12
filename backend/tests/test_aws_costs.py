"""Cost Explorer reader tests using botocore Stubber (moto lacks GetCostForecast). Stubber
validates every request against the real Cost Explorer API model."""

import datetime as dt
from decimal import Decimal

import boto3
import pytest
from botocore.stub import ANY, Stubber
from tests.aws_fixtures import make_account

from finops.aws.costs import CostExplorerReader
from finops.aws.session import AccountSession


@pytest.fixture
def reader_and_stub():
    session = AccountSession(
        boto3.Session(aws_access_key_id="x", aws_secret_access_key="x", region_name="ap-south-1"),
        make_account(),
    )
    reader = CostExplorerReader(session)
    with Stubber(reader.client) as stub:
        yield reader, stub
        stub.assert_no_pending_responses()


def group(keys, amount):
    return {"Keys": keys, "Metrics": {"UnblendedCost": {"Amount": amount, "Unit": "USD"}}}


def period(day, groups):
    end = (dt.date.fromisoformat(day) + dt.timedelta(days=1)).isoformat()
    return {"TimePeriod": {"Start": day, "End": end}, "Groups": groups, "Estimated": False}


def test_client_calls_the_us_east_1_endpoint(reader_and_stub):
    reader, _ = reader_and_stub
    assert reader.client.meta.endpoint_url == "https://ce.us-east-1.amazonaws.com"


def test_daily_costs_paginates_maps_regions_and_counts_calls(reader_and_stub):
    reader, stub = reader_and_stub
    base_params = {
        "TimePeriod": {"Start": "2026-10-01", "End": "2026-10-03"},  # End is exclusive
        "Granularity": "DAILY",
        "Metrics": ["UnblendedCost"],
        "GroupBy": [
            {"Type": "DIMENSION", "Key": "SERVICE"},
            {"Type": "DIMENSION", "Key": "REGION"},
        ],
    }
    stub.add_response(
        "get_cost_and_usage",
        {
            "ResultsByTime": [
                period(
                    "2026-10-01",
                    [
                        group(["Amazon Elastic Compute Cloud - Compute", "ap-south-1"], "12.5"),
                        group(["Amazon CloudFront", "NoRegion"], "1.25"),
                        group(["AWS Lambda", "ap-south-1"], "0"),  # dropped
                    ],
                )
            ],
            "NextPageToken": "page2",
        },
        expected_params=base_params,
    )
    stub.add_response(
        "get_cost_and_usage",
        {"ResultsByTime": [period("2026-10-02", [group(["AWS Lambda", "us-east-1"], "0.33")])]},
        expected_params=base_params | {"NextPageToken": "page2"},
    )
    rows = reader.daily_costs(dt.date(2026, 10, 1), dt.date(2026, 10, 2))
    assert [(str(r.date), r.service, r.region, r.amount_usd) for r in rows] == [
        ("2026-10-01", "Amazon Elastic Compute Cloud - Compute", "ap-south-1", Decimal("12.5")),
        ("2026-10-01", "Amazon CloudFront", "global", Decimal("1.25")),
        ("2026-10-02", "AWS Lambda", "us-east-1", Decimal("0.33")),
    ]
    assert reader.calls == 2


def test_tag_costs_parses_values_and_untagged(reader_and_stub):
    reader, stub = reader_and_stub
    stub.add_response(
        "get_cost_and_usage",
        {
            "ResultsByTime": [
                period(
                    "2026-10-01",
                    [group(["Project$shop"], "10"), group(["Project$"], "2.5")],
                )
            ]
        },
        expected_params={
            "TimePeriod": {"Start": "2026-10-01", "End": "2026-10-02"},
            "Granularity": "DAILY",
            "Metrics": ["UnblendedCost"],
            "GroupBy": [{"Type": "TAG", "Key": "Project"}],
        },
    )
    rows = reader.tag_costs("Project", dt.date(2026, 10, 1), dt.date(2026, 10, 1))
    assert [(r.tag_value, r.amount_usd) for r in rows] == [("shop", 10), ("", Decimal("2.5"))]


def test_forecast_adds_month_to_date(reader_and_stub):
    reader, stub = reader_and_stub
    stub.add_response(
        "get_cost_forecast",
        {
            "Total": {"Amount": "1500.0", "Unit": "USD"},
            "ForecastResultsByTime": [
                {
                    "TimePeriod": {"Start": "2026-10-11", "End": "2026-11-01"},
                    "MeanValue": "1500.0",
                    "PredictionIntervalLowerBound": "1400",
                    "PredictionIntervalUpperBound": "1650",
                }
            ],
        },
        expected_params={
            "TimePeriod": {"Start": "2026-10-11", "End": "2026-11-01"},
            "Metric": "UNBLENDED_COST",
            "Granularity": "MONTHLY",
            "PredictionIntervalLevel": 80,
        },
    )
    f = reader.month_end_forecast(dt.date(2026, 10, 11), Decimal("800"))
    assert f.month == "2026-10"
    assert f.forecast_usd == Decimal("2300")
    assert (f.lower_usd, f.upper_usd) == (Decimal("2200"), Decimal("2450"))
    assert reader.calls == 1


def test_forecast_returns_none_when_data_unavailable(reader_and_stub):
    reader, stub = reader_and_stub
    stub.add_client_error("get_cost_forecast", "DataUnavailableException", "not enough data")
    assert reader.month_end_forecast(dt.date(2026, 10, 11), Decimal(0)) is None
    assert reader.calls == 1  # still a billed request


def test_forecast_reraises_other_errors(reader_and_stub):
    reader, stub = reader_and_stub
    stub.add_client_error("get_cost_forecast", "AccessDeniedException", "denied")
    with pytest.raises(Exception, match="AccessDenied"):
        reader.month_end_forecast(dt.date(2026, 12, 31), Decimal(0))


def test_forecast_december_rolls_into_next_year(reader_and_stub):
    reader, stub = reader_and_stub
    stub.add_response(
        "get_cost_forecast",
        {"Total": {"Amount": "10", "Unit": "USD"}},
        expected_params={
            "TimePeriod": {"Start": "2026-12-31", "End": "2027-01-01"},
            "Metric": ANY,
            "Granularity": ANY,
            "PredictionIntervalLevel": ANY,
        },
    )
    f = reader.month_end_forecast(dt.date(2026, 12, 31), Decimal("990"))
    assert f.forecast_usd == Decimal("1000") and f.lower_usd is None
