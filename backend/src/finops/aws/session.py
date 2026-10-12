"""Cross-account access via STS AssumeRole + ExternalId.

The platform never stores customer access keys. Its own identity (the Lambda's execution role,
or your local profile) assumes the customer's read-only role, and only the resulting temporary
credentials (valid for 1 hour, kept in memory only) are used to call AWS.

The ExternalId is a random value generated per connected account and baked into the role's trust
policy. It stops the "confused deputy" problem: someone who learns your role ARN can't make this
platform read your account through their own tenant, because they don't know your ExternalId.
"""

import threading
from typing import Any

import boto3
from botocore.config import Config

from finops.domain import Account

SESSION_DURATION_SECONDS = 3600

BOTO_CONFIG = Config(
    retries={"mode": "adaptive", "max_attempts": 8},
    connect_timeout=5,
    read_timeout=30,
    user_agent_extra="finops-platform",
)


class AccountSession:
    """Temporary credentials for one customer account, plus a thread-safe client cache."""

    def __init__(self, session: boto3.Session, account: Account) -> None:
        self.session = session
        self.account = account
        self._clients: dict[tuple[str, str], Any] = {}
        self._lock = threading.Lock()

    @classmethod
    def assume(
        cls,
        account: Account,
        *,
        role_arn: str | None = None,
        base_session: boto3.Session | None = None,
        session_name: str = "finops-platform",
        region: str = "ap-south-1",
    ) -> "AccountSession":
        """AssumeRole into the read-only role, or `role_arn` (e.g. the remediation role)."""
        base = base_session or boto3.Session()
        sts = base.client("sts", region_name=region, config=BOTO_CONFIG)
        creds = sts.assume_role(
            RoleArn=role_arn or account.role_arn,
            RoleSessionName=session_name,
            ExternalId=account.external_id,
            DurationSeconds=SESSION_DURATION_SECONDS,
        )["Credentials"]
        session = boto3.Session(
            aws_access_key_id=creds["AccessKeyId"],
            aws_secret_access_key=creds["SecretAccessKey"],
            aws_session_token=creds["SessionToken"],
            region_name=region,
        )
        return cls(session, account)

    def client(self, service: str, region: str) -> Any:
        # boto3 Sessions aren't thread-safe for creating clients, but clients are safe to share.
        key = (service, region)
        with self._lock:
            if key not in self._clients:
                self._clients[key] = self.session.client(
                    service, region_name=region, config=BOTO_CONFIG
                )
            return self._clients[key]

    def caller_account_id(self) -> str:
        return self.client("sts", self.session.region_name or "ap-south-1").get_caller_identity()[
            "Account"
        ]

    def enabled_regions(self, home_region: str = "ap-south-1") -> list[str]:
        """Regions enabled for this account (opt-in regions only if the customer opted in)."""
        resp = self.client("ec2", home_region).describe_regions(AllRegions=False)
        return sorted(r["RegionName"] for r in resp["Regions"])
