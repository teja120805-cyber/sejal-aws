"""API tests in demo mode (in-memory data, no AWS)."""

import csv
import io
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from finops.api.deps import _build_state
from finops.api.main import create_app
from finops.config import get_settings

ACC = "/api/accounts/demo"
NEW_ARN = "arn:aws:iam::210987654321:role/FinOpsReadOnly"
EXT = "0f8fad5b-d9cb-469f-a165-70867728950e"


def fresh_client(monkeypatch=None, **env) -> TestClient:
    if monkeypatch:
        for k, v in env.items():
            monkeypatch.setenv(k, v)
    get_settings.cache_clear()
    _build_state.cache_clear()
    return TestClient(create_app())


@pytest.fixture(scope="module")
def ro():
    """Shared client for read-only tests (building demo state once keeps tests fast)."""
    mp = pytest.MonkeyPatch()
    mp.setenv("DEMO_MODE", "true")
    client = fresh_client()
    yield client
    mp.undo()


@pytest.fixture
def client(monkeypatch):
    return fresh_client(monkeypatch, DEMO_MODE="true")


# ---------------------------------------------------------------- meta


def test_config_and_me(ro):
    cfg = ro.get("/api/config").json()
    assert cfg["demo_mode"] is True and cfg["usd_inr_rate"] == 88.0
    assert cfg["auth"]["enabled"] is False and cfg["remediation_enabled"] is False
    assert ro.get("/api/me").json()["username"] == "demo"
    assert len(ro.get("/api/rules").json()) == 8


# ---------------------------------------------------------------- accounts


def test_onboarding_returns_template_and_fresh_external_id(ro):
    a, b = ro.get("/api/onboarding").json(), ro.get("/api/onboarding").json()
    assert a["external_id"] != b["external_id"]
    assert a["trusted_account_id"] == "111122223333"
    assert "sts:ExternalId" in a["template"]
    assert "FinOpsRemediation" in a["remediation_template"]
    assert a["external_id"] in a["cli_command"]
    assert a["quick_create_url"] is None  # needs CUSTOMER_TEMPLATE_URL


def test_account_lifecycle(client):
    r = client.post(
        "/api/accounts", json={"name": "Staging", "role_arn": NEW_ARN, "external_id": EXT}
    )
    assert r.status_code == 201
    acc = r.json()
    assert acc["status"] == "pending" and acc["aws_account_id"] == "210987654321"

    dup = client.post("/api/accounts", json={"name": "x", "role_arn": NEW_ARN, "external_id": EXT})
    assert dup.status_code == 409
    refresh = client.post(f"/api/accounts/{acc['id']}/refresh")
    assert refresh.status_code == 409  # must test the connection first

    tested = client.post(f"/api/accounts/{acc['id']}/test").json()
    assert tested["ok"] is True
    assert client.get(f"/api/accounts/{acc['id']}").json()["status"] == "connected"

    patched = client.patch(f"/api/accounts/{acc['id']}", json={"name": "Staging 2"}).json()
    assert patched["name"] == "Staging 2"
    assert len(client.get("/api/accounts").json()) == 2
    assert client.delete(f"/api/accounts/{acc['id']}").status_code == 204
    assert client.get(f"/api/accounts/{acc['id']}").status_code == 404


@pytest.mark.parametrize(
    "body",
    [
        {"name": "x", "role_arn": "not-an-arn", "external_id": EXT},
        {"name": "x", "role_arn": NEW_ARN, "external_id": "short"},
        {"name": "", "role_arn": NEW_ARN, "external_id": EXT},
        {"name": "x", "role_arn": "arn:aws:iam::210987654321:role/Admin", "external_id": EXT},
    ],
)
def test_account_validation(ro, body):
    assert ro.post("/api/accounts", json=body).status_code == 422


def test_refresh_is_rate_limited(client):
    first = client.post(f"{ACC}/refresh")
    assert first.status_code == 202 and first.json()["trigger"] == "demo"
    second = client.post(f"{ACC}/refresh")
    assert second.status_code == 429
    assert 3500 <= int(second.headers["Retry-After"]) <= 3600
    assert "Cost Explorer" in second.json()["detail"]
    runs = client.get(f"{ACC}/runs").json()
    assert len(runs) == 1 and runs[0]["status"] == "succeeded"


def test_unknown_account_404(ro):
    assert ro.get("/api/accounts/nope/costs/summary").status_code == 404


# ---------------------------------------------------------------- costs


def test_cost_summary(ro):
    s = ro.get(f"{ACC}/costs/summary").json()
    assert s["month_to_date_usd"] > 0 and s["last_month_usd"] > 1500
    assert s["forecast_source"] == "cost_explorer"
    assert s["linear_forecast_usd"] > 0 and s["cost_explorer_forecast_usd"] > 0
    assert 80 < s["budget"]["forecast_pct"] < 100
    assert s["potential_savings_usd"] > 300 and s["open_findings"] >= 15
    assert s["anomalies_30d"] == 1


def test_daily_breakdown_forecast_anomalies(ro):
    daily = ro.get(f"{ACC}/costs/daily").json()
    assert len(daily["days"]) == 90
    assert daily["services"][-1] == "Other" and len(daily["services"]) == 7  # top 6 + Other
    assert daily["total_usd"] == pytest.approx(sum(d["total"] for d in daily["days"]), abs=0.5)

    by_service = ro.get(f"{ACC}/costs/breakdown", params={"dimension": "service"}).json()["items"]
    assert by_service[0]["key"] == "Amazon Elastic Compute Cloud - Compute"
    by_region = ro.get(f"{ACC}/costs/breakdown", params={"dimension": "region"}).json()["items"]
    assert {r["key"] for r in by_region} == {"ap-south-1", "us-east-1", "global"}
    by_tag = ro.get(
        f"{ACC}/costs/breakdown", params={"dimension": "tag", "tag_key": "Environment"}
    ).json()["items"]
    assert by_tag[0]["key"] == "prod" and any(r["key"] == "(untagged)" for r in by_tag)
    assert ro.get(f"{ACC}/costs/breakdown", params={"dimension": "tag"}).status_code == 422

    forecast = ro.get(f"{ACC}/costs/forecast").json()
    assert forecast["linear"]["days_used"] >= 28
    assert forecast["cost_explorer"]["lower_usd"] < forecast["cost_explorer"]["forecast_usd"]
    assert any("predicted" in d for d in forecast["days"])

    anomalies = ro.get(f"{ACC}/costs/anomalies").json()
    assert anomalies[0]["scope"] == "Total" and anomalies[0]["excess_usd"] > 50


def test_cost_range_validation(ro):
    bad = ro.get(f"{ACC}/costs/daily", params={"start": "2026-10-05", "end": "2026-10-01"})
    assert bad.status_code == 422
    custom = ro.get(f"{ACC}/costs/daily", params={"start": "2026-09-01", "end": "2026-09-30"})
    assert len(custom.json()["days"]) == 30


# ---------------------------------------------------------------- resources


def test_resource_filters_and_sorting(ro):
    all_ = ro.get(f"{ACC}/resources").json()
    assert all_["total"] == 44 and all_["regions"] == ["ap-south-1", "us-east-1"]
    costs = [r["est_monthly_cost_usd"] for r in all_["items"]]
    assert costs == sorted(costs, reverse=True)

    ec2 = ro.get(f"{ACC}/resources", params={"type": "ec2_instance"}).json()
    assert ec2["total"] == 12
    us = ro.get(f"{ACC}/resources", params={"region": "us-east-1", "state": "running"}).json()
    assert all(r["region"] == "us-east-1" and r["state"] == "running" for r in us["items"])
    search = ro.get(f"{ACC}/resources", params={"q": "jenkins"}).json()
    assert {r["name"] for r in search["items"]} == {"jenkins-old", "jenkins-old-root"}
    tag_search = ro.get(f"{ACC}/resources", params={"q": "owner=data-team"}).json()
    assert tag_search["total"] > 0
    untagged = ro.get(f"{ACC}/resources", params={"missing_tag": "Owner"}).json()
    assert all("Owner" not in r["tags"] for r in untagged["items"])
    by_name = ro.get(f"{ACC}/resources", params={"sort": "name", "order": "asc"}).json()["items"]
    names = [(r["name"] or r["resource_id"]).lower() for r in by_name]
    assert names == sorted(names)


def test_csv_export(ro):
    r = ro.get(f"{ACC}/resources/export.csv", params={"type": "ebs_volume"})
    assert r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    rows = list(csv.DictReader(io.StringIO(r.text)))
    assert len(rows) == 11 and rows[0]["type"] == "ebs_volume"
    assert float(rows[0]["est_monthly_cost_usd"]) >= float(rows[-1]["est_monthly_cost_usd"])


# ---------------------------------------------------------------- findings & settings


def test_findings_listing(ro):
    data = ro.get(f"{ACC}/findings").json()
    severities = [f["severity"] for f in data["items"]]
    order = {"high": 0, "medium": 1, "low": 2}
    assert severities == sorted(severities, key=order.get)
    assert data["total_monthly_savings_usd"] == pytest.approx(
        sum(f["est_monthly_savings_usd"] for f in data["items"]), abs=0.01
    )
    assert sum(r["count"] for r in data["by_rule"]) == data["total"]
    high = ro.get(f"{ACC}/findings", params={"severity": "high"}).json()
    assert all(f["severity"] == "high" for f in high["items"])
    rds = ro.get(f"{ACC}/findings", params={"rule_id": "rds-idle"}).json()
    assert rds["total"] == 1


def test_dismiss_finding(client):
    first = client.get(f"{ACC}/findings").json()["items"][0]
    r = client.patch(f"{ACC}/findings/{quote(first['id'], safe='')}", json={"status": "dismissed"})
    assert r.status_code == 200 and r.json()["status"] == "dismissed"
    open_ids = [f["id"] for f in client.get(f"{ACC}/findings").json()["items"]]
    assert first["id"] not in open_ids
    dismissed = client.get(f"{ACC}/findings", params={"status": "dismissed"}).json()
    assert dismissed["total"] == 1
    assert client.patch(f"{ACC}/findings/nope", json={"status": "open"}).status_code == 404


def test_threshold_change_recomputes_findings(client):
    before = client.get(f"{ACC}/findings", params={"rule_id": "ec2-idle"}).json()["total"]
    r = client.put(f"{ACC}/settings", json={"rule_thresholds": {"ec2-idle": {"cpu_avg_pct": 2}}})
    assert r.status_code == 200
    after = client.get(f"{ACC}/findings", params={"rule_id": "ec2-idle"}).json()["total"]
    assert after < before
    rules = {r["id"]: r for r in client.get(f"{ACC}/rules").json()}
    assert rules["ec2-idle"]["thresholds"]["cpu_avg_pct"] == 2
    assert rules["ec2-idle"]["thresholds"]["cpu_max_pct"] == 20  # default kept
    bad = client.put(f"{ACC}/settings", json={"rule_thresholds": {"ec2-idle": {"nope": 1}}})
    assert bad.status_code == 422
    assert client.post(f"{ACC}/findings/recompute").json()["open_findings"] > 0


def test_settings_slack_webhook_is_write_only(client):
    bad = client.put(f"{ACC}/settings", json={"slack_webhook_url": "http://evil.example"})
    assert bad.status_code == 422
    url = "https://hooks.slack.com/services/T000/B000/XXXX"
    s = client.put(
        f"{ACC}/settings",
        json={"slack_webhook_url": url, "slack_enabled": True, "alert_emails": ["a@b.co"]},
    ).json()
    assert s["slack_configured"] is True and s["slack_enabled"] is True
    assert url not in str(s) and url not in client.get(f"{ACC}/settings").text
    cleared = client.put(f"{ACC}/settings", json={"slack_webhook_url": ""}).json()
    assert cleared["slack_configured"] is False and cleared["slack_enabled"] is False


def test_required_tags_setting_changes_compliance(client):
    before = client.get(f"{ACC}/tags/compliance").json()
    client.put(f"{ACC}/settings", json={"required_tags": ["Project", " "]})
    after = client.get(f"{ACC}/tags/compliance").json()
    assert after["required_tags"] == ["Project"]
    assert after["compliance_pct"] > before["compliance_pct"]


# ---------------------------------------------------------------- budgets, alerts, tags


def test_budget_get_put(client):
    b = client.get(f"{ACC}/budget").json()
    assert b["budget"]["monthly_limit_usd"] == 2600 and b["status"]["forecast_pct"] > 80
    put = client.put(f"{ACC}/budget", json={"monthly_limit_usd": 5000, "thresholds": [50, 90]})
    assert put.json()["thresholds"] == [50, 90]
    assert client.get(f"{ACC}/budget").json()["status"]["crossed"] == [
        {"threshold": 50, "kind": "forecast"}
    ]
    assert client.put(f"{ACC}/budget", json={"monthly_limit_usd": -1}).status_code == 422


def test_alert_history_has_budget_and_spike(ro):
    alerts = ro.get(f"{ACC}/alerts").json()
    types = {a["type"] for a in alerts}
    assert {"budget_forecast", "anomaly"} <= types
    spike = next(a for a in alerts if a["dedup_key"].endswith("#Total"))
    assert spike["severity"] == "high" and "email" in spike["channels_sent"]


def test_test_alert_endpoint(client):
    res = client.post(f"{ACC}/alerts/test").json()["results"]
    assert res == {"email": "sent", "slack": "disabled"}


def test_tag_costs(ro):
    r = ro.get(f"{ACC}/tags/costs", params={"tag_key": "Project"}).json()
    assert {i["key"] for i in r["items"]} >= {"payments-api", "(untagged)"}
    assert ro.get(f"{ACC}/tags/costs").status_code == 422


# ---------------------------------------------------------------- remediation


def _finding(client, rule_id):
    f = client.get(f"{ACC}/findings", params={"rule_id": rule_id}).json()["items"][0]
    f["id"] = quote(f["id"], safe="")  # ids contain '#', which must be URL-encoded
    return f


def test_remediation_disabled_by_default(ro):
    f = _finding(ro, "eip-unassociated")
    p = ro.get(f"{ACC}/remediation/{f['id']}").json()
    assert p["supported"] and not p["enabled"]
    assert "REMEDIATION_ENABLED" in p["disabled_reason"]
    r = ro.post(
        f"{ACC}/remediation/{f['id']}",
        json={"confirm": True, "confirm_resource_id": f["resource_id"]},
    )
    assert r.status_code == 403


def test_remediation_flow_when_enabled(monkeypatch):
    client = fresh_client(monkeypatch, DEMO_MODE="true", REMEDIATION_ENABLED="true")
    f = _finding(client, "eip-unassociated")
    plan = client.get(f"{ACC}/remediation/{f['id']}").json()
    assert plan["enabled"] and plan["api_call"] == "ec2:ReleaseAddress"
    assert plan["monthly_savings_usd"] == pytest.approx(3.65)

    wrong = client.post(
        f"{ACC}/remediation/{f['id']}", json={"confirm": True, "confirm_resource_id": "typo"}
    )
    assert wrong.status_code == 400
    ok = client.post(
        f"{ACC}/remediation/{f['id']}",
        json={"confirm": True, "confirm_resource_id": f["resource_id"]},
    )
    assert ok.status_code == 200 and "(demo)" in ok.json()["message"]
    assert client.get(f"{ACC}/remediation/{f['id']}").json()["enabled"] is False  # now resolved
    ids = {r["resource_id"] for r in client.get(f"{ACC}/resources").json()["items"]}
    assert f["resource_id"] not in ids

    idle = _finding(client, "ec2-idle")
    client.post(
        f"{ACC}/remediation/{idle['id']}",
        json={"confirm": True, "confirm_resource_id": idle["resource_id"]},
    )
    inst = client.get(f"{ACC}/resources", params={"q": idle["resource_id"]}).json()["items"][0]
    assert inst["state"] == "stopped"

    unsupported = _finding(client, "s3-no-lifecycle")
    p = client.get(f"{ACC}/remediation/{unsupported['id']}").json()
    assert p["supported"] is False and p["enabled"] is False
    assert client.get(f"{ACC}/remediation/nope").status_code == 404
