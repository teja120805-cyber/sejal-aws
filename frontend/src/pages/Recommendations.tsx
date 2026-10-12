import { useState } from 'react'
import { api, enc, qs } from '../api/client.ts'
import type { Finding, FindingList, FindingStatus, RemediationPreview, Severity } from '../api/types.ts'
import { useAccountApi } from '../api/useAccountApi.ts'
import { BreakdownBars } from '../components/charts.tsx'
import KpiCard from '../components/KpiCard.tsx'
import NoAccount from '../components/NoAccount.tsx'
import { AsyncBlock, Button, Card, EmptyState, ErrorState, Modal, PageHeader, Segmented, SeverityBadge, inputClass, inlineInputClass } from '../components/ui.tsx'
import { ICONS } from '../lib/icons.ts'
import { useApp } from '../context/AppContext.tsx'
import { RESOURCE_LABELS, timeAgo } from '../lib/format.ts'

// Rules with a one-click action (mirrors backend finops/remediation.py ACTIONS).
const AUTOMATED_RULES = new Set(['ec2-idle', 'ebs-unattached', 'eip-unassociated'])

function RemediationDialog({ finding, onClose, onDone }: { finding: Finding; onClose: () => void; onDone: () => void }) {
  const { account, fmt } = useApp()
  const plan = useAccountApi<RemediationPreview>(`/remediation/${enc(finding.id)}`)
  const [typed, setTyped] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<Error | null>(null)
  const [result, setResult] = useState<string | null>(null)

  const run = async () => {
    if (!account) return
    setBusy(true)
    setError(null)
    try {
      const r = await api.post<{ message: string }>(`/api/accounts/${account.id}/remediation/${enc(finding.id)}`, {
        confirm: true,
        confirm_resource_id: typed,
      })
      setResult(r.message)
      onDone()
    } catch (err) {
      setError(err as Error)
    } finally {
      setBusy(false)
    }
  }

  const p = plan.data
  return (
    <Modal
      open
      title="Confirm remediation"
      onClose={onClose}
      footer={
        result ? (
          <Button variant="primary" onClick={onClose}>Done</Button>
        ) : (
          <>
            <Button onClick={onClose}>Cancel</Button>
            <Button variant="danger" busy={busy} disabled={!p?.enabled || typed !== finding.resource_id} onClick={run}>
              Run action
            </Button>
          </>
        )
      }
    >
      <AsyncBlock state={plan}>
        {() =>
          result ? (
            <p className="text-sm text-emerald-700 dark:text-emerald-400">✓ {result}</p>
          ) : p ? (
            <div className="space-y-4 text-sm">
              <div className="rounded-lg bg-slate-50 p-3 dark:bg-slate-800/60">
                <p className="font-medium">{p.description ?? 'No automated action available'}</p>
                {p.api_call && <p className="mt-1 font-mono text-xs text-slate-500">AWS API: {p.api_call}</p>}
                <p className="mt-2">
                  Estimated saving: <strong>{fmt(p.monthly_savings_usd)}</strong> / month
                </p>
              </div>
              {p.warnings.length > 0 && (
                <ul className="list-disc space-y-1 pl-5 text-amber-800 dark:text-amber-300">
                  {p.warnings.map((w) => <li key={w}>{w}</li>)}
                </ul>
              )}
              {p.enabled ? (
                <label className="block">
                  <span className="text-slate-600 dark:text-slate-300">
                    Type <code className="rounded bg-slate-100 px-1 dark:bg-slate-800">{finding.resource_id}</code> to confirm
                  </span>
                  <input className={`${inputClass} mt-1 font-mono`} value={typed} onChange={(e) => setTyped(e.target.value)} autoComplete="off" />
                </label>
              ) : (
                <p className="rounded-lg border border-slate-200 p-3 text-slate-600 dark:border-slate-700 dark:text-slate-300">
                  {p.disabled_reason}
                </p>
              )}
              {error && <ErrorState error={error} />}
            </div>
          ) : null
        }
      </AsyncBlock>
    </Modal>
  )
}

export default function Recommendations() {
  const { account, fmt, bumpData } = useApp()
  const [status, setStatus] = useState<FindingStatus>('open')
  const [severity, setSeverity] = useState<Severity | ''>('')
  const [ruleId, setRuleId] = useState('')
  const [fixing, setFixing] = useState<Finding | null>(null)
  const [busyId, setBusyId] = useState<string | null>(null)
  const findings = useAccountApi<FindingList>(`/findings${qs({ status, severity, rule_id: ruleId })}`)
  const allOpen = useAccountApi<FindingList>('/findings')

  if (!account) return <NoAccount />

  const setFindingStatus = async (f: Finding, next: 'open' | 'dismissed') => {
    setBusyId(f.id)
    try {
      await api.patch(`/api/accounts/${account.id}/findings/${enc(f.id)}`, { status: next })
      findings.reload()
      allOpen.reload()
    } finally {
      setBusyId(null)
    }
  }

  const totals = allOpen.data
  return (
    <div className="space-y-6">
      <PageHeader title="Recommendations" description="Waste detected by 8 rules. Savings are estimates from list prices." />

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <KpiCard loading={!totals} label="Potential savings / month" value={totals ? fmt(totals.total_monthly_savings_usd) : ''} />
        <KpiCard loading={!totals} label="Potential savings / year" value={totals ? fmt(totals.total_monthly_savings_usd * 12, 0) : ''} />
        <KpiCard
          loading={!totals}
          label="Open recommendations"
          value={totals?.total ?? ''}
          footnote={totals ? `${totals.by_severity.high} high · ${totals.by_severity.medium} medium · ${totals.by_severity.low} low` : ''}
        />
      </div>

      {totals && totals.by_rule.length > 0 && (
        <Card title="Savings by rule" subtitle="Monthly, open recommendations">
          <BreakdownBars
            labelWidth={230}
            items={totals.by_rule.map((r) => ({
              key: r.title,
              amount_usd: r.savings_usd,
              share_pct: Math.round((r.savings_usd / (totals.total_monthly_savings_usd || 1)) * 1000) / 10,
            }))}
          />
        </Card>
      )}

      <Card>
        <div className="mb-4 flex flex-wrap items-center gap-2">
          <Segmented
            label="Status"
            value={status}
            onChange={setStatus}
            options={[
              { value: 'open', label: 'Open' },
              { value: 'dismissed', label: 'Dismissed' },
              { value: 'resolved', label: 'Resolved' },
            ]}
          />
          <select aria-label="Severity" className={`${inlineInputClass}`} value={severity} onChange={(e) => setSeverity(e.target.value as Severity | '')}>
            <option value="">All severities</option>
            <option value="high">High</option>
            <option value="medium">Medium</option>
            <option value="low">Low</option>
          </select>
          <select aria-label="Rule" className={`${inlineInputClass}`} value={ruleId} onChange={(e) => setRuleId(e.target.value)}>
            <option value="">All rules</option>
            {totals?.by_rule.map((r) => <option key={r.rule_id} value={r.rule_id}>{r.title}</option>)}
          </select>
        </div>

        <AsyncBlock state={findings}>
          {(data) =>
            data.items.length === 0 ? (
              <EmptyState title={status === 'open' ? 'No open recommendations' : `No ${status} recommendations`} />
            ) : (
              <ul className="divide-y divide-slate-100 dark:divide-slate-800">
                {data.items.map((f) => (
                  <li key={f.id} className="flex flex-col gap-3 py-4 md:flex-row md:items-start md:justify-between">
                    <div className="min-w-0 space-y-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <SeverityBadge severity={f.severity} />
                        <p className="font-medium">{f.title}</p>
                      </div>
                      <p className="text-sm text-slate-700 dark:text-slate-300">
                        <span className="font-medium">{f.resource_name ?? f.resource_id}</span>
                        <span className="text-slate-500"> · {RESOURCE_LABELS[f.resource_type]} · {f.region}</span>
                      </p>
                      <p className="text-sm text-slate-600 dark:text-slate-400">{f.detail}</p>
                      <p className="text-sm">
                        <span className="font-medium text-emerald-700 dark:text-emerald-400">Recommendation:</span> {f.recommendation}
                      </p>
                      <p className="text-xs text-slate-500">First seen {timeAgo(f.first_seen)}</p>
                    </div>
                    <div className="flex shrink-0 flex-row items-center gap-3 md:flex-col md:items-end">
                      <div className="text-right">
                        <p className="tabular text-lg font-semibold">{fmt(f.est_monthly_savings_usd)}</p>
                        <p className="text-xs text-slate-500">
                          per month{f.current_monthly_cost_usd !== null ? ` of ${fmt(f.current_monthly_cost_usd)}` : ''}
                        </p>
                      </div>
                      <div className="flex gap-2">
                        {f.status === 'open' && (
                          <>
                            {AUTOMATED_RULES.has(f.rule_id) && (
                              <Button icon={ICONS.bolt} onClick={() => setFixing(f)}>Fix</Button>
                            )}
                            <Button variant="ghost" busy={busyId === f.id} onClick={() => setFindingStatus(f, 'dismissed')}>Dismiss</Button>
                          </>
                        )}
                        {f.status === 'dismissed' && (
                          <Button variant="ghost" busy={busyId === f.id} onClick={() => setFindingStatus(f, 'open')}>Restore</Button>
                        )}
                      </div>
                    </div>
                  </li>
                ))}
              </ul>
            )
          }
        </AsyncBlock>
      </Card>

      {fixing && (
        <RemediationDialog
          finding={fixing}
          onClose={() => setFixing(null)}
          onDone={() => {
            findings.reload()
            allOpen.reload()
            bumpData()
          }}
        />
      )}
    </div>
  )
}
