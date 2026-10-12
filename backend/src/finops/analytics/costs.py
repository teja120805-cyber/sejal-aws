"""Cost aggregations for the dashboard (pure functions over stored DailyCost rows)."""

import datetime as dt
from collections import defaultdict
from collections.abc import Iterable
from datetime import timedelta
from decimal import Decimal

from finops.domain import DailyCost, TagCost

ZERO = Decimal(0)


def daily_totals(costs: Iterable[DailyCost]) -> dict[dt.date, Decimal]:
    totals: dict[dt.date, Decimal] = defaultdict(Decimal)
    for c in costs:
        totals[c.date] += c.amount_usd
    return dict(sorted(totals.items()))


def total(costs: Iterable[DailyCost], start: dt.date, end: dt.date) -> Decimal:
    return sum((c.amount_usd for c in costs if start <= c.date <= end), ZERO)


def month_start(day: dt.date) -> dt.date:
    return day.replace(day=1)


def previous_month(day: dt.date) -> tuple[dt.date, dt.date]:
    end = month_start(day) - timedelta(days=1)
    return end.replace(day=1), end


def pct_change(current: Decimal, previous: Decimal) -> float | None:
    if previous == 0:
        return None
    return round(float((current - previous) / previous * 100), 1)


def breakdown(
    costs: Iterable[DailyCost], key: str, limit: int | None = None
) -> list[dict[str, object]]:
    """Totals grouped by "service" or "region", largest first, with share of total."""
    sums: dict[str, Decimal] = defaultdict(Decimal)
    for c in costs:
        sums[getattr(c, key)] += c.amount_usd
    return _ranked(sums, limit)


def tag_breakdown(costs: Iterable[TagCost], limit: int | None = None) -> list[dict[str, object]]:
    sums: dict[str, Decimal] = defaultdict(Decimal)
    for c in costs:
        sums[c.tag_value or "(untagged)"] += c.amount_usd
    return _ranked(sums, limit)


def _ranked(sums: dict[str, Decimal], limit: int | None) -> list[dict[str, object]]:
    grand = sum(sums.values(), ZERO)
    rows = sorted(sums.items(), key=lambda kv: kv[1], reverse=True)
    if limit is not None and len(rows) > limit:
        other = sum((v for _, v in rows[limit:]), ZERO)
        rows = [*rows[:limit], ("Other", other)]
    return [
        {
            "key": k,
            "amount_usd": float(round(v, 2)),
            "share_pct": round(float(v / grand * 100), 1) if grand else 0.0,
        }
        for k, v in rows
    ]


def daily_series(
    costs: Iterable[DailyCost], start: dt.date, end: dt.date, top_services: int = 6
) -> dict[str, object]:
    """One row per day with total and top-N services (others grouped), zero-filled."""
    costs = [c for c in costs if start <= c.date <= end]
    services = [row["key"] for row in breakdown(costs, "service", limit=top_services)]
    named = {s for s in services if s != "Other"}
    days: dict[dt.date, dict[str, Decimal]] = {
        start + timedelta(days=i): defaultdict(Decimal) for i in range((end - start).days + 1)
    }
    for c in costs:
        bucket = c.service if c.service in named else "Other"
        days[c.date][bucket] += c.amount_usd
    rows = []
    for day, by_service in days.items():
        row: dict[str, object] = {"date": day.isoformat()}
        for s in services:
            row[s] = float(round(by_service.get(s, ZERO), 2))
        row["total"] = float(round(sum(by_service.values(), ZERO), 2))
        rows.append(row)
    return {"services": services, "days": rows}
