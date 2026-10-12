from unittest.mock import patch

import pytest

import handler as collector
from finops.config import get_settings
from finops.domain import Account, AccountStatus, CollectionRun, RunStatus, RunTrigger
from finops.repositories.memory import create_memory_repositories

ARN = "arn:aws:iam::123456789012:role/FinOpsReadOnly"
EVENT = {"source": "aws.events", "detail-type": "Scheduled Event"}


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def repos(monkeypatch):
    monkeypatch.setenv("DEMO_MODE", "false")
    repos = create_memory_repositories()
    repos.accounts.put(Account(id="ok", name="A", role_arn=ARN, status=AccountStatus.CONNECTED))
    repos.accounts.put(Account(id="new", name="B", role_arn=ARN, status=AccountStatus.PENDING))
    repos.accounts.put(Account(id="gone", name="C", role_arn=ARN, status=AccountStatus.CONNECTED))
    monkeypatch.setattr(collector, "build_repositories", lambda settings: repos)
    monkeypatch.setattr(collector, "_secret_store", lambda region: None)
    monkeypatch.setattr(collector, "build_notifiers", lambda settings, secrets: [])
    monkeypatch.setattr(
        collector, "evaluate_alerts", lambda repos, account, notifiers, now: ["alert"]
    )
    return repos


def fake_session_factory(home_region):
    def factory(account):
        if account.id == "gone":
            raise RuntimeError("AccessDenied: not authorized to perform sts:AssumeRole")
        return object()

    return factory


def fake_collect(account, repos, session, *, trigger, options):
    return CollectionRun(
        account_id=account.id, trigger=trigger, status=RunStatus.SUCCEEDED, cost_explorer_calls=5
    )


def test_demo_mode_skips(monkeypatch):
    monkeypatch.setenv("DEMO_MODE", "true")
    result = collector.handler(EVENT, None)
    assert result["status"] == "skipped"


def test_collects_connected_accounts_and_isolates_failures(repos, monkeypatch):
    monkeypatch.setattr(collector, "default_session_factory", fake_session_factory)
    with patch.object(collector, "collect_account", side_effect=fake_collect) as collect:
        result = collector.handler(EVENT, None)

    by_id = {r["account_id"]: r for r in result["results"]}
    assert set(by_id) == {"ok", "gone"}  # pending account skipped
    assert by_id["ok"]["status"] == "succeeded"
    assert by_id["ok"]["cost_explorer_calls"] == 5
    assert by_id["ok"]["alerts_raised"] == 1
    assert by_id["gone"]["status"] == "assume_role_failed"
    assert repos.accounts.get("gone").status == AccountStatus.ERROR
    assert collect.call_count == 1


def test_single_account_event(repos, monkeypatch):
    monkeypatch.setattr(collector, "default_session_factory", fake_session_factory)
    with patch.object(collector, "collect_account", side_effect=fake_collect):
        result = collector.handler({"account_id": "ok"}, None)
    assert [r["account_id"] for r in result["results"]] == ["ok"]


def test_manual_trigger_from_api(repos, monkeypatch):
    monkeypatch.setattr(collector, "default_session_factory", fake_session_factory)
    with patch.object(collector, "collect_account", side_effect=fake_collect) as collect:
        collector.handler({"account_id": "ok", "trigger": "manual"}, None)
        collector.handler({}, None)
    triggers = [c.kwargs["trigger"] for c in collect.call_args_list]
    assert triggers == [RunTrigger.MANUAL, RunTrigger.SCHEDULE]
