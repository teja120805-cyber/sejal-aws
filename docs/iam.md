# IAM role design

## Principles

1. **No stored credentials.** The platform never asks for, stores or logs access keys. It runs
   under its own Lambda execution role and obtains **temporary** credentials (1 hour) for a
   customer account with `sts:AssumeRole`. Those credentials only ever live in memory.
2. **Least privilege, read-only by default.** The customer role can read cost data, describe
   resources and read metrics. Nothing else.
3. **ExternalId against the confused deputy.** Each connected account gets a random ExternalId
   (a UUID). The customer's trust policy only allows `sts:AssumeRole` when that exact ExternalId is
   presented. Suppose another user of the platform learned your role ARN and tried to connect it to
   their own workspace. The platform would send *their* ExternalId, and AWS would reject the call.
4. **Writes are separate and opt-in.** Remediation uses a second, optional role with three write
   actions. It is gated four times:
   - `REMEDIATION_ENABLED` on the platform;
   - the role must exist in the customer account;
   - a confirmation dialog in which the user types the resource ID;
   - an EC2 `DryRun` permission check before the real call.

## Customer read-only role (`infra/customer-templates/finops-readonly-role.yaml`)

The trust policy allows `arn:aws:iam::<TrustedAccountId>:root` to call `sts:AssumeRole`, on the
condition `sts:ExternalId = <ExternalId>`. `MaxSessionDuration` is 3600 seconds.

| Statement | Actions | Why |
|---|---|---|
| CostExplorerRead | `ce:GetCostAndUsage`, `ce:GetCostForecast`, `ce:GetDimensionValues`, `ce:GetTags` | Daily cost by service, region and tag; month-end forecast |
| ComputeAndStorageDescribe | `ec2:DescribeRegions/Instances/Volumes/Snapshots/Addresses`, `rds:DescribeDBInstances`, `rds:ListTagsForResource`, `lambda:ListFunctions`, `lambda:ListTags` | Inventory across enabled regions |
| S3BucketMetadataOnly | `s3:ListAllMyBuckets`, `s3:GetBucketLocation`, `s3:GetLifecycleConfiguration`, `s3:GetBucketTagging`, `s3:GetBucketVersioning` | Bucket settings. **No `s3:GetObject`**: the platform cannot read your data. |
| MetricsRead | `cloudwatch:GetMetricData`, `GetMetricStatistics`, `ListMetrics` | CPU, DB connections, Lambda invocations, bucket size |
| PricingRead | `pricing:GetProducts` | Optional live prices |

These statements use `Resource: "*"` because these describe and list APIs do not support
resource-level permissions. AWS requires `*` for them.

**Role name.** The template only accepts role names starting with `FinOps`. The platform's own
policy allows `sts:AssumeRole` only on `arn:aws:iam::*:role/FinOps*`. So even if the platform were
compromised, it couldn't assume an arbitrary role such as `OrganizationAccountAccessRole`.

**What it cannot do:** create, modify or delete any resource; read S3 objects, database contents,
secrets or parameters; change IAM.

## Optional remediation role (`finops-remediation-role.yaml`)

| Statement | Actions | Condition |
|---|---|---|
| RemediationActions | `ec2:StopInstances`, `ec2:DeleteVolume`, `ec2:ReleaseAddress` | Denied for resources tagged `finops:protected` |
| SafetySnapshotBeforeDelete | `ec2:CreateSnapshot`, `ec2:CreateTags` | A snapshot is taken before any volume is deleted |
| DescribeForChecks | `ec2:DescribeInstances/Volumes/Addresses` | |

The trust policy uses the same ExternalId. Deleting the stack revokes write access instantly.

## Platform roles (created by CDK)

| Role | Permissions |
|---|---|
| API Lambda | DynamoDB read/write on its table; `sts:AssumeRole` on `role/FinOps*`; `sns:Publish` and subscription management on the alerts topic; `ssm:Get/Put/DeleteParameter` on `parameter/finops/slack-webhook/*`; `lambda:InvokeFunction` on the collector; `pricing:GetProducts` |
| Collector Lambda | Same, minus the SSM writes, SNS subscription management and invoke |

The platform roles have **no** `ce:*` or `ec2:*` permissions of their own. Every AWS read goes
through the customer's role. An infrastructure test (`test_platform_never_gets_cost_explorer_or_ec2_rights_of_its_own`)
asserts this.

## Revoking access

The customer deletes the `FinOpsReadOnlyRole` stack (and the remediation stack, if deployed). The
next collection fails with AccessDenied, and the account is marked `error` with that message.
"Disconnect" in Settings deletes the platform's stored data for the account.
