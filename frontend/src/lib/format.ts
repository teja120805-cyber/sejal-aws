export type Currency = 'USD' | 'INR'

const SYMBOL: Record<Currency, string> = { USD: '$', INR: '₹' }

export function convert(usd: number, currency: Currency, rate: number): number {
  return currency === 'INR' ? usd * rate : usd
}

/** Full amount, e.g. $1,234.56 / ₹1,08,641.28 (INR uses Indian digit grouping). */
export function money(usd: number, currency: Currency, rate: number, decimals = 2): string {
  const value = convert(usd, currency, rate)
  return new Intl.NumberFormat(currency === 'INR' ? 'en-IN' : 'en-US', {
    style: 'currency',
    currency,
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  }).format(value)
}

/** Compact amount for axes and tiles, e.g. $1.2K / ₹1.1L. */
export function moneyCompact(usd: number, currency: Currency, rate: number): string {
  const value = convert(usd, currency, rate)
  const sym = SYMBOL[currency]
  const abs = Math.abs(value)
  if (value === 0) return `${sym}0`
  if (currency === 'INR') {
    if (abs >= 1e7) return `${sym}${(value / 1e7).toFixed(1)}Cr`
    if (abs >= 1e5) return `${sym}${(value / 1e5).toFixed(1)}L`
    if (abs >= 1e3) return `${sym}${(value / 1e3).toFixed(1)}K`
    return `${sym}${value.toFixed(0)}`
  }
  if (abs >= 1e6) return `${sym}${(value / 1e6).toFixed(1)}M`
  if (abs >= 1e3) return `${sym}${(value / 1e3).toFixed(1)}K`
  return `${sym}${value.toFixed(abs < 10 ? 2 : 0)}`
}

export function pct(value: number | null | undefined, signed = false): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '-'
  const s = `${Math.abs(value).toFixed(1)}%`
  if (!signed) return `${value < 0 ? '-' : ''}${s}`
  return value > 0 ? `+${s}` : value < 0 ? `-${s}` : s
}

export function shortDate(iso: string): string {
  return new Date(`${iso.slice(0, 10)}T00:00:00`).toLocaleDateString('en-GB', { day: 'numeric', month: 'short' })
}

export function dateTime(iso: string | null | undefined): string {
  if (!iso) return 'never'
  return new Date(iso).toLocaleString('en-GB', {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

export function timeAgo(iso: string | null | undefined): string {
  if (!iso) return 'never'
  const seconds = Math.round((Date.now() - new Date(iso).getTime()) / 1000)
  if (seconds < 60) return 'just now'
  const minutes = Math.round(seconds / 60)
  if (minutes < 60) return `${minutes} min ago`
  const hours = Math.round(minutes / 60)
  if (hours < 48) return `${hours} h ago`
  return `${Math.round(hours / 24)} days ago`
}

export const RESOURCE_LABELS: Record<string, string> = {
  ec2_instance: 'EC2 instance',
  ebs_volume: 'EBS volume',
  ebs_snapshot: 'EBS snapshot',
  elastic_ip: 'Elastic IP',
  rds_instance: 'RDS instance',
  s3_bucket: 'S3 bucket',
  lambda_function: 'Lambda function',
}

/** Shorter AWS service names for chart legends. */
export function serviceLabel(service: string): string {
  const map: Record<string, string> = {
    'Amazon Elastic Compute Cloud - Compute': 'EC2 compute',
    'EC2 - Other': 'EC2 other',
    'Amazon Relational Database Service': 'RDS',
    'Amazon Simple Storage Service': 'S3',
    'Amazon Elastic Load Balancing': 'Load balancing',
    'Amazon CloudFront': 'CloudFront',
    AmazonCloudWatch: 'CloudWatch',
    'AWS Lambda': 'Lambda',
  }
  return map[service] ?? service
}

export function isoDaysAgo(days: number): string {
  const d = new Date()
  d.setDate(d.getDate() - days)
  return d.toISOString().slice(0, 10)
}
