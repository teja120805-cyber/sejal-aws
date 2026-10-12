#!/usr/bin/env python3
"""CDK entrypoint. Synthesises CloudFormation only; deploying is a separate, explicit step.

Stacks are added in Phase 7: data (DynamoDB), api (Lambda + HTTP API), collector
(Lambda + EventBridge), frontend (S3 + CloudFront), auth (Cognito), alerts (SNS).
"""

import aws_cdk as cdk

from stacks.platform_stack import FinOpsPlatformStack

app = cdk.App()

FinOpsPlatformStack(
    app,
    "FinOpsPlatform",
    # Account is resolved from the CLI profile at deploy time; region defaults to Mumbai.
    env=cdk.Environment(region=app.node.try_get_context("region") or "ap-south-1"),
    description="FinOps platform: AWS cost monitoring and optimisation",
)

cdk.Tags.of(app).add("Project", "finops-platform")
cdk.Tags.of(app).add("Owner", "finops")
cdk.Tags.of(app).add("Environment", app.node.try_get_context("stage") or "dev")

app.synth()
