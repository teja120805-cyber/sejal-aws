import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import boto3
import pytest
from moto import mock_aws

from finops.alerts.engine import evaluate_alerts
from finops.alerts.notifiers import (
    LogEmailNotifier,
    SlackNotifier,
    SnsEmailNotifier,
    build_notifiers,
    sync_email_subscriptions,
)
from finops.config import Settings
from finops.demo import DEMO_ACCOUNT_ID, generate_demo_dataset, load_dataset
from finops.demo.alerts import seed_demo_alerts
from finops.domain import AccountSettings, Alert, AlertType, Budget, Severity
from finops.repositories.memory import create_memory_repositories
from finops.secrets import InMemorySecretStore

NOW = datetime(2026, 10, 11, 9, 0, tzinfo=UTC)
HOOK = "https://hooks.slack.com/services/T/B/X"


class Recorder:
    channel = "test"

    def __init__(self, fail=False):
        self.sent: list[Alert] = []
        self.fail = fail

    def send(self, alert, account, settings):
        if self.fail:
            raise RuntimeError("smtp down")
        self.sent.append(alert)
        return True


@pytest.fixture
def demo():
    repos = create_memory_repositories()
    ds = generate_demo_dataset(today=NOW.date(), now=NOW)
    load_dataset(repos, ds)
    return repos, repos.accounts.get(DEMO_ACCOUNT_ID), ds


def test_budget_forecast_alert_is_sent_once(demo):
    repos, account, _ = demo
    rec = Recorder()
    first = evaluate_alerts(repos, account, [rec], NOW)
    assert [a.dedup_key for a in first] == ["budget#2026-10#80#forecast"]
    assert first[0].type == AlertType.BUDGET_FORECAST and first[0].channels_sent == ["test"]
    assert evaluate_alerts(repos, account, [rec], NOW + timedelta(hours=1)) == []  # deduplicated
    assert len(rec.sent) == 1
    assert len(repos.alerts.list(account.id)) == 1


def test_actual_threshold_alerts_separately_from_forecast(demo):
    repos, account, _ = demo
    evaluate_alerts(repos, account, [], NOW)
    repos.budgets.put(Budget(account_id=account.id, monthly_limit_usd=Decimal(500)))
    raised = evaluate_alerts(repos, account, [], NOW)
    keys = {a.dedup_key for a in raised}
    assert keys == {"budget#2026-10#80#actual", "budget#2026-10#100#actual"}
    assert next(a for a in raised if "100" in a.dedup_key).severity == Severity.HIGH


def test_anomaly_alerts_with_lookback_and_backdating(demo):
    repos, account, ds = demo
    assert not any(
        a.type == AlertType.ANOMALY for a in evaluate_alerts(repos, account, [], NOW)
    )  # spike is 18 days old: outside the default 3-day lookback
    raised = evaluate_alerts(
        repos, account, [], NOW, anomaly_lookback_days=30, backdate_anomalies=True
    )
    spike_day = datetime.fromisoformat(ds.summary["spike_day"]).date()
    total = next(a for a in raised if a.dedup_key == f"anomaly#{spike_day}#Total")
    assert total.severity == Severity.HIGH and "3 standard deviations" in total.message
    assert total.created_at.date() == spike_day + timedelta(days=1)
    assert {a.data["scope"] for a in raised} >= {"Total", "Amazon Elastic Compute Cloud - Compute"}


def test_failing_channel_does_not_block_others(demo):
    repos, account, _ = demo
    good = Recorder()
    raised = evaluate_alerts(repos, account, [Recorder(fail=True), good], NOW)
    assert raised[0].channels_sent == ["test (failed)", "test"]
    assert len(good.sent) == 1


def test_no_budget_no_budget_alerts():
    repos = create_memory_repositories()
    ds = generate_demo_dataset(today=NOW.date(), now=NOW)
    load_dataset(repos, ds)
    repos.budgets._s.budgets.clear()  # type: ignore[attr-defined]
    assert evaluate_alerts(repos, ds.account, [], NOW) == []


def test_seed_demo_alerts(demo):
    repos, _, _ = demo
    seed_demo_alerts(repos, [LogEmailNotifier()])
    alerts = repos.alerts.list(DEMO_ACCOUNT_ID)
    assert {a.type for a in alerts} == {AlertType.BUDGET_FORECAST, AlertType.ANOMALY}
    assert all("email" in a.channels_sent for a in alerts)
    seed_demo_alerts(create_memory_repositories(), [])  # no demo account: no-op


# ---------------------------------------------------------------- channels


def _alert():
    return Alert(
        account_id="acc1",
        type=AlertType.ANOMALY,
        severity=Severity.HIGH,
        title="Cost anomaly: Total",
        message="m",
        dedup_key="k",
    )


def test_sns_email_publishes_with_account_attribute(demo):
    _, account, _ = demo
    with mock_aws():
        sns = boto3.client("sns", region_name="ap-south-1")
        topic = sns.create_topic(Name="finops-alerts")["TopicArn"]
        settings = AccountSettings(account_id=account.id, alert_emails=["ops@example.com"])
        n = SnsEmailNotifier(sns, topic)
        assert n.send(_alert(), account, settings) is True
        assert (
            n.send(_alert(), account, settings.model_copy(update={"email_enabled": False})) is False
        )
        assert SnsEmailNotifier(sns, "").send(_alert(), account, settings) is False


def test_email_subscriptions_use_filter_policy():
    with mock_aws():
        sns = boto3.client("sns", region_name="ap-south-1")
        topic = sns.create_topic(Name="finops-alerts")["TopicArn"]
        sync_email_subscriptions(sns, topic, "acc1", ["A@example.com", "b@example.com"])
        subs = sns.list_subscriptions_by_topic(TopicArn=topic)["Subscriptions"]
        assert sorted(s["Endpoint"] for s in subs) == ["a@example.com", "b@example.com"]
        attrs = sns.get_subscription_attributes(SubscriptionArn=subs[0]["SubscriptionArn"])
        assert json.loads(attrs["Attributes"]["FilterPolicy"]) == {"account_id": ["acc1"]}

        sync_email_subscriptions(sns, topic, "acc1", ["a@example.com"])
        subs = sns.list_subscriptions_by_topic(TopicArn=topic)["Subscriptions"]
        assert [s["Endpoint"] for s in subs] == ["a@example.com"]


def test_slack_notifier(demo):
    _, account, _ = demo
    secrets = InMemorySecretStore()
    secrets.put("/finops/slack-webhook/demo", HOOK)
    calls = []
    n = SlackNotifier(
        secrets, http_post=lambda url, body: calls.append((url, json.loads(body))) or 200
    )
    settings = AccountSettings(
        account_id=account.id,
        slack_enabled=True,
        slack_webhook_ssm_param="/finops/slack-webhook/demo",
    )
    assert n.send(_alert(), account, settings) is True
    url, payload = calls[0]
    assert url == HOOK and "Cost anomaly" in payload["blocks"][0]["text"]["text"]
    assert n.send(_alert(), account, settings.model_copy(update={"slack_enabled": False})) is False

    failing = SlackNotifier(secrets, http_post=lambda url, body: 500)
    with pytest.raises(RuntimeError):
        failing.send(_alert(), account, settings)
    secrets.put("/finops/slack-webhook/demo", "https://evil.example/hook")
    with pytest.raises(ValueError):
        n.send(_alert(), account, settings)
    secrets.delete("/finops/slack-webhook/demo")
    assert n.send(_alert(), account, settings) is False


def test_build_notifiers():
    demo_channels = build_notifiers(Settings(demo_mode=True), InMemorySecretStore())
    assert [type(n) for n in demo_channels] == [LogEmailNotifier, SlackNotifier]
    with mock_aws():
        live = build_notifiers(
            Settings(demo_mode=False, alerts_topic_arn="arn:x"), InMemorySecretStore()
        )
    assert isinstance(live[0], SnsEmailNotifier)
