import { lazy, Suspense } from 'react'
import { Navigate, Route, Routes } from 'react-router-dom'
import Layout from './components/Layout.tsx'
import { LoadingBlock } from './components/ui.tsx'
import { AuthCallback } from './pages/AuthScreens.tsx'

// Route-level code splitting keeps the first load small.
const Overview = lazy(() => import('./pages/Overview.tsx'))
const Costs = lazy(() => import('./pages/Costs.tsx'))
const Resources = lazy(() => import('./pages/Resources.tsx'))
const Recommendations = lazy(() => import('./pages/Recommendations.tsx'))
const Budgets = lazy(() => import('./pages/Budgets.tsx'))
const Tags = lazy(() => import('./pages/Tags.tsx'))
const Settings = lazy(() => import('./pages/Settings.tsx'))
const ConnectAccount = lazy(() => import('./pages/ConnectAccount.tsx'))

function NotFound() {
  return <p className="text-sm text-slate-500">Page not found.</p>
}

export default function App() {
  return (
    <Routes>
      <Route path="/auth/callback" element={<AuthCallback />} />
      <Route element={<Layout />}>
        <Route index element={<Navigate to="/overview" replace />} />
        <Route
          path="*"
          element={
            <Suspense fallback={<LoadingBlock />}>
              <Routes>
                <Route path="/overview" element={<Overview />} />
                <Route path="/costs" element={<Costs />} />
                <Route path="/resources" element={<Resources />} />
                <Route path="/recommendations" element={<Recommendations />} />
                <Route path="/budgets" element={<Budgets />} />
                <Route path="/tags" element={<Tags />} />
                <Route path="/settings" element={<Settings />} />
                <Route path="/settings/connect" element={<ConnectAccount />} />
                <Route path="*" element={<NotFound />} />
              </Routes>
            </Suspense>
          }
        />
      </Route>
    </Routes>
  )
}
