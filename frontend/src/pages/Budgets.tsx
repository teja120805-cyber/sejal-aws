import { useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api/client.ts'
import type { Alert, BudgetResponse } from '../api/types.ts'
import { useAccountApi } from '../api/useAccountApi.ts'
import { BudgetMeter } from '../components/charts.tsx'
import NoAccount from '../components/NoAccount.tsx'
import { AsyncBlock, Button, Card, EmptyState, ErrorState, Field, PageHeader, Pill, SeverityBadge, inputClass } from '../components/ui.tsx'
import { ICONS } from '../lib/icons.ts'
import { useApp } from '../context/AppContext.tsx'
import { dateTime } from '../lib/format.ts'

const TYPE_LABEL: Record<Alert['type'], string> = {
  budget_actual: 'Budget (actual)',
  budget_forecast: 'Budget (forecast)',
  anomaly: 'Anomaly',
}

function BudgetEditor({ current, onSaved }: { current: BudgetResponse; onSaved: () => void }) {
  const { account, currency, config } = useApp()
  const rate = currency === 'INR' ? config.usd_inr_rate : 1
  const [limit, setLimit] = useState(current.budget ? String(Math.round(current.budget.monthly_limit_usd * rate)) : '')
  const [thresholds, setThresholds] = useState(current.budget ? current.budget.thresholds.join(', ') : '80, 100')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<Error | null>(null)

  const save = async () => {
    if (!account) return
    setBusy(true)
    setError(null)
    try {
      await api.put(`/api/accounts/${account.id}/budget`, {
        monthly_limit_usd: Number(limit) / rate,
        thresholds: thresholds.split(',').map((t) => Number(t.trim())).filter(Boolean),
      })
      onSaved()
    } catch (err) {
      setError(err as Error)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="grid gap-3 sm:grid-cols-3 sm:items-start">
      <Field label={`Monthly budget (${currency})`} hint={currency === 'INR' ? `Stored in USD at ₹${config.usd_inr_rate}/$` : undefined}>
        <input type="number" min={1} className={inputClass} value={limit} onChange={(e) => setLimit(e.target.value)} />
      </Field>
      <Field label="Alert at (% of budget)" hint="Comma separated, e.g. 80, 100">
        <input className={inputClass} value={thresholds} onChange={(e) => setThresholds(e.target.value)} />
      </Field>
      <div className="sm:pt-6">
        <Button variant="primary" busy={busy} disabled={!limit} onClick={save}>
          {current.budget ? 'Update budget' : 'Set budget'}
        </Button>
      </div>
      {error && <div className="sm:col-span-3"><ErrorState error={error} /></div>}
    </div>
  )
}

export default function Budgets() {
  const { account, fmt, currency } = useApp()
  const budget = useAccountApi<BudgetResponse>('/budget')
  const alerts = useAccountApi<Alert[]>('/alerts?limit=200')
  const [testResult, setTestResult] = useState<Record<string, string> | null>(null)
  const [testing, setTesting] = useState(false)

  if (!account) return <NoAccount />

  const sendTest = async () => {
    setTesting(true)
    try {
      const r = await api.post<{ results: Record<string, string> }>(`/api/accounts/${account.id}/alerts/test`)
      setTestResult(r.results)
    } finally {
      setTesting(false)
    }
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Budgets & Alerts"
        description="Alerts fire at your thresholds for actual or forecast spend, and for daily cost anomalies. Each alert is sent once."
        actions={
          <>
            <Button icon={ICONS.bolt} busy={testing} onClick={sendTest}>Send test alert</Button>
            <Link to="/settings#notifications" className="text-sm text-emerald-700 hover:underline dark:text-emerald-400">Notification settings</Link>
          </>
        }
      />
      {testResult && (
        <p role="status" className="text-sm text-slate-600 dark:text-slate-300">
          Test alert: {Object.entries(testResult).map(([ch, r]) => `${ch}: ${r}`).join(' · ')}
        </p>
      )}

      <Card title="Monthly budget">
        <AsyncBlock state={budget}>
          {(b) => (
            <div className="space-y-5">
              {b.budget && b.status ? (
                <div>
                  <div className="mb-3 grid grid-cols-2 gap-4 text-sm sm:grid-cols-4">
                    <div><p className="text-xs text-slate-500">Budget</p><p className="text-lg font-semibold">{fmt(b.budget.monthly_limit_usd, 0)}</p></div>
                    <div><p className="text-xs text-slate-500">Spent so far</p><p className="text-lg font-semibold">{fmt(b.status.actual_usd)}</p><p className="text-xs text-slate-500">{b.status.actual_pct}%</p></div>
                    <div><p className="text-xs text-slate-500">Forecast</p><p className="text-lg font-semibold">{fmt(b.status.forecast_usd)}</p><p className="text-xs text-slate-500">{b.status.forecast_pct}%</p></div>
                    <div>
                      <p className="text-xs text-slate-500">Thresholds crossed</p>
                      <div className="mt-1 flex flex-wrap gap-1">
                        {b.status.crossed.length ? b.status.crossed.map((c) => (
                          <Pill key={`${c.threshold}${c.kind}`} tone={c.kind === 'actual' ? 'bad' : 'info'}>{c.threshold}% {c.kind}</Pill>
                        )) : <Pill tone="good">none</Pill>}
                      </div>
                    </div>
                  </div>
                  <BudgetMeter actualPct={b.status.actual_pct} forecastPct={b.status.forecast_pct} thresholds={b.budget.thresholds} />
                  <p className="mt-1 text-xs text-slate-500">Solid = actual, faded = forecast. Markers = alert thresholds.</p>
                </div>
              ) : (
                <EmptyState title="No budget set" message="Set a monthly budget to get alerts at 80% and 100%." />
              )}
              <BudgetEditor
                key={`${JSON.stringify(b.budget)}-${currency}`}
                current={b}
                onSaved={() => {
                  budget.reload()
                  alerts.reload()
                }}
              />
            </div>
          )}
        </AsyncBlock>
      </Card>

      <Card title="Alert history">
        <AsyncBlock state={alerts}>
          {(list) =>
            list.length === 0 ? (
              <EmptyState title="No alerts yet" />
            ) : (
              <ul className="divide-y divide-slate-100 dark:divide-slate-800">
                {list.map((a) => (
                  <li key={a.id} className="py-3">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <div className="flex items-center gap-2">
                        <SeverityBadge severity={a.severity} />
                        <p className="font-medium">{a.title}</p>
                      </div>
                      <p className="text-xs text-slate-500">{dateTime(a.created_at)}</p>
                    </div>
                    <p className="mt-1 text-sm text-slate-600 dark:text-slate-300">{a.message}</p>
                    <div className="mt-1 flex flex-wrap gap-1">
                      <Pill>{TYPE_LABEL[a.type]}</Pill>
                      {a.channels_sent.length
                        ? a.channels_sent.map((c) => <Pill key={c} tone={c.includes('failed') ? 'bad' : 'good'}>{c}</Pill>)
                        : <Pill>not delivered (no channel enabled)</Pill>}
                    </div>
                  </li>
                ))}
              </ul>
            )
          }
        </AsyncBlock>
      </Card>
    </div>
  )
}
