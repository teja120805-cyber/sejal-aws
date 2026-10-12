"""Budget and anomaly alerting with de-duplication.

Run after every collection. Each potential alert has a deterministic key, e.g.
    budget#2026-10#80#forecast      (80% of October's budget, by forecast)
    budget#2026-10#100#actual
    anomaly#2026-09-23#Total
The key is claimed with an atomic conditional write before sending, so an alert is sent once,
even if the collector runs twice or the user refreshes. Keys expire via DynamoDB TTL.
"""

import datetime as dt
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal

from finops.alerts.notifiers import Notifier
from finops.analytics.anomaly import TOTAL, Anomaly, detect_cost_anomalies
from finops.analytics.budgets import ThresholdCrossing, evaluate_budget
from finops.analytics.costs import daily_totals
from finops.analytics.forecast import linear_forecast
from finops.domain import Account, Alert, AlertType, Severity
from finops.repositories import Repositories

logger = logging.getLogger(__name__)
DEDUP_TTL = timedelta(days=120)


@dataclass
class _Candidate:
    key: str
    alert: Alert


def month_forecast(repos: Repositories, account_id: str, today: dt.date) -> tuple[Decimal, Decimal]:
    """(month-to-date actual, month-end forecast). Prefers Cost Explorer's stored forecast and
    falls back to our linear regression."""
    month_start = today.replace(day=1)
    costs = repos.costs.query_daily_costs(account_id, today - timedelta(days=45), today)
    totals = daily_totals(costs)
    anomalies = {a.date for a in detect_cost_anomalies(costs) if a.scope == TOTAL}
    linear = linear_forecast(totals, today, exclude=anomalies)
    ce = repos.costs.get_forecast(account_id, month_start.strftime("%Y-%m"))
    return linear.month_to_date, ce.forecast_usd if ce else linear.forecast_total


def _budget_alert(
    account: Account, c: ThresholdCrossing, month: str, limit: Decimal, now: datetime
) -> _Candidate:
    pct = c.amount / limit * 100
    if c.kind == "actual":
        title = f"Budget {c.threshold}% reached"
        message = f"Spend for {month} is ${c.amount:,.2f}, {pct:.0f}% of the ${limit:,.2f} budget."
        severity = Severity.HIGH if c.threshold >= 100 else Severity.MEDIUM
        type_ = AlertType.BUDGET_ACTUAL
    else:
        title = f"Forecast to exceed {c.threshold}% of budget"
        message = (
            f"Forecast spend for {month} is ${c.amount:,.2f}, {pct:.0f}% of the "
            f"${limit:,.2f} budget."
        )
        severity = Severity.MEDIUM if c.threshold >= 100 else Severity.LOW
        type_ = AlertType.BUDGET_FORECAST
    key = f"budget#{month}#{c.threshold}#{c.kind}"
    alert = Alert(
        account_id=account.id,
        type=type_,
        severity=severity,
        title=title,
        message=message,
        dedup_key=key,
        created_at=now,
        data={
            "month": month,
            "threshold": c.threshold,
            "amount_usd": float(c.amount),
            "limit_usd": float(limit),
            "kind": c.kind,
        },
    )
    return _Candidate(key, alert)


def _anomaly_alert(account: Account, a: Anomaly, created_at: datetime) -> _Candidate:
    scope = "Total daily cost" if a.scope == TOTAL else a.scope
    key = f"anomaly#{a.date.isoformat()}#{a.scope}"
    alert = Alert(
        account_id=account.id,
        type=AlertType.ANOMALY,
        severity=Severity.HIGH if a.scope == TOTAL else Severity.MEDIUM,
        title=f"Cost anomaly: {scope}",
        message=(
            f"{scope} on {a.date:%d %b %Y} was ${a.actual:,.2f}, versus a 30-day average of "
            f"${a.mean:,.2f} (threshold ${a.threshold:,.2f} = mean + 3 standard deviations)."
        ),
        dedup_key=key,
        created_at=created_at,
        data={
            "date": a.date.isoformat(),
            "scope": a.scope,
            "actual_usd": a.actual,
            "mean_usd": a.mean,
            "threshold_usd": a.threshold,
        },
    )
    return _Candidate(key, alert)


def evaluate_alerts(
    repos: Repositories,
    account: Account,
    notifiers: list[Notifier],
    now: datetime,
    anomaly_lookback_days: int = 3,
    backdate_anomalies: bool = False,
) -> list[Alert]:
    """Create, send and store any new alerts. Returns the alerts that were newly raised."""
    today = now.date()
    candidates: list[_Candidate] = []

    budget = repos.budgets.get(account.id)
    if budget is not None:
        month = today.strftime("%Y-%m")
        mtd, forecast = month_forecast(repos, account.id, today)
        status = evaluate_budget(budget, month, mtd, forecast)
        candidates += [
            _budget_alert(account, c, month, budget.monthly_limit_usd, now) for c in status.crossed
        ]

    since = today - timedelta(days=anomaly_lookback_days)
    costs = repos.costs.query_daily_costs(account.id, since - timedelta(days=31), today)
    for a in reversed(detect_cost_anomalies(costs, since=since)):  # oldest first
        # Demo seeding backdates alerts to the morning after the anomaly so history looks real.
        when = datetime.combine(a.date + timedelta(days=1), time(2, 30), UTC)
        candidates.append(_anomaly_alert(account, a, min(when, now) if backdate_anomalies else now))

    settings = repos.settings.get(account.id)
    raised: list[Alert] = []
    for c in candidates:
        if not repos.alerts.claim_dedup(account.id, c.key, expires_at=now + DEDUP_TTL):
            continue  # already alerted
        for n in notifiers:
            try:
                if n.send(c.alert, account, settings):
                    c.alert.channels_sent.append(n.channel)
            except Exception as err:  # one broken channel must not block the others
                logger.warning("alert delivery via %s failed: %s", n.channel, err)
                c.alert.channels_sent.append(f"{n.channel} (failed)")
        repos.alerts.put(c.alert)
        raised.append(c.alert)
    return raised
