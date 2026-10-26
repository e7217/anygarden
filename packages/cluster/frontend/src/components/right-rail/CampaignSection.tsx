import { useState } from 'react'
import { Plus } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import CampaignForm from '@/components/campaign/CampaignForm'
import { useFeedback } from '@/components/feedback/FeedbackProvider'
import { useAuth } from '@/hooks/useAuth'
import { useRoomCampaigns } from '@/hooks/useRoomCampaigns'
import type { Room } from '@/hooks/useRooms'
import { useLocale } from '@/i18n/LocaleProvider'
import type { MessageKey } from '@/i18n/messages'
import {
  campaignActions,
  campaignStatusTone,
  type Campaign,
  type CampaignAction,
  type CampaignAttempt,
  type CampaignExtendInput,
  type CampaignStatus,
} from '@/lib/campaigns'

const BADGE_VARIANT = { neutral: 'secondary', warning: 'warning', success: 'success' } as const

export function CampaignStatusBadge({ status }: { status: CampaignStatus }) {
  const { t } = useLocale()
  const tone = campaignStatusTone(status)
  return (
    <Badge variant={BADGE_VARIANT[tone]} data-testid="campaign-status" data-tone={tone} className="shrink-0">
      {t(`campaigns.status.${status}` as MessageKey)}
    </Badge>
  )
}

/** ``<input type="datetime-local">`` value for *iso*, in local time. */
function toLocalInput(iso: string | null): string {
  if (!iso) return ''
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return ''
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`
}

function useCampaignFormat() {
  const { t, formatNumber, formatDate } = useLocale()
  const score = (value: number | null | undefined) =>
    value == null ? '—' : formatNumber(value, { maximumFractionDigits: 4 })
  const timeLeft = (iso: string): string => {
    const ms = new Date(iso).getTime() - Date.now()
    if (ms <= 0) return t('campaigns.time.passed')
    const minutes = Math.max(1, Math.round(ms / 60_000))
    const time = minutes < 60
      ? t('campaigns.time.minutes', { count: minutes })
      : minutes < 48 * 60
        ? t('campaigns.time.hours', { count: Math.round(minutes / 60) })
        : t('campaigns.time.days', { count: Math.round(minutes / 1440) })
    return t('campaigns.time.left', { time })
  }
  const date = (iso: string) => formatDate(new Date(iso), { dateStyle: 'medium', timeStyle: 'short' })
  return { t, score, timeLeft, date }
}

function AttemptRow({ attempt }: { attempt: CampaignAttempt }) {
  const { t, score } = useCampaignFormat()
  const label = attempt.kind === 'retrospective'
    ? t('campaigns.attempts.retrospective')
    : t('campaigns.attempts.cycle', { no: attempt.cycle_no })
  const state = attempt.outcome
    ? t(`campaigns.outcome.${attempt.outcome}` as MessageKey)
    : attempt.eval_status === 'running' || (attempt.closed_at && attempt.eval_status === 'pending')
      ? t('campaigns.attempts.scoring')
      : t('campaigns.attempts.inProgress')
  const value = attempt.trusted_score ?? attempt.dev_score
  return (
    <li className="flex min-w-0 items-baseline justify-between gap-2 py-1 text-caption" data-testid={`campaign-attempt-${attempt.id}`}>
      <span className="min-w-0 truncate text-[var(--color-foreground)]">{label} · {state}</span>
      {attempt.kind !== 'retrospective' && (
        <span className="shrink-0 tabular-nums text-[var(--color-foreground-muted)]">{score(value)}</span>
      )}
    </li>
  )
}

function ExtendForm({ campaign, onExtend, onCancel }: {
  campaign: Campaign
  onExtend: (input: CampaignExtendInput) => Promise<void>
  onCancel: () => void
}) {
  const { t } = useLocale()
  const initialDeadline = toLocalInput(campaign.deadline_at)
  const initialBudget = String(campaign.budget.max_total_tokens)
  const [deadline, setDeadline] = useState(initialDeadline)
  const [budget, setBudget] = useState(initialBudget)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const base = `campaign-extend-${campaign.id}`

  const submit = async () => {
    const input: CampaignExtendInput = {}
    if (deadline && deadline !== initialDeadline) input.deadline_at = new Date(deadline).toISOString()
    if (budget !== initialBudget) {
      const tokens = Number(budget)
      if (!Number.isInteger(tokens) || tokens <= 0) { setError(t('campaigns.form.positiveInteger')); return }
      input.max_total_tokens = tokens
    }
    if (!input.deadline_at && input.max_total_tokens === undefined) { setError(t('campaigns.extend.nothing')); return }
    setBusy(true)
    setError(null)
    try { await onExtend(input) }
    catch (e) { setError(t('campaigns.action.failed', { message: e instanceof Error ? e.message : String(e) })) }
    finally { setBusy(false) }
  }

  return (
    <form
      className="flex flex-col gap-3 rounded-[var(--radius-sm)] border border-[var(--color-border)] bg-[var(--color-surface)] p-3"
      onSubmit={event => { event.preventDefault(); void submit() }}
      noValidate
    >
      <p className="text-caption text-[var(--color-foreground-muted)]">{t('campaigns.extend.hint')}</p>
      <div className="flex flex-col gap-1">
        <label htmlFor={`${base}-deadline`} className="text-caption font-medium">{t('campaigns.extend.deadline')}</label>
        <Input id={`${base}-deadline`} type="datetime-local" value={deadline} onChange={e => setDeadline(e.target.value)} />
      </div>
      <div className="flex flex-col gap-1">
        <label htmlFor={`${base}-budget`} className="text-caption font-medium">{t('campaigns.extend.budget')}</label>
        <Input id={`${base}-budget`} inputMode="numeric" value={budget} onChange={e => setBudget(e.target.value)} />
      </div>
      {error && <p role="alert" className="text-caption text-[var(--color-danger)]">{error}</p>}
      <div className="flex flex-wrap justify-end gap-2">
        <Button type="button" size="sm" variant="outline" onClick={onCancel} disabled={busy}>{t('common.cancel')}</Button>
        <Button type="submit" size="sm" disabled={busy}>{t('campaigns.extend.submit')}</Button>
      </div>
    </form>
  )
}

function CampaignCard({ campaign, attempts, canManage, onAction, onExtend }: {
  campaign: Campaign
  attempts: CampaignAttempt[]
  canManage: boolean
  onAction: (action: CampaignAction) => Promise<void>
  onExtend: (input: CampaignExtendInput) => Promise<void>
}) {
  const { t, score, timeLeft, date } = useCampaignFormat()
  const { confirm } = useFeedback()
  const [busy, setBusy] = useState(false)
  const [extending, setExtending] = useState(false)
  const allowed = campaignActions(campaign.status)
  const best = campaign.final ?? campaign.best
  const bestValue = best ? best.trusted_score ?? best.dev_score : null
  const percent = campaign.usable_tokens > 0
    ? Math.min(100, Math.round((campaign.tokens_used / campaign.usable_tokens) * 100))
    : 0
  const ended = !allowed.stop

  const run = async (action: CampaignAction) => {
    if (action === 'stop') {
      const ok = await confirm({
        title: t('campaigns.stopTitle'),
        description: t('campaigns.stopConfirm', { name: campaign.title }),
        confirmLabel: t('campaigns.action.stop'),
        destructive: true,
      })
      if (!ok) return
    }
    setBusy(true)
    try { await onAction(action) }
    finally { setBusy(false) }
  }

  const criterion = campaign.mode === 'target' && campaign.metric.target != null
    ? t(campaign.metric.direction === 'minimize' ? 'campaigns.score.targetLower' : 'campaigns.score.targetHigher', { target: score(campaign.metric.target) })
    : campaign.deadline_at
      ? t('campaigns.score.optimizeUntil', { date: date(campaign.deadline_at) })
      : null

  return (
    <article
      data-testid={`campaign-card-${campaign.id}`}
      aria-label={campaign.title}
      className="flex min-w-0 flex-col gap-2 rounded-[var(--radius-sm)] px-2 py-2"
    >
      <div className="flex min-w-0 items-start justify-between gap-2">
        <p className="min-w-0 text-caption font-semibold text-[var(--color-foreground)] [overflow-wrap:anywhere]">{campaign.title}</p>
        <CampaignStatusBadge status={campaign.status} />
      </div>
      <p className="text-caption text-[var(--color-foreground)]" data-testid="campaign-score">
        <span className="font-medium">{campaign.metric.name}</span>{' · '}
        {best && bestValue != null
          ? <>{t('campaigns.score.best', { score: score(bestValue) })}{campaign.trusted_eval === 'external' && !best.verified && <> {t('campaigns.score.unverified')}</>}</>
          : t('campaigns.score.none')}
        {criterion && <> · {criterion}</>}
      </p>
      <p className="text-caption text-[var(--color-foreground-muted)]" data-testid="campaign-budget">
        {t('campaigns.budget.used', { percent })}
        {campaign.deadline_at && !ended && <> · {timeLeft(campaign.deadline_at)}</>}
      </p>
      {campaign.status_detail && (
        <p className="text-caption text-[var(--color-foreground-muted)] [overflow-wrap:anywhere]">{campaign.status_detail}</p>
      )}
      {allowed.decide && <p className="text-caption text-[var(--color-foreground)]">{t('campaigns.confirmPrompt')}</p>}

      <details className="min-w-0">
        <summary className="min-h-11 cursor-pointer content-center text-caption font-medium text-[var(--color-foreground-muted)]">
          {t('campaigns.attempts.summary')}
        </summary>
        {attempts.length === 0
          ? <p className="py-1 text-caption text-[var(--color-foreground-subtle)]">{t('campaigns.attempts.empty')}</p>
          : <ul className="divide-y divide-[var(--color-border)]">{attempts.map(a => <AttemptRow key={a.id} attempt={a} />)}</ul>}
      </details>

      {canManage && extending && (
        <ExtendForm
          campaign={campaign}
          onCancel={() => setExtending(false)}
          onExtend={async input => { await onExtend(input); setExtending(false) }}
        />
      )}
      {canManage && !extending && (allowed.pause || allowed.resume || allowed.stop || allowed.decide || allowed.extend) && (
        <div className="flex flex-wrap gap-2">
          {allowed.decide && <Button size="sm" disabled={busy} onClick={() => void run('confirm')}>{t('campaigns.action.confirm')}</Button>}
          {allowed.decide && <Button size="sm" variant="outline" disabled={busy} onClick={() => void run('reject')}>{t('campaigns.action.reject')}</Button>}
          {allowed.pause && <Button size="sm" variant="outline" disabled={busy} onClick={() => void run('pause')}>{t('campaigns.action.pause')}</Button>}
          {allowed.resume && <Button size="sm" variant="outline" disabled={busy} onClick={() => void run('resume')}>{t('campaigns.action.resume')}</Button>}
          {allowed.extend && <Button size="sm" variant="outline" disabled={busy} onClick={() => setExtending(true)}>{t('campaigns.action.extend')}</Button>}
          {allowed.stop && (
            <Button size="sm" variant="ghost" disabled={busy} onClick={() => void run('stop')} className="text-[var(--color-danger)] hover:text-[var(--color-danger)]">
              {t('campaigns.action.stop')}
            </Button>
          )}
        </div>
      )}
    </article>
  )
}

interface CampaignSectionProps {
  roomId: string
  room: Room | null
}

/**
 * Right-rail campaigns (#930 §3.9). Renders only when the room has
 * campaigns or can host one (a project room with a lead agent). Changes
 * are offered to the campaign's owner and admins; others see state only.
 */
export default function CampaignSection({ roomId, room }: CampaignSectionProps) {
  const { t } = useLocale()
  const { notify } = useFeedback()
  const { user } = useAuth()
  const { campaigns, attempts, loading, error, create, act, extend } = useRoomCampaigns(roomId)
  const [formOpen, setFormOpen] = useState(false)
  const canCreate = Boolean(room && room.project_id && !room.is_dm && room.representative_agent_id && user)

  if (!canCreate && campaigns.length === 0 && !error) return null

  const report = (e: unknown) =>
    notify({ message: t('campaigns.action.failed', { message: e instanceof Error ? e.message : String(e) }), tone: 'error' })

  return (
    <section className="flex min-w-0 flex-col border-t border-[var(--color-border)]" data-testid="campaign-section" aria-labelledby="campaign-section-title">
      <header className="flex items-center justify-between gap-2 px-3 py-2">
        <h3 id="campaign-section-title" className="text-caption font-semibold text-[var(--color-foreground)]">{t('campaigns.title')}</h3>
        {canCreate && (
          <Button variant="ghost" size="icon" type="button" aria-label={t('campaigns.new')} title={t('campaigns.new')} onClick={() => setFormOpen(true)} className="text-[var(--color-foreground-muted)]">
            <Plus />
          </Button>
        )}
      </header>
      <div className="flex min-w-0 flex-col gap-1 px-1 pb-2">
        {error && <p role="alert" className="px-2 text-caption text-[var(--color-danger)]">{t('campaigns.loadFailed')} {error}</p>}
        {loading && campaigns.length === 0 && <p className="px-2 text-caption text-[var(--color-foreground-muted)]">{t('common.loading')}</p>}
        {!loading && !error && campaigns.length === 0 && (
          <p className="px-2 py-2 text-caption text-[var(--color-foreground-subtle)]">{t('campaigns.empty')}</p>
        )}
        {campaigns.map(campaign => (
          <CampaignCard
            key={campaign.id}
            campaign={campaign}
            attempts={attempts[campaign.id] ?? []}
            canManage={Boolean(user && (user.is_admin || user.id === campaign.owner_id))}
            onAction={action => act(campaign.id, action).catch(report)}
            onExtend={input => extend(campaign.id, input)}
          />
        ))}
      </div>
      {canCreate && (
        <CampaignForm
          open={formOpen}
          onOpenChange={setFormOpen}
          onSubmit={create}
          onCreated={() => notify({ message: t('campaigns.form.created') })}
        />
      )}
    </section>
  )
}
