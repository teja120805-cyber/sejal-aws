"""Repository interfaces.

All persistence goes through these Protocols. The API, collector, rules engine and alerting
never import boto3's DynamoDB client directly, so the in-memory implementation (demo mode, tests)
and the DynamoDB implementation are interchangeable. The same contract test suite runs against both.
"""

import datetime as dt
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from finops.domain import (
    Account,
    AccountSettings,
    Alert,
    Budget,
    CollectionRun,
    CostForecast,
    DailyCost,
    Finding,
    FindingStatus,
    Resource,
    ResourceType,
    TagCost,
)


class NotFoundError(LookupError):
    pass


class AccountRepository(Protocol):
    def get(self, account_id: str) -> Account | None: ...
    def list(self) -> list[Account]: ...
    def put(self, account: Account) -> None: ...
    def delete(self, account_id: str) -> None:
        """Delete the account and every record that belongs to it."""
        ...

    def try_start_refresh(self, account_id: str, now: datetime, cooldown: timedelta) -> bool:
        """Atomically set last_refresh_at=now, but only if the previous refresh is older than
        `cooldown`. Returns False if a refresh happened too recently. Raises NotFoundError."""
        ...


class CostRepository(Protocol):
    def put_daily_costs(self, costs: Iterable[DailyCost]) -> None: ...
    def query_daily_costs(self, account_id: str, start: dt.date, end: dt.date) -> list[DailyCost]:
        """Inclusive date range, sorted by (date, service, region)."""
        ...

    def put_tag_costs(self, costs: Iterable[TagCost]) -> None: ...
    def query_tag_costs(
        self, account_id: str, tag_key: str, start: dt.date, end: dt.date
    ) -> list[TagCost]: ...
    def put_forecast(self, forecast: CostForecast) -> None: ...
    def get_forecast(self, account_id: str, month: str) -> CostForecast | None: ...


class ResourceRepository(Protocol):
    def list(self, account_id: str, type: ResourceType | None = None) -> list[Resource]: ...
    def replace_inventory(self, account_id: str, resources: Iterable[Resource]) -> None:
        """Store a full inventory snapshot; resources not in it (deleted in AWS) are removed."""
        ...


class FindingRepository(Protocol):
    def list(self, account_id: str, status: FindingStatus | None = None) -> list[Finding]: ...
    def get(self, account_id: str, finding_id: str) -> Finding | None: ...
    def put_many(self, findings: Iterable[Finding]) -> None: ...
    def update_status(self, account_id: str, finding_id: str, status: FindingStatus) -> Finding:
        """Raises NotFoundError."""
        ...


class BudgetRepository(Protocol):
    def get(self, account_id: str) -> Budget | None: ...
    def put(self, budget: Budget) -> None: ...


class AlertRepository(Protocol):
    def list(self, account_id: str, limit: int = 100) -> list[Alert]:
        """Newest first."""
        ...

    def put(self, alert: Alert) -> None: ...
    def claim_dedup(self, account_id: str, key: str, expires_at: datetime | None = None) -> bool:
        """Atomically record that an alert with this key was sent. Returns False if it already
        was, so concurrent or repeated runs never send the same alert twice."""
        ...


class SettingsRepository(Protocol):
    def get(self, account_id: str) -> AccountSettings:
        """Returns defaults if nothing has been saved yet."""
        ...

    def put(self, settings: AccountSettings) -> None: ...


class RunRepository(Protocol):
    def put(self, run: CollectionRun) -> None: ...
    def list(self, account_id: str, limit: int = 20) -> list[CollectionRun]:
        """Newest first."""
        ...


@dataclass(frozen=True)
class Repositories:
    """Everything the application needs, bundled so it can be injected as one dependency."""

    accounts: AccountRepository
    costs: CostRepository
    resources: ResourceRepository
    findings: FindingRepository
    budgets: BudgetRepository
    alerts: AlertRepository
    settings: SettingsRepository
    runs: RunRepository
