import { useState } from 'react'
import { useAgents } from '@/hooks/useAgents'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter,
} from '@/components/ui/dialog'
import { Bot, Plus, Minus, Loader2 } from 'lucide-react'
import PresenceDot from '@/components/PresenceDot'
import { agentStatusLabel, deriveAgentOnline } from '@/lib/agent-liveness'
import { useLocale } from '@/i18n/LocaleProvider'

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
  const { t } = useLocale()
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
  const stateKeys = {
    unreachable: 'admin.agentSettings.state.unreachable',
    unknown: 'admin.agentSettings.state.unknown',
    running: 'admin.agentSettings.state.running',
    starting: 'admin.agentSettings.state.starting',
    stopping: 'admin.agentSettings.state.stopping',
    stopped: 'admin.agentSettings.state.stopped',
    idle: 'admin.agentSettings.state.idle',
    pending: 'admin.agentSettings.state.pending',
    crashed: 'admin.agentSettings.state.crashed',
    failed: 'admin.agentSettings.state.failed',
  } as const
  const localizedState = (state: string) => state in stateKeys ? t(stateKeys[state as keyof typeof stateKeys]) : state

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>{t('rooms.manageAgents')}</DialogTitle>
          <DialogDescription>
            {t('rooms.manageAgentsDescription')}
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
              {t('rooms.noAgents')}
            </p>
          </div>
        ) : (
          <div className="space-y-5 py-2">
            {/* Agents currently in room */}
            <div>
              <h3 className="text-badge uppercase text-[var(--color-foreground-muted)] mb-2 tracking-wider">
                {t('rooms.inRoom', { count: inRoom.length })}
              </h3>
              {inRoom.length === 0 ? (
                <p className="text-caption text-[var(--color-foreground-subtle)] italic px-1">
                  {t('rooms.noAgentsInRoom')}
                </p>
              ) : (
                <ul className="space-y-2">
                  {inRoom.map(agent => {
                    const machineOffline = agent.machine_online === false
                    const online = deriveAgentOnline(agent.actual_state, { machineOffline })
                    const displayState = agentStatusLabel(agent.actual_state, { machineOffline })
                    return (
                      <li
                        key={agent.id}
                        className="flex items-center justify-between bg-[var(--color-surface-alt)] rounded-[var(--radius-md)] p-3 border border-[var(--color-border)]"
                      >
                        <div className="flex items-center gap-2 min-w-0">
                          <Bot className="h-4 w-4 shrink-0 text-[var(--color-foreground-subtle)]" />
                          <PresenceDot
                            variant="agent"
                            online={online}
                            agentState={localizedState(displayState)}
                          />
                          <span className="truncate font-medium text-[var(--color-foreground)]">{agent.name}</span>
                          <span className="text-caption text-[var(--color-foreground-muted)]">{agent.engine}</span>
                          <Badge variant="outline" className={stateBadgeClass(displayState)}>
                            {localizedState(displayState)}
                          </Badge>
                        </div>
                        <Button
                          variant="ghost"
                          size="icon"
                          className="text-[var(--color-warning)] hover:text-[var(--color-warning)]"
                          onClick={() => handleRemove(agent.id)}
                          disabled={busyAgentId === agent.id}
                          title={t('rooms.removeAgent')}
                          aria-label={`${t('rooms.removeAgent')}: ${agent.name}`}
                        >
                          {busyAgentId === agent.id ? (
                            <Loader2 className="h-4 w-4 animate-spin" />
                          ) : (
                            <Minus className="h-4 w-4" />
                          )}
                        </Button>
                      </li>
                    )
                  })}
                </ul>
              )}
            </div>

            {/* Available agents */}
            <div>
              <h3 className="text-badge uppercase text-[var(--color-foreground-muted)] mb-2 tracking-wider">
                {t('rooms.available', { count: available.length })}
              </h3>
              {available.length === 0 ? (
                <p className="text-caption text-[var(--color-foreground-subtle)] italic px-1">
                  {t('rooms.allAgentsInRoom')}
                </p>
              ) : (
                <ul className="space-y-2">
                  {available.map(agent => {
                    const machineOffline = agent.machine_online === false
                    const online = deriveAgentOnline(agent.actual_state, { machineOffline })
                    const displayState = agentStatusLabel(agent.actual_state, { machineOffline })
                    return (
                      <li
                        key={agent.id}
                        className="flex items-center justify-between bg-[var(--color-surface-alt)] rounded-[var(--radius-md)] p-3 border border-[var(--color-border)]"
                      >
                        <div className="flex items-center gap-2 min-w-0">
                          <Bot className="h-4 w-4 shrink-0 text-[var(--color-foreground-subtle)]" />
                          <PresenceDot
                            variant="agent"
                            online={online}
                            agentState={localizedState(displayState)}
                          />
                          <span className="truncate font-medium text-[var(--color-foreground)]">{agent.name}</span>
                          <span className="text-caption text-[var(--color-foreground-muted)]">{agent.engine}</span>
                          <Badge variant="outline" className={stateBadgeClass(displayState)}>
                            {localizedState(displayState)}
                          </Badge>
                        </div>
                        <Button
                          variant="ghost"
                          size="icon"
                          className="text-[var(--color-success)] hover:text-[var(--color-success)]"
                          onClick={() => handleAdd(agent.id)}
                          disabled={busyAgentId === agent.id}
                          title={t('rooms.addAgent')}
                          aria-label={`${t('rooms.addAgent')}: ${agent.name}`}
                        >
                          {busyAgentId === agent.id ? (
                            <Loader2 className="h-4 w-4 animate-spin" />
                          ) : (
                            <Plus className="h-4 w-4" />
                          )}
                        </Button>
                      </li>
                    )
                  })}
                </ul>
              )}
            </div>
          </div>
        )}

        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>{t('common.close')}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
