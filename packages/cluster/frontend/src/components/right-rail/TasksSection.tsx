import { useState, useMemo, useEffect } from 'react'
import {
  Plus,
  CheckCircle2,
  Circle,
  Clock,
  PauseCircle,
  XCircle,
  Trash2,
  Wand2,
  Loader2,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { useRoomTasks, type Task } from '@/hooks/useRoomTasks'
import { autoRouteUnassigned } from '@/lib/routing'
import type { Participant } from '@/pages/ChatPage'

interface TasksSectionProps {
  roomId: string
  participants: Record<string, Participant>
}

// #319 — ``blocked`` / ``failed`` are system-set statuses (the goals
// sweeper stamps ``failed`` on pickup/execution timeouts; agents can
// stamp ``blocked`` themselves via ``mark_task_status``). They render
// in their own status group but are deliberately *not* in the user
// toggle cycle — the click toggle stays on actionable transitions.
const STATUS_CYCLE = ['todo', 'in_progress', 'done'] as const
const STATUS_ORDER = ['todo', 'in_progress', 'blocked', 'failed', 'done'] as const
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

/**
 * Compact tasks panel for the right rail (#302). Shares the
 * ``useRoomTasks`` hook with ``TaskPanel`` so the legacy panel and
 * the rail render the same data with one WS subscription. The rail
 * is narrower (288–320px), so the layout drops the four-tab filter
 * row and instead groups by status with collapsible-ish headers.
 *
 * #312 — restored the assignee picker that the original PR-1 had
 * dropped for compactness. Without it the ``+`` button silently
 * created unassigned tasks and no agent ever picked them up. The
 * picker default stays "Unassigned" so the legacy behaviour
 * (memo-only intent) is still possible; users opt in to delegation
 * by selecting an agent. Single-agent rooms auto-fill the picker so
 * the chip is read-only — there is only one valid choice.
 */
export default function TasksSection({ roomId, participants }: TasksSectionProps) {
  const { tasks, refresh, create, update, remove } = useRoomTasks(roomId)
  const [newTitle, setNewTitle] = useState('')
  const [newAssignee, setNewAssignee] = useState<string>('')
  const [adding, setAdding] = useState(false)
  // #313 — auto-route batch state. ``routing`` blocks duplicate
  // clicks and feeds the spinner; ``routeMessage`` is shown for
  // 4 seconds as an inline toast under the header so the user
  // sees the outcome without an extra notification system.
  const [routing, setRouting] = useState(false)
  const [routeMessage, setRouteMessage] = useState<string | null>(null)

  // Agent participants only — humans are not eligible Task assignees
  // in the rail (mirrors the legacy TaskPanel default; rooms with
  // ``allow_human_assignment`` keep that path via the legacy panel
  // until the rail picks up a humans toggle in a follow-up).
  const agentParticipants = useMemo<Participant[]>(
    () =>
      Object.values(participants)
        .filter((p) => p.kind === 'agent')
        .sort((a, b) => a.display_name.localeCompare(b.display_name)),
    [participants],
  )

  const singleAgentRoom = agentParticipants.length === 1
  const singleAgentId = singleAgentRoom ? agentParticipants[0].id : null

  // Auto-select the sole agent when the room has exactly one. Re-run
  // when the candidate set changes so an agent leaving / joining
  // updates the chip without a remount.
  useEffect(() => {
    if (singleAgentId) setNewAssignee(singleAgentId)
    else if (newAssignee && !agentParticipants.some((p) => p.id === newAssignee)) {
      // The previously-picked agent is no longer in the room.
      setNewAssignee('')
    }
  }, [singleAgentId, agentParticipants, newAssignee])

  // #313 — count of unassigned items. Drives the auto-route
  // button's enabled state (no point in routing when nothing is
  // unassigned). Excludes ``done`` tasks because they are terminal.
  const unassignedCount = useMemo(
    () =>
      tasks.filter(
        (t) => t.assignee_participant_id === null && t.status !== 'done',
      ).length,
    [tasks],
  )

  const handleAutoRoute = async () => {
    setRouting(true)
    setRouteMessage(null)
    try {
      const result = await autoRouteUnassigned(roomId)
      const routedCount = result.routed.length
      const skippedCount = result.skipped.length
      if (routedCount === 0 && skippedCount === 0) {
        setRouteMessage('No tasks to route')
      } else {
        const routedNames = result.routed
          .map((r) => {
            const ap = Object.values(participants).find(
              (p) => p.agent_id === r.assignee_agent_id,
            )
            return ap?.display_name ?? r.assignee_agent_id.slice(0, 6)
          })
          .reduce<Record<string, number>>((acc, name) => {
            acc[name] = (acc[name] ?? 0) + 1
            return acc
          }, {})
        const breakdown = Object.entries(routedNames)
          .map(([name, n]) => `${n} → ${name}`)
          .join(', ')
        const suffix =
          skippedCount > 0 ? ` (${skippedCount} skipped)` : ''
        setRouteMessage(
          routedCount > 0 ? `Routed: ${breakdown}${suffix}` : `${skippedCount} skipped`,
        )
      }
      // refetch is implicit via the WS task.updated stream from
      // ``inject_task_assignment_message``, but force a refresh in
      // case the user is on a slow connection.
      await refresh()
    } catch (e) {
      setRouteMessage(
        e instanceof Error ? e.message : 'Auto-route failed',
      )
    } finally {
      setRouting(false)
      // Clear the toast after a few seconds so the header stays
      // calm — long-lived banners crowd the 320px column.
      window.setTimeout(() => setRouteMessage(null), 4000)
    }
  }

  const grouped = useMemo(() => {
    // #319 — pre-seed every known status so the iteration order in
    // STATUS_ORDER is deterministic and ``blocked`` / ``failed``
    // sections render even when the only items in those buckets
    // arrived via a sweeper or agent self-report.
    const groups: Record<string, Task[]> = {
      todo: [],
      in_progress: [],
      blocked: [],
      failed: [],
      done: [],
    }
    for (const t of tasks) {
      const bucket = groups[t.status] ?? (groups[t.status] = [])
      bucket.push(t)
    }
    return groups
  }, [tasks])

  const cycleStatus = async (task: Task) => {
    // #319 — when the current status is system-set (``blocked`` /
    // ``failed``) it is not in the cycle. Re-entering the cycle from
    // ``todo`` keeps the click toggle predictable and lets the user
    // unblock a task without a separate "reset" affordance.
    const idx = STATUS_CYCLE.indexOf(task.status as typeof STATUS_CYCLE[number])
    const next = idx === -1
      ? STATUS_CYCLE[0]
      : STATUS_CYCLE[(idx + 1) % STATUS_CYCLE.length]
    await update(task.id, { status: next })
  }

  const reassign = async (task: Task, participantId: string) => {
    await update(task.id, { assignee_participant_id: participantId || null })
  }

  const submitNew = async () => {
    if (!newTitle.trim()) return
    setAdding(true)
    await create({
      title: newTitle.trim(),
      assignee_participant_id: newAssignee || null,
    })
    setNewTitle('')
    if (!singleAgentId) setNewAssignee('')
    setAdding(false)
  }

  const renderRow = (task: Task) => {
    const Icon = STATUS_ICON[task.status] ?? Circle
    const assignee = task.assignee_participant_id
      ? participants[task.assignee_participant_id]
      : undefined
    return (
      <div
        key={task.id}
        data-testid={`right-rail-task-row-${task.id}`}
        className="group flex items-center gap-2 rounded-[var(--radius-sm)] px-2 py-1.5 hover:bg-[var(--color-surface-alt)]"
      >
        <button
          onClick={() => cycleStatus(task)}
          aria-label={`Cycle status (current: ${STATUS_LABEL[task.status] ?? task.status})`}
          className="shrink-0"
        >
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
          className={`flex-1 truncate text-[13px] ${
            task.status === 'done'
              ? 'line-through text-[var(--color-foreground-muted)]'
              : task.status === 'failed'
                ? 'line-through text-[var(--color-foreground-muted)]'
                : 'text-[var(--color-foreground)]'
          }`}
          title={task.title}
        >
          {task.title}
        </span>
        {/* Reassign picker — visible-on-hover so the row stays calm
            at rest. #312: restoring the legacy ``TaskPanel`` UX in
            the rail. ``""`` value = Unassigned, allowed because Tasks
            (unlike Goals) tolerate a NULL assignee. */}
        <select
          value={task.assignee_participant_id ?? ''}
          onChange={(e) => reassign(task, e.target.value)}
          onClick={(e) => e.stopPropagation()}
          className="opacity-0 group-hover:opacity-100 transition-opacity bg-transparent text-[10px] text-[var(--color-foreground-muted)] outline-none border-0 focus:ring-0 max-w-[6rem] truncate"
          aria-label={`Reassign ${task.title}`}
          data-testid={`right-rail-task-assignee-${task.id}`}
        >
          <option value="">— Unassigned —</option>
          {agentParticipants.map((p) => (
            <option key={p.id} value={p.id}>
              {p.display_name}
            </option>
          ))}
        </select>
        {assignee && (
          <span
            className="shrink-0 text-[10px] text-[var(--color-foreground-subtle)] truncate max-w-[6ch]"
            title={assignee.display_name}
          >
            {assignee.display_name}
          </span>
        )}
        <button
          onClick={() => remove(task.id)}
          className="opacity-0 group-hover:opacity-100 p-0.5 rounded hover:bg-red-50 text-red-400 hover:text-red-600 transition-all shrink-0"
          aria-label={`Delete ${task.title}`}
        >
          <Trash2 className="h-3 w-3" />
        </button>
      </div>
    )
  }

  return (
    <section className="flex flex-col">
      <header className="flex items-baseline justify-between px-3 py-2">
        <h3 className="text-[11px] font-semibold uppercase tracking-wider text-[var(--color-foreground-subtle)]">
          Tasks
        </h3>
        <div className="flex items-center gap-1.5">
          <span className="text-[11px] text-[var(--color-foreground-subtle)]">
            {tasks.length}
          </span>
          {/* #313 — Auto-route via room representative. Disabled
              when nothing is unassigned (avoids a wasted LLM
              roundtrip) or while a request is in flight. */}
          <button
            type="button"
            onClick={handleAutoRoute}
            disabled={routing || unassignedCount === 0}
            aria-label="Auto-route unassigned tasks via room representative"
            title={
              unassignedCount === 0
                ? 'No unassigned tasks'
                : `Auto-route ${unassignedCount} unassigned task${unassignedCount === 1 ? '' : 's'}`
            }
            data-testid="right-rail-auto-route-button"
            className="rounded-[var(--radius-sm)] p-0.5 text-[var(--color-foreground-muted)] hover:bg-black/5 disabled:opacity-40 disabled:cursor-not-allowed"
          >
            {routing ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <Wand2 className="h-3.5 w-3.5" />
            )}
          </button>
        </div>
      </header>
      {routeMessage && (
        <p
          className="px-3 pb-1 text-[11px] text-[var(--color-foreground-muted)]"
          role="status"
          data-testid="right-rail-route-toast"
        >
          {routeMessage}
        </p>
      )}
      <div className="px-1">
        {tasks.length === 0 && (
          <div className="px-3 py-4 text-center text-[12px] text-[var(--color-foreground-subtle)]">
            No tasks yet
          </div>
        )}
        {STATUS_ORDER.map((status) => {
          const items = grouped[status] ?? []
          if (items.length === 0) return null
          return (
            <div key={status} className="mb-1">
              <div className="px-3 pt-1 pb-0.5 text-[10px] uppercase tracking-wider text-[var(--color-foreground-subtle)]">
                {STATUS_LABEL[status]}
              </div>
              {items.map(renderRow)}
            </div>
          )
        })}
      </div>
      {/* Inline create input + assignee picker (#312). The picker
          renders even on rooms with a single agent — disabled in
          that case so the chip is informative read-only ("auto-
          assigned to <name>") rather than feeling like an empty
          dropdown the user has to deal with. */}
      <div className="border-t border-[var(--color-border)] px-3 py-2 space-y-2">
        <input
          value={newTitle}
          onChange={(e) => setNewTitle(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && submitNew()}
          placeholder="Add a task…"
          className="w-full bg-transparent text-[13px] outline-none placeholder:text-[var(--color-foreground-subtle)]"
        />
        <div className="flex items-center gap-2">
          <select
            value={newAssignee}
            onChange={(e) => setNewAssignee(e.target.value)}
            disabled={singleAgentRoom || agentParticipants.length === 0}
            className="flex-1 bg-transparent text-[11px] text-[var(--color-foreground-muted)] outline-none border border-[var(--color-border)] rounded-[var(--radius-sm)] px-1.5 py-0.5 truncate disabled:opacity-70"
            aria-label="Pick assignee"
            data-testid="right-rail-task-create-assignee"
          >
            <option value="">— Unassigned —</option>
            {agentParticipants.map((p) => (
              <option key={p.id} value={p.id}>
                {p.display_name}
              </option>
            ))}
          </select>
          <Button
            variant="ghost"
            size="sm"
            onClick={submitNew}
            disabled={adding || !newTitle.trim()}
            aria-label="Create task"
          >
            <Plus className="h-4 w-4" />
          </Button>
        </div>
      </div>
    </section>
  )
}
