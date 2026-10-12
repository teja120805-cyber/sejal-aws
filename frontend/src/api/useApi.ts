import { useCallback, useEffect, useState } from 'react'
import { ApiError, api } from './client.ts'

export interface ApiState<T> {
  data: T | null
  error: ApiError | null
  loading: boolean
  reload: () => void
}

interface Result<T> {
  key: string | null
  data: T | null
  error: ApiError | null
}

/**
 * GET `path` (null = skip). Re-fetches when the path or `refreshKey` changes, or on reload().
 * While re-fetching, the previous data stays visible (no flicker); `loading` is derived from
 * whether the latest result belongs to the current request key.
 */
export function useApi<T>(path: string | null, refreshKey: unknown = 0): ApiState<T> {
  const [version, setVersion] = useState(0)
  const [result, setResult] = useState<Result<T>>({ key: null, data: null, error: null })
  const key = path === null ? null : `${path}|${version}|${String(refreshKey)}`

  useEffect(() => {
    if (key === null || path === null) return
    let cancelled = false
    api
      .get<T>(path)
      .then((data) => {
        if (!cancelled) setResult({ key, data, error: null })
      })
      .catch((err: unknown) => {
        if (cancelled) return
        const error = err instanceof ApiError ? err : new ApiError(0, String(err))
        setResult((prev) => ({ key, data: prev.data, error }))
      })
    return () => {
      cancelled = true
    }
  }, [key, path])

  const reload = useCallback(() => setVersion((v) => v + 1), [])
  return {
    data: result.data,
    error: result.key === key ? result.error : null,
    loading: key !== null && result.key !== key,
    reload,
  }
}
