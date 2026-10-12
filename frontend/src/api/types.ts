// Mirrors the FastAPI response shapes (see docs/api.md).

export type AccountStatus = 'pending' | 'connected' | 'error'
export type Severity = 'high' | 'medium' | 'low'
export type FindingStatus = 'open' | 'dismissed' | 'resolved'
export type ResourceType =
  | 'ec2_instance'
  | 'ebs_volume'
  | 'ebs_snapshot'
  | 'elastic_ip'
  | 'rds_instance'
  | 's3_bucket'
  | 'lambda_function'

export interface AppConfig {
  demo_mode: boolean
  region: string
  usd_inr_rate: number
  refresh_cooldown_minutes: number
  remediation_enabled: boolean
  auth: { enabled: boolean; region: string; user_pool_id: string; client_id: string; domain: string }
}

export interface Account {
  id: string
  name: string
  role_arn: string
  external_id: string
  remediation_role_arn: string | null
  regions: string[]
  status: AccountStatus
  status_message: string | null
  created_at: string
  last_tested_at: string | null
  last_refresh_at: string | null
  last_collected_at: string | null
  aws_account_id: string
}

export interface CostSummary {
  month: string
  data_through: string | null
  month_to_date_usd: number
  last_month_usd: number
  last_month_same_period_usd: number
  mom_change_pct: number | null
  forecast_usd: number
  forecast_source: 'cost_explorer' | 'linear_regression'
  linear_forecast_usd: number
  cost_explorer_forecast_usd: number | null
  budget: { limit_usd: number; actual_pct: number; forecast_pct: number } | null
  potential_savings_usd: number
  open_findings: number
  anomalies_30d: number
  last_collected_at: string | null
  last_refresh_at: string | null
}

export interface DailySeries {
  services: string[]
  days: Array<Record<string, number | string>>
  start: string
  end: string
  total_usd: number
}

export interface BreakdownItem {
  key: string
  amount_usd: number
  share_pct: number
}

export interface Breakdown {
  dimension: 'service' | 'region' | 'tag'
  tag_key: string | null
  start: string
  end: string
  items: BreakdownItem[]
}

export interface ForecastData {
  month: string
  month_to_date_usd: number
  linear: {
    forecast_usd: number
    slope_per_day: number
    intercept: number
    r_squared: number | null
    days_used: number
    excluded_anomaly_days: string[]
  }
  cost_explorer: { forecast_usd: number; lower_usd: number | null; upper_usd: number | null; generated_at: string } | null
  budget_limit_usd: number | null
  days: Array<{ date: string; actual?: number; predicted?: number }>
}

export interface Anomaly {
  date: string
  scope: string
  actual_usd: number
  mean_usd: number
  std_usd: number
  threshold_usd: number
  excess_usd: number
}

export interface Resource {
  account_id: string
  resource_id: string
  type: ResourceType
  region: string
  name: string | null
  state: string | null
  tags: Record<string, string>
  created_at: string | null
  attributes: Record<string, unknown>
  metrics: Record<string, number>
  est_monthly_cost_usd: number
  last_seen_at: string
  key: string
}

export interface ResourceList {
  items: Resource[]
  total: number
  total_monthly_cost_usd: number
  by_type: Record<string, number>
  cost_by_type: Record<string, number>
  regions: string[]
  states: string[]
}

export interface Finding {
  id: string
  account_id: string
  rule_id: string
  resource_id: string
  resource_type: ResourceType
  resource_name: string | null
  region: string
  severity: Severity
  title: string
  detail: string
  recommendation: string
  current_monthly_cost_usd: number | null
  est_monthly_savings_usd: number
  status: FindingStatus
  first_seen: string
  last_seen: string
}

export interface FindingList {
  items: Finding[]
  total: number
  total_monthly_savings_usd: number
  by_rule: Array<{ rule_id: string; title: string; count: number; savings_usd: number }>
  by_severity: Record<Severity, number>
}

export interface RuleInfo {
  id: string
  title: string
  severity: Severity
  resource_type: ResourceType
  description: string
  default_thresholds: Record<string, number>
  thresholds: Record<string, number>
}

export interface RemediationPreview {
  supported: boolean
  enabled: boolean
  finding_id: string
  resource_id: string
  resource_name: string | null
  region: string
  action: string | null
  api_call: string | null
  description: string | null
  monthly_savings_usd: number
  warnings: string[]
  disabled_reason: string | null
}

export interface Budget {
  account_id: string
  monthly_limit_usd: number
  thresholds: number[]
}

export interface BudgetResponse {
  budget: Budget | null
  status: {
    month: string
    actual_usd: number
    forecast_usd: number
    actual_pct: number
    forecast_pct: number
    crossed: Array<{ threshold: number; kind: 'actual' | 'forecast' }>
  } | null
}

export interface Alert {
  id: string
  account_id: string
  type: 'budget_actual' | 'budget_forecast' | 'anomaly'
  severity: Severity
  title: string
  message: string
  dedup_key: string
  data: Record<string, unknown>
  channels_sent: string[]
  created_at: string
}

export interface AccountSettings {
  account_id: string
  required_tags: string[]
  rule_thresholds: Record<string, Record<string, number>>
  email_enabled: boolean
  alert_emails: string[]
  slack_enabled: boolean
  slack_configured: boolean
}

export interface TagCompliance {
  required_tags: string[]
  total_resources: number
  compliant_resources: number
  compliance_pct: number
  missing_by_tag: Record<string, number>
  noncompliant: Array<{
    resource_id: string
    name: string | null
    type: ResourceType
    region: string
    missing_tags: string[]
    tags: Record<string, string>
    est_monthly_cost_usd: number
  }>
  untagged_monthly_cost_usd: number
}

export interface Onboarding {
  trusted_account_id: string
  external_id: string
  role_name: string
  template: string
  remediation_template: string
  quick_create_url: string | null
  cli_command: string
}

export interface ConnectionCheck {
  ok: boolean
  message: string
  aws_account_id: string | null
  regions: string[]
}

export interface CollectionRun {
  id: string
  account_id: string
  trigger: 'schedule' | 'manual' | 'demo'
  status: 'running' | 'succeeded' | 'partial' | 'failed'
  started_at: string
  finished_at: string | null
  cost_explorer_calls: number
  resources_collected: number
  errors: string[]
}
