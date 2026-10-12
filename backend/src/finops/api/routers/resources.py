import csv
import io
from collections import Counter, defaultdict
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse

from finops.api.auth import CurrentUser
from finops.api.deps import AccountDep, ReposDep
from finops.domain import Resource, ResourceType

router = APIRouter(prefix="/accounts/{account_id}/resources", tags=["resources"])

SortField = Literal["name", "type", "region", "state", "cost", "created_at"]


class ResourceFilters:
    def __init__(
        self,
        type: ResourceType | None = None,
        region: str | None = None,
        state: str | None = None,
        q: Annotated[str | None, Query(max_length=200, description="search id/name/tags")] = None,
        missing_tag: str | None = None,
        sort: SortField = "cost",
        order: Literal["asc", "desc"] = "desc",
    ) -> None:
        self.type, self.region, self.state, self.q = type, region, state, q
        self.missing_tag, self.sort, self.order = missing_tag, sort, order

    def apply(self, resources: list[Resource]) -> list[Resource]:
        rows = resources
        if self.region:
            rows = [r for r in rows if r.region == self.region]
        if self.state:
            rows = [r for r in rows if (r.state or "") == self.state]
        if self.missing_tag:
            rows = [r for r in rows if not r.tags.get(self.missing_tag, "").strip()]
        if self.q:
            needle = self.q.lower()
            rows = [
                r
                for r in rows
                if needle in r.resource_id.lower()
                or needle in (r.name or "").lower()
                or any(needle in f"{k}={v}".lower() for k, v in r.tags.items())
            ]
        key = {
            "name": lambda r: (r.name or r.resource_id).lower(),
            "type": lambda r: r.type.value,
            "region": lambda r: r.region,
            "state": lambda r: r.state or "",
            "cost": lambda r: r.est_monthly_cost_usd,
            "created_at": lambda r: r.created_at.timestamp() if r.created_at else 0,
        }[self.sort]
        return sorted(rows, key=key, reverse=self.order == "desc")


Filters = Annotated[ResourceFilters, Depends()]


@router.get("")
def list_resources(
    account: AccountDep, repos: ReposDep, filters: Filters, _: CurrentUser
) -> dict[str, object]:
    everything = repos.resources.list(account.id, type=filters.type)
    rows = filters.apply(everything)
    cost_by_type: dict[str, Decimal] = defaultdict(Decimal)
    for r in rows:
        cost_by_type[r.type.value] += r.est_monthly_cost_usd
    return {
        "items": rows,
        "total": len(rows),
        "total_monthly_cost_usd": float(sum((r.est_monthly_cost_usd for r in rows), Decimal(0))),
        "by_type": dict(Counter(r.type.value for r in rows)),
        "cost_by_type": {k: float(v) for k, v in cost_by_type.items()},
        "regions": sorted({r.region for r in everything}),
        "states": sorted({r.state for r in everything if r.state}),
    }


CSV_COLUMNS = [
    "resource_id",
    "name",
    "type",
    "region",
    "state",
    "est_monthly_cost_usd",
    "created_at",
    "tags",
    "details",
]


@router.get("/export.csv")
def export_csv(
    account: AccountDep, repos: ReposDep, filters: Filters, _: CurrentUser
) -> StreamingResponse:
    rows = filters.apply(repos.resources.list(account.id, type=filters.type))
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(CSV_COLUMNS)
    for r in rows:
        writer.writerow(
            [
                r.resource_id,
                r.name or "",
                r.type.value,
                r.region,
                r.state or "",
                f"{r.est_monthly_cost_usd:.2f}",
                r.created_at.isoformat() if r.created_at else "",
                "; ".join(f"{k}={v}" for k, v in sorted(r.tags.items())),
                "; ".join(f"{k}={v}" for k, v in r.attributes.items() if v not in (None, "", [])),
            ]
        )
    filename = f"finops-resources-{account.aws_account_id}.csv"
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
