"""Multi-region resource inventory (read-only describe/list calls only).

Each (region, service) scan is isolated: if one fails (e.g. AccessDenied, region disabled
mid-run), the error is recorded and every other scan still completes. The collection service then
keeps the previous inventory for the failed scans instead of deleting it.
"""

import logging
import re
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from finops.aws.session import AccountSession
from finops.domain import Resource, ResourceType

logger = logging.getLogger(__name__)

# "User initiated (2026-08-01 10:15:00 GMT)" (some tools/emulators write UTC)
_STOP_REASON_RE = re.compile(r"\((\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) (?:GMT|UTC)\)")
_AMI_SNAPSHOT_RE = re.compile(r"Created by CreateImage\(.*\) for (ami-[0-9a-f]+)")


@dataclass
class InventoryResult:
    resources: list[Resource] = field(default_factory=list)
    # (region, resource type) pairs whose scan failed; region "global" for S3 listing
    failed: set[tuple[str, ResourceType]] = field(default_factory=set)
    errors: list[str] = field(default_factory=list)


def _tags(tag_list: list[dict[str, str]] | None) -> dict[str, str]:
    return {t["Key"]: t["Value"] for t in tag_list or []}


def _parse_stopped_at(reason: str | None) -> str | None:
    match = _STOP_REASON_RE.search(reason or "")
    if not match:
        return None
    return datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC).isoformat()


class InventoryScanner:
    def __init__(self, session: AccountSession, max_workers: int = 8) -> None:
        self.s = session
        self.account_id = session.account.id
        self.max_workers = max_workers

    # ------------------------------------------------------------------ public

    def scan(self, regions: list[str], s3_home_region: str = "us-east-1") -> InventoryResult:
        result = InventoryResult()
        jobs: list[tuple[str, ResourceType, Callable[[str], list[Resource]]]] = []
        for region in regions:
            jobs += [
                (region, ResourceType.EC2_INSTANCE, self.ec2_instances),
                (region, ResourceType.EBS_VOLUME, self.ebs_volumes),
                (region, ResourceType.EBS_SNAPSHOT, self.ebs_snapshots),
                (region, ResourceType.ELASTIC_IP, self.elastic_ips),
                (region, ResourceType.RDS_INSTANCE, self.rds_instances),
                (region, ResourceType.LAMBDA_FUNCTION, self.lambda_functions),
            ]
        jobs.append(("global", ResourceType.S3_BUCKET, lambda _: self.s3_buckets(s3_home_region)))

        def run(job: tuple[str, ResourceType, Callable[[str], list[Resource]]]) -> Any:
            region, rtype, fn = job
            try:
                return fn(region)
            except (ClientError, BotoCoreError) as err:
                logger.warning("scan failed: %s %s: %s", region, rtype.value, err)
                return err

        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            for (region, rtype, _), outcome in zip(jobs, pool.map(run, jobs), strict=True):
                if isinstance(outcome, Exception):
                    result.failed.add((region, rtype))
                    result.errors.append(f"{region}/{rtype.value}: {outcome}")
                else:
                    result.resources.extend(outcome)
        return result

    # ------------------------------------------------------------------ EC2 family

    def ec2_instances(self, region: str) -> list[Resource]:
        ec2 = self.s.client("ec2", region)
        out: list[Resource] = []
        for page in ec2.get_paginator("describe_instances").paginate():
            for reservation in page["Reservations"]:
                for inst in reservation["Instances"]:
                    state = inst["State"]["Name"]
                    if state in ("terminated", "shutting-down"):
                        continue
                    tags = _tags(inst.get("Tags"))
                    attrs: dict[str, Any] = {
                        "instance_type": inst["InstanceType"],
                        "platform": inst.get("PlatformDetails", "Linux/UNIX"),
                        "availability_zone": inst.get("Placement", {}).get("AvailabilityZone"),
                        "lifecycle": inst.get("InstanceLifecycle", "on-demand"),
                        "volume_ids": [
                            m["Ebs"]["VolumeId"]
                            for m in inst.get("BlockDeviceMappings", [])
                            if "Ebs" in m
                        ],
                    }
                    if state == "stopped":
                        attrs["stopped_at"] = _parse_stopped_at(inst.get("StateTransitionReason"))
                    out.append(
                        Resource(
                            account_id=self.account_id,
                            resource_id=inst["InstanceId"],
                            type=ResourceType.EC2_INSTANCE,
                            region=region,
                            name=tags.get("Name"),
                            state=state,
                            tags=tags,
                            created_at=inst.get("LaunchTime"),
                            attributes=attrs,
                        )
                    )
        return out

    def ebs_volumes(self, region: str) -> list[Resource]:
        ec2 = self.s.client("ec2", region)
        out: list[Resource] = []
        for page in ec2.get_paginator("describe_volumes").paginate():
            for vol in page["Volumes"]:
                tags = _tags(vol.get("Tags"))
                attachments = vol.get("Attachments") or []
                out.append(
                    Resource(
                        account_id=self.account_id,
                        resource_id=vol["VolumeId"],
                        type=ResourceType.EBS_VOLUME,
                        region=region,
                        name=tags.get("Name"),
                        state=vol["State"],
                        tags=tags,
                        created_at=vol.get("CreateTime"),
                        attributes={
                            "volume_type": vol.get("VolumeType", "gp2"),
                            "size_gb": vol["Size"],
                            "iops": vol.get("Iops"),
                            "throughput": vol.get("Throughput"),
                            "attached_instance_id": (
                                attachments[0]["InstanceId"] if attachments else None
                            ),
                            "availability_zone": vol.get("AvailabilityZone"),
                        },
                    )
                )
        return out

    def ebs_snapshots(self, region: str) -> list[Resource]:
        ec2 = self.s.client("ec2", region)
        out: list[Resource] = []
        for page in ec2.get_paginator("describe_snapshots").paginate(OwnerIds=["self"]):
            for snap in page["Snapshots"]:
                tags = _tags(snap.get("Tags"))
                description = snap.get("Description", "")
                ami = _AMI_SNAPSHOT_RE.search(description)
                out.append(
                    Resource(
                        account_id=self.account_id,
                        resource_id=snap["SnapshotId"],
                        type=ResourceType.EBS_SNAPSHOT,
                        region=region,
                        name=tags.get("Name") or description or None,
                        state=snap.get("State"),
                        tags=tags,
                        created_at=snap.get("StartTime"),
                        attributes={
                            "size_gb": snap.get("VolumeSize", 0),
                            "volume_id": snap.get("VolumeId"),
                            "description": description,
                            # Snapshots backing an AMI can't be deleted until the AMI is
                            "ami_id": ami.group(1) if ami else None,
                        },
                    )
                )
        return out

    def elastic_ips(self, region: str) -> list[Resource]:
        ec2 = self.s.client("ec2", region)
        out: list[Resource] = []
        for addr in ec2.describe_addresses()["Addresses"]:
            tags = _tags(addr.get("Tags"))
            associated = bool(addr.get("AssociationId") or addr.get("InstanceId"))
            out.append(
                Resource(
                    account_id=self.account_id,
                    resource_id=addr.get("AllocationId") or addr["PublicIp"],
                    type=ResourceType.ELASTIC_IP,
                    region=region,
                    name=tags.get("Name") or addr.get("PublicIp"),
                    state="associated" if associated else "unassociated",
                    tags=tags,
                    attributes={
                        "public_ip": addr.get("PublicIp"),
                        "association_id": addr.get("AssociationId"),
                        "instance_id": addr.get("InstanceId"),
                        "network_interface_id": addr.get("NetworkInterfaceId"),
                    },
                )
            )
        return out

    # ------------------------------------------------------------------ RDS / Lambda / S3

    def rds_instances(self, region: str) -> list[Resource]:
        rds = self.s.client("rds", region)
        out: list[Resource] = []
        for page in rds.get_paginator("describe_db_instances").paginate():
            for db in page["DBInstances"]:
                out.append(
                    Resource(
                        account_id=self.account_id,
                        resource_id=db["DBInstanceArn"],
                        type=ResourceType.RDS_INSTANCE,
                        region=region,
                        name=db["DBInstanceIdentifier"],
                        state=db.get("DBInstanceStatus"),
                        tags=_tags(db.get("TagList")),
                        created_at=db.get("InstanceCreateTime"),
                        attributes={
                            "instance_class": db["DBInstanceClass"],
                            "engine": db.get("Engine"),
                            "engine_version": db.get("EngineVersion"),
                            "multi_az": db.get("MultiAZ", False),
                            "storage_gb": db.get("AllocatedStorage", 0),
                            "storage_type": db.get("StorageType"),
                        },
                    )
                )
        return out

    def lambda_functions(self, region: str) -> list[Resource]:
        lam = self.s.client("lambda", region)
        out: list[Resource] = []
        for page in lam.get_paginator("list_functions").paginate():
            for fn in page["Functions"]:
                tags = lam.list_tags(Resource=fn["FunctionArn"]).get("Tags", {})
                out.append(
                    Resource(
                        account_id=self.account_id,
                        resource_id=fn["FunctionArn"],
                        type=ResourceType.LAMBDA_FUNCTION,
                        region=region,
                        name=fn["FunctionName"],
                        state=fn.get("State", "Active"),
                        tags=tags,
                        attributes={
                            "runtime": fn.get("Runtime"),
                            "memory_mb": fn.get("MemorySize", 128),
                            "timeout_s": fn.get("Timeout"),
                            "architectures": fn.get("Architectures", ["x86_64"]),
                            "last_modified": fn.get("LastModified"),
                        },
                    )
                )
        return out

    def s3_buckets(self, home_region: str) -> list[Resource]:
        s3 = self.s.client("s3", home_region)
        out: list[Resource] = []
        for bucket in s3.list_buckets().get("Buckets", []):
            name = bucket["Name"]
            location = s3.get_bucket_location(Bucket=name).get("LocationConstraint")
            # Legacy quirks: us-east-1 is reported as None, eu-west-1 sometimes as "EU"
            region = {None: "us-east-1", "": "us-east-1", "EU": "eu-west-1"}.get(location, location)
            regional = self.s.client("s3", region)
            out.append(
                Resource(
                    account_id=self.account_id,
                    resource_id=name,
                    type=ResourceType.S3_BUCKET,
                    region=region,
                    name=name,
                    tags=self._bucket_tags(regional, name),
                    created_at=bucket.get("CreationDate"),
                    attributes={
                        "has_lifecycle_policy": self._has_lifecycle(regional, name),
                        "versioning": regional.get_bucket_versioning(Bucket=name).get(
                            "Status", "Disabled"
                        ),
                        "storage_class": "STANDARD",
                        "size_gb": 0,  # filled from CloudWatch BucketSizeBytes
                    },
                )
            )
        return out

    @staticmethod
    def _has_lifecycle(s3: Any, bucket: str) -> bool:
        try:
            rules = s3.get_bucket_lifecycle_configuration(Bucket=bucket).get("Rules", [])
            return any(r.get("Status") == "Enabled" for r in rules)
        except ClientError as err:
            if err.response["Error"]["Code"] == "NoSuchLifecycleConfiguration":
                return False
            raise

    @staticmethod
    def _bucket_tags(s3: Any, bucket: str) -> dict[str, str]:
        try:
            return _tags(s3.get_bucket_tagging(Bucket=bucket).get("TagSet"))
        except ClientError as err:
            if err.response["Error"]["Code"] == "NoSuchTagSet":
                return {}
            raise
