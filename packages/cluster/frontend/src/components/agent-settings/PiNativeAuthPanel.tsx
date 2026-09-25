import { useEffect, useState } from 'react'
import { apiFetch } from '@/lib/api'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'

interface Status { configured: boolean; provider: string | null; revision: number | null }

export default function PiNativeAuthPanel({ agentId, provider, onSaved }: {
  agentId: string; provider: string | null; onSaved: () => Promise<unknown>
}) {
  const path = `/api/v1/agents/${agentId}/pi-auth`
  const [status, setStatus] = useState<Status | null>(null)
  const [value, setValue] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  useEffect(() => {
    let active = true
    apiFetch(path).then(async response => {
      if (!response.ok) throw new Error('Unable to load Pi authentication')
      const next = await response.json() as Status
      if (active) setStatus(next)
    }).catch(() => { if (active) setError('Unable to load Pi authentication') })
    return () => { active = false }
  }, [path])

  async function update(method: 'PUT' | 'DELETE') {
    const body = method === 'PUT' ? JSON.stringify({ value }) : undefined
    setValue('')
    setError(''); setNotice(''); setBusy(true)
    try {
      const response = await apiFetch(path, { method, ...(body ? { body } : {}) })
      if (!response.ok) {
        const result = await response.json().catch(() => ({}))
        throw new Error(typeof result.detail === 'string' ? result.detail : 'Unable to update Pi authentication')
      }
      setStatus(method === 'DELETE' ? { configured: false, provider: null, revision: null } : await response.json())
      setNotice(method === 'DELETE' ? 'Pi credential removed. The agent will restart.' : 'Pi credential saved. The agent will restart.')
      await onSaved()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Unable to update Pi authentication')
    } finally {
      setBusy(false)
    }
  }

  const active = status?.configured && status.provider === provider
  return <section className="space-y-3 rounded border p-3" aria-label="Pi provider authentication">
    <h3 className="font-medium">Pi provider authentication</h3>
    <p className="text-sm text-[var(--color-foreground-muted)]">
      {active ? `${provider} API key stored (revision ${status.revision})` : status?.provider
        ? `A key for ${status.provider} is stored. Save a key for ${provider ?? 'the selected provider'} before starting.`
        : 'No agent-specific Pi API key is stored.'}
    </p>
    <p className="text-sm">A Pi login in the machine user's home does not authenticate this isolated agent. Add an API key for its selected native provider. Direct model connections use separate credentials.</p>
    {error && <p role="alert">{error}</p>}
    {notice && <p role="status">{notice}</p>}
    <fieldset disabled={busy || !provider} className="space-y-2">
      <label className="block">Provider API key
        <Input aria-label="Pi provider API key" type="password" autoComplete="off" value={value} onChange={event => setValue(event.target.value)} />
      </label>
      <div className="flex gap-2">
        <Button onClick={() => void update('PUT')} disabled={!value}>Save key</Button>
        {status?.provider && <Button variant="outline" onClick={() => void update('DELETE')}>Remove key</Button>}
      </div>
    </fieldset>
  </section>
}
