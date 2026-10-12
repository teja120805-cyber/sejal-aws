import datetime as dt
from dataclasses import asdict

from fastapi import APIRouter, Query

from finops.analytics import costs as agg
from finops.analytics.tags import tag_compliance
from finops.api.auth import CurrentUser
from finops.api.deps import AccountDep, ReposDep
from finops.api.routers.costs import _range

router = APIRouter(prefix="/accounts/{account_id}/tags", tags=["tags"])


@router.get("/compliance")
def compliance(account: AccountDep, repos: ReposDep, _: CurrentUser) -> dict[str, object]:
    required = repos.settings.get(account.id).required_tags
    report = tag_compliance(repos.resources.list(account.id), required)
    data = asdict(report)
    data["untagged_monthly_cost_usd"] = float(report.untagged_monthly_cost_usd)
    return data


@router.get("/costs")
def tag_costs(
    account: AccountDep,
    repos: ReposDep,
    _: CurrentUser,
    tag_key: str = Query(..., min_length=1),
    start: dt.date | None = None,
    end: dt.date | None = None,
) -> dict[str, object]:
    """Cost by tag value (from Cost Explorer tag grouping; untagged spend included)."""
    start, end = _range(start, end, days=30)
    rows = repos.costs.query_tag_costs(account.id, tag_key, start, end)
    return {"tag_key": tag_key, "start": start, "end": end, "items": agg.tag_breakdown(rows)}
