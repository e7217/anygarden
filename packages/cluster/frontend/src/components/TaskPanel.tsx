import { useEffect, useMemo, useState } from 'react'
import {
  Plus,
  Trash2,
  CheckCircle2,
  Circle,
  Clock,
  PauseCircle,
  XCircle,
  X,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { apiFetch } from '@/lib/api'
import { EntityAvatar } from '@/components/EntityAvatar'
import { useRoomTasks, type Task } from '@/hooks/useRoomTasks'
import type { Participant } from '@/pages/ChatPage'
import { useLocale } from '@/i18n/LocaleProvider'

interface TaskPanelProps {
  roomId: string
  /** Participants of the current room — used to populate the
   * assignee dropdown without a second fetch. ChatPage already keeps
   * this map up-to-date via ``GET /rooms/{id}``. */
  participants: Record<string, Participant>
}

// #319 — see ``TasksSection`` for the rationale; both panels share the
// same status vocabulary and only differ in the layout chrome.
const STATUS_ICON: Record<string, typeof Circle> = {
  todo: Circle,
  in_progress: Clock,
  done: CheckCircle2,
  blocked: PauseCircle,
  failed: XCircle,
}
export default function TaskPanel({ roomId, participants }: TaskPanelProps) {
  const { t } = useLocale()
  const statusLabel: Record<string, string> = {
    todo: t('chat.todo'),
    in_progress: t('chat.inProgress'),
    done: t('chat.done'),
    blocked: t('chat.blocked'),
    failed: t('chat.failed'),
  }
  const [filter, setFilter] = useState<string | null>(null)
  const [newTitle, setNewTitle] = useState('')
  const [newAssignee, setNewAssignee] = useState<string>('')
  const [adding, setAdding] = useState(false)
  const [allowHumanAssignment, setAllowHumanAssignment] = useState(false)

  // #302 — data plane lives in useRoomTasks. The right-rail TasksSection
  // consumes the same hook, guaranteeing the legacy panel and the new
  // sidebar render the same list against the same WS event stream.
  const { tasks, create, claim, requeue, update, remove } = useRoomTasks(roomId, {
    status: filter,
  })

  // Group participants once per render — agents on top, humans below
  // when the room opts in. We keep the lists separate so the dropdown
  // can label each group inline.
  const { agentParticipants, humanParticipants } = useMemo(() => {
    const agents: Participant[] = []
    const humans: Participant[] = []
    for (const p of Object.values(participants)) {
      if (p.kind === 'agent') agents.push(p)
      else humans.push(p)
    }
    agents.sort((a, b) => a.display_name.localeCompare(b.display_name))
    humans.sort((a, b) => a.display_name.localeCompare(b.display_name))
    return { agentParticipants: agents, humanParticipants: humans }
  }, [participants])

  // Pull the room's allow_human_assignment flag once per room change.
  // We keep this self-contained (vs. lifting to ChatPage) so TaskPanel
  // remains drop-in usable elsewhere — e.g. a future per-agent task
  // panel.
  useEffect(() => {
    let cancelled = false
    apiFetch(`/api/v1/rooms/${roomId}`)
      .then(r => (r.ok ? r.json() : null))
      .then(room => {
        if (cancelled || !room) return
        setAllowHumanAssignment(Boolean(room.allow_human_assignment))
      })
      .catch(() => { /* swallow — default to agent-only */ })
    return () => { cancelled = true }
  }, [roomId])

  const createTask = async () => {
    if (!newTitle.trim()) return
    setAdding(true)
    await create({
      title: newTitle.trim(),
      assignee_participant_id: newAssignee || null,
    })
    setNewTitle('')
    setNewAssignee('')
    setAdding(false)
  }

  const cycleStatus = async (task: Task) => {
    if (task.status === 'todo') {
      await claim(task.id)
    } else if (task.status === 'in_progress') {
      await update(task.id, { status: 'done' })
    } else {
      await requeue(task.id, {
        reason: 'Requeued from task panel',
        assignee_participant_id: task.assignee_participant_id,
      })
    }
  }

  const reassign = async (task: Task, participantId: string) => {
    const assignee = participantId || null
    if (task.status === 'todo') {
      await update(task.id, { assignee_participant_id: assignee })
    } else {
      await requeue(task.id, {
        reason: 'Reassigned from task panel',
        assignee_participant_id: assignee,
      })
    }
  }

  const filters = [
    { key: null, label: t('chat.all') },
    { key: 'todo', label: t('chat.todo') },
    { key: 'in_progress', label: t('chat.inProgress') },
    { key: 'blocked', label: t('chat.blocked') },
    { key: 'failed', label: t('chat.failed') },
    { key: 'done', label: t('chat.done') },
  ]

  return (
    <div className="flex flex-1 flex-col overflow-hidden">
      {/* Filter tabs */}
      <div className="flex items-center gap-1 overflow-x-auto border-b border-[var(--color-border)] px-4 py-2">
        {filters.map(f => (
          <button
            key={f.key ?? 'all'}
            onClick={() => setFilter(f.key)}
            className={`min-h-11 shrink-0 whitespace-nowrap rounded-[var(--radius-sm)] px-2.5 text-xs transition-colors ${
              filter === f.key
                ? 'bg-[var(--color-brand-tint-bg)] text-[var(--color-brand-tint-text)] font-medium'
                : 'text-[var(--color-foreground-muted)] hover:bg-[var(--color-surface-hover)]'
            }`}
          >
            {f.label}
          </button>
        ))}
      </div>

      {/* Task list */}
      <div className="flex-1 overflow-y-auto px-4 py-2 space-y-1">
        {tasks.map(task => {
          const Icon = STATUS_ICON[task.status] ?? Circle
          const assignee = task.assignee_participant_id
            ? participants[task.assignee_participant_id]
            : undefined
          return (
            <div
              key={task.id}
              data-testid={`task-row-${task.id}`}
              className="group flex min-h-11 min-w-0 items-center gap-2 rounded-[var(--radius-sm)] px-2 py-1.5 hover:bg-[var(--color-surface-alt)]"
            >
              <button onClick={() => cycleStatus(task)} title={t('chat.taskStatus', { status: statusLabel[task.status] ?? task.status })} aria-label={t('chat.taskStatus', { status: statusLabel[task.status] ?? task.status })} className="flex h-11 w-11 shrink-0 items-center justify-center md:h-6 md:w-6">
                <Icon
                  className={`h-4 w-4 ${
                    task.status === 'done'
                      ? 'text-[var(--color-success)]'
                      : task.status === 'in_progress'
                        ? 'text-[var(--color-brand-text)]'
                        : task.status === 'failed'
                        ? 'text-[var(--color-danger)]'
                          : task.status === 'blocked'
                          ? 'text-[var(--color-warning)]'
                            : 'text-[var(--color-foreground-subtle)]'
                  }`}
                />
              </button>
              <span
                className={`min-w-0 flex-1 truncate text-sm ${
                  task.status === 'done' || task.status === 'failed'
                    ? 'line-through text-[var(--color-foreground-muted)]'
                    : 'text-[var(--color-foreground)]'
                }`}
              >
                {task.title}
              </span>
              {/* Assignee picker — collapsed avatar by default, expanded
                  to a select on hover. Keeping the picker inline avoids
                  a second modal for what is the most common edit. */}
              <select
                value={task.assignee_participant_id ?? ''}
                onChange={e => reassign(task, e.target.value)}
                className="min-h-11 min-w-0 max-w-[8rem] truncate border-0 bg-transparent text-xs text-[var(--color-foreground-muted)] outline-none focus:ring-0 md:min-h-6"
                aria-label={t('chat.reassign')}
              >
                <option value="">— {t('chat.unassigned')} —</option>
                {agentParticipants.length > 0 && (
                  <optgroup label={t('chat.agents')}>
                    {agentParticipants.map(p => (
                      <option key={p.id} value={p.id}>{p.display_name}</option>
                    ))}
                  </optgroup>
                )}
                {allowHumanAssignment && humanParticipants.length > 0 && (
                  <optgroup label={t('chat.people')}>
                    {humanParticipants.map(p => (
                      <option key={p.id} value={p.id}>{p.display_name}</option>
                    ))}
                  </optgroup>
                )}
              </select>
              {assignee ? (
                <EntityAvatar
                  id={assignee.id}
                  name={assignee.display_name}
                  kind={assignee.kind === 'agent' ? 'agent' : 'user'}
                  size="sm"
                  engine={assignee.engine}
                />
              ) : null}
              {!task.source_message_id ? (
                <button
                  onClick={() => remove(task.id)}
                  className="flex h-11 w-11 shrink-0 items-center justify-center rounded text-[var(--color-destructive)]/70 opacity-100 transition-all hover:bg-[var(--color-destructive)]/10 hover:text-[var(--color-destructive)] md:h-6 md:w-6 md:opacity-0 md:group-hover:opacity-100"
                  title={t('chat.deleteTask')}
                  aria-label={t('chat.deleteTask')}
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </button>
              ) : null}
            </div>
          )
        })}
        {tasks.length === 0 && (
          <div className="py-8 text-center text-sm text-[var(--color-foreground-muted)]">
            {t('chat.noTasks')}
          </div>
        )}
      </div>

      {/* Add task — inline composer with assignee picker */}
      <div className="border-t border-[var(--color-border)] px-4 py-2">
        <div className="flex flex-wrap items-center gap-2">
          <input
            value={newTitle}
            onChange={e => setNewTitle(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && createTask()}
            placeholder={t('chat.addTask')}
            className="min-h-11 min-w-[8rem] flex-1 bg-transparent text-sm outline-none placeholder:text-[var(--color-foreground-subtle)]"
          />
          <select
            value={newAssignee}
            onChange={e => setNewAssignee(e.target.value)}
            className="min-h-11 max-w-[8rem] truncate border-0 bg-transparent text-xs text-[var(--color-foreground-muted)] outline-none focus:ring-0 md:min-h-6"
            aria-label={t('chat.pickAssignee')}
          >
            <option value="">— {t('chat.assignee')} —</option>
            {agentParticipants.length > 0 && (
              <optgroup label={t('chat.agents')}>
                {agentParticipants.map(p => (
                  <option key={p.id} value={p.id}>{p.display_name}</option>
                ))}
              </optgroup>
            )}
            {allowHumanAssignment && humanParticipants.length > 0 && (
              <optgroup label={t('chat.people')}>
                {humanParticipants.map(p => (
                  <option key={p.id} value={p.id}>{p.display_name}</option>
                ))}
              </optgroup>
            )}
          </select>
          {newAssignee && (
            <button
              type="button"
              onClick={() => setNewAssignee('')}
              className="flex h-11 w-11 items-center justify-center rounded text-[var(--color-foreground-subtle)] hover:bg-[var(--color-surface-hover)] md:h-6 md:w-6"
              title={t('chat.clearAssignee')}
              aria-label={t('chat.clearAssignee')}
            >
              <X className="h-3.5 w-3.5" />
            </button>
          )}
          <Button
            variant="ghost"
            size="sm"
            onClick={createTask}
            disabled={adding || !newTitle.trim()}
            aria-label={t('chat.createTask')}
            className="min-h-11 min-w-11"
          >
            <Plus className="h-4 w-4" />
          </Button>
        </div>
      </div>
    </div>
  )
}
