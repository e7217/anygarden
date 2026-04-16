/**
 * PresenceDot — tiny liveness indicator (#54, #71).
 *
 * A 6px circle next to a participant's / agent's name:
 *   - ``online=true``  → muted sage green
 *     (``--color-status-online``). As of #71 we moved off the
 *     Notion Blue accent because blue was overloading the single
 *     accent semantic (interactive intent); sage keeps ``alive''
 *     semantically distinct without fighting the warm-neutral
 *     palette. Green too-saturated reads aggressive in this
 *     palette — the 0.56 luminance sage is deliberate.
 *   - ``online=false`` → warm neutral gray, same family as the
 *     ``--color-border`` whisper.
 *
 * ``variant`` switches the offline tooltip voice:
 *   - ``'user'``  (default) — uses the participant presence
 *     semantic: "오프라인 · 마지막 응답 ${formatAgo(lastSeenAt)}".
 *   - ``'agent'`` — renders the agent lifecycle state verbatim
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

function formatAgo(iso: string | null | undefined): string {
  if (!iso) return '알 수 없음'
  const ts = new Date(iso)
  if (Number.isNaN(ts.getTime())) return '알 수 없음'
  const seconds = Math.floor((Date.now() - ts.getTime()) / 1000)
  if (seconds < 60) return '방금 전'
  if (seconds < 3600) return `${Math.floor(seconds / 60)}분 전`
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}시간 전`
  return `${Math.floor(seconds / 86400)}일 전`
}

function buildTitle(
  online: boolean,
  variant: 'user' | 'agent',
  lastSeenAt: string | null | undefined,
  agentState: string | undefined,
): string {
  if (online) return '온라인'
  if (variant === 'agent') {
    // Agent variant — surface the lifecycle verbatim. Falls back
    // to a neutral offline label when the caller didn't pass
    // ``agentState`` (e.g. we don't know which agent powers this
    // DM).
    return agentState ? `오프라인 · ${agentState}` : '오프라인'
  }
  // User variant — "마지막 응답" phrasing for the WS presence path.
  return `오프라인 · 마지막 응답 ${formatAgo(lastSeenAt)}`
}

export default function PresenceDot({
  online,
  lastSeenAt,
  size = 6,
  className = '',
  variant = 'user',
  agentState,
}: PresenceDotProps) {
  const title = buildTitle(online, variant, lastSeenAt, agentState)

  // Sage green for online (``--color-status-online``, #71), warm
  // neutral gray for offline. Falling back via CSS var keeps
  // PresenceDot theme-agnostic.
  const bg = online
    ? 'var(--color-status-online, #5b9e6d)'
    : 'rgba(0, 0, 0, 0.25)'

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
