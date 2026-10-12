from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from finops.domain import (
    Account,
    AccountSettings,
    Budget,
    DailyCost,
    Finding,
    ResourceType,
    Severity,
    to_money,
)

ARN = "arn:aws:iam::123456789012:role/FinOpsReadOnly"


def test_money_is_rounded_decimal_and_serialises_as_number():
    cost = DailyCost(
        account_id="a", date="2026-10-01", service="S", region="r", amount_usd=0.1 + 0.2
    )
    assert cost.amount_usd == Decimal("0.3000")
    assert cost.model_dump(mode="json")["amount_usd"] == 0.3


def test_to_money_avoids_float_noise():
    assert to_money(1.1) == Decimal("1.1000")
    assert to_money("2.123456") == Decimal("2.1235")


def test_account_derives_aws_account_id_and_generates_external_id():
    a = Account(name="Prod", role_arn=ARN)
    b = Account(name="Prod", role_arn=ARN)
    assert a.aws_account_id == "123456789012"
    assert a.external_id != b.external_id  # unique per account
    assert a.id != b.id


@pytest.mark.parametrize(
    "arn",
    ["arn:aws:iam::12345:role/X", "arn:aws:iam::123456789012:user/X", "not-an-arn", ""],
)
def test_account_rejects_invalid_role_arn(arn):
    with pytest.raises(ValidationError):
        Account(name="x", role_arn=arn)


def test_datetimes_must_be_timezone_aware_and_are_normalised_to_utc():
    ist = timezone(timedelta(hours=5, minutes=30))
    a = Account(name="x", role_arn=ARN, created_at=datetime(2026, 10, 1, 12, 0, tzinfo=ist))
    assert a.created_at == datetime(2026, 10, 1, 6, 30, tzinfo=UTC)
    assert a.created_at.tzinfo == UTC
    with pytest.raises(ValidationError):
        Account(name="x", role_arn=ARN, created_at=datetime(2026, 10, 1))


def test_budget_thresholds_are_sorted_deduplicated_and_validated():
    assert Budget(account_id="a", monthly_limit_usd=100, thresholds=[100, 80, 80]).thresholds == [
        80,
        100,
    ]
    with pytest.raises(ValidationError):
        Budget(account_id="a", monthly_limit_usd=100, thresholds=[0])
    with pytest.raises(ValidationError):
        Budget(account_id="a", monthly_limit_usd=0)


def test_settings_defaults_and_email_validation():
    s = AccountSettings(account_id="a")
    assert s.required_tags == ["Project", "Owner", "Environment"]
    assert s.email_enabled and not s.slack_enabled
    with pytest.raises(ValidationError):
        AccountSettings(account_id="a", alert_emails=["not-an-email"])


def test_finding_id_is_deterministic():
    f = Finding(
        account_id="a",
        rule_id="ebs-unattached",
        resource_id="vol-1",
        resource_type=ResourceType.EBS_VOLUME,
        region="ap-south-1",
        severity=Severity.MEDIUM,
        title="t",
        detail="d",
        recommendation="r",
        est_monthly_savings_usd=5,
    )
    assert f.id == "ebs-unattached#vol-1"
    assert f.model_dump(mode="json")["id"] == "ebs-unattached#vol-1"
