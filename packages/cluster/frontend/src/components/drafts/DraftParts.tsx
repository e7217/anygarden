/**
 * #947/#948 — goal and routine drafts in the chat.
 *
 * - ``DraftSummary``: what a draft card creates, in words instead of JSON.
 * - ``DraftEditDialog``: the goal or routine form filled with the draft;
 *   submitting approves the card with the edited values.
 * - ``DraftRequestNote`` / ``DraftFailedNote``: the request a person made,
 *   and the notice when the agent ended its turn without a draft.
 */
import { useMemo, type ReactNode } from 'react'
import { FilePenLine, FileWarning } from 'lucide-react'
import CampaignForm from '@/components/campaign/CampaignForm'
import { draftFromCampaignInput } from '@/components/campaign/campaignDraft'
import GoalForm, { type GoalDraftValues } from '@/components/goal-form/GoalForm'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { useLocale } from '@/i18n/LocaleProvider'
import type { MessageKey } from '@/i18n/messages'
import type { CampaignCreateInput } from '@/lib/campaigns'
import { SLASH_FORM_OPEN_EVENT, type DraftFailedMeta, type DraftRequestMeta } from '@/lib/drafts'
import { routineAgentOptions } from '@/lib/goals'
import type { Participant } from '@/lib/participants'
import type { SlashFormRequest } from '@/lib/slashCommands'

type Translate = (key: MessageKey, vars?: Record<string, string | number>) => string

const record = (value: unknown): Record<string, unknown> =>
  value && typeof value === 'object' ? (value as Record<string, unknown>) : {}
const text = (value: unknown): string => (typeof value === 'string' ? value : value == null ? '' : String(value))

function agentName(participants: Record<string, Participant>, agentId: string): string {
  return Object.values(participants).find(p => p.agent_id === agentId)?.display_name ?? agentId
}

const WEEKDAYS: Record<string, MessageKey> = {
  '0': 'chat.draft.sun', '1': 'chat.draft.mon', '2': 'chat.draft.tue', '3': 'chat.draft.wed',
  '4': 'chat.draft.thu', '5': 'chat.draft.fri', '6': 'chat.draft.sat', '7': 'chat.draft.sun',
}

/** A cron or interval schedule in words; ``null`` when it is not a common shape. */
export function describeSchedule(t: Translate, triggerType: string, config: Record<string, unknown>): string | null {
  if (triggerType === 'manual') return t('chat.draft.manual')
  if (triggerType === 'interval') {
    const seconds = Number(config.interval_seconds)
    if (!Number.isFinite(seconds) || seconds <= 0) return null
    if (seconds % 3600 === 0) return t('chat.draft.everyHours', { n: seconds / 3600 })
    if (seconds % 60 === 0) return t('chat.draft.everyMinutes', { n: seconds / 60 })
    return t('chat.draft.everySeconds', { n: seconds })
  }
  const parts = text(config.cron).trim().split(/\s+/)
  if (parts.length !== 5) return null
  const [minute, hour, dom, month, dow] = parts
  if (!/^\d+$/.test(minute) || !/^\d+$/.test(hour) || dom !== '*' || month !== '*') return null
  const time = `${hour.padStart(2, '0')}:${minute.padStart(2, '0')}`
  if (dow === '*') return t('chat.draft.daily', { time })
  if (dow === '1-5') return t('chat.draft.weekdays', { time })
  if (WEEKDAYS[dow]) return t('chat.draft.weekly', { day: t(WEEKDAYS[dow]), time })
  return null
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid grid-cols-[minmax(6rem,auto)_1fr] gap-x-3 gap-y-0.5">
      <dt className="text-caption text-[var(--color-foreground-muted)]">{label}</dt>
      <dd className="min-w-0 break-words text-caption text-[var(--color-foreground)]">{children}</dd>
    </div>
  )
}

const mono = 'font-mono break-all'

function GoalSummary({ spec }: { spec: Record<string, unknown> }) {
  const { t } = useLocale()
  const metric = record(spec.metric)
  const evaluator = record(spec.evaluator)
  const budget = record(spec.budget)
  const mode = spec.mode === 'optimize' ? t('campaigns.form.modeOptimize') : t('campaigns.form.modeTarget')
  const direction = metric.direction === 'minimize' ? t('campaigns.form.directionMinimize') : t('campaigns.form.directionMaximize')
  const deadline = text(spec.deadline_at)
  return (
    <dl className="flex flex-col gap-1" data-testid="draft-summary-goal">
      <Row label={t('campaigns.form.name')}>{text(spec.title)}</Row>
      <Row label={t('campaigns.form.purpose')}>{text(spec.purpose)}</Row>
      <Row label={t('chat.draft.mode')}>{mode}</Row>
      <Row label={t('chat.draft.metric')}>{`${text(metric.name)} · ${direction}`}</Row>
      {metric.target != null && <Row label={t('campaigns.form.target')}>{text(metric.target)}</Row>}
      {deadline && <Row label={t('campaigns.form.deadline')}>{new Date(deadline).toLocaleString()}</Row>}
      <Row label={t('campaigns.form.command')}><code className={mono}>{text(evaluator.command)}</code></Row>
      <Row label={t('campaigns.form.scoreKey')}><code className={mono}>{text(evaluator.score_key || 'score')}</code></Row>
      <Row label={t('campaigns.form.budget')}>{Number(budget.max_total_tokens).toLocaleString()}</Row>
    </dl>
  )
}

const SPEC_PREVIEW = 240

function RoutineSummary({ params, participants, roomId }: {
  params: Record<string, unknown>
  participants: Record<string, Participant>
  roomId: string
}) {
  const { t } = useLocale()
  const config = record(params.trigger_config)
  const triggerType = text(params.trigger_type)
  const words = describeSchedule(t, triggerType, config)
  const raw = triggerType === 'cron' ? text(config.cron) : triggerType === 'interval' ? `${text(config.interval_seconds)}s` : ''
  const spec = text(params.spec)
  const reportRoom = text(params.report_room_id)
  return (
    <dl className="flex flex-col gap-1" data-testid="draft-summary-routine">
      <Row label={t('goals.title')}>{text(params.title)}</Row>
      <Row label={t('goals.agent')}>{agentName(participants, text(params.agent_id))}</Row>
      <Row label={t('goals.schedule')}>
        {words ?? raw}
        {words && raw && <code className={`${mono} ml-2 text-[var(--color-foreground-muted)]`}>{raw}</code>}
      </Row>
      {triggerType === 'cron' && <Row label={t('chat.draft.timezone')}>{text(config.timezone) || 'UTC'}</Row>}
      <Row label={t('goals.instructions')}>
        <span className="whitespace-pre-wrap">{spec.length > SPEC_PREVIEW ? `${spec.slice(0, SPEC_PREVIEW)}…` : spec}</span>
      </Row>
      <Row label={t('goals.reportRoom')}>
        {!reportRoom ? t('chat.draft.noReportRoom') : reportRoom === roomId ? t('chat.draft.thisRoom') : <code className={mono}>{reportRoom}</code>}
      </Row>
      <Row label={t('goals.recording')}>
        {params.materialize === 'full' ? t('goals.full') : t('goals.interestingOnly')}
      </Row>
    </dl>
  )
}

export function DraftSummary({ action, params, participants, roomId }: {
  action: string
  params: Record<string, unknown>
  participants: Record<string, Participant>
  roomId: string
}) {
  if (action === 'create_goal') return <GoalSummary spec={record(params.spec)} />
  return <RoutineSummary params={params} participants={participants} roomId={roomId} />
}

export function DraftEditDialog({ action, params, participants, open, onClose, onApprove }: {
  action: string
  params: Record<string, unknown>
  participants: Record<string, Participant>
  open: boolean
  onClose: () => void
  /** Approves the card with the edited values. */
  onApprove: (params: Record<string, unknown>) => Promise<unknown>
}) {
  const { t } = useLocale()
  const agents = useMemo(() => routineAgentOptions(participants), [participants])
  const goalDraft = useMemo(
    () => (action === 'create_goal' ? draftFromCampaignInput(record(params.spec) as Partial<CampaignCreateInput>) : null),
    [action, params],
  )
  if (action === 'create_goal') {
    return (
      <CampaignForm
        open={open}
        onOpenChange={next => { if (!next) onClose() }}
        initialDraft={goalDraft}
        // The form's values replace the draft's; a cleared field stays cleared.
        onSubmit={input => onApprove({ ...params, spec: input })}
      />
    )
  }
  const initial: GoalDraftValues = {
    agent_id: text(params.agent_id),
    title: text(params.title),
    spec: text(params.spec),
    trigger_type: (params.trigger_type as GoalDraftValues['trigger_type']) ?? 'cron',
    trigger_config: record(params.trigger_config),
    materialize: params.materialize === 'full' ? 'full' : 'interesting_only',
    report_room_id: text(params.report_room_id) || null,
  }
  return (
    <Dialog open={open} onOpenChange={next => { if (!next) onClose() }}>
      <DialogContent className="max-w-md max-h-[min(90dvh,52rem)] overflow-y-auto" data-testid="draft-routine-dialog">
        <DialogHeader>
          <DialogTitle>{t('chat.slash.routineTitle')}</DialogTitle>
          <DialogDescription>{t('chat.slash.routineDescription')}</DialogDescription>
        </DialogHeader>
        {open && (
          <GoalForm
            roomAgents={agents}
            initial={initial}
            onSubmit={(agentId, input) => onApprove({ ...input, agent_id: agentId, materialize: input.materialize ?? 'interesting_only' })}
            onCreated={onClose}
            onCancel={onClose}
          />
        )}
      </DialogContent>
    </Dialog>
  )
}

const noteClass =
  'flex w-full max-w-xl flex-col gap-1 rounded-[var(--radius-md)] border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 shadow-whisper'

export function DraftRequestNote({ meta, participants }: { meta: DraftRequestMeta; participants: Record<string, Participant> }) {
  const { t } = useLocale()
  const drafter = participants[meta.drafter_participant_id]?.display_name ?? t('chat.draft.anAgent')
  return (
    <div data-testid="draft-request-note" className={noteClass}>
      <span className="inline-flex items-center gap-1.5 text-caption font-semibold text-[var(--color-foreground)]">
        <FilePenLine className="h-4 w-4 text-[var(--color-brand-text)]" aria-hidden />
        {t(meta.kind === 'goal' ? 'chat.draft.goalRequested' : 'chat.draft.routineRequested', { name: drafter })}
      </span>
      <p className="whitespace-pre-wrap break-words text-body text-[var(--color-foreground)]">{meta.description}</p>
    </div>
  )
}

export function DraftFailedNote({ meta, participants }: { meta: DraftFailedMeta; participants: Record<string, Participant> }) {
  const { t } = useLocale()
  const openForm = () => {
    const assignee = meta.assignee_agent_id
      ? Object.values(participants).find(p => p.agent_id === meta.assignee_agent_id)?.id ?? null
      : null
    // The form opens empty except for the words the person typed.
    const detail: SlashFormRequest = meta.kind === 'goal'
      ? { command: 'goal', payload: { text: '' } }
      : { command: 'routine', payload: { assignee_pid: assignee, text: '' } }
    window.dispatchEvent(new CustomEvent(SLASH_FORM_OPEN_EVENT, { detail }))
  }
  return (
    <div data-testid="draft-failed-note" className={noteClass}>
      <span className="inline-flex items-center gap-1.5 text-caption font-semibold text-[var(--color-foreground)]">
        <FileWarning className="h-4 w-4 text-[var(--color-warning)]" aria-hidden />
        {t(meta.kind === 'goal' ? 'chat.draft.goalFailed' : 'chat.draft.routineFailed')}
      </span>
      <p className="whitespace-pre-wrap break-words text-caption text-[var(--color-foreground-muted)]">{meta.description}</p>
      <div>
        <Button type="button" size="sm" variant="outline" onClick={openForm}>
          {t('chat.draft.openEmptyForm')}
        </Button>
      </div>
    </div>
  )
}
