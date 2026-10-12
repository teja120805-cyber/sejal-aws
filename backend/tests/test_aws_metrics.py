from datetime import timedelta

import boto3
import pytest
from moto import mock_aws
from tests.aws_fixtures import REGION, make_account

from finops.aws.metrics import MetricsCollector, _queries_for, _reduce
from finops.aws.session import AccountSession
from finops.domain import Resource, ResourceType, utcnow


def resource(type, rid, name=None, state=None, region=REGION):
    return Resource(
        account_id="acc1", resource_id=rid, type=type, region=region, name=name, state=state
    )


def put(cw, namespace, metric, dims, values, now, step=timedelta(hours=1)):
    cw.put_metric_data(
        Namespace=namespace,
        MetricData=[
            {
                "MetricName": metric,
                "Dimensions": [{"Name": k, "Value": v} for k, v in dims.items()],
                "Timestamp": now - step * (i + 1),
                "Value": v,
            }
            for i, v in enumerate(values)
        ],
    )


def test_enrich_ec2_rds_lambda_s3():
    now = utcnow()
    with mock_aws():
        cw = boto3.client("cloudwatch", region_name=REGION)
        put(cw, "AWS/EC2", "CPUUtilization", {"InstanceId": "i-1"}, [2.0, 4.0, 3.0, 15.0], now)
        put(cw, "AWS/RDS", "DatabaseConnections", {"DBInstanceIdentifier": "db1"}, [0, 0], now)
        put(
            cw,
            "AWS/Lambda",
            "Invocations",
            {"FunctionName": "fn"},
            [100, 200, 300],
            now,
            step=timedelta(days=1),
        )
        put(
            cw,
            "AWS/S3",
            "BucketSizeBytes",
            {"BucketName": "b", "StorageType": "StandardStorage"},
            [2 * 1024**3, 1 * 1024**3],  # newest first: 2 GiB yesterday, 1 GiB the day before
            now,
            step=timedelta(days=1),
        )

        ec2 = resource(ResourceType.EC2_INSTANCE, "i-1", state="running")
        stopped = resource(ResourceType.EC2_INSTANCE, "i-2", state="stopped")
        rds = resource(ResourceType.RDS_INSTANCE, "arn:db1", name="db1", state="available")
        fn = resource(ResourceType.LAMBDA_FUNCTION, "arn:fn", name="fn")
        bucket = resource(ResourceType.S3_BUCKET, "b")
        vol = resource(ResourceType.EBS_VOLUME, "vol-1")

        MetricsCollector(AccountSession.assume(make_account())).enrich(
            [ec2, stopped, rds, fn, bucket, vol], now
        )

    assert ec2.metrics["cpu_avg_7d"] == pytest.approx(6.0)
    assert ec2.metrics["cpu_max_7d"] == 15.0
    assert ec2.metrics["cpu_datapoints_7d"] == 4
    assert stopped.metrics == {}  # no CPU for stopped instances
    assert rds.metrics["db_connections_max_7d"] == 0
    assert fn.metrics["invocations_30d"] == 600
    assert fn.attributes["monthly_invocations"] == 600
    assert bucket.metrics["bucket_size_bytes"] == 2 * 1024**3
    assert bucket.attributes["size_gb"] == 2.0
    assert vol.metrics == {}


def test_instance_with_no_data_reports_zero_datapoints():
    now = utcnow()
    with mock_aws():
        ec2 = resource(ResourceType.EC2_INSTANCE, "i-new", state="running")
        MetricsCollector(AccountSession.assume(make_account())).enrich([ec2], now)
    assert ec2.metrics == {"cpu_datapoints_7d": 0}


def test_queries_only_for_supported_types():
    assert _queries_for(resource(ResourceType.ELASTIC_IP, "eip")) == []
    assert len(_queries_for(resource(ResourceType.EC2_INSTANCE, "i", state="running"))) == 3


def test_reduce():
    t = [utcnow() - timedelta(hours=h) for h in range(3)]
    assert _reduce("avg", t, [1, 2, 3]) == 2
    assert _reduce("max", t, [1, 5, 3]) == 5
    assert _reduce("sum", t, [1, 2, 3]) == 6
    assert _reduce("last", t, [7, 8, 9]) == 7  # t[0] is the most recent
    assert _reduce("count", [], []) == 0
    assert _reduce("avg", [], []) is None
