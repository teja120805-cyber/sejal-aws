"""Domain models. Pure data + validation: no AWS or database code lives here."""

import datetime as dt
import re
import uuid
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator

from finops.domain.enums import (
    AccountStatus,
    AlertType,
    FindingStatus,
    ResourceType,
    RunStatus,
    RunTrigger,
    Severity,
)
from finops.domain.types import Money, UtcDatetime, utcnow

ROLE_ARN_RE = re.compile(r"^arn:aws:iam::(\d{12}):role/[\w+=,.@/-]{1,512}$")
DEFAULT_REQUIRED_TAGS = ["Project", "Owner", "Environment"]


def new_id() -> str:
    return uuid.uuid4().hex


def new_external_id() -> str:
    """ExternalId for AssumeRole. Random and per-account, so a leaked role ARN alone is useless."""
    return str(uuid.uuid4())


class DomainModel(BaseModel):
    # extra="ignore": DB items carry key attributes (PK/SK) that are not model fields.
    model_config = ConfigDict(extra="ignore", validate_assignment=True)


class Account(DomainModel):
    id: str = Field(default_factory=new_id)
    name: str = Field(min_length=1, max_length=100)
    role_arn: str
    external_id: str = Field(default_factory=new_external_id)
    remediation_role_arn: str | None = None
    regions: list[str] = Field(default_factory=list)
    status: AccountStatus = AccountStatus.PENDING
    status_message: str | None = None
    created_at: UtcDatetime = Field(default_factory=utcnow)
    last_tested_at: UtcDatetime | None = None
    last_refresh_at: UtcDatetime | None = None
    last_collected_at: UtcDatetime | None = None

    @field_validator("role_arn", "remediation_role_arn")
    @classmethod
    def _valid_role_arn(cls, value: str | None) -> str | None:
        if value is not None and not ROLE_ARN_RE.match(value):
            raise ValueError("must look like arn:aws:iam::123456789012:role/RoleName")
        return value

    @computed_field  # type: ignore[prop-decorator]
    @property
    def aws_account_id(self) -> str:
        match = ROLE_ARN_RE.match(self.role_arn)
        assert match  # guaranteed by the validator
        return match.group(1)


class DailyCost(DomainModel):
    """One day of unblended cost for one service in one region (Cost Explorer granularity)."""

    account_id: str
    date: dt.date
    service: str
    region: str  # "global" for services such as CloudFront
    amount_usd: Money


class TagCost(DomainModel):
    """One day of cost for one tag value. tag_value == "" means untagged spend."""

    account_id: str
    date: dt.date
    tag_key: str
    tag_value: str
    amount_usd: Money


class CostForecast(DomainModel):
    """Cost Explorer's month-end forecast. Stored by the collector, never fetched on page load."""

    account_id: str
    month: str = Field(pattern=r"^\d{4}-\d{2}$")
    forecast_usd: Money
    lower_usd: Money | None = None
    upper_usd: Money | None = None
    generated_at: UtcDatetime = Field(default_factory=utcnow)


class Resource(DomainModel):
    account_id: str
    resource_id: str
    type: ResourceType
    region: str
    name: str | None = None
    state: str | None = None
    tags: dict[str, str] = Field(default_factory=dict)
    created_at: UtcDatetime | None = None
    # Type-specific details, e.g. instance_type, volume_type, size_gb, attached_instance_id.
    attributes: dict[str, Any] = Field(default_factory=dict)
    # Summarised CloudWatch metrics, e.g. cpu_avg_7d, cpu_max_7d, db_connections_max_7d.
    metrics: dict[str, float] = Field(default_factory=dict)
    est_monthly_cost_usd: Money = Decimal("0")
    last_seen_at: UtcDatetime = Field(default_factory=utcnow)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def key(self) -> str:
        return f"{self.type.value}#{self.region}#{self.resource_id}"


class Finding(DomainModel):
    account_id: str
    rule_id: str
    resource_id: str
    resource_type: ResourceType
    region: str
    resource_name: str | None = None
    severity: Severity
    title: str
    detail: str
    recommendation: str
    current_monthly_cost_usd: Money | None = None
    est_monthly_savings_usd: Money
    status: FindingStatus = FindingStatus.OPEN
    first_seen: UtcDatetime = Field(default_factory=utcnow)
    last_seen: UtcDatetime = Field(default_factory=utcnow)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def id(self) -> str:
        # Deterministic: re-running the rules updates the same finding instead of duplicating it.
        return f"{self.rule_id}#{self.resource_id}"


class Budget(DomainModel):
    account_id: str
    monthly_limit_usd: Money = Field(gt=0)
    thresholds: list[int] = Field(default_factory=lambda: [80, 100])

    @field_validator("thresholds")
    @classmethod
    def _valid_thresholds(cls, value: list[int]) -> list[int]:
        if not value or any(not 1 <= t <= 200 for t in value):
            raise ValueError("thresholds must be percentages between 1 and 200")
        return sorted(set(value))


class Alert(DomainModel):
    id: str = Field(default_factory=new_id)
    account_id: str
    type: AlertType
    severity: Severity
    title: str
    message: str
    dedup_key: str
    data: dict[str, Any] = Field(default_factory=dict)
    channels_sent: list[str] = Field(default_factory=list)
    created_at: UtcDatetime = Field(default_factory=utcnow)


class AccountSettings(DomainModel):
    account_id: str
    required_tags: list[str] = Field(default_factory=lambda: list(DEFAULT_REQUIRED_TAGS))
    # Per-rule overrides, e.g. {"ec2-idle": {"cpu_avg_pct": 5, "cpu_max_pct": 20}}
    rule_thresholds: dict[str, dict[str, float]] = Field(default_factory=dict)
    email_enabled: bool = True
    alert_emails: list[str] = Field(default_factory=list)
    slack_enabled: bool = False
    # Name of the SSM SecureString holding the webhook URL - never the URL itself.
    slack_webhook_ssm_param: str | None = None

    @field_validator("alert_emails")
    @classmethod
    def _valid_emails(cls, value: list[str]) -> list[str]:
        for email in value:
            if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
                raise ValueError(f"invalid email address: {email}")
        return value


class CollectionRun(DomainModel):
    id: str = Field(default_factory=new_id)
    account_id: str
    trigger: RunTrigger
    status: RunStatus = RunStatus.RUNNING
    started_at: UtcDatetime = Field(default_factory=utcnow)
    finished_at: UtcDatetime | None = None
    cost_explorer_calls: int = 0
    resources_collected: int = 0
    errors: list[str] = Field(default_factory=list)
