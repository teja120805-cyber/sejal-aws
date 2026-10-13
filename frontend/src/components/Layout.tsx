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
import { Wordmark } from './Logo.tsx'
import { Button, Segmented } from './ui.tsx'
import { ICONS } from '../lib/icons.ts'

const SUN = 'M12 4V2m0 20v-2m8-8h2M2 12h2m13.7-5.7l1.4-1.4M4.9 19.1l1.4-1.4m0-11.4L4.9 4.9m14.2 14.2l-1.4-1.4M12 17a5 5 0 100-10 5 5 0 000 10z'
const MOON = 'M21 12.8A9 9 0 1111.2 3a7 7 0 009.8 9.8z'
const MENU = 'M4 6h16M4 12h16M4 18h16'

// Sidebar sections, in the order a FinOps review usually goes: what we spend, what to cut, guardrails
const NAV_GROUPS = [
  { label: 'Spend', paths: ['/overview', '/costs'] },
  { label: 'Optimise', paths: ['/resources', '/recommendations'] },
  { label: 'Govern', paths: ['/budgets', '/tags'] },
  { label: 'Workspace', paths: ['/settings'] },
].map((g) => ({ label: g.label, items: NAV_ITEMS.filter((i) => g.paths.includes(i.path)) }))

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
        className={`fixed inset-y-0 left-0 z-30 flex w-56 transform flex-col border-r border-slate-200 bg-slate-50 transition-transform dark:border-slate-800 dark:bg-slate-950 lg:translate-x-0 ${
          menuOpen ? 'translate-x-0' : '-translate-x-full'
        }`}
      >
        <div className="flex h-14 items-center px-4">
          <Wordmark />
        </div>
        <nav className="flex-1 overflow-y-auto px-2 pt-2" aria-label="Main">
          {NAV_GROUPS.map((group) => (
            <div key={group.label} className="mb-5">
              <p className="px-2 pb-1.5 text-[11px] font-medium text-slate-400 dark:text-slate-500">{group.label}</p>
              {group.items.map((item) => (
                <NavLink
                  key={item.path}
                  to={item.path}
                  onClick={() => setMenuOpen(false)}
                  className={({ isActive }) =>
                    `relative flex items-center gap-2.5 rounded-[5px] px-2 py-1.5 text-[13.5px] transition-colors ${
                      isActive
                        ? 'bg-white font-medium text-slate-900 shadow-[0_0_0_1px_var(--color-slate-200)] before:absolute before:inset-y-1.5 before:-left-2 before:w-[3px] before:rounded-r before:bg-accent-500 dark:bg-slate-900 dark:text-slate-50 dark:shadow-[0_0_0_1px_var(--color-slate-800)]'
                        : 'text-slate-600 hover:text-slate-900 dark:text-slate-400 dark:hover:text-slate-100'
                    }`
                  }
                >
                  <Icon d={item.icon} className="h-4 w-4 opacity-70" />
                  {item.label}
                </NavLink>
              ))}
            </div>
          ))}
        </nav>
        <div className="border-t border-slate-200 px-4 py-3 text-xs dark:border-slate-800">
          {config.demo_mode ? (
            <p className="text-slate-500 dark:text-slate-400">
              <span className="mr-1.5 inline-block h-1.5 w-1.5 -translate-y-px rounded-full bg-amber-500" aria-hidden="true" />
              Demo data, no AWS account
            </p>
          ) : (
            <p className="text-slate-500 dark:text-slate-400">Home region {config.region}</p>
          )}
        </div>
      </aside>

      {menuOpen && <div className="fixed inset-0 z-20 bg-slate-900/40 lg:hidden" onClick={() => setMenuOpen(false)} aria-hidden="true" />}

      <div className="lg:pl-56">
        <header className="sticky top-0 z-10 flex h-14 items-center gap-1.5 border-b border-slate-200 bg-[#f4f2ed] px-3 dark:border-slate-800 dark:bg-slate-950 sm:gap-2 sm:px-6">
          <button
            type="button"
            className="rounded-[5px] p-2 text-slate-600 hover:bg-slate-200/60 dark:text-slate-300 dark:hover:bg-slate-800 lg:hidden"
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
              className="min-w-0 max-w-[7rem] truncate rounded-[5px] border border-slate-300 bg-white px-2 py-1.5 text-[13px] font-medium dark:border-slate-700 dark:bg-slate-900 sm:max-w-xs"
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
            className="rounded-[5px] p-2 text-slate-600 hover:bg-slate-200/60 dark:text-slate-300 dark:hover:bg-slate-800"
            aria-label={`Switch to ${theme === 'dark' ? 'light' : 'dark'} mode`}
          >
            <Icon d={theme === 'dark' ? SUN : MOON} />
          </button>
          {isAuthEnabled() && (
            <button
              type="button"
              onClick={logout}
              title={currentUserEmail() ?? 'Sign out'}
              className="rounded-[5px] px-2 py-1.5 text-sm text-slate-600 hover:bg-slate-200/60 dark:text-slate-300 dark:hover:bg-slate-800"
            >
              Sign out
            </button>
          )}
        </header>
        <main className="mx-auto max-w-[1280px] px-4 py-6 sm:px-8 sm:py-8">
          <Outlet />
        </main>
      </div>
    </div>
  )
}
