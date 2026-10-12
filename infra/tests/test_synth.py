"""Synthesise the stack once and assert the properties that matter (security, cost, schedule)."""

import json

import aws_cdk as cdk
import pytest
from aws_cdk.assertions import Match, Template

from stacks.platform_stack import FinOpsPlatformStack


@pytest.fixture(scope="module")
def template(tmp_path_factory) -> Template:
    asset = tmp_path_factory.mktemp("lambda")
    (asset / "finops").mkdir()
    (asset / "finops" / "__init__.py").write_text("")
    app = cdk.App(context={"stage": "dev"})
    stack = FinOpsPlatformStack(
        app,
        "Test",
        lambda_asset=asset,
        frontend_dist=asset,
        env=cdk.Environment(region="ap-south-1"),
    )
    return Template.from_stack(stack)


def policies(template: Template) -> list[dict]:
    statements = []
    for res in template.find_resources("AWS::IAM::Policy").values():
        statements += res["Properties"]["PolicyDocument"]["Statement"]
    return statements


def test_dynamodb_single_table_with_gsi_ttl_and_pitr(template):
    template.has_resource_properties(
        "AWS::DynamoDB::GlobalTable",
        {
            "KeySchema": [
                {"AttributeName": "PK", "KeyType": "HASH"},
                {"AttributeName": "SK", "KeyType": "RANGE"},
            ],
            "BillingMode": "PAY_PER_REQUEST",
            "TimeToLiveSpecification": {"AttributeName": "expires_at", "Enabled": True},
            "GlobalSecondaryIndexes": [Match.object_like({"IndexName": "GSI1"})],
            "Replicas": [
                Match.object_like(
                    {"PointInTimeRecoverySpecification": {"PointInTimeRecoveryEnabled": True}}
                )
            ],
        },
    )


def test_two_python_312_arm_lambdas(template):
    fns = template.find_resources(
        "AWS::Lambda::Function", {"Properties": {"Runtime": "python3.12"}}
    )
    handlers = {f["Properties"]["Handler"]: f["Properties"] for f in fns.values()}
    assert set(handlers) >= {"finops.api.main.handler", "collector_handler.handler"}
    for props in handlers.values():
        assert props["Architectures"] == ["arm64"]
        assert props["Environment"]["Variables"]["DEMO_MODE"] == "false"
        assert props["Environment"]["Variables"]["REMEDIATION_ENABLED"] == "false"
    assert handlers["collector_handler.handler"]["Timeout"] == 900
    assert handlers["finops.api.main.handler"]["Timeout"] == 29


def test_daily_schedule_at_2am_ist(template):
    template.has_resource_properties(
        "AWS::Events::Rule", {"ScheduleExpression": "cron(30 20 * * ? *)", "State": "ENABLED"}
    )


def test_assume_role_limited_to_finops_roles(template):
    assume = [s for s in policies(template) if s.get("Action") == "sts:AssumeRole"]
    assert assume, "lambdas must be able to assume customer roles"
    for s in assume:
        assert s["Resource"] == "arn:aws:iam::*:role/FinOps*"


def test_no_wildcard_write_permissions(template):
    """Only read-style actions may use Resource '*'."""
    read_only_prefixes = ("pricing:Get", "xray:Put", "xray:Get")
    for s in policies(template):
        resources = s["Resource"] if isinstance(s["Resource"], list) else [s["Resource"]]
        actions = s["Action"] if isinstance(s["Action"], list) else [s["Action"]]
        if "*" in resources:
            assert all(a.startswith(read_only_prefixes) for a in actions), s


def test_platform_never_gets_cost_explorer_or_ec2_rights_of_its_own(template):
    """All AWS reads happen through the customer's role, not the platform's identity."""
    text = json.dumps(policies(template))
    assert '"ce:' not in text and '"ec2:' not in text


def test_api_routes_require_jwt_except_health_and_config(template):
    routes = template.find_resources("AWS::ApiGatewayV2::Route")
    by_key = {r["Properties"]["RouteKey"]: r["Properties"] for r in routes.values()}
    assert by_key["ANY /api/{proxy+}"]["AuthorizationType"] == "JWT"
    for public in ("GET /api/health", "GET /api/config"):
        assert by_key[public].get("AuthorizationType", "NONE") == "NONE"
    template.has_resource_properties(
        "AWS::ApiGatewayV2::Authorizer",
        {"AuthorizerType": "JWT", "IdentitySource": ["$request.header.Authorization"]},
    )


def test_cognito_pkce_client_without_secret(template):
    template.has_resource_properties(
        "AWS::Cognito::UserPoolClient",
        {
            "GenerateSecret": False,
            "AllowedOAuthFlows": ["code"],
            "AllowedOAuthFlowsUserPoolClient": True,
        },
    )
    template.has_resource_properties(
        "AWS::Cognito::UserPool", {"AdminCreateUserConfig": {"AllowAdminCreateUserOnly": True}}
    )


def test_site_bucket_private_and_served_via_oac(template):
    template.has_resource_properties(
        "AWS::S3::Bucket",
        {
            "PublicAccessBlockConfiguration": {
                "BlockPublicAcls": True,
                "BlockPublicPolicy": True,
                "IgnorePublicAcls": True,
                "RestrictPublicBuckets": True,
            }
        },
    )
    template.resource_count_is("AWS::CloudFront::OriginAccessControl", 1)
    dist = next(iter(template.find_resources("AWS::CloudFront::Distribution").values()))
    config = dist["Properties"]["DistributionConfig"]
    assert config["DefaultCacheBehavior"]["ViewerProtocolPolicy"] == "redirect-to-https"
    api_behaviour = next(b for b in config["CacheBehaviors"] if b["PathPattern"] == "/api/*")
    assert api_behaviour["ViewerProtocolPolicy"] == "https-only"
    assert "CustomErrorResponses" not in config  # SPA routing via a function, not error pages


def test_alerts_topic_enforces_tls(template):
    template.resource_count_is("AWS::SNS::Topic", 1)
    template.has_resource_properties(
        "AWS::SNS::TopicPolicy",
        {
            "PolicyDocument": {
                "Statement": Match.array_with([Match.object_like({"Effect": "Deny"})])
            }
        },
    )


def test_outputs(template):
    outputs = template.find_outputs("*")
    assert {"SiteUrl", "ApiUrl", "UserPoolId", "PlatformAccountId", "TableName"} <= set(outputs)
