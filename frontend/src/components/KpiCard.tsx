import type { ReactNode } from 'react'
import { Skeleton } from './ui.tsx'

/**
 * Stat tile: label, value, optional delta vs a named period. Delta colour = direction x whether
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
    <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm dark:border-slate-800 dark:bg-slate-900">
      <p className="text-sm text-slate-500 dark:text-slate-400">{label}</p>
      {loading ? (
        <Skeleton className="mt-2 h-8 w-32" />
      ) : (
        <p className="mt-1 text-2xl font-semibold tracking-tight text-slate-900 dark:text-slate-50">{value}</p>
      )}
      {hasDelta && !loading && (
        <p
          className={`mt-1 text-xs font-medium ${
            good ? 'text-emerald-700 dark:text-emerald-400' : bad ? 'text-red-700 dark:text-red-400' : 'text-slate-500'
          }`}
        >
          {delta > 0 ? '▲' : delta < 0 ? '▼' : '■'} {Math.abs(delta).toFixed(1)}% {deltaLabel}
        </p>
      )}
      {footnote && !loading && <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">{footnote}</p>}
    </div>
  )
}
