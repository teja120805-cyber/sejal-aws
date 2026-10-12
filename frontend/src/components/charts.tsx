/**
 * Charts. Rules applied throughout (see the dataviz guidelines):
 * - categorical colours in fixed slot order, assigned per entity (never re-coloured by rank)
 * - one y-axis only; thin marks; 4px rounded data-ends; 2px surface gap between stacked segments
 * - recessive hairline grid; a legend whenever there are 2+ series; hover tooltips everywhere
 * - a table view is offered because some light-mode hues sit below 3:1 contrast
 */
import { useState, type ReactNode } from 'react'
import {
  Area,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ComposedChart,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
  type TooltipContentProps,
} from 'recharts'
import type { NameType, ValueType } from 'recharts/types/component/DefaultTooltipContent'
import { useApp } from '../context/AppContext.tsx'
import { OTHER, SERIES, colorMap } from '../lib/chartColors.ts'
import { serviceLabel, shortDate } from '../lib/format.ts'
import { Segmented } from './ui.tsx'

type TipProps = TooltipContentProps<ValueType, NameType>

const axisProps = {
  stroke: 'var(--chart-axis)',
  tick: { fill: 'var(--chart-muted)', fontSize: 11 },
  tickLine: false,
} as const

function TooltipBox({ title, rows, footer }: { title: string; rows: Array<{ color?: string; label: string; value: string }>; footer?: ReactNode }) {
  return (
    <div className="min-w-44 rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs shadow-lg dark:border-slate-700 dark:bg-slate-900">
      <p className="mb-1 font-medium text-slate-900 dark:text-slate-100">{title}</p>
      {rows.map((r) => (
        <div key={r.label} className="flex items-center justify-between gap-4 py-0.5">
          <span className="flex items-center gap-1.5 text-slate-600 dark:text-slate-300">
            {r.color && <span className="h-2 w-2 rounded-sm" style={{ background: r.color }} />}
            {r.label}
          </span>
          <span className="tabular font-medium text-slate-900 dark:text-slate-100">{r.value}</span>
        </div>
      ))}
      {footer}
    </div>
  )
}

export function Legend({ items }: { items: Array<{ label: string; color: string; dashed?: boolean }> }) {
  return (
    <ul className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-xs text-slate-600 dark:text-slate-300">
      {items.map((i) => (
        <li key={i.label} className="flex items-center gap-1.5">
          {i.dashed ? (
            <span className="h-0 w-4 border-t-2 border-dashed" style={{ borderColor: i.color }} />
          ) : (
            <span className="h-2.5 w-2.5 rounded-sm" style={{ background: i.color }} />
          )}
          {i.label}
        </li>
      ))}
    </ul>
  )
}

export function ChartOrTable({ chart, table }: { chart: ReactNode; table: ReactNode }) {
  const [view, setView] = useState<'chart' | 'table'>('chart')
  return (
    <div>
      <div className="mb-2 flex justify-end">
        <Segmented
          label="View"
          value={view}
          onChange={setView}
          options={[
            { value: 'chart', label: 'Chart' },
            { value: 'table', label: 'Table' },
          ]}
        />
      </div>
      {view === 'chart' ? chart : <div className="max-h-80 overflow-auto">{table}</div>}
    </div>
  )
}

/* ------------------------------------------------------------------ daily stacked columns */

export function DailyCostChart({ days, services, height = 300 }: {
  days: Array<Record<string, number | string>>
  services: string[]
  height?: number
}) {
  const { fmt, fmtCompact, toDisplay } = useApp()
  const colors = colorMap(services)
  const data = days.map((d) => {
    const row: Record<string, number | string> = { date: d.date as string, total: toDisplay(d.total as number) }
    for (const s of services) row[s] = toDisplay((d[s] as number) ?? 0)
    return row
  })
  const content = (props: TipProps) => {
    const { active, payload, label } = props
    if (!active || !payload?.length) return null
    const src = days.find((d) => d.date === label)
    if (!src) return null
    return (
      <TooltipBox
        title={shortDate(String(label))}
        rows={[
          ...services
            .filter((s) => (src[s] as number) > 0)
            .reverse()
            .map((s) => ({ color: colors[s], label: serviceLabel(s), value: fmt(src[s] as number) })),
          { label: 'Total', value: fmt(src.total as number) },
        ]}
      />
    )
  }
  return (
    <div>
      <ResponsiveContainer width="100%" height={height}>
        <BarChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }} barCategoryGap={1}>
          <CartesianGrid vertical={false} stroke="var(--chart-grid)" />
          <XAxis dataKey="date" {...axisProps} tickFormatter={shortDate} minTickGap={24} />
          <YAxis {...axisProps} axisLine={false} width={56} tickFormatter={(v: number) => fmtCompact(v / (toDisplay(1) || 1))} />
          <Tooltip content={content} cursor={{ fill: 'var(--chart-grid)', opacity: 0.5 }} />
          {services.map((s, i) => (
            <Bar
              key={s}
              dataKey={s}
              stackId="cost"
              fill={colors[s]}
              stroke="var(--chart-surface)"
              strokeWidth={1}
              maxBarSize={24}
              radius={i === services.length - 1 ? [4, 4, 0, 0] : 0}
              isAnimationActive={false}
            />
          ))}
        </BarChart>
      </ResponsiveContainer>
      <Legend items={services.map((s) => ({ label: serviceLabel(s), color: colors[s] }))} />
    </div>
  )
}

/* ------------------------------------------------------------------ single-series trend */

export function TrendChart({ days, height = 220, highlight }: {
  days: Array<{ date: string; total: number }>
  height?: number
  highlight?: string[] // anomaly dates
}) {
  const { fmt, fmtCompact, toDisplay } = useApp()
  const data = days.map((d) => ({ date: d.date, value: toDisplay(d.total), usd: d.total }))
  const marks = new Set(highlight)
  return (
    <ResponsiveContainer width="100%" height={height}>
      <ComposedChart data={data} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
        <CartesianGrid vertical={false} stroke="var(--chart-grid)" />
        <XAxis dataKey="date" {...axisProps} tickFormatter={shortDate} minTickGap={32} />
        <YAxis {...axisProps} axisLine={false} width={56} tickFormatter={(v: number) => fmtCompact(v / (toDisplay(1) || 1))} />
        <Tooltip
          cursor={{ stroke: 'var(--chart-axis)' }}
          content={(p: TipProps) => {
            if (!p.active || !p.payload?.length) return null
            const row = p.payload[0].payload as { date: string; usd: number }
            return (
              <TooltipBox
                title={shortDate(row.date)}
                rows={[{ color: SERIES[0], label: 'Daily cost', value: fmt(row.usd) }]}
                footer={marks.has(row.date) ? <p className="mt-1 font-medium text-red-700 dark:text-red-400">▲ Anomaly (above mean + 3σ)</p> : undefined}
              />
            )
          }}
        />
        <Area type="monotone" dataKey="value" stroke="none" fill={SERIES[0]} fillOpacity={0.1} isAnimationActive={false} />
        <Line
          type="monotone"
          dataKey="value"
          stroke={SERIES[0]}
          strokeWidth={2}
          isAnimationActive={false}
          dot={(props: { cx?: number; cy?: number; payload?: { date: string } }) =>
            props.payload && marks.has(props.payload.date) ? (
              <circle key={props.payload.date} cx={props.cx} cy={props.cy} r={5} fill="var(--status-critical)" stroke="var(--chart-surface)" strokeWidth={2} />
            ) : (
              <g key={`${props.cx}-${props.cy}`} />
            )
          }
          activeDot={{ r: 4, stroke: 'var(--chart-surface)', strokeWidth: 2 }}
        />
      </ComposedChart>
    </ResponsiveContainer>
  )
}

/* ------------------------------------------------------------------ horizontal breakdown bars */

export function BreakdownBars({ items, labelFor = (k: string) => k, height, labelWidth = 140 }: {
  items: Array<{ key: string; amount_usd: number; share_pct: number }>
  labelFor?: (key: string) => string
  height?: number
  labelWidth?: number
}) {
  const { fmt, toDisplay } = useApp()
  const data = items.map((i) => ({ ...i, label: labelFor(i.key), value: toDisplay(i.amount_usd) }))
  const h = height ?? Math.max(120, data.length * 34)
  return (
    <ResponsiveContainer width="100%" height={h}>
      <BarChart data={data} layout="vertical" margin={{ top: 0, right: 72, left: 0, bottom: 0 }}>
        <XAxis type="number" hide />
        <YAxis type="category" dataKey="label" width={labelWidth} {...axisProps} axisLine={false} tick={{ fill: 'var(--chart-ink-2)', fontSize: 12 }} />
        <Tooltip
          cursor={{ fill: 'var(--chart-grid)', opacity: 0.5 }}
          content={(p: TipProps) => {
            if (!p.active || !p.payload?.length) return null
            const row = p.payload[0].payload as (typeof data)[number]
            return <TooltipBox title={row.label} rows={[{ label: 'Cost', value: fmt(row.amount_usd) }, { label: 'Share', value: `${row.share_pct}%` }]} />
          }}
        />
        <Bar
          dataKey="value"
          fill={SERIES[0]}
          maxBarSize={20}
          radius={[0, 4, 4, 0]}
          isAnimationActive={false}
          label={{
            position: 'right',
            fill: 'var(--chart-ink-2)',
            fontSize: 12,
            formatter: (v: unknown) => fmt(Number(v) / (toDisplay(1) || 1), 0),
          }}
        >
          {data.map((d) => (
            <Cell key={d.key} fill={d.key === 'Other' || d.key === '(untagged)' ? OTHER : SERIES[0]} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  )
}

/* ------------------------------------------------------------------ forecast */

export function ForecastChart({ days, budgetDaily, height = 260 }: {
  days: Array<{ date: string; actual?: number; predicted?: number }>
  budgetDaily?: number | null
  height?: number
}) {
  const { fmt, fmtCompact, toDisplay } = useApp()
  const data = days.map((d) => ({
    date: d.date,
    actual: d.actual !== undefined ? toDisplay(d.actual) : undefined,
    predicted: d.predicted !== undefined ? toDisplay(d.predicted) : undefined,
    raw: d,
  }))
  return (
    <div>
      <ResponsiveContainer width="100%" height={height}>
        <BarChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }} barCategoryGap={2}>
          <CartesianGrid vertical={false} stroke="var(--chart-grid)" />
          <XAxis dataKey="date" {...axisProps} tickFormatter={shortDate} minTickGap={20} />
          <YAxis {...axisProps} axisLine={false} width={56} tickFormatter={(v: number) => fmtCompact(v / (toDisplay(1) || 1))} />
          <Tooltip
            cursor={{ fill: 'var(--chart-grid)', opacity: 0.5 }}
            content={(p: TipProps) => {
              if (!p.active || !p.payload?.length) return null
              const raw = (p.payload[0].payload as (typeof data)[number]).raw
              return (
                <TooltipBox
                  title={shortDate(raw.date)}
                  rows={[
                    raw.actual !== undefined
                      ? { color: SERIES[0], label: 'Actual', value: fmt(raw.actual) }
                      : { color: 'var(--seq-light)', label: 'Predicted', value: fmt(raw.predicted ?? 0) },
                  ]}
                />
              )
            }}
          />
          {budgetDaily ? (
            <ReferenceLine y={toDisplay(budgetDaily)} stroke="var(--status-critical)" strokeDasharray="4 3" strokeWidth={1.5} />
          ) : null}
          <Bar dataKey="actual" stackId="d" fill={SERIES[0]} maxBarSize={24} radius={[4, 4, 0, 0]} isAnimationActive={false} />
          <Bar dataKey="predicted" stackId="d" fill="var(--seq-light)" maxBarSize={24} radius={[4, 4, 0, 0]} isAnimationActive={false} />
        </BarChart>
      </ResponsiveContainer>
      <Legend
        items={[
          { label: 'Actual daily cost', color: SERIES[0] },
          { label: 'Predicted (linear regression)', color: 'var(--seq-light)' },
          ...(budgetDaily ? [{ label: 'Budget per day', color: 'var(--status-critical)', dashed: true }] : []),
        ]}
      />
    </div>
  )
}

/* ------------------------------------------------------------------ budget meter */

export function BudgetMeter({ actualPct, forecastPct, thresholds }: { actualPct: number; forecastPct: number; thresholds: number[] }) {
  const max = Math.max(110, forecastPct + 5, actualPct + 5)
  const level = forecastPct >= 100 ? 'var(--status-critical)' : forecastPct >= 80 ? 'var(--status-serious)' : SERIES[0]
  return (
    <div>
      <div className="relative h-3 w-full overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800" role="img"
        aria-label={`Actual ${actualPct}% and forecast ${forecastPct}% of budget`}>
        <div className="absolute inset-y-0 left-0 rounded-full opacity-35" style={{ width: `${(forecastPct / max) * 100}%`, background: level }} />
        <div className="absolute inset-y-0 left-0 rounded-full" style={{ width: `${(actualPct / max) * 100}%`, background: level }} />
        {thresholds.map((t) => (
          <div key={t} className="absolute inset-y-0 w-0.5 bg-slate-500/70" style={{ left: `${(t / max) * 100}%` }} />
        ))}
      </div>
      <div className="relative mt-1 h-4 text-[10px] text-slate-500">
        {thresholds.map((t) => (
          <span key={t} className="absolute -translate-x-1/2" style={{ left: `${(t / max) * 100}%` }}>
            {t}%
          </span>
        ))}
      </div>
    </div>
  )
}
