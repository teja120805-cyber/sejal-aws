import { Link } from 'react-router-dom'
import { Card, EmptyState } from './ui.tsx'

export default function NoAccount() {
  return (
    <Card>
      <EmptyState
        title="No AWS account connected"
        message="Connect an account with a read-only IAM role to start collecting costs and resources."
        action={
          <Link
            to="/settings/connect"
            className="mt-2 rounded-[5px] bg-slate-900 px-3 py-1.5 text-[13px] font-medium text-slate-50 hover:bg-slate-700 dark:bg-slate-100 dark:text-slate-900"
          >
            Connect an AWS account
          </Link>
        }
      />
    </Card>
  )
}
