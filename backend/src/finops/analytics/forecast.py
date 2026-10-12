"""Month-end forecast by ordinary least-squares linear regression (numpy only).

Fit y = slope * x + intercept to the last `window` days of total daily cost (x = day number),
then forecast = actual month-to-date + predicted cost of each remaining day of the month.
Linear regression is deliberately simple: explainable, cheap, and good enough for a monthly
run-rate; we show Cost Explorer's own ML forecast next to it for comparison.
"""

import datetime as dt
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal

import numpy as np

from finops.domain import to_money


@dataclass
class ForecastResult:
    month: str
    month_to_date: Decimal
    forecast_total: Decimal
    remaining_days: int
    slope_per_day: float
    intercept: float
    r_squared: float | None
    days_used: int
    method: str = "linear_regression"
    predictions: list[tuple[dt.date, Decimal]] = field(default_factory=list)


def _month_bounds(today: dt.date) -> tuple[dt.date, dt.date]:
    start = today.replace(day=1)
    next_month = (start + timedelta(days=32)).replace(day=1)
    return start, next_month - timedelta(days=1)


def linear_forecast(
    daily_totals: dict[dt.date, Decimal],
    today: dt.date,
    window: int = 30,
    exclude: set[dt.date] | None = None,
) -> ForecastResult:
    """`exclude`: days left out of the fit, e.g. detected anomalies, so a one-off spike doesn't
    inflate the run-rate. They still count towards month-to-date actual spend."""
    month_start, month_end = _month_bounds(today)
    yesterday = today - timedelta(days=1)
    first_day = yesterday - timedelta(days=window - 1)
    history = sorted(
        (d, v)
        for d, v in daily_totals.items()
        if first_day <= d <= yesterday and d not in (exclude or set())
    )
    mtd = sum((v for d, v in daily_totals.items() if month_start <= d <= yesterday), Decimal(0))
    remaining = [today + timedelta(days=i) for i in range((month_end - today).days + 1)]

    if not history:
        return ForecastResult(
            month_start.strftime("%Y-%m"),
            to_money(mtd),
            to_money(mtd),
            len(remaining),
            0,
            0,
            None,
            0,
        )

    origin = history[0][0]
    x = np.array([(d - origin).days for d, _ in history], dtype=float)
    y = np.array([float(v) for _, v in history])
    if len(history) >= 2 and np.ptp(x) > 0:
        slope, intercept = np.polyfit(x, y, 1)
        fitted = slope * x + intercept
        ss_res = float(np.sum((y - fitted) ** 2))
        ss_tot = float(np.sum((y - y.mean()) ** 2))
        r2 = 1 - ss_res / ss_tot if ss_tot > 0 else None
    else:  # one data point: flat run-rate
        slope, intercept, r2 = 0.0, float(y.mean()), None

    predictions = [
        (d, to_money(max(0.0, slope * (d - origin).days + intercept))) for d in remaining
    ]
    total = mtd + sum((p for _, p in predictions), Decimal(0))
    return ForecastResult(
        month=month_start.strftime("%Y-%m"),
        month_to_date=to_money(mtd),
        forecast_total=to_money(total),
        remaining_days=len(remaining),
        slope_per_day=round(float(slope), 4),
        intercept=round(float(intercept), 4),
        r_squared=round(r2, 4) if r2 is not None else None,
        days_used=len(history),
        predictions=predictions,
    )
