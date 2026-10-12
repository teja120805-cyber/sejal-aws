"""Demo-mode "Refresh": same 1-per-hour rule as production, but data is regenerated locally."""

from datetime import datetime, timedelta

from finops.alerts.engine import evaluate_alerts
from finops.alerts.notifiers import Notifier
from finops.collection import RefreshTooSoonError
from finops.demo.generator import generate_demo_dataset
from finops.domain import CollectionRun, RunStatus, RunTrigger, utcnow
from finops.repositories import Repositories
from finops.rules import refresh_findings


def demo_refresh(
    repos: Repositories,
    account_id: str,
    cooldown: timedelta,
    notifiers: list[Notifier],
    now: datetime | None = None,
) -> CollectionRun:
    now = now or utcnow()
    if not repos.accounts.try_start_refresh(account_id, now, cooldown):
        account = repos.accounts.get(account_id)
        last = account.last_refresh_at if account and account.last_refresh_at else now
        raise RefreshTooSoonError(last + cooldown - now)

    ds = generate_demo_dataset(now=now)
    run = CollectionRun(account_id=account_id, trigger=RunTrigger.DEMO, started_at=now)
    repos.costs.put_daily_costs(ds.daily_costs)
    repos.costs.put_tag_costs(ds.tag_costs)
    repos.costs.put_forecast(ds.forecast)
    # Keep resource states changed by (simulated) remediation, refresh everything else.
    current = {r.key: r for r in repos.resources.list(account_id)}
    resources = []
    for r in ds.resources:
        old = current.get(r.key)
        if old is not None and old.state != r.state:
            r.state = old.state
        if old is not None or not current:
            resources.append(r)
    repos.resources.replace_inventory(account_id, resources)
    refresh_findings(repos, account_id, now)

    account = repos.accounts.get(account_id)
    if account is not None:
        account.last_collected_at = now
        repos.accounts.put(account)
        evaluate_alerts(repos, account, notifiers, now)

    run.status = RunStatus.SUCCEEDED
    run.finished_at = utcnow()
    run.resources_collected = len(resources)
    repos.runs.put(run)
    return run
