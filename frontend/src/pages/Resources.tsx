import { useEffect, useState } from 'react'
import { api, qs } from '../api/client.ts'
import type { Resource, ResourceList, ResourceType } from '../api/types.ts'
import { useAccountApi } from '../api/useAccountApi.ts'
import NoAccount from '../components/NoAccount.tsx'
import { AsyncBlock, Button, Card, EmptyState, Modal, PageHeader, Pill, inputClass } from '../components/ui.tsx'
import { ICONS } from '../lib/icons.ts'
import { useApp } from '../context/AppContext.tsx'
import { RESOURCE_LABELS, dateTime } from '../lib/format.ts'

type SortField = 'name' | 'type' | 'region' | 'state' | 'cost' | 'created_at'

const COLUMNS: Array<{ key: SortField; label: string; className?: string }> = [
  { key: 'name', label: 'Name / ID' },
  { key: 'type', label: 'Type' },
  { key: 'region', label: 'Region' },
  { key: 'state', label: 'State' },
  { key: 'created_at', label: 'Created', className: 'hidden lg:table-cell' },
  { key: 'cost', label: 'Est. monthly', className: 'text-right' },
]

function useDebounced<T>(value: T, ms = 300): T {
  const [v, setV] = useState(value)
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms)
    return () => clearTimeout(t)
  }, [value, ms])
  return v
}

function stateTone(state: string | null): 'good' | 'bad' | 'neutral' {
  if (!state) return 'neutral'
  if (['running', 'in-use', 'available', 'associated', 'Active', 'completed'].includes(state)) return 'good'
  if (['stopped', 'unassociated'].includes(state)) return 'bad'
  return 'neutral'
}

export default function Resources() {
  const { account, fmt } = useApp()
  const [q, setQ] = useState('')
  const [type, setType] = useState<ResourceType | ''>('')
  const [region, setRegion] = useState('')
  const [state, setState] = useState('')
  const [sort, setSort] = useState<SortField>('cost')
  const [order, setOrder] = useState<'asc' | 'desc'>('desc')
  const [selected, setSelected] = useState<Resource | null>(null)
  const [exporting, setExporting] = useState(false)
  const search = useDebounced(q)

  const params = { q: search, type, region, state, sort, order }
  const list = useAccountApi<ResourceList>(`/resources${qs(params)}`)
  const options = useAccountApi<ResourceList>('/resources?sort=name') // unfiltered: for filter dropdowns

  if (!account) return <NoAccount />

  const toggleSort = (field: SortField) => {
    if (sort === field) setOrder(order === 'asc' ? 'desc' : 'asc')
    else {
      setSort(field)
      setOrder(field === 'cost' || field === 'created_at' ? 'desc' : 'asc')
    }
  }

  const exportCsv = async () => {
    setExporting(true)
    try {
      await api.download(`/api/accounts/${account.id}/resources/export.csv${qs(params)}`, 'resources.csv')
    } finally {
      setExporting(false)
    }
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Resources"
        description="Inventory across all enabled regions, with estimated monthly cost from list prices."
        actions={
          <Button icon={ICONS.download} busy={exporting} onClick={exportCsv}>
            Export CSV
          </Button>
        }
      />

      <Card>
        <div className="mb-4 grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-4">
          <input
            type="search"
            placeholder="Search name, ID or tag (e.g. owner=web-team)"
            aria-label="Search resources"
            className={inputClass}
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
          <select aria-label="Type" className={inputClass} value={type} onChange={(e) => setType(e.target.value as ResourceType | '')}>
            <option value="">All types</option>
            {Object.entries(RESOURCE_LABELS).map(([k, v]) => (
              <option key={k} value={k}>
                {v} {options.data?.by_type[k] ? `(${options.data.by_type[k]})` : ''}
              </option>
            ))}
          </select>
          <select aria-label="Region" className={inputClass} value={region} onChange={(e) => setRegion(e.target.value)}>
            <option value="">All regions</option>
            {options.data?.regions.map((r) => <option key={r}>{r}</option>)}
          </select>
          <select aria-label="State" className={inputClass} value={state} onChange={(e) => setState(e.target.value)}>
            <option value="">All states</option>
            {options.data?.states.map((s) => <option key={s}>{s}</option>)}
          </select>
        </div>

        <AsyncBlock state={list}>
          {(data) => (
            <>
              <p className="mb-2 text-xs text-slate-500">
                {data.total} resources · {fmt(data.total_monthly_cost_usd)} / month estimated
              </p>
              {data.items.length === 0 ? (
                <EmptyState title="No resources match these filters" />
              ) : (
                <div className="-mx-4 overflow-x-auto sm:mx-0">
                  <table className="w-full min-w-[640px] text-sm">
                    <thead className="border-b border-slate-200 text-left text-xs text-slate-500 dark:border-slate-800">
                      <tr>
                        {COLUMNS.map((c) => (
                          <th key={c.key} className={`px-4 py-2 font-medium sm:px-2 ${c.className ?? ''}`} aria-sort={sort === c.key ? (order === 'asc' ? 'ascending' : 'descending') : 'none'}>
                            <button type="button" onClick={() => toggleSort(c.key)} className="inline-flex items-center gap-1 hover:text-slate-900 dark:hover:text-slate-100">
                              {c.label}
                              <span aria-hidden="true" className="text-[10px]">{sort === c.key ? (order === 'asc' ? '▲' : '▼') : '↕'}</span>
                            </button>
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                      {data.items.map((r) => (
                        <tr key={r.key} onClick={() => setSelected(r)} className="cursor-pointer hover:bg-slate-50 dark:hover:bg-slate-800/50">
                          <td className="max-w-[16rem] px-4 py-2 sm:px-2">
                            <p className="truncate font-medium">{r.name ?? r.resource_id}</p>
                            {r.name && <p className="truncate text-xs text-slate-500">{r.resource_id.split(':').pop()}</p>}
                          </td>
                          <td className="px-4 py-2 sm:px-2">{RESOURCE_LABELS[r.type]}</td>
                          <td className="px-4 py-2 sm:px-2">{r.region}</td>
                          <td className="px-4 py-2 sm:px-2">{r.state ? <Pill tone={stateTone(r.state)}>{r.state}</Pill> : '-'}</td>
                          <td className="hidden px-4 py-2 text-slate-500 sm:px-2 lg:table-cell">{r.created_at ? r.created_at.slice(0, 10) : '-'}</td>
                          <td className="tabular px-4 py-2 text-right font-medium sm:px-2">{fmt(r.est_monthly_cost_usd)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </>
          )}
        </AsyncBlock>
      </Card>

      <Modal open={selected !== null} title={selected?.name ?? selected?.resource_id ?? ''} onClose={() => setSelected(null)}>
        {selected && (
          <dl className="grid grid-cols-3 gap-x-3 gap-y-2 text-sm">
            <dt className="text-slate-500">ID</dt>
            <dd className="col-span-2 break-all">{selected.resource_id}</dd>
            <dt className="text-slate-500">Type</dt>
            <dd className="col-span-2">{RESOURCE_LABELS[selected.type]}</dd>
            <dt className="text-slate-500">Region / state</dt>
            <dd className="col-span-2">{selected.region} · {selected.state ?? '-'}</dd>
            <dt className="text-slate-500">Est. monthly</dt>
            <dd className="col-span-2 font-medium">{fmt(selected.est_monthly_cost_usd)}</dd>
            <dt className="text-slate-500">Created</dt>
            <dd className="col-span-2">{dateTime(selected.created_at)}</dd>
            <dt className="text-slate-500">Tags</dt>
            <dd className="col-span-2 flex flex-wrap gap-1">
              {Object.keys(selected.tags).length ? Object.entries(selected.tags).map(([k, v]) => <Pill key={k}>{k}={v}</Pill>) : <span className="text-red-700 dark:text-red-400">no tags</span>}
            </dd>
            {Object.entries(selected.attributes)
              .filter(([, v]) => v !== null && v !== '' && !(Array.isArray(v) && v.length === 0))
              .map(([k, v]) => (
                <div key={k} className="contents">
                  <dt className="text-slate-500">{k.replace(/_/g, ' ')}</dt>
                  <dd className="col-span-2 break-all">{Array.isArray(v) ? v.join(', ') : String(v)}</dd>
                </div>
              ))}
            {Object.entries(selected.metrics).map(([k, v]) => (
              <div key={k} className="contents">
                <dt className="text-slate-500">{k.replace(/_/g, ' ')}</dt>
                <dd className="col-span-2 tabular">{v.toLocaleString()}</dd>
              </div>
            ))}
          </dl>
        )}
      </Modal>
    </div>
  )
}
