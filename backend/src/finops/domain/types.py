"""Shared field types for the domain models."""

from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated

from pydantic import AfterValidator, AwareDatetime, PlainSerializer

MONEY_PLACES = Decimal("0.0001")


def _quantize(value: Decimal) -> Decimal:
    return value.quantize(MONEY_PLACES, rounding=ROUND_HALF_UP)


def to_money(value: Decimal | float | int | str) -> Decimal:
    """Convert any number to a 4-decimal-place Decimal (floats via repr to avoid binary noise)."""
    if isinstance(value, float):
        value = repr(float(value))  # float() also normalises numpy floats
    return _quantize(Decimal(value))


# USD amounts. Stored as Decimal (DynamoDB rejects floats; floats also accumulate rounding error),
# rounded to 4 dp, and emitted as plain JSON numbers so the frontend gets numbers, not strings.
Money = Annotated[
    Decimal,
    AfterValidator(_quantize),
    PlainSerializer(float, return_type=float, when_used="json"),
]

# Timezone-aware datetime, always normalised to UTC.
UtcDatetime = Annotated[AwareDatetime, AfterValidator(lambda d: d.astimezone(UTC))]


def utcnow() -> datetime:
    return datetime.now(UTC)
