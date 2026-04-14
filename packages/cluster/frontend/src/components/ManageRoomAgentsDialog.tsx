import { useState } from 'react'
import { useAgents } from '@/hooks/useAgents'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter,
} from '@/components/ui/dialog'
import { Bot, Plus, Minus, Loader2 } from 'lucide-react'

interface ManageRoomAgentsDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  roomId: string
  participantAgentIds: Set<string>
  onChange: () => void
}

function stateBadgeClass(state: string) {
  switch (state) {
    case 'running':
      return 'bg-[color:color-mix(in_srgb,var(--color-success)_10%,transparent)] text-[var(--color-success)] border-[color:color-mix(in_srgb,var(--color-success)_25%,transparent)]'
    case 'starting':
    case 'pending':
    case 'stopping':
      return 'bg-[color:color-mix(in_srgb,var(--color-warning)_10%,transparent)] text-[var(--color-warning)] border-[color:color-mix(in_srgb,var(--color-warning)_25%,transparent)]'
    case 'crashed':
      return 'bg-[color:color-mix(in_srgb,var(--color-warning)_15%,transparent)] text-[var(--color-warning)] border-[color:color-mix(in_srgb,var(--color-warning)_30%,transparent)]'
    case 'stopped':
    case 'idle':
    default:
      return 'bg-[var(--color-surface-alt)] text-[var(--color-foreground-muted)] border-[var(--color-border)]'
  }
}

export default function ManageRoomAgentsDialog({
  open, onOpenChange, roomId, participantAgentIds, onChange,
}: ManageRoomAgentsDialogProps) {
  const { agents, addAgentToRoom, removeAgentFromRoom } = useAgents()
  const [busyAgentId, setBusyAgentId] = useState<string | null>(null)
  const [error, setError] = useState('')

  const handleAdd = async (agentId: string) => {
    setBusyAgentId(agentId)
    setError('')
    try {
      await addAgentToRoom(agentId, roomId)
      onChange()
    } catch (e) {
      setError((e as Error).message)
    }
    setBusyAgentId(null)
  }

  const handleRemove = async (agentId: string) => {
    setBusyAgentId(agentId)
    setError('')
    try {
      await removeAgentFromRoom(agentId, roomId)
      onChange()
    } catch (e) {
      setError((e as Error).message)
    }
    setBusyAgentId(null)
  }

  const inRoom = agents.filter(a => participantAgentIds.has(a.id))
  const available = agents.filter(a => !participantAgentIds.has(a.id))

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>Manage Agents in Room</DialogTitle>
          <DialogDescription>
            Add or remove agents from this room. Agents auto-start when added and stop when they have no rooms left.
          </DialogDescription>
        </DialogHeader>

        {error && (
          <div className="text-sm text-[var(--color-warning)] bg-[color:color-mix(in_srgb,var(--color-warning)_8%,transparent)] rounded-[var(--radius-md)] px-3 py-2 border border-[color:color-mix(in_srgb,var(--color-warning)_20%,transparent)]">
            {error}
          </div>
        )}

        {agents.length === 0 ? (
          <div className="bg-[var(--color-surface-alt)] rounded-[var(--radius-lg)] py-8 text-center">
            <p className="text-caption text-[var(--color-foreground-muted)]">
              No agents exist yet. Create one from the Agents admin page.
            </p>
          </div>
        ) : (
          <div className="space-y-5 py-2">
            {/* Agents currently in room */}
            <div>
              <h3 className="text-badge uppercase text-[var(--color-foreground-muted)] mb-2 tracking-wider">
                In this room ({inRoom.length})
              </h3>
              {inRoom.length === 0 ? (
                <p className="text-caption text-[var(--color-foreground-subtle)] italic px-1">
                  No agents in this room yet.
                </p>
              ) : (
                <ul className="space-y-2">
                  {inRoom.map(agent => (
                    <li
                      key={agent.id}
                      className="flex items-center justify-between bg-[var(--color-surface-alt)] rounded-[var(--radius-md)] p-3 border border-[var(--color-border)]"
                    >
                      <div className="flex items-center gap-2 min-w-0">
                        <Bot className="h-4 w-4 shrink-0 text-[var(--color-foreground-subtle)]" />
                        <span className="truncate font-medium text-[var(--color-foreground)]">{agent.name}</span>
                        <span className="text-caption text-[var(--color-foreground-muted)]">{agent.engine}</span>
                        <Badge variant="outline" className={stateBadgeClass(agent.actual_state)}>
                          {agent.actual_state}
                        </Badge>
                      </div>
                      <Button
                        variant="ghost"
                        size="icon"
                        className="text-[var(--color-warning)] hover:text-[var(--color-warning)]"
                        onClick={() => handleRemove(agent.id)}
                        disabled={busyAgentId === agent.id}
                        title="Remove from room"
                      >
                        {busyAgentId === agent.id ? (
                          <Loader2 className="h-4 w-4 animate-spin" />
                        ) : (
                          <Minus className="h-4 w-4" />
                        )}
                      </Button>
                    </li>
                  ))}
                </ul>
              )}
            </div>

            {/* Available agents */}
            <div>
              <h3 className="text-badge uppercase text-[var(--color-foreground-muted)] mb-2 tracking-wider">
                Available ({available.length})
              </h3>
              {available.length === 0 ? (
                <p className="text-caption text-[var(--color-foreground-subtle)] italic px-1">
                  All agents are already in this room.
                </p>
              ) : (
                <ul className="space-y-2">
                  {available.map(agent => (
                    <li
                      key={agent.id}
                      className="flex items-center justify-between bg-[var(--color-surface-alt)] rounded-[var(--radius-md)] p-3 border border-[var(--color-border)]"
                    >
                      <div className="flex items-center gap-2 min-w-0">
                        <Bot className="h-4 w-4 shrink-0 text-[var(--color-foreground-subtle)]" />
                        <span className="truncate font-medium text-[var(--color-foreground)]">{agent.name}</span>
                        <span className="text-caption text-[var(--color-foreground-muted)]">{agent.engine}</span>
                        <Badge variant="outline" className={stateBadgeClass(agent.actual_state)}>
                          {agent.actual_state}
                        </Badge>
                      </div>
                      <Button
                        variant="ghost"
                        size="icon"
                        className="text-[var(--color-success)] hover:text-[var(--color-success)]"
                        onClick={() => handleAdd(agent.id)}
                        disabled={busyAgentId === agent.id}
                        title="Add to room"
                      >
                        {busyAgentId === agent.id ? (
                          <Loader2 className="h-4 w-4 animate-spin" />
                        ) : (
                          <Plus className="h-4 w-4" />
                        )}
                      </Button>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </div>
        )}

        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>Close</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
