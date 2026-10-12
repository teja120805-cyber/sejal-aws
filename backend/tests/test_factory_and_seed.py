import importlib.util
import json
from pathlib import Path

import boto3
from moto import mock_aws

from finops.config import Settings
from finops.demo import DEMO_ACCOUNT_ID
from finops.repositories import build_repositories
from finops.repositories.dynamodb import DynamoAccountRepository, create_table
from finops.repositories.memory import InMemoryAccountRepository

SEED_SCRIPT = Path(__file__).parents[1] / "scripts" / "seed_demo.py"


def load_seed_module():
    spec = importlib.util.spec_from_file_location("seed_demo", SEED_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_demo_mode_builds_seeded_in_memory_repositories():
    repos = build_repositories(Settings(demo_mode=True))
    assert isinstance(repos.accounts, InMemoryAccountRepository)
    assert repos.accounts.get(DEMO_ACCOUNT_ID) is not None
    assert len(repos.resources.list(DEMO_ACCOUNT_ID)) > 30


def test_non_demo_mode_uses_dynamodb():
    with mock_aws():
        repos = build_repositories(Settings(demo_mode=False, table_name="t"))
        assert isinstance(repos.accounts, DynamoAccountRepository)


def test_seed_script_exports_json(tmp_path, capsys):
    out = tmp_path / "demo.json"
    assert load_seed_module().main(["--json", str(out)]) == 0
    data = json.loads(out.read_text())
    assert data["summary"]["days"] == 90
    assert len(data["resources"]) == data["summary"]["resources"]
    assert isinstance(data["daily_costs"][0]["amount_usd"], float)


def test_seed_script_refuses_real_aws_without_confirmation(capsys):
    assert load_seed_module().main(["--dynamodb"]) == 2
    assert "refusing" in capsys.readouterr().err


def test_seed_script_loads_dynamodb():
    with mock_aws():
        code = load_seed_module().main(
            ["--dynamodb", "--create-table", "--table", "seeded", "--confirm-real-aws"]
        )
        assert code == 0
        table = boto3.resource("dynamodb", region_name="ap-south-1").Table("seeded")
        assert table.item_count >= 0
        assert table.get_item(Key={"PK": f"ACCOUNT#{DEMO_ACCOUNT_ID}", "SK": "META"})["Item"]


def test_create_table_enables_ttl():
    with mock_aws():
        ddb = boto3.resource("dynamodb", region_name="ap-south-1")
        create_table(ddb, "ttl-test")
        ttl = ddb.meta.client.describe_time_to_live(TableName="ttl-test")
        assert ttl["TimeToLiveDescription"]["AttributeName"] == "expires_at"
