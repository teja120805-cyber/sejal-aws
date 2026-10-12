import { useApp } from '../context/AppContext.tsx'
import { useApi } from './useApi.ts'

/** GET /api/accounts/{selected}{suffix}; re-fetches when the account changes or after Refresh. */
export function useAccountApi<T>(suffix: string | null) {
  const { account, dataVersion } = useApp()
  return useApi<T>(account && suffix !== null ? `/api/accounts/${account.id}${suffix}` : null, dataVersion)
}
