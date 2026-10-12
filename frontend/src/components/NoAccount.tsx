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
            className="mt-2 rounded-lg bg-emerald-600 px-3 py-2 text-sm font-medium text-white hover:bg-emerald-700"
          >
            Connect an AWS account
          </Link>
        }
      />
    </Card>
  )
}
