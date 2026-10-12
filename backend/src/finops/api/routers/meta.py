from fastapi import APIRouter

from finops.api.auth import CurrentUser
from finops.api.deps import SettingsDep
from finops.rules import ALL_RULES

router = APIRouter(tags=["meta"])


@router.get("/config")
def public_config(settings: SettingsDep) -> dict[str, object]:
    """Everything the frontend needs before login (no secrets)."""
    return {
        "demo_mode": settings.demo_mode,
        "region": settings.aws_region,
        "usd_inr_rate": settings.usd_inr_rate,
        "refresh_cooldown_minutes": settings.refresh_cooldown_minutes,
        "remediation_enabled": settings.remediation_enabled,
        "auth": {
            "enabled": not settings.demo_mode,
            "region": settings.cognito_region,
            "user_pool_id": settings.cognito_user_pool_id,
            "client_id": settings.cognito_app_client_id,
            "domain": settings.cognito_domain,
        },
    }


@router.get("/me")
def me(user: CurrentUser) -> dict[str, object]:
    return {"username": user.username, "email": user.email, "groups": user.groups}


@router.get("/rules")
def rules(_: CurrentUser) -> list[dict[str, object]]:
    return [rule.metadata() for rule in ALL_RULES]
