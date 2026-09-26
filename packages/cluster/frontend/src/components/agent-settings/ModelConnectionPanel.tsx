import { useCallback, useEffect, useMemo, useState } from 'react'
import { apiFetch } from '@/lib/api'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import type { Agent, EngineCatalog } from '@/hooks/useAgents'
import { applyEndpoint, getEndpoint, switchPiToNative, type EndpointConfiguration } from '@/lib/engineEndpoints'
import DirectEndpointPanel from './DirectEndpointPanel'
import PiNativeAuthPanel from './PiNativeAuthPanel'

export type ConnectionState =
  | { agentId: string; status: 'loading' }
  | { agentId: string; status: 'error' }
  | { agentId: string; status: 'ready'; config: EndpointConfiguration }

const selectClass = 'flex min-h-11 w-full rounded border border-[var(--color-border-strong)] bg-white px-3 text-sm'
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

  const reasoningLevels = useMemo(() => {
    const selected = catalog?.models.find(item => item.id === agent.model)
    return selected?.reasoning_levels.length ? selected.reasoning_levels : (catalog?.reasoning_levels ?? [])
  }, [catalog, agent.model])

  async function saveNative() {
    if (!providerPattern.test(provider) || !model.trim()) {
      setError('Enter a valid provider ID and model ID.')
      return
    }
    if (!connection?.base_url && provider === agent.provider && model.trim() === agent.model) return
    setBusy(true); setError(''); setNotice('')
    try {
      if (connection?.base_url) {
        if (!nativeKey && storedNativeProvider !== provider) {
          setError('Enter the API key for this native provider before switching.')
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
            if (!response.ok) throw new Error('The provider key could not be saved')
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
              if (restored) throw new Error('Provider key was not saved; the previous direct connection was restored. Retry the switch.')
              throw new Error('Provider key was not saved, and the direct connection could not be restored. Check the current connection and retry.')
            }
            throw new Error('Provider key was not saved. Check the current connection and retry.')
          }
        }
      } else {
        await updateAgent(agent.id, {
          provider, provider_set: true, model: model.trim(), model_set: true,
        })
      }
      await updateAgent(agent.id, {})
      await refresh()
      setNotice('Native provider and model saved.')
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Unable to save connection')
      try { await refresh() } catch { /* load error is displayed below */ }
    } finally { setBusy(false) }
  }

  async function saveModel(value: string) {
    setBusy(true); setError('')
    try { await updateAgent(agent.id, { model: value || null, model_set: true }) }
    catch (cause) { setError(cause instanceof Error ? cause.message : 'Unable to save model') }
    finally { setBusy(false) }
  }

  async function saveReasoning(value: string) {
    setBusy(true); setError('')
    try { await updateAgent(agent.id, { reasoning_effort: value || null, reasoning_effort_set: true }) }
    catch (cause) { setError(cause instanceof Error ? cause.message : 'Unable to save reasoning') }
    finally { setBusy(false) }
  }

  if (loadStatus === 'loading') return <p role="status">Loading model connection…</p>
  if (loadStatus === 'error' || !connection) return <p role="alert">Unable to load model connection. Close and reopen settings to retry.</p>

  const isPi = agent.engine === 'pi-cli'
  const activeDirect = Boolean(connection.base_url)
  return <div className="space-y-4">
    {isPi && <div className="space-y-1">
      <label htmlFor="pi-connection-mode" className="block text-sm font-medium">Connection type</label>
      <select id="pi-connection-mode" className={selectClass} value={mode} disabled={busy}
        onChange={event => {
          const next = event.target.value as 'native' | 'direct'
          setMode(next); setError(''); setNotice('')
          if (next === 'native' && activeDirect) { setProvider(''); setModel('') }
          if (next === 'direct' && !activeDirect) { setNativeKey('') }
        }}>
        <option value="native">Pi provider</option><option value="direct">Direct model server</option>
      </select>
    </div>}
    {error && <p role="alert" className="text-sm text-[var(--color-warning)]">{error}</p>}
    {notice && <p role="status" className="text-sm">{notice}</p>}
    {(activeDirect && (!isPi || mode === 'direct')) || (isPi && mode === 'direct') ? (
      <DirectEndpointPanel key={agent.id} agentId={agent.id} engine={agent.engine} nativeCredentialProvider={storedNativeProvider}
        onSaved={async () => { await updateAgent(agent.id, {}); await refresh() }} />
    ) : isPi ? <div className="space-y-3">
      <label className="block text-sm">Provider ID
        <Input aria-label="Agent provider" value={provider} maxLength={64} onChange={event => setProvider(event.target.value)} disabled={busy} />
      </label>
      <label className="block text-sm">Model ID
        <Input aria-label="Agent model" value={model} list="pi-native-models" onChange={event => setModel(event.target.value)} disabled={busy} />
      </label>
      <datalist id="pi-native-models">{catalog?.models.map(item => <option key={item.id} value={item.id}>{item.label}</option>)}</datalist>
      {activeDirect && <>
        <p className="text-sm">Switching removes the direct connection. The previous endpoint credential remains stored.</p>
        <label className="block text-sm">Native provider API key {storedNativeProvider === provider ? '(already stored)' : '(required)'}
          <Input aria-label="Native provider API key" type="password" autoComplete="new-password" value={nativeKey} onChange={event => setNativeKey(event.target.value)} disabled={busy} />
        </label>
      </>}
      <Button className="min-h-11" disabled={busy || !providerPattern.test(provider) || !model.trim() || (activeDirect && !nativeKey && storedNativeProvider !== provider)}
        onClick={() => void saveNative()}>{activeDirect ? 'Switch to Pi provider' : 'Apply provider and model'}</Button>
      {!activeDirect && <PiNativeAuthPanel key={`${agent.id}-${agent.provider ?? ''}`} agentId={agent.id} provider={agent.provider ?? null} onSaved={() => updateAgent(agent.id, {})} />}
    </div> : <div className="space-y-3">
      <label className="block text-sm">Model
        <select aria-label="Agent model" className={selectClass} value={agent.model ?? ''} disabled={busy || !catalog}
          onChange={event => void saveModel(event.target.value)}>
          <option value="">Default ({catalog?.default_model ?? 'engine default'})</option>
          {catalog?.models.map(item => <option key={item.id} value={item.id}>{item.label}</option>)}
          {agent.model && !catalog?.models.some(item => item.id === agent.model) && <option value={agent.model}>Current: {agent.model}</option>}
        </select>
      </label>
    </div>}
    {catalog && <label className="block text-sm">Reasoning
      <select aria-label="Reasoning effort" className={selectClass} value={agent.reasoning_effort ?? ''} disabled={busy || !reasoningLevels.length}
        onChange={event => void saveReasoning(event.target.value)}>
        <option value="">Default</option>
        {reasoningLevels.map(level => <option key={level} value={level}>{level}</option>)}
      </select>
    </label>}
    {activeDirect && <p className="text-xs text-[var(--color-foreground-muted)]">Stored direct connections remain available for existing agents. Disabling one returns to the engine default.</p>}
  </div>
}
