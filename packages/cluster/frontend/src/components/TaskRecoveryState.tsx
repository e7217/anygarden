import type { TaskRecovery } from '@/hooks/useRoomTasks'
import { useExecutionTaskRetry } from '@/hooks/useExecutionTaskRetry'
import { Button } from '@/components/ui/button'
import { useLocale } from '@/i18n/LocaleProvider'
import type { MessageKey } from '@/i18n/messages'

const REASONS: Record<string, MessageKey> = Object.fromEntries([
  'MODEL_CALL_FAILED', 'MODEL_TIMEOUT', 'MODEL_CONFIGURATION_INVALID', 'AUTHENTICATION_FAILED', 'MODEL_TRANSIENT_FAILURE', 'MODEL_EXECUTION_FAILED', 'RATE_LIMITED', 'QUOTA_EXHAUSTED', 'PROCESS_LOST', 'PROCESS_OUTCOME_UNKNOWN',
  'LEASE_EXPIRED', 'AGENT_STOPPED', 'TRANSPORT_UNAVAILABLE', 'NATIVE_STOP_UNCONFIRMED', 'TASK_BLOCKED',
  'AUTHORIZATION_REVOKED', 'RETRY_EXHAUSTED', 'INPUT_SUPERSEDED', 'EXECUTION_CANCELLED', 'APPROVAL_REJECTED', 'QA_VERIFICATION_FAILED',
  'EXECUTION_DEADLINE_REACHED', 'EXECUTION_NATIVE_INVOCATION_LIMIT', 'EXECUTION_USAGE_LIMIT_REACHED', 'EXECUTION_USAGE_ACCOUNTING_INCOMPLETE', 'FAILED_REPLY', 'LEGACY_RETRY_UNSUPPORTED', 'RECOVERY_PENDING', 'UNKNOWN_FAILURE',
].map(code => [code, `taskRecovery.reason.${code}`])) as Record<string, MessageKey>
const OUTCOMES: Record<string, MessageKey> = Object.fromEntries([
  'pending', 'leased', 'started', 'completing', 'completed', 'cancelled', 'failed', 'interrupted',
  'unknown', 'stale', 'ok', 'succeeded', 'timeout', 'skipped', 'rejected', 'retry_exhausted',
].map(outcome => [outcome, `taskRecovery.outcome.${outcome}`])) as Record<string, MessageKey>

export function hasTaskRecovery(recovery?: TaskRecovery | null): boolean {
  if (!recovery) return false
  const previousFailure = (recovery.attempts ?? []).some(attempt => attempt.reason_code || ['failed', 'interrupted', 'unknown', 'timeout', 'rejected', 'retry_exhausted'].includes(attempt.outcome ?? attempt.state))
  if (recovery.state === 'none') return previousFailure || (recovery.retry_count ?? 0) > 0
  const failureReason = recovery.reason_code && !['INPUT_SUPERSEDED', 'EXECUTION_CANCELLED'].includes(recovery.reason_code)
  return !['running', 'completed', 'historical', 'cancelled'].includes(recovery.state) || previousFailure || !!failureReason || (recovery.retry_count ?? 0) > 0
}

export default function TaskRecoveryState({ recovery, taskId, inputRevision, className = '' }: { recovery?: TaskRecovery | null; taskId: string; inputRevision?: number | null; className?: string }) {
  const { t, formatDate } = useLocale()
  const retry = useExecutionTaskRetry(taskId, inputRevision, recovery)
  const attempts = recovery?.attempts ?? []
  if (!recovery || !hasTaskRecovery(recovery)) return null
  const reason = (code?: string | null) => code ? t(REASONS[code] ?? 'taskRecovery.reason.UNKNOWN_FAILURE') : null
  const nextRetry = recovery.next_retry_at && !Number.isNaN(Date.parse(recovery.next_retry_at)) ? recovery.next_retry_at : null
  const needsAttention = ['failed', 'action_required'].includes(recovery.state)
  const stateLabel: MessageKey = recovery.state === 'completed' && recovery.reason_code === 'QA_VERIFICATION_FAILED'
    ? 'taskRecovery.reviewCompleted'
    : recovery.state === 'completed' && !(recovery.retry_count ?? 0)
      ? 'taskRecovery.processingCompleted'
      : `taskRecovery.state.${recovery.state}`
  return <div data-testid={`task-recovery-${taskId}`} data-recovery-state={recovery.state} className={`min-w-0 space-y-2 border-l-2 border-[var(--color-border)] pl-2 text-sm [overflow-wrap:anywhere] ${className}`}>
    <p className={`font-medium ${needsAttention ? 'text-[var(--color-warning)]' : 'text-[var(--color-foreground)]'}`}>{t(stateLabel)}</p>
    {recovery.reason_code && <p>{reason(recovery.reason_code)}</p>}
    {recovery.next_action && recovery.next_action !== 'none' && <p className="text-[var(--color-foreground-muted)]">{t(`taskRecovery.action.${recovery.next_action}`)}</p>}
    {recovery.state === 'retry_wait' && nextRetry && <p>{t('taskRecovery.nextRetry')}: <time dateTime={nextRetry}>{formatDate(new Date(nextRetry), { dateStyle: 'medium', timeStyle: 'short' })}</time></p>}
    {retry.canRetry && <Button type="button" variant="outline" className="min-h-11 h-auto max-w-full whitespace-normal text-left" data-testid={`task-retry-${taskId}`} disabled={retry.sending || retry.accepted} onClick={() => { void retry.submit() }}>{retry.sending ? t('common.loading') : retry.accepted ? t('taskRecovery.retryAccepted') : t('taskRecovery.retrySameTask')}</Button>}
    {retry.error && <p role="alert" className="whitespace-pre-wrap text-[var(--color-danger)]">{retry.error}</p>}
    {attempts.length > 0 && <details data-testid={`task-recovery-history-${taskId}`}>
      <summary className="min-h-11 cursor-pointer content-center text-xs font-medium md:min-h-8">{t('taskRecovery.history')}{recovery.attempt_count != null && recovery.attempt_count > 0 && <> · {t('taskRecovery.attemptCount', { count: recovery.attempt_count })}</>}</summary>
      <div className="space-y-2 pt-1">
        <p className="text-xs text-[var(--color-foreground-muted)]">{t('taskRecovery.historyMeaning')}</p>
        {recovery.max_retries != null && <p className="text-xs text-[var(--color-foreground-muted)]">{t('taskRecovery.retryLimit', { count: recovery.max_retries })}</p>}
        {attempts.map(attempt => <div key={attempt.ordinal} className="space-y-1 border-l-2 border-[var(--color-border)] pl-2 text-xs">
          <p className="font-medium">{t('taskRecovery.attempt', { count: attempt.ordinal })} · {t(OUTCOMES[attempt.outcome ?? attempt.state] ?? 'taskRecovery.outcome.unknown')}</p>
          {attempt.reason_code && <p>{reason(attempt.reason_code)}</p>}
          {attempt.started_at && !Number.isNaN(Date.parse(attempt.started_at)) && <p>{t('taskRecovery.started')}: <time dateTime={attempt.started_at}>{formatDate(new Date(attempt.started_at), { dateStyle: 'medium', timeStyle: 'short' })}</time></p>}
          {attempt.finished_at && !Number.isNaN(Date.parse(attempt.finished_at)) && <p>{t('taskRecovery.finished')}: <time dateTime={attempt.finished_at}>{formatDate(new Date(attempt.finished_at), { dateStyle: 'medium', timeStyle: 'short' })}</time></p>}
        </div>)}
      </div>
    </details>}
  </div>
}
