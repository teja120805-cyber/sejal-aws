"""Application settings, loaded from environment variables (and an optional .env file).

Secrets are never hard-coded: anything sensitive is either an env var or the *name* of an
SSM Parameter Store entry that is resolved at runtime.
"""

from functools import lru_cache
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=("../.env", ".env"), extra="ignore")

    demo_mode: bool = True

    aws_region: str = "ap-south-1"
    # Cost Explorer and the Pricing API are global services served from us-east-1.
    cost_explorer_region: str = "us-east-1"
    table_name: str = "finops"
    alerts_topic_arn: str = ""
    # Slack webhook URLs are stored as SSM SecureStrings named {prefix}/{account_id}
    slack_webhook_ssm_prefix: str = "/finops/slack-webhook"

    refresh_cooldown_minutes: int = Field(default=60, ge=1)
    # First collection backfills this many days; later runs re-read the last few days only.
    cost_backfill_days: int = Field(default=90, ge=7, le=365)
    cost_lookback_days: int = Field(default=7, ge=1, le=30)
    usd_inr_rate: float = Field(default=88.0, gt=0)
    remediation_enabled: bool = False
    # Live EC2 prices from the AWS Pricing API (free) instead of only the static price table
    use_pricing_api: bool = False
    # In AWS, manual refresh runs the collector Lambda asynchronously (API Gateway has a 30 s
    # limit). Empty = run the collection inside the API request (local development).
    collector_function_name: str = ""

    cognito_user_pool_id: str = ""
    cognito_app_client_id: str = ""
    cognito_region: str = "ap-south-1"
    # Hosted UI domain, e.g. finops-demo.auth.ap-south-1.amazoncognito.com (for the frontend)
    cognito_domain: str = ""

    # AWS account the platform runs in; customers' roles trust this account.
    platform_account_id: str = ""
    # Public HTTPS URL of finops-readonly-role.yaml (enables the CloudFormation quick-create link)
    customer_template_url: str = ""

    # NoDecode: accept a plain comma-separated string instead of JSON
    cors_origins: Annotated[list[str], NoDecode] = ["http://localhost:5173"]

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [o.strip() for o in value.split(",") if o.strip()]
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
