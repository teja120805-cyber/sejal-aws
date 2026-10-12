import { useState } from 'react'
import { Link } from 'react-router-dom'
import { qs } from '../api/client.ts'
import type { Breakdown, TagCompliance } from '../api/types.ts'
import { useAccountApi } from '../api/useAccountApi.ts'
import { BreakdownBars } from '../components/charts.tsx'
import KpiCard from '../components/KpiCard.tsx'
import NoAccount from '../components/NoAccount.tsx'
import { AsyncBlock, Card, EmptyState, PageHeader, Pill, Segmented } from '../components/ui.tsx'
import { useApp } from '../context/AppContext.tsx'
import { RESOURCE_LABELS } from '../lib/format.ts'

export default function Tags() {
  const { account, fmt } = useApp()
  const compliance = useAccountApi<TagCompliance>('/tags/compliance')
  const required = compliance.data?.required_tags ?? []
  const [tagKey, setTagKey] = useState<string | null>(null)
  const activeKey = tagKey ?? required[0] ?? null
  const costs = useAccountApi<Breakdown>(activeKey ? `/tags/costs${qs({ tag_key: activeKey })}` : null)

  if (!account) return <NoAccount />
  const c = compliance.data

  return (
    <div className="space-y-6">
      <PageHeader
        title="Tags"
        description="Required tags make costs attributable. Change the required set in Settings."
        actions={<Link to="/settings#tags" className="text-sm text-emerald-700 hover:underline dark:text-emerald-400">Edit required tags</Link>}
      />

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <KpiCard loading={!c} label="Tag compliance" value={c ? `${c.compliance_pct}%` : ''} footnote={c ? `${c.compliant_resources} of ${c.total_resources} resources fully tagged` : ''} />
        <KpiCard loading={!c} label="Non-compliant resources" value={c ? c.total_resources - c.compliant_resources : ''} footnote={c ? `Required: ${c.required_tags.join(', ')}` : ''} />
        <KpiCard loading={!c} label="Monthly cost not attributable" value={c ? fmt(c.untagged_monthly_cost_usd) : ''} footnote="Estimated cost of non-compliant resources" />
      </div>

      <div className="grid grid-cols-1 gap-6 xl:grid-cols-2">
        <Card title="Missing tags" subtitle="Resources missing each required tag">
          <AsyncBlock state={compliance}>
            {(data) => (
              <ul className="space-y-3">
                {data.required_tags.map((t) => {
                  const missing = data.missing_by_tag[t] ?? 0
                  const share = data.total_resources ? (missing / data.total_resources) * 100 : 0
                  return (
                    <li key={t}>
                      <div className="mb-1 flex justify-between text-sm">
                        <span className="font-medium">{t}</span>
                        <span className="tabular text-slate-500">{missing} missing ({share.toFixed(0)}%)</span>
                      </div>
                      <div className="h-2 rounded-full bg-slate-100 dark:bg-slate-800">
                        <div className="h-2 rounded-full" style={{ width: `${share}%`, background: 'var(--status-serious)' }} />
                      </div>
                    </li>
                  )
                })}
              </ul>
            )}
          </AsyncBlock>
        </Card>

        <Card
          title="Cost by tag value"
          subtitle="Last 30 days, from Cost Explorer tag grouping"
          action={
            required.length > 0 && activeKey ? (
              <Segmented label="Tag key" value={activeKey} onChange={setTagKey} options={required.map((t) => ({ value: t, label: t }))} />
            ) : undefined
          }
        >
          {activeKey ? (
            <AsyncBlock state={costs}>
              {(b) => (b.items.length ? <BreakdownBars items={b.items} /> : <EmptyState title="No cost data for this tag" message="Cost by tag needs the tag to be activated as a cost allocation tag in Billing." />)}
            </AsyncBlock>
          ) : (
            <EmptyState title="No required tags configured" />
          )}
        </Card>
      </div>

      <Card title="Non-compliant resources" subtitle="Sorted by estimated monthly cost">
        <AsyncBlock state={compliance}>
          {(data) =>
            data.noncompliant.length === 0 ? (
              <EmptyState title="All resources are fully tagged" />
            ) : (
              <div className="-mx-4 overflow-x-auto sm:mx-0">
                <table className="w-full min-w-[600px] text-sm">
                  <thead className="text-left text-xs text-slate-500">
                    <tr>
                      <th className="px-4 py-2 sm:px-2">Resource</th>
                      <th className="px-4 py-2 sm:px-2">Type</th>
                      <th className="px-4 py-2 sm:px-2">Region</th>
                      <th className="px-4 py-2 sm:px-2">Missing</th>
                      <th className="px-4 py-2 text-right sm:px-2">Est. monthly</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                    {data.noncompliant.map((r) => (
                      <tr key={r.resource_id}>
                        <td className="max-w-[16rem] truncate px-4 py-2 font-medium sm:px-2">{r.name ?? r.resource_id}</td>
                        <td className="px-4 py-2 sm:px-2">{RESOURCE_LABELS[r.type]}</td>
                        <td className="px-4 py-2 sm:px-2">{r.region}</td>
                        <td className="px-4 py-2 sm:px-2">
                          <div className="flex flex-wrap gap-1">{r.missing_tags.map((t) => <Pill key={t} tone="bad">{t}</Pill>)}</div>
                        </td>
                        <td className="tabular px-4 py-2 text-right sm:px-2">{fmt(r.est_monthly_cost_usd)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )
          }
        </AsyncBlock>
      </Card>
    </div>
  )
}
