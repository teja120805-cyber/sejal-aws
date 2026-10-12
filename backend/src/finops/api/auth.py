"""Cognito JWT verification.

In AWS, API Gateway's JWT authorizer already rejects bad tokens; verifying again here is
defence in depth and also protects the API when it runs anywhere else (e.g. uvicorn).
Demo mode has no authentication.
"""

from dataclasses import dataclass, field
from functools import lru_cache
from typing import Annotated, Any

import jwt
from fastapi import Depends, HTTPException, Request, status

from finops.config import Settings, get_settings


@dataclass(frozen=True)
class User:
    sub: str
    username: str
    email: str | None = None
    groups: list[str] = field(default_factory=list)


DEMO_USER = User(sub="demo", username="demo", email="demo@example.com")


class CognitoVerifier:
    def __init__(self, region: str, user_pool_id: str, client_id: str) -> None:
        self.issuer = f"https://cognito-idp.{region}.amazonaws.com/{user_pool_id}"
        self.client_id = client_id
        self.jwks = jwt.PyJWKClient(f"{self.issuer}/.well-known/jwks.json", cache_keys=True)

    def verify(self, token: str) -> dict[str, Any]:
        key = self.jwks.get_signing_key_from_jwt(token).key
        claims = jwt.decode(
            token,
            key,
            algorithms=["RS256"],
            issuer=self.issuer,
            options={"require": ["exp", "iat", "token_use"], "verify_aud": False},
        )
        # Cognito access tokens carry client_id instead of aud.
        if claims.get("token_use") != "access" or claims.get("client_id") != self.client_id:
            raise jwt.InvalidTokenError("wrong token type or client")
        return claims


@lru_cache
def _verifier(region: str, pool: str, client: str) -> CognitoVerifier:
    return CognitoVerifier(region, pool, client)


def get_verifier(settings: Annotated[Settings, Depends(get_settings)]) -> CognitoVerifier:
    if not settings.cognito_user_pool_id or not settings.cognito_app_client_id:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "authentication not configured")
    return _verifier(
        settings.cognito_region, settings.cognito_user_pool_id, settings.cognito_app_client_id
    )


def get_current_user(
    request: Request, settings: Annotated[Settings, Depends(get_settings)]
) -> User:
    if settings.demo_mode:
        return DEMO_USER
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "missing bearer token", {"WWW-Authenticate": "Bearer"}
        )
    try:
        claims = get_verifier(settings).verify(token)
    except jwt.PyJWTError as err:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, f"invalid token: {err}", {"WWW-Authenticate": "Bearer"}
        ) from err
    return User(
        sub=claims["sub"],
        username=claims.get("username", claims["sub"]),
        email=claims.get("email"),
        groups=list(claims.get("cognito:groups", [])),
    )


CurrentUser = Annotated[User, Depends(get_current_user)]
