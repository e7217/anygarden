import { useState, useEffect, useCallback, useMemo } from 'react'
import { useMachines } from '@/hooks/useMachines'
import { useAgents } from '@/hooks/useAgents'
import { useRooms } from '@/hooks/useRooms'
import { useMachineDetail, type MachineEngineInfo } from '@/hooks/useMachineDetail'
import type { RegisterMachineResult } from '@/hooks/useMachines'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter, DialogDescription,
} from '@/components/ui/dialog'
import {
  Plus, Trash2, RefreshCw,
  PauseCircle, Server, Bot, Square, Play,
  Loader2, ArrowUpCircle, Cable,
} from 'lucide-react'
import AgentSettingsDialog from '@/components/AgentSettingsDialog'
import MachineConnectionDialog from '@/components/MachineConnectionDialog'
import CreateAgentDialog from '@/components/CreateAgentDialog'
import AgentSettingsMenu from '@/components/AgentSettingsMenu'
import { EntityAvatar, type AvatarKind } from '@/components/EntityAvatar'
import PresenceDot from '@/components/PresenceDot'
import { deriveAgentOnline, agentStatusLabel } from '@/lib/agent-liveness'
import { shouldShowFallbackCrashWarning } from '@/lib/admin-agent-warning'
import { useLocale } from '@/i18n/LocaleProvider'
import { useFeedback } from '@/components/feedback/FeedbackProvider'
import type { Agent } from '@/hooks/useAgents'

// ── Types ──────────────────────────────────────────────────────────

const ENGINE_LABELS: Record<string, string> = {
  'pi-cli': 'Pi',
  'codex-cli': 'Codex CLI',
  'claude-code': 'Claude Code',
  'gemini-cli': 'Gemini CLI',
  'openai': 'OpenAI API',
  'anthropic': 'Anthropic API',
}

const DEPRECATED_BADGE_CSS =
  'border-[color:color-mix(in_srgb,var(--color-warning)_40%,transparent)] bg-[color:color-mix(in_srgb,var(--color-warning)_8%,transparent)] text-xs text-[var(--color-warning)]'

// #553 — small engine status pill, mirroring #546's StatusBadge styling.
function EngineStatusBadge({ info }: { info: MachineEngineInfo }) {
  const { t } = useLocale()
  const pill = 'shrink-0 rounded-full px-2 py-0.5 text-xs font-semibold'
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
  const { t, formatDate } = useLocale()
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
  const { machines, status: machinesStatus, fetchMachines, drainMachine, registerMachine, deleteMachine, updateMachineDaemon, checkMachineEngine, updateMachineEngine, regenerateToken } = useMachines()
  const {
    createAgent, fetchEngineCatalog, agents, startAgent, stopAgent,
    pendingIds,
    deleteAgent, updateAgent, fetchAgentFiles, upsertAgentFile, deleteAgentFile,
    fetchAttachedSkills, fetchSkillPreview,
    availableEngines,
  } = useAgents()
  const { projects, rooms: roomsByProject, fetchAgentDMs, status: roomsStatus, refetch: refetchRooms } = useRooms()

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

  // The hook keeps each response tied to its selected machine and request.
  const selectedAgentStates = useMemo(() => agents
    .filter(agent => agent.placed_on_machine_id === selectedId)
    .map(agent => `${agent.id}:${agent.actual_state}`)
    .sort().join(','), [agents, selectedId])
  const { data: detail, status: detailStatus, refresh: fetchDetail } = useMachineDetail(
    selectedMachine?.id ?? null, selectedAgentStates,
  )
  const machineAgents = detail?.agents ?? []
  const machineEngines = detail?.engines ?? []
  const machineActivity = detail?.activity ?? []
  const detailReady = detailStatus === 'loaded'

  // #553 — engine check/update in flight (per engine key), disables its row.
  const [engineBusy, setEngineBusy] = useState<string | null>(null)

  const handleCheckEngine = useCallback(async (engine: string) => {
    if (!selectedId) return
    const actionKey = `${selectedId}:${engine}`
    setEngineBusy(actionKey)
    try {
      await checkMachineEngine(selectedId, engine)
      // Result arrives over WS; re-fetch shortly after to pick it up.
      setTimeout(() => { if (selectedId) fetchDetail(selectedId) }, 1500)
    } catch {
      /* disabled state already conveys failure; refresh reconciles */
    } finally {
      setEngineBusy(current => current === actionKey ? null : current)
    }
  }, [selectedId, checkMachineEngine, fetchDetail])

  const handleUpdateEngine = useCallback(async (engine: string) => {
    if (!selectedId) return
    const actionKey = `${selectedId}:${engine}`
    setEngineBusy(actionKey)
    try {
      await updateMachineEngine(selectedId, engine)
      await fetchDetail(selectedId)  // reflect "updating" immediately
      setTimeout(() => { if (selectedId) fetchDetail(selectedId) }, 3000)
    } catch {
      /* status stays as-is */
    } finally {
      setEngineBusy(current => current === actionKey ? null : current)
    }
  }, [selectedId, updateMachineEngine, fetchDetail])

  // Pending starts reserve capacity as well as currently running agents.
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
  const [connectionOpen, setConnectionOpen] = useState(false)
  const [connectionMachineId, setConnectionMachineId] = useState<string | null>(null)
  const [registerError, setRegisterError] = useState<string | null>(null)

  const handleRegister = async () => {
    if (!regName.trim()) return
    setRegLoading(true)
    setRegisterError(null)
    try {
      const result = await registerMachine({
        name: regName.trim(),
        description: regDescription.trim() || undefined,
      })
      setTokenResult(result)
      setSelectedId(result.id)
      setConnectionMachineId(result.id)
      setRegName(''); setRegDescription('')
      setRegisterOpen(false)
      setConnectionOpen(true)
    } catch { setRegisterError(t('admin.machines.registerFailed')) }
    setRegLoading(false)
  }

  // ── Create Agent on Machine ──────────────────────────────────────
  const [createAgentOpen, setCreateAgentOpen] = useState(false)
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

  // ── Token / Control ──────────────────────────────────────────────
  const connectionMachine = machines.find(machine => machine.id === connectionMachineId)
    ?? (tokenResult?.id === connectionMachineId ? tokenResult : null)
  const openConnectionGuide = (machineId: string) => {
    setTokenResult(null)
    setConnectionMachineId(machineId)
    setConnectionOpen(true)
  }

  // ── Render ───────────────────────────────────────────────────────

  return (
    <div className="flex min-h-full min-w-0 flex-col lg:h-full lg:flex-row">
      {/* ── Left: Machine Card List ── */}
      <div className="w-full min-w-0 shrink-0 border-b border-[var(--color-border)] bg-[var(--color-background)] lg:w-64 lg:overflow-y-auto lg:border-r lg:border-b-0">
        <div className="flex items-center justify-between gap-3 border-b border-[var(--color-border)] px-4 py-3 lg:flex-col lg:items-stretch">
          <h1 className="text-heading min-w-0 text-[var(--color-foreground)]">{t('admin.machines.title')}</h1>
          <Button size="sm" className="gap-2 lg:w-full" onClick={() => { setRegisterError(null); setRegisterOpen(true) }} aria-label={t('admin.machines.registerMachine')}>
            <Plus className="h-4 w-4" />
            <span>{t('admin.machines.registerMachine')}</span>
          </Button>
        </div>
        <p className="px-4 pt-3 text-xs leading-relaxed text-[var(--color-foreground-muted)]">{t('admin.machines.purpose')}</p>
        {machinesStatus === 'error' && <div className="px-4 pt-3 text-xs text-[var(--color-destructive)]" role="alert">
          <p>{t('admin.machines.loadFailed')}</p>
          <Button variant="ghost" size="sm" onClick={() => { void fetchMachines().catch(() => {}) }}><RefreshCw />{t('common.refresh')}</Button>
        </div>}
        <div className="flex min-w-0 gap-2 overflow-x-auto p-3 lg:block lg:space-y-2 lg:overflow-x-visible lg:p-2">
          {machines.length === 0 ? (
            <div className="w-full px-3 py-8 text-center">
              <Server className="mx-auto h-8 w-8 text-[var(--color-foreground-subtle)] mb-2" />
              <p className="text-xs text-[var(--color-foreground-muted)]">{t('admin.machines.none')}</p>
              <Button variant="ghost" size="sm" className="mt-2" onClick={() => { setRegisterError(null); setRegisterOpen(true) }}>
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

            <div className="flex flex-col items-stretch justify-between gap-3 rounded-[var(--radius-md)] border border-[var(--color-border)] bg-[var(--color-surface-elevated)] p-4 sm:flex-row sm:items-center">
              <p className="min-w-0 flex-1 text-sm text-[var(--color-foreground-muted)]">
                {selectedMachine.status === 'offline' ? t('admin.machines.offlineDescription') : t('admin.machines.connectionReady')}
              </p>
              <Button variant="outline" size="sm" className="w-full sm:w-auto" onClick={() => openConnectionGuide(selectedMachine.id)}><Cable />{t('admin.machines.connectionGuide')}</Button>
            </div>

            {detailStatus === 'loading' && <p role="status" className="flex items-center gap-2 text-sm text-[var(--color-foreground-muted)]"><Loader2 className="h-4 w-4 animate-spin" />{t('admin.machines.detailLoading')}</p>}
            {detailStatus === 'error' && <div role="alert" className="flex flex-wrap items-center gap-3 rounded-[var(--radius-md)] border border-[var(--color-destructive)] p-3 text-sm">
              <p className="flex-1">{t('admin.machines.detailFailed')}</p>
              <Button variant="outline" size="sm" onClick={() => void fetchDetail(selectedMachine.id)}>{t('common.retry')}</Button>
            </div>}

            {/* Agents */}
            <div className="rounded-[var(--radius-lg)] border border-[var(--color-border)] bg-[var(--color-surface-elevated)] shadow-[var(--shadow-card)]">
              <div className="flex items-center justify-between px-4 py-2.5 border-b border-[var(--color-border)]">
                <h3 className="text-sm font-semibold text-[var(--color-foreground-muted)]">
                  {detailReady ? t('admin.machines.agentsHeading', { count: machineAgents.length }) : t('chat.agents')}
                </h3>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => setCreateAgentOpen(true)}
                  disabled={!detailReady || selectedMachine.status !== 'online' || machineEngines.length === 0}
                >
                  <Plus className="mr-1 h-3.5 w-3.5" /> {t('admin.machines.newAgent')}
                </Button>
              </div>
              <div className="divide-y divide-[var(--color-border)]">
                {!detailReady ? null : machineAgents.length === 0 ? (
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

            {/* Info */}
            <div className="rounded-[var(--radius-lg)] border border-[var(--color-border)] bg-[var(--color-surface-elevated)] shadow-[var(--shadow-card)]">
              <div className="px-4 py-2.5 border-b border-[var(--color-border)]">
                <h3 className="text-sm font-semibold text-[var(--color-foreground-muted)]">{t('admin.machines.info')}</h3>
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
                      const busy = engineBusy === `${selectedMachine.id}:${e.engine}`
                      const online = selectedMachine?.status === 'online'
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
                            <span className="font-mono text-xs text-[var(--color-foreground-muted)]">
                              {e.version}
                            </span>
                          )}
                          {e.update_available && e.latest_version && (
                            <span className="font-mono text-xs text-[var(--color-brand-text)]">
                              → {e.latest_version}
                            </span>
                          )}
                          <EngineStatusBadge info={e} />
                          <Button
                            variant="outline"
                            size="sm"
                            onClick={() => handleCheckEngine(e.engine)}
                            disabled={busy || !online || !detailReady}
                            title={online ? t('admin.machines.checkLatest') : t('admin.machines.offlineHint')}
                          >
                            {busy ? '…' : t('admin.machines.check')}
                          </Button>
                          {e.update_available && (
                            <Button
                              variant="outline"
                              size="sm"
                              onClick={() => handleUpdateEngine(e.engine)}
                              disabled={busy || !online || !detailReady}
                            >
                              {t('admin.machines.update')}
                            </Button>
                          )}
                        </div>
                      )
                    })}
                    {detailReady && sortedMachineEngines.length === 0 && <span className="text-xs text-[var(--color-foreground-muted)]">{t('admin.machines.noEnginesHint')}</span>}
                  </div>
                </div>
                <div>
                  <span className="text-[var(--color-foreground-muted)]">{t('admin.machines.activeAgents')}</span>
                  <p className="text-[var(--color-foreground)] font-medium">
                    {!detailReady ? '—' : selectedMachine.status === 'offline'
                      ? <span className="text-[var(--color-foreground-subtle)]">{t('admin.machines.unknownOffline')}</span>
                      : machineAgents.filter(a => a.actual_state === 'running' || a.actual_state === 'starting' || a.actual_state === 'pending').length}
                  </p>
                </div>
              </div>
            </div>

            {/* Token & Control */}
            <div className="rounded-[var(--radius-lg)] border border-[var(--color-border)] bg-[var(--color-surface-elevated)] shadow-[var(--shadow-card)]">
              <div className="px-4 py-2.5 border-b border-[var(--color-border)]">
                <h3 className="text-sm font-semibold text-[var(--color-foreground-muted)]">{t('admin.machines.tokenControl')}</h3>
              </div>
              <div className="px-4 py-3 space-y-3">
                <div className="flex flex-wrap gap-2">
                  <Button variant="outline" size="sm" onClick={async () => {
                    if (!await confirmAction({ title: t('admin.machines.rotateToken'), description: t('admin.machines.confirmRotate') })) return
                    try {
                      const result = await regenerateToken(selectedMachine.id, false)
                      setTokenResult({ ...selectedMachine, machine_token: result.token })
                      setConnectionMachineId(selectedMachine.id)
                      setConnectionOpen(true)
                    } catch { notify({ message: t('admin.machines.rotateFailed'), tone: 'error' }) }
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
                    disabled={!detailReady}
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
                <h3 className="text-sm font-semibold text-[var(--color-foreground-muted)]">{t('admin.machines.history')}</h3>
              </div>
              <div className="px-4 py-3 max-h-64 overflow-y-auto">
                {!detailReady ? null : machineActivity.length === 0 ? (
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
            <div className="rounded-[var(--radius-md)] border border-[var(--color-border)] bg-[var(--color-surface-alt)] p-3 text-sm">
              <p className="font-medium">{t('admin.machines.prepare')}</p>
              <p className="mt-1 text-[var(--color-foreground-muted)]">{t('admin.machines.prepareDescription')}</p>
            </div>
            <div className="space-y-2">
              <Label htmlFor="register-machine-name">{t('admin.machines.name')}</Label>
              <Input id="register-machine-name" placeholder={t('admin.machines.namePlaceholder')} value={regName} onChange={e => setRegName(e.target.value)} />
            </div>
            <div className="space-y-2">
              <Label htmlFor="register-machine-description">{t('admin.machines.description')} <span className="text-[var(--color-foreground-subtle)]">{t('admin.machines.optional')}</span></Label>
              <Input id="register-machine-description" placeholder={t('admin.machines.descriptionPlaceholder')} value={regDescription} onChange={e => setRegDescription(e.target.value)} />
            </div>
          </div>
          <DialogFooter>
            {registerError && <p role="alert" className="text-sm text-[var(--color-destructive)]">{registerError}</p>}
            <Button variant="outline" onClick={() => setRegisterOpen(false)}>{t('common.cancel')}</Button>
            <Button onClick={handleRegister} disabled={regLoading || !regName.trim()}>
              {regLoading ? t('admin.machines.registering') : t('admin.machines.register')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {connectionMachine && <MachineConnectionDialog
        key={connectionMachine.id + (tokenResult?.machine_token ? ':token' : ':saved')}
        open={connectionOpen}
        onOpenChange={open => {
          setConnectionOpen(open)
          if (!open) setTokenResult(null)
        }}
        machine={connectionMachine}
        token={tokenResult?.id === connectionMachine.id ? tokenResult.machine_token : undefined}
        refreshWarning={tokenResult?.refreshWarning}
        onCheck={async () => {
          await fetchMachines()
          if (selectedId && selectedId !== UNPLACED) await fetchDetail(selectedId)
        }}
      />}

      <CreateAgentDialog
        open={createAgentOpen}
        onOpenChange={setCreateAgentOpen}
        machineId={selectedMachine?.id ?? ''}
        machineName={selectedMachine?.name ?? ''}
        engines={sortedMachineEngines}
        availableEngines={availableEngines}
        projects={projects}
        roomsByProject={roomsByProject}
        roomsStatus={roomsStatus}
        onRetryRooms={refetchRooms}
        createAgent={createAgent}
        fetchEngineCatalog={fetchEngineCatalog}
        onCreated={created => {
          if (selectedId && selectedId !== UNPLACED) void fetchDetail(selectedId)
          fetchAgentDMs()
          setSettingsAgentId(created.id)
          setSettingsOpen(true)
        }}
      />

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
