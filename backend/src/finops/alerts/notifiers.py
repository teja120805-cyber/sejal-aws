"""Alert delivery channels: SNS email and Slack incoming webhooks.

Email goes through ONE platform SNS topic. Each account's recipients subscribe with a filter
policy on the `account_id` message attribute, so people only receive their own account's alerts
and no per-account topics are needed.
"""

import json
import logging
import urllib.request
from collections.abc import Callable
from typing import Any, Protocol

from finops.config import Settings
from finops.domain import Account, AccountSettings, Alert, Severity
from finops.secrets import SecretStore

logger = logging.getLogger(__name__)

SEVERITY_EMOJI = {
    Severity.HIGH: ":red_circle:",
    Severity.MEDIUM: ":large_orange_circle:",
    Severity.LOW: ":large_blue_circle:",
}


class Notifier(Protocol):
    channel: str

    def send(self, alert: Alert, account: Account, settings: AccountSettings) -> bool:
        """Deliver the alert. Returns True if sent, False if the channel is disabled for this
        account. Raises on delivery failure."""
        ...


def format_text(alert: Alert, account: Account) -> str:
    header = f"[{alert.severity.value.upper()}] {alert.title}"
    return f"{header}\nAccount: {account.name}\n\n{alert.message}"


class SnsEmailNotifier:
    channel = "email"

    def __init__(self, sns: Any, topic_arn: str) -> None:
        self.sns = sns
        self.topic_arn = topic_arn

    def send(self, alert: Alert, account: Account, settings: AccountSettings) -> bool:
        if not settings.email_enabled or not settings.alert_emails or not self.topic_arn:
            return False
        self.sns.publish(
            TopicArn=self.topic_arn,
            Subject=f"FinOps: {alert.title}"[:100],
            Message=format_text(alert, account),
            MessageAttributes={"account_id": {"DataType": "String", "StringValue": account.id}},
        )
        return True


class LogEmailNotifier:
    """Demo mode: no SNS, emails are only logged (and shown in alert history)."""

    channel = "email"

    def send(self, alert: Alert, account: Account, settings: AccountSettings) -> bool:
        if not settings.email_enabled or not settings.alert_emails:
            return False
        logger.info("[demo email to %s] %s", ", ".join(settings.alert_emails), alert.title)
        return True


HttpPost = Callable[[str, bytes], int]


def _http_post(url: str, body: bytes) -> int:
    request = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        return int(response.status)


class SlackNotifier:
    channel = "slack"

    def __init__(self, secrets: SecretStore, http_post: HttpPost = _http_post) -> None:
        self.secrets = secrets
        self.http_post = http_post

    def send(self, alert: Alert, account: Account, settings: AccountSettings) -> bool:
        if not settings.slack_enabled or not settings.slack_webhook_ssm_param:
            return False
        url = self.secrets.get(settings.slack_webhook_ssm_param)
        if not url:
            return False
        if not url.startswith("https://hooks.slack.com/"):
            raise ValueError("Slack webhook URL must start with https://hooks.slack.com/")
        payload = {
            "text": f"{alert.title} ({account.name})",
            "blocks": [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"{SEVERITY_EMOJI[alert.severity]} *{alert.title}*\n"
                        f"_Account: {account.name}_\n{alert.message}",
                    },
                }
            ],
        }
        status = self.http_post(url, json.dumps(payload).encode())
        if status >= 300:
            raise RuntimeError(f"Slack returned HTTP {status}")
        return True


def build_notifiers(settings: Settings, secrets: SecretStore) -> list[Notifier]:
    slack = SlackNotifier(secrets)  # real in demo mode too: paste a webhook to see live alerts
    if settings.demo_mode:
        return [LogEmailNotifier(), slack]
    import boto3

    sns = boto3.client("sns", region_name=settings.aws_region)
    return [SnsEmailNotifier(sns, settings.alerts_topic_arn), slack]


def sync_email_subscriptions(sns: Any, topic_arn: str, account_id: str, emails: list[str]) -> None:
    """Subscribe new addresses (AWS emails them a confirmation link) and remove dropped ones."""
    wanted = {e.lower() for e in emails}
    existing: dict[str, str] = {}
    paginator = sns.get_paginator("list_subscriptions_by_topic")
    for page in paginator.paginate(TopicArn=topic_arn):
        for sub in page["Subscriptions"]:
            if sub["Protocol"] != "email" or not sub["SubscriptionArn"].startswith("arn:"):
                continue  # pending confirmations have no ARN yet
            attrs = sns.get_subscription_attributes(SubscriptionArn=sub["SubscriptionArn"])
            policy = json.loads(attrs["Attributes"].get("FilterPolicy", "{}"))
            if policy.get("account_id") == [account_id]:
                existing[sub["Endpoint"].lower()] = sub["SubscriptionArn"]

    for email in wanted - existing.keys():
        sns.subscribe(
            TopicArn=topic_arn,
            Protocol="email",
            Endpoint=email,
            Attributes={"FilterPolicy": json.dumps({"account_id": [account_id]})},
        )
    for email, arn in existing.items():
        if email not in wanted:
            sns.unsubscribe(SubscriptionArn=arn)
