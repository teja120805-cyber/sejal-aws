import { useState } from 'react'
import { NavLink, Outlet, useNavigate } from 'react-router-dom'
import { ApiError, api } from '../api/client.ts'
import type { CollectionRun } from '../api/types.ts'
import { useApp } from '../context/AppContext.tsx'
import { useTheme } from '../context/ThemeContext.tsx'
import { currentUserEmail, isAuthEnabled, logout } from '../lib/auth.ts'
import { timeAgo } from '../lib/format.ts'
import { NAV_ITEMS } from '../lib/nav.ts'
import Icon from './Icon.tsx'
import { Button, Segmented } from './ui.tsx'
import { ICONS } from '../lib/icons.ts'

const SUN = 'M12 4V2m0 20v-2m8-8h2M2 12h2m13.7-5.7l1.4-1.4M4.9 19.1l1.4-1.4m0-11.4L4.9 4.9m14.2 14.2l-1.4-1.4M12 17a5 5 0 100-10 5 5 0 000 10z'
const MOON = 'M21 12.8A9 9 0 1111.2 3a7 7 0 009.8 9.8z'
const MENU = 'M4 6h16M4 12h16M4 18h16'

function RefreshButton() {
  const { account, config, reloadAccounts, bumpData } = useApp()
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<{ text: string; tone: 'ok' | 'err' } | null>(null)
  if (!account) return null

  const refresh = async () => {
    setBusy(true)
    setMessage(null)
    try {
      const run = await api.post<CollectionRun>(`/api/accounts/${account.id}/refresh`)
      setMessage({ text: `Refreshed: ${run.resources_collected} resources`, tone: 'ok' })
      await reloadAccounts()
      bumpData()
    } catch (err) {
      const text =
        err instanceof ApiError && err.status === 429
          ? `Next refresh in ${Math.ceil((err.retryAfterSeconds ?? 60) / 60)} min`
          : (err as Error).message
      setMessage({ text, tone: 'err' })
    } finally {
      setBusy(false)
      setTimeout(() => setMessage(null), 6000)
    }
  }

  return (
    <div className="flex items-center gap-2">
      {message && (
        <span
          role="status"
          className={`hidden text-xs md:inline ${message.tone === 'ok' ? 'text-emerald-700 dark:text-emerald-400' : 'text-amber-700 dark:text-amber-400'}`}
        >
          {message.text}
        </span>
      )}
      <Button
        icon={ICONS.refresh}
        busy={busy}
        onClick={refresh}
        title={`Pull fresh data from AWS now. Cost Explorer charges per request, so this is limited to once every ${config.refresh_cooldown_minutes} minutes. Data last collected ${timeAgo(account.last_collected_at)}.`}
      >
        <span className="hidden sm:inline">Refresh</span>
      </Button>
    </div>
  )
}

export default function Layout() {
  const { theme, toggleTheme } = useTheme()
  const { accounts, account, selectAccount, currency, setCurrency, config } = useApp()
  const [menuOpen, setMenuOpen] = useState(false)
  const navigate = useNavigate()

  return (
    <div className="min-h-screen text-slate-900 dark:text-slate-100">
      <aside
        className={`fixed inset-y-0 left-0 z-30 flex w-60 transform flex-col border-r border-slate-200 bg-white transition-transform dark:border-slate-800 dark:bg-slate-900 lg:translate-x-0 ${
          menuOpen ? 'translate-x-0' : '-translate-x-full'
        }`}
      >
        <div className="flex h-16 items-center gap-2 px-5">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-emerald-600 text-sm font-bold text-white">F</div>
          <div>
            <p className="font-semibold leading-tight tracking-tight">FinOps</p>
            <p className="text-[11px] leading-tight text-slate-500">AWS cost optimisation</p>
          </div>
        </div>
        <nav className="flex-1 space-y-1 px-3" aria-label="Main">
          {NAV_ITEMS.map((item) => (
            <NavLink
              key={item.path}
              to={item.path}
              onClick={() => setMenuOpen(false)}
              className={({ isActive }) =>
                `flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors ${
                  isActive
                    ? 'bg-emerald-50 text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-400'
                    : 'text-slate-600 hover:bg-slate-100 dark:text-slate-400 dark:hover:bg-slate-800'
                }`
              }
            >
              <Icon d={item.icon} />
              {item.label}
            </NavLink>
          ))}
        </nav>
        {config.demo_mode && (
          <div className="m-3 rounded-lg bg-amber-50 p-3 text-xs text-amber-900 dark:bg-amber-500/10 dark:text-amber-200">
            <p className="font-semibold">Demo mode</p>
            <p className="mt-0.5">Generated data, no AWS account connected.</p>
          </div>
        )}
      </aside>

      {menuOpen && <div className="fixed inset-0 z-20 bg-slate-900/40 lg:hidden" onClick={() => setMenuOpen(false)} aria-hidden="true" />}

      <div className="lg:pl-60">
        <header className="sticky top-0 z-10 flex h-16 items-center gap-1.5 border-b border-slate-200 bg-white/85 px-3 backdrop-blur dark:border-slate-800 dark:bg-slate-900/85 sm:gap-2 sm:px-6">
          <button
            type="button"
            className="rounded-lg p-2 text-slate-600 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800 lg:hidden"
            onClick={() => setMenuOpen(true)}
            aria-label="Open menu"
          >
            <Icon d={MENU} />
          </button>

          {accounts.length > 0 && (
            <select
              aria-label="AWS account"
              value={account?.id ?? ''}
              onChange={(e) => (e.target.value === '__add' ? navigate('/settings/connect') : selectAccount(e.target.value))}
              className="min-w-0 max-w-[7rem] truncate rounded-lg border border-slate-300 bg-white px-2 py-1.5 text-sm dark:border-slate-700 dark:bg-slate-900 sm:max-w-xs"
            >
              {accounts.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.name} ({a.aws_account_id})
                </option>
              ))}
              <option value="__add">+ Connect account…</option>
            </select>
          )}

          <div className="flex-1" />
          <RefreshButton />
          <Segmented
            label="Currency"
            value={currency}
            onChange={setCurrency}
            options={[
              { value: 'USD', label: 'USD' },
              { value: 'INR', label: 'INR' },
            ]}
          />
          <button
            type="button"
            onClick={toggleTheme}
            className="rounded-lg p-2 text-slate-600 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800"
            aria-label={`Switch to ${theme === 'dark' ? 'light' : 'dark'} mode`}
          >
            <Icon d={theme === 'dark' ? SUN : MOON} />
          </button>
          {isAuthEnabled() && (
            <button
              type="button"
              onClick={logout}
              title={currentUserEmail() ?? 'Sign out'}
              className="rounded-lg px-2 py-1.5 text-sm text-slate-600 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800"
            >
              Sign out
            </button>
          )}
        </header>
        <main className="mx-auto max-w-7xl p-4 sm:p-6">
          <Outlet />
        </main>
      </div>
    </div>
  )
}
