"""One collection run for one account: inventory -> metrics -> cost estimates -> Cost Explorer.

This is the ONLY code path that calls Cost Explorer. It is reached from:
  * the daily collector Lambda (EventBridge schedule), and
  * refresh_account(), which first claims the 1-per-hour refresh slot atomically in the database.
"""

import datetime as dt
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from botocore.exceptions import BotoCoreError, ClientError

from finops.aws.costs import CostExplorerReader
from finops.aws.inventory import InventoryScanner
from finops.aws.metrics import MetricsCollector
from finops.aws.session import AccountSession
from finops.config import Settings
from finops.domain import (
    Account,
    AccountStatus,
    CollectionRun,
    Resource,
    ResourceType,
    RunStatus,
    RunTrigger,
    utcnow,
)
from finops.pricing import estimate_monthly_cost
from finops.repositories import NotFoundError, Repositories
from finops.rules import refresh_findings

logger = logging.getLogger(__name__)

SessionFactory = Callable[[Account], AccountSession]


class RefreshTooSoonError(Exception):
    def __init__(self, retry_after: timedelta) -> None:
        super().__init__(f"refresh allowed again in {int(retry_after.total_seconds() // 60)} min")
        self.retry_after = retry_after


@dataclass(frozen=True)
class CollectionOptions:
    home_region: str = "ap-south-1"
    backfill_days: int = 90  # first run: fill the dashboard's 90-day history
    lookback_days: int = 7  # later runs: re-read recent days (Cost Explorer restates them)
    collect_costs: bool = True
    max_workers: int = 8

    @classmethod
    def from_settings(cls, settings: Settings) -> "CollectionOptions":
        return cls(
            home_region=settings.aws_region,
            backfill_days=settings.cost_backfill_days,
            lookback_days=settings.cost_lookback_days,
        )


def default_session_factory(home_region: str) -> SessionFactory:
    return lambda account: AccountSession.assume(account, region=home_region)


def collect_account(
    account: Account,
    repos: Repositories,
    session: AccountSession,
    *,
    trigger: RunTrigger,
    options: CollectionOptions | None = None,
    now: datetime | None = None,
) -> CollectionRun:
    opts = options or CollectionOptions()
    now = now or utcnow()
    run = CollectionRun(account_id=account.id, trigger=trigger, started_at=now)
    repos.runs.put(run)

    try:
        regions = session.enabled_regions(opts.home_region)
        account.regions = regions
        _collect_inventory(account, repos, session, regions, run, opts, now)
        refresh_findings(repos, account.id, now)
        if opts.collect_costs:
            try:
                _collect_costs(account, repos, session, run, opts, now.date())
            except (ClientError, BotoCoreError) as err:  # inventory is still worth keeping
                logger.warning("cost explorer failed for %s: %s", account.id, err)
                run.errors.append(f"cost explorer: {err}")
        run.status = RunStatus.PARTIAL if run.errors else RunStatus.SUCCEEDED
        account.status = AccountStatus.CONNECTED
        account.status_message = None
    except Exception as err:  # record any failure on the run, don't crash the whole batch
        logger.exception("collection failed for account %s", account.id)
        run.status = RunStatus.FAILED
        run.errors.append(f"{type(err).__name__}: {err}")
        account.status = AccountStatus.ERROR
        account.status_message = str(err)[:500]

    run.finished_at = utcnow()
    repos.runs.put(run)
    # Re-read so we don't overwrite fields changed meanwhile (e.g. last_refresh_at).
    latest = repos.accounts.get(account.id) or account
    latest.regions = account.regions
    latest.status = account.status
    latest.status_message = account.status_message
    if run.status != RunStatus.FAILED:
        latest.last_collected_at = run.finished_at
    repos.accounts.put(latest)
    return run


def _collect_inventory(
    account: Account,
    repos: Repositories,
    session: AccountSession,
    regions: list[str],
    run: CollectionRun,
    opts: CollectionOptions,
    now: datetime,
) -> None:
    result = InventoryScanner(session, max_workers=opts.max_workers).scan(regions)
    run.errors.extend(result.errors)
    for r in result.resources:
        r.last_seen_at = now

    try:
        MetricsCollector(session).enrich(result.resources, now)
    except Exception as err:  # metrics are an enhancement; inventory is still useful without
        logger.warning("metrics collection failed: %s", err)
        run.errors.append(f"cloudwatch: {err}")

    for r in result.resources:
        r.est_monthly_cost_usd = estimate_monthly_cost(r)

    # Keep the last known resources for scans that failed, instead of making them "disappear".
    kept = [
        old
        for old in repos.resources.list(account.id)
        if (_scan_region(old), old.type) in result.failed
    ]

    repos.resources.replace_inventory(account.id, result.resources + kept)
    run.resources_collected = len(result.resources)


def _scan_region(r: Resource) -> str:
    return "global" if r.type == ResourceType.S3_BUCKET else r.region


def _collect_costs(
    account: Account,
    repos: Repositories,
    session: AccountSession,
    run: CollectionRun,
    opts: CollectionOptions,
    today: dt.date,
) -> None:
    ce = CostExplorerReader(session)
    end = today - timedelta(days=1)  # today's data is incomplete
    history_start = end - timedelta(days=opts.backfill_days - 1)
    oldest_week = repos.costs.query_daily_costs(
        account.id, history_start, history_start + timedelta(days=6)
    )
    have_history = bool(oldest_week)
    days = opts.lookback_days if have_history else opts.backfill_days
    start = end - timedelta(days=days - 1)

    try:
        costs = ce.daily_costs(start, end)
        repos.costs.put_daily_costs(costs)

        for tag_key in repos.settings.get(account.id).required_tags:
            repos.costs.put_tag_costs(ce.tag_costs(tag_key, start, end))

        month_start = today.replace(day=1)
        mtd_rows = repos.costs.query_daily_costs(account.id, month_start, end)
        mtd = sum((c.amount_usd for c in mtd_rows), Decimal(0))
        forecast = ce.month_end_forecast(today, mtd)
        if forecast is not None:
            repos.costs.put_forecast(forecast)
    finally:
        run.cost_explorer_calls = ce.calls


def start_async_refresh(
    account_id: str,
    repos: Repositories,
    settings: Settings,
    invoke: Callable[[dict[str, str]], None],
    *,
    now: datetime | None = None,
) -> CollectionRun:
    """Claim the refresh slot, then hand the work to the collector Lambda (async invoke).

    Returns immediately with a RUNNING placeholder; the collector records the real run.
    """
    now = now or utcnow()
    cooldown = timedelta(minutes=settings.refresh_cooldown_minutes)
    if not repos.accounts.try_start_refresh(account_id, now, cooldown):
        account = repos.accounts.get(account_id)
        last = account.last_refresh_at if account and account.last_refresh_at else now
        raise RefreshTooSoonError(retry_after=last + cooldown - now)
    invoke({"account_id": account_id, "trigger": RunTrigger.MANUAL.value})
    return CollectionRun(account_id=account_id, trigger=RunTrigger.MANUAL, started_at=now)


def lambda_invoker(function_name: str, region: str) -> Callable[[dict[str, str]], None]:
    import json

    import boto3

    client = boto3.client("lambda", region_name=region)

    def invoke(payload: dict[str, str]) -> None:
        client.invoke(
            FunctionName=function_name, InvocationType="Event", Payload=json.dumps(payload)
        )

    return invoke


def refresh_account(
    account_id: str,
    repos: Repositories,
    settings: Settings,
    *,
    session_factory: SessionFactory | None = None,
    now: datetime | None = None,
) -> CollectionRun:
    """Manual refresh, limited to once per cooldown period (default 60 min) per account."""
    now = now or utcnow()
    cooldown = timedelta(minutes=settings.refresh_cooldown_minutes)
    if not repos.accounts.try_start_refresh(account_id, now, cooldown):
        account = repos.accounts.get(account_id)
        last = account.last_refresh_at if account and account.last_refresh_at else now
        raise RefreshTooSoonError(retry_after=last + cooldown - now)

    account = repos.accounts.get(account_id)
    if account is None:  # deleted in between
        raise NotFoundError(account_id)
    factory = session_factory or default_session_factory(settings.aws_region)
    return collect_account(
        account,
        repos,
        factory(account),
        trigger=RunTrigger.MANUAL,
        options=CollectionOptions.from_settings(settings),
        now=now,
    )
