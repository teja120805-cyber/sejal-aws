import { useEffect, useRef, type ButtonHTMLAttributes, type ReactNode } from 'react'
import type { ApiError } from '../api/client.ts'
import type { Severity } from '../api/types.ts'
import { ICONS } from '../lib/icons.ts'
import Icon from './Icon.tsx'


export function Card({ title, subtitle, action, children, className = '' }: {
  title?: ReactNode
  subtitle?: ReactNode
  action?: ReactNode
  children: ReactNode
  className?: string
}) {
  return (
    <section className={`rounded-md border border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900 ${className}`}>
      {(title || action) && (
        <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 border-b border-slate-200 px-4 py-3 dark:border-slate-800 sm:px-5">
          <div className="min-w-0">
            {title && <h2 className="text-[13.5px] font-semibold text-slate-900 dark:text-slate-100">{title}</h2>}
            {subtitle && <p className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">{subtitle}</p>}
          </div>
          {action}
        </div>
      )}
      <div className="p-4 sm:p-5">{children}</div>
    </section>
  )
}

export function PageHeader({ title, description, actions }: { title: string; description?: string; actions?: ReactNode }) {
  return (
    <div className="mb-7 flex flex-wrap items-end justify-between gap-3 border-b border-slate-300/70 pb-4 dark:border-slate-800">
      <div className="min-w-0">
        <h1 className="text-[26px] font-semibold leading-tight tracking-[-0.015em]">{title}</h1>
        {description && <p className="mt-1 max-w-2xl text-[13.5px] text-slate-500 dark:text-slate-400">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  )
}

type Variant = 'primary' | 'secondary' | 'danger' | 'ghost'

const VARIANTS: Record<Variant, string> = {
  primary: 'bg-slate-900 text-slate-50 hover:bg-slate-700 dark:bg-slate-100 dark:text-slate-900 dark:hover:bg-white',
  secondary:
    'border border-slate-300 bg-white text-slate-800 hover:border-slate-400 hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200 dark:hover:border-slate-600',
  danger: 'bg-red-700 text-white hover:bg-red-800',
  ghost: 'text-slate-600 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800',
}

export function Button({ variant = 'secondary', icon, busy, children, className = '', ...props }: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: Variant
  icon?: string
  busy?: boolean
}) {
  return (
    <button
      type="button"
      {...props}
      disabled={props.disabled || busy}
      className={`inline-flex items-center justify-center gap-1.5 rounded-[5px] px-3 py-1.5 text-[13px] font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-60 ${VARIANTS[variant]} ${className}`}
    >
      {busy ? <Spinner /> : icon ? <Icon d={icon} className="h-4 w-4" /> : null}
      {children}
    </button>
  )
}

export function Spinner({ className = 'h-4 w-4' }: { className?: string }) {
  return (
    <svg className={`animate-spin ${className}`} viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <circle cx="12" cy="12" r="9" stroke="currentColor" strokeOpacity="0.25" strokeWidth="3" />
      <path d="M21 12a9 9 0 00-9-9" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
    </svg>
  )
}

export function Skeleton({ className = 'h-4 w-full' }: { className?: string }) {
  return <div className={`animate-pulse rounded bg-slate-200 dark:bg-slate-800 ${className}`} />
}

export function LoadingBlock({ height = 'h-64' }: { height?: string }) {
  return (
    <div className={`flex ${height} flex-col justify-end gap-2`} role="status" aria-label="Loading">
      <Skeleton className="h-1/3 w-full" />
      <Skeleton className="h-4 w-2/3" />
      <Skeleton className="h-4 w-1/3" />
    </div>
  )
}

export function EmptyState({ title, message, action }: { title: string; message?: string; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 py-10 text-center">
      <Icon d={ICONS.inbox} className="h-8 w-8 text-slate-400" />
      <p className="text-sm font-medium text-slate-700 dark:text-slate-200">{title}</p>
      {message && <p className="max-w-md text-sm text-slate-500 dark:text-slate-400">{message}</p>}
      {action}
    </div>
  )
}

export function ErrorState({ error, onRetry }: { error: ApiError | Error; onRetry?: () => void }) {
  return (
    <div
      role="alert"
      className="flex flex-col items-center justify-center gap-2 rounded-md border border-red-200 bg-red-50 px-4 py-8 text-center dark:border-red-900/60 dark:bg-red-950/30"
    >
      <Icon d={ICONS.alert} className="h-7 w-7 text-red-600 dark:text-red-400" />
      <p className="text-sm font-medium text-red-800 dark:text-red-300">Couldn't load this</p>
      <p className="max-w-md text-sm text-red-700 dark:text-red-300/80">{error.message}</p>
      {onRetry && (
        <Button onClick={onRetry} icon={ICONS.refresh}>
          Try again
        </Button>
      )}
    </div>
  )
}

/** Renders loading / error / content consistently. */
export function AsyncBlock<T>({ state, height, children }: {
  state: { data: T | null; error: ApiError | null; loading: boolean; reload: () => void }
  height?: string
  children: (data: T) => ReactNode
}) {
  if (state.error) return <ErrorState error={state.error} onRetry={state.reload} />
  if (state.loading && !state.data) return <LoadingBlock height={height} />
  if (!state.data) return null
  return <>{children(state.data)}</>
}

const SEVERITY_STYLE: Record<Severity, { label: string; dot: string; icon: string }> = {
  high: { label: 'High', dot: 'var(--status-critical)', icon: ICONS.alert },
  medium: { label: 'Medium', dot: 'var(--status-serious)', icon: ICONS.alert },
  low: { label: 'Low', dot: 'var(--status-warning)', icon: ICONS.info },
}

/** Status colour always paired with an icon and a label (never colour alone). */
export function SeverityBadge({ severity }: { severity: Severity }) {
  const s = SEVERITY_STYLE[severity]
  return (
    <span className="inline-flex w-[4.5rem] items-center gap-1 text-xs font-medium text-slate-700 dark:text-slate-300">
      <span style={{ color: s.dot }}>
        <Icon d={s.icon} className="h-3.5 w-3.5" />
      </span>
      {s.label}
    </span>
  )
}

export function Pill({ children, tone = 'neutral' }: { children: ReactNode; tone?: 'neutral' | 'good' | 'bad' | 'info' }) {
  const tones = {
    neutral: 'bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300',
    good: 'bg-emerald-50 text-emerald-800 dark:bg-emerald-500/10 dark:text-emerald-300',
    bad: 'bg-red-50 text-red-800 dark:bg-red-500/10 dark:text-red-300',
    info: 'bg-accent-50 text-accent-700 dark:bg-accent-500/15 dark:text-accent-400',
  }
  return <span className={`inline-flex items-center rounded-[4px] px-1.5 py-0.5 text-xs font-medium ${tones[tone]}`}>{children}</span>
}

export function Modal({ open, title, onClose, children, footer }: {
  open: boolean
  title: string
  onClose: () => void
  children: ReactNode
  footer?: ReactNode
}) {
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    document.addEventListener('keydown', onKey)
    ref.current?.focus()
    return () => document.removeEventListener('keydown', onKey)
  }, [open, onClose])
  if (!open) return null
  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-slate-950/45 p-4 sm:items-center" onClick={onClose}>
      <div
        ref={ref}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        tabIndex={-1}
        onClick={(e) => e.stopPropagation()}
        className="max-h-[90vh] w-full max-w-lg overflow-y-auto rounded-md border border-slate-300 bg-white shadow-[0_12px_32px_-12px_rgb(0_0_0/0.35)] outline-none dark:border-slate-700 dark:bg-slate-900"
      >
        <div className="flex items-center justify-between border-b border-slate-200 px-5 py-3 dark:border-slate-800">
          <h2 className="font-semibold">{title}</h2>
          <button type="button" onClick={onClose} aria-label="Close" className="rounded p-1 text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-800">
            <Icon d={ICONS.x} className="h-4 w-4" />
          </button>
        </div>
        <div className="px-5 py-4">{children}</div>
        {footer && <div className="flex justify-end gap-2 border-t border-slate-200 px-5 py-3 dark:border-slate-800">{footer}</div>}
      </div>
    </div>
  )
}

export function Field({ label, hint, children }: { label: string; hint?: ReactNode; children: ReactNode }) {
  return (
    <label className="block">
      <span className="text-sm font-medium text-slate-700 dark:text-slate-200">{label}</span>
      <div className="mt-1">{children}</div>
      {hint && <span className="mt-1 block text-xs text-slate-500 dark:text-slate-400">{hint}</span>}
    </label>
  )
}

const inputBase =
  'rounded-[5px] border border-slate-300 bg-white px-2.5 py-1.5 text-[13px] text-slate-900 focus:border-accent-500 focus:ring-2 focus:ring-accent-500/25 focus:outline-none dark:border-slate-700 dark:bg-slate-950 dark:text-slate-100'

export const inputClass = `w-full ${inputBase}`
/** For selects/inputs that sit inline in a toolbar. */
export const inlineInputClass = `w-auto ${inputBase}`

export function Segmented<T extends string>({ value, options, onChange, label }: {
  value: T
  options: Array<{ value: T; label: string }>
  onChange: (v: T) => void
  label: string
}) {
  return (
    <div role="radiogroup" aria-label={label} className="inline-flex rounded-[5px] border border-slate-300 bg-white p-0.5 dark:border-slate-700 dark:bg-slate-900">
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          role="radio"
          aria-checked={value === o.value}
          onClick={() => onChange(o.value)}
          className={`rounded-[3px] px-2 py-0.5 text-xs font-medium transition-colors ${
            value === o.value
              ? 'bg-slate-900 text-white dark:bg-slate-100 dark:text-slate-900'
              : 'text-slate-600 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800'
          }`}
        >
          {o.label}
        </button>
      ))}
    </div>
  )
}

export function CopyButton({ text }: { text: string }) {
  return (
    <Button
      variant="ghost"
      icon={ICONS.copy}
      onClick={() => void navigator.clipboard?.writeText(text)}
      aria-label="Copy to clipboard"
      className="px-2 py-1 text-xs"
    >
      Copy
    </Button>
  )
}
