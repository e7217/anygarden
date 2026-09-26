import { useLocale } from '@/i18n/LocaleProvider'

/**
 * PresenceDot — tiny liveness indicator (#54, #71).
 *
 * A 6px circle next to a participant's / agent's name:
 *   - ``online=true`` → semantic online green (``--color-status-online``).
 *   - ``online=false`` → muted foreground (``--color-foreground-subtle``).
 *
 * ``variant`` switches the offline tooltip voice:
 *   - ``'user'``  (default) — uses the participant presence
 *     semantic: "오프라인 · 마지막 응답 ${formatAgo(lastSeenAt)}".
 *   - ``'agent'`` — renders a localized known agent lifecycle state
 *     (``stopped`` / ``crashed`` / ``unreachable`` / etc). Callers
 *     pass the raw ``actual_state`` via ``agentState``; the helper
 *     in ``lib/agent-liveness.ts`` prepares it (including the
 *     ``machine_offline → 'unreachable'`` mapping).
 *
 * The ``title`` attribute carries the human-readable label so
 * hovering the dot shows the appropriate detail. Using the native
 * ``title`` keeps us clear of a new radix-tooltip dependency for
 * a single place.
 */
export interface PresenceDotProps {
  online: boolean
  lastSeenAt?: string | null
  size?: number
  className?: string
  /** Offline-tooltip voice. Defaults to ``'user'`` (last-seen
   *  timestamp). Pass ``'agent'`` at agent-row call sites so the
   *  tooltip surfaces the lifecycle state instead. */
  variant?: 'user' | 'agent'
  /** Raw agent state ("running"/"stopped"/"crashed"/... or
   *  "unreachable" when the hosting machine is offline). Only
   *  read when ``variant === 'agent'``. */
  agentState?: string
}

const agentStateKeys = {
  unreachable: 'admin.agentSettings.state.unreachable',
  unknown: 'admin.agentSettings.state.unknown',
  running: 'admin.agentSettings.state.running',
  starting: 'admin.agentSettings.state.starting',
  stopping: 'admin.agentSettings.state.stopping',
  stopped: 'admin.agentSettings.state.stopped',
  idle: 'admin.agentSettings.state.idle',
  pending: 'admin.agentSettings.state.pending',
  crashed: 'admin.agentSettings.state.crashed',
  failed: 'admin.agentSettings.state.failed',
} as const

function formatAgo(iso: string | null | undefined, locale: 'ko' | 'en', unknown: string, justNow: string): string {
  if (!iso) return unknown
  const ts = new Date(iso)
  if (Number.isNaN(ts.getTime())) return unknown
  const seconds = Math.floor((Date.now() - ts.getTime()) / 1000)
  if (seconds < 60) return justNow
  const formatter = new Intl.RelativeTimeFormat(locale, { numeric: 'auto' })
  if (seconds < 3600) return formatter.format(-Math.floor(seconds / 60), 'minute')
  if (seconds < 86400) return formatter.format(-Math.floor(seconds / 3600), 'hour')
  return formatter.format(-Math.floor(seconds / 86400), 'day')
}

export default function PresenceDot({
  online,
  lastSeenAt,
  size = 6,
  className = '',
  variant = 'user',
  agentState,
}: PresenceDotProps) {
  const { locale, t } = useLocale()
  const offline = t('common.offline')
  const stateKey = agentState && agentStateKeys[agentState as keyof typeof agentStateKeys]
  const stateLabel = stateKey ? t(stateKey) : agentState
  const title = online
    ? t('common.online')
    : variant === 'agent'
      ? stateLabel ? `${offline} · ${stateLabel}` : offline
      : `${offline} · ${t('common.lastSeen', {
        time: formatAgo(lastSeenAt, locale, t('common.unknown'), t('common.justNow')),
      })}`

  // Semantic colors follow the active light or dark theme.
  const bg = online
    ? 'var(--color-status-online, #5b9e6d)'
    : 'var(--color-foreground-subtle)'

  return (
    <span
      role="status"
      aria-label={title}
      title={title}
      className={`inline-block rounded-full ${className}`}
      style={{
        width: size,
        height: size,
        background: bg,
        // Whisper border (DESIGN.md §4) so the offline dot still
        // reads against a white participant row.
        boxShadow: '0 0 0 1px rgba(0,0,0,0.04)',
        flexShrink: 0,
      }}
    />
  )
}
