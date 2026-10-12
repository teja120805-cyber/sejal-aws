# Waste detection rules

Each rule is a small class in `backend/src/finops/rules/definitions.py`. Every rule has an `id`, a
`severity`, a `description`, default thresholds, a `detect(resource, ctx)` method and an
`estimated_monthly_savings(resource, ctx)` method. Thresholds can be overridden per account
(Settings → Waste rules, or `PUT /settings` with `rule_thresholds`). Changing them re-evaluates the
findings immediately, with no AWS calls.

Savings use the static price table `backend/src/finops/pricing/prices.json`: ap-south-1 and
us-east-1 on-demand list prices, 730 hours per month. Other regions fall back to us-east-1, which is
usually the cheapest region, so estimates stay conservative. With `USE_PRICING_API=true`, EC2 prices
come live from the free AWS Pricing API.

| Rule id | Severity | Detects | Default thresholds | Estimated saving |
|---|---|---|---|---|
| `ec2-idle` | High | Running instance with low CPU over 7 days | `cpu_avg_pct` 5, `cpu_max_pct` 20, `stop_below_avg_pct` 2, `min_datapoints` 120 | Average below 2%: the full compute cost (stop it). Otherwise: current cost minus the price of one size smaller (e.g. m5.xlarge → m5.large). |
| `ec2-stopped-ebs` | Medium | Instance stopped for more than N days whose EBS volumes are still billed | `stopped_days` 7 | Cost of its attached volumes |
| `ebs-unattached` | Medium | Volume in state `available` | `min_age_days` 1 | Full volume cost |
| `ebs-gp2-to-gp3` | Low | gp2 volume | `min_size_gb` 1 | size × (gp2 − gp3) per GB-month, about 20% |
| `eip-unassociated` | Low | Elastic IP not associated | none | $0.005/h × 730 = $3.65 per month |
| `snapshot-old` | Low | Snapshot older than N days | `max_age_days` 90 | size × snapshot price (an upper bound, since snapshots are incremental) |
| `s3-no-lifecycle` | Low | Bucket of at least N GB with no enabled lifecycle rule | `min_size_gb` 1, `ia_share` 0.4 | size × `ia_share` × (Standard − Standard-IA) |
| `rds-idle` | High | `available` instance whose peak connections over 7 days are at or below N | `max_connections` 0.5 | Full instance cost (snapshot, then delete) |

## Design notes

- **Not flagging new instances.** `ec2-idle` requires at least `min_datapoints` hourly CPU samples
  (about 5 days). An instance launched yesterday has almost no data and would otherwise look idle.
- **Short peaks matter.** `ec2-idle` checks the maximum as well as the average, so a bastion host
  averaging 2.5% but peaking at 35% is not flagged. The demo data includes exactly this case.
- **No double counting.** A volume recommended for deletion (unattached, or attached to an instance
  stopped too long) is not also counted under gp2→gp3. Otherwise the same dollars would appear
  twice in "total potential savings". See `engine._drop_superseded`.
- **Stable finding IDs.** The ID is `rule_id#resource_id`, so re-running the rules updates findings
  in place:
  - still detected: `first_seen` and a user's "dismissed" status are kept;
  - no longer detected: the finding becomes `resolved`;
  - detected again later: it reopens.
- **Snapshots behind AMIs.** A snapshot created by `CreateImage` has `ami_id` set, and the
  recommendation says to deregister the AMI first. AWS won't delete the snapshot otherwise.
- **The S3 saving is an assumption.** Without S3 access logs we can't know which objects are cold.
  The configurable `ia_share` (default 40%) makes the assumption visible and adjustable.

## Adding a rule

1. Subclass `Rule` in `definitions.py`: set `id`, `title`, `severity`, `resource_type`,
   `description`, `default_thresholds`, and implement `detect`, `estimated_monthly_savings` and
   `explain`.
2. Add an instance to `ALL_RULES`.
3. Add tests in `tests/test_rules.py`. The API and UI pick the rule up automatically.

## One-click remediation

Three rules have automated actions (`finops/remediation.py`). They need `REMEDIATION_ENABLED=true`,
the optional remediation role, and a confirmation in which the user types the resource ID:

| Rule | Action | AWS calls |
|---|---|---|
| `ec2-idle` | Stop instance | `ec2:StopInstances` (DryRun first) |
| `ebs-unattached` | Snapshot, then delete volume | `ec2:CreateSnapshot`, wait, `ec2:DeleteVolume` |
| `eip-unassociated` | Release address | `ec2:ReleaseAddress` |
