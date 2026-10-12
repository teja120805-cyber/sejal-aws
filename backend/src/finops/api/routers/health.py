from typing import Annotated

from fastapi import APIRouter, Depends

from finops import __version__
from finops.config import Settings, get_settings

router = APIRouter(tags=["health"])


@router.get("/health")
def health(settings: Annotated[Settings, Depends(get_settings)]) -> dict[str, object]:
    return {
        "status": "ok",
        "version": __version__,
        "demo_mode": settings.demo_mode,
        "region": settings.aws_region,
    }
