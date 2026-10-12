"""Daily collector Lambda, triggered by an EventBridge schedule.

This is a thin entrypoint: all collection logic lives in the shared `finops` package
(backend/src/finops) so the API's manual "Refresh" runs exactly the same code.

Event: {} collects every connected account; {"account_id": "..."} collects just one.
The API's manual Refresh invokes this asynchronously with {"account_id", "trigger": "manual"}
after it has claimed the 1-per-hour refresh slot.
"""

import logging
from typing import Any

from finops.alerts import build_notifiers, evaluate_alerts
from finops.collection import CollectionOptions, collect_account, default_session_factory
from finops.config import get_settings
from finops.domain import AccountStatus, RunStatus, RunTrigger, utcnow
from finops.repositories import build_repositories

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    settings = get_settings()
    logger.info("collector invoked: source=%s demo=%s", event.get("source"), settings.demo_mode)
    if settings.demo_mode:
        # Demo data is generated in memory by the API; there is nothing to collect.
        return {"status": "skipped", "reason": "demo mode", "accounts_processed": 0}

    repos = build_repositories(settings)
    session_factory = default_session_factory(settings.aws_region)
    options = CollectionOptions.from_settings(settings)
    notifiers = build_notifiers(settings, _secret_store(settings.aws_region))

    accounts = repos.accounts.list()
    if event.get("account_id"):
        accounts = [a for a in accounts if a.id == event["account_id"]]

    trigger = RunTrigger.MANUAL if event.get("trigger") == "manual" else RunTrigger.SCHEDULE
    results = []
    for account in accounts:
        if account.status == AccountStatus.PENDING:
            continue  # connection never tested successfully
        try:
            session = session_factory(account)
        except Exception as err:  # e.g. customer deleted the role or changed the ExternalId
            logger.exception("assume role failed for %s", account.id)
            account.status = AccountStatus.ERROR
            account.status_message = f"AssumeRole failed: {err}"[:500]
            repos.accounts.put(account)
            results.append({"account_id": account.id, "status": "assume_role_failed"})
            continue
        run = collect_account(account, repos, session, trigger=trigger, options=options)
        alerts = []
        if run.status != RunStatus.FAILED:
            fresh = repos.accounts.get(account.id) or account
            alerts = evaluate_alerts(repos, fresh, notifiers, utcnow())
        results.append(
            {
                "account_id": account.id,
                "status": run.status.value,
                "resources": run.resources_collected,
                "cost_explorer_calls": run.cost_explorer_calls,
                "errors": len(run.errors),
                "alerts_raised": len(alerts),
            }
        )

    return {"status": "ok", "accounts_processed": len(results), "results": results}


def _secret_store(region: str):  # type: ignore[no-untyped-def]
    import boto3

    from finops.secrets import SsmSecretStore

    return SsmSecretStore(boto3.client("ssm", region_name=region))
