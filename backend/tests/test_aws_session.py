import boto3
import pytest
from botocore.stub import Stubber
from moto import mock_aws
from tests.aws_fixtures import MOTO_ACCOUNT, ROLE_ARN, make_account

from finops.aws.session import AccountSession


def test_assume_role_passes_external_id_and_never_uses_long_term_keys():
    """The request MUST carry the account's ExternalId; verified against the real STS API model."""
    base = boto3.Session(
        aws_access_key_id="platform", aws_secret_access_key="platform", region_name="ap-south-1"
    )
    sts = base.client("sts", region_name="ap-south-1")
    stub = Stubber(sts)
    stub.add_response(
        "assume_role",
        {
            "Credentials": {
                "AccessKeyId": "ASIATEMPORARY000000",
                "SecretAccessKey": "temporary-secret",
                "SessionToken": "temporary-token",
                "Expiration": "2026-10-11T13:00:00Z",
            }
        },
        expected_params={
            "RoleArn": ROLE_ARN,
            "RoleSessionName": "finops-platform",
            "ExternalId": "ext-123",
            "DurationSeconds": 3600,
        },
    )
    base.client = lambda *a, **kw: sts  # hand our stubbed client to AccountSession
    with stub:
        session = AccountSession.assume(make_account(), base_session=base)
    creds = session.session.get_credentials()
    assert creds.access_key == "ASIATEMPORARY000000"
    assert creds.token == "temporary-token"
    stub.assert_no_pending_responses()


def test_assume_role_can_target_remediation_role():
    with mock_aws():
        account = make_account()
        other = f"arn:aws:iam::{MOTO_ACCOUNT}:role/FinOpsRemediation"
        session = AccountSession.assume(account, role_arn=other)
        assert session.caller_account_id() == MOTO_ACCOUNT


def test_clients_are_cached_per_service_and_region():
    with mock_aws():
        session = AccountSession.assume(make_account())
        assert session.client("ec2", "ap-south-1") is session.client("ec2", "ap-south-1")
        assert session.client("ec2", "ap-south-1") is not session.client("ec2", "us-east-1")
        ce = session.client("ce", "us-east-1")
        assert ce.meta.endpoint_url == "https://ce.us-east-1.amazonaws.com"


def test_enabled_regions_includes_mumbai():
    with mock_aws():
        regions = AccountSession.assume(make_account()).enabled_regions()
        assert "ap-south-1" in regions and "us-east-1" in regions
        assert regions == sorted(regions)


def test_assume_role_failure_propagates():
    base = boto3.Session(aws_access_key_id="x", aws_secret_access_key="x", region_name="ap-south-1")
    sts = base.client("sts", region_name="ap-south-1")
    stub = Stubber(sts)
    stub.add_client_error("assume_role", "AccessDenied", "not authorized to assume role")
    base.client = lambda *a, **kw: sts
    with stub, pytest.raises(Exception, match="AccessDenied"):
        AccountSession.assume(make_account(), base_session=base)
