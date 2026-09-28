import { useMemo, type ReactNode } from 'react'
import { Button } from '@/components/ui/button'
import { useAgentActivity } from '@/hooks/useAgentActivity'
import { useLocale } from '@/i18n/LocaleProvider'
import type { MessageKey } from '@/i18n/messages'
import { splitLogs } from './ActivityPanel'
import { latestTurnHealth, type TurnFailureCategory, type TurnHealthStatus } from './turnHealth'

interface Props {
  agentId: string | null
  active: boolean
  /** Opens the Activity section with this turn expanded. */
  onShowTurn?: (requestId: string) => void
}

const DOT: Record<TurnHealthStatus, string> = {
  none: 'bg-[var(--color-foreground-subtle)]',
  succeeded: 'bg-[var(--color-success)]',
  failed: 'bg-[var(--color-destructive)]',
  in_flight: 'bg-[var(--color-foreground-muted)]',
  cancelled: 'bg-[var(--color-foreground-muted)]',
}

const MODEL_CONNECTION_REASONS: Record<string, MessageKey> = {
  AUTH_MISSING: 'admin.activity.authMissing',
  UNKNOWN_PROVIDER: 'admin.activity.unknownProvider',
  AUTH_CHECK_FAILED: 'admin.activity.authCheckFailed',
  ENGINE_AUTH_ERROR: 'admin.activity.engineAuthError',
  PI_PROVIDER_ERROR: 'admin.activity.piProviderError',
}

function reasonKey(category: TurnFailureCategory, code: string | null): MessageKey {
  if (category === 'model_connection' && code && MODEL_CONNECTION_REASONS[code]) return MODEL_CONNECTION_REASONS[code]
  if (category === 'engine' && code === 'missing_terminal_event') return 'admin.overview.recentTurn.reason.missingTerminal'
  if (category === 'engine' && code === 'UNSUPPORTED_RUNTIME') return 'admin.overview.recentTurn.reason.unsupportedRuntime'
  const byCategory: Record<TurnFailureCategory, MessageKey> = {
    model_connection: 'admin.overview.recentTurn.reason.modelConnection',
    policy: 'admin.overview.recentTurn.reason.policy',
    timeout: 'admin.overview.recentTurn.reason.timeout',
    busy: 'admin.overview.recentTurn.reason.busy',
    engine: 'admin.overview.recentTurn.reason.engine',
  }
  return byCategory[category]
}

const STATUS_LABEL: Record<TurnHealthStatus, MessageKey> = {
  none: 'admin.overview.recentTurn.status.none',
  succeeded: 'admin.overview.recentTurn.status.succeeded',
  failed: 'admin.overview.recentTurn.status.failed',
  in_flight: 'admin.overview.recentTurn.status.inFlight',
  cancelled: 'admin.overview.recentTurn.status.cancelled',
}

const CATEGORY_LABEL: Record<TurnFailureCategory, MessageKey> = {
  model_connection: 'admin.overview.recentTurn.category.modelConnection',
  policy: 'admin.overview.recentTurn.category.policy',
  timeout: 'admin.overview.recentTurn.category.timeout',
  busy: 'admin.overview.recentTurn.category.busy',
  engine: 'admin.overview.recentTurn.category.engine',
}

/**
 * #716 — the latest turn's result beside the process state. A connected
 * process (Online) can still fail every turn; this row keeps the two apart.
 * Rendered as a ``dt``/``dd`` pair inside the Overview definition list.
 */
export default function RecentTurnSummary({ agentId, active, onShowTurn }: Props) {
  const { t, formatDate } = useLocale()
  const activity = useAgentActivity(agentId, active)
  const health = useMemo(() => latestTurnHealth(splitLogs(activity.logs).turns), [activity.logs])

  let body: ReactNode
  if (activity.loading) {
    body = <span className="text-[var(--color-foreground-muted)]">{t('admin.overview.recentTurn.loading')}</span>
  } else if (activity.error && activity.logs.length === 0) {
    body = <span className="text-[var(--color-foreground-muted)]">{t('admin.overview.recentTurn.unavailable')}</span>
  } else {
    const { status, turn, at, category, code } = health
    body = (
      <div className="min-w-0 space-y-1">
        <div className="flex flex-wrap items-center gap-2">
          <span aria-hidden="true" className={`inline-block h-2 w-2 shrink-0 rounded-full ${DOT[status]}`} />
          <span className="text-[var(--color-foreground)]">{t(STATUS_LABEL[status])}</span>
          {at && (
            <span className="text-[var(--color-foreground-muted)]">
              · {formatDate(at, { dateStyle: 'medium', timeStyle: 'short' })}
            </span>
          )}
        </div>
        {status === 'failed' && category && (
          <p className="text-xs leading-relaxed text-[var(--color-foreground-muted)]" data-testid="overview-recent-turn-reason">
            <span className="font-medium text-[var(--color-destructive)]">{t(CATEGORY_LABEL[category])}</span>
            {' — '}
            {t(reasonKey(category, code))}
            {code && <code className="ml-1 font-mono text-[11px] text-[var(--color-foreground-subtle)]">{code}</code>}
          </p>
        )}
        {status === 'failed' && turn && onShowTurn && (
          <Button
            variant="link"
            size="sm"
            className="h-auto p-0 text-xs"
            onClick={() => onShowTurn(turn.requestId)}
            data-testid="overview-recent-turn-details"
          >
            {t('admin.overview.recentTurn.viewActivity')}
          </Button>
        )}
      </div>
    )
  }

  return (
    <>
      <dt className="text-[var(--color-foreground-muted)]">{t('admin.overview.recentTurn')}</dt>
      <dd data-testid="overview-recent-turn" data-status={health.status}>{body}</dd>
    </>
  )
}
