/**
 * Cognito Hosted UI login with Authorization Code + PKCE (no client secret in the browser).
 *
 * 1. login(): create a random code_verifier, send its SHA-256 (code_challenge) to /oauth2/authorize
 * 2. Cognito redirects back to /auth/callback?code=...
 * 3. handleCallback(): exchange code + code_verifier at /oauth2/token for tokens
 * Tokens live in sessionStorage (cleared when the tab closes). The access token is sent as a
 * Bearer token and verified by API Gateway's JWT authorizer and again by the FastAPI app.
 */

export interface AuthConfig {
  enabled: boolean
  domain: string
  client_id: string
}

interface Tokens {
  access_token: string
  id_token?: string
  refresh_token?: string
  expires_at: number // epoch ms
}

const TOKENS_KEY = 'finops-tokens'
const VERIFIER_KEY = 'finops-pkce-verifier'
const STATE_KEY = 'finops-pkce-state'
const RETURN_KEY = 'finops-return-to'

let config: AuthConfig | null = null

export function configureAuth(cfg: AuthConfig) {
  config = cfg
}

const redirectUri = () => `${window.location.origin}/auth/callback`

function readTokens(): Tokens | null {
  try {
    const raw = sessionStorage.getItem(TOKENS_KEY)
    return raw ? (JSON.parse(raw) as Tokens) : null
  } catch {
    return null
  }
}

function saveTokens(t: Tokens | null) {
  try {
    if (t) sessionStorage.setItem(TOKENS_KEY, JSON.stringify(t))
    else sessionStorage.removeItem(TOKENS_KEY)
  } catch {
    // storage unavailable
  }
}

function base64Url(bytes: ArrayBuffer | Uint8Array): string {
  const arr = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes)
  let s = ''
  arr.forEach((b) => (s += String.fromCharCode(b)))
  return btoa(s).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
}

function randomString(bytes = 32): string {
  return base64Url(crypto.getRandomValues(new Uint8Array(bytes)))
}

async function challengeFor(verifier: string): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(verifier))
  return base64Url(digest)
}

export function isAuthEnabled(): boolean {
  return Boolean(config?.enabled)
}

export function isLoggedIn(): boolean {
  if (!isAuthEnabled()) return true
  const t = readTokens()
  return Boolean(t && (t.expires_at > Date.now() || t.refresh_token))
}

export async function login(): Promise<void> {
  if (!config) return
  const verifier = randomString(48)
  const state = randomString(16)
  sessionStorage.setItem(VERIFIER_KEY, verifier)
  sessionStorage.setItem(STATE_KEY, state)
  sessionStorage.setItem(RETURN_KEY, window.location.pathname + window.location.search)
  const params = new URLSearchParams({
    response_type: 'code',
    client_id: config.client_id,
    redirect_uri: redirectUri(),
    scope: 'openid email profile',
    code_challenge_method: 'S256',
    code_challenge: await challengeFor(verifier),
    state,
  })
  window.location.assign(`https://${config.domain}/oauth2/authorize?${params}`)
}

async function tokenRequest(body: Record<string, string>): Promise<Tokens> {
  if (!config) throw new Error('auth not configured')
  const res = await fetch(`https://${config.domain}/oauth2/token`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams({ client_id: config.client_id, ...body }),
  })
  if (!res.ok) throw new Error(`token request failed (${res.status})`)
  const data = (await res.json()) as { access_token: string; id_token?: string; refresh_token?: string; expires_in: number }
  return {
    access_token: data.access_token,
    id_token: data.id_token,
    refresh_token: data.refresh_token ?? body.refresh_token,
    expires_at: Date.now() + (data.expires_in - 60) * 1000,
  }
}

/** Handles /auth/callback. Returns the path to navigate back to. */
export async function handleCallback(search: string): Promise<string> {
  const params = new URLSearchParams(search)
  const code = params.get('code')
  const state = params.get('state')
  const verifier = sessionStorage.getItem(VERIFIER_KEY)
  if (!code || !verifier || state !== sessionStorage.getItem(STATE_KEY)) {
    throw new Error(params.get('error_description') ?? 'Invalid login response')
  }
  const tokens = await tokenRequest({
    grant_type: 'authorization_code',
    code,
    redirect_uri: redirectUri(),
    code_verifier: verifier,
  })
  saveTokens(tokens)
  sessionStorage.removeItem(VERIFIER_KEY)
  sessionStorage.removeItem(STATE_KEY)
  return sessionStorage.getItem(RETURN_KEY) || '/'
}

let refreshing: Promise<Tokens> | null = null

export async function getAccessToken(): Promise<string | null> {
  if (!isAuthEnabled()) return null
  const t = readTokens()
  if (!t) return null
  if (t.expires_at > Date.now()) return t.access_token
  if (!t.refresh_token) return null
  try {
    refreshing ??= tokenRequest({ grant_type: 'refresh_token', refresh_token: t.refresh_token })
    const fresh = await refreshing
    saveTokens(fresh)
    return fresh.access_token
  } catch {
    saveTokens(null)
    return null
  } finally {
    refreshing = null
  }
}

export function logout(): void {
  saveTokens(null)
  if (!config) return
  const params = new URLSearchParams({ client_id: config.client_id, logout_uri: window.location.origin })
  window.location.assign(`https://${config.domain}/logout?${params}`)
}

export function currentUserEmail(): string | null {
  const idToken = readTokens()?.id_token
  if (!idToken) return null
  try {
    const payload = JSON.parse(atob(idToken.split('.')[1].replace(/-/g, '+').replace(/_/g, '/')))
    return (payload.email as string) ?? null
  } catch {
    return null
  }
}
