from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator

from finops.alerts import month_forecast
from finops.alerts.notifiers import sync_email_subscriptions
from finops.analytics.budgets import evaluate_budget
from finops.api.auth import CurrentUser
from finops.api.deps import AccountDep, ReposDep, SettingsDep, StateDep
from finops.domain import AccountSettings, Alert, AlertType, Budget, Severity, utcnow
from finops.rules import RULES_BY_ID, refresh_findings
from finops.secrets import slack_param_name

router = APIRouter(prefix="/accounts/{account_id}", tags=["budgets & alerts"])


class BudgetIn(BaseModel):
    monthly_limit_usd: Annotated[Decimal, Field(gt=0, le=10_000_000)]
    thresholds: list[int] = [80, 100]


@router.get("/budget")
def get_budget(account: AccountDep, repos: ReposDep, _: CurrentUser) -> dict[str, object]:
    budget = repos.budgets.get(account.id)
    if budget is None:
        return {"budget": None, "status": None}
    today = utcnow().date()
    mtd, forecast = month_forecast(repos, account.id, today)
    s = evaluate_budget(budget, today.strftime("%Y-%m"), mtd, forecast)
    return {
        "budget": budget,
        "status": {
            "month": s.month,
            "actual_usd": float(s.actual),
            "forecast_usd": float(s.forecast),
            "actual_pct": s.actual_pct,
            "forecast_pct": s.forecast_pct,
            "crossed": [{"threshold": c.threshold, "kind": c.kind} for c in s.crossed],
        },
    }


@router.put("/budget")
def put_budget(body: BudgetIn, account: AccountDep, repos: ReposDep, _: CurrentUser) -> Budget:
    budget = Budget(
        account_id=account.id, monthly_limit_usd=body.monthly_limit_usd, thresholds=body.thresholds
    )
    repos.budgets.put(budget)
    return budget


@router.get("/alerts")
def list_alerts(
    account: AccountDep, repos: ReposDep, _: CurrentUser, limit: int = Query(100, ge=1, le=500)
) -> list[Alert]:
    return repos.alerts.list(account.id, limit=limit)


@router.post("/alerts/test")
def test_alert(account: AccountDep, state: StateDep, _: CurrentUser) -> dict[str, object]:
    """Send a test message through every enabled channel (not stored in history)."""
    settings = state.repos.settings.get(account.id)
    alert = Alert(
        account_id=account.id,
        type=AlertType.ANOMALY,
        severity=Severity.LOW,
        title="Test notification",
        message="If you can read this, FinOps alerts are configured correctly.",
        dedup_key="test",
    )
    results: dict[str, str] = {}
    for n in state.notifiers:
        try:
            results[n.channel] = "sent" if n.send(alert, account, settings) else "disabled"
        except Exception as err:
            results[n.channel] = f"failed: {err}"
    return {"results": results}


# ---------------------------------------------------------------- settings


class SettingsOut(BaseModel):
    account_id: str
    required_tags: list[str]
    rule_thresholds: dict[str, dict[str, float]]
    email_enabled: bool
    alert_emails: list[str]
    slack_enabled: bool
    slack_configured: bool  # the webhook URL itself is never returned


class SettingsIn(BaseModel):
    required_tags: Annotated[list[str], Field(max_length=20)] | None = None
    rule_thresholds: dict[str, dict[str, float]] | None = None
    email_enabled: bool | None = None
    alert_emails: Annotated[list[str], Field(max_length=20)] | None = None
    slack_enabled: bool | None = None
    slack_webhook_url: str | None = None  # write-only; "" removes it

    @field_validator("slack_webhook_url")
    @classmethod
    def _slack_url(cls, v: str | None) -> str | None:
        if v and not v.startswith("https://hooks.slack.com/"):
            raise ValueError("must be a Slack incoming webhook URL (https://hooks.slack.com/...)")
        return v

    @field_validator("required_tags")
    @classmethod
    def _tags(cls, v: list[str] | None) -> list[str] | None:
        if v is not None:
            v = [t.strip() for t in v if t.strip()]
        return v


def _out(s: AccountSettings) -> SettingsOut:
    return SettingsOut(
        account_id=s.account_id,
        required_tags=s.required_tags,
        rule_thresholds=s.rule_thresholds,
        email_enabled=s.email_enabled,
        alert_emails=s.alert_emails,
        slack_enabled=s.slack_enabled,
        slack_configured=bool(s.slack_webhook_ssm_param),
    )


@router.get("/settings")
def get_settings_(account: AccountDep, repos: ReposDep, _: CurrentUser) -> SettingsOut:
    return _out(repos.settings.get(account.id))


@router.put("/settings")
def put_settings(
    body: SettingsIn,
    account: AccountDep,
    state: StateDep,
    app_settings: SettingsDep,
    _: CurrentUser,
) -> SettingsOut:
    repos = state.repos
    current = repos.settings.get(account.id)
    if body.rule_thresholds is not None:
        for rule_id, values in body.rule_thresholds.items():
            rule = RULES_BY_ID.get(rule_id)
            unknown = set(values) - set(rule.default_thresholds) if rule else {rule_id}
            if unknown:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_ENTITY, f"unknown threshold(s): {sorted(unknown)}"
                )
    updates = body.model_dump(exclude_unset=True, exclude={"slack_webhook_url"})
    new = AccountSettings.model_validate(current.model_dump() | updates)

    if body.slack_webhook_url is not None:
        name = slack_param_name(app_settings.slack_webhook_ssm_prefix, account.id)
        if body.slack_webhook_url:
            state.secrets.put(name, body.slack_webhook_url)
            new.slack_webhook_ssm_param = name
        else:
            state.secrets.delete(name)
            new.slack_webhook_ssm_param = None
            new.slack_enabled = False

    repos.settings.put(new)
    if (
        body.alert_emails is not None
        and not app_settings.demo_mode
        and app_settings.alerts_topic_arn
    ):
        import boto3

        sns = boto3.client("sns", region_name=app_settings.aws_region)
        sync_email_subscriptions(sns, app_settings.alerts_topic_arn, account.id, new.alert_emails)
    if body.rule_thresholds is not None or body.required_tags is not None:
        refresh_findings(repos, account.id, utcnow())  # thresholds changed: re-evaluate (free)
    return _out(new)
