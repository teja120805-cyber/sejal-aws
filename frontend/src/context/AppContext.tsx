import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { api } from '../api/client.ts'
import type { Account, AppConfig } from '../api/types.ts'
import { configureAuth, isLoggedIn } from '../lib/auth.ts'
import { convert, money, moneyCompact, type Currency } from '../lib/format.ts'

interface AppContextValue {
  config: AppConfig
  accounts: Account[]
  account: Account | null
  selectAccount: (id: string) => void
  reloadAccounts: () => Promise<void>
  currency: Currency
  setCurrency: (c: Currency) => void
  /** Format a USD amount in the selected currency. */
  fmt: (usd: number, decimals?: number) => string
  fmtCompact: (usd: number) => string
  toDisplay: (usd: number) => number
  /** Bumped after a refresh so pages re-fetch. */
  dataVersion: number
  bumpData: () => void
}

const AppContext = createContext<AppContextValue | null>(null)
const ACCOUNT_KEY = 'finops-account'
const CURRENCY_KEY = 'finops-currency'

function stored(key: string): string | null {
  try {
    return localStorage.getItem(key)
  } catch {
    return null
  }
}

function store(key: string, value: string) {
  try {
    localStorage.setItem(key, value)
  } catch {
    // ignore
  }
}

type Boot =
  | { state: 'loading' }
  | { state: 'error'; message: string }
  | { state: 'ready'; config: AppConfig }

export function AppProvider({ children, loginScreen, errorScreen, loadingScreen }: {
  children: ReactNode
  loginScreen: ReactNode
  errorScreen: (message: string, retry: () => void) => ReactNode
  loadingScreen: ReactNode
}) {
  const [boot, setBoot] = useState<Boot>({ state: 'loading' })
  const [accounts, setAccounts] = useState<Account[]>([])
  const [accountId, setAccountId] = useState<string | null>(stored(ACCOUNT_KEY))
  const [currency, setCurrencyState] = useState<Currency>(stored(CURRENCY_KEY) === 'INR' ? 'INR' : 'USD')
  const [dataVersion, setDataVersion] = useState(0)

  const reloadAccounts = useCallback(async () => {
    const list = await api.get<Account[]>('/api/accounts')
    setAccounts(list)
    setAccountId((current) => (current && list.some((a) => a.id === current) ? current : (list[0]?.id ?? null)))
  }, [])

  // Async only (no synchronous setState), so it is safe to call from an effect.
  const load = useCallback(() => {
    api
      .get<AppConfig>('/api/config')
      .then(async (config) => {
        configureAuth({ enabled: config.auth.enabled, domain: config.auth.domain, client_id: config.auth.client_id })
        if (isLoggedIn()) await reloadAccounts()
        setBoot({ state: 'ready', config })
      })
      .catch((err: Error) => setBoot({ state: 'error', message: err.message }))
  }, [reloadAccounts])

  useEffect(() => {
    load()
  }, [load])

  const retry = useCallback(() => {
    setBoot({ state: 'loading' })
    load()
  }, [load])

  const selectAccount = useCallback((id: string) => {
    setAccountId(id)
    store(ACCOUNT_KEY, id)
  }, [])

  const setCurrency = useCallback((c: Currency) => {
    setCurrencyState(c)
    store(CURRENCY_KEY, c)
  }, [])

  const value = useMemo<AppContextValue | null>(() => {
    if (boot.state !== 'ready') return null
    const rate = boot.config.usd_inr_rate
    return {
      config: boot.config,
      accounts,
      account: accounts.find((a) => a.id === accountId) ?? null,
      selectAccount,
      reloadAccounts,
      currency,
      setCurrency,
      fmt: (usd, decimals = 2) => money(usd, currency, rate, decimals),
      fmtCompact: (usd) => moneyCompact(usd, currency, rate),
      toDisplay: (usd) => convert(usd, currency, rate),
      dataVersion,
      bumpData: () => setDataVersion((v) => v + 1),
    }
  }, [boot, accounts, accountId, selectAccount, reloadAccounts, currency, setCurrency, dataVersion])

  if (boot.state === 'loading') return <>{loadingScreen}</>
  if (boot.state === 'error') return <>{errorScreen(boot.message, retry)}</>
  if (!isLoggedIn() && !window.location.pathname.startsWith('/auth/callback')) return <>{loginScreen}</>
  return <AppContext.Provider value={value}>{children}</AppContext.Provider>
}

// eslint-disable-next-line react/only-export-components
export function useApp(): AppContextValue {
  const ctx = useContext(AppContext)
  if (!ctx) throw new Error('useApp must be used inside AppProvider')
  return ctx
}
