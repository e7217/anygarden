// frontend/src/components/RoomQueryBanner.tsx
//
// Top-of-room pending-query chip strip. Issue #55.
//
// One chip per ``query_id`` in four states:
//
//   * ``pending``   — spinner + ``N/M`` live count (no dismiss
//                     button; auto-resolves on result delivery).
//   * ``completed`` — checkmark; whole chip is a scroll-to-card
//                     button. Auto-dismissed by ChatArea when the
//                     result card enters the viewport, so the
//                     chip doesn't pile up in long sessions.
//   * ``timeout``   — warning triangle + explicit ``×`` close
//                     button. Persists until the user acknowledges
//                     it because a partial answer is something
//                     they should consciously see.
//   * ``solo``      — ``응답 가능 에이전트 없음`` label with a
//                     dismiss button; not auto-removed because
//                     the user should know the target room was
//                     empty.
//
// The component is pure presentational — all state lives in
// ChatArea. Accessibility: wrapped in ``role="status"`` +
// ``aria-live="polite"`` so screen readers announce count changes
// without interrupting the user's typing.

import { Loader2, Check, AlertTriangle, X, UserX } from 'lucide-react'
import type { RoomQueryStatus } from '@/lib/room-query'

/** What ChatArea keeps per in-flight query. */
export interface PendingQuery {
  query_id: string
  target_room_id: string
  /** Display name for the target room; falls back to ``#id-slice``. */
  target_room_name: string
  status: RoomQueryStatus
  /** Count from the latest result, or ``0`` while pending. */
  responded: number
  /** Expected count snapshot. ``0`` for solo, unknown-pending shows no denominator. */
  expected: number
  /** Message id of the corresponding result bubble — used to scroll to it. */
  result_message_id?: string
}

interface RoomQueryBannerProps {
  queries: PendingQuery[]
  /** User clicked the ``×`` on a timeout/solo chip. */
  onDismiss: (queryId: string) => void
  /** User clicked a completed chip — ChatArea scrolls to the result bubble
   * (and will auto-dismiss once it's on screen). */
  onScrollTo: (queryId: string) => void
}

export default function RoomQueryBanner({
  queries,
  onDismiss,
  onScrollTo,
}: RoomQueryBannerProps) {
  if (queries.length === 0) return null

  return (
    <div
      role="status"
      aria-live="polite"
      className="flex flex-wrap gap-2 border-b border-[var(--color-border)] bg-[var(--color-surface-alt)] px-4 py-2"
      data-testid="room-query-banner"
    >
      {queries.map((q) => (
        <QueryChip
          key={q.query_id}
          query={q}
          onDismiss={onDismiss}
          onScrollTo={onScrollTo}
        />
      ))}
    </div>
  )
}

interface QueryChipProps {
  query: PendingQuery
  onDismiss: (queryId: string) => void
  onScrollTo: (queryId: string) => void
}

function QueryChip({ query, onDismiss, onScrollTo }: QueryChipProps) {
  const base =
    'inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-medium shadow-[0_1px_2px_rgba(0,0,0,0.03)]'

  if (query.status === 'pending') {
    // Pending chip: no interaction — the user is waiting for the
    // representative to synthesize. We only show a denominator
    // once ``expected`` is known (it's populated from the
    // question metadata snapshot on the client side).
    const count =
      query.expected > 0
        ? `${query.responded}/${query.expected}`
        : '응답 대기 중'
    return (
      <span
        className={`${base} border-[var(--color-border)] bg-white text-[var(--color-foreground-muted)]`}
        data-testid={`room-query-chip-${query.query_id}`}
        data-status="pending"
      >
        <Loader2 className="h-3 w-3 animate-spin" aria-hidden="true" />
        <span className="text-[var(--color-foreground)]">
          #{query.target_room_name}
        </span>
        <span>{count}</span>
      </span>
    )
  }

  if (query.status === 'completed') {
    // Clickable chip — scroll-to triggers IntersectionObserver in
    // ChatArea which eventually dismisses it. We don't dismiss
    // here directly so the chip stays visible during the scroll
    // animation (otherwise it'd vanish before the user's eye
    // catches the result).
    return (
      <button
        type="button"
        onClick={() => onScrollTo(query.query_id)}
        className={`${base} cursor-pointer border-[var(--color-brand)]/30 bg-[var(--color-brand-tint-bg)] text-[var(--color-brand-tint-text)] hover:bg-[var(--color-brand-tint-bg)] hover:ring-1 hover:ring-[var(--color-brand)]/40 focus:outline-none focus:ring-2 focus:ring-[var(--color-ring)]`}
        data-testid={`room-query-chip-${query.query_id}`}
        data-status="completed"
      >
        <Check className="h-3 w-3" aria-hidden="true" />
        <span>#{query.target_room_name}</span>
        <span>
          {query.responded}/{query.expected}
        </span>
      </button>
    )
  }

  if (query.status === 'timeout') {
    const missing = Math.max(query.expected - query.responded, 0)
    return (
      <span
        className={`${base} border-[var(--color-warning)]/30 bg-[#fff5ec] text-[var(--color-warning)]`}
        data-testid={`room-query-chip-${query.query_id}`}
        data-status="timeout"
      >
        <AlertTriangle className="h-3 w-3" aria-hidden="true" />
        <span>#{query.target_room_name}</span>
        <span>
          {query.responded}/{query.expected}
          {missing > 0 ? ` · ${missing}명 미응답` : ''}
        </span>
        <button
          type="button"
          onClick={() => onDismiss(query.query_id)}
          className="ml-0.5 rounded p-0.5 hover:bg-black/5 focus:outline-none focus:ring-1 focus:ring-[var(--color-ring)]"
          aria-label="알림 닫기"
        >
          <X className="h-3 w-3" aria-hidden="true" />
        </button>
      </span>
    )
  }

  // solo
  return (
    <span
      className={`${base} border-[var(--color-border)] bg-white text-[var(--color-foreground-muted)]`}
      data-testid={`room-query-chip-${query.query_id}`}
      data-status="solo"
    >
      <UserX className="h-3 w-3" aria-hidden="true" />
      <span>#{query.target_room_name}</span>
      <span>응답 가능 에이전트 없음</span>
      <button
        type="button"
        onClick={() => onDismiss(query.query_id)}
        className="ml-0.5 rounded p-0.5 hover:bg-black/5 focus:outline-none focus:ring-1 focus:ring-[var(--color-ring)]"
        aria-label="알림 닫기"
      >
        <X className="h-3 w-3" aria-hidden="true" />
      </button>
    </span>
  )
}
