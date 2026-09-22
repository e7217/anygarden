import { useEffect, useState } from 'react'
import { apiFetch } from '@/lib/api'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'

interface Configuration {
  provider: string | null
  model: string | null
  base_url: string | null
  api_protocol: string | null
  credential_ref: string | null
}
interface Credential { id: string; label: string; revision: number }

export default function DirectEndpointPanel({ agentId, engine, onSaved }: {
  agentId: string; engine: string; onSaved: () => Promise<unknown>
}) {
  const path = `/api/v1/agents/${agentId}/endpoint`
  const [config, setConfig] = useState<Configuration | null>(null)
  const [credentials, setCredentials] = useState<Credential[]>([])
  const [value, setValue] = useState('')
  const [label, setLabel] = useState('Endpoint credential')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [status, setStatus] = useState('')

  async function request(url: string, method = 'GET', body?: unknown) {
    const response = await apiFetch(url, { method, ...(body === undefined ? {} : { body: JSON.stringify(body) }) })
    if (!response.ok) {
      const data = await response.json().catch(() => ({}))
      throw new Error(typeof data.detail === 'string' ? data.detail : 'Unable to update endpoint settings')
    }
    return response.status === 204 ? null : response.json()
  }
  useEffect(() => {
    let active = true
    Promise.all([request(path), request(`${path}/credentials`)]).then(([next, rows]) => {
      if (active) { setConfig(next); setCredentials(rows) }
    }).catch(e => { if (active) setError(e.message) })
    return () => { active = false }
  }, [path]) // eslint-disable-line react-hooks/exhaustive-deps

  async function action(run: () => Promise<void>) {
    setBusy(true); setError(''); setStatus('')
    try { await run() } catch (e) { setError(e instanceof Error ? e.message : 'Unable to update settings') }
    finally { setBusy(false) }
  }
  async function save(enabled: boolean) {
    await action(async () => {
      const next = await request(path, 'PUT', enabled ? config : { base_url: null })
      setConfig(next); await onSaved()
      setStatus('Connection settings saved. The agent will restart with the new settings.')
    })
  }
  async function store(rotate: boolean) {
    // Clear the password immediately after constructing the write-only request.
    const body = { value, label }; setValue('')
    await action(async () => {
      const next = await request(`${path}/credentials${rotate ? `/${config?.credential_ref}` : ''}`, rotate ? 'PUT' : 'POST', body)
      setCredentials(await request(`${path}/credentials`))
      if (!rotate) setConfig(previous => previous && ({ ...previous, credential_ref: next.id }))
      setStatus(rotate ? 'Credential replaced. The agent will restart if it uses this credential.' : 'Credential stored. Apply connection settings to use it.')
    })
  }
  return <section className="space-y-3 rounded border p-3" aria-label="Direct model connection">
    <h3 className="font-medium">Direct model connection</h3>
    <p className="text-sm">Connect this agent to a local or custom model server. Use a Responses endpoint for Codex; Pi also supports Chat Completions.</p>
    {error && <p role="alert">{error}</p>}
    {status && <p role="status">{status}</p>}
    {!config ? (!error && <p>Loading connection settings…</p>) : <fieldset disabled={busy} className="space-y-3">
      <label className="block">Provider ID<Input aria-label="Endpoint provider" value={config.provider ?? ''} onChange={e => setConfig({ ...config, provider: e.target.value })} /></label>
      <label className="block">Model ID<Input aria-label="Endpoint model" value={config.model ?? ''} onChange={e => setConfig({ ...config, model: e.target.value })} /></label>
      <label className="block">Base URL<Input aria-label="Endpoint base URL" placeholder="http://localhost:8000/v1" value={config.base_url ?? ''} onChange={e => setConfig({ ...config, base_url: e.target.value })} /></label>
      <label className="block">API protocol<select aria-label="Endpoint API protocol" value={config.api_protocol ?? ''} onChange={e => setConfig({ ...config, api_protocol: e.target.value })}>
        <option value="" disabled>Select a protocol</option><option value="responses">Responses</option>{engine === 'pi-cli' && <option value="chat-completions">Chat Completions</option>}
      </select></label>
      <label className="block">Authentication<select aria-label="Endpoint credential" value={config.credential_ref ?? ''} onChange={e => setConfig({ ...config, credential_ref: e.target.value || null })}>
        <option value="">No authentication</option>{credentials.map(row => <option key={row.id} value={row.id}>{row.label} — stored (revision {row.revision})</option>)}
      </select></label>
      <div className="flex gap-2"><Button onClick={() => void save(true)} disabled={!config.base_url || !config.provider || !config.model || !config.api_protocol}>Apply connection</Button>
        <Button variant="outline" onClick={() => void save(false)}>Disable direct connection</Button></div>
      <p className="text-sm">Credentials belong to this agent and engine. Stored values cannot be read back.</p>
      <label className="block">Credential label<Input aria-label="Credential label" value={label} onChange={e => setLabel(e.target.value)} /></label>
      <label className="block">New API key<Input aria-label="New endpoint API key" type="password" autoComplete="new-password" value={value} onChange={e => setValue(e.target.value)} /></label>
      <div className="flex gap-2"><Button variant="outline" disabled={!value} onClick={() => void store(false)}>Store new credential</Button>
        <Button variant="outline" disabled={!value || !config.credential_ref} onClick={() => void store(true)}>Replace selected credential</Button></div>
      <ul>{credentials.map(row => <li key={row.id} className="flex items-center gap-2">{row.label}<Button variant="ghost" onClick={() => void action(async () => {
        await request(`${path}/credentials/${row.id}`, 'DELETE'); setCredentials(await request(`${path}/credentials`))
        if (config.credential_ref === row.id) setConfig({ ...config, credential_ref: null })
        setStatus('Credential deleted.')
      })}>Delete {row.label}</Button></li>)}</ul>
    </fieldset>}
  </section>
}
