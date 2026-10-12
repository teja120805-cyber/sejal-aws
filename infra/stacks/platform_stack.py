"""The whole platform as one CloudFormation stack.

One stack (with focused helper methods) rather than six cross-referencing stacks: it deploys and
rolls back atomically, and avoids CloudFormation export lock-in between stacks. Nothing here is
deployed by `cdk synth`; deploying is an explicit `cdk deploy`.
"""

from pathlib import Path

from aws_cdk import (
    Annotations,
    Aws,
    CfnOutput,
    Duration,
    RemovalPolicy,
    Stack,
)
from aws_cdk import aws_apigatewayv2 as apigw
from aws_cdk import aws_apigatewayv2_authorizers as authorizers
from aws_cdk import aws_apigatewayv2_integrations as integrations
from aws_cdk import aws_cloudfront as cloudfront
from aws_cdk import aws_cloudfront_origins as origins
from aws_cdk import aws_cognito as cognito
from aws_cdk import aws_dynamodb as dynamodb
from aws_cdk import aws_events as events
from aws_cdk import aws_events_targets as targets
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_s3_deployment as s3deploy
from aws_cdk import aws_sns as sns
from constructs import Construct

REPO = Path(__file__).resolve().parents[2]
SLACK_PARAM_PREFIX = "/finops/slack-webhook"

# SPA routing: send unknown paths (no file extension) to index.html. Runs only on the S3
# behaviour, so API 404s are never rewritten.
SPA_REWRITE = """
function handler(event) {
  var req = event.request;
  if (!req.uri.includes('.')) { req.uri = '/index.html'; }
  return req;
}
"""


class FinOpsPlatformStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        lambda_asset: Path | None = None,
        frontend_dist: Path | None = None,
        **kwargs: object,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)  # type: ignore[arg-type]
        ctx = self.node.try_get_context
        self.stage = ctx("stage") or "dev"
        prod = self.stage == "prod"
        removal = RemovalPolicy.RETAIN if prod else RemovalPolicy.DESTROY

        self.table = self._table(removal, prod)
        self.topic = sns.Topic(self, "AlertsTopic", display_name="FinOps alerts", enforce_ssl=True)
        self.site_bucket, self.distribution = self._frontend_hosting(removal)
        self.user_pool, self.user_pool_client, self.domain = self._auth(removal)

        code = self._lambda_code(lambda_asset or REPO / "infra" / "build" / "lambda")
        common_env = {
            "DEMO_MODE": "false",
            "TABLE_NAME": self.table.table_name,
            "ALERTS_TOPIC_ARN": self.topic.topic_arn,
            "SLACK_WEBHOOK_SSM_PREFIX": SLACK_PARAM_PREFIX,
            "PLATFORM_ACCOUNT_ID": Aws.ACCOUNT_ID,
            "USD_INR_RATE": str(ctx("usdInrRate") or "88.0"),
            "REMEDIATION_ENABLED": "true"
            if ctx("remediationEnabled") in (True, "true")
            else "false",
            "USE_PRICING_API": "false",
        }
        self.collector = self._collector(code, common_env)
        self.api_fn, self.http_api = self._api(code, common_env)
        self._wire_cloudfront_api()
        self._deploy_frontend(frontend_dist or REPO / "frontend" / "dist")
        self._outputs()

    # ------------------------------------------------------------------ data

    def _table(self, removal: RemovalPolicy, prod: bool) -> dynamodb.TableV2:
        table = dynamodb.TableV2(
            self,
            "Table",
            partition_key=dynamodb.Attribute(name="PK", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="SK", type=dynamodb.AttributeType.STRING),
            billing=dynamodb.Billing.on_demand(),
            time_to_live_attribute="expires_at",  # alert de-duplication keys expire
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(
                point_in_time_recovery_enabled=True
            ),
            deletion_protection=prod,
            removal_policy=removal,
            global_secondary_indexes=[
                dynamodb.GlobalSecondaryIndexPropsV2(
                    index_name="GSI1",
                    partition_key=dynamodb.Attribute(
                        name="GSI1PK", type=dynamodb.AttributeType.STRING
                    ),
                    sort_key=dynamodb.Attribute(name="GSI1SK", type=dynamodb.AttributeType.STRING),
                )
            ],
        )
        return table

    # ------------------------------------------------------------------ auth

    def _auth(
        self, removal: RemovalPolicy
    ) -> tuple[cognito.UserPool, cognito.UserPoolClient, cognito.UserPoolDomain]:
        pool = cognito.UserPool(
            self,
            "UserPool",
            self_sign_up_enabled=False,  # admins invite users
            sign_in_aliases=cognito.SignInAliases(email=True),
            auto_verify=cognito.AutoVerifiedAttrs(email=True),
            password_policy=cognito.PasswordPolicy(
                min_length=12,
                require_digits=True,
                require_lowercase=True,
                require_uppercase=True,
                require_symbols=False,
            ),
            mfa=cognito.Mfa.OPTIONAL,
            mfa_second_factor=cognito.MfaSecondFactor(sms=False, otp=True),
            account_recovery=cognito.AccountRecovery.EMAIL_ONLY,
            removal_policy=removal,
        )
        site = f"https://{self.distribution.distribution_domain_name}"
        local = "http://localhost:5173"
        client = pool.add_client(
            "WebClient",
            generate_secret=False,  # public SPA client: Authorization Code + PKCE
            auth_flows=cognito.AuthFlow(user_srp=True),
            o_auth=cognito.OAuthSettings(
                flows=cognito.OAuthFlows(authorization_code_grant=True),
                scopes=[
                    cognito.OAuthScope.OPENID,
                    cognito.OAuthScope.EMAIL,
                    cognito.OAuthScope.PROFILE,
                ],
                callback_urls=[f"{site}/auth/callback", f"{local}/auth/callback"],
                logout_urls=[site, local],
            ),
            access_token_validity=Duration.hours(1),
            id_token_validity=Duration.hours(1),
            refresh_token_validity=Duration.days(7),
            prevent_user_existence_errors=True,
        )
        prefix = (
            self.node.try_get_context("cognitoDomainPrefix")
            or f"finops-{self.stage}-{Aws.ACCOUNT_ID}"
        )
        domain = pool.add_domain(
            "Domain", cognito_domain=cognito.CognitoDomainOptions(domain_prefix=prefix)
        )
        return pool, client, domain

    # ------------------------------------------------------------------ lambdas

    def _lambda_code(self, asset: Path) -> lambda_.Code:
        if (asset / "finops").is_dir():
            return lambda_.Code.from_asset(str(asset))
        Annotations.of(self).add_warning(
            f"Lambda package not found at {asset}. Run `python infra/scripts/build_lambda.py` "
            "before `cdk deploy`. Synthesising with a placeholder."
        )
        return lambda_.Code.from_inline(
            "def handler(event, context):\n    raise RuntimeError('not built')\n"
        )

    def _function(
        self,
        id_: str,
        code: lambda_.Code,
        handler: str,
        env: dict[str, str],
        timeout: Duration,
        memory: int,
    ) -> lambda_.Function:
        log_group = logs.LogGroup(
            self,
            f"{id_}Logs",
            retention=logs.RetentionDays.ONE_MONTH,
            removal_policy=RemovalPolicy.DESTROY,
        )
        fn = lambda_.Function(
            self,
            id_,
            runtime=lambda_.Runtime.PYTHON_3_12,
            architecture=lambda_.Architecture.ARM_64,  # Graviton: ~20% cheaper per GB-second
            code=code,
            handler=handler,
            timeout=timeout,
            memory_size=memory,
            environment=env,  # AWS_REGION is set by Lambda itself
            log_group=log_group,
            tracing=lambda_.Tracing.ACTIVE,
        )
        self._grant_common(fn)
        return fn

    def _grant_common(self, fn: lambda_.Function) -> None:
        self.table.grant_read_write_data(fn)
        self.topic.grant_publish(fn)
        # Cross-account access: ONLY roles named FinOps* (the customer templates enforce the
        # name), and the customer's trust policy additionally requires the ExternalId.
        fn.add_to_role_policy(
            iam.PolicyStatement(
                sid="AssumeCustomerFinOpsRoles",
                actions=["sts:AssumeRole"],
                resources=["arn:aws:iam::*:role/FinOps*"],
            )
        )
        fn.add_to_role_policy(
            iam.PolicyStatement(sid="PricingRead", actions=["pricing:GetProducts"], resources=["*"])
        )
        fn.add_to_role_policy(
            iam.PolicyStatement(
                sid="ReadSlackWebhooks",
                actions=["ssm:GetParameter"],
                resources=[self._slack_param_arn()],
            )
        )

    def _slack_param_arn(self) -> str:
        return (
            f"arn:{Aws.PARTITION}:ssm:{Aws.REGION}:{Aws.ACCOUNT_ID}:parameter{SLACK_PARAM_PREFIX}/*"
        )

    def _collector(self, code: lambda_.Code, env: dict[str, str]) -> lambda_.Function:
        fn = self._function(
            "CollectorFunction", code, "collector_handler.handler", env, Duration.minutes(15), 1024
        )
        events.Rule(
            self,
            "DailyCollection",
            description="Collect costs and inventory daily at 02:00 IST",
            schedule=events.Schedule.cron(minute="30", hour="20"),  # 20:30 UTC = 02:00 IST
            targets=[targets.LambdaFunction(fn, retry_attempts=2, max_event_age=Duration.hours(2))],
        )
        return fn

    def _api(
        self, code: lambda_.Code, env: dict[str, str]
    ) -> tuple[lambda_.Function, apigw.HttpApi]:
        site = f"https://{self.distribution.distribution_domain_name}"
        fn = self._function(
            "ApiFunction",
            code,
            "finops.api.main.handler",
            env
            | {
                "COGNITO_USER_POOL_ID": self.user_pool.user_pool_id,
                "COGNITO_APP_CLIENT_ID": self.user_pool_client.user_pool_client_id,
                "COGNITO_REGION": Aws.REGION,
                "COGNITO_DOMAIN": f"{self.domain.domain_name}.auth.{Aws.REGION}.amazoncognito.com",
                "COLLECTOR_FUNCTION_NAME": self.collector.function_name,
                "CORS_ORIGINS": f"{site},http://localhost:5173",
                "CUSTOMER_TEMPLATE_URL": str(
                    self.node.try_get_context("customerTemplateUrl") or ""
                ),
            },
            Duration.seconds(29),  # API Gateway's integration limit is 30 s
            1024,
        )
        self.collector.grant_invoke(fn)  # manual refresh runs the collector asynchronously
        fn.add_to_role_policy(
            iam.PolicyStatement(
                sid="ManageSlackWebhooks",
                actions=["ssm:PutParameter", "ssm:DeleteParameter"],
                resources=[self._slack_param_arn()],
            )
        )
        fn.add_to_role_policy(
            iam.PolicyStatement(
                sid="ManageAlertEmailSubscriptions",
                actions=[
                    "sns:Subscribe",
                    "sns:Unsubscribe",
                    "sns:ListSubscriptionsByTopic",
                    "sns:GetSubscriptionAttributes",
                ],
                resources=[self.topic.topic_arn],
            )
        )
        fn.add_to_role_policy(
            iam.PolicyStatement(
                sid="UnsubscribeNeedsWildcard",
                actions=["sns:Unsubscribe", "sns:GetSubscriptionAttributes"],
                resources=[f"{self.topic.topic_arn}:*"],
            )
        )

        authorizer = authorizers.HttpJwtAuthorizer(
            "CognitoAuthorizer",
            f"https://cognito-idp.{Aws.REGION}.amazonaws.com/{self.user_pool.user_pool_id}",
            jwt_audience=[self.user_pool_client.user_pool_client_id],
        )
        integration = integrations.HttpLambdaIntegration("ApiIntegration", fn)
        api = apigw.HttpApi(
            self, "HttpApi", description="FinOps platform API", create_default_stage=True
        )
        api.add_routes(
            path="/api/{proxy+}",
            methods=[apigw.HttpMethod.ANY],
            integration=integration,
            authorizer=authorizer,
        )
        # Public, unauthenticated routes (no secrets): health check and frontend bootstrap config.
        for path in ("/api/health", "/api/config"):
            api.add_routes(path=path, methods=[apigw.HttpMethod.GET], integration=integration)
        stage = api.default_stage.node.default_child  # type: ignore[union-attr]
        stage.add_property_override("DefaultRouteSettings.ThrottlingBurstLimit", 50)
        stage.add_property_override("DefaultRouteSettings.ThrottlingRateLimit", 20)
        return fn, api

    # ------------------------------------------------------------------ frontend

    def _frontend_hosting(
        self, removal: RemovalPolicy
    ) -> tuple[s3.Bucket, cloudfront.Distribution]:
        bucket = s3.Bucket(
            self,
            "SiteBucket",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            removal_policy=removal,
            auto_delete_objects=removal == RemovalPolicy.DESTROY,
        )
        spa = cloudfront.Function(
            self, "SpaRewrite", code=cloudfront.FunctionCode.from_inline(SPA_REWRITE)
        )
        headers = cloudfront.ResponseHeadersPolicy(
            self,
            "SecurityHeaders",
            security_headers_behavior=cloudfront.ResponseSecurityHeadersBehavior(
                strict_transport_security=cloudfront.ResponseHeadersStrictTransportSecurity(
                    access_control_max_age=Duration.days(365),
                    include_subdomains=True,
                    override=True,
                ),
                content_type_options=cloudfront.ResponseHeadersContentTypeOptions(override=True),
                frame_options=cloudfront.ResponseHeadersFrameOptions(
                    frame_option=cloudfront.HeadersFrameOption.DENY, override=True
                ),
                referrer_policy=cloudfront.ResponseHeadersReferrerPolicy(
                    referrer_policy=cloudfront.HeadersReferrerPolicy.STRICT_ORIGIN_WHEN_CROSS_ORIGIN,
                    override=True,
                ),
            ),
        )
        distribution = cloudfront.Distribution(
            self,
            "Distribution",
            comment="FinOps platform",
            default_root_object="index.html",
            price_class=cloudfront.PriceClass.PRICE_CLASS_200,  # includes India edge locations
            default_behavior=cloudfront.BehaviorOptions(
                origin=origins.S3BucketOrigin.with_origin_access_control(bucket),
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                response_headers_policy=headers,
                function_associations=[
                    cloudfront.FunctionAssociation(
                        function=spa, event_type=cloudfront.FunctionEventType.VIEWER_REQUEST
                    )
                ],
            ),
        )
        self._headers_policy = headers
        return bucket, distribution

    def _wire_cloudfront_api(self) -> None:
        """Serve /api/* from the same origin as the SPA: no CORS, and one domain for the user."""
        api_domain = f"{self.http_api.api_id}.execute-api.{Aws.REGION}.amazonaws.com"
        self.distribution.add_behavior(
            "/api/*",
            origins.HttpOrigin(
                api_domain, protocol_policy=cloudfront.OriginProtocolPolicy.HTTPS_ONLY
            ),
            viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.HTTPS_ONLY,
            allowed_methods=cloudfront.AllowedMethods.ALLOW_ALL,
            cache_policy=cloudfront.CachePolicy.CACHING_DISABLED,
            origin_request_policy=cloudfront.OriginRequestPolicy.ALL_VIEWER_EXCEPT_HOST_HEADER,
            response_headers_policy=self._headers_policy,
        )

    def _deploy_frontend(self, dist: Path) -> None:
        if not (dist / "index.html").is_file():
            Annotations.of(self).add_warning(
                f"Frontend build not found at {dist}; "
                "run `npm run build` in frontend/ before deploy."
            )
            return
        s3deploy.BucketDeployment(
            self,
            "DeploySite",
            sources=[s3deploy.Source.asset(str(dist))],
            destination_bucket=self.site_bucket,
            distribution=self.distribution,
            distribution_paths=["/*"],
        )

    # ------------------------------------------------------------------ outputs

    def _outputs(self) -> None:
        site = f"https://{self.distribution.distribution_domain_name}"
        CfnOutput(self, "SiteUrl", value=site, description="Open this in a browser")
        CfnOutput(self, "ApiUrl", value=self.http_api.api_endpoint)
        CfnOutput(self, "UserPoolId", value=self.user_pool.user_pool_id)
        CfnOutput(self, "UserPoolClientId", value=self.user_pool_client.user_pool_client_id)
        CfnOutput(self, "TableName", value=self.table.table_name)
        CfnOutput(self, "AlertsTopicArn", value=self.topic.topic_arn)
        CfnOutput(self, "CollectorFunctionName", value=self.collector.function_name)
        CfnOutput(
            self,
            "PlatformAccountId",
            value=Aws.ACCOUNT_ID,
            description="Customers enter this as TrustedAccountId in the role template",
        )
