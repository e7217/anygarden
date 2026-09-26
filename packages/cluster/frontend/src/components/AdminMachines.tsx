import { useState, useEffect, useCallback, useMemo } from 'react'
import { useMachines } from '@/hooks/useMachines'
import { useAgents, type EngineCatalog } from '@/hooks/useAgents'
import { useRooms } from '@/hooks/useRooms'
import type { Machine, RegisterMachineResult } from '@/hooks/useMachines'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter, DialogDescription,
} from '@/components/ui/dialog'
import {
  Plus, Copy, Check, Trash2, RefreshCw, Save as SaveIcon,
  PauseCircle, Server, Bot, Square, Settings, Play,
  DoorOpen, FileCog, History, Loader2, ArrowUpCircle,
} from 'lucide-react'
import { apiFetch } from '@/lib/api'
import { extractEngineVersion, unsupportedEngineVersionWarning } from '@/lib/engineVersion'
import { applyEndpoint, storeEndpointCredential, type DiscoveredModel } from '@/lib/engineEndpoints'
import CreateAgentEndpointSection, {
  emptyEndpointDraft,
  endpointDraftReady,
  type EndpointDraft,
} from '@/components/CreateAgentEndpointSection'
import AgentSettingsDialog from '@/components/AgentSettingsDialog'
import AgentSettingsMenu from '@/components/AgentSettingsMenu'
import { EntityAvatar, type AvatarKind } from '@/components/EntityAvatar'
import PresenceDot from '@/components/PresenceDot'
import { deriveAgentOnline, agentStatusLabel } from '@/lib/agent-liveness'
import { shouldShowFallbackCrashWarning } from '@/lib/admin-agent-warning'
import { useLocale } from '@/i18n/LocaleProvider'
import { useFeedback } from '@/components/feedback/FeedbackProvider'
import type { Agent } from '@/hooks/useAgents'

// ── Types ──────────────────────────────────────────────────────────

interface MachineAgent {
  id: string; name: string; engine: string
  desired_state: string; actual_state: string
  reasoning_effort?: string | null; rooms: string[]
  // Issue #101 — mirrors the new MachineAgentOut avatar fields.
  avatar_kind?: string | null
  avatar_value?: string | null
  // Issue #148 Part 2 — mirrors the new MachineAgentOut flag so the
  // per-row AgentSettingsMenu can render the check-mark toggle.
  context_window_opt_out?: boolean
}

interface MachineEngineInfo {
  engine: string
  version?: string | null
  // #553 — engine lifecycle, merged from machine_engine_status.
  latest_version?: string | null
  update_available?: boolean
  update_status?: string | null
  latest_checked_at?: string | null
}

const ENGINE_LABELS: Record<string, string> = {
  'pi-cli': 'Pi',
  'codex-cli': 'Codex CLI',
  'claude-code': 'Claude Code',
  'gemini-cli': 'Gemini CLI',
  'openai': 'OpenAI API',
  'anthropic': 'Anthropic API',
}

const DEPRECATED_BADGE_CSS =
  'border-[color:color-mix(in_srgb,var(--color-warning)_40%,transparent)] bg-[color:color-mix(in_srgb,var(--color-warning)_8%,transparent)] text-[10px] text-[var(--color-warning)]'

// #553 — small engine status pill, mirroring #546's StatusBadge styling.
function EngineStatusBadge({ info }: { info: MachineEngineInfo }) {
  const { t } = useLocale()
  const pill = 'shrink-0 rounded-full px-2 py-0.5 text-[10px] font-semibold'
  const muted = `${pill} bg-[var(--color-surface-alt)] text-[var(--color-foreground-muted)]`
  const accent = `${pill} bg-[color:color-mix(in_srgb,var(--color-brand)_15%,transparent)] text-[var(--color-brand-text)]`
  if (info.update_status === 'updating') return <span className={accent}>{t('admin.machines.updating')}</span>
  if (info.update_status === 'failed')
    return (
      <span
        className={`${pill} bg-[color:color-mix(in_srgb,var(--color-destructive)_10%,transparent)] text-[var(--color-destructive)]`}
      >
        {t('admin.machines.updateFailed')}
      </span>
    )
  if (info.update_available) return <span className={accent}>{t('admin.machines.updateAvailable')}</span>
  if (info.latest_checked_at) return <span className={muted}>{t('admin.machines.upToDate')}</span>
  return null
}

function statusDot(status: string) {
  if (status === 'online' || status === 'running') return 'bg-[var(--color-success)]'
  if (status === 'draining' || status === 'starting' || status === 'pending') return 'bg-[var(--color-warning)]'
  return 'bg-[var(--color-foreground-subtle)]'
}

// ── Main Component ─────────────────────────────────────────────────

export default function AdminMachines() {
  const { locale, t, formatDate } = useLocale()
  const { confirm: confirmAction, notify } = useFeedback()
  const statusLabel = (status: string) => {
    if (status === 'online') return t('common.online')
    if (status === 'offline') return t('common.offline')
    if (status === 'draining') return t('admin.machines.draining')
    return status
  }
  const agentStateLabel = (status: string) => {
    const labels: Record<string, ReturnType<typeof t>> = {
      unreachable: t('admin.machines.agentState.unreachable'),
      unknown: t('admin.machines.agentState.unknown'),
      running: t('admin.machines.agentState.running'),
      starting: t('admin.machines.agentState.starting'),
      stopping: t('admin.machines.agentState.stopping'),
      stopped: t('admin.machines.agentState.stopped'),
      idle: t('admin.machines.agentState.idle'),
      pending: t('admin.machines.agentState.pending'),
      crashed: t('admin.machines.agentState.crashed'),
      failed: t('admin.machines.agentState.failed'),
    }
    return labels[status] ?? status
  }
  const reasoningLabel = (level: string) => {
    const labels: Record<string, string> = {
      minimal: t('admin.machines.reasoning.minimal'),
      low: t('admin.machines.reasoning.low'),
      medium: t('admin.machines.reasoning.medium'),
      high: t('admin.machines.reasoning.high'),
      xhigh: t('admin.machines.reasoning.xhigh'),
      max: t('admin.machines.reasoning.max'),
      ultra: t('admin.machines.reasoning.ultra'),
    }
    return labels[level] ?? level
  }
  const activityLabel = (eventType: string) => {
    const labels: Record<string, string> = {
      online: t('admin.machines.activity.online'),
      offline: t('admin.machines.activity.offline'),
      drain: t('admin.machines.activity.drain'),
      self_update: t('admin.machines.activity.selfUpdate'),
      engine_update: t('admin.machines.activity.engineUpdate'),
    }
    return labels[eventType] ?? eventType
  }
  const { machines, drainMachine, registerMachine, deleteMachine, updateMachine, updateMachineDaemon, checkMachineEngine, updateMachineEngine, regenerateToken } = useMachines()
  const {
    createAgent, fetchEngineCatalog, agents, startAgent, stopAgent,
    pendingIds,
    deleteAgent, updateAgent, fetchAgentFiles, upsertAgentFile, deleteAgentFile,
    fetchAttachedSkills, fetchSkillPreview,
    availableEngines,
  } = useAgents()
  const { projects, rooms: roomsByProject, fetchAgentDMs } = useRooms()

  // ``selectedId`` can be either a real machine id or the sentinel
  // ``UNPLACED`` meaning "show agents that aren't placed on any
  // machine". Centralising as a single constant keeps the string
  // from leaking into a dozen equality checks.
  const UNPLACED = '__unplaced__'
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const selectedMachine = machines.find(m => m.id === selectedId) ?? null
  const isUnplacedView = selectedId === UNPLACED

  // Agents without a placed_on_machine_id — created but never
  // successfully scheduled (or detached after a stop). Filtered from
  // the cluster-wide list so any CRUD on them reacts to it. Stopped
  // included so delete/retry are reachable.
  const unplacedAgents = useMemo(
    () => agents.filter(a => !a.placed_on_machine_id),
    [agents],
  )

  // Auto-select first machine (or unplaced if it's the only thing
  // with content and there are no machines)
  useEffect(() => {
    if (selectedId) return
    if (machines.length > 0) setSelectedId(machines[0].id)
    else if (unplacedAgents.length > 0) setSelectedId(UNPLACED)
  }, [machines, selectedId, unplacedAgents.length])

  // ── Detail data ──────────────────────────────────────────────────
  const [machineAgents, setMachineAgents] = useState<MachineAgent[]>([])
  const [machineEngines, setMachineEngines] = useState<MachineEngineInfo[]>([])
  const [machineActivity, setMachineActivity] = useState<{ id: string; event_type: string; timestamp: string; details: Record<string, unknown> | null }[]>([])

  const fetchDetail = useCallback(async (id: string) => {
    const [agentsResp, enginesResp, activityResp] = await Promise.all([
      apiFetch(`/api/v1/machines/${id}/agents`),
      apiFetch(`/api/v1/machines/${id}/engines`),
      apiFetch(`/api/v1/machines/${id}/activity?limit=50`),
    ])
    if (agentsResp.ok) setMachineAgents(await agentsResp.json())
    if (enginesResp.ok) setMachineEngines(await enginesResp.json())
    if (activityResp.ok) setMachineActivity(await activityResp.json())
  }, [])

  // #553 — engine check/update in flight (per engine key), disables its row.
  const [engineBusy, setEngineBusy] = useState<string | null>(null)

  const handleCheckEngine = useCallback(async (engine: string) => {
    if (!selectedId) return
    setEngineBusy(engine)
    try {
      await checkMachineEngine(selectedId, engine)
      // Result arrives over WS; re-fetch shortly after to pick it up.
      setTimeout(() => { if (selectedId) fetchDetail(selectedId) }, 1500)
    } catch {
      /* disabled state already conveys failure; refresh reconciles */
    } finally {
      setEngineBusy(null)
    }
  }, [selectedId, checkMachineEngine, fetchDetail])

  const handleUpdateEngine = useCallback(async (engine: string) => {
    if (!selectedId) return
    setEngineBusy(engine)
    try {
      await updateMachineEngine(selectedId, engine)
      await fetchDetail(selectedId)  // reflect "updating" immediately
      setTimeout(() => { if (selectedId) fetchDetail(selectedId) }, 3000)
    } catch {
      /* status stays as-is */
    } finally {
      setEngineBusy(null)
    }
  }, [selectedId, updateMachineEngine, fetchDetail])

  useEffect(() => {
    if (selectedId) fetchDetail(selectedId)
  }, [selectedId, fetchDetail])

  // #219 — while any agent on the selected machine is mid-transition
  // the top-level ``useAgents`` hook re-polls ``/api/v1/agents`` every
  // ~1.5 s. The machine detail payload comes from a separate endpoint
  // though (``/api/v1/machines/<id>/agents``), so mirror the refresh
  // here so the detail list's badge keeps pace with the global list.
  const selectedAgentStates = useMemo(() => {
    if (!selectedId || selectedId === UNPLACED) return ''
    return agents
      .filter(a => a.placed_on_machine_id === selectedId)
      .map(a => `${a.id}:${a.actual_state}`)
      .sort()
      .join(',')
  }, [agents, selectedId])
  useEffect(() => {
    if (selectedId && selectedId !== UNPLACED) {
      fetchDetail(selectedId)
    }
    // selectedAgentStates is a dependency — intentionally drives the
    // mirrored refetch.
  }, [selectedAgentStates, selectedId, fetchDetail])

  // Agent count per machine — only running/starting agents count toward capacity
  const agentCountByMachine = new Map<string, number>()
  for (const a of agents) {
    if (a.placed_on_machine_id && (a.actual_state === 'running' || a.actual_state === 'starting' || a.actual_state === 'pending')) {
      agentCountByMachine.set(a.placed_on_machine_id, (agentCountByMachine.get(a.placed_on_machine_id) ?? 0) + 1)
    }
  }

  // ── Register Machine ─────────────────────────────────────────────
  const [registerOpen, setRegisterOpen] = useState(false)
  const [regName, setRegName] = useState('')
  // #523 — hostname is now daemon-detected; the user field is a free-form
  // description (alias / note), and it's optional.
  const [regDescription, setRegDescription] = useState('')
  const [regLoading, setRegLoading] = useState(false)
  const [tokenResult, setTokenResult] = useState<RegisterMachineResult | null>(null)
  const [tokenDialogOpen, setTokenDialogOpen] = useState(false)
  const [copied, setCopied] = useState(false)

  const handleRegister = async () => {
    if (!regName.trim()) return
    setRegLoading(true)
    try {
      const result = await registerMachine({
        name: regName.trim(),
        description: regDescription.trim() || undefined,
      })
      setTokenResult(result)
      setRegName(''); setRegDescription('')
      setRegisterOpen(false)
      setTokenDialogOpen(true)
    } catch { /* ignore */ }
    setRegLoading(false)
  }

  // ── Create Agent on Machine ──────────────────────────────────────
  const [createAgentOpen, setCreateAgentOpen] = useState(false)
  const [agentName, setAgentName] = useState('')
  const [agentEngine, setAgentEngine] = useState('')
  const [agentReasoning, setAgentReasoning] = useState('')
  const [agentModel, setAgentModel] = useState('')
  const [agentProvider, setAgentProvider] = useState('')
  const validPiProvider = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/.test(agentProvider)
  const [agentCatalog, setAgentCatalog] = useState<EngineCatalog | null>(null)
  // #685 — optional direct endpoint (local / custom OpenAI-compatible server).
  const [endpointDraft, setEndpointDraft] = useState<EndpointDraft>(() => emptyEndpointDraft(''))
  const [discoveredModels, setDiscoveredModels] = useState<DiscoveredModel[]>([])
  const endpointActive = agentEngine === 'pi-cli' && endpointDraft.enabled
  const providerRequired = agentEngine === 'pi-cli'
  const [agentRooms, setAgentRooms] = useState<Set<string>>(new Set())
  const [creating, setCreating] = useState(false)
  const [createError, setCreateError] = useState<string | null>(null)

  const engineMetadataById = useMemo(() => {
    const map = new Map<string, (typeof availableEngines)[number]>()
    for (const engine of availableEngines) map.set(engine.engine, engine)
    return map
  }, [availableEngines])

  const sortedMachineEngines = useMemo(
    () =>
      machineEngines
        .map((info, index) => ({ info, index }))
        .sort((a, b) => {
          const aDeprecated =
            engineMetadataById.get(a.info.engine)?.deprecated === true
          const bDeprecated =
            engineMetadataById.get(b.info.engine)?.deprecated === true
          if (aDeprecated !== bDeprecated) return aDeprecated ? 1 : -1
          return a.index - b.index
        })
        .map(item => item.info),
    [engineMetadataById, machineEngines],
  )

  const preferredMachineEngine = sortedMachineEngines[0]?.engine ?? ''
  // #687 — warn before creation for engines that publish an exact version gate.
  // Codex publishes an empty list because it attempts unlisted versions.
  const rawAgentEngineVersionWarning = agentEngine && agentCatalog?.engine === agentEngine
    ? unsupportedEngineVersionWarning(
        agentEngine,
        machineEngines.find(e => e.engine === agentEngine)?.version,
        agentCatalog.supported_versions,
      )
    : null
  const detectedEngineVersion = extractEngineVersion(machineEngines.find(e => e.engine === agentEngine)?.version)
  const requiredVersions = agentCatalog?.supported_versions ?? []
  const agentEngineVersionWarning = rawAgentEngineVersionWarning && locale === 'ko' && detectedEngineVersion
    ? t('admin.machines.versionWarning', {
        engine: agentEngine,
        version: detectedEngineVersion,
        required: requiredVersions.length === 1
          ? requiredVersions[0]
          : t('admin.machines.oneOfVersions', { versions: requiredVersions.join(', ') }),
      })
    : rawAgentEngineVersionWarning
  const selectedAgentEngineMeta = agentEngine
    ? engineMetadataById.get(agentEngine)
    : undefined

  // Keep the model/reasoning catalog in sync with the selected engine.
  // Resetting model + reasoning on every change prevents a stale
  // selection from a previous engine (e.g. codex "xhigh") leaking
  // into a different one (gemini).
  useEffect(() => {
    setAgentProvider('')
    setEndpointDraft(emptyEndpointDraft(agentEngine))
    setDiscoveredModels([])
    if (!agentEngine) {
      setAgentCatalog(null)
      setAgentModel('')
      setAgentReasoning('')
      return
    }
    let cancelled = false
    setAgentModel('')
    setAgentReasoning('')
    fetchEngineCatalog(agentEngine).then(cat => {
      if (!cancelled) setAgentCatalog(cat)
    })
    return () => { cancelled = true }
  }, [agentEngine, fetchEngineCatalog])

  const agentReasoningLevels = useMemo(() => {
    if (!agentCatalog) return []
    if (agentModel) {
      const m = agentCatalog.models.find(x => x.id === agentModel)
      if (m && m.reasoning_levels.length > 0) return m.reasoning_levels
    }
    return agentCatalog.reasoning_levels
  }, [agentCatalog, agentModel])

  // #158 — collapsed into a single AgentSettingsDialog. The
  // machine-detail agent list carries a stripped-down shape
  // (MachineAgent) that misses fields the dialog needs (agents_md,
  // model, restart_policy, etc.), so we look up the full record from
  // the cluster-wide ``agents`` list when opening settings.
  //
  // #281 — keep only the agent ID and derive the Agent object from
  // the live ``agents`` list every render. The earlier
  // ``useState<Agent | null>`` snapshot left the dialog showing stale
  // model/reasoning/collaboration values after an in-dialog edit
  // (the edit triggers ``updateAgent → fetchAgents`` which refreshes
  // the list, but a snapshot doesn't track that). See
  // AgentSettingsDialog.test.tsx — "parent state pattern (#281)".
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [settingsAgentId, setSettingsAgentId] = useState<string | null>(null)
  const settingsAgent = useMemo<Agent | null>(
    () =>
      settingsAgentId
        ? agents.find(a => a.id === settingsAgentId) ?? null
        : null,
    [agents, settingsAgentId],
  )

  const handleOpenSettings = (agentId: string) => {
    if (!agents.some(a => a.id === agentId)) return
    setSettingsAgentId(agentId)
    setSettingsOpen(true)
  }

  const handleDeleteAgent = async (agentId: string): Promise<boolean> => {
    if (!await confirmAction({ title: t('admin.machines.deleteAgentTitle'), description: t('admin.machines.confirmDeleteAgent'), destructive: true })) return false
    await deleteAgent(agentId)
    // Delete cascades the DM room (see PR #12) — refresh both the
    // per-machine detail and the sidebar DM list so the ghost entry
    // doesn't linger until a manual reload.
    if (selectedId && selectedId !== UNPLACED) fetchDetail(selectedId)
    fetchAgentDMs()
    return true
  }

  // #148 Part 2 — flip the agent-side ambient opt-out. We read the
  // current value off the MachineAgent row the menu renders, flip
  // it, and re-fetch the detail so the check mark reflects truth.
  // ``_set`` is always true on this code path — the caller chose to
  // toggle, so "omit = keep previous" never applies here.
  const handleToggleContextWindowOptOut = async (
    agentId: string,
    current: boolean,
  ) => {
    try {
      await updateAgent(agentId, {
        context_window_opt_out: !current,
        context_window_opt_out_set: true,
      })
      if (selectedId && selectedId !== UNPLACED) fetchDetail(selectedId)
    } catch {
      // Swallow — the top-of-page error banner pattern used by the
      // rest of this file owns fatal surfacing. A transient toggle
      // failure is fine to retry via the next click.
    }
  }

  const handleCreateAgent = async () => {
    if (!agentName.trim() || !agentEngine || !selectedId) return
    if (providerRequired && !validPiProvider) return
    if (agentEngine === 'pi-cli' && !endpointActive && !agentModel.trim()) return
    if (endpointActive && !endpointDraftReady(endpointDraft, agentModel)) return
    setCreateError(null)
    setCreating(true)
    const endpoint = endpointActive ? endpointDraft : null
    const model = agentModel.trim()
    try {
      // #685 — the (secret-free) endpoint is persisted with the agent in one
      // request; an API key goes through the write-only credential endpoint.
      const created = await createAgent({
        name: agentName.trim(),
        engine: agentEngine,
        ...(providerRequired ? { provider: agentProvider } : {}),
        rooms: Array.from(agentRooms),
        ...(agentReasoning ? { reasoning_effort: agentReasoning } : {}),
        ...(model ? { model } : {}),
        ...(endpoint ? { endpoint: { base_url: endpoint.baseUrl, api_protocol: endpoint.protocol } } : {}),
      })
      setAgentName(''); setAgentEngine(''); setAgentReasoning('')
      setAgentModel(''); setAgentCatalog(null); setAgentRooms(new Set())
      setEndpointDraft(emptyEndpointDraft('')); setDiscoveredModels([])
      fetchDetail(selectedId)
      // create_agent auto-creates a DM room server-side; the sidebar
      // caches DMs separately so nudge it to refetch otherwise the
      // new agent only appears after a full page reload.
      fetchAgentDMs()
      if (endpoint?.auth === 'key') {
        try {
          const credentialRef = await storeEndpointCredential(created.id, endpoint.apiKey)
          await applyEndpoint(created.id, {
            provider: agentProvider, model, base_url: endpoint.baseUrl,
            api_protocol: endpoint.protocol, credential_ref: credentialRef,
          })
        } catch (e) {
          // The agent exists; keep the dialog open to explain, but the form
          // is already reset so a second click cannot create a duplicate.
          setCreateError(t('admin.machines.agentKeyError', { error: e instanceof Error ? e.message : String(e) }))
          setCreating(false)
          return
        }
      }
      setCreateAgentOpen(false)
    } catch (e) {
      setCreateError(e instanceof Error ? e.message : String(e))
    }
    setCreating(false)
  }

  // ── Token / Control ──────────────────────────────────────────────
  const [regenToken, setRegenToken] = useState<string | null>(null)
  const [regenCopied, setRegenCopied] = useState(false)

  const selectCSS = "flex h-9 w-full rounded-[var(--radius-xs)] border border-[var(--color-border-strong)] bg-[var(--color-background)] px-3 py-1 text-sm text-[var(--color-foreground)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-brand-focus)]"

  // ── Render ───────────────────────────────────────────────────────

  return (
    <div className="flex min-h-full min-w-0 flex-col lg:h-full lg:flex-row">
      {/* ── Left: Machine Card List ── */}
      <div className="w-full min-w-0 shrink-0 border-b border-[var(--color-border)] bg-[var(--color-background)] lg:w-64 lg:overflow-y-auto lg:border-r lg:border-b-0">
        <div className="flex items-center justify-between gap-3 border-b border-[var(--color-border)] px-4 py-3 lg:flex-col lg:items-stretch">
          <h1 className="text-heading min-w-0 text-[var(--color-foreground)]">{t('admin.machines.title')}</h1>
          <Button size="sm" className="min-h-11 gap-2 lg:w-full" onClick={() => setRegisterOpen(true)} aria-label={t('admin.machines.registerMachine')}>
            <Plus className="h-4 w-4" />
            <span>{t('admin.machines.registerMachine')}</span>
          </Button>
        </div>
        <div className="flex min-w-0 gap-2 overflow-x-auto p-3 lg:block lg:space-y-2 lg:overflow-x-visible lg:p-2">
          {machines.length === 0 ? (
            <div className="w-full px-3 py-8 text-center">
              <Server className="mx-auto h-8 w-8 text-[var(--color-foreground-subtle)] mb-2" />
              <p className="text-xs text-[var(--color-foreground-muted)]">{t('admin.machines.none')}</p>
              <Button variant="ghost" size="sm" className="mt-2" onClick={() => setRegisterOpen(true)}>
                <Plus className="mr-1 h-3 w-3" /> {t('admin.machines.register')}
              </Button>
            </div>
          ) : (
            <>
              {machines.map(m => (
                <button
                  key={m.id}
                  onClick={() => setSelectedId(m.id)}
                  className={`w-48 shrink-0 text-left rounded-[var(--radius-lg)] border px-3 py-2.5 transition-all lg:w-full ${
                    selectedId === m.id
                      ? 'bg-[var(--color-brand-tint-bg)] border-[var(--color-brand)] shadow-[var(--shadow-card)]'
                      : 'bg-[var(--color-surface-elevated)] border-[var(--color-border)] hover:shadow-[var(--shadow-card)]'
                  }`}
                >
                  <div className="text-sm font-medium text-[var(--color-foreground)] truncate">{m.name}</div>
                  {(m.description || m.hostname) && (
                    <div className="text-xs text-[var(--color-foreground-muted)] truncate">{m.description || m.hostname}</div>
                  )}
                  <div className="flex items-center gap-2 mt-1.5">
                    <span className="flex items-center gap-1 text-xs text-[var(--color-foreground-muted)]">
                      <span className={`inline-block h-1.5 w-1.5 rounded-full ${statusDot(m.status)}`} />
                      {statusLabel(m.status)}
                    </span>
                    <span className="text-xs text-[var(--color-foreground-subtle)]">
                      {t('admin.machines.agentCount', { count: agentCountByMachine.get(m.id) ?? 0 })}
                    </span>
                  </div>
                </button>
              ))}
              {unplacedAgents.length > 0 && (
                <button
                  onClick={() => setSelectedId(UNPLACED)}
                  className={`w-48 shrink-0 text-left rounded-[var(--radius-lg)] border border-dashed px-3 py-2.5 transition-all lg:w-full ${
                    isUnplacedView
                      ? 'bg-[color:color-mix(in_srgb,var(--color-warning)_8%,transparent)] border-[var(--color-warning)] shadow-[var(--shadow-card)]'
                      : 'bg-[var(--color-surface-elevated)] border-[var(--color-border)] hover:shadow-[var(--shadow-card)]'
                  }`}
                >
                  <div className="text-sm font-medium text-[var(--color-foreground)] truncate">{t('admin.machines.unplaced')}</div>
                  <div className="text-xs text-[var(--color-foreground-muted)] truncate">{t('admin.machines.unplacedSubtitle')}</div>
                  <div className="flex items-center gap-2 mt-1.5">
                    <span className="text-xs text-[var(--color-foreground-subtle)]">
                      {t('admin.machines.agentCount', { count: unplacedAgents.length })}
                    </span>
                  </div>
                </button>
              )}
            </>
          )}
        </div>
      </div>

      {/* ── Right: Machine Detail ── */}
      <div className="min-w-0 flex-1 p-4 sm:p-6 lg:overflow-y-auto">
        {isUnplacedView ? (
          <div className="max-w-2xl space-y-6">
            <div>
              <h2 className="text-lead text-[var(--color-foreground)]">{t('admin.machines.unplacedTitle')}</h2>
              <p className="text-sm text-[var(--color-foreground-muted)]">
                {t('admin.machines.unplacedDescription')}
              </p>
            </div>
            <div className="rounded-[var(--radius-lg)] border border-[var(--color-border)] bg-[var(--color-surface-elevated)] shadow-[var(--shadow-card)] divide-y divide-[var(--color-border)]">
              {unplacedAgents.length === 0 ? (
                <div className="px-4 py-8 text-center">
                  <Bot className="mx-auto h-8 w-8 text-[var(--color-foreground-subtle)] mb-2" />
                  <p className="text-sm text-[var(--color-foreground-muted)]">{t('admin.machines.noUnplaced')}</p>
                </div>
              ) : unplacedAgents.map(agent => (
                <div key={agent.id} className="flex items-center justify-between px-4 py-3 gap-3">
                  <div className="flex items-center gap-3 min-w-0">
                    <EntityAvatar
                      id={agent.id}
                      name={agent.name}
                      kind="agent"
                      engine={agent.engine}
                      size="md"
                      avatarKind={
                        (agent.avatar_kind as AvatarKind | null | undefined) ?? null
                      }
                      avatarValue={agent.avatar_value ?? null}
                      data-testid={`admin-agent-avatar-${agent.id}`}
                    />
                    <div className="min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="text-sm font-medium text-[var(--color-foreground)] truncate">{agent.name}</span>
                        <span className="text-xs text-[var(--color-foreground-muted)]">· {agentStateLabel(agent.actual_state)}</span>
                      </div>
                      <div className="flex items-center gap-2 mt-0.5 text-xs text-[var(--color-foreground-subtle)]">
                        <span>{ENGINE_LABELS[agent.engine] ?? agent.engine}</span>
                        {/* #516 — the structured unavailability reason is the
                            authoritative "why". Show its first line inline and
                            the full admin message (may include stderr) on hover;
                            fall back to the raw last_crash_reason. */}
                        {agent.unavailable_reason ? (
                          <span
                            className="truncate text-[var(--color-warning)]"
                            title={agent.unavailable_reason.message}
                          >
                            · {agent.unavailable_reason.message.split('\n')[0]}
                          </span>
                        ) : shouldShowFallbackCrashWarning(agent) ? (
                          <span
                            className="truncate text-[var(--color-warning)]"
                            title={agent.last_crash_reason ?? undefined}
                          >
                            · {agent.last_crash_reason}
                          </span>
                        ) : null}
                      </div>
                    </div>
                  </div>
                  <div className="flex items-center gap-1">
                    {/* Retry placement stays inline — it's the primary
                        affordance on an unplaced agent and a hidden
                        menu entry would bury it. */}
                    <Button
                      variant="ghost"
                      size="icon"
                      onClick={async () => { try { await startAgent(agent.id) } catch { /* ignore */ } }}
                      title={t('admin.machines.retryPlacement')}
                    >
                      <Play className="h-3.5 w-3.5 text-[var(--color-success)]" />
                    </Button>
                    <AgentSettingsMenu
                      onOpenSettings={() => handleOpenSettings(agent.id)}
                      onDelete={() => handleDeleteAgent(agent.id)}
                      contextWindowOptOut={
                        agents.find(a => a.id === agent.id)
                          ?.context_window_opt_out ?? false
                      }
                      onToggleContextWindowOptOut={() =>
                        handleToggleContextWindowOptOut(
                          agent.id,
                          agents.find(a => a.id === agent.id)
                            ?.context_window_opt_out ?? false,
                        )
                      }
                    />
                  </div>
                </div>
              ))}
            </div>
          </div>
        ) : !selectedMachine ? (
          <div className="flex h-full items-center justify-center">
            <p className="text-sm text-[var(--color-foreground-muted)]">{t('admin.machines.select')}</p>
          </div>
        ) : (
          <div className="max-w-2xl space-y-6">
            {/* Header */}
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div className="min-w-0 flex-1">
                <h2 className="text-lead break-words text-[var(--color-foreground)]">{selectedMachine.name}</h2>
                {(selectedMachine.description || selectedMachine.hostname) && (
                  <p className="text-sm text-[var(--color-foreground-muted)]">{selectedMachine.description || selectedMachine.hostname}</p>
                )}
              </div>
              <Badge variant="outline" className={`${
                selectedMachine.status === 'online'
                  ? 'bg-[color:color-mix(in_srgb,var(--color-success)_10%,transparent)] text-[var(--color-success)] border-[color:color-mix(in_srgb,var(--color-success)_25%,transparent)]'
                  : 'bg-[var(--color-surface-alt)] text-[var(--color-foreground-muted)] border-[var(--color-border)]'
              }`}>
                {statusLabel(selectedMachine.status)}
              </Badge>
            </div>

            {/* Info */}
            <div className="rounded-[var(--radius-lg)] border border-[var(--color-border)] bg-[var(--color-surface-elevated)] shadow-[var(--shadow-card)]">
              <div className="px-4 py-2.5 border-b border-[var(--color-border)]">
                <h3 className="text-xs font-semibold uppercase tracking-wider text-[var(--color-foreground-muted)]">{t('admin.machines.info')}</h3>
              </div>
              <div className="grid grid-cols-1 gap-x-8 gap-y-2 px-4 py-3 text-sm sm:grid-cols-2">
                <div>
                  <span className="text-[var(--color-foreground-muted)]">{t('admin.machines.hostname')}</span>
                  <p className="text-[var(--color-foreground)] font-medium break-all">{selectedMachine.hostname || '—'}</p>
                </div>
                <div>
                  <span className="text-[var(--color-foreground-muted)]">{t('admin.machines.ipAddress')}</span>
                  <p className="text-[var(--color-foreground)] font-medium">{selectedMachine.lan_ip || '—'}</p>
                </div>
                <div>
                  <span className="text-[var(--color-foreground-muted)]">OS</span>
                  <p className="text-[var(--color-foreground)] font-medium break-words">{selectedMachine.os_platform || '—'}</p>
                </div>
                <div>
                  <span className="text-[var(--color-foreground-muted)]">CPU</span>
                  <p className="text-[var(--color-foreground)] font-medium">{selectedMachine.cpu_cores ? t('admin.machines.cpuCores', { count: selectedMachine.cpu_cores }) : '—'}</p>
                </div>
                <div>
                  <span className="text-[var(--color-foreground-muted)]">{t('admin.machines.memory')}</span>
                  <p className="text-[var(--color-foreground)] font-medium">{selectedMachine.memory_gb ? `${selectedMachine.memory_gb} GB` : '—'}</p>
                </div>
                <div>
                  <span className="text-[var(--color-foreground-muted)]">{t('admin.machines.version')}</span>
                  <p className="text-[var(--color-foreground)] font-medium">
                    {selectedMachine.daemon_version || '-'}
                    {selectedMachine.update_status === 'updating' && (
                      <span className="ml-2 text-xs text-[var(--color-foreground-muted)]">{t('admin.machines.updating')}</span>
                    )}
                    {selectedMachine.update_status === 'success' && (
                      <span className="ml-2 text-xs text-[var(--color-brand-text)]">{t('admin.machines.updated')}</span>
                    )}
                    {selectedMachine.update_status === 'failed' && (
                      <span
                        className="ml-2 text-xs text-[var(--color-destructive)]"
                        title={selectedMachine.update_error || undefined}
                      >
                        {t('admin.machines.updateFailed')}
                      </span>
                    )}
                  </p>
                </div>
                <div>
                  <span className="text-[var(--color-foreground-muted)]">{t('admin.machines.engines')}</span>
                  <div className="flex flex-col gap-1.5 mt-0.5">
                    {sortedMachineEngines.map(e => {
                      const engineMeta = engineMetadataById.get(e.engine)
                      const busy = engineBusy === e.engine
                      const online = selectedMachine?.status === 'online'
                      const btn =
                        'rounded-[var(--radius-sm)] border border-[var(--color-border)] px-1.5 py-0.5 text-[11px] text-[var(--color-foreground-muted)] hover:bg-[var(--color-surface-hover)] disabled:opacity-40 disabled:cursor-not-allowed'
                      return (
                        <div key={e.engine} className="flex items-center gap-1.5 flex-wrap">
                          <Badge variant="outline" className="text-xs">
                            {ENGINE_LABELS[e.engine] ?? e.engine}
                          </Badge>
                          {engineMeta?.deprecated ? (
                            <Badge
                              variant="outline"
                              className={DEPRECATED_BADGE_CSS}
                              title={engineMeta.deprecation_note ?? undefined}
                            >
                              {t('admin.machines.deprecated')}
                            </Badge>
                          ) : null}
                          {e.version && (
                            <span className="font-mono text-[11px] text-[var(--color-foreground-muted)]">
                              {e.version}
                            </span>
                          )}
                          {e.update_available && e.latest_version && (
                            <span className="font-mono text-[11px] text-[var(--color-brand-text)]">
                              → {e.latest_version}
                            </span>
                          )}
                          <EngineStatusBadge info={e} />
                          <button
                            className={btn}
                            onClick={() => handleCheckEngine(e.engine)}
                            disabled={busy || !online}
                            title={online ? t('admin.machines.checkLatest') : t('admin.machines.offlineHint')}
                          >
                            {busy ? '…' : t('admin.machines.check')}
                          </button>
                          {e.update_available && (
                            <button
                              className={`${btn} text-[var(--color-brand-text)] border-[color:color-mix(in_srgb,var(--color-brand)_35%,transparent)] hover:bg-[color:color-mix(in_srgb,var(--color-brand)_15%,transparent)]`}
                              onClick={() => handleUpdateEngine(e.engine)}
                              disabled={busy || !online}
                            >
                              {t('admin.machines.update')}
                            </button>
                          )}
                        </div>
                      )
                    })}
                    {sortedMachineEngines.length === 0 && <span className="text-[var(--color-foreground-subtle)]">-</span>}
                  </div>
                </div>
                <div>
                  <span className="text-[var(--color-foreground-muted)]">{t('admin.machines.activeAgents')}</span>
                  <p className="text-[var(--color-foreground)] font-medium">
                    {selectedMachine.status === 'offline'
                      ? <span className="text-[var(--color-foreground-subtle)]">{t('admin.machines.unknownOffline')}</span>
                      : machineAgents.filter(a => a.actual_state === 'running' || a.actual_state === 'starting' || a.actual_state === 'pending').length}
                  </p>
                </div>
              </div>
            </div>

            {/* Agents */}
            <div className="rounded-[var(--radius-lg)] border border-[var(--color-border)] bg-[var(--color-surface-elevated)] shadow-[var(--shadow-card)]">
              <div className="flex items-center justify-between px-4 py-2.5 border-b border-[var(--color-border)]">
                <h3 className="text-xs font-semibold uppercase tracking-wider text-[var(--color-foreground-muted)]">
                  {t('admin.machines.agentsHeading', { count: machineAgents.length })}
                </h3>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => {
                    setCreateAgentOpen(true)
                    if (preferredMachineEngine && !agentEngine) {
                      setAgentEngine(preferredMachineEngine)
                    }
                  }}
                  disabled={selectedMachine.status !== 'online'}
                >
                  <Plus className="mr-1 h-3.5 w-3.5" /> {t('admin.machines.newAgent')}
                </Button>
              </div>
              <div className="divide-y divide-[var(--color-border)]">
                {machineAgents.length === 0 ? (
                  <div className="px-4 py-8 text-center">
                    <Bot className="mx-auto h-8 w-8 text-[var(--color-foreground-subtle)] mb-2" />
                    <p className="text-sm text-[var(--color-foreground-muted)]">{t('admin.machines.noAgents')}</p>
                  </div>
                ) : machineAgents.map(agent => {
                  // When the hosting machine's WS is disconnected we
                  // have no way to know the agent's real state — the
                  // DB still shows whatever was last reported, which
                  // is misleading (e.g. "running" on an offline box).
                  // Surface the uncertainty as a derived
                  // "unreachable" display without touching the
                  // underlying actual_state in the DB; once the
                  // machine reconnects the daemon's reports will
                  // reconcile state naturally.
                  //
                  // #71: delegated to the shared ``agent-liveness``
                  // helpers so the sidebar, dialogs, and this page
                  // all derive liveness the same way.
                  const isMachineOffline = selectedMachine.status === 'offline'
                  const online = deriveAgentOnline(agent.actual_state, { machineOffline: isMachineOffline })
                  const displayState = agentStateLabel(agentStatusLabel(agent.actual_state, { machineOffline: isMachineOffline }))
                  const isStopped = agent.actual_state === 'stopped' || agent.actual_state === 'idle' || agent.actual_state === 'crashed'
                  const isRunning = agent.actual_state === 'running' || agent.actual_state === 'starting'
                  return (
                    <div key={agent.id} className={`flex items-center justify-between px-4 py-3 gap-3 ${isStopped || isMachineOffline ? 'opacity-50' : ''}`}>
                      <div className="flex items-center gap-3 min-w-0">
                        <EntityAvatar
                          id={agent.id}
                          name={agent.name}
                          kind="agent"
                          engine={agent.engine}
                          size="md"
                          avatarKind={
                            (agent.avatar_kind as AvatarKind | null | undefined) ?? null
                          }
                          avatarValue={agent.avatar_value ?? null}
                          data-testid={`admin-agent-avatar-${agent.id}`}
                        />
                        <div className="min-w-0">
                          <div className="flex items-center gap-2">
                            <span className="text-sm font-medium text-[var(--color-foreground)] truncate">{agent.name}</span>
                            <span className="flex items-center gap-1 text-xs text-[var(--color-foreground-muted)]">
                              <PresenceDot
                                variant="agent"
                                online={online}
                                agentState={displayState}
                              />
                              {displayState}
                            </span>
                          </div>
                          <div className="flex items-center gap-2 mt-0.5 text-xs text-[var(--color-foreground-subtle)]">
                            <span>{ENGINE_LABELS[agent.engine] ?? agent.engine}</span>
                            {agent.reasoning_effort && <span>· {reasoningLabel(agent.reasoning_effort)}</span>}
                            {agent.rooms.length > 0 && (
                              <span className="truncate">· {agent.rooms.map(r => `#${r}`).join(', ')}</span>
                            )}
                          </div>
                        </div>
                      </div>
                      <div className="flex items-center gap-1">
                        {/* Start/Stop stays inline — frequent toggle,
                            and Play/Square icons themselves
                            communicate state better than a menu item
                            would. Rest of the admin actions collapsed
                            into AgentSettingsMenu.

                            #219 — while the POST is in flight, swap to
                            a spinner + disabled so the admin gets
                            immediate feedback. The subsequent
                            ``starting``/``stopping`` badge is driven
                            by the hook's transitional poll and the
                            daemon's fast-path report. */}
                        {(() => {
                          const isPending = pendingIds.has(agent.id)
                          if (isPending) {
                            return (
                              <Button
                                variant="ghost"
                                size="icon"
                                disabled
                                title={isRunning ? t('admin.machines.stopping') : t('admin.machines.starting')}
                              >
                                <Loader2 className="h-3.5 w-3.5 animate-spin text-[var(--color-foreground-muted)]" />
                              </Button>
                            )
                          }
                          if (isRunning) {
                            return (
                              <Button
                                variant="ghost"
                                size="icon"
                                onClick={() => { void stopAgent(agent.id) }}
                                title={isMachineOffline ? t('admin.machines.offlineHint') : t('admin.machines.stop')}
                                disabled={isMachineOffline}
                              >
                                <Square className="h-3.5 w-3.5 text-[var(--color-destructive)]" />
                              </Button>
                            )
                          }
                          return (
                            <Button
                              variant="ghost"
                              size="icon"
                              onClick={() => { void startAgent(agent.id) }}
                              title={isMachineOffline ? t('admin.machines.offlineHint') : t('admin.machines.start')}
                              disabled={isMachineOffline}
                            >
                              <Play className="h-3.5 w-3.5 text-[var(--color-success)]" />
                            </Button>
                          )
                        })()}
                        <AgentSettingsMenu
                          onOpenSettings={() => handleOpenSettings(agent.id)}
                          onDelete={() => handleDeleteAgent(agent.id)}
                          contextWindowOptOut={
                            agent.context_window_opt_out ?? false
                          }
                          onToggleContextWindowOptOut={() =>
                            handleToggleContextWindowOptOut(
                              agent.id,
                              agent.context_window_opt_out ?? false,
                            )
                          }
                        />
                      </div>
                    </div>
                  )
                })}
              </div>
            </div>

            {/* Token & Control */}
            <div className="rounded-[var(--radius-lg)] border border-[var(--color-border)] bg-[var(--color-surface-elevated)] shadow-[var(--shadow-card)]">
              <div className="px-4 py-2.5 border-b border-[var(--color-border)]">
                <h3 className="text-xs font-semibold uppercase tracking-wider text-[var(--color-foreground-muted)]">{t('admin.machines.tokenControl')}</h3>
              </div>
              <div className="px-4 py-3 space-y-3">
                {regenToken && (
                  <div className="flex gap-2">
                    <code className="flex-1 font-mono text-xs bg-[var(--color-surface-alt)] rounded-[var(--radius-md)] p-2 border border-[var(--color-border)] break-all">
                      {regenToken}
                    </code>
                    <Button variant="ghost" size="icon" onClick={async () => {
                      await navigator.clipboard.writeText(regenToken)
                      setRegenCopied(true); setTimeout(() => setRegenCopied(false), 2000)
                    }}>
                      {regenCopied ? <Check className="h-4 w-4 text-[var(--color-success)]" /> : <Copy className="h-4 w-4" />}
                    </Button>
                  </div>
                )}
                <div className="flex flex-wrap gap-2">
                  <Button variant="outline" size="sm" onClick={async () => {
                    if (!await confirmAction({ title: t('admin.machines.rotateToken'), description: t('admin.machines.confirmRotate') })) return
                    const r = await regenerateToken(selectedMachine.id, false)
                    setRegenToken(r.token)
                  }}>
                    <RefreshCw className="mr-1.5 h-3 w-3" /> {t('admin.machines.rotateToken')}
                  </Button>
                  <Button variant="outline" size="sm"
                    disabled={selectedMachine.status === 'draining' || selectedMachine.status === 'offline'}
                    onClick={async () => {
                      if (!await confirmAction({ title: t('admin.machines.drain'), description: t('admin.machines.confirmDrain') })) return
                      await drainMachine(selectedMachine.id)
                    }}
                  >
                    <PauseCircle className="mr-1.5 h-3 w-3" /> {t('admin.machines.drain')}
                  </Button>
                  <Button variant="outline" size="sm"
                    disabled={selectedMachine.status === 'offline' || selectedMachine.update_status === 'updating'}
                    onClick={async () => {
                      if (!await confirmAction({ title: t('admin.machines.update'), description: t('admin.machines.confirmUpdate') })) return
                      try {
                        await updateMachineDaemon(selectedMachine.id)
                      } catch (e) {
                        notify({ message: t('admin.machines.triggerUpdateFailed', { error: (e as Error).message }), tone: 'error' })
                      }
                    }}
                  >
                    <ArrowUpCircle className="mr-1.5 h-3 w-3" /> {t('admin.machines.update')}
                  </Button>
                  <Button variant="outline" size="sm"
                    className="text-[var(--color-destructive)] hover:text-[var(--color-destructive)] border-[var(--color-destructive)]/30 hover:border-[var(--color-destructive)]/50"
                    onClick={async () => {
                      if (!await confirmAction({ title: t('admin.machines.deleteMachine'), description: t('admin.machines.confirmDelete', { name: selectedMachine.name }), destructive: true })) return
                      try {
                        await deleteMachine(selectedMachine.id, machineAgents.length > 0)
                        setSelectedId(null)
                      } catch (e) {
                        notify({ message: t('admin.machines.deleteFailed', { error: (e as Error).message }), tone: 'error' })
                      }
                    }}
                  >
                    <Trash2 className="mr-1.5 h-3 w-3" /> {t('admin.machines.deleteMachine')}
                  </Button>
                </div>
              </div>
            </div>

            {/* History */}
            <div className="rounded-[var(--radius-lg)] border border-[var(--color-border)] bg-[var(--color-surface-elevated)] shadow-[var(--shadow-card)]">
              <div className="px-4 py-2.5 border-b border-[var(--color-border)]">
                <h3 className="text-xs font-semibold uppercase tracking-wider text-[var(--color-foreground-muted)]">{t('admin.machines.history')}</h3>
              </div>
              <div className="px-4 py-3 max-h-64 overflow-y-auto">
                {machineActivity.length === 0 ? (
                  <p className="text-caption text-[var(--color-foreground-muted)]">{t('admin.machines.noActivity')}</p>
                ) : (
                  <div className="space-y-1.5">
                    {machineActivity.map(evt => (
                      <div key={evt.id} className="flex items-center gap-2 text-xs">
                        <span className={`inline-block h-1.5 w-1.5 rounded-full ${
                          evt.event_type === 'online' ? 'bg-[var(--color-success)]'
                            : evt.event_type === 'offline' ? 'bg-[var(--color-foreground-subtle)]'
                            : 'bg-[var(--color-warning)]'
                        }`} />
                        <span className="font-medium text-[var(--color-foreground)]">{activityLabel(evt.event_type)}</span>
                        <span className="text-[var(--color-foreground-muted)]">
                          {formatDate(new Date(evt.timestamp), { dateStyle: 'medium', timeStyle: 'short' })}
                        </span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>
          </div>
        )}
      </div>

      {/* ── Register Machine Dialog ── */}
      <Dialog open={registerOpen} onOpenChange={setRegisterOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t('admin.machines.registerTitle')}</DialogTitle>
            <DialogDescription>
              {t('admin.machines.registerDescription')}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4 py-2">
            <div className="space-y-2">
              <Label>{t('admin.machines.name')}</Label>
              <Input placeholder={t('admin.machines.namePlaceholder')} value={regName} onChange={e => setRegName(e.target.value)} />
            </div>
            <div className="space-y-2">
              <Label>{t('admin.machines.description')} <span className="text-[var(--color-foreground-subtle)]">{t('admin.machines.optional')}</span></Label>
              <Input placeholder={t('admin.machines.descriptionPlaceholder')} value={regDescription} onChange={e => setRegDescription(e.target.value)} />
            </div>
          </div>
          <DialogFooter>
            <Button onClick={handleRegister} disabled={regLoading || !regName.trim()}>
              {regLoading ? t('admin.machines.registering') : t('admin.machines.register')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* ── Token Display Dialog ── */}
      <Dialog open={tokenDialogOpen} onOpenChange={setTokenDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t('admin.machines.registered')}</DialogTitle>
            <DialogDescription>{t('admin.machines.copyTokenDescription')}</DialogDescription>
          </DialogHeader>
          <div className="space-y-3 py-2">
            <div className="rounded-[var(--radius-md)] border border-[color:color-mix(in_srgb,var(--color-warning)_25%,transparent)] bg-[color:color-mix(in_srgb,var(--color-warning)_8%,transparent)] p-3 text-sm text-[var(--color-warning)]">
              {t('admin.machines.copyTokenWarning')}
            </div>
            <div className="flex gap-2">
              <code className="flex-1 font-mono text-sm bg-[var(--color-surface-alt)] rounded-[var(--radius-md)] p-3 border border-[var(--color-border)] break-all">
                {tokenResult?.machine_token}
              </code>
              <Button variant="ghost" size="icon" onClick={async () => {
                if (tokenResult) await navigator.clipboard.writeText(tokenResult.machine_token)
                setCopied(true); setTimeout(() => setCopied(false), 2000)
              }}>
                {copied ? <Check className="h-4 w-4 text-[var(--color-success)]" /> : <Copy className="h-4 w-4" />}
              </Button>
            </div>
          </div>
          <DialogFooter>
            <Button onClick={() => setTokenDialogOpen(false)}>{t('admin.machines.done')}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* ── Create Agent on Machine Dialog ── */}
      <Dialog open={createAgentOpen} onOpenChange={setCreateAgentOpen}>
        <DialogContent className="max-w-md max-h-[90dvh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>{t('admin.machines.createAgentTitle', { name: selectedMachine?.name ?? '' })}</DialogTitle>
            <DialogDescription>{t('admin.machines.createAgentDescription')}</DialogDescription>
          </DialogHeader>
          <div className="space-y-4 py-2">
            <div className="space-y-2">
              <Label>{t('admin.machines.name')}</Label>
              <Input placeholder={t('admin.machines.agentNamePlaceholder')} value={agentName} onChange={e => setAgentName(e.target.value)} />
            </div>
            <div className="space-y-2">
              <Label htmlFor="create-agent-engine">{t('admin.machines.engine')}</Label>
              <select id="create-agent-engine" value={agentEngine} onChange={e => setAgentEngine(e.target.value)} className={selectCSS}>
                <option value="" disabled>{t('admin.machines.selectEngine')}</option>
                {sortedMachineEngines.map(e => {
                  const label = ENGINE_LABELS[e.engine] ?? e.engine
                  const deprecated = engineMetadataById.get(e.engine)?.deprecated === true
                  return (
                    <option key={e.engine} value={e.engine}>
                      {deprecated ? `${label} (${t('admin.machines.deprecated')})` : label}
                    </option>
                  )
                })}
              </select>
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
                <select id="create-pi-connection-type" className={selectCSS}
                  value={endpointActive ? 'direct' : 'native'}
                  onChange={e => {
                    setEndpointDraft({ ...emptyEndpointDraft('pi-cli'), enabled: e.target.value === 'direct' })
                    setAgentProvider(''); setAgentModel(''); setDiscoveredModels([])
                  }}>
                  <option value="native">{t('admin.machines.piProvider')}</option>
                  <option value="direct">{t('admin.machines.directServer')}</option>
                </select>
              </div>
            )}
            {agentEngine === 'pi-cli' && (
              <>
                <div className="space-y-2">
                  <Label htmlFor="pi-provider">{t('admin.machines.providerRequired')}</Label>
                  <Input id="pi-provider" value={agentProvider} onChange={e => setAgentProvider(e.target.value)}
                    placeholder={t('admin.machines.providerPlaceholder')} maxLength={64} required aria-invalid={!validPiProvider} />
                  <p className="text-xs text-[var(--color-foreground-muted)]">
                    {endpointActive
                      ? t('admin.machines.directProviderHint')
                      : t('admin.machines.providerHint')}
                  </p>
                </div>
                <div className="space-y-2">
                  <Label htmlFor="pi-model">{t('admin.machines.modelRequired')}</Label>
                  <Input id="pi-model" value={agentModel} onChange={e => setAgentModel(e.target.value)}
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
                <Label>{t('admin.machines.model')}</Label>
                <select value={agentModel} onChange={e => setAgentModel(e.target.value)} className={selectCSS}>
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
                </select>
              </div>
            )}
            {endpointActive && (
              <CreateAgentEndpointSection
                engine={agentEngine}
                draft={endpointDraft}
                onChange={setEndpointDraft}
                onModelsLoaded={models => {
                  setDiscoveredModels(models)
                  if (models.length === 1 && !agentModel) setAgentModel(models[0].id)
                }}
                selectClassName={selectCSS}
              />
            )}
            {agentReasoningLevels.length > 0 && (
              <div className="space-y-2">
                <Label>{t('admin.machines.reasoningEffort')}</Label>
                <select value={agentReasoning} onChange={e => setAgentReasoning(e.target.value)} className={selectCSS}>
                  <option value="">{t('admin.machines.default')}</option>
                  {agentReasoningLevels.map(level => (
                    <option key={level} value={level}>
                      {reasoningLabel(level)}
                    </option>
                  ))}
                </select>
              </div>
            )}
            <div className="space-y-2">
              <Label>{t('admin.machines.roomsOptional')}</Label>
              <div className="max-h-40 overflow-y-auto rounded-[var(--radius-md)] border border-[var(--color-border)]">
                {projects.map(project => {
                  const rs = roomsByProject[project.id] ?? []
                  if (rs.length === 0) return null
                  return (
                    <div key={project.id} className="py-1">
                      <div className="px-3 py-1 text-[10px] uppercase tracking-wider text-[var(--color-foreground-muted)]">{project.name}</div>
                      {rs.map(room => (
                        <label key={room.id} className="flex items-center gap-2 px-3 py-1 text-sm hover:bg-[var(--color-surface-alt)] cursor-pointer">
                          <input type="checkbox" checked={agentRooms.has(room.id)} onChange={() => {
                            setAgentRooms(prev => {
                              const next = new Set(prev)
                              if (next.has(room.id)) next.delete(room.id); else next.add(room.id)
                              return next
                            })
                          }} />
                          <span className="truncate">{room.name}</span>
                        </label>
                      ))}
                    </div>
                  )
                })}
              </div>
              {agentRooms.size === 0 && (
                <p className="text-xs text-[var(--color-foreground-muted)]">
                  {t('admin.machines.noRoomsSelected')}
                </p>
              )}
            </div>
          </div>
          <DialogFooter>
            {createError && <p role="alert">{createError}</p>}
            <Button onClick={handleCreateAgent} disabled={creating || !agentName.trim() || !agentEngine || (providerRequired && !validPiProvider) || (agentEngine === 'pi-cli' && !endpointActive && !agentModel.trim()) || (endpointActive && !endpointDraftReady(endpointDraft, agentModel))}>
              {creating ? t('admin.machines.creating') : t('admin.machines.createAgent')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* #158 — unified per-agent settings dialog replaces the four
          separate dialogs (rooms / edit / history / avatar). */}
      <AgentSettingsDialog
        agent={settingsAgent}
        open={settingsOpen}
        onOpenChange={open => {
          setSettingsOpen(open)
          // #281 — drop the tracked ID on close so an externally-
          // deleted agent doesn't leave the dialog re-opening into a
          // ``null`` derived prop on the next admin click.
          if (!open) setSettingsAgentId(null)
        }}
        fetchAgentFiles={fetchAgentFiles}
        updateAgent={updateAgent}
        upsertAgentFile={upsertAgentFile}
        deleteAgentFile={deleteAgentFile}
        fetchAttachedSkills={fetchAttachedSkills}
        fetchSkillPreview={fetchSkillPreview}
        fetchEngineCatalog={fetchEngineCatalog}
        onRoomsChange={() => selectedId && fetchDetail(selectedId)}
        onDelete={
          settingsAgent
            ? async () => {
                if (await handleDeleteAgent(settingsAgent.id)) {
                  setSettingsOpen(false)
                  setSettingsAgentId(null)
                }
              }
            : undefined
        }
        contextWindowOptOut={settingsAgent?.context_window_opt_out ?? false}
        onToggleContextWindowOptOut={
          settingsAgent
            ? () =>
                handleToggleContextWindowOptOut(
                  settingsAgent.id,
                  settingsAgent.context_window_opt_out ?? false,
                )
            : undefined
        }
      />
    </div>
  )
}
