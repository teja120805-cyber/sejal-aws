import os

# moto otherwise loads thousands of public AMIs per region (~4 s each); we don't need them.
os.environ.setdefault("MOTO_EC2_LOAD_DEFAULT_AMIS", "false")

import boto3
import pytest
from moto import mock_aws

from finops.config import get_settings
from finops.repositories.base import Repositories
from finops.repositories.dynamodb import create_dynamodb_repositories, create_table
from finops.repositories.memory import create_memory_repositories


@pytest.fixture(autouse=True)
def _demo_env(monkeypatch: pytest.MonkeyPatch):
    """Every test starts in demo mode with fake AWS credentials, so nothing can reach real AWS."""
    monkeypatch.setenv("DEMO_MODE", "true")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-south-1")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def ddb_table():
    with mock_aws():
        ddb = boto3.resource("dynamodb", region_name="ap-south-1")
        yield create_table(ddb, "finops-test")


@pytest.fixture(params=["memory", "dynamodb"])
def repos(request: pytest.FixtureRequest) -> Repositories:
    """Contract tests run once per implementation, proving they behave identically."""
    if request.param == "memory":
        return create_memory_repositories()
    return create_dynamodb_repositories(request.getfixturevalue("ddb_table"))
