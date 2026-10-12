import { getAccessToken } from '../lib/auth.ts'

const BASE = (import.meta.env.VITE_API_BASE_URL as string | undefined)?.replace(/\/$/, '') ?? ''

export class ApiError extends Error {
  status: number
  retryAfterSeconds: number | null

  constructor(status: number, message: string, retryAfterSeconds: number | null = null) {
    super(message)
    this.status = status
    this.retryAfterSeconds = retryAfterSeconds
  }
}

function detailMessage(body: unknown, fallback: string): string {
  if (body && typeof body === 'object' && 'detail' in body) {
    const detail = (body as { detail: unknown }).detail
    if (typeof detail === 'string') return detail
    if (Array.isArray(detail)) {
      // FastAPI validation errors: [{loc, msg}]
      return detail
        .map((d: { loc?: unknown[]; msg?: string }) => `${(d.loc ?? []).slice(1).join('.')}: ${d.msg}`)
        .join('; ')
    }
  }
  return fallback
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = {}
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  const token = await getAccessToken()
  if (token) headers.Authorization = `Bearer ${token}`

  let res: Response
  try {
    res = await fetch(`${BASE}${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    })
  } catch {
    throw new ApiError(0, 'Cannot reach the API. Is the backend running?')
  }

  if (res.status === 204) return undefined as T
  const text = await res.text()
  const data: unknown = text ? JSON.parse(text) : null
  if (!res.ok) {
    const retry = res.headers.get('Retry-After')
    throw new ApiError(res.status, detailMessage(data, res.statusText), retry ? Number(retry) : null)
  }
  return data as T
}

export const api = {
  get: <T>(path: string) => request<T>('GET', path),
  post: <T>(path: string, body?: unknown) => request<T>('POST', path, body ?? {}),
  put: <T>(path: string, body: unknown) => request<T>('PUT', path, body),
  patch: <T>(path: string, body: unknown) => request<T>('PATCH', path, body),
  del: (path: string) => request<void>('DELETE', path),

  /** Downloads a file (e.g. CSV export) with auth, then triggers a browser save. */
  async download(path: string, fallbackName: string): Promise<void> {
    const token = await getAccessToken()
    const res = await fetch(`${BASE}${path}`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    })
    if (!res.ok) throw new ApiError(res.status, `Download failed (${res.status})`)
    const disposition = res.headers.get('Content-Disposition') ?? ''
    const name = /filename="([^"]+)"/.exec(disposition)?.[1] ?? fallbackName
    const url = URL.createObjectURL(await res.blob())
    const a = document.createElement('a')
    a.href = url
    a.download = name
    a.click()
    URL.revokeObjectURL(url)
  },
}

/** Finding IDs contain '#', which must be encoded in URLs. */
export const enc = (value: string) => encodeURIComponent(value)

export function qs(params: Record<string, string | number | undefined | null>): string {
  const entries = Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== '')
  if (!entries.length) return ''
  return '?' + new URLSearchParams(entries.map(([k, v]) => [k, String(v)])).toString()
}
