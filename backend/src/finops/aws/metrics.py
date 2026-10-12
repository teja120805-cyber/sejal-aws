"""CloudWatch metrics used by the waste rules.

GetMetricData fetches up to 500 metric queries per request, so each region needs only a handful of
calls regardless of how many resources it has (much cheaper than GetMetricStatistics per resource).

Metrics written into Resource.metrics:
    EC2    cpu_avg_7d, cpu_max_7d, cpu_datapoints_7d (hours of data; guards against new instances)
    RDS    db_connections_avg_7d, db_connections_max_7d
    Lambda invocations_30d, duration_avg_ms_30d
    S3     bucket_size_bytes (Standard storage)
"""

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from finops.aws.session import AccountSession
from finops.domain import Resource, ResourceType

MAX_QUERIES_PER_CALL = 500
HOUR, DAY = 3600, 86400


@dataclass(frozen=True)
class _Query:
    resource_key: str
    metric_key: str  # name in Resource.metrics
    reducer: str  # how to combine datapoints: avg | max | sum | last | count
    namespace: str
    metric_name: str
    dimensions: tuple[tuple[str, str], ...]
    stat: str
    period: int
    days: int


# resource type -> (namespace, [(metric_key, reducer, metric_name, stat, period, days)])
_SPECS: dict[ResourceType, tuple[str, list[tuple[str, str, str, str, int, int]]]] = {
    ResourceType.EC2_INSTANCE: (
        "AWS/EC2",
        [
            ("cpu_avg_7d", "avg", "CPUUtilization", "Average", HOUR, 7),
            ("cpu_max_7d", "max", "CPUUtilization", "Maximum", HOUR, 7),
            ("cpu_datapoints_7d", "count", "CPUUtilization", "Average", HOUR, 7),
        ],
    ),
    ResourceType.RDS_INSTANCE: (
        "AWS/RDS",
        [
            ("db_connections_avg_7d", "avg", "DatabaseConnections", "Average", HOUR, 7),
            ("db_connections_max_7d", "max", "DatabaseConnections", "Maximum", HOUR, 7),
        ],
    ),
    ResourceType.LAMBDA_FUNCTION: (
        "AWS/Lambda",
        [
            ("invocations_30d", "sum", "Invocations", "Sum", DAY, 30),
            ("duration_avg_ms_30d", "avg", "Duration", "Average", DAY, 30),
        ],
    ),
    # Published once a day; look back 3 days and take the most recent value.
    ResourceType.S3_BUCKET: (
        "AWS/S3",
        [("bucket_size_bytes", "last", "BucketSizeBytes", "Average", DAY, 3)],
    ),
}


def _dimensions(r: Resource) -> dict[str, str]:
    match r.type:
        case ResourceType.EC2_INSTANCE:
            return {"InstanceId": r.resource_id}
        case ResourceType.RDS_INSTANCE:
            return {"DBInstanceIdentifier": r.name or ""}
        case ResourceType.LAMBDA_FUNCTION:
            return {"FunctionName": r.name or ""}
        case _:  # S3
            return {"BucketName": r.resource_id, "StorageType": "StandardStorage"}


def _queries_for(r: Resource) -> list[_Query]:
    if r.type not in _SPECS or (r.type == ResourceType.EC2_INSTANCE and r.state != "running"):
        return []
    namespace, specs = _SPECS[r.type]
    dims = tuple(_dimensions(r).items())
    return [
        _Query(r.key, key, reducer, namespace, name, dims, stat, period, days)
        for key, reducer, name, stat, period, days in specs
    ]


def _reduce(reducer: str, timestamps: list[datetime], values: list[float]) -> float | None:
    if reducer == "count":
        return float(len(values))
    if not values:
        return None
    if reducer == "avg":
        return sum(values) / len(values)
    if reducer == "max":
        return max(values)
    if reducer == "sum":
        return sum(values)
    # "last": value with the latest timestamp
    return max(zip(timestamps, values, strict=True))[1]


class MetricsCollector:
    def __init__(self, session: AccountSession) -> None:
        self.s = session

    def enrich(self, resources: Iterable[Resource], now: datetime) -> None:
        """Fetch metrics for all resources (grouped by region) and write them onto each resource."""
        by_region: dict[str, list[Resource]] = defaultdict(list)
        for r in resources:
            by_region[r.region].append(r)
        for region, items in by_region.items():
            self._enrich_region(region, items, now)

    def _enrich_region(self, region: str, resources: list[Resource], now: datetime) -> None:
        index = {r.key: r for r in resources}
        queries = [q for r in resources for q in _queries_for(r)]
        cw = self.s.client("cloudwatch", region)

        # One GetMetricData call can only cover one time range, so group by look-back window.
        by_window: dict[int, list[_Query]] = defaultdict(list)
        for q in queries:
            by_window[q.days].append(q)

        for days, group in by_window.items():
            for start in range(0, len(group), MAX_QUERIES_PER_CALL):
                batch = group[start : start + MAX_QUERIES_PER_CALL]
                results = self._fetch(cw, batch, now - timedelta(days=days), now)
                for i, q in enumerate(batch):
                    timestamps, values = results.get(f"m{i}", ([], []))
                    value = _reduce(q.reducer, timestamps, values)
                    if value is not None:
                        index[q.resource_key].metrics[q.metric_key] = round(value, 4)

        for r in resources:
            _apply_derived_attributes(r)

    @staticmethod
    def _fetch(
        cw: Any, batch: list[_Query], start: datetime, end: datetime
    ) -> dict[str, tuple[list[datetime], list[float]]]:
        request = [
            {
                "Id": f"m{i}",
                "MetricStat": {
                    "Metric": {
                        "Namespace": q.namespace,
                        "MetricName": q.metric_name,
                        "Dimensions": [{"Name": k, "Value": v} for k, v in q.dimensions],
                    },
                    "Period": q.period,
                    "Stat": q.stat,
                },
                "ReturnData": True,
            }
            for i, q in enumerate(batch)
        ]
        out: dict[str, tuple[list[datetime], list[float]]] = defaultdict(lambda: ([], []))
        kwargs: dict[str, Any] = {"MetricDataQueries": request, "StartTime": start, "EndTime": end}
        while True:
            resp = cw.get_metric_data(**kwargs)
            for res in resp["MetricDataResults"]:
                ts, vals = out[res["Id"]]
                ts.extend(res.get("Timestamps", []))
                vals.extend(float(v) for v in res.get("Values", []))
            if not resp.get("NextToken"):
                return out
            kwargs["NextToken"] = resp["NextToken"]


def _apply_derived_attributes(r: Resource) -> None:
    """Copy metric-derived figures into attributes used by the cost estimator."""
    if r.type == ResourceType.S3_BUCKET and "bucket_size_bytes" in r.metrics:
        r.attributes["size_gb"] = round(r.metrics["bucket_size_bytes"] / 1024**3, 3)
    elif r.type == ResourceType.LAMBDA_FUNCTION:
        r.attributes["monthly_invocations"] = int(r.metrics.get("invocations_30d", 0))
        r.attributes["avg_duration_ms"] = r.metrics.get("duration_avg_ms_30d", 0)
