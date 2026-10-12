"""Cost endpoints. All of these read stored data only - they never call Cost Explorer."""

import datetime as dt
from datetime import timedelta
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, status

from finops.analytics import costs as agg
from finops.analytics.anomaly import TOTAL, detect_cost_anomalies
from finops.analytics.budgets import evaluate_budget
from finops.analytics.forecast import linear_forecast
from finops.api.auth import CurrentUser
from finops.api.deps import AccountDep, ReposDep
from finops.domain import FindingStatus, utcnow
from finops.rules import total_savings

router = APIRouter(prefix="/accounts/{account_id}/costs", tags=["costs"])


def _range(start: dt.date | None, end: dt.date | None, days: int = 90) -> tuple[dt.date, dt.date]:
    end = end or utcnow().date() - timedelta(days=1)
    start = start or end - timedelta(days=days - 1)
    if start > end:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "start must be before end")
    if (end - start).days > 400:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "range is limited to 400 days")
    return start, end


@router.get("/summary")
def summary(account: AccountDep, repos: ReposDep, _: CurrentUser) -> dict[str, object]:
    """KPI cards: month-to-date, last month, month-over-month, forecast, savings."""
    today = utcnow().date()
    yesterday = today - timedelta(days=1)
    month_start = today.replace(day=1)
    prev_start, prev_end = agg.previous_month(today)
    costs = repos.costs.query_daily_costs(account.id, prev_start - timedelta(days=31), yesterday)

    mtd = agg.total(costs, month_start, yesterday)
    last_month = agg.total(costs, prev_start, prev_end)
    # Fair comparison: same number of days into last month.
    days_in = max(0, (yesterday - month_start).days + 1)
    same_period_end = min(prev_start + timedelta(days=days_in - 1), prev_end)
    last_month_same_period = (
        agg.total(costs, prev_start, same_period_end) if days_in else Decimal(0)
    )

    anomalies = detect_cost_anomalies(costs)
    totals = agg.daily_totals(costs)
    linear = linear_forecast(totals, today, exclude={a.date for a in anomalies if a.scope == TOTAL})
    ce = repos.costs.get_forecast(account.id, month_start.strftime("%Y-%m"))
    forecast = ce.forecast_usd if ce else linear.forecast_total

    findings = repos.findings.list(account.id, status=FindingStatus.OPEN)
    budget = repos.budgets.get(account.id)
    budget_status = (
        evaluate_budget(budget, month_start.strftime("%Y-%m"), mtd, forecast) if budget else None
    )
    recent_anomalies = [a for a in anomalies if a.date >= today - timedelta(days=30)]
    return {
        "currency": "USD",
        "month": month_start.strftime("%Y-%m"),
        "data_through": max(totals) if totals else None,
        "month_to_date_usd": float(mtd),
        "last_month_usd": float(last_month),
        "last_month_same_period_usd": float(last_month_same_period),
        "mom_change_pct": agg.pct_change(mtd, last_month_same_period),
        "forecast_usd": float(forecast),
        "forecast_source": "cost_explorer" if ce else "linear_regression",
        "linear_forecast_usd": float(linear.forecast_total),
        "cost_explorer_forecast_usd": float(ce.forecast_usd) if ce else None,
        "budget": {
            "limit_usd": float(budget_status.limit),
            "actual_pct": budget_status.actual_pct,
            "forecast_pct": budget_status.forecast_pct,
        }
        if budget_status
        else None,
        "potential_savings_usd": float(total_savings(findings)),
        "open_findings": len(findings),
        "anomalies_30d": len({a.date for a in recent_anomalies}),
        "last_collected_at": account.last_collected_at,
        "last_refresh_at": account.last_refresh_at,
    }


@router.get("/daily")
def daily(
    account: AccountDep,
    repos: ReposDep,
    _: CurrentUser,
    start: dt.date | None = None,
    end: dt.date | None = None,
    top: int = Query(6, ge=1, le=12),
) -> dict[str, object]:
    start, end = _range(start, end)
    costs = repos.costs.query_daily_costs(account.id, start, end)
    return agg.daily_series(costs, start, end, top_services=top) | {
        "start": start,
        "end": end,
        "total_usd": float(agg.total(costs, start, end)),
    }


@router.get("/breakdown")
def breakdown(
    account: AccountDep,
    repos: ReposDep,
    _: CurrentUser,
    dimension: Literal["service", "region", "tag"] = "service",
    tag_key: str | None = None,
    start: dt.date | None = None,
    end: dt.date | None = None,
    limit: int = Query(10, ge=1, le=50),
) -> dict[str, object]:
    start, end = _range(start, end)
    if dimension == "tag":
        if not tag_key:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "tag_key is required")
        rows = agg.tag_breakdown(
            repos.costs.query_tag_costs(account.id, tag_key, start, end), limit
        )
    else:
        rows = agg.breakdown(
            repos.costs.query_daily_costs(account.id, start, end), dimension, limit
        )
    return {"dimension": dimension, "tag_key": tag_key, "start": start, "end": end, "items": rows}


@router.get("/forecast")
def forecast(account: AccountDep, repos: ReposDep, _: CurrentUser) -> dict[str, object]:
    """Linear regression vs Cost Explorer forecast, with actual and predicted days for a chart."""
    today = utcnow().date()
    month_start = today.replace(day=1)
    costs = repos.costs.query_daily_costs(account.id, today - timedelta(days=60), today)
    totals = agg.daily_totals(costs)
    spikes = {a.date for a in detect_cost_anomalies(costs) if a.scope == TOTAL}
    linear = linear_forecast(totals, today, exclude=spikes)
    ce = repos.costs.get_forecast(account.id, month_start.strftime("%Y-%m"))
    budget = repos.budgets.get(account.id)
    actual_days = [
        {"date": d.isoformat(), "actual": float(v)} for d, v in totals.items() if d >= month_start
    ]
    predicted_days = [{"date": d.isoformat(), "predicted": float(v)} for d, v in linear.predictions]
    return {
        "month": linear.month,
        "month_to_date_usd": float(linear.month_to_date),
        "linear": {
            "forecast_usd": float(linear.forecast_total),
            "slope_per_day": linear.slope_per_day,
            "intercept": linear.intercept,
            "r_squared": linear.r_squared,
            "days_used": linear.days_used,
            "excluded_anomaly_days": sorted(
                d.isoformat() for d in spikes if d >= today - timedelta(days=30)
            ),
        },
        "cost_explorer": {
            "forecast_usd": float(ce.forecast_usd),
            "lower_usd": float(ce.lower_usd) if ce.lower_usd is not None else None,
            "upper_usd": float(ce.upper_usd) if ce.upper_usd is not None else None,
            "generated_at": ce.generated_at,
        }
        if ce
        else None,
        "budget_limit_usd": float(budget.monthly_limit_usd) if budget else None,
        "days": actual_days + predicted_days,
    }


@router.get("/anomalies")
def anomalies(
    account: AccountDep, repos: ReposDep, _: CurrentUser, days: int = Query(90, ge=7, le=365)
) -> list[dict[str, object]]:
    today = utcnow().date()
    since = today - timedelta(days=days)
    costs = repos.costs.query_daily_costs(account.id, since - timedelta(days=31), today)
    return [
        {
            "date": a.date,
            "scope": a.scope,
            "actual_usd": a.actual,
            "mean_usd": a.mean,
            "std_usd": a.std,
            "threshold_usd": a.threshold,
            "excess_usd": round(a.excess, 2),
        }
        for a in detect_cost_anomalies(costs, since=since)
    ]
