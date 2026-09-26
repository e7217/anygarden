import { useCallback, useEffect, useMemo, useState } from 'react'
import { apiFetch } from '@/lib/api'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import type { Agent, EngineCatalog } from '@/hooks/useAgents'
import { applyEndpoint, getEndpoint, switchPiToNative, type EndpointConfiguration } from '@/lib/engineEndpoints'
import DirectEndpointPanel from './DirectEndpointPanel'
import PiNativeAuthPanel from './PiNativeAuthPanel'
import { useLocale } from '@/i18n/LocaleProvider'
import { compatibleReasoning, reasoningLevelsFor } from '@/lib/engineReasoning'

export type ConnectionState =
  | { agentId: string; status: 'loading' }
  | { agentId: string; status: 'error' }
  | { agentId: string; status: 'ready'; config: EndpointConfiguration }

const selectClass = 'flex min-h-11 w-full rounded border border-[var(--color-border-strong)] bg-[var(--color-background)] px-3 text-sm text-[var(--color-foreground)]'
const providerPattern = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/

type UpdateAgent = (id: string, patch: {
  provider?: string | null; provider_set?: boolean; model?: string | null; model_set?: boolean
  reasoning_effort?: string | null; reasoning_effort_set?: boolean
}) => Promise<Agent>

export default function ModelConnectionPanel({ agent, updateAgent, fetchEngineCatalog, onConnectionChange }: {
  agent: Agent
  updateAgent: UpdateAgent
  fetchEngineCatalog?: (engine: string) => Promise<EngineCatalog | null>
  onConnectionChange: (state: ConnectionState) => void
}) {
  const { t } = useLocale()
  const [connection, setConnection] = useState<EndpointConfiguration | null>(null)
  const [loadStatus, setLoadStatus] = useState<'loading' | 'ready' | 'error'>('loading')
  const [mode, setMode] = useState<'native' | 'direct'>('native')
  const [provider, setProvider] = useState(agent.provider ?? '')
  const [model, setModel] = useState(agent.model ?? '')
  const [nativeKey, setNativeKey] = useState('')
  const [storedNativeProvider, setStoredNativeProvider] = useState<string | null>(null)
  const [catalog, setCatalog] = useState<EngineCatalog | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  const refresh = useCallback(async () => {
    onConnectionChange({ agentId: agent.id, status: 'loading' })
    setLoadStatus('loading')
    try {
      const next = await getEndpoint(agent.id)
      setConnection(next)
      setMode(next.base_url ? 'direct' : 'native')
      setLoadStatus('ready')
      onConnectionChange({ agentId: agent.id, status: 'ready', config: next })
      return next
    } catch (cause) {
      setLoadStatus('error')
      onConnectionChange({ agentId: agent.id, status: 'error' })
      throw cause
    }
  }, [agent.id, onConnectionChange])

  useEffect(() => {
    let active = true
    getEndpoint(agent.id).then(next => {
      if (!active) return
      setConnection(next); setMode(next.base_url ? 'direct' : 'native'); setLoadStatus('ready')
      onConnectionChange({ agentId: agent.id, status: 'ready', config: next })
    }).catch(() => {
      if (!active) return
      setLoadStatus('error'); onConnectionChange({ agentId: agent.id, status: 'error' })
    })
    if (agent.engine === 'pi-cli') {
      apiFetch(`/api/v1/agents/${agent.id}/pi-auth`).then(async response => {
        if (response.ok && active) setStoredNativeProvider((await response.json()).provider ?? null)
      }).catch(() => {})
    }
    return () => { active = false }
  }, [agent.id, agent.engine, onConnectionChange])

  useEffect(() => {
    setProvider(agent.provider ?? '')
    setModel(agent.model ?? '')
  }, [agent.id, agent.provider, agent.model])

  useEffect(() => {
    let active = true
    fetchEngineCatalog?.(agent.engine).then(next => { if (active) setCatalog(next) }).catch(() => {})
    return () => { active = false }
  }, [agent.engine, fetchEngineCatalog])

  const reasoningLevels = useMemo(() => reasoningLevelsFor(catalog, agent.model), [catalog, agent.model])

  async function saveNative() {
    if (!providerPattern.test(provider) || !model.trim()) {
      setError(t('admin.modelConnection.validProviderModel'))
      return
    }
    if (!connection?.base_url && provider === agent.provider && model.trim() === agent.model) return
    setBusy(true); setError(''); setNotice('')
    try {
      if (connection?.base_url) {
        if (!nativeKey && storedNativeProvider !== provider) {
          setError(t('admin.modelConnection.nativeKeyRequired'))
          return
        }
        const previous = connection
        await switchPiToNative(agent.id, provider, model.trim())
        if (nativeKey) {
          const key = nativeKey
          setNativeKey('')
          try {
            const response = await apiFetch(`/api/v1/agents/${agent.id}/pi-auth`, {
              method: 'PUT', body: JSON.stringify({ value: key }),
            })
            if (!response.ok) throw new Error(t('admin.modelConnection.keySaveFailed'))
            setStoredNativeProvider(provider)
          } catch {
            // Re-read before rollback: the first request may have succeeded even
            // if the browser missed its response. Never overwrite another edit.
            const latest = await getEndpoint(agent.id)
            if (!latest.base_url && latest.provider === provider && latest.model === model.trim()) {
              let restored = false
              try {
                await applyEndpoint(agent.id, {
                  provider: previous.provider!, model: previous.model!, base_url: previous.base_url!,
                  api_protocol: previous.api_protocol!, credential_ref: previous.credential_ref,
                })
                restored = true
              } catch { /* current state is shown after refresh below */ }
              if (restored) throw new Error(t('admin.modelConnection.keyRestored'))
              throw new Error(t('admin.modelConnection.keyRestoreFailed'))
            }
            throw new Error(t('admin.modelConnection.keyRetry'))
          }
        }
      } else {
        await updateAgent(agent.id, {
          provider, provider_set: true, model: model.trim(), model_set: true,
        })
      }
      await updateAgent(agent.id, {})
      await refresh()
      setNotice(t('admin.modelConnection.nativeSaved'))
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : t('admin.modelConnection.saveFailed'))
      try { await refresh() } catch { /* load error is displayed below */ }
    } finally { setBusy(false) }
  }

  async function saveModel(value: string) {
    setBusy(true); setError('')
    const reasoning = compatibleReasoning(catalog, value, agent.reasoning_effort)
    try { await updateAgent(agent.id, { model: value || null, model_set: true,
      ...(reasoning !== (agent.reasoning_effort ?? null) ? { reasoning_effort: reasoning, reasoning_effort_set: true } : {}),
    }) }
    catch (cause) { setError(cause instanceof Error ? cause.message : t('admin.modelConnection.saveModelFailed')) }
    finally { setBusy(false) }
  }

  async function saveReasoning(value: string) {
    setBusy(true); setError('')
    try { await updateAgent(agent.id, { reasoning_effort: value || null, reasoning_effort_set: true }) }
    catch (cause) { setError(cause instanceof Error ? cause.message : t('admin.modelConnection.saveReasoningFailed')) }
    finally { setBusy(false) }
  }

  if (loadStatus === 'loading') return <p role="status">{t('admin.modelConnection.loading')}</p>
  if (loadStatus === 'error' || !connection) return <p role="alert">{t('admin.modelConnection.loadFailed')}</p>

  const isPi = agent.engine === 'pi-cli'
  const activeDirect = Boolean(connection.base_url)
  const reasoningLabels: Record<string, string> = {
    minimal: t('admin.machines.reasoning.minimal'),
    low: t('admin.machines.reasoning.low'),
    medium: t('admin.machines.reasoning.medium'),
    high: t('admin.machines.reasoning.high'),
    xhigh: t('admin.machines.reasoning.xhigh'),
    max: t('admin.machines.reasoning.max'),
    ultra: t('admin.machines.reasoning.ultra'),
  }
  return <div className="space-y-4">
    {isPi && <div className="space-y-1">
      <label htmlFor="pi-connection-mode" className="block text-sm font-medium">{t('admin.modelConnection.connectionType')}</label>
      <select id="pi-connection-mode" className={selectClass} value={mode} disabled={busy}
        onChange={event => {
          const next = event.target.value as 'native' | 'direct'
          setMode(next); setError(''); setNotice('')
          if (next === 'native' && activeDirect) { setProvider(''); setModel('') }
          if (next === 'direct' && !activeDirect) { setNativeKey('') }
        }}>
        <option value="native">{t('admin.modelConnection.piProvider')}</option><option value="direct">{t('admin.modelConnection.directServer')}</option>
      </select>
    </div>}
    {error && <p role="alert" className="text-sm text-[var(--color-warning)]">{error}</p>}
    {notice && <p role="status" className="text-sm">{notice}</p>}
    {(activeDirect && (!isPi || mode === 'direct')) || (isPi && mode === 'direct') ? (
      <DirectEndpointPanel key={agent.id} agentId={agent.id} engine={agent.engine} nativeCredentialProvider={storedNativeProvider}
        onSaved={async () => { await updateAgent(agent.id, {}); await refresh() }} />
    ) : isPi ? <div className="space-y-3">
      <label className="block text-sm">{t('admin.modelConnection.providerId')}
        <Input aria-label={t('admin.modelConnection.agentProvider')} value={provider} maxLength={64} onChange={event => setProvider(event.target.value)} disabled={busy} />
      </label>
      <label className="block text-sm">{t('admin.modelConnection.modelId')}
        <Input aria-label={t('admin.modelConnection.agentModel')} value={model} list="pi-native-models" onChange={event => setModel(event.target.value)} disabled={busy} />
      </label>
      <datalist id="pi-native-models">{catalog?.models.map(item => <option key={item.id} value={item.id}>{item.label}</option>)}</datalist>
      {activeDirect && <>
        <p className="text-sm">{t('admin.modelConnection.switchWarning')}</p>
        <label className="block text-sm">{t('admin.modelConnection.nativeKey')} {storedNativeProvider === provider ? t('admin.modelConnection.alreadyStored') : t('admin.modelConnection.required')}
          <Input aria-label={t('admin.modelConnection.nativeKey')} type="password" autoComplete="new-password" value={nativeKey} onChange={event => setNativeKey(event.target.value)} disabled={busy} />
        </label>
      </>}
      <Button className="min-h-11" disabled={busy || !providerPattern.test(provider) || !model.trim() || (activeDirect && !nativeKey && storedNativeProvider !== provider)}
        onClick={() => void saveNative()}>{activeDirect ? t('admin.modelConnection.switchToPi') : t('admin.modelConnection.applyProvider')}</Button>
      {!activeDirect && <PiNativeAuthPanel key={`${agent.id}-${agent.provider ?? ''}`} agentId={agent.id} provider={agent.provider ?? null} onSaved={() => updateAgent(agent.id, {})} />}
    </div> : <div className="space-y-3">
      <label className="block text-sm">{t('admin.modelConnection.model')}
        <select aria-label={t('admin.modelConnection.agentModel')} className={selectClass} value={agent.model ?? ''} disabled={busy || !catalog}
          onChange={event => void saveModel(event.target.value)}>
          <option value="">{t('admin.modelConnection.defaultModel', { model: catalog?.default_model ?? t('admin.modelConnection.engineDefault') })}</option>
          {catalog?.models.map(item => <option key={item.id} value={item.id}>{item.label}</option>)}
          {agent.model && !catalog?.models.some(item => item.id === agent.model) && <option value={agent.model}>{t('admin.modelConnection.currentModel', { model: agent.model })}</option>}
        </select>
      </label>
    </div>}
    {catalog && <label className="block text-sm">{t('admin.modelConnection.reasoning')}
      <select aria-label={t('admin.modelConnection.reasoningEffort')} className={selectClass} value={agent.reasoning_effort ?? ''} disabled={busy || !reasoningLevels.length}
        onChange={event => void saveReasoning(event.target.value)}>
        <option value="">{t('admin.modelConnection.default')}</option>
        {reasoningLevels.map(level => <option key={level} value={level}>{reasoningLabels[level] ?? level}</option>)}
      </select>
    </label>}
    {activeDirect && <p className="text-xs text-[var(--color-foreground-muted)]">{t('admin.modelConnection.directStoredInfo')}</p>}
  </div>
}
