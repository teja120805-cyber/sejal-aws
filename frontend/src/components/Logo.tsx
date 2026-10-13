/** Brand mark: a cost line stepping down to a point. Same drawing as public/favicon.svg. */
export function LogoMark({ className = 'h-7 w-7' }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" className={className} aria-hidden="true">
      <rect width="32" height="32" rx="7" className="fill-slate-900 dark:fill-slate-100" />
      <path
        d="M8 10h5v5h5v5h6"
        fill="none"
        strokeWidth="2.6"
        strokeLinecap="square"
        className="stroke-slate-50 dark:stroke-slate-900"
      />
      <circle cx="24" cy="20" r="2.6" className="fill-accent-400 dark:fill-accent-600" />
    </svg>
  )
}

export function Wordmark() {
  return (
    <div className="flex items-center gap-2.5">
      <LogoMark />
      <span className="text-[15px] font-semibold tracking-tight">
        FinOps<span className="font-normal text-slate-500 dark:text-slate-400"> / AWS</span>
      </span>
    </div>
  )
}
