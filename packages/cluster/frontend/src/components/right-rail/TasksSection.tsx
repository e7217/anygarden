import { useState, useMemo, useEffect } from 'react'
import { Link } from 'react-router-dom'
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
import { Input } from '@/components/ui/input'
import { Select } from '@/components/ui/select'
import MarkdownContent from '@/components/MarkdownContent'
import { DispositionNotice } from '@/components/ExecutionState'
import TaskRecoveryState, { hasTaskRecovery } from '@/components/TaskRecoveryState'
import { useRoomTasks, type Task } from '@/hooks/useRoomTasks'
import { taskPresentation } from '@/lib/taskPresentation'
import { autoRouteUnassigned } from '@/lib/routing'
import type { Participant } from '@/pages/ChatPage'
import { useLocale } from '@/i18n/LocaleProvider'
import { useFeedback } from '@/components/feedback/FeedbackProvider'

interface TasksSectionProps {
  roomId: string
  participants: Record<string, Participant>
  onNavigate?: () => void
}

// #319 — ``blocked`` / ``failed`` are system-set statuses (the goals
// sweeper stamps ``failed`` on pickup/execution timeouts; agents can
// stamp ``blocked`` themselves via ``mark_task_status``). They render
// in their own status group but are deliberately *not* in the user
// toggle cycle — the click toggle stays on actionable transitions.
const STATUS_ORDER = ['in_progress', 'cancelling', 'blocked', 'failed', 'todo'] as const
const STATUS_ICON: Record<string, typeof Circle> = {
  todo: Circle,
  in_progress: Clock,
  done: CheckCircle2,
  blocked: PauseCircle,
  failed: XCircle,
  cancelled: XCircle,
  cancelling: PauseCircle,
  superseded: PauseCircle,
}

function TaskExecutionDetails({ task, onNavigate }: { task: Task; onNavigate?: () => void }) {
  const { t, formatDate } = useLocale()
  const dependencies = task.dependency_results ?? []
  const schedule = task.schedule_context
  const originalRequest = task.execution_operating_room_id && task.execution_source_message_id
    ? `/rooms/${task.execution_operating_room_id}?message=${encodeURIComponent(task.execution_source_message_id)}` : null
  if (!task.spec?.trim() && !task.result_markdown?.trim() && dependencies.length === 0 && !schedule && !originalRequest && !task.execution_objective?.trim()) return null
  return (
    <details data-testid={`right-rail-task-details-${task.id}`} className="min-w-0 px-2 pb-2">
      <summary className="min-h-11 cursor-pointer content-center text-xs font-medium text-[var(--color-foreground-muted)] md:min-h-8">
        {t('tasks.details')}
      </summary>
      <div className="min-w-0 space-y-3 rounded-[var(--radius-sm)] border border-[var(--color-border)] bg-[var(--color-surface-alt)] p-2 text-sm [overflow-wrap:anywhere]">
        {task.execution_objective?.trim() && (
          <div><p className="mb-1 text-xs font-semibold">{t('tasks.originalRequest')}</p><MarkdownContent content={task.execution_objective} /></div>
        )}
        {originalRequest && <Link data-testid={`right-rail-task-source-${task.id}`} to={originalRequest} onClick={onNavigate} className="flex min-h-11 items-center text-xs text-[var(--color-brand-text)] underline md:min-h-8">{t('tasks.openOriginalRequest')}</Link>}
        {task.parent_task_title && <p className="text-xs text-[var(--color-foreground-muted)]">{t('tasks.parentTask')}: {task.parent_task_title}</p>}
        {task.spec?.trim() && (
          <div><p className="mb-1 text-xs font-semibold">{t('tasks.instructions')}</p><MarkdownContent content={task.spec} /></div>
        )}
        {task.result_markdown?.trim() && (
          <div><p className="mb-1 text-xs font-semibold">{t('tasks.result')}</p>{task.result_version != null && task.result_version > 0 && <p className="mb-1 text-xs text-[var(--color-foreground-muted)]">{t('tasks.resultVersion', { version: task.result_version })}</p>}<MarkdownContent content={task.result_markdown} /></div>
        )}
        {dependencies.length > 0 && (
          <div>
            <p className="mb-1 text-xs font-semibold">{t('tasks.sourceResults')}</p>
            <div className="space-y-2">
              {dependencies.map(source => (
                <div key={source.task_id} className="min-w-0 border-l-2 border-[var(--color-border)] pl-2">
                  <p className="font-medium">{source.title}</p>
                  <p className="break-all text-xs text-[var(--color-foreground-muted)]">{t('tasks.sourceTask')}: <code>{source.task_id}</code></p>
                  {source.room_id && <p className="break-all text-xs text-[var(--color-foreground-muted)]">{t('tasks.sourceRoom')}: <code>{source.room_id}</code></p>}
                  {source.result_version != null && <p className="text-xs text-[var(--color-foreground-muted)]">{t('tasks.resultVersion', { version: source.result_version })}</p>}
                  {source.result_sha256 && <p className="truncate font-mono text-xs text-[var(--color-foreground-muted)]" title={source.result_sha256}>SHA-256: {source.result_sha256.slice(0, 12)}…</p>}
                  {source.result_markdown?.trim() && <div className="mt-1"><MarkdownContent content={source.result_markdown} /></div>}
                </div>
              ))}
            </div>
          </div>
        )}
        {schedule && (
          <div className="space-y-1 text-xs text-[var(--color-foreground-muted)]">
            <p className="font-semibold text-[var(--color-foreground)]">{t('tasks.schedule')}</p>
            <p className="break-all">{t('tasks.goalSource')}: <code>{schedule.goal_id}</code></p>
            {schedule.scheduled_for && <p>{t('tasks.scheduledFor')}: <time dateTime={schedule.scheduled_for}>{formatDate(new Date(schedule.scheduled_for), { dateStyle: 'medium', timeStyle: 'short', timeZone: schedule.timezone })}</time></p>}
            <p>{t('tasks.timezone')}: <span>{schedule.timezone}</span></p>
            {schedule.overlap_policy === 'wait' && <p>{t('tasks.overlapWait')}</p>}
            {schedule.trigger_source === 'scheduler' && <p>{t('tasks.scheduledTrigger')}</p>}
            {schedule.trigger_source === 'manual' && <p>{t('tasks.manualTrigger')}</p>}
          </div>
        )}
      </div>
    </details>
  )
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
export default function TasksSection({ roomId, participants, onNavigate }: TasksSectionProps) {
  const { t } = useLocale()
  const { confirm, notify } = useFeedback()
  const statusLabel = (status: string): string => {
    switch (status) {
      case 'todo': return t('tasks.todo')
      case 'in_progress': return t('tasks.inProgress')
      case 'done': return t('tasks.done')
      case 'blocked': return t('tasks.blocked')
      case 'failed': return t('tasks.failed')
      default: return status
    }
  }
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

  const grouped = useMemo(() => {
    const groups: Record<string, Task[]> = {}
    for (const task of tasks) {
      const presentation = taskPresentation(task)
      const status = presentation.history ? 'history' : presentation.status
      ;(groups[status] ??= []).push(task)
    }
    groups.history?.sort((a, b) => b.created_at.localeCompare(a.created_at))
    return groups
  }, [tasks])
  const recentResults = (grouped.history ?? []).filter(task => taskPresentation(task).status === 'done').slice(0, 3)
  const remainingHistory = (grouped.history ?? []).filter(task => !recentResults.includes(task))
  const statuses = [...STATUS_ORDER, ...Object.keys(grouped).filter(status => status !== 'history' && !STATUS_ORDER.includes(status as typeof STATUS_ORDER[number]))]

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

  const renderRow = (task: Task) => {
    const presentation = taskPresentation(task)
    const label = presentation.label ? t(presentation.label) : presentation.status
    const Icon = STATUS_ICON[presentation.status] ?? Circle
    const readOnly = Boolean(task.execution_id) || task.room_id !== roomId || presentation.history && task.status !== 'done'
    const assignee = task.assignee_participant_id
      ? participants[task.assignee_participant_id]
      : undefined
    const assigneeName = assignee?.display_name ?? task.assignee_display_name ?? t('tasks.unassigned')
    return (
      <article
        key={task.id}
        data-testid={`right-rail-task-row-${task.id}`}
        className="group min-w-0 rounded-[var(--radius-sm)] hover:bg-[var(--color-surface-hover)]"
      >
      <div className="relative flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 px-2 py-1.5 md:pointer-fine:flex-nowrap">
        <Button
          variant="ghost"
          size="icon"
          onClick={() => cycleStatus(task)}
          aria-label={readOnly ? label : t('tasks.cycle', { status: label })}
          disabled={readOnly}
          title={label}
          className="shrink-0"
        >
          <Icon
            className={`h-4 w-4 ${
              task.status === 'done'
                ? 'text-[var(--color-success)]'
                : task.status === 'in_progress'
                  ? 'text-[var(--color-status-online)]'
                  : task.status === 'failed'
                    ? 'text-[var(--color-danger)]'
                  : task.status === 'blocked'
                      ? 'text-[var(--color-warning)]'
                      : 'text-[var(--color-foreground-subtle)]'
            }`}
          />
        </Button>
        <span
          className={`min-w-0 flex-1 truncate text-sm ${
            task.status === 'done'
              ? 'line-through text-[var(--color-foreground-muted)]'
              : task.status === 'failed'
                ? 'text-[var(--color-danger)]'
                : 'text-[var(--color-foreground)]'
          }`}
          title={task.title}
        >
          {task.title}
        </span>
        {/* Keep the native picker usable on touch; desktop hover replaces
            the assignee label without reserving another row. The delete
            action has its own target beside the picker. */}
        {readOnly ? <span data-testid={`right-rail-task-owner-${task.id}`} className="max-w-full truncate text-xs text-[var(--color-foreground-muted)]" title={assigneeName}>{assigneeName}</span> : <div className={`relative ml-[calc(var(--control-icon-size)+.5rem)] flex h-[var(--control-sm-height)] w-[calc(100%-var(--control-icon-size)-.5rem)] min-w-0 items-center md:pointer-fine:ml-0 md:pointer-fine:w-auto md:pointer-fine:min-w-[5rem] md:pointer-fine:max-w-[8rem] md:pointer-fine:flex-[0_1_8rem] ${task.source_message_id ? '' : 'pr-[calc(var(--control-icon-size)+.25rem)]'}`}>
          <span
            aria-hidden="true"
            className="invisible block w-full min-w-0 max-w-full truncate text-right text-xs text-[var(--color-foreground-subtle)] md:pointer-fine:visible md:pointer-fine:group-hover:invisible md:pointer-fine:group-focus-within:invisible"
            title={assignee?.display_name ?? t('tasks.unassigned')}
          >
            {assignee?.display_name ?? '—'}
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
            onClick={async () => {
              if (await confirm({ title: t('tasks.deleteTitle'), description: t('tasks.deleteConfirm', { name: task.title }), confirmLabel: t('tasks.deleteTitle'), destructive: true })) await remove(task.id)
            }}
            className="absolute bottom-1.5 right-2 text-[var(--color-destructive)] opacity-100 transition-opacity hover:bg-[var(--color-danger-soft)] md:pointer-fine:bottom-auto md:pointer-fine:top-1/2 md:pointer-fine:-translate-y-1/2 md:pointer-fine:opacity-0 md:pointer-fine:group-hover:opacity-100 md:pointer-fine:group-focus-within:opacity-100"
            aria-label={t('tasks.delete', { name: task.title })}
          >
            <Trash2 className="h-3 w-3" />
          </Button>
        ) : null}
      </div>
      {task.execution_id && <div className="flex min-w-0 flex-wrap items-center gap-x-2 px-2 pb-1 text-xs text-[var(--color-foreground-muted)]">
        <Link data-testid={`right-rail-task-room-${task.id}`} to={`/rooms/${task.room_id}`} onClick={onNavigate} className="flex min-h-11 min-w-0 items-center break-words text-[var(--color-brand-text)] underline md:min-h-8">{task.room_name ?? t('tasks.openTaskRoom')}</Link>
        <span>{label}</span>
        {task.input_revision != null && <span>{t('executions.inputVersion', { version: task.input_revision })}</span>}
      </div>}
      {task.execution_id && <div className="px-2 pb-2"><DispositionNotice disposition={task.disposition} inputRevision={task.input_revision} operationAction={task.execution_operation_action} /></div>}
      {hasTaskRecovery(task.recovery) && <details className="mx-2 mb-2 min-w-0">
        <summary className="min-h-11 cursor-pointer content-center text-sm text-[var(--color-foreground-muted)]">{t('contextRail.processingDetails')}</summary>
        <TaskRecoveryState recovery={task.recovery} taskId={task.id} inputRevision={task.input_revision} />
      </details>}
      {(task.status === 'blocked' || task.status === 'failed') && task.error?.trim() && (
        (task.error === 'APPROVAL_REJECTED' && task.recovery?.reason_code !== 'APPROVAL_REJECTED') ||
        !(hasTaskRecovery(task.recovery) && task.recovery?.reason_code)
      ) && (
        <p data-testid={`right-rail-task-reason-${task.id}`} className={`break-words px-2 pb-2 text-xs [overflow-wrap:anywhere] ${task.status === 'failed' ? 'text-[var(--color-danger)]' : 'text-[var(--color-foreground-muted)]'}`}>
          <span className="font-medium">{t('tasks.reason')}: </span>{task.error === 'APPROVAL_REJECTED' ? t('taskRecovery.reason.APPROVAL_REJECTED') : task.error}
        </p>
      )}
      {task.status === 'blocked' && (task.blocked_by?.length ?? 0) > 0 && <div data-testid={`right-rail-task-blockers-${task.id}`} className="space-y-1 px-2 pb-2 text-xs text-[var(--color-foreground-muted)] [overflow-wrap:anywhere]">
        <p className="font-medium">{t('tasks.waitingForWork')}</p>
        {task.blocked_by!.map(blocker => <p key={blocker.task_id}><Link to={`/rooms/${blocker.room_id}`} onClick={onNavigate} className="inline-flex min-h-11 items-center text-[var(--color-brand-text)] underline md:min-h-8">{blocker.title}</Link> · {statusLabel(blocker.status)}</p>)}
      </div>}
      <TaskExecutionDetails task={task} onNavigate={onNavigate} />
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
      <div className="min-w-0 px-1">
        {error && <p className="px-3 py-2 text-xs text-[var(--color-danger)]" role="alert">{error.match(/HTTP (\d+)/) ? t('tasks.requestFailed', { status: error.match(/HTTP (\d+)/)?.[1] ?? '' }) : error}</p>}
        {loading && <p className="px-3 py-2 text-xs text-[var(--color-foreground-muted)]">{t('common.loading')}</p>}
        {!loading && !error && tasks.length === 0 && (
          <div className="px-3 py-4 text-center text-caption font-normal text-[var(--color-foreground-subtle)]">
            {t('tasks.empty')}
          </div>
        )}
        {statuses.map((status) => {
          const items = grouped[status] ?? []
          if (items.length === 0) return null
          return (
            <div key={status} className="mb-1 min-w-0">
              <div className="px-3 pt-1 pb-0.5 text-xs uppercase tracking-wider text-[var(--color-foreground-subtle)]">
                {status === 'cancelling' ? t('executions.status.cancelling') : statusLabel(status)} · {items.length}
              </div>
              {items.slice(0, 5).map(renderRow)}
              {items.length > 5 && <details className="min-w-0">
                <summary className="min-h-11 cursor-pointer content-center px-3 text-sm text-[var(--color-brand-text)]">{t('contextRail.more', { count: items.length })}</summary>
                {items.slice(5).map(renderRow)}
              </details>}
            </div>
          )
        })}
        {recentResults.length > 0 && <div className="min-w-0 pt-2" data-testid="right-rail-recent-results">
          <h4 className="px-3 py-1 text-sm font-semibold">{t('contextRail.recentResults')}</h4>
          {recentResults.map(renderRow)}
        </div>}
        {remainingHistory.length > 0 && <details className="min-w-0" data-testid="right-rail-task-history">
          <summary className="min-h-11 cursor-pointer content-center px-3 text-sm text-[var(--color-brand-text)]">{t('contextRail.history')} ({remainingHistory.length})</summary>
          {remainingHistory.map(renderRow)}
        </details>}
      </div>
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
