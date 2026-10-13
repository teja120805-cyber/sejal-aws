import type { ReactNode } from 'react'
import { Skeleton } from './ui.tsx'

const COLS = { 3: 'sm:grid-cols-3', 4: 'sm:grid-cols-2 xl:grid-cols-4' } as const

/**
 * A row of stat cells sharing one frame. The 1px gap over a border-coloured background draws the
 * dividers, so they stay correct however the grid wraps.
 */
export function KpiStrip({ cols = 4, children }: { cols?: 3 | 4; children: ReactNode }) {
  return (
    <div
      className={`grid grid-cols-1 gap-px overflow-hidden rounded-md border border-slate-200 bg-slate-200 dark:border-slate-800 dark:bg-slate-800 ${COLS[cols]}`}
    >
      {children}
    </div>
  )
}

/**
 * Stat cell: label, value, optional delta vs a named period. Delta colour = direction x whether
 * "up" is good (for cost, up is bad), and it always carries an arrow + text, never colour alone.
 */
export default function KpiCard({ label, value, delta, deltaLabel, upIsGood = false, footnote, loading }: {
  label: string
  value: ReactNode
  delta?: number | null
  deltaLabel?: string
  upIsGood?: boolean
  footnote?: ReactNode
  loading?: boolean
}) {
  const hasDelta = delta !== undefined && delta !== null
  const good = hasDelta && (upIsGood ? delta > 0 : delta < 0)
  const bad = hasDelta && (upIsGood ? delta < 0 : delta > 0)
  return (
    <div className="bg-white px-4 py-4 dark:bg-slate-900 sm:px-5">
      <p className="text-[12.5px] text-slate-500 dark:text-slate-400">{label}</p>
      {loading ? (
        <Skeleton className="mt-2.5 h-7 w-32" />
      ) : (
        <p className="figure mt-1.5 text-[26px] font-medium leading-none text-slate-900 dark:text-slate-50">{value}</p>
      )}
      {hasDelta && !loading && (
        <p
          className={`mt-2 text-xs ${
            good ? 'text-emerald-700 dark:text-emerald-400' : bad ? 'text-red-700 dark:text-red-400' : 'text-slate-500'
          }`}
        >
          <span className="figure font-medium">
            {delta > 0 ? '↑' : delta < 0 ? '↓' : '→'} {Math.abs(delta).toFixed(1)}%
          </span>{' '}
          <span className="text-slate-500 dark:text-slate-400">{deltaLabel}</span>
        </p>
      )}
      {footnote && !loading && <p className="mt-2 text-xs text-slate-500 dark:text-slate-400">{footnote}</p>}
    </div>
  )
}
