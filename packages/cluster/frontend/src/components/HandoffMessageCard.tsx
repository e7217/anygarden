// frontend/src/components/HandoffMessageCard.tsx
//
// Renders an accepted orchestrator ``[HANDOFF]`` message as a card
// with a breathing top-accent border that signals "waiting on the
// target agent". Issue #238.
//
// The three visual states are driven entirely by the caller:
//
//   * ``pending``  — resolvedAt is null AND createdAt is within
//                    ``HANDOFF_TIMEOUT_MS``. The 1px top accent
//                    animates as a slow left-to-right sweep.
//   * ``resolved`` — resolvedAt is non-null (the target agent has
//                    replied in-room). Accent goes to a static,
//                    translucent brand tint — still visible as a
//                    pair-marker, but no longer moving.
//   * ``timeout``  — resolvedAt is null AND createdAt is older than
//                    ``HANDOFF_TIMEOUT_MS``. Accent switches to a
//                    muted neutral so the card visibly "fades".
//
// The body is collapsed by default (DESIGN.md §4 — keep the row
// lightweight; admins who need the raw instruction can click to
// expand). A ``prefers-reduced-motion`` fallback is declared in
// ``index.css`` so no JS toggle is needed here.

import { memo, useMemo, useState } from 'react'
import { ChevronDown, ChevronRight } from 'lucide-react'
import MarkdownContent from '@/components/MarkdownContent'
import { parseServerDate } from '@/lib/datetime'
import type { HandoffMeta } from '@/lib/handoff'

/** 5 minutes in milliseconds — a pending handoff older than this
 * transitions to the ``timeout`` state so the breathing animation
 * doesn't run indefinitely on stuck rooms. */
export const HANDOFF_TIMEOUT_MS = 5 * 60 * 1000

export type HandoffState = 'pending' | 'resolved' | 'timeout'

interface HandoffMessageCardProps {
  handoff: HandoffMeta
  /** Resolved display name of the target agent. Falls through to the
   * ``targetParticipantId`` id slice when the caller can't look up
   * the participant (left the room, etc.). */
  targetName: string
  createdAt: string
  resolvedAt: string | null
  /** Optional — forwarded to MarkdownContent so ``<@user:>`` tokens in
   * the instruction body render as display names. */
  resolveUser?: (id: string) => string | undefined
  resolveRoom?: (id: string) => { name: string; id: string } | undefined
}

function deriveState(
  createdAt: string,
  resolvedAt: string | null,
): HandoffState {
  if (resolvedAt) return 'resolved'
  try {
    const d = parseServerDate(createdAt).getTime()
    if (Number.isFinite(d) && Date.now() - d >= HANDOFF_TIMEOUT_MS) {
      return 'timeout'
    }
  } catch {
    // Bad timestamp — keep the pending sweep rather than silently
    // greying out the card. The alternative is worse UX.
  }
  return 'pending'
}

const STATE_CLASS: Record<HandoffState, string> = {
  pending: 'handoff-card--pending',
  resolved: 'handoff-card--resolved',
  timeout: 'handoff-card--timeout',
}

export default memo(function HandoffMessageCard({
  handoff,
  targetName,
  createdAt,
  resolvedAt,
  resolveUser,
  resolveRoom,
}: HandoffMessageCardProps) {
  const [expanded, setExpanded] = useState(false)
  const state = useMemo(
    () => deriveState(createdAt, resolvedAt),
    [createdAt, resolvedAt],
  )
  const stateClass = STATE_CLASS[state]
  const hasBody = handoff.instruction.trim().length > 0

  return (
    <div
      data-testid="handoff-card"
      data-state={state}
      className={`handoff-card ${stateClass} relative w-full overflow-hidden rounded-[var(--radius-lg)] border border-[var(--color-border)] bg-white px-3 py-2.5`}
    >
      {/* Top accent bar — the ::before pseudo-element in index.css
          paints the 1px line and drives the sweep animation. */}
      <div
        data-testid="handoff-target-caption"
        className="flex items-center gap-1.5 text-xs text-[var(--color-foreground-muted)]"
      >
        <span aria-hidden="true" className="text-[var(--color-brand)]">
          →
        </span>
        <span className="font-medium text-[var(--color-foreground)]">
          {targetName}
        </span>
        {state === 'timeout' && (
          <span className="ml-1 text-[11px] text-[var(--color-foreground-subtle)]">
            · 응답 없음
          </span>
        )}
      </div>
      {hasBody && (
        <>
          <button
            type="button"
            data-testid="handoff-toggle"
            onClick={() => setExpanded((v) => !v)}
            aria-expanded={expanded}
            aria-controls="handoff-instruction-panel"
            className="mt-1 inline-flex items-center gap-1 text-[11px] text-[var(--color-foreground-subtle)] hover:text-[var(--color-foreground-muted)] focus:outline-none focus-visible:ring-1 focus-visible:ring-[var(--color-brand-focus)] rounded-sm"
          >
            {expanded ? (
              <ChevronDown className="h-3 w-3" aria-hidden="true" />
            ) : (
              <ChevronRight className="h-3 w-3" aria-hidden="true" />
            )}
            <span>{expanded ? '숨기기' : '지시문 보기'}</span>
          </button>
          {expanded && (
            <div
              id="handoff-instruction-panel"
              data-testid="handoff-instruction"
              className="mt-2 border-t border-[var(--color-border-subtle)] pt-2 text-sm text-[var(--color-foreground)]"
            >
              <MarkdownContent
                content={handoff.instruction}
                resolveUser={resolveUser}
                resolveRoom={resolveRoom}
              />
            </div>
          )}
        </>
      )}
    </div>
  )
})
