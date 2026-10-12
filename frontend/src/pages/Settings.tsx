import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api } from '../api/client.ts'
import type { AccountSettings, CollectionRun, ConnectionCheck, RuleInfo } from '../api/types.ts'
import { useAccountApi } from '../api/useAccountApi.ts'
import NoAccount from '../components/NoAccount.tsx'
import { AsyncBlock, Button, Card, CopyButton, ErrorState, Field, Modal, PageHeader, Pill, SeverityBadge, inputClass } from '../components/ui.tsx'
import { useApp } from '../context/AppContext.tsx'
import { dateTime, timeAgo } from '../lib/format.ts'
import { ICONS } from '../lib/icons.ts'

function useSaver() {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<Error | null>(null)
  const [saved, setSaved] = useState(false)
  const run = async (fn: () => Promise<unknown>) => {
    setBusy(true)
    setError(null)
    setSaved(false)
    try {
      await fn()
      setSaved(true)
      setTimeout(() => setSaved(false), 2500)
    } catch (err) {
      setError(err as Error)
    } finally {
      setBusy(false)
    }
  }
  return { busy, error, saved, run }
}

function AccountCard() {
  const { account, reloadAccounts, accounts, selectAccount } = useApp()
  const navigate = useNavigate()
  const [check, setCheck] = useState<ConnectionCheck | null>(null)
  const [remediationArn, setRemediationArn] = useState(account?.remediation_role_arn ?? '')
  const [confirmDelete, setConfirmDelete] = useState(false)
  const tester = useSaver()
  const saver = useSaver()
  if (!account) return null

  const statusTone = account.status === 'connected' ? 'good' : account.status === 'error' ? 'bad' : 'neutral'
  return (
    <Card title="AWS account" subtitle={`${account.name} · ${account.aws_account_id}`} action={<Pill tone={statusTone}>{account.status}</Pill>}>
      <dl className="grid grid-cols-1 gap-x-4 gap-y-2 text-sm sm:grid-cols-3">
        <dt className="text-slate-500">Read-only role</dt>
        <dd className="break-all sm:col-span-2">{account.role_arn}</dd>
        <dt className="text-slate-500">ExternalId</dt>
        <dd className="flex items-center gap-2 break-all sm:col-span-2">
          <code className="text-xs">{account.external_id}</code>
          <CopyButton text={account.external_id} />
        </dd>
        <dt className="text-slate-500">Regions scanned</dt>
        <dd className="sm:col-span-2">{account.regions.length ? account.regions.join(', ') : 'discovered on first collection'}</dd>
        <dt className="text-slate-500">Last collected</dt>
        <dd className="sm:col-span-2">
          {dateTime(account.last_collected_at)} ({timeAgo(account.last_collected_at)})
        </dd>
        <dt className="text-slate-500">Last tested</dt>
        <dd className="sm:col-span-2">{dateTime(account.last_tested_at)}</dd>
        {account.status_message && (
          <>
            <dt className="text-slate-500">Last error</dt>
            <dd className="text-red-700 dark:text-red-400 sm:col-span-2">{account.status_message}</dd>
          </>
        )}
      </dl>

      <div className="mt-4 flex flex-wrap gap-2">
        <Button
          icon={ICONS.check}
          busy={tester.busy}
          onClick={() =>
            tester.run(async () => {
              setCheck(await api.post<ConnectionCheck>(`/api/accounts/${account.id}/test`))
              await reloadAccounts()
            })
          }
        >
          Test connection
        </Button>
        <Button variant="ghost" icon={ICONS.plus} onClick={() => navigate('/settings/connect')}>
          Connect another account
        </Button>
        <Button variant="ghost" className="text-red-700 dark:text-red-400" onClick={() => setConfirmDelete(true)}>
          Disconnect
        </Button>
      </div>
      {check && (
        <p role="status" className={`mt-2 text-sm ${check.ok ? 'text-emerald-700 dark:text-emerald-400' : 'text-red-700 dark:text-red-400'}`}>
          {check.ok ? '✓' : '✗'} {check.message}
        </p>
      )}
      {tester.error && (
        <div className="mt-2">
          <ErrorState error={tester.error} />
        </div>
      )}

      <div className="mt-6 border-t border-slate-200 pt-4 dark:border-slate-800">
        <Field
          label="Remediation role ARN (optional)"
          hint="Deploy finops-remediation-role.yaml to allow one-click fixes. Leave empty to keep the platform strictly read-only."
        >
          <div className="flex gap-2">
            <input
              className={inputClass}
              placeholder="arn:aws:iam::123456789012:role/FinOpsRemediation"
              value={remediationArn}
              onChange={(e) => setRemediationArn(e.target.value)}
            />
            <Button
              busy={saver.busy}
              onClick={() =>
                saver.run(async () => {
                  await api.patch(`/api/accounts/${account.id}`, { remediation_role_arn: remediationArn.trim() || null })
                  await reloadAccounts()
                })
              }
            >
              {saver.saved ? 'Saved' : 'Save'}
            </Button>
          </div>
        </Field>
        {saver.error && (
          <div className="mt-2">
            <ErrorState error={saver.error} />
          </div>
        )}
      </div>

      <Modal
        open={confirmDelete}
        title="Disconnect account?"
        onClose={() => setConfirmDelete(false)}
        footer={
          <>
            <Button onClick={() => setConfirmDelete(false)}>Cancel</Button>
            <Button
              variant="danger"
              onClick={async () => {
                await api.del(`/api/accounts/${account.id}`)
                setConfirmDelete(false)
                await reloadAccounts()
                const next = accounts.find((a) => a.id !== account.id)
                if (next) selectAccount(next.id)
              }}
            >
              Disconnect
            </Button>
          </>
        }
      >
        <p className="text-sm">
          This deletes all stored costs, resources, findings and alerts for <strong>{account.name}</strong>. Nothing in AWS is changed;
          delete the IAM role stack in that account to revoke access completely.
        </p>
      </Modal>
    </Card>
  )
}

function NotificationsCard({ settings, onSaved }: { settings: AccountSettings; onSaved: () => void }) {
  const { account } = useApp()
  const [emails, setEmails] = useState(settings.alert_emails.join(', '))
  const [emailEnabled, setEmailEnabled] = useState(settings.email_enabled)
  const [slackEnabled, setSlackEnabled] = useState(settings.slack_enabled)
  const [webhook, setWebhook] = useState('')
  const saver = useSaver()
  if (!account) return null

  const save = () =>
    saver.run(async () => {
      await api.put(`/api/accounts/${account.id}/settings`, {
        alert_emails: emails
          .split(',')
          .map((e) => e.trim())
          .filter(Boolean),
        email_enabled: emailEnabled,
        slack_enabled: slackEnabled,
        ...(webhook ? { slack_webhook_url: webhook } : {}),
      })
      setWebhook('')
      onSaved()
    })

  return (
    <Card title="Notifications" subtitle="Where budget and anomaly alerts are delivered">
      <div id="notifications" className="space-y-4">
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={emailEnabled} onChange={(e) => setEmailEnabled(e.target.checked)} />
          Email via Amazon SNS
        </label>
        <Field label="Alert emails" hint="Comma separated. Each address receives a one-time confirmation email from AWS SNS.">
          <input className={inputClass} value={emails} onChange={(e) => setEmails(e.target.value)} placeholder="ops@example.com" />
        </Field>
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={slackEnabled} onChange={(e) => setSlackEnabled(e.target.checked)} />
          Slack incoming webhook {settings.slack_configured ? <Pill tone="good">webhook configured</Pill> : <Pill>no webhook</Pill>}
        </label>
        <Field
          label={settings.slack_configured ? 'Replace Slack webhook URL' : 'Slack webhook URL'}
          hint="Stored encrypted in SSM Parameter Store and never shown again."
        >
          <input
            type="password"
            autoComplete="off"
            className={inputClass}
            value={webhook}
            onChange={(e) => setWebhook(e.target.value)}
            placeholder="https://hooks.slack.com/services/..."
          />
        </Field>
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="primary" busy={saver.busy} onClick={save}>
            {saver.saved ? 'Saved ✓' : 'Save notifications'}
          </Button>
          {settings.slack_configured && (
            <Button
              variant="ghost"
              onClick={() =>
                saver.run(async () => {
                  await api.put(`/api/accounts/${account.id}/settings`, { slack_webhook_url: '' })
                  setSlackEnabled(false)
                  onSaved()
                })
              }
            >
              Remove webhook
            </Button>
          )}
          <Link to="/budgets" className="text-sm text-emerald-700 hover:underline dark:text-emerald-400">
            Send a test alert
          </Link>
        </div>
        {saver.error && <ErrorState error={saver.error} />}
      </div>
    </Card>
  )
}

function TagsCard({ settings, onSaved }: { settings: AccountSettings; onSaved: () => void }) {
  const { account } = useApp()
  const [tags, setTags] = useState(settings.required_tags.join(', '))
  const saver = useSaver()
  if (!account) return null
  return (
    <Card title="Required tags" subtitle="Used for tag compliance and cost-by-tag">
      <div id="tags" className="flex flex-col gap-2 sm:flex-row sm:items-end">
        <div className="flex-1">
          <Field label="Tag keys" hint="Comma separated, case-sensitive (AWS tag keys are).">
            <input className={inputClass} value={tags} onChange={(e) => setTags(e.target.value)} />
          </Field>
        </div>
        <div className="sm:pb-5">
          <Button
            variant="primary"
            busy={saver.busy}
            onClick={() =>
              saver.run(async () => {
                await api.put(`/api/accounts/${account.id}/settings`, {
                  required_tags: tags
                    .split(',')
                    .map((t) => t.trim())
                    .filter(Boolean),
                })
                onSaved()
              })
            }
          >
            {saver.saved ? 'Saved ✓' : 'Save'}
          </Button>
        </div>
      </div>
      {saver.error && <ErrorState error={saver.error} />}
    </Card>
  )
}

function ThresholdsCard({ onSaved }: { onSaved: () => void }) {
  const rules = useAccountApi<RuleInfo[]>('/rules')
  return (
    <Card title="Waste rules" subtitle="Thresholds are per account; saving re-evaluates findings immediately (no AWS calls).">
      <AsyncBlock state={rules}>
        {(list) => (
          <ThresholdsForm
            key={JSON.stringify(list)}
            rules={list}
            onSaved={() => {
              rules.reload()
              onSaved()
            }}
          />
        )}
      </AsyncBlock>
    </Card>
  )
}

function ThresholdsForm({ rules, onSaved }: { rules: RuleInfo[]; onSaved: () => void }) {
  const { account } = useApp()
  const [values, setValues] = useState<Record<string, Record<string, string>>>(() =>
    Object.fromEntries(rules.map((r) => [r.id, Object.fromEntries(Object.entries(r.thresholds).map(([k, v]) => [k, String(v)]))])),
  )
  const saver = useSaver()
  if (!account) return null

  const save = () =>
    saver.run(async () => {
      const payload: Record<string, Record<string, number>> = {}
      for (const [ruleId, th] of Object.entries(values)) {
        payload[ruleId] = Object.fromEntries(Object.entries(th).map(([k, v]) => [k, Number(v)]))
      }
      await api.put(`/api/accounts/${account.id}/settings`, { rule_thresholds: payload })
      onSaved()
    })

  return (
    <div className="space-y-4">
      {rules.map((r) => (
        <div key={r.id} className="rounded-lg border border-slate-200 p-3 dark:border-slate-800">
          <div className="flex flex-wrap items-center gap-2">
            <SeverityBadge severity={r.severity} />
            <p className="font-medium">{r.title}</p>
            <code className="text-xs text-slate-500">{r.id}</code>
          </div>
          <p className="mt-1 text-sm text-slate-600 dark:text-slate-400">{r.description}</p>
          {Object.keys(r.thresholds).length > 0 && (
            <div className="mt-2 flex flex-wrap gap-3">
              {Object.keys(r.thresholds).map((k) => (
                <label key={k} className="text-xs">
                  <span className="block text-slate-500">{k}</span>
                  <input
                    type="number"
                    step="any"
                    className={`${inputClass} w-28 py-1`}
                    value={values[r.id]?.[k] ?? ''}
                    onChange={(e) => setValues((v) => ({ ...v, [r.id]: { ...v[r.id], [k]: e.target.value } }))}
                  />
                  {r.default_thresholds[k] !== undefined && <span className="text-slate-400">default {r.default_thresholds[k]}</span>}
                </label>
              ))}
            </div>
          )}
        </div>
      ))}
      <Button variant="primary" busy={saver.busy} onClick={save}>
        {saver.saved ? 'Saved ✓' : 'Save thresholds'}
      </Button>
      {saver.error && <ErrorState error={saver.error} />}
    </div>
  )
}

function RunsCard() {
  const runs = useAccountApi<CollectionRun[]>('/runs?limit=10')
  return (
    <Card title="Collection runs" subtitle="Daily schedule plus manual refreshes. Cost Explorer calls are billed at $0.01 each.">
      <AsyncBlock state={runs}>
        {(list) =>
          list.length === 0 ? (
            <p className="text-sm text-slate-500">No runs recorded yet.</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="text-left text-xs text-slate-500">
                  <tr>
                    <th className="py-2 pr-3">Started</th>
                    <th className="py-2 pr-3">Trigger</th>
                    <th className="py-2 pr-3">Status</th>
                    <th className="py-2 pr-3 text-right">Resources</th>
                    <th className="py-2 text-right">CE calls</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                  {list.map((r) => (
                    <tr key={r.id} title={r.errors.join('\n')}>
                      <td className="py-2 pr-3">{dateTime(r.started_at)}</td>
                      <td className="py-2 pr-3">{r.trigger}</td>
                      <td className="py-2 pr-3">
                        <Pill tone={r.status === 'succeeded' ? 'good' : r.status === 'failed' ? 'bad' : 'neutral'}>{r.status}</Pill>
                      </td>
                      <td className="tabular py-2 pr-3 text-right">{r.resources_collected}</td>
                      <td className="tabular py-2 text-right">{r.cost_explorer_calls}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )
        }
      </AsyncBlock>
    </Card>
  )
}

export default function Settings() {
  const { account } = useApp()
  const settings = useAccountApi<AccountSettings>('/settings')
  if (!account) return <NoAccount />
  return (
    <div className="space-y-6">
      <PageHeader title="Settings" description="Account connection, notifications, tagging policy and rule thresholds." />
      <AccountCard key={account.id} />
      <AsyncBlock state={settings}>
        {(s) => (
          <div className="space-y-6">
            <NotificationsCard key={`n-${account.id}`} settings={s} onSaved={settings.reload} />
            <TagsCard key={`t-${account.id}`} settings={s} onSaved={settings.reload} />
          </div>
        )}
      </AsyncBlock>
      <ThresholdsCard onSaved={settings.reload} />
      <RunsCard />
    </div>
  )
}
