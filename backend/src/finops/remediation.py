"""Opt-in remediation: stop an idle instance, delete an unattached volume, release an Elastic IP.

Four independent safety gates:
  1. REMEDIATION_ENABLED=true on the platform (off by default),
  2. a separate remediation role deployed by the customer (the read-only role can't write),
  3. the user confirms a dialog showing the exact API call, resource and savings, and types the
     resource ID back,
  4. an EC2 DryRun call first verifies permission without changing anything.
Volumes are snapshotted before deletion so the action is recoverable.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from botocore.exceptions import ClientError

from finops.aws.session import AccountSession
from finops.domain import Account, Finding, FindingStatus, utcnow

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ActionSpec:
    action: str
    api_call: str
    describe: str


ACTIONS: dict[str, ActionSpec] = {
    "ec2-idle": ActionSpec("stop_instance", "ec2:StopInstances", "Stop EC2 instance {id}"),
    "ebs-unattached": ActionSpec(
        "delete_volume",
        "ec2:CreateSnapshot + ec2:DeleteVolume",
        "Snapshot EBS volume {id}, then delete the volume",
    ),
    "eip-unassociated": ActionSpec(
        "release_address", "ec2:ReleaseAddress", "Release Elastic IP {id}"
    ),
}


@dataclass
class RemediationPreview:
    supported: bool
    enabled: bool
    finding_id: str
    resource_id: str
    resource_name: str | None
    region: str
    action: str | None = None
    api_call: str | None = None
    description: str | None = None
    monthly_savings_usd: float = 0.0
    warnings: list[str] = field(default_factory=list)
    disabled_reason: str | None = None


class RemediationError(Exception):
    pass


def preview(finding: Finding, account: Account, platform_enabled: bool) -> RemediationPreview:
    spec = ACTIONS.get(finding.rule_id)
    p = RemediationPreview(
        supported=spec is not None,
        enabled=False,
        finding_id=finding.id,
        resource_id=finding.resource_id,
        resource_name=finding.resource_name,
        region=finding.region,
        monthly_savings_usd=float(finding.est_monthly_savings_usd),
    )
    if spec is None:
        p.disabled_reason = "No automated action for this rule; follow the recommendation manually."
        return p
    p.action, p.api_call = spec.action, spec.api_call
    p.description = spec.describe.format(id=finding.resource_id) + f" in {finding.region}."
    p.warnings = {
        "stop_instance": [
            "The instance and anything running on it will go offline.",
            "EBS volumes are kept and still billed; restart any time.",
        ],
        "delete_volume": [
            "A snapshot is created first; restore from it if the data is needed.",
            "The volume itself cannot be recovered after deletion.",
        ],
        "release_address": ["The public IP address is returned to AWS and cannot be reclaimed."],
    }[spec.action]
    if not platform_enabled:
        p.disabled_reason = "Remediation is disabled on this platform (REMEDIATION_ENABLED=false)."
    elif not account.remediation_role_arn:
        p.disabled_reason = "No remediation role configured for this account."
    elif finding.status != FindingStatus.OPEN:
        p.disabled_reason = f"Finding is {finding.status.value}."
    else:
        p.enabled = True
    return p


def _dry_run(call: Callable[..., Any], **kwargs: Any) -> None:
    """EC2 DryRun: 'DryRunOperation' means we WOULD be allowed. Anything else is a failure."""
    try:
        call(DryRun=True, **kwargs)
    except ClientError as err:
        if err.response["Error"]["Code"] != "DryRunOperation":
            raise RemediationError(f"permission check failed: {err}") from err


def execute(finding: Finding, account: Account, session: AccountSession) -> str:
    """Perform the action with the REMEDIATION role session. Returns a human-readable result."""
    spec = ACTIONS.get(finding.rule_id)
    if spec is None:
        raise RemediationError("unsupported action")
    ec2 = session.client("ec2", finding.region)
    rid = finding.resource_id
    if spec.action == "stop_instance":
        _dry_run(ec2.stop_instances, InstanceIds=[rid])
        ec2.stop_instances(InstanceIds=[rid])
        return f"Stop requested for {rid}."
    if spec.action == "delete_volume":
        _dry_run(ec2.delete_volume, VolumeId=rid)
        snap = ec2.create_snapshot(
            VolumeId=rid,
            Description=f"FinOps safety snapshot before deleting {rid}",
            TagSpecifications=[
                {
                    "ResourceType": "snapshot",
                    "Tags": [{"Key": "CreatedBy", "Value": "finops-remediation"}],
                }
            ],
        )["SnapshotId"]
        ec2.get_waiter("snapshot_completed").wait(
            SnapshotIds=[snap], WaiterConfig={"Delay": 5, "MaxAttempts": 120}
        )
        ec2.delete_volume(VolumeId=rid)
        return f"Snapshot {snap} created, volume {rid} deleted."
    _dry_run(ec2.release_address, AllocationId=rid)
    ec2.release_address(AllocationId=rid)
    return f"Elastic IP {rid} released."


def mark_remediated(finding: Finding, result: str) -> Finding:
    finding.status = FindingStatus.RESOLVED
    finding.last_seen = utcnow()
    finding.detail = f"{finding.detail} Remediated: {result}"
    return finding
