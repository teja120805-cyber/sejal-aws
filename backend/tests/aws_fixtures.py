"""Builders for a small but realistic moto-backed AWS account."""

import io
import json
import zipfile
from dataclasses import dataclass, field

import boto3

from finops.domain import Account, AccountStatus

# moto's default account; assumed-role credentials for this account see the same mock resources
MOTO_ACCOUNT = "123456789012"
ROLE_ARN = f"arn:aws:iam::{MOTO_ACCOUNT}:role/FinOpsReadOnly"
REGION = "ap-south-1"


def make_account(**kwargs) -> Account:
    defaults = dict(
        id="acc1",
        name="Test account",
        role_arn=ROLE_ARN,
        external_id="ext-123",
        status=AccountStatus.CONNECTED,
    )
    return Account(**(defaults | kwargs))


@dataclass
class World:
    """IDs of everything created, so tests can assert on them."""

    running_instance: str = ""
    stopped_instance: str = ""
    unattached_volume: str = ""
    snapshot: str = ""
    free_eip: str = ""
    used_eip: str = ""
    db_name: str = "orders-db"
    function_name: str = "order-processor"
    buckets: dict[str, str] = field(default_factory=dict)  # name -> region


def _lambda_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("app.py", "def handler(event, context):\n    return 1\n")
    return buf.getvalue()


def build_world(region: str = REGION) -> World:
    w = World()
    ec2 = boto3.client("ec2", region_name=region)
    ami = "ami-0123456789abcdef0"  # default AMIs are disabled in conftest for speed

    def tags(**kv: str) -> list[dict]:
        return [
            {"ResourceType": "instance", "Tags": [{"Key": k, "Value": v} for k, v in kv.items()]}
        ]

    w.running_instance = ec2.run_instances(
        ImageId=ami,
        MinCount=1,
        MaxCount=1,
        InstanceType="m5.large",
        TagSpecifications=tags(Name="web-1", Project="shop", Owner="web-team", Environment="prod"),
    )["Instances"][0]["InstanceId"]
    w.stopped_instance = ec2.run_instances(
        ImageId=ami,
        MinCount=1,
        MaxCount=1,
        InstanceType="t3.large",
        TagSpecifications=tags(Name="old"),
    )["Instances"][0]["InstanceId"]
    ec2.stop_instances(InstanceIds=[w.stopped_instance])

    vol = ec2.create_volume(Size=100, AvailabilityZone=f"{region}a", VolumeType="gp2")
    w.unattached_volume = vol["VolumeId"]
    w.snapshot = ec2.create_snapshot(VolumeId=vol["VolumeId"], Description="nightly")["SnapshotId"]

    w.free_eip = ec2.allocate_address(Domain="vpc")["AllocationId"]
    w.used_eip = ec2.allocate_address(Domain="vpc")["AllocationId"]
    ec2.associate_address(AllocationId=w.used_eip, InstanceId=w.running_instance)

    boto3.client("rds", region_name=region).create_db_instance(
        DBInstanceIdentifier=w.db_name,
        DBInstanceClass="db.t3.medium",
        Engine="postgres",
        AllocatedStorage=50,
        MasterUsername="admin",
        MasterUserPassword="password123",
        Tags=[{"Key": "Project", "Value": "shop"}],
    )

    iam = boto3.client("iam", region_name="us-east-1")
    role = iam.create_role(
        RoleName="lambda-exec",
        AssumeRolePolicyDocument=json.dumps({"Version": "2012-10-17", "Statement": []}),
    )["Role"]["Arn"]
    boto3.client("lambda", region_name=region).create_function(
        FunctionName=w.function_name,
        Runtime="python3.12",
        Role=role,
        Handler="app.handler",
        Code={"ZipFile": _lambda_zip()},
        MemorySize=512,
        Tags={"Project": "shop"},
    )

    s3_local = boto3.client("s3", region_name=region)
    s3_local.create_bucket(
        Bucket="shop-logs", CreateBucketConfiguration={"LocationConstraint": region}
    )
    s3_local.put_bucket_tagging(
        Bucket="shop-logs", Tagging={"TagSet": [{"Key": "Project", "Value": "shop"}]}
    )
    s3_us = boto3.client("s3", region_name="us-east-1")
    s3_us.create_bucket(Bucket="shop-archive")
    s3_us.put_bucket_lifecycle_configuration(
        Bucket="shop-archive",
        LifecycleConfiguration={
            "Rules": [
                {
                    "ID": "to-ia",
                    "Status": "Enabled",
                    "Filter": {"Prefix": ""},
                    "Transitions": [{"Days": 30, "StorageClass": "STANDARD_IA"}],
                }
            ]
        },
    )
    w.buckets = {"shop-logs": region, "shop-archive": "us-east-1"}
    return w
