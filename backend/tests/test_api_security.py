"""Non-demo API: Cognito JWT auth and the 'no Cost Explorer on page load' guarantee."""

import time
from unittest.mock import patch

import botocore.client
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from finops.api import auth
from finops.api.deps import AppState, get_state
from finops.api.main import create_app
from finops.config import get_settings
from finops.demo import generate_demo_dataset, load_dataset
from finops.repositories.memory import create_memory_repositories
from finops.secrets import InMemorySecretStore

POOL, CLIENT, REGION = "ap-south-1_TEST", "client123", "ap-south-1"
ISSUER = f"https://cognito-idp.{REGION}.amazonaws.com/{POOL}"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


class FakeJwks:
    def get_signing_key_from_jwt(self, token):
        return type("K", (), {"key": KEY.public_key()})()


def token(**overrides) -> str:
    now = int(time.time())
    claims = {
        "sub": "user-1",
        "username": "alice",
        "iss": ISSUER,
        "client_id": CLIENT,
        "token_use": "access",
        "iat": now,
        "exp": now + 300,
        "cognito:groups": ["admins"],
    } | overrides
    return jwt.encode(claims, KEY, algorithm="RS256")


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("DEMO_MODE", "false")
    monkeypatch.setenv("COGNITO_USER_POOL_ID", POOL)
    monkeypatch.setenv("COGNITO_APP_CLIENT_ID", CLIENT)
    get_settings.cache_clear()
    auth._verifier.cache_clear()
    monkeypatch.setattr(auth.jwt, "PyJWKClient", lambda *a, **k: FakeJwks())

    repos = create_memory_repositories()
    load_dataset(repos, generate_demo_dataset())
    app = create_app()
    app.dependency_overrides[get_state] = lambda: AppState(repos, InMemorySecretStore(), [])
    yield TestClient(app)
    auth._verifier.cache_clear()


def bearer(t: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {t}"}


def test_public_config_needs_no_token(client):
    cfg = client.get("/api/config").json()
    assert cfg["auth"] == {
        "enabled": True,
        "region": REGION,
        "user_pool_id": POOL,
        "client_id": CLIENT,
        "domain": "",
    }


def test_missing_token_is_rejected(client):
    r = client.get("/api/accounts")
    assert r.status_code == 401 and r.headers["WWW-Authenticate"] == "Bearer"


def test_valid_token_is_accepted(client):
    me = client.get("/api/me", headers=bearer(token())).json()
    assert me == {"username": "alice", "email": None, "groups": ["admins"]}


@pytest.mark.parametrize(
    "claims",
    [
        {"client_id": "other-app"},
        {"token_use": "id"},
        {"iss": "https://evil.example"},
        {"exp": int(time.time()) - 10},
    ],
)
def test_bad_tokens_are_rejected(client, claims):
    assert client.get("/api/me", headers=bearer(token(**claims))).status_code == 401


def test_token_signed_with_another_key_is_rejected(client):
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    forged = jwt.encode(
        {
            "sub": "x",
            "iss": ISSUER,
            "client_id": CLIENT,
            "token_use": "access",
            "iat": int(time.time()),
            "exp": int(time.time()) + 60,
        },
        other,
        algorithm="RS256",
    )
    assert client.get("/api/me", headers=bearer(forged)).status_code == 401


def test_auth_not_configured_is_a_server_error(client, monkeypatch):
    monkeypatch.setenv("COGNITO_USER_POOL_ID", "")
    get_settings.cache_clear()
    assert client.get("/api/me", headers=bearer(token())).status_code == 500


def test_page_load_endpoints_never_call_cost_explorer(client):
    """Every GET endpoint the dashboard uses must work with Cost Explorer made unreachable."""
    original = botocore.client.BaseClient._make_api_call

    def guard(self, operation, params):
        if self.meta.service_model.service_name == "ce":
            raise AssertionError(f"Cost Explorer called on page load: {operation}")
        return original(self, operation, params)

    acc = "/api/accounts/demo"
    paths = [
        "/api/accounts",
        "/api/rules",
        "/api/onboarding",
        acc,
        f"{acc}/runs",
        f"{acc}/costs/summary",
        f"{acc}/costs/daily",
        f"{acc}/costs/breakdown?dimension=region",
        f"{acc}/costs/breakdown?dimension=tag&tag_key=Project",
        f"{acc}/costs/forecast",
        f"{acc}/costs/anomalies",
        f"{acc}/resources",
        f"{acc}/resources/export.csv",
        f"{acc}/findings",
        f"{acc}/rules",
        f"{acc}/budget",
        f"{acc}/alerts",
        f"{acc}/settings",
        f"{acc}/tags/compliance",
        f"{acc}/tags/costs?tag_key=Owner",
    ]
    with (
        patch.object(botocore.client.BaseClient, "_make_api_call", guard),
        patch("finops.api.routers.accounts._platform_account", return_value="111122223333"),
    ):
        for path in paths:
            r = client.get(path, headers=bearer(token()))
            assert r.status_code == 200, (path, r.text)
