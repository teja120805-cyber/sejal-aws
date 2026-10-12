"""Budget evaluation: which thresholds are crossed by actual and by forecast spend."""

from dataclasses import dataclass, field
from decimal import Decimal

from finops.domain import Budget


@dataclass
class ThresholdCrossing:
    threshold: int  # percent of budget
    kind: str  # "actual" or "forecast"
    amount: Decimal  # the spend that crossed it


@dataclass
class BudgetStatus:
    month: str
    limit: Decimal
    actual: Decimal
    forecast: Decimal
    actual_pct: float
    forecast_pct: float
    crossed: list[ThresholdCrossing] = field(default_factory=list)


def evaluate_budget(budget: Budget, month: str, actual: Decimal, forecast: Decimal) -> BudgetStatus:
    limit = budget.monthly_limit_usd
    status = BudgetStatus(
        month=month,
        limit=limit,
        actual=actual,
        forecast=forecast,
        actual_pct=round(float(actual / limit * 100), 1),
        forecast_pct=round(float(forecast / limit * 100), 1),
    )
    for t in budget.thresholds:
        line = limit * t / 100
        if actual >= line:
            status.crossed.append(ThresholdCrossing(t, "actual", actual))
        elif forecast >= line:
            # Only warn on forecast if actual hasn't already crossed (that alert is stronger).
            status.crossed.append(ThresholdCrossing(t, "forecast", forecast))
    return status
