import { useState, useEffect, useCallback } from 'react'
import { useMachines } from '@/hooks/useMachines'
import { useAgents } from '@/hooks/useAgents'
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
} from 'lucide-react'
import { apiFetch } from '@/lib/api'

// ── Types ──────────────────────────────────────────────────────────

interface MachineAgent {
  id: string; name: string; engine: string
  desired_state: string; actual_state: string
  reasoning_effort?: string | null; rooms: string[]
}

interface MachineEngineInfo {
  engine: string; version?: string | null
}

const ENGINE_LABELS: Record<string, string> = {
  'codex': 'Codex CLI',
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
  const { createAgent, agents, startAgent, stopAgent } = useAgents()
  const { projects, rooms: roomsByProject } = useRooms()

  const [selectedId, setSelectedId] = useState<string | null>(null)
  const selectedMachine = machines.find(m => m.id === selectedId) ?? null

  // Auto-select first machine
  useEffect(() => {
    if (!selectedId && machines.length > 0) setSelectedId(machines[0].id)
  }, [machines, selectedId])

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
  const [regMaxAgents, setRegMaxAgents] = useState('4')
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
        max_agents: parseInt(regMaxAgents) || 4,
      })
      setTokenResult(result)
      setRegName(''); setRegHostname(''); setRegMaxAgents('4')
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
  const [agentRooms, setAgentRooms] = useState<Set<string>>(new Set())
  const [creating, setCreating] = useState(false)

  const handleCreateAgent = async () => {
    if (!agentName.trim() || !agentEngine || !selectedId) return
    setCreating(true)
    try {
      await createAgent({
        name: agentName.trim(),
        engine: agentEngine,
        rooms: Array.from(agentRooms),
        ...(agentReasoning ? { reasoning_effort: agentReasoning } : {}),
      })
      setAgentName(''); setAgentEngine(''); setAgentReasoning(''); setAgentRooms(new Set())
      setCreateAgentOpen(false)
      fetchDetail(selectedId)
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
          ) : machines.map(m => (
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
        </div>
      </div>

      {/* ── Right: Machine Detail ── */}
      <div className="flex-1 overflow-y-auto p-6">
        {!selectedMachine ? (
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
                  <span className="text-[var(--color-foreground-muted)]">Capacity</span>
                  <p className="text-[var(--color-foreground)] font-medium">
                    {machineAgents.filter(a => a.actual_state === 'running' || a.actual_state === 'starting' || a.actual_state === 'pending').length} / {selectedMachine.max_agents} agents
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
                  const isStopped = agent.actual_state === 'stopped' || agent.actual_state === 'idle' || agent.actual_state === 'crashed'
                  const isRunning = agent.actual_state === 'running' || agent.actual_state === 'starting'
                  return (
                    <div key={agent.id} className={`flex items-center justify-between px-4 py-3 ${isStopped ? 'opacity-50' : ''}`}>
                      <div className="min-w-0">
                        <div className="flex items-center gap-2">
                          <span className="text-sm font-medium text-[var(--color-foreground)] truncate">{agent.name}</span>
                          <span className="flex items-center gap-1 text-xs text-[var(--color-foreground-muted)]">
                            <span className={`inline-block h-1.5 w-1.5 rounded-full ${statusDot(agent.actual_state)}`} />
                            {agent.actual_state}
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
                      <div className="flex items-center gap-1">
                        {isRunning ? (
                          <Button variant="ghost" size="icon" onClick={() => { stopAgent(agent.id); setTimeout(() => fetchDetail(selectedId!), 500) }} title="Stop">
                            <Square className="h-3.5 w-3.5 text-red-500" />
                          </Button>
                        ) : (
                          <Button variant="ghost" size="icon" onClick={() => { startAgent(agent.id); setTimeout(() => fetchDetail(selectedId!), 500) }} title="Start">
                            <Play className="h-3.5 w-3.5 text-[var(--color-success)]" />
                          </Button>
                        )}
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
            <div className="space-y-2">
              <Label>Max Agents</Label>
              <Input type="number" min="1" value={regMaxAgents} onChange={e => setRegMaxAgents(e.target.value)} />
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
            <div className="space-y-2">
              <Label>Reasoning Effort</Label>
              <select value={agentReasoning} onChange={e => setAgentReasoning(e.target.value)} className={selectCSS}>
                <option value="">Default</option>
                <option value="low">Low</option>
                <option value="medium">Medium</option>
                <option value="high">High</option>
              </select>
            </div>
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
    </div>
  )
}
