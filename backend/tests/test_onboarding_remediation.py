import re
from pathlib import Path

import boto3
import pytest
import yaml
from botocore.exceptions import ClientError
from moto import mock_aws
from tests.aws_fixtures import REGION, make_account

from finops.aws.session import AccountSession
from finops.domain import Finding, FindingStatus, ResourceType, Severity
from finops.onboarding import (
    ConnectionCheckResult,
    apply_test_result,
    check_connection,
    load_template,
    quick_create_url,
)
from finops.remediation import RemediationError, execute, mark_remediated, preview
from finops.secrets import SsmSecretStore

TEMPLATES = Path(__file__).parents[2] / "infra" / "customer-templates"


# ---------------------------------------------------------------- CloudFormation templates


class _CfnLoader(yaml.SafeLoader):
    pass


for tag in ("!Ref", "!Sub", "!GetAtt"):
    _CfnLoader.add_constructor(tag, lambda loader, node: loader.construct_scalar(node))


def _template(name):
    return yaml.load((TEMPLATES / name).read_text(), Loader=_CfnLoader)


def _actions(template):
    role = next(iter(template["Resources"].values()))
    statements = role["Properties"]["Policies"][0]["PolicyDocument"]["Statement"]
    return role, [a for s in statements for a in s["Action"]]


def test_readonly_role_is_really_read_only():
    role, actions = _actions(_template("finops-readonly-role.yaml"))
    write_verbs = re.compile(
        r":(Create|Delete|Put|Update|Modify|Stop|Start|Terminate|Release|Attach)"
    )
    assert not [a for a in actions if write_verbs.search(a)]
    assert "s3:GetObject" not in actions  # metadata only, never bucket contents
    assert {
        "ce:GetCostAndUsage",
        "ce:GetCostForecast",
        "cloudwatch:GetMetricData",
        "pricing:GetProducts",
        "s3:GetLifecycleConfiguration",
    } <= set(actions)
    trust = role["Properties"]["AssumeRolePolicyDocument"]["Statement"][0]
    assert trust["Condition"]["StringEquals"]["sts:ExternalId"] == "ExternalId"
    assert role["Properties"]["MaxSessionDuration"] == 3600


def test_remediation_role_allows_only_three_write_actions():
    role, actions = _actions(_template("finops-remediation-role.yaml"))
    writes = {a for a in actions if not a.startswith("ec2:Describe")}
    assert writes == {
        "ec2:StopInstances",
        "ec2:DeleteVolume",
        "ec2:ReleaseAddress",
        "ec2:CreateSnapshot",
        "ec2:CreateTags",
    }
    trust = role["Properties"]["AssumeRolePolicyDocument"]["Statement"][0]
    assert "sts:ExternalId" in trust["Condition"]["StringEquals"]


def test_templates_served_by_api_are_the_canonical_files():
    assert load_template("readonly") == (TEMPLATES / "finops-readonly-role.yaml").read_text()


def test_quick_create_url():
    url = quick_create_url("https://b.s3.amazonaws.com/t.yaml", "111122223333", "ext", "ap-south-1")
    assert url.startswith("https://ap-south-1.console.aws.amazon.com/cloudformation/")
    assert "param_ExternalId=ext" in url and "param_TrustedAccountId=111122223333" in url


# ---------------------------------------------------------------- connection test


def test_check_connection_success_and_account_mismatch(monkeypatch):
    with mock_aws():
        ok = check_connection(make_account(), REGION)
        assert ok.ok and ok.aws_account_id == "123456789012" and REGION in ok.regions
        # A role ARN that resolves to a different account than it claims is rejected.
        monkeypatch.setattr(AccountSession, "caller_account_id", lambda self: "999999999999")
        mismatch = check_connection(make_account(), REGION)
        assert not mismatch.ok and "999999999999" in mismatch.message


def test_check_connection_reports_assume_role_errors(monkeypatch):
    def deny(*a, **k):
        raise ClientError(
            {"Error": {"Code": "AccessDenied", "Message": "not authorized to perform AssumeRole"}},
            "AssumeRole",
        )

    monkeypatch.setattr(AccountSession, "assume", deny)
    result = check_connection(make_account(), REGION)
    assert not result.ok and "ExternalId" in result.message


def test_apply_test_result():
    account = make_account()
    apply_test_result(account, ConnectionCheckResult(False, "boom"))
    assert account.status == "error" and account.status_message == "boom"
    apply_test_result(account, ConnectionCheckResult(True, "ok", "123456789012", ["ap-south-1"]))
    assert account.status == "connected" and account.regions == ["ap-south-1"]
    assert account.last_tested_at is not None


# ---------------------------------------------------------------- remediation


def finding(rule_id, resource_id, status=FindingStatus.OPEN):
    return Finding(
        account_id="acc1",
        rule_id=rule_id,
        resource_id=resource_id,
        resource_type=ResourceType.EC2_INSTANCE,
        region=REGION,
        severity=Severity.HIGH,
        title="t",
        detail="d.",
        recommendation="r",
        est_monthly_savings_usd=10,
        status=status,
    )


def test_preview_gates():
    with_role = make_account(
        remediation_role_arn="arn:aws:iam::123456789012:role/FinOpsRemediation"
    )
    f = finding("ec2-idle", "i-1")
    assert "REMEDIATION_ENABLED" in preview(f, with_role, False).disabled_reason
    assert "remediation role" in preview(f, make_account(), True).disabled_reason
    resolved = finding("ec2-idle", "i-1", FindingStatus.RESOLVED)
    assert "resolved" in preview(resolved, with_role, True).disabled_reason
    ok = preview(f, with_role, True)
    assert ok.enabled and ok.api_call == "ec2:StopInstances" and ok.warnings
    assert not preview(finding("rds-idle", "db"), with_role, True).supported


def test_execute_against_mocked_ec2():
    with mock_aws():
        ec2 = boto3.client("ec2", region_name=REGION)
        instance = ec2.run_instances(ImageId="ami-0123456789abcdef0", MinCount=1, MaxCount=1)[
            "Instances"
        ][0]["InstanceId"]
        volume = ec2.create_volume(Size=10, AvailabilityZone=f"{REGION}a")["VolumeId"]
        eip = ec2.allocate_address(Domain="vpc")["AllocationId"]
        session = AccountSession.assume(make_account())

        assert "Stop requested" in execute(finding("ec2-idle", instance), make_account(), session)
        state = ec2.describe_instances(InstanceIds=[instance])["Reservations"][0]["Instances"][0]
        assert state["State"]["Name"] in ("stopping", "stopped")

        msg = execute(finding("ebs-unattached", volume), make_account(), session)
        assert "Snapshot snap-" in msg
        assert ec2.describe_volumes()["Volumes"] == [] or all(
            v["VolumeId"] != volume for v in ec2.describe_volumes()["Volumes"]
        )
        snaps = ec2.describe_snapshots(OwnerIds=["self"])["Snapshots"]
        assert any(s["VolumeId"] == volume for s in snaps)

        execute(finding("eip-unassociated", eip), make_account(), session)
        assert ec2.describe_addresses()["Addresses"] == []

        with pytest.raises(RemediationError):
            execute(finding("rds-idle", "db"), make_account(), session)


def test_dry_run_permission_failure_stops_the_action(monkeypatch):
    class DeniedEc2:
        def stop_instances(self, **kwargs):
            raise ClientError({"Error": {"Code": "UnauthorizedOperation", "Message": "no"}}, "Stop")

    session = type("S", (), {"client": lambda self, *a: DeniedEc2()})()
    with pytest.raises(RemediationError, match="permission check failed"):
        execute(finding("ec2-idle", "i-1"), make_account(), session)


def test_mark_remediated():
    f = mark_remediated(finding("ec2-idle", "i-1"), "Stopped.")
    assert f.status == FindingStatus.RESOLVED and f.detail.endswith("Remediated: Stopped.")


def test_ssm_secret_store():
    with mock_aws():
        store = SsmSecretStore(boto3.client("ssm", region_name=REGION))
        assert store.get("/finops/slack-webhook/a") is None
        store.put("/finops/slack-webhook/a", "https://hooks.slack.com/x")
        assert store.get("/finops/slack-webhook/a") == "https://hooks.slack.com/x"
        store.delete("/finops/slack-webhook/a")
        store.delete("/finops/slack-webhook/a")  # idempotent
        assert store.get("/finops/slack-webhook/a") is None
