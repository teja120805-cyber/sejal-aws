import re
from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, HTTPException, Response, status
from pydantic import BaseModel, Field, field_validator

from finops.alerts import evaluate_alerts
from finops.api.auth import CurrentUser
from finops.api.deps import AccountDep, ReposDep, SettingsDep, StateDep
from finops.collection import (
    RefreshTooSoonError,
    lambda_invoker,
    refresh_account,
    start_async_refresh,
)
from finops.domain import Account, AccountStatus, CollectionRun, new_external_id, utcnow
from finops.domain.models import ROLE_ARN_RE
from finops.onboarding import (
    ConnectionCheckResult,
    apply_test_result,
    check_connection,
    load_template,
    quick_create_url,
)

router = APIRouter(tags=["accounts"])

EXTERNAL_ID_RE = re.compile(r"^[\w+=,.@:/-]{16,128}$")
DEMO_PLATFORM_ACCOUNT = "111122223333"


class AccountCreate(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=100)]
    role_arn: str
    external_id: str
    remediation_role_arn: str | None = None

    @field_validator("role_arn", "remediation_role_arn")
    @classmethod
    def _arn(cls, v: str | None) -> str | None:
        if v is not None:
            if not ROLE_ARN_RE.match(v):
                raise ValueError("must look like arn:aws:iam::123456789012:role/RoleName")
            if not v.rsplit("/", 1)[-1].startswith("FinOps"):
                # The platform's own IAM policy only allows sts:AssumeRole on role/FinOps*.
                raise ValueError("role name must start with 'FinOps' (as in the provided template)")
        return v

    @field_validator("external_id")
    @classmethod
    def _external_id(cls, v: str) -> str:
        if not EXTERNAL_ID_RE.match(v):
            raise ValueError("ExternalId must be 16-128 characters of [A-Za-z0-9+=,.@:/_-]")
        return v


class AccountUpdate(BaseModel):
    name: Annotated[str | None, Field(min_length=1, max_length=100)] = None
    remediation_role_arn: str | None = None

    @field_validator("remediation_role_arn")
    @classmethod
    def _arn(cls, v: str | None) -> str | None:
        if v and not ROLE_ARN_RE.match(v):
            raise ValueError("must look like arn:aws:iam::123456789012:role/RoleName")
        return v or None


def _platform_account(settings: SettingsDep) -> str:
    if settings.platform_account_id:
        return settings.platform_account_id
    if settings.demo_mode:
        return DEMO_PLATFORM_ACCOUNT
    import boto3

    return boto3.client("sts", region_name=settings.aws_region).get_caller_identity()["Account"]


@router.get("/onboarding")
def onboarding(settings: SettingsDep, _: CurrentUser) -> dict[str, object]:
    """Step 1 of connecting an account: a fresh ExternalId plus the template to deploy."""
    trusted = _platform_account(settings)
    external_id = new_external_id()
    return {
        "trusted_account_id": trusted,
        "external_id": external_id,
        "role_name": "FinOpsReadOnly",
        "template": load_template("readonly"),
        "remediation_template": load_template("remediation"),
        "quick_create_url": (
            quick_create_url(
                settings.customer_template_url, trusted, external_id, settings.aws_region
            )
            if settings.customer_template_url
            else None
        ),
        "cli_command": (
            "aws cloudformation deploy --stack-name FinOpsReadOnlyRole "
            "--template-file finops-readonly-role.yaml --capabilities CAPABILITY_NAMED_IAM "
            f"--parameter-overrides TrustedAccountId={trusted} ExternalId={external_id}"
        ),
    }


@router.get("/accounts")
def list_accounts(repos: ReposDep, _: CurrentUser) -> list[Account]:
    return repos.accounts.list()


@router.post("/accounts", status_code=status.HTTP_201_CREATED)
def create_account(body: AccountCreate, repos: ReposDep, _: CurrentUser) -> Account:
    account = Account(
        name=body.name,
        role_arn=body.role_arn,
        external_id=body.external_id,
        remediation_role_arn=body.remediation_role_arn,
    )
    if any(a.aws_account_id == account.aws_account_id for a in repos.accounts.list()):
        raise HTTPException(status.HTTP_409_CONFLICT, "this AWS account is already connected")
    repos.accounts.put(account)
    return account


@router.get("/accounts/{account_id}")
def get_account(account: AccountDep, _: CurrentUser) -> Account:
    return account


@router.patch("/accounts/{account_id}")
def update_account(
    body: AccountUpdate, account: AccountDep, repos: ReposDep, _: CurrentUser
) -> Account:
    for field_name in body.model_fields_set:
        setattr(account, field_name, getattr(body, field_name))
    repos.accounts.put(account)
    return account


@router.delete("/accounts/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_account(account: AccountDep, repos: ReposDep, _: CurrentUser) -> Response:
    repos.accounts.delete(account.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/accounts/{account_id}/test")
def test_account(
    account: AccountDep, repos: ReposDep, settings: SettingsDep, _: CurrentUser
) -> ConnectionCheckResult:
    """AssumeRole + identity check. Free: no Cost Explorer call."""
    if settings.demo_mode:
        result = ConnectionCheckResult(
            True,
            "Connected (demo mode, simulated).",
            account.aws_account_id,
            account.regions or [settings.aws_region, "us-east-1"],
        )
    else:
        result = check_connection(account, settings.aws_region)
    apply_test_result(account, result)
    repos.accounts.put(account)
    return result


@router.post("/accounts/{account_id}/refresh", status_code=status.HTTP_202_ACCEPTED)
def refresh(
    account: AccountDep,
    state: StateDep,
    settings: SettingsDep,
    response: Response,
    _: CurrentUser,
) -> CollectionRun:
    """Manual refresh (calls Cost Explorer). Limited to once per cooldown period per account."""
    if account.status == AccountStatus.PENDING:
        raise HTTPException(status.HTTP_409_CONFLICT, "test the connection first")
    try:
        if settings.demo_mode:
            from finops.demo.refresh import demo_refresh

            run = demo_refresh(
                state.repos,
                account.id,
                timedelta(minutes=settings.refresh_cooldown_minutes),
                state.notifiers,
            )
        elif settings.collector_function_name:
            invoke = lambda_invoker(settings.collector_function_name, settings.aws_region)
            run = start_async_refresh(account.id, state.repos, settings, invoke)
        else:
            run = refresh_account(account.id, state.repos, settings)
            fresh = state.repos.accounts.get(account.id)
            if fresh is not None:
                evaluate_alerts(state.repos, fresh, state.notifiers, utcnow())
    except RefreshTooSoonError as err:
        seconds = max(1, int(err.retry_after.total_seconds()))
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"Refresh is limited to once every {settings.refresh_cooldown_minutes} minutes "
            f"(Cost Explorer charges per request). Try again in {seconds // 60 + 1} min.",
            headers={"Retry-After": str(seconds)},
        ) from err
    return run


@router.get("/accounts/{account_id}/runs")
def runs(
    account: AccountDep, repos: ReposDep, _: CurrentUser, limit: int = 20
) -> list[CollectionRun]:
    return repos.runs.list(account.id, limit=min(limit, 100))
