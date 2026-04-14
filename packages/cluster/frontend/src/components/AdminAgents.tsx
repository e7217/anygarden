import { useState, useCallback, useEffect, useMemo } from 'react'
import { useAgents, type Agent, type EngineCatalog } from '@/hooks/useAgents'
import { useMachines } from '@/hooks/useMachines'
import { useRooms } from '@/hooks/useRooms'
import { apiFetch } from '@/lib/api'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Table, TableHeader, TableBody, TableRow, TableHead, TableCell,
} from '@/components/ui/table'
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter, DialogTrigger, DialogDescription,
} from '@/components/ui/dialog'
import { Plus, Trash2, Play, Square, DoorOpen, X, FileCog, History } from 'lucide-react'
import AgentEditDialog from '@/components/AgentEditDialog'

// Engine display names — used when the API returns a raw engine key.
const ENGINE_LABELS: Record<string, string> = {
  'codex': 'Codex',
  'claude-code': 'Claude Code',
  'openai': 'OpenAI',
  'anthropic': 'Anthropic',
  'openhands': 'OpenHands',
  'deep-agents': 'Deep Agents',
}

function stateBadgeClass(state: string) {
  switch (state) {
    case 'running':
      return 'bg-[color:color-mix(in_srgb,var(--color-success)_10%,transparent)] text-[var(--color-success)] border-[color:color-mix(in_srgb,var(--color-success)_25%,transparent)]'
    case 'starting':
    case 'pending':
      return 'bg-[color:color-mix(in_srgb,var(--color-warning)_10%,transparent)] text-[var(--color-warning)] border-[color:color-mix(in_srgb,var(--color-warning)_25%,transparent)]'
    case 'crashed':
      return 'bg-[color:color-mix(in_srgb,var(--color-warning)_15%,transparent)] text-[var(--color-warning)] border-[color:color-mix(in_srgb,var(--color-warning)_30%,transparent)]'
    case 'stopping':
      return 'bg-[color:color-mix(in_srgb,var(--color-warning)_10%,transparent)] text-[var(--color-warning)] border-[color:color-mix(in_srgb,var(--color-warning)_25%,transparent)]'
    case 'idle':
    case 'stopped':
    default:
      return 'bg-[var(--color-surface-alt)] text-[var(--color-foreground-muted)] border-[var(--color-border)]'
  }
}

interface RoomInfo { id: string; name: string; project_id: string; }

export default function AdminAgents() {
  const {
    agents,
    availableEngines,
    fetchAvailableEngines,
    fetchEngineCatalog,
    createAgent,
    deleteAgent,
    startAgent,
    stopAgent,
    updateAgent,
    fetchAgentFiles,
    upsertAgentFile,
    deleteAgentFile,
  } = useAgents()
  const { machines, status: machinesStatus } = useMachines()
  // Projects + rooms are provided app-wide by ``RoomsProvider`` so
  // we can show a multi-select of all known rooms in the Create
  // Agent dialog without another round-trip. ``status`` +
  // ``refetch`` let the dialog distinguish "still loading" from
  // "really empty" — without that distinction a slow / stale
  // fetch would silently let the admin create a roomless agent
  // (the exact pending trap we're trying to prevent).
  const {
    projects,
    rooms: roomsByProject,
    status: roomsStatus,
    refetch: refetchRooms,
  } = useRooms()

  // Build a machine_id → friendly name map so the Machine column can
  // show "home" instead of "bde990d3-bc9f-...". We only treat a
  // missing id as "deleted" when the machines fetch has actually
  // completed successfully — otherwise a still-loading or errored
  // fetch would label every agent as pointing at a deleted machine.
  const machineNameById = useMemo(() => {
    const map = new Map<string, string>()
    for (const m of machines) map.set(m.id, m.name)
    return map
  }, [machines])

  const renderMachineCell = (machineId: string | null | undefined) => {
    if (!machineId) return '-'
    const name = machineNameById.get(machineId)
    if (name) return name
    // Still loading: don't accuse a deletion we can't verify yet.
    if (machinesStatus === 'idle' || machinesStatus === 'loading') {
      return <span className="opacity-60">…</span>
    }
    // Fetch failed: show the raw id prefix without claiming deletion.
    if (machinesStatus === 'error') {
      return (
        <span title="Could not load machines list">
          {machineId.slice(0, 8)}…
        </span>
      )
    }
    // Fetch completed successfully and the id really isn't there.
    return `${machineId.slice(0, 8)}… (deleted)`
  }

  const [dialogOpen, setDialogOpen] = useState(false)
  const [name, setName] = useState('')
  const [engine, setEngine] = useState('')
  const [reasoningEffort, setReasoningEffort] = useState('')
  const [model, setModel] = useState('')
  const [engineCatalog, setEngineCatalog] = useState<EngineCatalog | null>(null)
  const [loading, setLoading] = useState(false)

  // When the engine changes in the Create dialog, re-fetch the model
  // catalog for that engine. Reset model + reasoning to "default" so
  // a stale selection from the previous engine can't leak through
  // (e.g. picking "xhigh" on codex then switching to gemini).
  useEffect(() => {
    if (!engine) {
      setEngineCatalog(null)
      setModel('')
      setReasoningEffort('')
      return
    }
    let cancelled = false
    setModel('')
    setReasoningEffort('')
    fetchEngineCatalog(engine).then(cat => {
      if (!cancelled) setEngineCatalog(cat)
    })
    return () => { cancelled = true }
  }, [engine, fetchEngineCatalog])

  // Reasoning levels to offer. If a specific model is selected and
  // narrows the engine's default set, use its per-model list;
  // otherwise fall back to the engine-level list.
  const reasoningLevels = useMemo(() => {
    if (!engineCatalog) return []
    if (model) {
      const m = engineCatalog.models.find(x => x.id === model)
      if (m && m.reasoning_levels.length > 0) return m.reasoning_levels
    }
    return engineCatalog.reasoning_levels
  }, [engineCatalog, model])
  // Initial room assignments for the new agent. Multi-select
  // because the server accepts ``rooms: string[]`` and because
  // without at least one room the lifecycle's
  // ``spawn_refused_no_rooms`` guard leaves the agent pinned at
  // ``pending``. The dialog still allows zero rooms (admin may
  // want to pre-create an agent before deciding which rooms it
  // joins) but shows an explicit warning in that case.
  const [selectedRoomIds, setSelectedRoomIds] = useState<Set<string>>(new Set())

  // Manage rooms dialog state
  const [roomsDialogOpen, setRoomsDialogOpen] = useState(false)
  const [selectedAgentId, setSelectedAgentId] = useState<string | null>(null)
  const [assignedRooms, setAssignedRooms] = useState<RoomInfo[]>([])
  const [availableRooms, setAvailableRooms] = useState<RoomInfo[]>([])
  const [roomsLoading, setRoomsLoading] = useState(false)

  // Edit manifest (AGENTS.md + agent_files) dialog state. We keep a
  // snapshot of the row the dialog was opened against so late edits
  // by another admin don't blow up the currently-open form.
  const [editDialogOpen, setEditDialogOpen] = useState(false)
  const [editingAgent, setEditingAgent] = useState<Agent | null>(null)

  const handleEditManifest = (agent: Agent) => {
    setEditingAgent(agent)
    setEditDialogOpen(true)
  }

  const toggleSelectedRoom = (roomId: string) => {
    setSelectedRoomIds(prev => {
      const next = new Set(prev)
      if (next.has(roomId)) next.delete(roomId)
      else next.add(roomId)
      return next
    })
  }

  // Reset transient dialog state on close so the next open starts
  // clean (name cleared, engine back to default, room selection
  // cleared). Called from both the success path and the
  // ``onOpenChange`` close path.
  const resetCreateDialog = () => {
    setName('')
    setEngine('')
    setReasoningEffort('')
    setModel('')
    setEngineCatalog(null)
    setSelectedRoomIds(new Set())
  }

  const handleCreate = async () => {
    if (!name.trim() || !engine) return
    setLoading(true)
    try {
      await createAgent({
        name: name.trim(),
        engine,
        rooms: Array.from(selectedRoomIds),
        ...(reasoningEffort ? { reasoning_effort: reasoningEffort } : {}),
        ...(model ? { model } : {}),
      })
      resetCreateDialog()
      setDialogOpen(false)
    } catch { /* ignore */ }
    setLoading(false)
  }

  const handleDelete = async (id: string) => {
    if (!confirm('Are you sure you want to delete this agent?')) return
    await deleteAgent(id)
  }

  const handleStart = async (id: string) => {
    try { await startAgent(id) } catch { /* ignore */ }
  }

  const fetchRoomsForAgent = useCallback(async (agentId: string) => {
    setRoomsLoading(true)
    try {
      // Fetch assigned rooms for this agent (server returns {room_id, room_name, role})
      const assignedResp = await apiFetch(`/api/v1/agents/${agentId}/rooms`)
      const rawAssigned = assignedResp.ok ? await assignedResp.json() : []
      const assigned: RoomInfo[] = rawAssigned.map((r: { room_id: string; room_name: string }) => ({
        id: r.room_id,
        name: r.room_name,
        project_id: '',
      }))
      setAssignedRooms(assigned)

      // Fetch all available rooms from projects
      const projResp = await apiFetch('/api/v1/projects')
      const projects = projResp.ok ? await projResp.json() : []
      const allRooms: RoomInfo[] = []
      for (const proj of projects) {
        const roomResp = await apiFetch(`/api/v1/rooms?project_id=${proj.id}`)
        if (roomResp.ok) {
          const rooms = await roomResp.json()
          allRooms.push(...rooms.map((r: RoomInfo) => ({ id: r.id, name: r.name, project_id: r.project_id })))
        }
      }
      // Filter out already-assigned rooms
      const assignedIds = new Set(assigned.map((r: RoomInfo) => r.id))
      setAvailableRooms(allRooms.filter(r => !assignedIds.has(r.id)))
    } catch { /* ignore */ }
    setRoomsLoading(false)
  }, [])

  // ── History dialog ──
  const [historyOpen, setHistoryOpen] = useState(false)
  const [historyAgentId, setHistoryAgentId] = useState<string | null>(null)
  const [historyAgentName, setHistoryAgentName] = useState('')
  const [activityLogs, setActivityLogs] = useState<{ id: string; event_type: string; timestamp: string; details: Record<string, unknown> | null }[]>([])

  const handleShowHistory = async (agentId: string, name: string) => {
    setHistoryAgentId(agentId)
    setHistoryAgentName(name)
    setHistoryOpen(true)
    const resp = await apiFetch(`/api/v1/agents/${agentId}/activity?limit=50`)
    if (resp.ok) setActivityLogs(await resp.json())
  }

  const handleManageRooms = (agentId: string) => {
    setSelectedAgentId(agentId)
    setRoomsDialogOpen(true)
    fetchRoomsForAgent(agentId)
  }

  const handleAddRoom = async (roomId: string) => {
    if (!selectedAgentId) return
    await apiFetch(`/api/v1/agents/${selectedAgentId}/rooms`, {
      method: 'POST',
      body: JSON.stringify({ room_id: roomId }),
    })
    await fetchRoomsForAgent(selectedAgentId)
  }

  const handleRemoveRoom = async (roomId: string) => {
    if (!selectedAgentId) return
    await apiFetch(`/api/v1/agents/${selectedAgentId}/rooms/${roomId}`, {
      method: 'DELETE',
    })
    await fetchRoomsForAgent(selectedAgentId)
  }

  return (
    <div className="p-4 md:p-6">
      <div className="mb-6 flex flex-col items-start justify-between gap-4 md:mb-8 md:flex-row md:items-start">
        <div className="space-y-1">
          <h1 className="text-card-title text-[var(--color-foreground)]">Agents</h1>
          <p className="text-caption text-[var(--color-foreground-muted)]">
            Manage AI agents in your workspace.
          </p>
        </div>
        <Dialog
          open={dialogOpen}
          onOpenChange={(next) => {
            setDialogOpen(next)
            if (next) {
              // Every open pulls the freshest data so the dialog
              // never shows stale engines or rooms.
              void fetchAvailableEngines()
              void refetchRooms()
            } else {
              resetCreateDialog()
            }
          }}
        >
          <DialogTrigger asChild>
            <Button variant="outline" size="sm" title="Create agents from the Machines tab">
              <Plus className="mr-2 h-4 w-4" />
              New Agent
            </Button>
          </DialogTrigger>
          <DialogContent className="max-w-md">
            <DialogHeader>
              <DialogTitle>Create Agent</DialogTitle>
              <DialogDescription>
                Define a new agent and the engine that powers it.
              </DialogDescription>
            </DialogHeader>
            <div className="space-y-4 py-2">
              <div className="space-y-2">
                <Label htmlFor="agent-name">Name</Label>
                <Input
                  id="agent-name"
                  placeholder="Agent name"
                  value={name}
                  onChange={e => setName(e.target.value)}
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="agent-engine">Engine</Label>
                {availableEngines.length === 0 ? (
                  <p className="text-caption text-[var(--color-foreground-muted)]">
                    No engines available — connect a machine first.
                  </p>
                ) : (
                  <select
                    id="agent-engine"
                    value={engine}
                    onChange={e => setEngine(e.target.value)}
                    className="flex h-9 w-full rounded-[var(--radius-xs)] border border-[var(--color-border-strong)] bg-[var(--color-background)] px-3 py-1 text-sm text-[var(--color-foreground)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-brand-focus)]"
                  >
                    <option value="" disabled>Select engine</option>
                    {availableEngines.map(e => (
                      <option key={e.engine} value={e.engine}>
                        {ENGINE_LABELS[e.engine] ?? e.engine} ({e.machine_count} machine{e.machine_count > 1 ? 's' : ''})
                      </option>
                    ))}
                  </select>
                )}
              </div>
              {engineCatalog && engineCatalog.models.length > 0 && (
                <div className="space-y-2">
                  <Label htmlFor="agent-model">Model</Label>
                  <select
                    id="agent-model"
                    value={model}
                    onChange={e => setModel(e.target.value)}
                    className="flex h-9 w-full rounded-[var(--radius-xs)] border border-[var(--color-border-strong)] bg-[var(--color-background)] px-3 py-1 text-sm text-[var(--color-foreground)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-brand-focus)]"
                  >
                    <option value="">Default ({engineCatalog.default_model})</option>
                    {engineCatalog.models.map(m => (
                      <option key={m.id} value={m.id}>{m.label}</option>
                    ))}
                  </select>
                </div>
              )}
              {reasoningLevels.length > 0 && (
                <div className="space-y-2">
                  <Label htmlFor="agent-reasoning">Reasoning Effort</Label>
                  <select
                    id="agent-reasoning"
                    value={reasoningEffort}
                    onChange={e => setReasoningEffort(e.target.value)}
                    className="flex h-9 w-full rounded-[var(--radius-xs)] border border-[var(--color-border-strong)] bg-[var(--color-background)] px-3 py-1 text-sm text-[var(--color-foreground)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-brand-focus)]"
                  >
                    <option value="">Default</option>
                    {reasoningLevels.map(level => (
                      <option key={level} value={level}>
                        {level.charAt(0).toUpperCase() + level.slice(1)}
                      </option>
                    ))}
                  </select>
                </div>
              )}
              <div className="space-y-2">
                <Label>Initial rooms</Label>
                {roomsStatus === 'idle' || roomsStatus === 'loading' ? (
                  <p
                    className="text-caption text-[var(--color-foreground-muted)]"
                    data-testid="agent-create-rooms-loading"
                  >
                    Loading rooms…
                  </p>
                ) : roomsStatus === 'error' ? (
                  <div
                    className="space-y-2 rounded-[var(--radius-md)] border border-[color:color-mix(in_srgb,var(--color-warning)_30%,transparent)] bg-[color:color-mix(in_srgb,var(--color-warning)_10%,transparent)] px-3 py-2 text-sm text-[var(--color-warning)]"
                    data-testid="agent-create-rooms-error"
                  >
                    <p>Failed to load rooms from the server.</p>
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => void refetchRooms()}
                    >
                      Retry
                    </Button>
                  </div>
                ) : projects.length === 0 ? (
                  <p
                    className="text-caption text-[var(--color-foreground-subtle)]"
                    data-testid="agent-create-rooms-empty"
                  >
                    No projects yet. Create a project and at least one room before spawning agents.
                  </p>
                ) : (
                  <div className="max-h-56 overflow-y-auto rounded-[var(--radius-md)] border border-[var(--color-border)]">
                    {projects.map(project => {
                      const rs = roomsByProject[project.id] ?? []
                      if (rs.length === 0) return null
                      return (
                        <div key={project.id} className="py-1">
                          <div className="px-3 py-1 text-badge uppercase tracking-wider text-[var(--color-foreground-muted)]">
                            {project.name}
                          </div>
                          {rs.map(room => (
                            <label
                              key={room.id}
                              className="flex items-center gap-2 px-3 py-1.5 text-sm hover:bg-[var(--color-surface-alt)] cursor-pointer"
                              data-testid={`agent-create-room-${room.id}`}
                            >
                              <input
                                type="checkbox"
                                checked={selectedRoomIds.has(room.id)}
                                onChange={() => toggleSelectedRoom(room.id)}
                              />
                              <span className="truncate">{room.name}</span>
                              {room.parent_room_id ? (
                                <span className="ml-1 text-xs text-[var(--color-foreground-muted)]">
                                  (sub-room)
                                </span>
                              ) : null}
                            </label>
                          ))}
                        </div>
                      )
                    })}
                  </div>
                )}
                {roomsStatus === 'ready' &&
                projects.length > 0 &&
                selectedRoomIds.size === 0 ? (
                  <p className="text-caption text-[var(--color-foreground-muted)]">
                    No rooms selected — agent will be created but won't start until assigned to a room.
                  </p>
                ) : null}
              </div>
            </div>
            <DialogFooter>
              <Button
                onClick={handleCreate}
                disabled={loading || !name.trim() || !engine}
                data-testid="agent-create-submit"
              >
                {loading ? 'Creating...' : 'Create'}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </div>

      {/* Manage Rooms Dialog */}
      <Dialog open={roomsDialogOpen} onOpenChange={setRoomsDialogOpen}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>Manage Rooms</DialogTitle>
            <DialogDescription>
              Assign or remove this agent from rooms.
            </DialogDescription>
          </DialogHeader>
          {roomsLoading ? (
            <div className="py-8 text-center text-caption text-[var(--color-foreground-muted)]">
              Loading rooms...
            </div>
          ) : (
            <div className="space-y-5 py-2">
              <div>
                <h3 className="text-badge uppercase text-[var(--color-foreground-muted)] mb-2 tracking-wider">
                  Assigned Rooms
                </h3>
                {assignedRooms.length === 0 ? (
                  <p className="text-caption text-[var(--color-foreground-subtle)]">No rooms assigned</p>
                ) : (
                  <div className="space-y-2">
                    {assignedRooms.map(room => (
                      <div
                        key={room.id}
                        className="flex items-center justify-between rounded-[var(--radius-md)] bg-[var(--color-surface-alt)] border border-[var(--color-border)] px-3 py-2"
                      >
                        <span className="text-sm font-medium text-[var(--color-foreground)]">{room.name}</span>
                        <Button
                          variant="ghost"
                          size="icon"
                          onClick={() => handleRemoveRoom(room.id)}
                          title="Remove room"
                        >
                          <X className="h-4 w-4 text-[var(--color-warning)]" />
                        </Button>
                      </div>
                    ))}
                  </div>
                )}
              </div>
              <div>
                <h3 className="text-badge uppercase text-[var(--color-foreground-muted)] mb-2 tracking-wider">
                  Available Rooms
                </h3>
                {availableRooms.length === 0 ? (
                  <p className="text-caption text-[var(--color-foreground-subtle)]">No available rooms</p>
                ) : (
                  <div className="space-y-2">
                    {availableRooms.map(room => (
                      <div
                        key={room.id}
                        className="flex items-center justify-between rounded-[var(--radius-md)] bg-[var(--color-surface-alt)] border border-[var(--color-border)] px-3 py-2"
                      >
                        <span className="text-sm font-medium text-[var(--color-foreground)]">{room.name}</span>
                        <Button
                          variant="ghost"
                          size="icon"
                          onClick={() => handleAddRoom(room.id)}
                          title="Add room"
                        >
                          <Plus className="h-4 w-4 text-[var(--color-success)]" />
                        </Button>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>
          )}
          <DialogFooter>
            <Button variant="ghost" onClick={() => setRoomsDialogOpen(false)}>Close</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {agents.length === 0 ? (
        <div className="bg-[var(--color-surface-alt)] rounded-[var(--radius-lg)] p-12 text-center">
          <p className="text-body-lg text-[var(--color-foreground)]">No agents yet</p>
          <p className="text-caption text-[var(--color-foreground-muted)] mt-1">
            Create an agent to get started.
          </p>
        </div>
      ) : (
        <div className="overflow-x-auto rounded-[var(--radius-lg)] border border-[var(--color-border)] bg-[var(--color-background)]">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Name</TableHead>
                <TableHead>Engine</TableHead>
                <TableHead>Desired State</TableHead>
                <TableHead>Actual State</TableHead>
                <TableHead>Machine</TableHead>
                <TableHead className="w-[160px] text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {agents.map(agent => (
                <TableRow
                  key={agent.id}
                  className="hover:bg-[var(--color-surface-alt)] transition-colors"
                >
                  <TableCell className="font-medium text-[var(--color-foreground)]">{agent.name}</TableCell>
                  <TableCell>
                    <Badge
                      variant="outline"
                      className="bg-[var(--color-brand-tint-bg)] text-[var(--color-brand-tint-text)] border-[color:color-mix(in_srgb,var(--color-brand)_20%,transparent)]"
                    >
                      {agent.engine}
                    </Badge>
                  </TableCell>
                  <TableCell>
                    <Badge variant="outline" className={stateBadgeClass(agent.desired_state)}>
                      {agent.desired_state}
                    </Badge>
                  </TableCell>
                  <TableCell>
                    {(() => {
                      // Surface ``last_crash_reason`` as a native
                      // tooltip on pending/crashed state badges.
                      // Any state is eligible but non-failure
                      // states rarely carry a reason, so we only
                      // pad the style hint for the failure set.
                      const failure =
                        agent.actual_state === 'pending' ||
                        agent.actual_state === 'crashed'
                      const hasReason =
                        !!agent.last_crash_reason &&
                        agent.last_crash_reason.length > 0
                      const tooltip = hasReason
                        ? `Last reason: ${agent.last_crash_reason}`
                        : undefined
                      return (
                        <Badge
                          variant="outline"
                          className={
                            stateBadgeClass(agent.actual_state) +
                            (failure && hasReason ? ' cursor-help' : '')
                          }
                          title={tooltip}
                          data-testid={`agent-state-badge-${agent.id}`}
                        >
                          {agent.actual_state}
                          {failure && hasReason ? (
                            <span className="ml-1 opacity-70" aria-hidden>ⓘ</span>
                          ) : null}
                        </Badge>
                      )
                    })()}
                  </TableCell>
                  <TableCell className="text-sm text-[var(--color-foreground-muted)]">
                    {renderMachineCell(agent.placed_on_machine_id)}
                  </TableCell>
                  <TableCell>
                    <div className="flex justify-end gap-1">
                      {agent.actual_state === 'running' || agent.actual_state === 'starting' ? (
                        <Button
                          variant="ghost"
                          size="icon"
                          onClick={() => stopAgent(agent.id)}
                          title="Stop agent"
                        >
                          <Square className="h-4 w-4 text-[var(--color-warning)]" />
                        </Button>
                      ) : (
                        <Button
                          variant="ghost"
                          size="icon"
                          onClick={() => handleStart(agent.id)}
                          title="Start agent"
                        >
                          <Play className="h-4 w-4 text-[var(--color-success)]" />
                        </Button>
                      )}
                      <Button
                        variant="ghost"
                        size="icon"
                        onClick={() => handleEditManifest(agent)}
                        title="Edit manifest (AGENTS.md + files)"
                        data-testid={`agent-edit-manifest-${agent.id}`}
                      >
                        <FileCog className="h-4 w-4 text-[var(--color-foreground-muted)]" />
                      </Button>
                      <Button
                        variant="ghost"
                        size="icon"
                        onClick={() => handleManageRooms(agent.id)}
                        title="Manage rooms"
                      >
                        <DoorOpen className="h-4 w-4 text-[var(--color-foreground-muted)]" />
                      </Button>
                      <Button
                        variant="ghost"
                        size="icon"
                        onClick={() => handleShowHistory(agent.id, agent.name)}
                        title="Activity history"
                      >
                        <History className="h-4 w-4 text-[var(--color-foreground-muted)]" />
                      </Button>
                      <Button
                        variant="ghost"
                        size="icon"
                        onClick={() => handleDelete(agent.id)}
                        title="Delete agent"
                      >
                        <Trash2 className="h-4 w-4 text-[var(--color-warning)]" />
                      </Button>
                    </div>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}

      <AgentEditDialog
        agent={editingAgent}
        open={editDialogOpen}
        onOpenChange={setEditDialogOpen}
        fetchAgentFiles={fetchAgentFiles}
        updateAgent={updateAgent}
        upsertAgentFile={upsertAgentFile}
        deleteAgentFile={deleteAgentFile}
      />

      {/* History Dialog */}
      <Dialog open={historyOpen} onOpenChange={setHistoryOpen}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>Activity — {historyAgentName}</DialogTitle>
          </DialogHeader>
          <div className="max-h-80 overflow-y-auto space-y-1.5 py-2">
            {activityLogs.length === 0 ? (
              <p className="text-caption text-[var(--color-foreground-muted)]">No activity yet</p>
            ) : (
              activityLogs.map(evt => (
                <div key={evt.id} className="flex items-center gap-2 text-xs">
                  <span className={`inline-block h-1.5 w-1.5 rounded-full ${
                    evt.event_type === 'start_requested' ? 'bg-[var(--color-success)]'
                      : evt.event_type === 'stop_requested' ? 'bg-[var(--color-foreground-subtle)]'
                      : 'bg-[var(--color-warning)]'
                  }`} />
                  <span className="font-medium text-[var(--color-foreground)]">{evt.event_type}</span>
                  <span className="text-[var(--color-foreground-muted)]">
                    {new Date(evt.timestamp).toLocaleString()}
                  </span>
                </div>
              ))
            )}
          </div>
        </DialogContent>
      </Dialog>
    </div>
  )
}
