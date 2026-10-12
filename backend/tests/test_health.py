from fastapi.testclient import TestClient

from finops.api.main import create_app, handler
from finops.config import Settings


def test_health_reports_demo_mode():
    client = TestClient(create_app())
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["demo_mode"] is True
    assert body["region"] == "ap-south-1"


def test_settings_parse_cors_origins_from_csv(monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", "http://a.test, http://b.test")
    assert Settings().cors_origins == ["http://a.test", "http://b.test"]


def test_settings_defaults():
    s = Settings()
    assert s.cost_explorer_region == "us-east-1"
    assert s.remediation_enabled is False
    assert s.refresh_cooldown_minutes == 60


def test_mangum_handler_exists():
    assert callable(handler)
