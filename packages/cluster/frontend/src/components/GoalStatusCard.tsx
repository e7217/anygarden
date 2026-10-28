/**
 * GoalStatusCard — in-stream notice that a goal needs a person or has
 * ended (#957). Quiet like TaskAssignmentCard; the status keeps its icon
 * and label so color is never the only signal.
 *
 * #964 — the same message may be the lead's report request: the card says
 * who was asked to report (their reply follows in the chat) and shows the
 * cycle's files. The message body is the lead's brief and is not shown.
 */
import { Activity, AlertTriangle, CheckCircle2, CircleSlash, Target } from 'lucide-react'
import type { GoalNoticeStatus, GoalStatusMeta } from '@/lib/goalStatus'
import MessageAttachments from '@/components/MessageAttachments'
import { useLocale } from '@/i18n/LocaleProvider'

const TONE: Record<GoalNoticeStatus, { icon: typeof Target; color: string }> = {
  active: { icon: Activity, color: 'text-[var(--color-brand-text)]' },
  blocked: { icon: AlertTriangle, color: 'text-[var(--color-warning)]' },
  plateau: { icon: AlertTriangle, color: 'text-[var(--color-warning)]' },
  awaiting_confirmation: { icon: Target, color: 'text-[var(--color-brand-text)]' },
  achieved: { icon: CheckCircle2, color: 'text-[var(--color-success)]' },
  deadline_reached: { icon: CircleSlash, color: 'text-[var(--color-foreground-muted)]' },
  budget_exhausted: { icon: CircleSlash, color: 'text-[var(--color-danger)]' },
  stopped: { icon: CircleSlash, color: 'text-[var(--color-foreground-muted)]' },
}

export default function GoalStatusCard({
  meta,
  metadata,
  leadName,
}: {
  meta: GoalStatusMeta
  /** The message's metadata; its ``artifact`` references are shown as files. */
  metadata?: Record<string, unknown>
  /** Display name of the lead asked to report, when known. */
  leadName?: string
}) {
  const { t } = useLocale()
  const { icon: Icon, color } = TONE[meta.status]
  const report = meta.report
  return (
    <div
      data-testid="goal-status-card"
      className="flex max-w-full flex-col gap-1 rounded-[var(--radius-md)] border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-1.5 text-sm shadow-whisper"
    >
      <div className="flex min-w-0 items-center gap-2">
        <Icon className={`h-3.5 w-3.5 shrink-0 ${color}`} aria-hidden />
        <span className="text-badge font-medium uppercase tracking-[0.06em] text-[var(--color-foreground-muted)]">
          {t('chat.goalStatus')}
        </span>
        <span className="truncate text-[var(--color-foreground)]">{meta.title}</span>
        <span className={`shrink-0 font-medium ${color}`}>
          {t(`campaigns.status.${meta.status}`)}
        </span>
      </div>
      {meta.detail && (
        <p className="text-caption text-[var(--color-foreground-muted)] break-words">{meta.detail}</p>
      )}
      {report && (
        <p data-testid="goal-report-request" className="text-caption text-[var(--color-foreground-muted)] break-words">
          {report.leadParticipantId && leadName
            ? t('chat.goalReportRequested', { name: leadName })
            : report.leadParticipantId
              ? t('chat.goalReportRequestedLead')
              : t('chat.goalReportNoLead')}
        </p>
      )}
      <MessageAttachments metadata={metadata} />
    </div>
  )
}
