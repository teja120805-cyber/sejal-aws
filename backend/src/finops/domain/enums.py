from enum import StrEnum


class ResourceType(StrEnum):
    EC2_INSTANCE = "ec2_instance"
    EBS_VOLUME = "ebs_volume"
    EBS_SNAPSHOT = "ebs_snapshot"
    ELASTIC_IP = "elastic_ip"
    RDS_INSTANCE = "rds_instance"
    S3_BUCKET = "s3_bucket"
    LAMBDA_FUNCTION = "lambda_function"


class Severity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class FindingStatus(StrEnum):
    OPEN = "open"
    DISMISSED = "dismissed"
    RESOLVED = "resolved"


class AlertType(StrEnum):
    BUDGET_ACTUAL = "budget_actual"
    BUDGET_FORECAST = "budget_forecast"
    ANOMALY = "anomaly"


class AccountStatus(StrEnum):
    PENDING = "pending"  # role ARN saved, connection not yet tested
    CONNECTED = "connected"
    ERROR = "error"


class RunTrigger(StrEnum):
    SCHEDULE = "schedule"
    MANUAL = "manual"
    DEMO = "demo"


class RunStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"  # finished, but some regions/services failed
    FAILED = "failed"
