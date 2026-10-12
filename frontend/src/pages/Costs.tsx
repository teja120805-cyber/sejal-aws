import { useState } from 'react'
import { qs } from '../api/client.ts'
import type { AccountSettings, Anomaly, Breakdown, DailySeries, ForecastData } from '../api/types.ts'
import { useAccountApi } from '../api/useAccountApi.ts'
import { BreakdownBars, ChartOrTable, DailyCostChart, ForecastChart } from '../components/charts.tsx'
import NoAccount from '../components/NoAccount.tsx'
import { AsyncBlock, Card, EmptyState, PageHeader, Segmented, inlineInputClass } from '../components/ui.tsx'
import { useApp } from '../context/AppContext.tsx'
import { isoDaysAgo, pct, serviceLabel, shortDate } from '../lib/format.ts'

type Preset = '30' | '60' | '90' | 'custom'

function DateRange({ preset, setPreset, start, end, setStart, setEnd }: {
  preset: Preset
  setPreset: (p: Preset) => void
  start: string
  end: string
  setStart: (s: string) => void
  setEnd: (s: string) => void
}) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      <Segmented
        label="Date range"
        value={preset}
        onChange={setPreset}
        options={[
          { value: '30', label: '30 days' },
          { value: '60', label: '60 days' },
          { value: '90', label: '90 days' },
          { value: 'custom', label: 'Custom' },
        ]}
      />
      {preset === 'custom' && (
        <>
          <input type="date" aria-label="Start date" className={`${inlineInputClass} py-1`} value={start} max={end} onChange={(e) => setStart(e.target.value)} />
          <span className="text-slate-400">to</span>
          <input type="date" aria-label="End date" className={`${inlineInputClass} py-1`} value={end} min={start} onChange={(e) => setEnd(e.target.value)} />
        </>
      )}
    </div>
  )
}

export default function Costs() {
  const { account, fmt } = useApp()
  const [preset, setPreset] = useState<Preset>('90')
  const [customStart, setCustomStart] = useState(isoDaysAgo(30))
  const [customEnd, setCustomEnd] = useState(isoDaysAgo(1))
  const [dimension, setDimension] = useState<'service' | 'region' | 'tag'>('service')
  const [tagKey, setTagKey] = useState('Project')

  const range =
    preset === 'custom' ? { start: customStart, end: customEnd } : { start: isoDaysAgo(Number(preset)), end: isoDaysAgo(1) }
  const daily = useAccountApi<DailySeries>(`/costs/daily${qs(range)}`)
  const breakdown = useAccountApi<Breakdown>(
    `/costs/breakdown${qs({ ...range, dimension, tag_key: dimension === 'tag' ? tagKey : undefined, limit: 10 })}`,
  )
  const forecast = useAccountApi<ForecastData>('/costs/forecast')
  const anomalies = useAccountApi<Anomaly[]>('/costs/anomalies?days=90')
  const settings = useAccountApi<AccountSettings>('/settings')

  if (!account) return <NoAccount />

  return (
    <div className="space-y-6">
      <PageHeader
        title="Costs"
        description="Daily unblended cost from AWS Cost Explorer, collected once a day."
        actions={<DateRange preset={preset} setPreset={setPreset} start={customStart} end={customEnd} setStart={setCustomStart} setEnd={setCustomEnd} />}
      />

      <Card
        title="Daily cost by service"
        subtitle={daily.data ? `${shortDate(daily.data.start)} – ${shortDate(daily.data.end)} · total ${fmt(daily.data.total_usd)}` : undefined}
      >
        <AsyncBlock state={daily} height="h-72">
          {(d) =>
            d.days.length ? (
              <ChartOrTable
                chart={<DailyCostChart days={d.days} services={d.services} />}
                table={
                  <table className="w-full text-sm">
                    <thead className="sticky top-0 bg-white text-left text-xs text-slate-500 dark:bg-slate-900">
                      <tr>
                        <th className="py-2 pr-3">Date</th>
                        {d.services.map((s) => (
                          <th key={s} className="py-2 pr-3 text-right">{serviceLabel(s)}</th>
                        ))}
                        <th className="py-2 text-right">Total</th>
                      </tr>
                    </thead>
                    <tbody className="tabular divide-y divide-slate-100 dark:divide-slate-800">
                      {[...d.days].reverse().map((row) => (
                        <tr key={row.date as string}>
                          <td className="py-1.5 pr-3">{shortDate(row.date as string)}</td>
                          {d.services.map((s) => (
                            <td key={s} className="py-1.5 pr-3 text-right">{fmt(row[s] as number)}</td>
                          ))}
                          <td className="py-1.5 text-right font-medium">{fmt(row.total as number)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                }
              />
            ) : (
              <EmptyState title="No costs in this range" />
            )
          }
        </AsyncBlock>
      </Card>

      <div className="grid grid-cols-1 gap-6 xl:grid-cols-2">
        <Card
          title="Breakdown"
          action={
            <div className="flex items-center gap-2">
              <Segmented
                label="Breakdown dimension"
                value={dimension}
                onChange={setDimension}
                options={[
                  { value: 'service', label: 'Service' },
                  { value: 'region', label: 'Region' },
                  { value: 'tag', label: 'Tag' },
                ]}
              />
              {dimension === 'tag' && (
                <select aria-label="Tag key" className={`${inlineInputClass} py-1`} value={tagKey} onChange={(e) => setTagKey(e.target.value)}>
                  {(settings.data?.required_tags ?? ['Project', 'Owner', 'Environment']).map((t) => (
                    <option key={t}>{t}</option>
                  ))}
                </select>
              )}
            </div>
          }
        >
          <AsyncBlock state={breakdown}>
            {(b) =>
              b.items.length ? (
                <BreakdownBars items={b.items} labelFor={dimension === 'service' ? serviceLabel : (k) => k} />
              ) : (
                <EmptyState title="No data for this breakdown" />
              )
            }
          </AsyncBlock>
        </Card>

        <Card title="Month-end forecast" subtitle="Linear regression on the last 30 days (anomaly days excluded) vs Cost Explorer">
          <AsyncBlock state={forecast}>
            {(f) => (
              <div>
                <div className="mb-4 grid grid-cols-2 gap-3 text-sm sm:grid-cols-3">
                  <div>
                    <p className="text-xs text-slate-500">Month to date</p>
                    <p className="font-semibold">{fmt(f.month_to_date_usd)}</p>
                  </div>
                  <div>
                    <p className="text-xs text-slate-500">Linear regression</p>
                    <p className="font-semibold">{fmt(f.linear.forecast_usd)}</p>
                    <p className="text-xs text-slate-500">slope {fmt(f.linear.slope_per_day)}/day</p>
                  </div>
                  <div>
                    <p className="text-xs text-slate-500">Cost Explorer</p>
                    <p className="font-semibold">{f.cost_explorer ? fmt(f.cost_explorer.forecast_usd) : 'n/a'}</p>
                    {f.cost_explorer && (
                      <p className="text-xs text-slate-500">
                        diff {pct(((f.linear.forecast_usd - f.cost_explorer.forecast_usd) / f.cost_explorer.forecast_usd) * 100, true)}
                      </p>
                    )}
                  </div>
                </div>
                <ForecastChart days={f.days} budgetDaily={f.budget_limit_usd ? f.budget_limit_usd / 30.4 : null} />
              </div>
            )}
          </AsyncBlock>
        </Card>
      </div>

      <Card title="Cost anomalies" subtitle="Days above mean + 3 standard deviations of the trailing 30 days (and at least 20% above normal)">
        <AsyncBlock state={anomalies}>
          {(list) =>
            list.length ? (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead className="text-left text-xs text-slate-500">
                    <tr>
                      <th className="py-2 pr-4">Date</th>
                      <th className="py-2 pr-4">Scope</th>
                      <th className="py-2 pr-4 text-right">Actual</th>
                      <th className="py-2 pr-4 text-right">30-day mean</th>
                      <th className="py-2 pr-4 text-right">Threshold</th>
                      <th className="py-2 text-right">Excess</th>
                    </tr>
                  </thead>
                  <tbody className="tabular divide-y divide-slate-100 dark:divide-slate-800">
                    {list.map((a) => (
                      <tr key={`${a.date}-${a.scope}`}>
                        <td className="py-2 pr-4">{shortDate(a.date)}</td>
                        <td className="py-2 pr-4">{a.scope === 'Total' ? <strong>Total</strong> : serviceLabel(a.scope)}</td>
                        <td className="py-2 pr-4 text-right">{fmt(a.actual_usd)}</td>
                        <td className="py-2 pr-4 text-right">{fmt(a.mean_usd)}</td>
                        <td className="py-2 pr-4 text-right">{fmt(a.threshold_usd)}</td>
                        <td className="py-2 text-right font-medium text-red-700 dark:text-red-400">+{fmt(a.excess_usd)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <EmptyState title="No anomalies in the last 90 days" />
            )
          }
        </AsyncBlock>
      </Card>
    </div>
  )
}
