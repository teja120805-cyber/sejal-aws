"""Shared FastAPI dependencies."""

from dataclasses import dataclass
from functools import lru_cache
from typing import Annotated

from fastapi import Depends, HTTPException, status

from finops.alerts.notifiers import Notifier, build_notifiers
from finops.config import Settings, get_settings
from finops.domain import Account
from finops.repositories import Repositories, build_repositories
from finops.secrets import InMemorySecretStore, SecretStore, SsmSecretStore

SettingsDep = Annotated[Settings, Depends(get_settings)]


@dataclass
class AppState:
    """Process-wide singletons (one per Lambda container / uvicorn process)."""

    repos: Repositories
    secrets: SecretStore
    notifiers: list[Notifier]


@lru_cache
def _build_state(demo_mode: bool) -> AppState:
    settings = get_settings()
    if demo_mode:
        secrets: SecretStore = InMemorySecretStore()
    else:
        import boto3

        secrets = SsmSecretStore(boto3.client("ssm", region_name=settings.aws_region))
    notifiers = build_notifiers(settings, secrets)
    repos = build_repositories(settings)
    if demo_mode:
        from finops.demo.alerts import seed_demo_alerts

        seed_demo_alerts(repos, notifiers)
    return AppState(repos=repos, secrets=secrets, notifiers=notifiers)


def get_state(settings: SettingsDep) -> AppState:
    return _build_state(settings.demo_mode)


StateDep = Annotated[AppState, Depends(get_state)]


def get_repos(state: StateDep) -> Repositories:
    return state.repos


ReposDep = Annotated[Repositories, Depends(get_repos)]


def get_account(account_id: str, repos: ReposDep) -> Account:
    account = repos.accounts.get(account_id)
    if account is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"account {account_id!r} not found")
    return account


AccountDep = Annotated[Account, Depends(get_account)]
