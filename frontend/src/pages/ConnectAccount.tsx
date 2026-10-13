import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ApiError, api } from '../api/client.ts'
import type { Account, ConnectionCheck, Onboarding } from '../api/types.ts'
import { useApi } from '../api/useApi.ts'
import { AsyncBlock, Button, Card, CopyButton, ErrorState, Field, PageHeader, inputClass } from '../components/ui.tsx'
import { ICONS } from '../lib/icons.ts'
import { useApp } from '../context/AppContext.tsx'

function Step({ n, title, active, done, children }: { n: number; title: string; active: boolean; done: boolean; children: React.ReactNode }) {
  return (
    <Card>
      <div className="flex items-start gap-3">
        <div
          className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-sm font-semibold ${
            done ? 'bg-accent-600 text-white' : active ? 'bg-slate-900 text-white dark:bg-slate-100 dark:text-slate-900' : 'bg-slate-200 text-slate-500 dark:bg-slate-800'
          }`}
        >
          {done ? '✓' : n}
        </div>
        <div className="min-w-0 flex-1">
          <h2 className="font-semibold">{title}</h2>
          {(active || done) && <div className="mt-3">{children}</div>}
        </div>
      </div>
    </Card>
  )
}

function download(name: string, text: string) {
  const url = URL.createObjectURL(new Blob([text], { type: 'text/yaml' }))
  const a = document.createElement('a')
  a.href = url
  a.download = name
  a.click()
  URL.revokeObjectURL(url)
}

export default function ConnectAccount() {
  const { reloadAccounts, selectAccount, config } = useApp()
  const navigate = useNavigate()
  const onboarding = useApi<Onboarding>('/api/onboarding')
  const [step, setStep] = useState(1)
  const [name, setName] = useState('')
  const [roleArn, setRoleArn] = useState('')
  const [created, setCreated] = useState<Account | null>(null)
  const [check, setCheck] = useState<ConnectionCheck | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<Error | null>(null)
  const [showTemplate, setShowTemplate] = useState(false)

  const o = onboarding.data

  const save = async () => {
    if (!o) return
    setBusy(true)
    setError(null)
    try {
      const account = await api.post<Account>('/api/accounts', { name, role_arn: roleArn.trim(), external_id: o.external_id })
      setCreated(account)
      setStep(3)
    } catch (err) {
      setError(err as ApiError)
    } finally {
      setBusy(false)
    }
  }

  const test = async () => {
    if (!created) return
    setBusy(true)
    setError(null)
    try {
      const result = await api.post<ConnectionCheck>(`/api/accounts/${created.id}/test`)
      setCheck(result)
      if (result.ok) {
        await reloadAccounts()
        selectAccount(created.id)
      }
    } catch (err) {
      setError(err as Error)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="mx-auto max-w-3xl space-y-4">
      <PageHeader
        title="Connect an AWS account"
        description="The platform never asks for access keys. You create a read-only IAM role in your account that trusts this platform, protected by a unique ExternalId."
      />
      {config.demo_mode && (
        <p className="rounded-md bg-amber-50 p-3 text-sm text-amber-900 dark:bg-amber-500/10 dark:text-amber-200">
          Demo mode: the connection test is simulated, so you can walk through the flow without AWS.
        </p>
      )}

      <AsyncBlock state={onboarding}>
        {() =>
          o && (
            <>
              <Step n={1} title="Deploy the read-only role in your AWS account" active={step === 1} done={step > 1}>
                <div className="space-y-3 text-sm">
                  <p>Create a CloudFormation stack from the template below with these parameters:</p>
                  <dl className="grid grid-cols-1 gap-2 rounded-md bg-slate-50 p-3 dark:bg-slate-800/60 sm:grid-cols-3">
                    <dt className="text-slate-500">TrustedAccountId</dt>
                    <dd className="flex items-center gap-2 sm:col-span-2"><code>{o.trusted_account_id}</code><CopyButton text={o.trusted_account_id} /></dd>
                    <dt className="text-slate-500">ExternalId</dt>
                    <dd className="flex items-center gap-2 break-all sm:col-span-2"><code>{o.external_id}</code><CopyButton text={o.external_id} /></dd>
                  </dl>
                  <div className="flex flex-wrap gap-2">
                    {o.quick_create_url && (
                      <a href={o.quick_create_url} target="_blank" rel="noreferrer" className="inline-flex items-center rounded-[5px] bg-slate-900 px-3 py-1.5 text-[13px] font-medium text-slate-50 hover:bg-slate-700 dark:bg-slate-100 dark:text-slate-900">
                        Launch stack in AWS console
                      </a>
                    )}
                    <Button icon={ICONS.download} onClick={() => download('finops-readonly-role.yaml', o.template)}>Download template</Button>
                    <Button variant="ghost" onClick={() => setShowTemplate(!showTemplate)}>{showTemplate ? 'Hide' : 'View'} template</Button>
                  </div>
                  {showTemplate && (
                    <pre className="max-h-80 overflow-auto rounded-md bg-slate-950 p-3 text-xs text-slate-100">{o.template}</pre>
                  )}
                  <p className="text-slate-600 dark:text-slate-400">Or with the AWS CLI:</p>
                  <div className="flex items-start gap-2">
                    <pre className="flex-1 overflow-x-auto rounded-md bg-slate-950 p-3 text-xs text-slate-100">{o.cli_command}</pre>
                    <CopyButton text={o.cli_command} />
                  </div>
                  <p className="text-xs text-slate-500">
                    The role can read Cost Explorer, describe EC2/RDS/Lambda, list S3 bucket settings and read CloudWatch metrics. It cannot
                    change anything or read data inside your resources.
                  </p>
                  <Button variant="primary" onClick={() => setStep(2)}>The stack is deployed</Button>
                </div>
              </Step>

              <Step n={2} title="Paste the role ARN" active={step === 2} done={step > 2}>
                <div className="space-y-3">
                  <Field label="Account name" hint="Anything that helps you recognise it, e.g. Production">
                    <input className={inputClass} value={name} onChange={(e) => setName(e.target.value)} disabled={step > 2} />
                  </Field>
                  <Field label="Role ARN" hint="From the stack's Outputs tab (RoleArn)">
                    <input className={`${inputClass} font-mono`} placeholder="arn:aws:iam::123456789012:role/FinOpsReadOnly" value={roleArn} onChange={(e) => setRoleArn(e.target.value)} disabled={step > 2} />
                  </Field>
                  {step === 2 && <Button variant="primary" busy={busy} disabled={!name || !roleArn} onClick={save}>Save account</Button>}
                </div>
              </Step>

              <Step n={3} title="Test the connection" active={step === 3} done={Boolean(check?.ok)}>
                <div className="space-y-3 text-sm">
                  <p className="text-slate-600 dark:text-slate-400">
                    We assume the role with your ExternalId and list enabled regions. This is free: it does not call Cost Explorer.
                  </p>
                  {check && (
                    <p role="status" className={check.ok ? 'text-emerald-700 dark:text-emerald-400' : 'text-red-700 dark:text-red-400'}>
                      {check.ok ? '✓' : '✗'} {check.message}
                    </p>
                  )}
                  <div className="flex gap-2">
                    {!check?.ok && <Button variant="primary" busy={busy} onClick={test}>Test connection</Button>}
                    {check?.ok && <Button variant="primary" onClick={() => navigate('/overview')}>Go to dashboard</Button>}
                  </div>
                  {check?.ok && (
                    <p className="text-slate-600 dark:text-slate-400">
                      The first collection runs on the next daily schedule, or press Refresh in the top bar to collect now.
                    </p>
                  )}
                </div>
              </Step>
              {error && <ErrorState error={error} />}
            </>
          )
        }
      </AsyncBlock>
    </div>
  )
}
