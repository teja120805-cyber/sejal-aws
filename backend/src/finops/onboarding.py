"""Account onboarding: serve the customer CloudFormation templates and test a connection.

The canonical templates live in infra/customer-templates/. The Lambda build copies them into the
package (finops/customer_templates/); locally they are read from the repository.
"""

from dataclasses import dataclass, field
from functools import lru_cache
from importlib import resources
from pathlib import Path

from botocore.exceptions import BotoCoreError, ClientError

from finops.aws.session import AccountSession
from finops.domain import Account, AccountStatus, utcnow

TEMPLATES = {
    "readonly": "finops-readonly-role.yaml",
    "remediation": "finops-remediation-role.yaml",
}
_REPO_TEMPLATES = Path(__file__).resolve().parents[3] / "infra" / "customer-templates"


@lru_cache
def load_template(kind: str) -> str:
    filename = TEMPLATES[kind]
    packaged = resources.files("finops").joinpath("customer_templates", filename)
    if packaged.is_file():
        return packaged.read_text(encoding="utf-8")
    return (_REPO_TEMPLATES / filename).read_text(encoding="utf-8")


def quick_create_url(template_url: str, trusted_account: str, external_id: str, region: str) -> str:
    """CloudFormation console 'quick create' link with parameters pre-filled."""
    from urllib.parse import urlencode

    query = urlencode(
        {
            "templateURL": template_url,
            "stackName": "FinOpsReadOnlyRole",
            "param_TrustedAccountId": trusted_account,
            "param_ExternalId": external_id,
        }
    )
    console = f"https://{region}.console.aws.amazon.com/cloudformation/home?region={region}"
    return f"{console}#/stacks/quickcreate?{query}"


@dataclass
class ConnectionCheckResult:
    ok: bool
    message: str
    aws_account_id: str | None = None
    regions: list[str] = field(default_factory=list)


def check_connection(account: Account, home_region: str) -> ConnectionCheckResult:
    """AssumeRole + identity + region listing. Deliberately does NOT call Cost Explorer (paid)."""
    try:
        session = AccountSession.assume(account, region=home_region)
        actual = session.caller_account_id()
        if actual != account.aws_account_id:
            return ConnectionCheckResult(
                False, f"role belongs to account {actual}, expected {account.aws_account_id}"
            )
        regions = session.enabled_regions(home_region)
    except (ClientError, BotoCoreError) as err:
        return ConnectionCheckResult(False, _friendly_error(err))
    return ConnectionCheckResult(
        True, f"Connected. {len(regions)} enabled regions found.", actual, regions
    )


def apply_test_result(account: Account, result: ConnectionCheckResult) -> None:
    account.last_tested_at = utcnow()
    if result.ok:
        account.status = AccountStatus.CONNECTED
        account.status_message = None
        account.regions = result.regions
    else:
        account.status = AccountStatus.ERROR
        account.status_message = result.message


def _friendly_error(err: Exception) -> str:
    text = str(err)
    if "AccessDenied" in text and "AssumeRole" in text:
        return (
            "AccessDenied on sts:AssumeRole - check the role ARN, that the stack finished, and "
            "that the role's ExternalId matches the one shown here."
        )
    return text[:500]
