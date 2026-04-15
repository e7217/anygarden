// frontend/src/components/RoomQueryResultCard.tsx
//
// Structured render of the ``[취합 결과]`` summary broadcast back
// to the source room. Issue #55.
//
// The server still sends a plain ``[취합 결과] ...`` body (the
// body prefix is load-bearing for ``should_respond``'s startswith
// path — see plan §6.1). This component ignores the body entirely
// and renders from the ``room_query_result`` metadata blob:
//
//   { query_id, target_room_id, responded, expected, status,
//     responses: [{ participant_id, content }, ...] }
//
// Visual contract:
//   * left 3px accent bar (brand blue) to visually anchor the
//     card to the room-query family — same treatment as the
//     forward variant in MessageBubble.
//   * header: source badge ``↪ #<target> · N/M 응답`` +
//             status-specific tail:
//       - completed: no tail
//       - timeout:   ``· K명 미응답``
//       - solo:      whole header replaced with
//                    ``대상 방에 응답할 에이전트가 없음``
//   * body: one expandable sub-card per response. Default
//           expanded (users want to read answers, not click them
//           open). Header click toggles collapse.
//
// Participant name fallback: if the id isn't in the ChatArea
// participants map (agent left the room, or cross-room query),
// show the last 6 chars of the id so the card still renders
// something meaningful.

import { useState } from 'react'
import { ChevronDown, ChevronUp, UserX } from 'lucide-react'
import type { RoomQueryResultMeta } from '@/lib/room-query'
import MarkdownContent from '@/components/MarkdownContent'

interface RoomQueryResultCardProps {
  result: RoomQueryResultMeta
  /** ``participant_id`` → display-name map threaded from ChatArea. */
  participantNames: Map<string, string>
  /** Resolved target-room name, or fallback (``#id-slice``). */
  targetRoomName?: string
}

/** Last-6-char fallback for unknown participant ids (agent left
 * the room, cross-room query, etc.). Matches the ``↪ #room``
 * fallback used elsewhere for room ids. */
function nameFallback(pid: string): string {
  if (!pid) return '알 수 없음'
  return pid.slice(-6)
}

export default function RoomQueryResultCard({
  result,
  participantNames,
  targetRoomName,
}: RoomQueryResultCardProps) {
  const displayRoom = targetRoomName ?? `#${result.target_room_id.slice(-6)}`
  const missing = Math.max(result.expected - result.responded, 0)

  let header: React.ReactNode
  if (result.status === 'solo') {
    header = (
      <div className="flex items-center gap-2 text-xs text-[var(--color-foreground-muted)]">
        <UserX className="h-3.5 w-3.5" aria-hidden="true" />
        <span>
          ↪ #{displayRoom} · 대상 방에 응답할 에이전트가 없음
        </span>
      </div>
    )
  } else {
    header = (
      <div className="flex items-center gap-1 text-xs text-[var(--color-foreground-muted)]">
        <span>↪</span>
        <span className="font-medium text-[var(--color-foreground)]">
          #{displayRoom}
        </span>
        <span>·</span>
        <span>
          {result.responded}/{result.expected} 응답
        </span>
        {result.status === 'timeout' && missing > 0 && (
          <>
            <span>·</span>
            <span className="text-[var(--color-warning)]">
              {missing}명 미응답
            </span>
          </>
        )}
      </div>
    )
  }

  return (
    <div
      className="relative w-full rounded-[var(--radius-lg)] border border-[var(--color-border)] bg-white pl-4 pr-3 py-3"
      data-testid={`room-query-result-${result.query_id}`}
      data-status={result.status}
    >
      {/* 3px left accent bar — matches the forward variant so the
          two message types read as a pair. */}
      <div
        aria-hidden="true"
        className="absolute left-0 top-0 h-full w-[3px] rounded-l-[var(--radius-lg)] bg-[var(--color-brand)]"
      />
      <div className="mb-2">{header}</div>
      {result.responses.length === 0 ? (
        <p className="text-sm text-[var(--color-foreground-muted)]">
          {result.status === 'solo'
            ? '이 방에서 응답할 에이전트를 찾지 못했습니다.'
            : '아직 응답이 도착하지 않았습니다.'}
        </p>
      ) : (
        <div className="flex flex-col gap-2">
          {result.responses.map((r, idx) => (
            <ResponseCard
              key={`${r.participant_id}-${idx}`}
              participantId={r.participant_id}
              displayName={
                participantNames.get(r.participant_id) ??
                nameFallback(r.participant_id)
              }
              content={r.content}
            />
          ))}
        </div>
      )}
    </div>
  )
}

interface ResponseCardProps {
  participantId: string
  displayName: string
  content: string
}

function ResponseCard({ participantId, displayName, content }: ResponseCardProps) {
  // Default expanded — the whole point of structured rendering is
  // to surface the answers, not hide them behind another click.
  const [expanded, setExpanded] = useState(true)
  const panelId = `resp-panel-${participantId}`

  return (
    <div
      className="rounded-[var(--radius-md)] border border-[var(--color-border-subtle)] bg-[var(--color-surface-alt)]"
      data-testid={`room-query-response-${participantId}`}
    >
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="flex w-full items-center justify-between px-3 py-2 text-left text-xs text-[var(--color-foreground-muted)] hover:bg-black/[0.02] focus:outline-none focus-visible:ring-1 focus-visible:ring-[var(--color-ring)]"
        aria-expanded={expanded}
        aria-controls={panelId}
      >
        <span className="font-medium text-[var(--color-foreground)]">
          @{displayName}
        </span>
        {expanded ? (
          <ChevronUp className="h-3.5 w-3.5" aria-hidden="true" />
        ) : (
          <ChevronDown className="h-3.5 w-3.5" aria-hidden="true" />
        )}
      </button>
      {expanded && (
        <div id={panelId} className="border-t border-[var(--color-border-subtle)] px-3 py-2">
          <MarkdownContent content={content} />
        </div>
      )}
    </div>
  )
}
