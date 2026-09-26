import { useEffect, useState } from 'react'
import { apiFetch } from '@/lib/api'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { type DiscoveredModel, defaultEndpointProtocol, discoverEndpointModels, isValidEndpointUrl } from '@/lib/engineEndpoints'
import { useLocale } from '@/i18n/LocaleProvider'

interface Configuration {
  provider: string | null
  model: string | null
  base_url: string | null
  api_protocol: string | null
  credential_ref: string | null
}
interface Credential { id: string; label: string; revision: number }

const PROTOCOL_LABELS: Record<string, string> = { responses: 'Responses', 'chat-completions': 'Chat Completions' }

function connectionSummary(saved: Configuration | null, t: ReturnType<typeof useLocale>['t']): string {
  if (!saved?.base_url) return t('admin.directEndpoint.notConfigured')
  return t('admin.directEndpoint.summary', { url: saved.base_url, protocol: PROTOCOL_LABELS[saved.api_protocol ?? ''] ?? saved.api_protocol ?? '' })
}

export default function DirectEndpointPanel({ agentId, engine, onSaved, nativeCredentialProvider }: {
  agentId: string; engine: string; onSaved: () => Promise<unknown>; nativeCredentialProvider?: string | null
}) {
  const { t } = useLocale()
  const path = `/api/v1/agents/${agentId}/endpoint`
  const [config, setConfig] = useState<Configuration | null>(null)
  // Last configuration confirmed by the server — drives the status line.
  const [saved, setSaved] = useState<Configuration | null>(null)
  // #685 — model IDs served by the endpoint (null until loaded).
  const [models, setModels] = useState<DiscoveredModel[] | null>(null)
  const [modelsStatus, setModelsStatus] = useState<{ count: number; reachableFrom: string } | null>(null)
  const [credentials, setCredentials] = useState<Credential[]>([])
  const [value, setValue] = useState('')
  const [label, setLabel] = useState(() => t('admin.directEndpoint.credentialLabel'))
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [status, setStatus] = useState('')

  async function request(url: string, method = 'GET', body?: unknown) {
    const response = await apiFetch(url, { method, ...(body === undefined ? {} : { body: JSON.stringify(body) }) })
    if (!response.ok) {
      const data = await response.json().catch(() => ({}))
      throw new Error(typeof data.detail === 'string' ? data.detail : t('admin.directEndpoint.updateFailed'))
    }
    return response.status === 204 ? null : response.json()
  }
  useEffect(() => {
    let active = true
    Promise.all([request(path), request(`${path}/credentials`)]).then(([next, rows]) => {
      if (active) {
        setConfig({ ...next,
          provider: next.base_url ? next.provider : '', model: next.base_url ? next.model : '',
          api_protocol: next.api_protocol ?? defaultEndpointProtocol(engine),
        })
        setSaved(next); setCredentials(rows)
      }
    }).catch(e => { if (active) setError(e.message) })
    return () => { active = false }
  }, [path]) // eslint-disable-line react-hooks/exhaustive-deps

  async function action(run: () => Promise<void>) {
    setBusy(true); setError(''); setStatus('')
    try { await run() } catch (e) { setError(e instanceof Error ? e.message : t('admin.directEndpoint.actionFailed')) }
    finally { setBusy(false) }
  }
  async function save(enabled: boolean) {
    await action(async () => {
      const next = await request(path, 'PUT', enabled ? config : engine === 'codex-cli' ? { base_url: null, model: null } : { base_url: null })
      setConfig(next); setSaved(next); await onSaved()
      setStatus(t('admin.directEndpoint.saved'))
    })
  }
  async function store(rotate: boolean) {
    // Clear the password immediately after constructing the write-only request.
    const body = { value, label }; setValue('')
    await action(async () => {
      const next = await request(`${path}/credentials${rotate ? `/${config?.credential_ref}` : ''}`, rotate ? 'PUT' : 'POST', body)
      setCredentials(await request(`${path}/credentials`))
      if (!rotate) setConfig(previous => previous && ({ ...previous, credential_ref: next.id }))
      setStatus(rotate ? t('admin.directEndpoint.credentialReplaced') : t('admin.directEndpoint.credentialStored'))
    })
  }
  async function loadModels() {
    if (!config?.base_url) return
    const base_url = config.base_url
    await action(async () => {
      setModels(null); setModelsStatus(null)
      const result = await discoverEndpointModels(config.credential_ref
        ? { base_url, agent_id: agentId, credential_ref: config.credential_ref }
        : { base_url })
      setModels(result.models)
      setModelsStatus({ count: result.models.length, reachableFrom: result.reachable_from })
    })
  }
  const modelNotServed = models !== null && !!config?.model && !models.some(m => m.id === config.model)
  return <section className="space-y-3 rounded border border-[var(--color-border)] p-3" aria-label={t('admin.directEndpoint.title')}>
    <h3 className="font-medium">{t('admin.directEndpoint.title')}</h3>
    <p className="text-sm text-[var(--color-foreground-muted)]">{connectionSummary(saved, t)}</p>
    <p className="text-sm">{t('admin.directEndpoint.description')}</p>
    {error && <p role="alert">{error}</p>}
    {status && <p role="status">{status}</p>}
    {!config ? (!error && <p>{t('admin.directEndpoint.loading')}</p>) : <fieldset disabled={busy} className="space-y-3">
      <label className="block">{t('admin.directEndpoint.providerId')}<Input aria-label={t('admin.directEndpoint.providerLabel')} value={config.provider ?? ''} onChange={e => setConfig({ ...config, provider: e.target.value })} /></label>
      {engine === 'pi-cli' && nativeCredentialProvider && config.provider === nativeCredentialProvider && (
        <p role="alert" className="text-sm text-[var(--color-warning)]">{t('admin.directEndpoint.nativeConflict')}</p>
      )}
      <label className="block">{t('admin.endpoint.baseUrl')}<Input aria-label={t('admin.directEndpoint.baseUrlLabel')} placeholder="http://localhost:8000/v1" value={config.base_url ?? ''} onChange={e => { setConfig({ ...config, base_url: e.target.value }); setModels(null); setModelsStatus(null) }} /></label>
      <label className="block">{t('admin.directEndpoint.modelId')}<Input aria-label={t('admin.directEndpoint.modelLabel')} list="direct-endpoint-models" value={config.model ?? ''} onChange={e => setConfig({ ...config, model: e.target.value })} /></label>
      <datalist id="direct-endpoint-models">{models?.map(m => <option key={m.id} value={m.id} />)}</datalist>
      <div className="flex flex-wrap items-center gap-2">
        <Button variant="outline" size="sm" disabled={!isValidEndpointUrl(config.base_url ?? '')} onClick={() => void loadModels()}>{t('admin.endpoint.loadModels')}</Button>
        {modelsStatus && <span className="text-xs text-[var(--color-foreground-muted)]">{modelsStatus.reachableFrom === 'server'
          ? t('admin.endpoint.modelsFoundServer', { count: modelsStatus.count })
          : t('admin.endpoint.modelsFoundHost', { count: modelsStatus.count, host: modelsStatus.reachableFrom })}</span>}
      </div>
      {modelNotServed && <p className="text-xs text-[var(--color-warning)]">{t('admin.directEndpoint.modelNotServed', { model: config.model ?? '' })}</p>}
      <label className="block">{t('admin.endpoint.apiProtocol')}<select aria-label={t('admin.directEndpoint.protocolLabel')} value={config.api_protocol ?? ''} onChange={e => setConfig({ ...config, api_protocol: e.target.value })}>
        <option value="" disabled>{t('admin.directEndpoint.selectProtocol')}</option><option value="responses">Responses</option>{engine === 'pi-cli' && <option value="chat-completions">Chat Completions</option>}
      </select></label>
      <label className="block">{t('admin.endpoint.authentication')}<select aria-label={t('admin.directEndpoint.credentialLabel')} value={config.credential_ref ?? ''} onChange={e => setConfig({ ...config, credential_ref: e.target.value || null })}>
        <option value="">{t('admin.endpoint.noAuthentication')}</option>{credentials.map(row => <option key={row.id} value={row.id}>{t('admin.directEndpoint.storedRevision', { label: row.label, revision: row.revision })}</option>)}
      </select></label>
      <div className="flex flex-wrap gap-2"><Button className="min-h-11" onClick={() => void save(true)} disabled={!isValidEndpointUrl(config.base_url ?? '') || !config.provider || !config.model || !config.api_protocol}>{t('admin.directEndpoint.apply')}</Button>
        {saved?.base_url && engine === 'codex-cli' && <Button className="min-h-11" variant="outline" onClick={() => void save(false)}>{t('admin.directEndpoint.disable')}</Button>}</div>
      <p className="text-sm">{t('admin.directEndpoint.credentialsHelp')}</p>
      <label className="block">{t('admin.directEndpoint.credentialName')}<Input aria-label={t('admin.directEndpoint.credentialName')} value={label} onChange={e => setLabel(e.target.value)} /></label>
      <label className="block">{t('admin.directEndpoint.newApiKey')}<Input aria-label={t('admin.directEndpoint.newApiKeyLabel')} type="password" autoComplete="new-password" value={value} onChange={e => setValue(e.target.value)} /></label>
      <div className="flex flex-wrap gap-2"><Button variant="outline" disabled={!value} onClick={() => void store(false)}>{t('admin.directEndpoint.storeNew')}</Button>
        <Button variant="outline" disabled={!value || !config.credential_ref} onClick={() => void store(true)}>{t('admin.directEndpoint.replace')}</Button></div>
      <ul>{credentials.map(row => <li key={row.id} className="flex items-center gap-2">{row.label}<Button variant="ghost" onClick={() => void action(async () => {
        await request(`${path}/credentials/${row.id}`, 'DELETE'); setCredentials(await request(`${path}/credentials`))
        if (config.credential_ref === row.id) setConfig({ ...config, credential_ref: null })
        setStatus(t('admin.directEndpoint.credentialDeleted'))
      })}>{t('admin.directEndpoint.deleteCredential', { label: row.label })}</Button></li>)}</ul>
    </fieldset>}
  </section>
}
