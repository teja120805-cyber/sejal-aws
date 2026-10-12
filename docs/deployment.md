# Deployment

> **Nothing in this repository deploys automatically.** CI only runs `cdk synth`. You deploy
> yourself, from your own machine, with the steps below.

## Prerequisites

- An AWS account for the platform, with an admin-capable profile configured (`aws configure`)
- Python 3.12, Node.js 22+, and the AWS CLI v2
- Docker is **not** needed: the Lambda package is built from pre-built Linux ARM64 wheels

## 1. Build

```bash
# from the repo root, with the venv active
python infra/scripts/build_lambda.py        # -> infra/build/lambda (about 70 MB unzipped)
cd frontend && npm ci && npm run build && cd ..   # -> frontend/dist
```

## 2. Bootstrap CDK (once per account and region)

```bash
cd infra
python -m venv .venv && .venv\Scripts\activate    # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
npm ci
npx cdk bootstrap aws://<ACCOUNT_ID>/ap-south-1
```

## 3. Review, then deploy

```bash
npx cdk diff                      # review what will be created
npx cdk deploy                    # creates the stack "FinOpsPlatform" in ap-south-1
```

Optional context flags, for example `npx cdk deploy -c stage=prod -c remediationEnabled=true`:

| Context | Default | Effect |
|---|---|---|
| `stage` | `dev` | `prod` retains the table, user pool and bucket on stack deletion, and enables deletion protection |
| `region` | `ap-south-1` | Deployment region |
| `remediationEnabled` | `false` | Allow one-click remediation (customers still need the remediation role) |
| `usdInrRate` | `88.0` | Fixed rate for the INR toggle |
| `cognitoDomainPrefix` | `finops-<stage>-<account>` | Hosted UI domain prefix (must be globally unique) |
| `customerTemplateUrl` | none | S3 URL of the read-only template, for the console quick-create link |

The stack outputs `SiteUrl`, `UserPoolId`, `PlatformAccountId` and the other identifiers.

## 4. Create the first user

Self sign-up is disabled. Invite yourself:

```bash
aws cognito-idp admin-create-user --user-pool-id <UserPoolId> \
  --username you@example.com --user-attributes Name=email,Value=you@example.com Name=email_verified,Value=true
```

Cognito emails a temporary password. Open `SiteUrl`, sign in and set a new password.

## 5. Connect an AWS account

In the app: **Settings → Connect another account**, or the account dropdown → "+ Connect account".

1. Deploy `finops-readonly-role.yaml` in the account to monitor, with the shown
   `TrustedAccountId` (`PlatformAccountId`) and `ExternalId`. You can use the CLI command shown
   on the page.
2. Paste the `RoleArn` output.
3. Click **Test connection**. This is free.
4. Click **Refresh** to collect now, or wait for the daily run at 02:00 IST.

For cost-by-tag data, activate the `Project`, `Owner` and `Environment` **cost allocation tags** in
Billing → Cost allocation tags in the monitored account. Cost Explorer only reports activated tags.

## 6. Alerts

- **Email:** add addresses in Settings → Notifications. Each person receives an AWS SNS
  confirmation email and must click the link once.
- **Slack:** create an Incoming Webhook in Slack and paste its URL in Settings. It's stored as an
  SSM SecureString.

## Running costs (rough estimate)

| Item | Cost |
|---|---|
| Cost Explorer API | About 5 calls per account per day, $0.01 each: about **$1.50 per account per month**, plus manual refreshes (limited to one per hour) |
| Lambda, API Gateway, DynamoDB on-demand, CloudFront, Cognito | A few cents per month at project scale; mostly within the free tier |

## Tear down

```bash
cd infra && npx cdk destroy
```

With `stage=dev` this deletes everything, including the table and bucket contents. With
`stage=prod` the table, user pool and bucket are retained.

## Local, non-demo mode against AWS (optional)

Set `DEMO_MODE=false`, `TABLE_NAME`, the `COGNITO_*` variables and so on in `.env` (see
`.env.example`), with AWS credentials for the platform account in your environment. Then run
`uvicorn finops.api.main:app`. Manual refresh runs in-process when `COLLECTOR_FUNCTION_NAME` is
empty.
