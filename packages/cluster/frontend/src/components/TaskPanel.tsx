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

interface TaskPanelProps {
  roomId: string
  /** Participants of the current room — used to populate the
   * assignee dropdown without a second fetch. ChatPage already keeps
   * this map up-to-date via ``GET /rooms/{id}``. */
  participants: Record<string, Participant>
}

// #319 — see ``TasksSection`` for the rationale; both panels share the
// same status vocabulary and only differ in the layout chrome.
const STATUS_CYCLE = ['todo', 'in_progress', 'done'] as const
const STATUS_ICON: Record<string, typeof Circle> = {
  todo: Circle,
  in_progress: Clock,
  done: CheckCircle2,
  blocked: PauseCircle,
  failed: XCircle,
}
const STATUS_LABEL: Record<string, string> = {
  todo: 'Todo',
  in_progress: 'In Progress',
  done: 'Done',
  blocked: 'Blocked',
  failed: 'Failed',
}

export default function TaskPanel({ roomId, participants }: TaskPanelProps) {
  const [filter, setFilter] = useState<string | null>(null)
  const [newTitle, setNewTitle] = useState('')
  const [newAssignee, setNewAssignee] = useState<string>('')
  const [adding, setAdding] = useState(false)
  const [allowHumanAssignment, setAllowHumanAssignment] = useState(false)

  // #302 — data plane lives in useRoomTasks. The right-rail TasksSection
  // consumes the same hook, guaranteeing the legacy panel and the new
  // sidebar render the same list against the same WS event stream.
  const { tasks, create, update, remove } = useRoomTasks(roomId, {
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
    // #319 — system-set statuses (``blocked`` / ``failed``) are not in
    // the user toggle cycle. Click on a row in those buckets resets to
    // ``todo`` so the user can re-engage without a separate control.
    const idx = STATUS_CYCLE.indexOf(task.status as typeof STATUS_CYCLE[number])
    const next = idx === -1
      ? STATUS_CYCLE[0]
      : STATUS_CYCLE[(idx + 1) % STATUS_CYCLE.length]
    await update(task.id, { status: next })
  }

  const reassign = async (task: Task, participantId: string) => {
    await update(task.id, { assignee_participant_id: participantId || null })
  }

  const filters = [
    { key: null, label: 'All' },
    { key: 'todo', label: 'Todo' },
    { key: 'in_progress', label: 'In Progress' },
    { key: 'blocked', label: 'Blocked' },
    { key: 'failed', label: 'Failed' },
    { key: 'done', label: 'Done' },
  ]

  return (
    <div className="flex flex-1 flex-col overflow-hidden">
      {/* Filter tabs */}
      <div className="flex items-center gap-1 border-b border-[var(--color-border)] px-4 py-2">
        {filters.map(f => (
          <button
            key={f.key ?? 'all'}
            onClick={() => setFilter(f.key)}
            className={`rounded-[var(--radius-sm)] px-2.5 py-1 text-xs transition-colors ${
              filter === f.key
                ? 'bg-[var(--color-brand-tint-bg)] text-[var(--color-brand)] font-medium'
                : 'text-[var(--color-foreground-muted)] hover:bg-black/5'
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
              className="group flex items-center gap-2 rounded-[var(--radius-sm)] px-2 py-1.5 hover:bg-[var(--color-surface-alt)]"
            >
              <button onClick={() => cycleStatus(task)} title={`Status: ${STATUS_LABEL[task.status] ?? task.status}`}>
                <Icon
                  className={`h-4 w-4 ${
                    task.status === 'done'
                      ? 'text-green-600'
                      : task.status === 'in_progress'
                        ? 'text-[var(--color-brand)]'
                        : task.status === 'failed'
                          ? 'text-rose-600'
                          : task.status === 'blocked'
                            ? 'text-amber-600'
                            : 'text-[var(--color-foreground-subtle)]'
                  }`}
                />
              </button>
              <span
                className={`flex-1 text-sm ${
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
                className="bg-transparent text-xs text-[var(--color-foreground-muted)] outline-none border-0 focus:ring-0 max-w-[8rem] truncate"
                aria-label="Reassign task"
              >
                <option value="">— unassigned —</option>
                {agentParticipants.length > 0 && (
                  <optgroup label="Agents">
                    {agentParticipants.map(p => (
                      <option key={p.id} value={p.id}>{p.display_name}</option>
                    ))}
                  </optgroup>
                )}
                {allowHumanAssignment && humanParticipants.length > 0 && (
                  <optgroup label="People">
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
              <button
                onClick={() => remove(task.id)}
                className="opacity-0 group-hover:opacity-100 p-0.5 rounded hover:bg-[var(--color-destructive)]/10 text-[var(--color-destructive)]/70 hover:text-[var(--color-destructive)] transition-all"
                title="Delete task"
              >
                <Trash2 className="h-3.5 w-3.5" />
              </button>
            </div>
          )
        })}
        {tasks.length === 0 && (
          <div className="py-8 text-center text-sm text-[var(--color-foreground-muted)]">
            No tasks yet
          </div>
        )}
      </div>

      {/* Add task — inline composer with assignee picker */}
      <div className="border-t border-[var(--color-border)] px-4 py-2">
        <div className="flex items-center gap-2">
          <input
            value={newTitle}
            onChange={e => setNewTitle(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && createTask()}
            placeholder="Add a task..."
            className="flex-1 bg-transparent text-sm outline-none placeholder:text-[var(--color-foreground-subtle)]"
          />
          <select
            value={newAssignee}
            onChange={e => setNewAssignee(e.target.value)}
            className="bg-transparent text-xs text-[var(--color-foreground-muted)] outline-none border-0 focus:ring-0 max-w-[8rem] truncate"
            aria-label="Pick assignee"
          >
            <option value="">— assignee —</option>
            {agentParticipants.length > 0 && (
              <optgroup label="Agents">
                {agentParticipants.map(p => (
                  <option key={p.id} value={p.id}>{p.display_name}</option>
                ))}
              </optgroup>
            )}
            {allowHumanAssignment && humanParticipants.length > 0 && (
              <optgroup label="People">
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
              className="p-0.5 rounded hover:bg-black/5 text-[var(--color-foreground-subtle)]"
              title="Clear assignee"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          )}
          <Button
            variant="ghost"
            size="sm"
            onClick={createTask}
            disabled={adding || !newTitle.trim()}
          >
            <Plus className="h-4 w-4" />
          </Button>
        </div>
      </div>
    </div>
  )
}
