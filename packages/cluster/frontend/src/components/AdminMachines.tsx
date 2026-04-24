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
  DoorOpen, FileCog, History, Loader2,
} from 'lucide-react'
import { apiFetch } from '@/lib/api'
import AgentSettingsDialog from '@/components/AgentSettingsDialog'
import AgentSettingsMenu from '@/components/AgentSettingsMenu'
import { EntityAvatar, type AvatarKind } from '@/components/EntityAvatar'
import PresenceDot from '@/components/PresenceDot'
import { deriveAgentOnline, agentStatusLabel } from '@/lib/agent-liveness'
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
  engine: string; version?: string | null
}

const ENGINE_LABELS: Record<string, string> = {
  'codex': 'Codex CLI',
  'codex-extra': 'Codex (extra)',
  'claude-code': 'Claude Code',
  'gemini-cli': 'Gemini CLI',
  'openai': 'OpenAI API',
  'anthropic': 'Anthropic API',
}

function statusDot(status: string) {
  if (status === 'online' || status === 'running') return 'bg-[var(--color-success)]'
  if (status === 'draining' || status === 'starting' || status === 'pending') return 'bg-[var(--color-warning)]'
  return 'bg-[var(--color-foreground-subtle)]'
}

function statusLabel(status: string) {
  if (status === 'online') return 'Online'
  if (status === 'offline') return 'Offline'
  if (status === 'draining') return 'Draining'
  return status
}

// ── Main Component ─────────────────────────────────────────────────

export default function AdminMachines() {
  const { machines, drainMachine, registerMachine, deleteMachine, updateMachine, regenerateToken } = useMachines()
  const {
    createAgent, fetchEngineCatalog, agents, startAgent, stopAgent,
    pendingIds,
    deleteAgent, updateAgent, fetchAgentFiles, upsertAgentFile, deleteAgentFile,
    fetchAttachedSkills, fetchSkillPreview,
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
  const [regHostname, setRegHostname] = useState('')
  const [regLoading, setRegLoading] = useState(false)
  const [tokenResult, setTokenResult] = useState<RegisterMachineResult | null>(null)
  const [tokenDialogOpen, setTokenDialogOpen] = useState(false)
  const [copied, setCopied] = useState(false)

  const handleRegister = async () => {
    if (!regName.trim() || !regHostname.trim()) return
    setRegLoading(true)
    try {
      const result = await registerMachine({
        name: regName.trim(), hostname: regHostname.trim(),
      })
      setTokenResult(result)
      setRegName(''); setRegHostname('')
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
  const [agentCatalog, setAgentCatalog] = useState<EngineCatalog | null>(null)
  const [agentRooms, setAgentRooms] = useState<Set<string>>(new Set())
  const [creating, setCreating] = useState(false)

  // Keep the model/reasoning catalog in sync with the selected engine.
  // Resetting model + reasoning on every change prevents a stale
  // selection from a previous engine (e.g. codex "xhigh") leaking
  // into a different one (gemini).
  useEffect(() => {
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
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [settingsAgent, setSettingsAgent] = useState<Agent | null>(null)

  const handleOpenSettings = (agentId: string) => {
    const full = agents.find(a => a.id === agentId)
    if (!full) return
    setSettingsAgent(full)
    setSettingsOpen(true)
  }

  const handleDeleteAgent = async (agentId: string) => {
    if (!confirm('Delete this agent? This cannot be undone.')) return
    await deleteAgent(agentId)
    // Delete cascades the DM room (see PR #12) — refresh both the
    // per-machine detail and the sidebar DM list so the ghost entry
    // doesn't linger until a manual reload.
    if (selectedId && selectedId !== UNPLACED) fetchDetail(selectedId)
    fetchAgentDMs()
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
    setCreating(true)
    try {
      await createAgent({
        name: agentName.trim(),
        engine: agentEngine,
        rooms: Array.from(agentRooms),
        ...(agentReasoning ? { reasoning_effort: agentReasoning } : {}),
        ...(agentModel ? { model: agentModel } : {}),
      })
      setAgentName(''); setAgentEngine(''); setAgentReasoning('')
      setAgentModel(''); setAgentCatalog(null); setAgentRooms(new Set())
      setCreateAgentOpen(false)
      fetchDetail(selectedId)
      // create_agent auto-creates a DM room server-side; the sidebar
      // caches DMs separately so nudge it to refetch otherwise the
      // new agent only appears after a full page reload.
      fetchAgentDMs()
    } catch { /* ignore */ }
    setCreating(false)
  }

  // ── Token / Control ──────────────────────────────────────────────
  const [regenToken, setRegenToken] = useState<string | null>(null)
  const [regenCopied, setRegenCopied] = useState(false)

  const selectCSS = "flex h-9 w-full rounded-[var(--radius-xs)] border border-[var(--color-border-strong)] bg-[var(--color-background)] px-3 py-1 text-sm text-[var(--color-foreground)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-brand-focus)]"

  // ── Render ───────────────────────────────────────────────────────

  return (
    <div className="flex h-full">
      {/* ── Left: Machine Card List ── */}
      <div className="w-64 shrink-0 border-r border-[var(--color-border)] overflow-y-auto bg-[var(--color-background)]">
        <div className="flex items-center justify-between px-4 py-3 border-b border-[var(--color-border)]">
          <h2 className="text-sm font-semibold text-[var(--color-foreground)]">Machines</h2>
          <Button variant="ghost" size="sm" onClick={() => setRegisterOpen(true)}>
            <Plus className="h-4 w-4" />
          </Button>
        </div>
        <div className="p-2 space-y-1">
          {machines.length === 0 ? (
            <div className="px-3 py-8 text-center">
              <Server className="mx-auto h-8 w-8 text-[var(--color-foreground-subtle)] mb-2" />
              <p className="text-xs text-[var(--color-foreground-muted)]">No machines registered</p>
              <Button variant="ghost" size="sm" className="mt-2" onClick={() => setRegisterOpen(true)}>
                <Plus className="mr-1 h-3 w-3" /> Register
              </Button>
            </div>
          ) : (
            <>
              {machines.map(m => (
                <button
                  key={m.id}
                  onClick={() => setSelectedId(m.id)}
                  className={`w-full text-left rounded-[var(--radius-lg)] border px-3 py-2.5 transition-all ${
                    selectedId === m.id
                      ? 'bg-[#f2f9ff] border-[var(--color-brand)] shadow-[var(--shadow-card)]'
                      : 'bg-white border-[rgba(0,0,0,0.1)] hover:shadow-[var(--shadow-card)]'
                  }`}
                >
                  <div className="text-sm font-medium text-[var(--color-foreground)] truncate">{m.name}</div>
                  <div className="text-xs text-[var(--color-foreground-muted)] truncate">{m.hostname}</div>
                  <div className="flex items-center gap-2 mt-1.5">
                    <span className="flex items-center gap-1 text-xs text-[var(--color-foreground-muted)]">
                      <span className={`inline-block h-1.5 w-1.5 rounded-full ${statusDot(m.status)}`} />
                      {statusLabel(m.status)}
                    </span>
                    <span className="text-xs text-[var(--color-foreground-subtle)]">
                      {agentCountByMachine.get(m.id) ?? 0} agents
                    </span>
                  </div>
                </button>
              ))}
              {unplacedAgents.length > 0 && (
                <button
                  onClick={() => setSelectedId(UNPLACED)}
                  className={`w-full text-left rounded-[var(--radius-lg)] border border-dashed px-3 py-2.5 transition-all ${
                    isUnplacedView
                      ? 'bg-[#fff7ed] border-[var(--color-warning)] shadow-[var(--shadow-card)]'
                      : 'bg-white border-[rgba(0,0,0,0.2)] hover:shadow-[var(--shadow-card)]'
                  }`}
                >
                  <div className="text-sm font-medium text-[var(--color-foreground)] truncate">Unplaced</div>
                  <div className="text-xs text-[var(--color-foreground-muted)] truncate">Agents not bound to any machine</div>
                  <div className="flex items-center gap-2 mt-1.5">
                    <span className="text-xs text-[var(--color-foreground-subtle)]">
                      {unplacedAgents.length} agent{unplacedAgents.length > 1 ? 's' : ''}
                    </span>
                  </div>
                </button>
              )}
            </>
          )}
        </div>
      </div>

      {/* ── Right: Machine Detail ── */}
      <div className="flex-1 overflow-y-auto p-6">
        {isUnplacedView ? (
          <div className="max-w-2xl space-y-6">
            <div>
              <h1 className="text-lg font-semibold text-[var(--color-foreground)]">Unplaced agents</h1>
              <p className="text-sm text-[var(--color-foreground-muted)]">
                These agents exist but are not bound to any machine — usually
                because their last spawn attempt found no eligible host.
                Delete to remove, or Retry to let the scheduler try placing
                them again.
              </p>
            </div>
            <div className="rounded-[var(--radius-lg)] border border-[rgba(0,0,0,0.1)] bg-white shadow-[var(--shadow-card)] divide-y divide-[var(--color-border)]">
              {unplacedAgents.length === 0 ? (
                <div className="px-4 py-8 text-center">
                  <Bot className="mx-auto h-8 w-8 text-[var(--color-foreground-subtle)] mb-2" />
                  <p className="text-sm text-[var(--color-foreground-muted)]">No unplaced agents</p>
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
                        <span className="text-xs text-[var(--color-foreground-muted)]">· {agent.actual_state}</span>
                      </div>
                      <div className="flex items-center gap-2 mt-0.5 text-xs text-[var(--color-foreground-subtle)]">
                        <span>{ENGINE_LABELS[agent.engine] ?? agent.engine}</span>
                        {agent.last_crash_reason && (
                          <span className="truncate text-[var(--color-warning)]" title={agent.last_crash_reason}>
                            · {agent.last_crash_reason}
                          </span>
                        )}
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
                      title="Retry placement"
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
            <p className="text-sm text-[var(--color-foreground-muted)]">Select a machine</p>
          </div>
        ) : (
          <div className="max-w-2xl space-y-6">
            {/* Header */}
            <div className="flex items-center justify-between">
              <div>
                <h1 className="text-lg font-semibold text-[var(--color-foreground)]">{selectedMachine.name}</h1>
                <p className="text-sm text-[var(--color-foreground-muted)]">{selectedMachine.hostname}</p>
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
            <div className="rounded-[var(--radius-lg)] border border-[rgba(0,0,0,0.1)] bg-white shadow-[var(--shadow-card)]">
              <div className="px-4 py-2.5 border-b border-[var(--color-border)]">
                <h3 className="text-xs font-semibold uppercase tracking-wider text-[var(--color-foreground-muted)]">Info</h3>
              </div>
              <div className="grid grid-cols-2 gap-x-8 gap-y-2 px-4 py-3 text-sm">
                <div>
                  <span className="text-[var(--color-foreground-muted)]">Hostname</span>
                  <p className="text-[var(--color-foreground)] font-medium">{selectedMachine.hostname}</p>
                </div>
                <div>
                  <span className="text-[var(--color-foreground-muted)]">Version</span>
                  <p className="text-[var(--color-foreground)] font-medium">{selectedMachine.daemon_version || '-'}</p>
                </div>
                <div>
                  <span className="text-[var(--color-foreground-muted)]">Engines</span>
                  <div className="flex flex-wrap gap-1 mt-0.5">
                    {machineEngines.map(e => (
                      <Badge key={e.engine} variant="outline" className="text-xs">
                        {ENGINE_LABELS[e.engine] ?? e.engine}
                      </Badge>
                    ))}
                    {machineEngines.length === 0 && <span className="text-[var(--color-foreground-subtle)]">-</span>}
                  </div>
                </div>
                <div>
                  <span className="text-[var(--color-foreground-muted)]">Active agents</span>
                  <p className="text-[var(--color-foreground)] font-medium">
                    {selectedMachine.status === 'offline'
                      ? <span className="text-[var(--color-foreground-subtle)]">unknown (offline)</span>
                      : machineAgents.filter(a => a.actual_state === 'running' || a.actual_state === 'starting' || a.actual_state === 'pending').length}
                  </p>
                </div>
              </div>
            </div>

            {/* Agents */}
            <div className="rounded-[var(--radius-lg)] border border-[rgba(0,0,0,0.1)] bg-white shadow-[var(--shadow-card)]">
              <div className="flex items-center justify-between px-4 py-2.5 border-b border-[var(--color-border)]">
                <h3 className="text-xs font-semibold uppercase tracking-wider text-[var(--color-foreground-muted)]">
                  Agents ({machineAgents.length})
                </h3>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => {
                    setCreateAgentOpen(true)
                    if (machineEngines.length > 0 && !agentEngine) setAgentEngine(machineEngines[0].engine)
                  }}
                  disabled={selectedMachine.status !== 'online'}
                >
                  <Plus className="mr-1 h-3.5 w-3.5" /> New Agent
                </Button>
              </div>
              <div className="divide-y divide-[var(--color-border)]">
                {machineAgents.length === 0 ? (
                  <div className="px-4 py-8 text-center">
                    <Bot className="mx-auto h-8 w-8 text-[var(--color-foreground-subtle)] mb-2" />
                    <p className="text-sm text-[var(--color-foreground-muted)]">No agents on this machine</p>
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
                  const displayState = agentStatusLabel(agent.actual_state, { machineOffline: isMachineOffline })
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
                            {agent.reasoning_effort && <span>· {agent.reasoning_effort}</span>}
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
                                title={isRunning ? 'Stopping…' : 'Starting…'}
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
                                title={isMachineOffline ? 'Machine is offline' : 'Stop'}
                                disabled={isMachineOffline}
                              >
                                <Square className="h-3.5 w-3.5 text-red-500" />
                              </Button>
                            )
                          }
                          return (
                            <Button
                              variant="ghost"
                              size="icon"
                              onClick={() => { void startAgent(agent.id) }}
                              title={isMachineOffline ? 'Machine is offline' : 'Start'}
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
            <div className="rounded-[var(--radius-lg)] border border-[rgba(0,0,0,0.1)] bg-white shadow-[var(--shadow-card)]">
              <div className="px-4 py-2.5 border-b border-[var(--color-border)]">
                <h3 className="text-xs font-semibold uppercase tracking-wider text-[var(--color-foreground-muted)]">Token & Control</h3>
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
                    if (!confirm('Rotate token? Daemon will reconnect with the new token.')) return
                    const r = await regenerateToken(selectedMachine.id, false)
                    setRegenToken(r.token)
                  }}>
                    <RefreshCw className="mr-1.5 h-3 w-3" /> Rotate Token
                  </Button>
                  <Button variant="outline" size="sm"
                    disabled={selectedMachine.status === 'draining' || selectedMachine.status === 'offline'}
                    onClick={async () => {
                      if (!confirm('Drain this machine? No new agents will be placed.')) return
                      await drainMachine(selectedMachine.id)
                    }}
                  >
                    <PauseCircle className="mr-1.5 h-3 w-3" /> Drain
                  </Button>
                  <Button variant="outline" size="sm"
                    className="text-red-500 hover:text-red-600 border-red-200 hover:border-red-300"
                    onClick={async () => {
                      if (!confirm(`Delete machine "${selectedMachine.name}"? This cannot be undone.`)) return
                      try {
                        await deleteMachine(selectedMachine.id, machineAgents.length > 0)
                        setSelectedId(null)
                      } catch (e) {
                        alert(`Failed to delete machine: ${(e as Error).message}`)
                      }
                    }}
                  >
                    <Trash2 className="mr-1.5 h-3 w-3" /> Delete Machine
                  </Button>
                </div>
              </div>
            </div>

            {/* History */}
            <div className="rounded-[var(--radius-lg)] border border-[rgba(0,0,0,0.1)] bg-white shadow-[var(--shadow-card)]">
              <div className="px-4 py-2.5 border-b border-[var(--color-border)]">
                <h3 className="text-xs font-semibold uppercase tracking-wider text-[var(--color-foreground-muted)]">History</h3>
              </div>
              <div className="px-4 py-3 max-h-64 overflow-y-auto">
                {machineActivity.length === 0 ? (
                  <p className="text-caption text-[var(--color-foreground-muted)]">No activity yet</p>
                ) : (
                  <div className="space-y-1.5">
                    {machineActivity.map(evt => (
                      <div key={evt.id} className="flex items-center gap-2 text-xs">
                        <span className={`inline-block h-1.5 w-1.5 rounded-full ${
                          evt.event_type === 'online' ? 'bg-[var(--color-success)]'
                            : evt.event_type === 'offline' ? 'bg-[var(--color-foreground-subtle)]'
                            : 'bg-[var(--color-warning)]'
                        }`} />
                        <span className="font-medium text-[var(--color-foreground)]">{evt.event_type}</span>
                        <span className="text-[var(--color-foreground-muted)]">
                          {new Date(evt.timestamp).toLocaleString()}
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
            <DialogTitle>Register Machine</DialogTitle>
            <DialogDescription>Add a new machine to host agents.</DialogDescription>
          </DialogHeader>
          <div className="space-y-4 py-2">
            <div className="space-y-2">
              <Label>Name</Label>
              <Input placeholder="e.g. gpu-worker-01" value={regName} onChange={e => setRegName(e.target.value)} />
            </div>
            <div className="space-y-2">
              <Label>Hostname</Label>
              <Input placeholder="e.g. worker01.example.com" value={regHostname} onChange={e => setRegHostname(e.target.value)} />
            </div>
          </div>
          <DialogFooter>
            <Button onClick={handleRegister} disabled={regLoading || !regName.trim() || !regHostname.trim()}>
              {regLoading ? 'Registering...' : 'Register'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* ── Token Display Dialog ── */}
      <Dialog open={tokenDialogOpen} onOpenChange={setTokenDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Machine Registered</DialogTitle>
            <DialogDescription>Copy the token — it's shown only once.</DialogDescription>
          </DialogHeader>
          <div className="space-y-3 py-2">
            <div className="rounded-[var(--radius-md)] border border-[color:color-mix(in_srgb,var(--color-warning)_25%,transparent)] bg-[color:color-mix(in_srgb,var(--color-warning)_8%,transparent)] p-3 text-sm text-[var(--color-warning)]">
              This token is shown only once. Copy it now.
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
            <Button onClick={() => setTokenDialogOpen(false)}>Done</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* ── Create Agent on Machine Dialog ── */}
      <Dialog open={createAgentOpen} onOpenChange={setCreateAgentOpen}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>Create Agent on {selectedMachine?.name}</DialogTitle>
            <DialogDescription>The agent will be placed on this machine.</DialogDescription>
          </DialogHeader>
          <div className="space-y-4 py-2">
            <div className="space-y-2">
              <Label>Name</Label>
              <Input placeholder="Agent name" value={agentName} onChange={e => setAgentName(e.target.value)} />
            </div>
            <div className="space-y-2">
              <Label>Engine</Label>
              <select value={agentEngine} onChange={e => setAgentEngine(e.target.value)} className={selectCSS}>
                <option value="" disabled>Select engine</option>
                {machineEngines.map(e => (
                  <option key={e.engine} value={e.engine}>
                    {ENGINE_LABELS[e.engine] ?? e.engine}
                  </option>
                ))}
              </select>
            </div>
            {agentCatalog && agentCatalog.models.length > 0 && (
              <div className="space-y-2">
                <Label>Model</Label>
                <select value={agentModel} onChange={e => setAgentModel(e.target.value)} className={selectCSS}>
                  {agentCatalog.default_model && (
                    <option value="">Default ({agentCatalog.default_model})</option>
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
                          <optgroup label="Built-in">
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
                {agentEngine === 'codex-extra' && agentCatalog.models.length === 0 && (
                  <p className="text-xs text-[var(--color-foreground-muted)]">
                    No models registered. Add one in Admin › LLM Gateway first.
                  </p>
                )}
              </div>
            )}
            {agentEngine === 'codex-extra' && (!agentCatalog || agentCatalog.models.length === 0) && (
              <p className="text-xs text-[var(--color-foreground-muted)]">
                This engine routes through the embedded LLM Gateway. Register a model in Admin › LLM Gateway to pick it here.
              </p>
            )}
            {agentReasoningLevels.length > 0 && (
              <div className="space-y-2">
                <Label>Reasoning Effort</Label>
                <select value={agentReasoning} onChange={e => setAgentReasoning(e.target.value)} className={selectCSS}>
                  <option value="">Default</option>
                  {agentReasoningLevels.map(level => (
                    <option key={level} value={level}>
                      {level.charAt(0).toUpperCase() + level.slice(1)}
                    </option>
                  ))}
                </select>
              </div>
            )}
            <div className="space-y-2">
              <Label>Rooms (optional)</Label>
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
                  No rooms selected — agent will stay idle until assigned to a room.
                </p>
              )}
            </div>
          </div>
          <DialogFooter>
            <Button onClick={handleCreateAgent} disabled={creating || !agentName.trim() || !agentEngine}>
              {creating ? 'Creating...' : 'Create Agent'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* #158 — unified per-agent settings dialog replaces the four
          separate dialogs (rooms / edit / history / avatar). */}
      <AgentSettingsDialog
        agent={settingsAgent}
        open={settingsOpen}
        onOpenChange={setSettingsOpen}
        fetchAgentFiles={fetchAgentFiles}
        updateAgent={updateAgent}
        upsertAgentFile={upsertAgentFile}
        deleteAgentFile={deleteAgentFile}
        fetchAttachedSkills={fetchAttachedSkills}
        fetchSkillPreview={fetchSkillPreview}
        fetchEngineCatalog={fetchEngineCatalog}
        onRoomsChange={() => selectedId && fetchDetail(selectedId)}
      />
    </div>
  )
}
