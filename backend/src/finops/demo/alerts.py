from finops.alerts.engine import evaluate_alerts
from finops.alerts.notifiers import Notifier
from finops.demo.generator import DEMO_ACCOUNT_ID
from finops.domain import utcnow
from finops.repositories import Repositories


def seed_demo_alerts(repos: Repositories, notifiers: list[Notifier]) -> None:
    """Give the demo a realistic alert history: the budget-forecast warning and the spike."""
    account = repos.accounts.get(DEMO_ACCOUNT_ID)
    if account is None:
        return
    evaluate_alerts(
        repos,
        account,
        notifiers,
        utcnow(),
        anomaly_lookback_days=30,
        backdate_anomalies=True,
    )
