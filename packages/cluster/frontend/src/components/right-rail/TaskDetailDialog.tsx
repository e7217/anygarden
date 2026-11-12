import { useEffect, useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { Trash2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select } from '@/components/ui/select'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import MarkdownContent from '@/components/MarkdownContent'
import { DispositionNotice } from '@/components/ExecutionState'
import TaskRecoveryState, { FAILED_ATTEMPT_OUTCOMES, attemptOutcomeKey, hasTaskRecovery } from '@/components/TaskRecoveryState'
import type { Task } from '@/hooks/useRoomTasks'
import { taskPresentation } from '@/lib/taskPresentation'
import { formatElapsed, useNow } from '@/lib/elapsed'
import { formatRunning, formatStamp, msSince } from '@/lib/taskTime'
import type { Participant } from '@/pages/ChatPage'
import { useLocale } from '@/i18n/LocaleProvider'
import { STATUS_ICON, statusIconColor } from './taskStatusIcon'

interface TaskDetailDialogProps {
  task: Task | null
  participants: Record<string, Participant>
  agentParticipants: Participant[]
  readOnly: boolean
  onClose: () => void
  onNavigate?: () => void
  onCycleStatus: (task: Task) => Promise<void>
  onSave: (task: Task, patch: { title: string; assigneeId: string }) => Promise<boolean>
  onDelete: (task: Task) => Promise<boolean>
}

/**
 * Task detail and edit dialog for the right rail. The rail row stays a
 * single line; everything that used to unfold beneath it — run state,
 * recovery, blockers, instructions, results and sources — lives here,
 * next to the fields the API lets people change (title, assignee,
 * status, delete).
 */
export default function TaskDetailDialog({
  task,
  participants,
  agentParticipants,
  readOnly,
  onClose,
  onNavigate,
  onCycleStatus,
  onSave,
  onDelete,
}: TaskDetailDialogProps) {
  const { t } = useLocale()
  const [title, setTitle] = useState('')
  const [assigneeId, setAssigneeId] = useState('')
  const [busy, setBusy] = useState(false)

  // Reset the draft whenever another task opens; live updates to the
  // open task must not overwrite what the person is typing.
  const taskId = task?.id
  useEffect(() => {
    if (!task) return
    setTitle(task.title)
    setAssigneeId(task.assignee_participant_id ?? '')
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [taskId])

  const run = async (action: () => Promise<unknown>) => {
    setBusy(true)
    try {
      await action()
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog open={task !== null} onOpenChange={open => { if (!open) onClose() }}>
      {task && (
        <DialogContent data-testid={`right-rail-task-dialog-${task.id}`} className="max-w-xl">
          <TaskDialogBody
            task={task}
            participants={participants}
            agentParticipants={agentParticipants}
            readOnly={readOnly}
            busy={busy}
            title={title}
            assigneeId={assigneeId}
            setTitle={setTitle}
            setAssigneeId={setAssigneeId}
            onNavigate={onNavigate}
            onCycle={() => run(() => onCycleStatus(task))}
            onSubmit={() => run(async () => {
              if (await onSave(task, { title: title.trim(), assigneeId })) onClose()
            })}
            onDelete={() => run(async () => {
              if (await onDelete(task)) onClose()
            })}
            onCancel={onClose}
            t={t}
          />
        </DialogContent>
      )}
    </Dialog>
  )
}

interface BodyProps {
  task: Task
  participants: Record<string, Participant>
  agentParticipants: Participant[]
  readOnly: boolean
  busy: boolean
  title: string
  assigneeId: string
  setTitle: (value: string) => void
  setAssigneeId: (value: string) => void
  onNavigate?: () => void
  onCycle: () => void
  onSubmit: () => void
  onDelete: () => void
  onCancel: () => void
  t: ReturnType<typeof useLocale>['t']
}

function TaskDialogBody({
  task, participants, agentParticipants, readOnly, busy, title, assigneeId,
  setTitle, setAssigneeId, onNavigate, onCycle, onSubmit, onDelete, onCancel, t,
}: BodyProps) {
  const presentation = taskPresentation(task)
  const statusText = presentation.label ? t(presentation.label) : presentation.status
  const Icon = STATUS_ICON[presentation.status] ?? STATUS_ICON.todo
  const assignee = task.assignee_participant_id ? participants[task.assignee_participant_id] : undefined
  const assigneeName = assignee?.display_name ?? task.assignee_display_name ?? t('tasks.unassigned')
  const nextAction = task.status === 'todo'
    ? t('tasks.start')
    : task.status === 'in_progress' ? t('tasks.markDone') : t('tasks.backToTodo')
  const dirty = title.trim() !== task.title || assigneeId !== (task.assignee_participant_id ?? '')
  const canDelete = !readOnly && !task.source_message_id
  useNow(60_000)
  const now = Date.now()
  const running = task.status === 'in_progress' ? msSince(task.started_at, now) : null
  const took = task.status === 'done' && task.started_at && task.finished_at
    ? Date.parse(task.finished_at) - Date.parse(task.started_at) : NaN
  const duration = running !== null
    ? formatRunning(t, running)
    : Number.isFinite(took) && took >= 0 ? t('tasks.took', { time: formatElapsed(t, took) }) : null

  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (dirty && title.trim()) onSubmit()
  }

  return (
    <form onSubmit={submit} className="grid min-w-0 gap-4">
      <DialogHeader>
        <DialogTitle className="[overflow-wrap:anywhere]">{task.title}</DialogTitle>
        <DialogDescription className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <span data-testid={`right-rail-task-status-${task.id}`} className="inline-flex items-center gap-1.5 font-medium text-[var(--color-foreground)]">
            <Icon aria-hidden="true" className={`h-4 w-4 ${statusIconColor(presentation.status)}`} />
            {statusText}
          </span>
          <span aria-hidden="true">·</span>
          <span>{assigneeName}</span>
          {duration && <><span aria-hidden="true">·</span><span>{duration}</span></>}
        </DialogDescription>
      </DialogHeader>

      <TaskStateDetails task={task} participants={participants} now={now} onNavigate={onNavigate} t={t} />

      {readOnly ? (
        <p className="text-sm text-[var(--color-foreground-muted)]">{t('tasks.readOnly')}</p>
      ) : (
        <div className="grid min-w-0 gap-4 border-t border-[var(--color-border)] pt-4">
          <div className="grid gap-2">
            <Label htmlFor={`task-title-${task.id}`}>{t('tasks.title')}</Label>
            <Input
              id={`task-title-${task.id}`}
              value={title}
              maxLength={500}
              onChange={e => setTitle(e.target.value)}
              data-testid="right-rail-task-dialog-title"
            />
          </div>
          <div className="grid gap-2">
            <Label htmlFor={`task-assignee-${task.id}`}>{t('tasks.assignee')}</Label>
            <Select
              id={`task-assignee-${task.id}`}
              value={assigneeId}
              onChange={e => setAssigneeId(e.target.value)}
              data-testid="right-rail-task-dialog-assignee"
            >
              <option value="">— {t('tasks.unassigned')} —</option>
              {agentParticipants.map(p => <option key={p.id} value={p.id}>{p.display_name}</option>)}
            </Select>
          </div>
          <div className="grid gap-2">
            <span className="text-sm font-semibold">{t('tasks.status')}</span>
            <div className="flex flex-wrap items-center gap-2">
              <Button type="button" variant="outline" onClick={onCycle} disabled={busy} data-testid="right-rail-task-dialog-cycle">
                {nextAction}
              </Button>
            </div>
          </div>
        </div>
      )}

      <DialogFooter className="items-stretch sm:items-center">
        {canDelete && (
          <Button
            type="button"
            variant="ghost"
            onClick={onDelete}
            disabled={busy}
            className="text-[var(--color-destructive)] hover:bg-[var(--color-danger-soft)] sm:mr-auto"
            data-testid="right-rail-task-dialog-delete"
          >
            <Trash2 aria-hidden="true" />
            {t('tasks.deleteTitle')}
          </Button>
        )}
        {readOnly ? (
          <Button type="button" variant="outline" onClick={onCancel}>{t('common.close')}</Button>
        ) : (
          <>
            <Button type="button" variant="outline" onClick={onCancel}>{t('common.cancel')}</Button>
            <Button type="submit" disabled={busy || !dirty || !title.trim()} data-testid="right-rail-task-dialog-save">
              {t('common.save')}
            </Button>
          </>
        )}
      </DialogFooter>
    </form>
  )
}

/** Run state, recovery, blockers and the task's evidence, read-only. */
function TaskStateDetails({ task, participants, now, onNavigate, t }: { task: Task; participants: Record<string, Participant>; now: number; onNavigate?: () => void; t: BodyProps['t'] }) {
  const { formatDate } = useLocale()
  const dependencies = task.dependency_results ?? []
  const schedule = task.schedule_context
  const originalRequest = task.execution_operating_room_id && task.execution_source_message_id
    ? `/rooms/${task.execution_operating_room_id}?message=${encodeURIComponent(task.execution_source_message_id)}` : null
  const showReason = (task.status === 'blocked' || task.status === 'failed') && task.error?.trim() && (
    (task.error === 'APPROVAL_REJECTED' && task.recovery?.reason_code !== 'APPROVAL_REJECTED') ||
    !(hasTaskRecovery(task.recovery) && task.recovery?.reason_code)
  )
  const statusLabel = (status: string) => {
    switch (status) {
      case 'todo': return t('tasks.todo')
      case 'in_progress': return t('tasks.inProgress')
      case 'done': return t('tasks.done')
      case 'blocked': return t('tasks.blocked')
      case 'failed': return t('tasks.failed')
      default: return status
    }
  }

  // Running work leads with its progress; finished work leads with what
  // it produced, then how it got there.
  const timeline = <TaskTimeline task={task} participants={participants} now={now} t={t} />
  const finished = task.status === 'done'

  return (
    <div data-testid={`right-rail-task-details-${task.id}`} className="grid min-w-0 gap-4 text-sm [overflow-wrap:anywhere]">
      {task.execution_id && (
        <div className="grid gap-2">
          <Link data-testid={`right-rail-task-room-${task.id}`} to={`/rooms/${task.room_id}`} onClick={onNavigate} className="inline-flex min-h-8 items-center text-[var(--color-brand-text)] underline">
            {task.room_name ?? t('tasks.openTaskRoom')}
          </Link>
          <DispositionNotice disposition={task.disposition} inputRevision={task.input_revision} operationAction={task.execution_operation_action} />
        </div>
      )}
      {showReason && (
        <p data-testid={`right-rail-task-reason-${task.id}`} className={task.status === 'failed' ? 'text-[var(--color-danger)]' : 'text-[var(--color-foreground-muted)]'}>
          <span className="font-medium">{t('tasks.reason')}: </span>
          {task.error === 'APPROVAL_REJECTED' ? t('taskRecovery.reason.APPROVAL_REJECTED') : task.error}
        </p>
      )}
      {!finished && timeline}
      {hasTaskRecovery(task.recovery) && (
        <section className="grid gap-1">
          <h3 className="text-sm font-semibold">{t('contextRail.processingDetails')}</h3>
          <TaskRecoveryState recovery={task.recovery} taskId={task.id} inputRevision={task.input_revision} hideHistory />
        </section>
      )}
      {task.status === 'blocked' && (task.blocked_by?.length ?? 0) > 0 && (
        <section data-testid={`right-rail-task-blockers-${task.id}`} className="grid gap-1 text-[var(--color-foreground-muted)]">
          <h3 className="text-sm font-semibold text-[var(--color-foreground)]">{t('tasks.waitingForWork')}</h3>
          {task.blocked_by!.map(blocker => (
            <p key={blocker.task_id}>
              <Link to={`/rooms/${blocker.room_id}`} onClick={onNavigate} className="inline-flex min-h-8 items-center text-[var(--color-brand-text)] underline">{blocker.title}</Link> · {statusLabel(blocker.status)}
            </p>
          ))}
        </section>
      )}
      {task.execution_objective?.trim() && (
        <section><h3 className="mb-1 text-sm font-semibold">{t('tasks.originalRequest')}</h3><MarkdownContent content={task.execution_objective} /></section>
      )}
      {originalRequest && <Link data-testid={`right-rail-task-source-${task.id}`} to={originalRequest} onClick={onNavigate} className="inline-flex min-h-8 items-center text-[var(--color-brand-text)] underline">{t('tasks.openOriginalRequest')}</Link>}
      {task.parent_task_title && <p className="text-[var(--color-foreground-muted)]">{t('tasks.parentTask')}: {task.parent_task_title}</p>}
      {task.spec?.trim() && (
        <section><h3 className="mb-1 text-sm font-semibold">{t('tasks.instructions')}</h3><MarkdownContent content={task.spec} /></section>
      )}
      {task.result_markdown?.trim() && (
        <section>
          <h3 className="mb-1 text-sm font-semibold">{t('tasks.result')}</h3>
          {task.result_version != null && task.result_version > 0 && <p className="mb-1 text-xs text-[var(--color-foreground-muted)]">{t('tasks.resultVersion', { version: task.result_version })}</p>}
          <MarkdownContent content={task.result_markdown} />
        </section>
      )}
      {finished && timeline}
      {dependencies.length > 0 && (
        <section>
          <h3 className="mb-1 text-sm font-semibold">{t('tasks.sourceResults')}</h3>
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
        </section>
      )}
      {schedule && (
        <section className="space-y-1 text-xs text-[var(--color-foreground-muted)]">
          <h3 className="text-sm font-semibold text-[var(--color-foreground)]">{t('tasks.schedule')}</h3>
          <p className="break-all">{t('tasks.goalSource')}: <code>{schedule.goal_id}</code></p>
          {schedule.scheduled_for && <p>{t('tasks.scheduledFor')}: <time dateTime={schedule.scheduled_for}>{formatDate(new Date(schedule.scheduled_for), { dateStyle: 'medium', timeStyle: 'short', timeZone: schedule.timezone })}</time></p>}
          <p>{t('tasks.timezone')}: <span>{schedule.timezone}</span></p>
          {schedule.overlap_policy === 'wait' && <p>{t('tasks.overlapWait')}</p>}
          {schedule.trigger_source === 'scheduler' && <p>{t('tasks.scheduledTrigger')}</p>}
          {schedule.trigger_source === 'manual' && <p>{t('tasks.manualTrigger')}</p>}
        </section>
      )}
    </div>
  )
}

type TimelineTone = 'neutral' | 'success' | 'warning' | 'danger'

interface TimelineEvent {
  key: string
  at: string | null
  label: string
  detail?: string
  tone: TimelineTone
  /** The task's present state, drawn as the open end of the line. */
  current?: boolean
}

const TONE_TEXT: Record<TimelineTone, string> = {
  neutral: 'text-[var(--color-foreground)]',
  success: 'text-[var(--color-success)]',
  warning: 'text-[var(--color-warning)]',
  danger: 'text-[var(--color-danger)]',
}

const validStamp = (iso: string | null | undefined): iso is string => Boolean(iso) && !Number.isNaN(Date.parse(iso!))

/**
 * Created → assigned → started (or each run attempt) → finished, from the
 * stamps the server keeps. A requeue clears start and finish, so the line
 * shows the latest pass through the task, not every pass.
 */
function TaskTimeline({ task, participants, now, t }: { task: Task; participants: Record<string, Participant>; now: number; t: BodyProps['t'] }) {
  const { formatDate } = useLocale()
  const creator = task.created_by
    ? Object.values(participants).find(p => p.user_id === task.created_by)?.display_name
    : undefined
  const assignee = task.assignee_participant_id ? participants[task.assignee_participant_id] : undefined
  const assigneeName = assignee?.display_name ?? task.assignee_display_name
  const attempts = task.recovery?.attempts ?? []

  const events: TimelineEvent[] = [{ key: 'created', at: task.created_at, label: t('tasks.event.created'), detail: creator, tone: 'neutral' }]
  if (validStamp(task.assigned_at) && assigneeName) {
    events.push({ key: 'assigned', at: task.assigned_at, label: t('tasks.event.assigned', { name: assigneeName }), tone: 'neutral' })
  }
  if (attempts.length > 0) {
    for (const attempt of attempts) {
      if (validStamp(attempt.started_at)) {
        events.push({ key: `attempt-${attempt.ordinal}-start`, at: attempt.started_at, label: t('tasks.event.attemptStarted', { count: attempt.ordinal }), tone: 'neutral' })
      }
      const outcome = attempt.outcome ?? attempt.state
      if (validStamp(attempt.finished_at)) {
        events.push({
          key: `attempt-${attempt.ordinal}-end`,
          at: attempt.finished_at,
          label: t('tasks.event.attemptEnded', { count: attempt.ordinal, outcome: t(attemptOutcomeKey(outcome)) }),
          tone: FAILED_ATTEMPT_OUTCOMES.includes(outcome) || attempt.reason_code ? 'danger' : 'neutral',
        })
      }
    }
  } else if (validStamp(task.started_at)) {
    events.push({ key: 'started', at: task.started_at, label: t('tasks.event.started'), tone: 'neutral' })
  }
  if (validStamp(task.finished_at) && (task.status === 'done' || task.status === 'failed')) {
    events.push({
      key: 'finished',
      at: task.finished_at,
      label: t(task.status === 'done' ? 'tasks.event.done' : 'tasks.event.failed'),
      detail: task.status === 'done' && task.result_version ? t('tasks.resultVersion', { version: task.result_version }) : undefined,
      tone: task.status === 'done' ? 'success' : 'danger',
    })
  }
  events.sort((a, b) => Date.parse(a.at ?? '') - Date.parse(b.at ?? ''))

  const presentation = taskPresentation(task)
  if (!presentation.history && task.status !== 'done' && task.status !== 'failed') {
    const running = task.status === 'in_progress' ? msSince(task.started_at, now) : null
    const label = running !== null
      ? `${t('tasks.inProgress')} · ${formatRunning(t, running)}`
      : task.status === 'todo' ? t('tasks.event.waiting') : presentation.label ? t(presentation.label) : task.status
    const tone: TimelineTone = task.status === 'in_progress' ? 'success' : task.status === 'blocked' || presentation.status === 'cancelling' ? 'warning' : 'neutral'
    events.push({ key: 'now', at: null, label, tone, current: true })
  }

  return (
    <section data-testid={`right-rail-task-timeline-${task.id}`} className="grid gap-2">
      <h3 className="text-sm font-semibold">{t('tasks.timeline')}</h3>
      <ol className="grid">
        {events.map((event, index) => {
          const last = index === events.length - 1
          return (
            <li key={event.key} className="relative grid grid-cols-[1.25rem_minmax(0,1fr)_auto] items-start gap-x-2.5 pb-3 last:pb-0">
              {!last && <span aria-hidden="true" className="absolute bottom-0 left-[0.625rem] top-3.5 w-px -translate-x-1/2 bg-[var(--color-border)]" />}
              <span aria-hidden="true" className="relative flex justify-center pt-1.5">
                {event.current
                  ? <span className={`size-3 -mt-0.5 rounded-full border-2 bg-[var(--color-surface-elevated)] ${event.tone === 'success' ? 'border-[var(--color-success)]' : event.tone === 'warning' ? 'border-[var(--color-warning)]' : 'border-[var(--color-border-strong)]'}`} />
                  : <span className={`size-2 rounded-full ${event.tone === 'success' ? 'bg-[var(--color-success)]' : event.tone === 'danger' ? 'bg-[var(--color-danger)]' : 'bg-[var(--color-border-strong)]'}`} />}
              </span>
              <span className={`min-w-0 ${event.current || event.tone !== 'neutral' ? 'font-medium' : ''} ${TONE_TEXT[event.tone]}`}>
                {event.label}
                {event.detail && <span className="font-normal text-[var(--color-foreground-muted)]"> · {event.detail}</span>}
              </span>
              {event.at
                ? <time dateTime={event.at} className="pt-0.5 text-xs tabular-nums text-[var(--color-foreground-muted)]">{formatStamp(formatDate, event.at, now)}</time>
                : <span className="pt-0.5 text-xs text-[var(--color-foreground-muted)]">{t('tasks.event.now')}</span>}
            </li>
          )
        })}
      </ol>
    </section>
  )
}
