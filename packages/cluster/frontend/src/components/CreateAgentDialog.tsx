import { useEffect, useMemo, useState } from 'react'
import { ChevronRight, FolderOpen, History, Server, Shield } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select } from '@/components/ui/select'
import { Textarea } from '@/components/ui/textarea'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import type { Agent, AvailableEngine, EngineCatalog, useAgents } from '@/hooks/useAgents'
import type { Room, RoomsStatus } from '@/hooks/useRooms'
import { useLocale } from '@/i18n/LocaleProvider'
import { extractEngineVersion, unsupportedEngineVersionWarning } from '@/lib/engineVersion'
import { applyEndpoint, storeEndpointCredential, type DiscoveredModel } from '@/lib/engineEndpoints'
import { uuid } from '@/lib/federationApi'
import { compatibleReasoning, reasoningLevelsFor } from '@/lib/engineReasoning'
import CreateAgentEndpointSection, { emptyEndpointDraft, endpointDraftReady, type EndpointDraft } from '@/components/CreateAgentEndpointSection'

interface Props {
  open: boolean
  onOpenChange: (open: boolean) => void
  machineId: string
  machineName: string
  engines: Array<{ engine: string; version?: string | null }>
  availableEngines: AvailableEngine[]
  projects: Array<{ id: string; name: string }>
  roomsByProject: Record<string, Room[]>
  roomsStatus?: RoomsStatus
  onRetryRooms?: () => Promise<void>
  createAgent: ReturnType<typeof useAgents>['createAgent']
  fetchEngineCatalog: ReturnType<typeof useAgents>['fetchEngineCatalog']
  onCreated: (agent: Agent) => void
}

const ENGINE_LABELS: Record<string, string> = {
  'pi-cli': 'Pi', 'codex-cli': 'Codex CLI', 'claude-code': 'Claude Code',
  'gemini-cli': 'Gemini CLI', openai: 'OpenAI API', anthropic: 'Anthropic API',
}
const selectCSS = 'h-[var(--control-height)]'

/** Owns validation and initial role, room, and permission setup in one draft. */
export default function CreateAgentDialog({ open, onOpenChange, machineId, machineName, engines, availableEngines, projects, roomsByProject, roomsStatus = 'ready', onRetryRooms, createAgent, fetchEngineCatalog, onCreated }: Props) {
  const { locale, t } = useLocale()
  const [agentName, setAgentName] = useState('')
  const [requestId, setRequestId] = useState(uuid)
  const [description, setDescription] = useState('')
  const [instructions, setInstructions] = useState('')
  const [permission, setPermission] = useState<'restricted' | 'standard' | 'trusted'>('standard')
  const [agentEngine, setAgentEngine] = useState('')
  const [agentModel, setAgentModel] = useState('')
  const [agentProvider, setAgentProvider] = useState('')
  const [agentReasoning, setAgentReasoning] = useState('')
  const [agentCatalog, setAgentCatalog] = useState<EngineCatalog | null>(null)
  const [endpointDraft, setEndpointDraft] = useState<EndpointDraft>(() => emptyEndpointDraft(''))
  const [discoveredModels, setDiscoveredModels] = useState<DiscoveredModel[]>([])
  const [agentRooms, setAgentRooms] = useState<Set<string>>(new Set())
  const [creating, setCreating] = useState(false)
  const [createError, setCreateError] = useState<string | null>(null)
  const [createdAgent, setCreatedAgent] = useState<Agent | null>(null)
  const validPiProvider = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/.test(agentProvider)
  const endpointActive = agentEngine === 'pi-cli' && endpointDraft.enabled
  const providerRequired = agentEngine === 'pi-cli'
  const engineMetadataById = useMemo(() => new Map(availableEngines.map(engine => [engine.engine, engine])), [availableEngines])
  const selectedAgentEngineMeta = engineMetadataById.get(agentEngine)
  const selectableRooms = projects.flatMap(project => (roomsByProject[project.id] ?? []).filter(room => !room.is_dm))

  useEffect(() => {
    if (!open) return
    setAgentName(''); setDescription(''); setInstructions(''); setPermission('standard')
    setAgentEngine(engines[0]?.engine ?? ''); setAgentRooms(new Set())
    setAgentProvider(''); setAgentModel(''); setAgentReasoning('')
    setEndpointDraft(emptyEndpointDraft('')); setDiscoveredModels([])
    setCreateError(null); setCreatedAgent(null)
    setRequestId(uuid())
    // Start a fresh draft for each opening; machine refreshes must not erase edits.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, machineId])

  useEffect(() => {
    setAgentProvider(''); setEndpointDraft(emptyEndpointDraft(agentEngine)); setDiscoveredModels([])
    setAgentModel(''); setAgentReasoning(''); setAgentCatalog(null)
    if (!agentEngine) return
    let cancelled = false
    fetchEngineCatalog(agentEngine).then(catalog => { if (!cancelled) setAgentCatalog(catalog) })
      .catch(() => { if (!cancelled) setAgentCatalog(null) })
    return () => { cancelled = true }
  }, [agentEngine, fetchEngineCatalog])

  const agentReasoningLevels = useMemo(() => reasoningLevelsFor(agentCatalog, agentModel), [agentCatalog, agentModel])
  function changeModel(model: string) {
    setAgentModel(model)
    setAgentReasoning(current => compatibleReasoning(agentCatalog, model, current) ?? '')
  }
  const rawVersionWarning = agentCatalog?.engine === agentEngine
    ? unsupportedEngineVersionWarning(agentEngine, engines.find(e => e.engine === agentEngine)?.version, agentCatalog.supported_versions)
    : null
  const detectedVersion = extractEngineVersion(engines.find(e => e.engine === agentEngine)?.version)
  const requiredVersions = agentCatalog?.supported_versions ?? []
  const agentEngineVersionWarning = rawVersionWarning && locale === 'ko' && detectedVersion
    ? t('admin.machines.versionWarning', { engine: agentEngine, version: detectedVersion,
        required: requiredVersions.length === 1 ? requiredVersions[0] : t('admin.machines.oneOfVersions', { versions: requiredVersions.join(', ') }) })
    : rawVersionWarning
  const reasoningLabel = (level: string) => ({
    minimal: t('admin.machines.reasoning.minimal'), low: t('admin.machines.reasoning.low'),
    medium: t('admin.machines.reasoning.medium'), high: t('admin.machines.reasoning.high'),
    xhigh: t('admin.machines.reasoning.xhigh'), max: t('admin.machines.reasoning.max'), ultra: t('admin.machines.reasoning.ultra'),
  }[level] ?? level)
  const ready = !!agentName.trim() && !!agentEngine && !!machineId
    && (!providerRequired || (validPiProvider && !!agentModel.trim()))
    && (!endpointActive || endpointDraftReady(endpointDraft, agentModel))

  const finish = (agent: Agent) => { onOpenChange(false); onCreated(agent) }
  async function handleCreateAgent() {
    if (!ready || creating || createdAgent) return
    setCreating(true); setCreateError(null)
    try {
      const model = agentModel.trim()
      const created: Agent = await createAgent({
        name: agentName.trim(), engine: agentEngine, machine_id: machineId,
        request_id: requestId,
        rooms: Array.from(agentRooms),
        ...(description.trim() ? { description: description.trim() } : {}),
        ...(instructions.trim() ? { agents_md: instructions.trim() } : {}),
        ...(permission !== 'standard' ? { permission_level: permission } : {}),
        ...(providerRequired ? { provider: agentProvider } : {}),
        ...(model ? { model } : {}),
        ...(agentReasoning ? { reasoning_effort: agentReasoning } : {}),
        ...(endpointActive ? { endpoint: { base_url: endpointDraft.baseUrl, api_protocol: endpointDraft.protocol } } : {}),
      })
      setCreatedAgent(created)
      if (endpointActive && endpointDraft.auth === 'key') {
        try {
          const credentialRef = await storeEndpointCredential(created.id, endpointDraft.apiKey)
          await applyEndpoint(created.id, { provider: agentProvider, model,
            base_url: endpointDraft.baseUrl, api_protocol: endpointDraft.protocol, credential_ref: credentialRef })
        } catch (error) {
          setCreateError(t('admin.machines.agentKeyError', { error: error instanceof Error ? error.message : String(error) }))
          return
        }
      }
      finish(created)
    } catch (error) {
      setCreateError(error instanceof Error ? error.message : String(error))
    } finally { setCreating(false) }
  }

  return (
    <Dialog open={open} onOpenChange={next => { if (!creating) onOpenChange(next) }}>
      <DialogContent className="flex max-h-[90dvh] max-w-2xl flex-col gap-0 overflow-hidden p-0">
        <DialogHeader className="shrink-0 border-b border-[var(--color-border)] px-4 py-4 pr-14 sm:px-6 sm:pr-14">
          <DialogTitle>{t('admin.machines.createAgentTitle', { name: machineName })}</DialogTitle>
          <DialogDescription>{t('agentSetup.intro')}</DialogDescription>
        </DialogHeader>
        <div className="min-h-0 flex-1 space-y-6 overflow-y-auto px-4 py-5 sm:px-6">
          <fieldset disabled={creating || !!createdAgent} className="min-w-0 space-y-4 disabled:opacity-60">
            <legend className="text-sm font-semibold">{t('agentSetup.identity')}</legend>
            <div className="space-y-2">
              <Label htmlFor="create-agent-name">{t('admin.machines.name')}</Label>
              <Input id="create-agent-name" placeholder={t('admin.machines.agentNamePlaceholder')} value={agentName} onChange={e => setAgentName(e.target.value)} autoFocus required />
            </div>
            <div className="space-y-2">
              <Label htmlFor="create-agent-description">{t('admin.overview.agentDescription')}</Label>
              <Textarea id="create-agent-description" rows={2} maxLength={200} value={description} onChange={e => setDescription(e.target.value)} className="min-h-20" placeholder={t('admin.overview.descriptionPlaceholder')} aria-describedby="create-agent-description-help" />
              <div className="flex justify-between gap-4 text-xs text-[var(--color-foreground-muted)]">
                <p id="create-agent-description-help">{t('admin.overview.descriptionVisibility')}</p><span className="shrink-0">{description.length}/200</span>
              </div>
            </div>
          </fieldset>
          <fieldset disabled={creating || !!createdAgent} className="min-w-0 space-y-4 border-t border-[var(--color-border)] disabled:opacity-60">
            <legend className="flex items-center gap-2 pr-2 text-sm font-semibold"><Server className="h-4 w-4" />{t('agentSetup.runtime')}</legend>
            <div className="space-y-2">
              <Label htmlFor="create-agent-engine">{t('admin.machines.engine')}</Label>
              <Select id="create-agent-engine" value={agentEngine} onChange={e => setAgentEngine(e.target.value)} className={selectCSS}>
                <option value="" disabled>{t('admin.machines.selectEngine')}</option>
                {engines.map(e => {
                  const label = ENGINE_LABELS[e.engine] ?? e.engine
                  const deprecated = engineMetadataById.get(e.engine)?.deprecated === true
                  return (
                    <option key={e.engine} value={e.engine}>
                      {deprecated ? `${label} (${t('admin.machines.deprecated')})` : label}
                    </option>
                  )
                })}
              </Select>
              {selectedAgentEngineMeta?.deprecated ? (
                <p
                  className="text-xs text-[var(--color-warning)]"
                  title={selectedAgentEngineMeta.deprecation_note ?? undefined}
                >
                  {t('admin.machines.deprecatedEngine')}
                </p>
              ) : null}
              {agentEngineVersionWarning && (
                <p role="status" className="text-xs text-[var(--color-warning)]">
                  {agentEngineVersionWarning}
                </p>
              )}
            </div>
            {agentEngine === 'pi-cli' && (
              <div className="space-y-2">
                <Label htmlFor="create-pi-connection-type">{t('admin.machines.connectionType')}</Label>
                <Select id="create-pi-connection-type" className={selectCSS}
                  value={endpointActive ? 'direct' : 'native'}
                  onChange={e => {
                    setEndpointDraft({ ...emptyEndpointDraft('pi-cli'), enabled: e.target.value === 'direct' })
                    setAgentProvider(''); setAgentModel(''); setAgentReasoning(''); setDiscoveredModels([])
                  }}>
                  <option value="native">{t('admin.machines.piProvider')}</option>
                  <option value="direct">{t('admin.machines.directServer')}</option>
                </Select>
              </div>
            )}
            {agentEngine === 'pi-cli' && (
              <>
                <div className="space-y-2">
                  <Label htmlFor="pi-provider">{t('admin.machines.providerRequired')}</Label>
                  <Input id="pi-provider" value={agentProvider} onChange={e => setAgentProvider(e.target.value)}
                    placeholder={t('admin.machines.providerPlaceholder')} maxLength={64} required aria-invalid={!!agentProvider && !validPiProvider} />
                  <p className="text-xs text-[var(--color-foreground-muted)]">
                    {endpointActive
                      ? t('admin.machines.directProviderHint')
                      : t('admin.machines.providerHint')}
                  </p>
                </div>
                <div className="space-y-2">
                  <Label htmlFor="pi-model">{t('admin.machines.modelRequired')}</Label>
                  <Input id="pi-model" value={agentModel} onChange={e => changeModel(e.target.value)}
                    list="pi-models" placeholder={endpointActive ? t('admin.machines.endpointModelPlaceholder') : t('admin.machines.providerModelPlaceholder')} />
                  <datalist id="pi-models">
                    {discoveredModels.map(m => <option key={`endpoint-${m.id}`} value={m.id}>{m.max_model_len ? `${m.id} (${m.max_model_len.toLocaleString()} tokens)` : m.id}</option>)}
                    {!endpointActive && agentCatalog?.models.map(m => <option key={m.id} value={m.id}>{m.label}</option>)}
                  </datalist>
                  {!endpointActive && <p className="text-xs text-[var(--color-foreground-muted)]">{t('admin.machines.providerKeyHint')}</p>}
                </div>
              </>
            )}
            {agentEngine !== 'pi-cli' && !endpointActive && agentCatalog && agentCatalog.models.length > 0 && (
              <div className="space-y-2">
                <Label htmlFor="create-agent-model">{t('admin.machines.model')}</Label>
                <Select id="create-agent-model" value={agentModel} onChange={e => changeModel(e.target.value)} className={selectCSS}>
                  {agentCatalog.default_model && (
                    <option value="">{t('admin.machines.defaultModel', { model: agentCatalog.default_model })}</option>
                  )}
                  {(() => {
                    const builtins = agentCatalog.models.filter(m => m.source !== 'gateway')
                    const gateway = agentCatalog.models.filter(m => m.source === 'gateway')
                    if (gateway.length === 0) {
                      return agentCatalog.models.map(m => (
                        <option key={m.id} value={m.id}>{m.label}</option>
                      ))
                    }
                    return (
                      <>
                        {builtins.length > 0 && (
                          <optgroup label={t('admin.machines.builtIn')}>
                            {builtins.map(m => (
                              <option key={m.id} value={m.id}>{m.label}</option>
                            ))}
                          </optgroup>
                        )}
                        <optgroup label="LLM Gateway">
                          {gateway.map(m => (
                            <option key={m.id} value={m.id}>{m.label}</option>
                          ))}
                        </optgroup>
                      </>
                    )
                  })()}
                </Select>
              </div>
            )}
            {endpointActive && (
              <CreateAgentEndpointSection
                engine={agentEngine}
                draft={endpointDraft}
                onChange={next => {
                  if (next.baseUrl !== endpointDraft.baseUrl || next.auth !== endpointDraft.auth || next.apiKey !== endpointDraft.apiKey) setDiscoveredModels([])
                  setEndpointDraft(next)
                }}
                onModelsLoaded={models => {
                  setDiscoveredModels(models)
                  if (models.length === 1) setAgentModel(current => current || models[0].id)
                }}
                selectClassName={selectCSS}
              />
            )}
            {agentReasoningLevels.length > 0 && (
              <div className="space-y-2">
                <Label htmlFor="create-agent-reasoning">{t('admin.machines.reasoningEffort')}</Label>
                <Select id="create-agent-reasoning" value={agentReasoning} onChange={e => setAgentReasoning(e.target.value)} className={selectCSS}>
                  <option value="">{t('admin.machines.default')}</option>
                  {agentReasoningLevels.map(level => (
                    <option key={level} value={level}>
                      {reasoningLabel(level)}
                    </option>
                  ))}
                </Select>
              </div>
            )}

          </fieldset>
          <fieldset disabled={creating || !!createdAgent} className="min-w-0 space-y-4 border-t border-[var(--color-border)] disabled:opacity-60">
            <legend className="flex items-center gap-2 pr-2 text-sm font-semibold"><Shield className="h-4 w-4" />{t('agentSetup.access')}</legend>
            <div className="space-y-2">
              <Label htmlFor="create-agent-permission">{t('admin.overview.permissionTier')}</Label>
              <Select id="create-agent-permission" value={permission} onChange={e => setPermission(e.target.value as typeof permission)} className={selectCSS}>
                <option value="restricted">{t('admin.overview.permissionRestricted')}</option>
                <option value="standard">{agentEngine === 'pi-cli' ? t('agentSetup.piStandardPermission') : t('admin.overview.permissionStandard')}</option>
                <option value="trusted">{t('admin.overview.permissionTrusted')}</option>
              </Select>
              {permission === 'trusted' && <p className="text-xs text-[var(--color-warning)]">{t('admin.overview.trustedWarning')}</p>}
              <p className="text-xs text-[var(--color-foreground-muted)]">{t('agentSetup.permissionHint')}</p>
              {agentEngine === 'pi-cli' && <p className="text-xs text-[var(--color-foreground-muted)]">{t('agentSetup.piPermissionHint')}</p>}
            </div>
            <div className="space-y-2">
              <p id="create-agent-rooms-label" className="text-sm font-medium">{t('agentSetup.channelsRooms')}</p>
              {roomsStatus === 'error' ? <div className="flex flex-wrap items-center gap-2"><p role="alert" className="text-sm text-[var(--color-destructive)]">{t('agentSetup.roomsLoadFailed')}</p>{onRetryRooms && <Button variant="outline" size="sm" onClick={() => void onRetryRooms()}>{t('common.retry')}</Button>}</div>
                : roomsStatus === 'idle' || roomsStatus === 'loading' ? <p role="status" className="text-sm text-[var(--color-foreground-muted)]">{t('admin.rooms.loading')}</p>
                  : selectableRooms.length > 0 ? (
                <div role="group" aria-labelledby="create-agent-rooms-label" className="max-h-48 overflow-y-auto rounded-[var(--radius-md)] border border-[var(--color-border)]">
                  {projects.map(project => {
                    const rooms = (roomsByProject[project.id] ?? []).filter(room => !room.is_dm)
                    if (!rooms.length) return null
                    return <div key={project.id} className="py-1">
                      <p className="px-3 py-2 text-xs font-medium text-[var(--color-foreground-muted)]">{project.name}</p>
                      {rooms.map(room => <label key={room.id} className="flex min-h-11 cursor-pointer items-center gap-3 px-3 py-2 text-sm hover:bg-[var(--color-surface-alt)] sm:min-h-9">
                        <input type="checkbox" className="h-4 w-4 accent-[var(--color-brand)]" checked={agentRooms.has(room.id)} onChange={() => setAgentRooms(previous => {
                          const next = new Set(previous)
                          if (next.has(room.id)) next.delete(room.id); else next.add(room.id)
                          return next
                        })} />
                        <span className="min-w-0 break-words">{room.parent_room_id ? '↳ ' : '# '}{room.name}</span>
                      </label>)}
                    </div>
                  })}
                </div>
              ) : <p className="rounded-[var(--radius-md)] bg-[var(--color-surface-alt)] p-3 text-sm text-[var(--color-foreground-muted)]">{t('agentSetup.noRooms')}</p>}
              <p className="text-xs text-[var(--color-foreground-muted)]">{t('agentSetup.roomHint')}</p>
            </div>
            <div className="flex gap-3 rounded-[var(--radius-md)] bg-[var(--color-surface-alt)] p-3">
              <FolderOpen className="mt-0.5 h-4 w-4 shrink-0 text-[var(--color-foreground-muted)]" />
              <div className="space-y-1"><p className="text-sm font-medium">{t('agentSetup.workspace')}</p><p className="text-xs leading-relaxed text-[var(--color-foreground-muted)]">{t('agentSetup.workspaceHint')}</p></div>
            </div>
            <details className="group rounded-[var(--radius-md)] border border-[var(--color-border)]">
              <summary className="flex min-h-11 cursor-pointer list-none items-center justify-between gap-3 px-3 py-2 text-sm font-medium sm:min-h-9">
                {t('agentSetup.instructions')}<ChevronRight className="h-4 w-4 transition-transform group-open:rotate-90" />
              </summary>
              <div className="space-y-2 border-t border-[var(--color-border)] p-3">
                <Label htmlFor="create-agent-instructions">AGENTS.md</Label>
                <Textarea id="create-agent-instructions" rows={5} value={instructions} onChange={e => setInstructions(e.target.value)} className="font-mono" placeholder={t('agentSetup.instructionsPlaceholder')} />
                <p className="text-xs text-[var(--color-foreground-muted)]">{t('agentSetup.instructionsHint')}</p>
              </div>
            </details>
          </fieldset>
          <p className="flex gap-2 text-xs leading-relaxed text-[var(--color-foreground-muted)]"><History className="mt-0.5 h-4 w-4 shrink-0" />{t('agentSetup.afterCreate')}</p>
        </div>
        <DialogFooter className="shrink-0 border-t border-[var(--color-border)] px-4 py-3 sm:px-6">
          {createError && <p role="alert" className="min-w-0 flex-1 text-sm text-[var(--color-destructive)]">{createError}</p>}
          {createdAgent ? <Button disabled={creating} onClick={() => finish(createdAgent)}>{t('agentSetup.openSettings')}</Button> : <>
            <Button variant="outline" disabled={creating} onClick={() => onOpenChange(false)}>{t('common.cancel')}</Button>
            <Button onClick={() => void handleCreateAgent()} disabled={!ready || creating}>{creating ? t('admin.machines.creating') : t('admin.machines.createAgent')}</Button>
          </>}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
