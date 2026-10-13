import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Button, ErrorState, Spinner } from '../components/ui.tsx'
import { LogoMark } from '../components/Logo.tsx'
import { handleCallback, login } from '../lib/auth.ts'

function Centered({ children }: { children: React.ReactNode }) {
  return <div className="flex min-h-screen items-center justify-center p-4 text-slate-900 dark:text-slate-100">{children}</div>
}

export function LoginScreen() {
  return (
    <Centered>
      <div className="w-full max-w-sm rounded-md border border-slate-200 bg-white p-7 text-center dark:border-slate-800 dark:bg-slate-900">
        <LogoMark className="mx-auto h-10 w-10" />
        <h1 className="mt-4 text-lg font-semibold">FinOps / AWS</h1>
        <p className="mt-1 text-sm text-slate-500">AWS cost monitoring and optimisation</p>
        <Button variant="primary" className="mt-5 w-full" onClick={() => void login()}>
          Sign in
        </Button>
        <p className="mt-3 text-xs text-slate-500">Secured by Amazon Cognito</p>
      </div>
    </Centered>
  )
}

export function LoadingScreen() {
  return (
    <Centered>
      <Spinner className="h-6 w-6 text-accent-500" />
    </Centered>
  )
}

export function BootError({ message, retry }: { message: string; retry: () => void }) {
  return (
    <Centered>
      <div className="w-full max-w-md">
        <ErrorState error={new Error(message)} onRetry={retry} />
      </div>
    </Centered>
  )
}

export function AuthCallback() {
  const navigate = useNavigate()
  const [error, setError] = useState<Error | null>(null)
  useEffect(() => {
    handleCallback(window.location.search)
      .then((to) => {
        // Full reload so the app boots with the new tokens.
        window.location.replace(to)
      })
      .catch((err: Error) => setError(err))
  }, [navigate])
  return (
    <Centered>
      {error ? (
        <div className="w-full max-w-md space-y-3 text-center">
          <ErrorState error={error} />
          <Button onClick={() => void login()}>Try signing in again</Button>
        </div>
      ) : (
        <Spinner className="h-6 w-6 text-accent-500" />
      )}
    </Centered>
  )
}
