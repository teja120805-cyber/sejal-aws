"""Cost Explorer reader.

Cost Explorer charges $0.01 per API request, so this class is only used by the collection service
(daily schedule + rate-limited manual refresh), never by API endpoints serving page loads.
Every request is counted so each CollectionRun records how many paid calls it made.

Cost Explorer is a global service whose endpoint lives in us-east-1.
"""

import datetime as dt
from collections.abc import Iterator
from datetime import timedelta
from decimal import Decimal
from typing import Any

from botocore.exceptions import ClientError

from finops.aws.session import AccountSession
from finops.domain import CostForecast, DailyCost, TagCost, to_money, utcnow

CE_REGION = "us-east-1"
METRIC = "UnblendedCost"


class CostExplorerReader:
    def __init__(self, session: AccountSession) -> None:
        self.account_id = session.account.id
        self.client = session.client("ce", CE_REGION)
        self.calls = 0

    def _get_cost_and_usage(self, **kwargs: Any) -> Iterator[dict[str, Any]]:
        token: str | None = None
        while True:
            if token:
                kwargs["NextPageToken"] = token
            self.calls += 1
            resp = self.client.get_cost_and_usage(**kwargs)
            yield from resp["ResultsByTime"]
            token = resp.get("NextPageToken")
            if not token:
                return

    def daily_costs(self, start: dt.date, end: dt.date) -> list[DailyCost]:
        """Daily cost per (service, region) for start..end inclusive."""
        rows: list[DailyCost] = []
        results = self._get_cost_and_usage(
            TimePeriod={"Start": start.isoformat(), "End": (end + timedelta(days=1)).isoformat()},
            Granularity="DAILY",
            Metrics=[METRIC],
            GroupBy=[
                {"Type": "DIMENSION", "Key": "SERVICE"},
                {"Type": "DIMENSION", "Key": "REGION"},
            ],
        )
        for period in results:
            day = dt.date.fromisoformat(period["TimePeriod"]["Start"])
            for group in period.get("Groups", []):
                service, region = group["Keys"]
                amount = Decimal(group["Metrics"][METRIC]["Amount"])
                if amount == 0:
                    continue
                rows.append(
                    DailyCost(
                        account_id=self.account_id,
                        date=day,
                        service=service,
                        # Global services (CloudFront, Route 53...) report "NoRegion"
                        region="global" if region in ("NoRegion", "") else region,
                        amount_usd=amount,
                    )
                )
        return rows

    def tag_costs(self, tag_key: str, start: dt.date, end: dt.date) -> list[TagCost]:
        """Daily cost per value of one tag. Untagged spend gets tag_value == ""."""
        rows: list[TagCost] = []
        results = self._get_cost_and_usage(
            TimePeriod={"Start": start.isoformat(), "End": (end + timedelta(days=1)).isoformat()},
            Granularity="DAILY",
            Metrics=[METRIC],
            GroupBy=[{"Type": "TAG", "Key": tag_key}],
        )
        for period in results:
            day = dt.date.fromisoformat(period["TimePeriod"]["Start"])
            for group in period.get("Groups", []):
                # Keys look like "Project$payments-api"; "Project$" means the tag is missing.
                value = group["Keys"][0].split("$", 1)[1] if "$" in group["Keys"][0] else ""
                amount = Decimal(group["Metrics"][METRIC]["Amount"])
                if amount == 0:
                    continue
                rows.append(
                    TagCost(
                        account_id=self.account_id,
                        date=day,
                        tag_key=tag_key,
                        tag_value=value,
                        amount_usd=amount,
                    )
                )
        return rows

    def month_end_forecast(self, today: dt.date, month_to_date: Decimal) -> CostForecast | None:
        """Cost Explorer's forecast for the rest of the month, plus spend so far.

        Returns None when Cost Explorer can't forecast yet (new accounts have too little history).
        """
        month_start = today.replace(day=1)
        next_month = (month_start + timedelta(days=32)).replace(day=1)
        self.calls += 1
        try:
            resp = self.client.get_cost_forecast(
                TimePeriod={"Start": today.isoformat(), "End": next_month.isoformat()},
                Metric="UNBLENDED_COST",
                Granularity="MONTHLY",
                PredictionIntervalLevel=80,
            )
        except ClientError as err:
            if err.response["Error"]["Code"] == "DataUnavailableException":
                return None
            raise
        remaining = Decimal(resp["Total"]["Amount"])
        intervals = resp.get("ForecastResultsByTime") or [{}]
        lower = intervals[0].get("PredictionIntervalLowerBound")
        upper = intervals[0].get("PredictionIntervalUpperBound")
        return CostForecast(
            account_id=self.account_id,
            month=month_start.strftime("%Y-%m"),
            forecast_usd=month_to_date + remaining,
            lower_usd=to_money(month_to_date + Decimal(lower)) if lower else None,
            upper_usd=to_money(month_to_date + Decimal(upper)) if upper else None,
            generated_at=utcnow(),
        )
