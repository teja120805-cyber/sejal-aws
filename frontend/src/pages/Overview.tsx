import { Link } from 'react-router-dom'
import type { Alert, Anomaly, Breakdown, CostSummary, DailySeries, FindingList } from '../api/types.ts'
import { useAccountApi } from '../api/useAccountApi.ts'
import { BreakdownBars, BudgetMeter, TrendChart } from '../components/charts.tsx'
import KpiCard from '../components/KpiCard.tsx'
import NoAccount from '../components/NoAccount.tsx'
import { AsyncBlock, Card, EmptyState, PageHeader, SeverityBadge } from '../components/ui.tsx'
import { useApp } from '../context/AppContext.tsx'
import { dateTime, serviceLabel, shortDate, timeAgo } from '../lib/format.ts'

export default function Overview() {
  const { account, fmt } = useApp()
  const summary = useAccountApi<CostSummary>('/costs/summary')
  const daily = useAccountApi<DailySeries>('/costs/daily?top=1')
  const services = useAccountApi<Breakdown>('/costs/breakdown?dimension=service&limit=6')
  const findings = useAccountApi<FindingList>('/findings')
  const alerts = useAccountApi<Alert[]>('/alerts?limit=5')
  const anomalies = useAccountApi<Anomaly[]>('/costs/anomalies?days=90')

  if (!account) return <NoAccount />
  const s = summary.data

  return (
    <div className="space-y-6">
      <PageHeader
        title="Overview"
        description={`${account.name} · data through ${s?.data_through ? shortDate(s.data_through) : '...'} · collected ${timeAgo(account.last_collected_at)}`}
      />

      {summary.error ? (
        <AsyncBlock state={summary}>{() => null}</AsyncBlock>
      ) : (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
          <KpiCard
            loading={!s}
            label="Month-to-date spend"
            value={s ? fmt(s.month_to_date_usd) : ''}
            delta={s?.mom_change_pct}
            deltaLabel="vs same period last month"
          />
          <KpiCard loading={!s} label="Last month" value={s ? fmt(s.last_month_usd) : ''} footnote="Full calendar month" />
          <KpiCard
            loading={!s}
            label="Month-end forecast"
            value={s ? fmt(s.forecast_usd) : ''}
            footnote={
              s?.budget
                ? `${s.budget.forecast_pct}% of ${fmt(s.budget.limit_usd, 0)} budget`
                : s?.forecast_source === 'cost_explorer'
                  ? 'Cost Explorer forecast'
                  : 'Linear regression'
            }
          />
          <KpiCard
            loading={!s}
            label="Potential savings / month"
            value={s ? fmt(s.potential_savings_usd) : ''}
            footnote={s ? `${s.open_findings} open recommendations` : ''}
          />
        </div>
      )}

      {s?.budget && (
        <Card title="Budget" subtitle={`Actual ${s.budget.actual_pct}% · forecast ${s.budget.forecast_pct}% of ${fmt(s.budget.limit_usd, 0)}`}
          action={<Link to="/budgets" className="text-sm text-emerald-700 hover:underline dark:text-emerald-400">Manage</Link>}>
          <BudgetMeter actualPct={s.budget.actual_pct} forecastPct={s.budget.forecast_pct} thresholds={[80, 100]} />
        </Card>
      )}

      <div className="grid grid-cols-1 gap-6 xl:grid-cols-3">
        <Card
          className="xl:col-span-2"
          title="Daily cost, last 90 days"
          subtitle={anomalies.data?.length ? 'Red dot = cost anomaly (above 30-day mean + 3σ)' : 'Total across all services'}
          action={<Link to="/costs" className="text-sm text-emerald-700 hover:underline dark:text-emerald-400">Details</Link>}
        >
          <AsyncBlock state={daily} height="h-56">
            {(d) =>
              d.days.length ? (
                <TrendChart
                  days={d.days.map((x) => ({ date: x.date as string, total: x.total as number }))}
                  highlight={anomalies.data?.filter((a) => a.scope === 'Total').map((a) => a.date)}
                />
              ) : (
                <EmptyState title="No cost data yet" message="Costs appear after the first collection run." />
              )
            }
          </AsyncBlock>
        </Card>
        <Card title="Top services" subtitle="Last 90 days">
          <AsyncBlock state={services}>
            {(b) => (b.items.length ? <BreakdownBars items={b.items} labelFor={serviceLabel} /> : <EmptyState title="No data" />)}
          </AsyncBlock>
        </Card>
      </div>

      <div className="grid grid-cols-1 gap-6 xl:grid-cols-2">
        <Card
          title="Top recommendations"
          action={<Link to="/recommendations" className="text-sm text-emerald-700 hover:underline dark:text-emerald-400">View all</Link>}
        >
          <AsyncBlock state={findings}>
            {(f) =>
              f.items.length ? (
                <ul className="divide-y divide-slate-100 dark:divide-slate-800">
                  {[...f.items]
                    .sort((a, b) => b.est_monthly_savings_usd - a.est_monthly_savings_usd)
                    .slice(0, 5)
                    .map((x) => (
                      <li key={x.id} className="flex items-center justify-between gap-3 py-2.5">
                        <div className="min-w-0">
                          <p className="truncate text-sm font-medium">{x.title}</p>
                          <p className="truncate text-xs text-slate-500">{x.resource_name ?? x.resource_id} · {x.region}</p>
                        </div>
                        <div className="flex shrink-0 items-center gap-2">
                          <SeverityBadge severity={x.severity} />
                          <span className="tabular w-24 text-right text-sm font-semibold">{fmt(x.est_monthly_savings_usd)}</span>
                        </div>
                      </li>
                    ))}
                </ul>
              ) : (
                <EmptyState title="No waste found" message="Nice - nothing to optimise right now." />
              )
            }
          </AsyncBlock>
        </Card>
        <Card
          title="Recent alerts"
          action={<Link to="/budgets" className="text-sm text-emerald-700 hover:underline dark:text-emerald-400">History</Link>}
        >
          <AsyncBlock state={alerts}>
            {(list) =>
              list.length ? (
                <ul className="divide-y divide-slate-100 dark:divide-slate-800">
                  {list.map((a) => (
                    <li key={a.id} className="py-2.5">
                      <div className="flex items-center justify-between gap-2">
                        <p className="text-sm font-medium">{a.title}</p>
                        <SeverityBadge severity={a.severity} />
                      </div>
                      <p className="mt-0.5 text-xs text-slate-500">{dateTime(a.created_at)}</p>
                    </li>
                  ))}
                </ul>
              ) : (
                <EmptyState title="No alerts" message="Budget and anomaly alerts will show up here." />
              )
            }
          </AsyncBlock>
        </Card>
      </div>
    </div>
  )
}
