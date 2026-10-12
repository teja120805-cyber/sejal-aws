"""In-memory repositories for demo mode and tests.

Objects are deep-copied on the way in and out so callers can't mutate stored state by accident,
which mimics a real database and keeps behaviour identical to the DynamoDB implementation.
"""

import datetime as dt
import threading
from collections.abc import Iterable
from datetime import datetime, timedelta

from pydantic import BaseModel

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
from finops.repositories.base import NotFoundError, Repositories


def _copy[M: BaseModel](model: M) -> M:
    return model.model_copy(deep=True)


class _Store:
    """Shared state for all in-memory repositories (so deleting an account cascades)."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.accounts: dict[str, Account] = {}
        self.daily_costs: dict[tuple[str, dt.date, str, str], DailyCost] = {}
        self.tag_costs: dict[tuple[str, str, dt.date, str], TagCost] = {}
        self.forecasts: dict[tuple[str, str], CostForecast] = {}
        self.resources: dict[str, dict[str, Resource]] = {}
        self.findings: dict[str, dict[str, Finding]] = {}
        self.budgets: dict[str, Budget] = {}
        self.alerts: dict[str, list[Alert]] = {}
        self.dedup: dict[tuple[str, str], datetime | None] = {}
        self.settings: dict[str, AccountSettings] = {}
        self.runs: dict[str, dict[str, CollectionRun]] = {}


class InMemoryAccountRepository:
    def __init__(self, store: _Store) -> None:
        self._s = store

    def get(self, account_id: str) -> Account | None:
        account = self._s.accounts.get(account_id)
        return _copy(account) if account else None

    def list(self) -> list[Account]:
        return [_copy(a) for a in sorted(self._s.accounts.values(), key=lambda a: a.name)]

    def put(self, account: Account) -> None:
        self._s.accounts[account.id] = _copy(account)

    def delete(self, account_id: str) -> None:
        s = self._s
        with s.lock:
            s.accounts.pop(account_id, None)
            for mapping in (s.daily_costs, s.tag_costs, s.forecasts, s.dedup):
                for key in [k for k in mapping if k[0] == account_id]:
                    del mapping[key]
            for per_account in (s.resources, s.findings, s.budgets, s.alerts, s.settings, s.runs):
                per_account.pop(account_id, None)

    def try_start_refresh(self, account_id: str, now: datetime, cooldown: timedelta) -> bool:
        with self._s.lock:
            account = self._s.accounts.get(account_id)
            if account is None:
                raise NotFoundError(account_id)
            last = account.last_refresh_at
            if last is not None and last > now - cooldown:
                return False
            account.last_refresh_at = now
            return True


class InMemoryCostRepository:
    def __init__(self, store: _Store) -> None:
        self._s = store

    def put_daily_costs(self, costs: Iterable[DailyCost]) -> None:
        for c in costs:
            self._s.daily_costs[(c.account_id, c.date, c.service, c.region)] = _copy(c)

    def query_daily_costs(self, account_id: str, start: dt.date, end: dt.date) -> list[DailyCost]:
        rows = [
            _copy(c)
            for (acc, day, _, _), c in self._s.daily_costs.items()
            if acc == account_id and start <= day <= end
        ]
        return sorted(rows, key=lambda c: (c.date, c.service, c.region))

    def put_tag_costs(self, costs: Iterable[TagCost]) -> None:
        for c in costs:
            self._s.tag_costs[(c.account_id, c.tag_key, c.date, c.tag_value)] = _copy(c)

    def query_tag_costs(
        self, account_id: str, tag_key: str, start: dt.date, end: dt.date
    ) -> list[TagCost]:
        rows = [
            _copy(c)
            for (acc, key, day, _), c in self._s.tag_costs.items()
            if acc == account_id and key == tag_key and start <= day <= end
        ]
        return sorted(rows, key=lambda c: (c.date, c.tag_value))

    def put_forecast(self, forecast: CostForecast) -> None:
        self._s.forecasts[(forecast.account_id, forecast.month)] = _copy(forecast)

    def get_forecast(self, account_id: str, month: str) -> CostForecast | None:
        f = self._s.forecasts.get((account_id, month))
        return _copy(f) if f else None


class InMemoryResourceRepository:
    def __init__(self, store: _Store) -> None:
        self._s = store

    def list(self, account_id: str, type: ResourceType | None = None) -> list[Resource]:
        items = self._s.resources.get(account_id, {})
        rows = [_copy(r) for r in items.values() if type is None or r.type == type]
        return sorted(rows, key=lambda r: r.key)

    def replace_inventory(self, account_id: str, resources: Iterable[Resource]) -> None:
        self._s.resources[account_id] = {r.key: _copy(r) for r in resources}


class InMemoryFindingRepository:
    def __init__(self, store: _Store) -> None:
        self._s = store

    def list(self, account_id: str, status: FindingStatus | None = None) -> list[Finding]:
        items = self._s.findings.get(account_id, {})
        rows = [_copy(f) for f in items.values() if status is None or f.status == status]
        return sorted(rows, key=lambda f: f.id)

    def get(self, account_id: str, finding_id: str) -> Finding | None:
        f = self._s.findings.get(account_id, {}).get(finding_id)
        return _copy(f) if f else None

    def put_many(self, findings: Iterable[Finding]) -> None:
        for f in findings:
            self._s.findings.setdefault(f.account_id, {})[f.id] = _copy(f)

    def update_status(self, account_id: str, finding_id: str, status: FindingStatus) -> Finding:
        f = self._s.findings.get(account_id, {}).get(finding_id)
        if f is None:
            raise NotFoundError(finding_id)
        f.status = status
        return _copy(f)


class InMemoryBudgetRepository:
    def __init__(self, store: _Store) -> None:
        self._s = store

    def get(self, account_id: str) -> Budget | None:
        b = self._s.budgets.get(account_id)
        return _copy(b) if b else None

    def put(self, budget: Budget) -> None:
        self._s.budgets[budget.account_id] = _copy(budget)


class InMemoryAlertRepository:
    def __init__(self, store: _Store) -> None:
        self._s = store

    def list(self, account_id: str, limit: int = 100) -> list[Alert]:
        rows = sorted(self._s.alerts.get(account_id, []), key=lambda a: a.created_at, reverse=True)
        return [_copy(a) for a in rows[:limit]]

    def put(self, alert: Alert) -> None:
        self._s.alerts.setdefault(alert.account_id, []).append(_copy(alert))

    def claim_dedup(self, account_id: str, key: str, expires_at: datetime | None = None) -> bool:
        with self._s.lock:
            if (account_id, key) in self._s.dedup:
                return False
            self._s.dedup[(account_id, key)] = expires_at
            return True


class InMemorySettingsRepository:
    def __init__(self, store: _Store) -> None:
        self._s = store

    def get(self, account_id: str) -> AccountSettings:
        s = self._s.settings.get(account_id)
        return _copy(s) if s else AccountSettings(account_id=account_id)

    def put(self, settings: AccountSettings) -> None:
        self._s.settings[settings.account_id] = _copy(settings)


class InMemoryRunRepository:
    def __init__(self, store: _Store) -> None:
        self._s = store

    def put(self, run: CollectionRun) -> None:
        self._s.runs.setdefault(run.account_id, {})[run.id] = _copy(run)

    def list(self, account_id: str, limit: int = 20) -> list[CollectionRun]:
        rows = sorted(
            self._s.runs.get(account_id, {}).values(), key=lambda r: r.started_at, reverse=True
        )
        return [_copy(r) for r in rows[:limit]]


def create_memory_repositories() -> Repositories:
    store = _Store()
    return Repositories(
        accounts=InMemoryAccountRepository(store),
        costs=InMemoryCostRepository(store),
        resources=InMemoryResourceRepository(store),
        findings=InMemoryFindingRepository(store),
        budgets=InMemoryBudgetRepository(store),
        alerts=InMemoryAlertRepository(store),
        settings=InMemorySettingsRepository(store),
        runs=InMemoryRunRepository(store),
    )
