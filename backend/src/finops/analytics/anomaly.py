"""Cost anomaly detection: a day is anomalous if its cost is above
mean + sigma * standard deviation of the trailing `window` days (the day itself excluded).

Two extra guards stop false alarms on very flat series (where sigma is tiny):
`min_increase_usd` ignores financially trivial blips (e.g. $0.02 -> $0.10) and
`min_increase_pct` requires the day to be meaningfully above normal (default +20%).
"""

import datetime as dt
from collections import defaultdict
from dataclasses import dataclass

import numpy as np

from finops.domain import DailyCost

TOTAL = "Total"


@dataclass(frozen=True)
class Anomaly:
    date: dt.date
    scope: str  # "Total" or a service name
    actual: float
    mean: float
    std: float
    threshold: float

    @property
    def excess(self) -> float:
        return self.actual - self.mean


def detect_anomalies(
    series: dict[dt.date, float],
    scope: str = TOTAL,
    window: int = 30,
    sigma: float = 3.0,
    min_history: int = 14,
    min_increase_usd: float = 1.0,
    since: dt.date | None = None,
    min_increase_pct: float = 20.0,
) -> list[Anomaly]:
    days = sorted(series)
    out: list[Anomaly] = []
    for i, day in enumerate(days):
        if since and day < since:
            continue
        history = [series[d] for d in days[max(0, i - window) : i] if (day - d).days <= window]
        if len(history) < min_history:
            continue
        values = np.array(history)
        mean, std = float(values.mean()), float(values.std())
        threshold = mean + sigma * std
        actual = series[day]
        big_enough = actual - mean >= max(min_increase_usd, mean * min_increase_pct / 100)
        if actual > threshold and big_enough:
            out.append(
                Anomaly(
                    day, scope, round(actual, 4), round(mean, 4), round(std, 4), round(threshold, 4)
                )
            )
    return out


def detect_cost_anomalies(
    costs: list[DailyCost],
    since: dt.date | None = None,
    window: int = 30,
    sigma: float = 3.0,
    min_increase_usd: float = 1.0,
) -> list[Anomaly]:
    """Check total daily cost and each service separately; newest first."""
    total: dict[dt.date, float] = defaultdict(float)
    per_service: dict[str, dict[dt.date, float]] = defaultdict(lambda: defaultdict(float))
    for c in costs:
        total[c.date] += float(c.amount_usd)
        per_service[c.service][c.date] += float(c.amount_usd)

    all_days = sorted(total)
    found = detect_anomalies(
        total, TOTAL, window, sigma, min_increase_usd=min_increase_usd, since=since
    )
    for service, series in per_service.items():
        # A service with no cost on a day really cost $0 that day - fill gaps with zeros.
        filled = {d: series.get(d, 0.0) for d in all_days}
        found += detect_anomalies(
            filled, service, window, sigma, min_increase_usd=min_increase_usd, since=since
        )
    # Newest day first; within a day the Total row first, then biggest excess first.
    return sorted(found, key=lambda a: (-a.date.toordinal(), a.scope != TOTAL, -a.excess))
