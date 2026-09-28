import { useEffect, useState } from 'react'
import { ChevronRight } from 'lucide-react'
import { apiFetch } from '@/lib/api'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Select } from '@/components/ui/select'
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
// Sentinel select values; real credential ids and model ids never use them.
const NEW_KEY = '__new_key'
const MANUAL_MODEL = '__manual_model'

const PROVIDER_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/

/** Default provider ID for a new server: its host name, when that is a valid ID. */
function providerFromUrl(url: string | null): string {
  try {
    const host = new URL(url ?? '').hostname
    return PROVIDER_PATTERN.test(host) ? host : ''
  } catch { return '' }
}

function connectionSummary(saved: Configuration | null, t: ReturnType<typeof useLocale>['t']): string {
  if (!saved?.base_url) return t('admin.directEndpoint.notConfigured')
  return t('admin.directEndpoint.summary', { url: saved.base_url, protocol: PROTOCOL_LABELS[saved.api_protocol ?? ''] ?? saved.api_protocol ?? '' })
}

/**
 * A model server the agent calls directly (#685, #715). The model list
 * becomes a select once loaded, credential inputs appear only when the
 * admin adds or replaces a key, and the provider ID and protocol sit
 * under Advanced because they have defaults.
 */
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
  const [manualModel, setManualModel] = useState(false)
  const [credentials, setCredentials] = useState<Credential[]>([])
  const [keyEntry, setKeyEntry] = useState<'none' | 'new' | 'replace'>('none')
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
  async function save() {
    await action(async () => {
      const next = await request(path, 'PUT', { ...config, provider: provider })
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
      setKeyEntry('none')
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
      setManualModel(result.models.length === 0)
      // A single served model is the obvious choice for a new connection.
      if (result.models.length === 1 && !config.model) setConfig(previous => previous && ({ ...previous, model: result.models[0].id }))
    })
  }

  const provider = config?.provider || providerFromUrl(config?.base_url ?? null)
  const modelNotServed = models !== null && !!config?.model && !models.some(m => m.id === config.model)
  const showModelSelect = models !== null && models.length > 0 && !manualModel
  const selected = credentials.find(row => row.id === config?.credential_ref)
  const authValue = keyEntry === 'new' ? NEW_KEY : config?.credential_ref ?? ''
  const protocolLabel = PROTOCOL_LABELS[config?.api_protocol ?? ''] ?? config?.api_protocol ?? ''
  return <section className="space-y-4" aria-label={t('admin.directEndpoint.title')}>
    <p className="text-sm text-[var(--color-foreground-muted)]">{connectionSummary(saved, t)}</p>
    {error && <p role="alert" className="text-sm text-[var(--color-destructive)]">{error}</p>}
    {status && <p role="status" className="text-sm">{status}</p>}
    {!config ? (!error && <p className="text-sm">{t('admin.directEndpoint.loading')}</p>) : <fieldset disabled={busy} className="space-y-4">
      <label className="block space-y-1 text-sm font-medium">{t('admin.endpoint.baseUrl')}
        <Input aria-label={t('admin.directEndpoint.baseUrlLabel')} placeholder="http://localhost:8000/v1" value={config.base_url ?? ''}
          onChange={e => { setConfig({ ...config, base_url: e.target.value }); setModels(null); setModelsStatus(null); setManualModel(false) }} />
      </label>

      <div className="space-y-1">
        <div className="flex items-center justify-between gap-2">
          <label htmlFor="direct-endpoint-model" className="text-sm font-medium">{t('admin.directEndpoint.modelId')}</label>
          <Button variant="ghost" size="sm" disabled={!isValidEndpointUrl(config.base_url ?? '')} onClick={() => void loadModels()}>{t('admin.endpoint.loadModels')}</Button>
        </div>
        {showModelSelect ? (
          <Select id="direct-endpoint-model" aria-label={t('admin.directEndpoint.modelLabel')} value={config.model ?? ''}
            onChange={e => {
              if (e.target.value === MANUAL_MODEL) setManualModel(true)
              else setConfig({ ...config, model: e.target.value })
            }}>
            {!config.model && <option value="" disabled>{t('admin.directEndpoint.chooseModel')}</option>}
            {modelNotServed && <option value={config.model ?? ''}>{t('admin.directEndpoint.notServedOption', { model: config.model ?? '' })}</option>}
            {models.map(m => <option key={m.id} value={m.id}>{m.id}</option>)}
            <option value={MANUAL_MODEL}>{t('admin.directEndpoint.manualModel')}</option>
          </Select>
        ) : (
          <div className="flex gap-2">
            <Input id="direct-endpoint-model" aria-label={t('admin.directEndpoint.modelLabel')} value={config.model ?? ''} onChange={e => setConfig({ ...config, model: e.target.value })} />
            {models !== null && models.length > 0 && (
              <Button variant="outline" onClick={() => setManualModel(false)}>{t('admin.directEndpoint.backToList')}</Button>
            )}
          </div>
        )}
        {modelsStatus && <p className="text-xs text-[var(--color-foreground-muted)]">{modelsStatus.reachableFrom === 'server'
          ? t('admin.endpoint.modelsFoundServer', { count: modelsStatus.count })
          : t('admin.endpoint.modelsFoundHost', { count: modelsStatus.count, host: modelsStatus.reachableFrom })}</p>}
        {modelNotServed && <p className="text-xs text-[var(--color-warning)]">{t('admin.directEndpoint.modelNotServed', { model: config.model ?? '' })}</p>}
      </div>

      <div className="space-y-2">
        <label className="block space-y-1 text-sm font-medium">{t('admin.endpoint.authentication')}
          <Select aria-label={t('admin.directEndpoint.credentialLabel')} value={authValue}
            onChange={e => {
              setValue('')
              if (e.target.value === NEW_KEY) { setKeyEntry('new'); return }
              setKeyEntry('none')
              setConfig({ ...config, credential_ref: e.target.value || null })
            }}>
            <option value="">{t('admin.endpoint.noAuthentication')}</option>
            {credentials.map(row => <option key={row.id} value={row.id}>{t('admin.directEndpoint.storedRevision', { label: row.label, revision: row.revision })}</option>)}
            <option value={NEW_KEY}>{t('admin.directEndpoint.newKeyOption')}</option>
          </Select>
        </label>
        {selected && keyEntry === 'none' && (
          <div className="flex flex-wrap items-center gap-3 text-sm">
            <button type="button" className="text-[var(--color-brand-text)] underline-offset-4 hover:underline cursor-pointer" onClick={() => setKeyEntry('replace')}>{t('admin.directEndpoint.replaceKey')}</button>
            <button type="button" className="text-[var(--color-destructive)] underline-offset-4 hover:underline cursor-pointer" onClick={() => void action(async () => {
              await request(`${path}/credentials/${selected.id}`, 'DELETE'); setCredentials(await request(`${path}/credentials`))
              setConfig({ ...config, credential_ref: null })
              setStatus(t('admin.directEndpoint.credentialDeleted'))
            })}>{t('admin.directEndpoint.deleteCredential', { label: selected.label })}</button>
          </div>
        )}
        {keyEntry !== 'none' && (
          <div className="space-y-3 rounded-[var(--radius-md)] border border-[var(--color-border)] bg-[var(--color-surface-alt)] p-3">
            {keyEntry === 'new' && (
              <label className="block space-y-1 text-sm font-medium">{t('admin.directEndpoint.credentialName')}
                <Input aria-label={t('admin.directEndpoint.credentialName')} value={label} onChange={e => setLabel(e.target.value)} />
              </label>
            )}
            <label className="block space-y-1 text-sm font-medium">{t('admin.directEndpoint.newApiKey')}
              <Input aria-label={t('admin.directEndpoint.newApiKeyLabel')} type="password" autoComplete="new-password" value={value} onChange={e => setValue(e.target.value)} />
            </label>
            <p className="text-xs text-[var(--color-foreground-muted)]">{t('admin.directEndpoint.credentialsHelp')}</p>
            <div className="flex flex-wrap gap-2">
              {keyEntry === 'new'
                ? <Button variant="outline" disabled={!value} onClick={() => void store(false)}>{t('admin.directEndpoint.storeNew')}</Button>
                : <Button variant="outline" disabled={!value} onClick={() => void store(true)}>{t('admin.directEndpoint.replace')}</Button>}
              <Button variant="ghost" onClick={() => { setKeyEntry('none'); setValue('') }}>{t('common.cancel')}</Button>
            </div>
          </div>
        )}
      </div>

      {engine === 'pi-cli' && nativeCredentialProvider && provider === nativeCredentialProvider && (
        <p role="alert" className="text-sm text-[var(--color-warning)]">{t('admin.directEndpoint.nativeConflict')}</p>
      )}
      <details className="group border-t border-[var(--color-border)] pt-3">
        <summary className="flex min-h-[var(--control-sm-height)] cursor-pointer list-none items-center gap-2 text-sm font-medium">
          <ChevronRight className="h-4 w-4 transition-transform group-open:rotate-90" aria-hidden="true" />
          {t('admin.directEndpoint.advanced')}
          <span className="font-normal text-[var(--color-foreground-muted)]">· {protocolLabel}{provider ? ` · ${provider}` : ''}</span>
        </summary>
        <div className="mt-3 grid gap-3 sm:grid-cols-2">
          <label className="block space-y-1 text-sm font-medium">{t('admin.directEndpoint.providerId')}
            <Input aria-label={t('admin.directEndpoint.providerLabel')} value={config.provider ?? ''} placeholder={providerFromUrl(config.base_url)} onChange={e => setConfig({ ...config, provider: e.target.value })} />
            <span className="block text-xs font-normal text-[var(--color-foreground-muted)]">{t('admin.directEndpoint.providerHint')}</span>
          </label>
          <label className="block space-y-1 text-sm font-medium">{t('admin.endpoint.apiProtocol')}
            {engine === 'pi-cli' ? (
              <Select aria-label={t('admin.directEndpoint.protocolLabel')} value={config.api_protocol ?? ''} onChange={e => setConfig({ ...config, api_protocol: e.target.value })}>
                <option value="" disabled>{t('admin.directEndpoint.selectProtocol')}</option>
                <option value="responses">Responses</option>
                <option value="chat-completions">Chat Completions</option>
              </Select>
            ) : (
              <span className="flex min-h-[var(--control-height)] items-center text-sm font-normal text-[var(--color-foreground-muted)]">{t('admin.directEndpoint.fixedProtocol')}</span>
            )}
          </label>
        </div>
      </details>

      <div className="flex justify-end">
        <Button onClick={() => void save()} disabled={!isValidEndpointUrl(config.base_url ?? '') || !PROVIDER_PATTERN.test(provider) || !config.model || !config.api_protocol}>{t('admin.directEndpoint.apply')}</Button>
      </div>
    </fieldset>}
  </section>
}
