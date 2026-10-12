"""FastAPI application.

The same `app` object runs locally under uvicorn and on AWS Lambda through Mangum,
which translates API Gateway events into ASGI requests.
"""

import logging

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from mangum import Mangum

from finops import __version__
from finops.api.routers import (
    accounts,
    budgets_alerts,
    costs,
    findings,
    health,
    meta,
    resources,
    tags,
)
from finops.config import get_settings
from finops.repositories import NotFoundError

logging.basicConfig(level=logging.INFO)


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="FinOps Platform API",
        version=__version__,
        description="AWS cost monitoring and optimisation. All read endpoints serve stored data; "
        "only POST /accounts/{id}/refresh calls Cost Explorer (rate-limited).",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
        expose_headers=["Retry-After", "Content-Disposition"],
    )

    @app.exception_handler(NotFoundError)
    async def _not_found(_: Request, exc: NotFoundError) -> JSONResponse:
        return JSONResponse({"detail": f"not found: {exc}"}, status_code=status.HTTP_404_NOT_FOUND)

    for module in (health, meta, accounts, costs, resources, findings, budgets_alerts, tags):
        app.include_router(module.router, prefix="/api")
    return app


app = create_app()

# Lambda entrypoint: finops.api.main.handler
handler = Mangum(app, lifespan="off")
