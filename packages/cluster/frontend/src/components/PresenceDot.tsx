/**
 * PresenceDot — tiny liveness indicator (#54).
 *
 * A 6px circle next to a participant's name:
 *   - ``online=true``  → Notion blue accent (the system's "alive"
 *                        semantic; green reads too aggressively in
 *                        the warm-neutral palette we use elsewhere
 *                        — see DESIGN.md §2).
 *   - ``online=false`` → warm neutral gray, same family as the
 *                        ``--color-border`` whisper.
 *
 * The ``title`` attribute carries a human-readable last-seen label so
 * hovering the dot shows "방금 전" / "12분 전" / the raw ISO stamp as
 * a fallback. Using the native ``title`` keeps us clear of a new
 * radix-tooltip dependency for a single place.
 */
export interface PresenceDotProps {
  online: boolean
  lastSeenAt?: string | null
  size?: number
  className?: string
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

export default function PresenceDot({
  online,
  lastSeenAt,
  size = 6,
  className = '',
}: PresenceDotProps) {
  const title = online
    ? '온라인'
    : `오프라인 · 마지막 응답 ${formatAgo(lastSeenAt)}`

  // Notion-blue accent for online, warm neutral for offline.
  // ``--color-brand`` is the project's single accent token
  // (DESIGN.md §2); falling back via the CSS var keeps PresenceDot
  // theme-agnostic.
  const bg = online
    ? 'var(--color-brand, #0075de)'
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
