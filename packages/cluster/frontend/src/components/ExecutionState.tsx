import type { ExecutionDisposition, ExecutionLimits, ExecutionOperationAction, ExecutionUsageSummary, ExecutionUsageTotals } from '@/hooks/useProjectExecutions'
import type { ExecutionApproval } from '@/hooks/useExecutionApprovals'
import { useLocale } from '@/i18n/LocaleProvider'
import type { MessageKey } from '@/i18n/messages'

export function executionLimitReasonKey(reason?: string | null): MessageKey {
  return reason === 'EXECUTION_NATIVE_INVOCATION_LIMIT' ? 'executions.limit.nativeReason'
    : reason === 'EXECUTION_USAGE_LIMIT_REACHED' ? 'executions.limit.tokenReason'
    : reason === 'EXECUTION_USAGE_ACCOUNTING_INCOMPLETE' ? 'executions.usage.incomplete'
    : 'executions.limit.reason'
}

export function DispositionNotice({ disposition, inputRevision, operationAction }: { disposition?: ExecutionDisposition; inputRevision?: number | null; operationAction?: ExecutionOperationAction | null }) {
  const { t } = useLocale()
  if (operationAction === 'deadline' && disposition !== 'superseded') return <p data-testid="execution-deadline-notice" className="border-l-2 border-[var(--color-warning)] pl-2 text-sm text-[var(--color-foreground-muted)]">{t('executions.deadline.reason')}</p>
  if (operationAction === 'limit' && disposition !== 'superseded') return <p data-testid="execution-limit-notice" className="border-l-2 border-[var(--color-warning)] pl-2 text-sm text-[var(--color-foreground-muted)]">{t('executions.limit.reason')}</p>
  if (!disposition || disposition === 'current') return null
  return <div data-testid="execution-disposition" className="space-y-1 border-l-2 border-[var(--color-warning)] pl-2 text-sm text-[var(--color-foreground-muted)]">
    {disposition === 'superseded' && inputRevision != null && <p className="font-medium">{t('executions.previousInput', { version: inputRevision })}</p>}
    <p>{t(`executions.${disposition}`)}</p>
  </div>
}

function UsageTotals({ usage }: { usage: ExecutionUsageTotals }) {
  const { t, formatNumber } = useLocale()
  const completeTokens = usage.total_tokens != null && usage.input_tokens != null && usage.output_tokens != null
  const tokens = completeTokens
    ? { input: usage.input_tokens!, output: usage.output_tokens!, total: usage.total_tokens! }
    : usage.known_input_tokens != null && usage.known_output_tokens != null && usage.known_total_tokens != null ? { input: usage.known_input_tokens, output: usage.known_output_tokens, total: usage.known_total_tokens } : null
  const cached = completeTokens ? usage.cached_input_tokens : usage.known_cached_input_tokens
  const cost = usage.cost_usd ?? usage.known_cost_usd
  return <div className="space-y-1 text-[var(--color-foreground-muted)]">
    <p>{tokens ? t(completeTokens ? 'executions.usage.tokens' : 'executions.usage.knownTokens', { input: formatNumber(tokens.input), output: formatNumber(tokens.output), total: formatNumber(tokens.total) }) : t('executions.usage.tokensUnknown')}</p>
    {cached != null && <p>{t('executions.usage.cachedInput', { count: formatNumber(cached) })}</p>}
    <p>{cost != null ? t(usage.cost_usd != null ? 'executions.usage.cost' : 'executions.usage.knownCost', { cost }) : t('executions.usage.costUnknown')}</p>
    {usage.pending_invocations > 0 && <p>{t('executions.usage.pending', { count: formatNumber(usage.pending_invocations) })}</p>}
    {usage.unknown_invocations > 0 && <p>{t('executions.usage.unknown', { count: formatNumber(usage.unknown_invocations) })}</p>}
    {usage.not_started_invocations > 0 && <p>{t('executions.usage.notStarted', { count: formatNumber(usage.not_started_invocations) })}</p>}
  </div>
}

export function ExecutionUsageState({ summary, limits, executionId }: { summary?: ExecutionUsageSummary | null; limits?: ExecutionLimits | null; executionId: string }) {
  const { t, formatNumber, formatDate } = useLocale()
  if (!summary) return null
  const asOf = summary.as_of && !Number.isNaN(Date.parse(summary.as_of)) ? summary.as_of : null
  return <div data-testid={`execution-usage-${executionId}`} className="min-w-0 space-y-1 border-l-2 border-[var(--color-border)] pl-2 text-sm [overflow-wrap:anywhere]">
    <p className="font-medium">{t('executions.usage.title')}</p>
    <p>{t(limits?.max_native_invocations != null ? 'executions.usage.runsWithLimit' : 'executions.usage.runs', { count: formatNumber(summary.native_invocations_reserved), limit: limits?.max_native_invocations != null ? formatNumber(limits.max_native_invocations) : '' })}</p>
    {limits?.max_total_tokens != null && <p>{t('executions.usage.tokenThreshold', { count: formatNumber(limits.max_total_tokens) })}</p>}
    <UsageTotals usage={summary} />
    {summary.coverage?.status !== 'complete' && <p>{t('executions.usage.incomplete')}</p>}
    <p>{t('executions.usage.scope')}</p>
    {limits?.max_total_tokens != null && <p>{t('executions.usage.observedThreshold')}</p>}
    {summary.enforcement?.initial_limit_declared_after_native_start && <p>{t('executions.usage.declaredAfterStart')}</p>}
    {asOf && <p>{t('executions.usage.asOf')}: <time dateTime={asOf}>{formatDate(new Date(asOf), { dateStyle: 'medium', timeStyle: 'short' })}</time></p>}
  </div>
}

export function ExecutionUsageHistory({ summary, executionId }: { summary?: ExecutionUsageSummary | null; executionId: string }) {
  const { t, formatNumber } = useLocale()
  if (!summary || summary.revisions.length < 2) return null
  return <div className="space-y-2"><p className="font-semibold">{t('executions.usage.revisionHistory')}</p>{summary.revisions.map(revision => <details key={revision.input_revision} data-testid={`execution-usage-revision-${executionId}-${revision.input_revision}`}>
    <summary className="min-h-11 cursor-pointer content-center">{t('executions.inputVersion', { version: revision.input_revision })}</summary>
    <div className="space-y-1 pb-2"><p>{t('executions.usage.runs', { count: formatNumber(revision.invocation_count) })}</p><UsageTotals usage={revision} /></div>
  </details>)}</div>
}

export function TransmissionNotice({ approval }: { approval: ExecutionApproval }) {
  const { t } = useLocale()
  const status = approval.status
  const message = status === 'approved'
    ? approval.revoked || approval.permit_revoked || (approval.disposition && approval.disposition !== 'current') ? 'revoked' : 'approved'
    : status === 'executing' ? 'executing' : status === 'succeeded' ? 'succeeded' : status === 'failed' ? 'failed' : status === 'unknown' ? 'unknown' : null
  if (!message) return null
  return <p data-testid={`execution-effect-${approval.id}`} className="whitespace-pre-wrap text-sm text-[var(--color-foreground-muted)]">{t(`executions.effect.${message}`)}</p>
}
