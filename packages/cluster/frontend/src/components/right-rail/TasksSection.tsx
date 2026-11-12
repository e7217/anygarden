import { useState, useMemo, useEffect } from 'react'
import { Plus, Trash2, Wand2, Loader2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Select } from '@/components/ui/select'
import { useRoomTasks, type Task } from '@/hooks/useRoomTasks'
import { taskPresentation } from '@/lib/taskPresentation'
import { autoRouteUnassigned } from '@/lib/routing'
import type { Participant } from '@/pages/ChatPage'
import { isLinkParticipant } from '@/lib/participants'
import { useLocale } from '@/i18n/LocaleProvider'
import { useFeedback } from '@/components/feedback/FeedbackProvider'
import { useNow } from '@/lib/elapsed'
import { formatAgo, formatRunning, msSince } from '@/lib/taskTime'
import TaskDetailDialog from './TaskDetailDialog'
import { STATUS_ICON, statusIconColor } from './taskStatusIcon'

interface TasksSectionProps {
  roomId: string
  participants: Record<string, Participant>
  onNavigate?: () => void
}

// Current work is one list ordered by status; the row icon carries the
// status, so the list needs no per-status headings.
const STATUS_ORDER = ['in_progress', 'cancelling', 'blocked', 'failed', 'todo'] as const

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
export default function TasksSection({ roomId, participants, onNavigate }: TasksSectionProps) {
  const { t, locale, formatDate } = useLocale()
  // Row times are minute-grained ("23m"): a minute tick re-renders, and
  // each render reads the clock so freshly loaded rows are not behind.
  useNow(60_000)
  const now = Date.now()
  const { confirm, notify } = useFeedback()
  const { tasks, loading, error, refresh, create, claim, requeue, update, remove } = useRoomTasks(roomId)
  const [newTitle, setNewTitle] = useState('')
  const [newAssignee, setNewAssignee] = useState<string>('')
  const [adding, setAdding] = useState(false)
  // #313 — auto-route batch state. ``routing`` blocks duplicate
  // clicks and feeds the spinner; ``routeMessage`` is shown for
  // 4 seconds as an inline toast under the header so the user
  // sees the outcome without an extra notification system.
  const [routing, setRouting] = useState(false)
  const [routeMessage, setRouteMessage] = useState<string | null>(null)
  // The open detail dialog follows the live task list, so a task that
  // disappears (deleted elsewhere) closes it.
  const [openTaskId, setOpenTaskId] = useState<string | null>(null)

  // Agent participants only — humans are not eligible Task assignees
  // in the rail (mirrors the legacy TaskPanel default; rooms with
  // ``allow_human_assignment`` keep that path via the legacy panel
  // until the rail picks up a humans toggle in a follow-up).
  const agentParticipants = useMemo<Participant[]>(
    () =>
      Object.values(participants)
        // #1006 — link-only agents answer peer questions, not tasks.
        .filter((p) => p.kind === 'agent' && !isLinkParticipant(p))
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
        (t) => t.room_id === roomId && !t.execution_id && t.assignee_participant_id === null && t.status !== 'done',
      ).length,
    [tasks, roomId],
  )

  const handleAutoRoute = async () => {
    setRouting(true)
    setRouteMessage(null)
    try {
      const result = await autoRouteUnassigned(roomId)
      const routedCount = result.routed.length
      const skippedCount = result.skipped.length
      if (routedCount === 0 && skippedCount === 0) {
        setRouteMessage(t('tasks.noRoute'))
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
        const suffix = skippedCount > 0 ? ` (${t('tasks.skipped', { count: skippedCount })})` : ''
        setRouteMessage(
          routedCount > 0 ? t('tasks.routed', { names: breakdown, suffix }) : t('tasks.skipped', { count: skippedCount }),
        )
      }
      // refetch is implicit via the WS task.updated stream from
      // ``inject_task_assignment_message``, but force a refresh in
      // case the user is on a slow connection.
      await refresh()
    } catch (e) {
      setRouteMessage(
        e instanceof Error ? e.message : t('tasks.routeFailed'),
      )
    } finally {
      setRouting(false)
      // Clear the toast after a few seconds so the header stays
      // calm — long-lived banners crowd the 320px column.
      window.setTimeout(() => setRouteMessage(null), 4000)
    }
  }

  const { current, recentResults, remainingHistory } = useMemo(() => {
    const byStatus: Record<string, Task[]> = {}
    const history: Task[] = []
    for (const task of tasks) {
      const presentation = taskPresentation(task)
      if (presentation.history) history.push(task)
      else (byStatus[presentation.status] ??= []).push(task)
    }
    history.sort((a, b) => b.created_at.localeCompare(a.created_at))
    const order = [...STATUS_ORDER, ...Object.keys(byStatus).filter(status => !STATUS_ORDER.includes(status as typeof STATUS_ORDER[number]))]
    const recent = history.filter(task => taskPresentation(task).status === 'done').slice(0, 3)
    return {
      current: order.flatMap(status => byStatus[status] ?? []),
      recentResults: recent,
      remainingHistory: history.filter(task => !recent.includes(task)),
    }
  }, [tasks])
  const openTask = openTaskId ? tasks.find(task => task.id === openTaskId) ?? null : null

  const isReadOnly = (task: Task) =>
    Boolean(task.execution_id) || task.room_id !== roomId || (taskPresentation(task).history && task.status !== 'done')

  const cycleStatus = async (task: Task) => {
    if (task.status === 'todo') {
      await claim(task.id)
    } else if (task.status === 'in_progress') {
      await update(task.id, { status: 'done' })
    } else {
      await requeue(task.id, {
        reason: t('tasks.requeuedReason'),
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
        reason: t('tasks.reassignedReason'),
        assignee_participant_id: assignee,
      })
    }
  }

  const saveTask = async (task: Task, patch: { title: string; assigneeId: string }): Promise<boolean> => {
    try {
      if (patch.title && patch.title !== task.title) await update(task.id, { title: patch.title })
      if (patch.assigneeId !== (task.assignee_participant_id ?? '')) await reassign(task, patch.assigneeId)
      return true
    } catch (error) {
      notify({ message: error instanceof Error ? error.message : t('common.error'), tone: 'error' })
      return false
    }
  }

  const deleteTask = async (task: Task): Promise<boolean> => {
    if (!await confirm({ title: t('tasks.deleteTitle'), description: t('tasks.deleteConfirm', { name: task.title }), confirmLabel: t('tasks.deleteTitle'), destructive: true })) return false
    await remove(task.id)
    return true
  }

  const submitNew = async () => {
    if (!newTitle.trim()) return
    setAdding(true)
    try {
      const created = await create({
        title: newTitle.trim(),
        assignee_participant_id: newAssignee || null,
      })
      if (created) {
        setNewTitle('')
        if (!singleAgentId) setNewAssignee('')
      }
    } catch (error) {
      notify({ message: error instanceof Error ? error.message : t('common.error'), tone: 'error' })
    } finally {
      setAdding(false)
    }
  }

  // The row's trailing text: how long running work has gone on, when
  // finished work ended, or what stopped it. ``null`` falls back to the
  // assignee, which is what a waiting task needs.
  const rowMeta = (task: Task): { text: string; tone: string } | null => {
    const presentation = taskPresentation(task)
    const label = presentation.label ? t(presentation.label) : presentation.status
    if (presentation.history && task.status !== 'done') return { text: label, tone: 'text-[var(--color-foreground-muted)]' }
    if (presentation.status === 'cancelling') return { text: label, tone: 'text-[var(--color-warning)]' }
    const running = task.status === 'in_progress' ? msSince(task.started_at, now) : null
    if (running !== null) return { text: formatRunning(t, running), tone: 'font-medium text-[var(--color-success)]' }
    if (task.status === 'blocked') return { text: label, tone: 'font-medium text-[var(--color-warning)]' }
    if (task.status === 'failed') {
      return { text: task.finished_at ? `${label} · ${formatAgo(t, locale, formatDate, task.finished_at, now)}` : label, tone: 'font-medium text-[var(--color-danger)]' }
    }
    if (task.status === 'done' && task.finished_at) return { text: formatAgo(t, locale, formatDate, task.finished_at, now), tone: 'text-[var(--color-foreground-muted)]' }
    return null
  }

  const renderRow = (task: Task) => {
    const presentation = taskPresentation(task)
    const label = presentation.label ? t(presentation.label) : presentation.status
    const Icon = STATUS_ICON[presentation.status] ?? STATUS_ICON.todo
    const readOnly = isReadOnly(task)
    const assignee = task.assignee_participant_id
      ? participants[task.assignee_participant_id]
      : undefined
    const assigneeName = assignee?.display_name ?? task.assignee_display_name ?? t('tasks.unassigned')
    const meta = rowMeta(task)
    return (
      <article
        key={task.id}
        data-testid={`right-rail-task-row-${task.id}`}
        className="group min-w-0 rounded-[var(--radius-sm)] hover:bg-[var(--color-surface-hover)]"
      >
      <div className="relative flex min-w-0 flex-wrap items-center gap-x-1 gap-y-1 px-1 md:pointer-fine:flex-nowrap">
        <Button
          variant="ghost"
          size="icon"
          onClick={() => cycleStatus(task)}
          aria-label={readOnly ? label : t('tasks.cycle', { status: label })}
          disabled={readOnly}
          title={label}
          className="shrink-0"
        >
          <Icon className={`h-4 w-4 ${statusIconColor(task.status)}`} />
        </Button>
        {/* The title opens the detail and edit dialog; the whole
            remaining width goes to it so short rails truncate less. */}
        <button
          type="button"
          onClick={() => setOpenTaskId(task.id)}
          aria-label={t('tasks.open', { name: task.title })}
          title={task.title}
          data-testid={`right-rail-task-open-${task.id}`}
          className={`flex min-h-[var(--control-icon-size)] min-w-0 flex-1 items-center truncate rounded-[var(--radius-sm)] text-left text-sm focus-visible:outline-2 focus-visible:outline-[var(--color-brand-focus)] ${
            task.status === 'done'
              ? 'line-through text-[var(--color-foreground-muted)]'
              : task.status === 'failed'
                ? 'text-[var(--color-danger)]'
                : 'text-[var(--color-foreground)]'
          }`}
        >
          <span className="min-w-0 truncate">{task.title}</span>
        </button>
        {/* Touch rows show the picker on its own line, so the time sits
            beside the title instead of behind the picker. */}
        {!readOnly && meta && <span className={`shrink-0 pr-1 text-xs md:pointer-fine:hidden ${meta.tone}`}>{meta.text}</span>}
        {/* Keep the native picker usable on touch; desktop hover replaces
            the assignee label, and the delete action covers the label's
            tail only while hovered, so no width is held back for it. */}
        {readOnly ? <span data-testid={`right-rail-task-owner-${task.id}`} className={`max-w-[8rem] shrink-0 truncate pr-1 text-xs ${meta?.tone ?? 'text-[var(--color-foreground-muted)]'}`} title={meta ? `${meta.text} · ${assigneeName}` : assigneeName}>{meta?.text ?? assigneeName}</span> : <div className={`relative ml-[calc(var(--control-icon-size)+.25rem)] flex h-[var(--control-sm-height)] w-[calc(100%-var(--control-icon-size)-.25rem)] min-w-0 items-center md:pointer-fine:ml-0 md:pointer-fine:w-auto md:pointer-fine:min-w-[5rem] md:pointer-fine:max-w-[8rem] md:pointer-fine:flex-[0_1_auto] md:pointer-fine:pr-0 ${task.source_message_id ? '' : 'pr-[calc(var(--control-icon-size)+.25rem)]'}`}>
          <span
            data-testid={`right-rail-task-meta-${task.id}`}
            className={`invisible block w-full min-w-0 max-w-full truncate pr-1 text-right text-xs md:pointer-fine:visible md:pointer-fine:group-hover:invisible md:pointer-fine:group-focus-within:invisible ${meta?.tone ?? 'text-[var(--color-foreground-subtle)]'}`}
            title={meta ? `${meta.text} · ${assigneeName}` : assigneeName}
          >
            {meta?.text ?? assignee?.display_name ?? t('tasks.unassigned')}
          </span>
          <Select
            value={task.assignee_participant_id ?? ''}
            onChange={(e) => reassign(task, e.target.value)}
            onClick={(e) => e.stopPropagation()}
            className={`absolute inset-y-0 left-0 h-[var(--control-sm-height)] min-w-0 max-w-full truncate px-1 text-base opacity-100 transition-opacity md:pointer-fine:text-xs md:pointer-fine:opacity-0 md:pointer-fine:group-hover:opacity-100 md:pointer-fine:group-focus-within:opacity-100 ${task.source_message_id ? 'w-full' : 'w-[calc(100%-var(--control-icon-size)-.25rem)]'}`}
            aria-label={t('tasks.reassign', { name: task.title })}
            data-testid={`right-rail-task-assignee-${task.id}`}
          >
            <option value="">— {t('tasks.unassigned')} —</option>
            {agentParticipants.map((p) => (
              <option key={p.id} value={p.id}>
                {p.display_name}
              </option>
            ))}
          </Select>
        </div>}
        {!readOnly && !task.source_message_id ? (
          <Button
            variant="ghost"
            size="icon"
            onClick={() => deleteTask(task)}
            className="absolute bottom-0 right-1 text-[var(--color-destructive)] opacity-100 transition-opacity hover:bg-[var(--color-danger-soft)] md:pointer-fine:bottom-auto md:pointer-fine:top-1/2 md:pointer-fine:-translate-y-1/2 md:pointer-fine:opacity-0 md:pointer-fine:group-hover:opacity-100 md:pointer-fine:group-focus-within:opacity-100"
            aria-label={t('tasks.delete', { name: task.title })}
          >
            <Trash2 className="h-3 w-3" />
          </Button>
        ) : null}
      </div>
      </article>
    )
  }

  return (
    <section className="flex min-w-0 flex-col">
      <header className="flex items-baseline justify-between px-3 py-2">
        <h3 className="text-sm font-semibold text-[var(--color-foreground)]">
          {t('chat.tasks')}
        </h3>
        <div className="flex items-center gap-1.5">
          <span className="text-xs text-[var(--color-foreground-subtle)]">
            {tasks.length}
          </span>
          {/* #313 — Auto-route via room representative. Disabled
              when nothing is unassigned (avoids a wasted LLM
              roundtrip) or while a request is in flight. */}
          <Button
            variant="ghost"
            size="icon"
            type="button"
            onClick={handleAutoRoute}
            disabled={routing || unassignedCount === 0}
            aria-label={t('tasks.autoRoute')}
            title={
              unassignedCount === 0
                ? t('tasks.noUnassigned')
                : t('tasks.autoRouteCount', { count: unassignedCount })
            }
            data-testid="right-rail-auto-route-button"
            className="text-[var(--color-foreground-muted)]"
          >
            {routing ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <Wand2 className="h-3.5 w-3.5" />
            )}
          </Button>
        </div>
      </header>
      {routeMessage && (
        <p
          className="break-words px-3 pb-1 text-xs text-[var(--color-foreground-muted)]"
          role="status"
          data-testid="right-rail-route-toast"
        >
          {routeMessage}
        </p>
      )}
      <div className="min-w-0 px-1 pb-1">
        {error && <p className="px-3 py-2 text-xs text-[var(--color-danger)]" role="alert">{error.match(/HTTP (\d+)/) ? t('tasks.requestFailed', { status: error.match(/HTTP (\d+)/)?.[1] ?? '' }) : error}</p>}
        {loading && <p className="px-3 py-2 text-xs text-[var(--color-foreground-muted)]">{t('common.loading')}</p>}
        {!loading && !error && tasks.length === 0 && (
          <div className="px-3 py-4 text-center text-caption font-normal text-[var(--color-foreground-subtle)]">
            {t('tasks.empty')}
          </div>
        )}
        {current.map(renderRow)}
        {recentResults.length > 0 && <div className="min-w-0" data-testid="right-rail-recent-results">
          {recentResults.map(renderRow)}
        </div>}
        {remainingHistory.length > 0 && <details className="min-w-0" data-testid="right-rail-task-history">
          <summary className="min-h-11 cursor-pointer content-center px-2 text-sm text-[var(--color-brand-text)] md:pointer-fine:min-h-8">{t('contextRail.history')} ({remainingHistory.length})</summary>
          {remainingHistory.map(renderRow)}
        </details>}
      </div>
      <TaskDetailDialog
        task={openTask}
        participants={participants}
        agentParticipants={agentParticipants}
        readOnly={openTask ? isReadOnly(openTask) : true}
        onClose={() => setOpenTaskId(null)}
        onNavigate={onNavigate}
        onCycleStatus={cycleStatus}
        onSave={saveTask}
        onDelete={deleteTask}
      />
      {/* Inline create input + assignee picker (#312). The picker
          renders even on rooms with a single agent — disabled in
          that case so the chip is informative read-only ("auto-
          assigned to <name>") rather than feeling like an empty
          dropdown the user has to deal with. */}
      <div className="min-w-0 space-y-2 border-t border-[var(--color-border)] px-3 py-2">
        <Input
          value={newTitle}
          onChange={(e) => setNewTitle(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && submitNew()}
          placeholder={t('tasks.addPlaceholder')}
          aria-label={t('tasks.addPlaceholder')}
          className="min-w-0"
        />
        <div className="flex min-w-0 items-center gap-2">
          <Select
            value={newAssignee}
            onChange={(e) => setNewAssignee(e.target.value)}
            disabled={singleAgentRoom || agentParticipants.length === 0}
            className="min-w-0 flex-1 truncate"
            aria-label={t('tasks.pickAssignee')}
            data-testid="right-rail-task-create-assignee"
          >
            <option value="">— {t('tasks.unassigned')} —</option>
            {agentParticipants.map((p) => (
              <option key={p.id} value={p.id}>
                {p.display_name}
              </option>
            ))}
          </Select>
          <Button
            variant="ghost"
            size="icon"
            onClick={submitNew}
            disabled={adding || !newTitle.trim()}
            aria-label={t('tasks.create')}
            className="shrink-0"
          >
            <Plus className="h-4 w-4" />
          </Button>
        </div>
      </div>
    </section>
  )
}
