import { useState, useCallback, memo, useMemo } from 'react'
import { Bookmark, BookmarkCheck, CornerDownRight } from 'lucide-react'
import type { ChatMessage } from '@/hooks/useWebSocket'
import type { Participant } from '@/pages/ChatPage'
import MarkdownContent from '@/components/MarkdownContent'
import RoomQueryResultCard from '@/components/RoomQueryResultCard'
import BrailleSpinner from '@/components/BrailleSpinner'
import { apiFetch } from '@/lib/api'
import { useRooms } from '@/hooks/useRooms'
import {
  parseForward,
  parseQuestion,
  parseResult,
  stripRoomQueryPrefix,
} from '@/lib/room-query'
import { parseServerDate } from '@/lib/datetime'

interface MessageBubbleProps {
  message: ChatMessage
  participants: Record<string, Participant>
  isMine: boolean
  /** Issue #94 — ``query_id``s of currently in-flight room_query
   * questions. When a question bubble's ``query_id`` is in this set
   * we render a ``BrailleSpinner`` + "응답 대기 중" badge so the user
   * can tie the banner chip back to the originating message. */
  pendingQueryIds?: Set<string>
}

export default memo(function MessageBubble({
  message,
  participants,
  isMine,
  pendingQueryIds,
}: MessageBubbleProps) {
  const [saved, setSaved] = useState(false)
  const { rooms } = useRooms()

  const resolveUser = useCallback(
    (id: string) => participants[id]?.display_name,
    [participants],
  )
  const resolveRoom = useCallback(
    (id: string) => {
      for (const projectRooms of Object.values(rooms)) {
        const found = projectRooms.find(r => r.id === id)
        if (found) return { name: found.name, id: found.id }
      }
      return undefined
    },
    [rooms],
  )
  const participant = message.participant_id ? participants[message.participant_id] : undefined
  // participant_id can be null if the sender has left the room (FK SET NULL).
  // Fall back to a clear "left the room" label instead of a UUID slice.
  const displayName = participant?.display_name
    ?? (message.participant_id ? message.participant_id.slice(0, 8) : '(left the room)')
  const isAgent = participant?.kind === 'agent'
  const isOrphan = message.participant_id == null || participant == null

  const formatTime = (iso: string) => {
    try {
      const d = parseServerDate(iso)
      return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
    } catch {
      return ''
    }
  }

  // ---------- Issue #55: room_query result / forward variants ----------
  // Result variant delegates entirely to RoomQueryResultCard. The
  // card ignores the body (which still carries the legacy
  // ``[취합 결과] ...`` text so ``should_respond`` keeps working)
  // and renders from metadata. We pre-compute the participant
  // names / room name here so the card stays presentational.
  const resultMeta = useMemo(() => parseResult(message), [message])
  const forwardMeta = useMemo(() => parseForward(message), [message])
  const questionMeta = useMemo(() => parseQuestion(message), [message])
  const isPendingQuestion = !!(
    questionMeta && pendingQueryIds?.has(questionMeta.query_id)
  )
  const pendingBadge = isPendingQuestion ? (
    <span
      data-testid="question-pending-badge"
      className="inline-flex items-center gap-1 text-[11px] text-[var(--color-foreground-subtle)]"
    >
      <BrailleSpinner />
      <span>응답 대기 중</span>
    </span>
  ) : null

  const participantNamesMap = useMemo(() => {
    const m = new Map<string, string>()
    for (const [pid, p] of Object.entries(participants)) {
      m.set(pid, p.display_name)
    }
    return m
  }, [participants])

  const toggleSave = async () => {
    if (saved) {
      await apiFetch(`/api/v1/saved/${message.id}`, { method: 'DELETE' })
      setSaved(false)
    } else {
      await apiFetch('/api/v1/saved', {
        method: 'POST',
        body: JSON.stringify({ message_id: message.id }),
      })
      setSaved(true)
    }
  }

  const bookmarkBtn = (
    <button
      onClick={toggleSave}
      className="opacity-0 group-hover:opacity-100 transition-opacity p-0.5 rounded hover:bg-black/5"
      title={saved ? 'Remove bookmark' : 'Bookmark this message'}
    >
      {saved
        ? <BookmarkCheck className="h-3.5 w-3.5 text-[var(--color-brand)]" />
        : <Bookmark className="h-3.5 w-3.5 text-[var(--color-foreground-subtle)]" />}
    </button>
  )

  // Result variant: full-width structured card. We still expose
  // the bookmark button in the corner so users can save the
  // aggregated result like any other message.
  if (resultMeta) {
    const targetRoomName = resolveRoom(resultMeta.target_room_id)?.name
    return (
      <div className="group flex flex-col items-start">
        <div className="flex items-center gap-1 mb-1 pl-1">
          <span className="text-badge text-[var(--color-foreground-muted)]">
            {displayName}
          </span>
          {bookmarkBtn}
        </div>
        <div className="w-full">
          <RoomQueryResultCard
            result={resultMeta}
            participantNames={participantNamesMap}
            targetRoomName={targetRoomName}
          />
        </div>
        <span className="text-[11px] text-[var(--color-foreground-subtle)] mt-1 pl-1">
          {formatTime(message.created_at)}
        </span>
      </div>
    )
  }

  // Forward variant: shown in the *target* room. Looks like a
  // normal agent message with a left accent bar and a source
  // badge (``↪ #srcRoom · @srcUser``). We strip the
  // ``[ROOM_QUERY] `` prefix for rendering only — the wire body
  // stays prefixed so ``should_respond`` can still detect it.
  if (forwardMeta) {
    const srcRoom = resolveRoom(forwardMeta.source_room_id)
    const srcRoomLabel = srcRoom?.name ?? forwardMeta.source_room_id.slice(-6)
    const srcUserLabel = forwardMeta.source_participant_id
      ? (resolveUser(forwardMeta.source_participant_id) ??
        forwardMeta.source_participant_id.slice(-6))
      : null
    const stripped = stripRoomQueryPrefix(message.content)
    return (
      <div className="group flex flex-col items-start">
        <div className="flex items-center gap-1 mb-1 pl-1">
          <span className="text-badge text-[var(--color-foreground-muted)]">
            {displayName}
          </span>
          {bookmarkBtn}
        </div>
        <div
          className="relative w-full rounded-[var(--radius-lg)] rounded-tl-[var(--radius-xs)] border border-[var(--color-border)] bg-white pl-4 pr-3 py-2"
          data-testid="room-query-forward"
        >
          <div
            aria-hidden="true"
            className="absolute left-0 top-0 h-full w-[3px] rounded-l-[var(--radius-lg)] bg-[var(--color-brand)]"
          />
          <div
            className="mb-1 flex items-center gap-1 text-xs text-[var(--color-foreground-muted)]"
            data-testid="room-query-forward-badge"
          >
            <CornerDownRight className="h-3 w-3" aria-hidden="true" />
            <span className="font-medium text-[var(--color-foreground)]">
              #{srcRoomLabel}
            </span>
            {srcUserLabel && (
              <>
                <span>·</span>
                <span>@{srcUserLabel}</span>
              </>
            )}
          </div>
          <MarkdownContent
            content={stripped}
            resolveUser={resolveUser}
            resolveRoom={resolveRoom}
          />
        </div>
        <span className="text-[11px] text-[var(--color-foreground-subtle)] mt-1 pl-1">
          {formatTime(message.created_at)}
        </span>
      </div>
    )
  }

  if (isMine) {
    // 나 = 오른쪽
    return (
      <div className="group flex flex-col items-end">
        <div className="flex items-center gap-1 mb-1 pr-1">
          {bookmarkBtn}
          <span className="text-badge text-[var(--color-foreground-muted)]">나</span>
        </div>
        <div className="max-w-[85%] rounded-[var(--radius-lg)] rounded-tr-[var(--radius-xs)] bg-[var(--color-brand-tint-bg)] px-3 py-2 sm:max-w-[75%] md:max-w-[70%]">
          <MarkdownContent
            content={message.content}
            resolveUser={resolveUser}
            resolveRoom={resolveRoom}
          />
        </div>
        <div className="mt-1 pr-1 flex items-center gap-2 justify-end">
          {pendingBadge}
          <span className="text-[11px] text-[var(--color-foreground-subtle)]">
            {formatTime(message.created_at)}
          </span>
        </div>
      </div>
    )
  }

  // 다른 참여자 = 왼쪽
  const bubbleClass = isOrphan
    ? 'bg-white border border-dashed border-[var(--color-border)] opacity-80'
    : isAgent
      ? 'bg-white border border-[var(--color-border)]'
      : 'bg-[var(--color-surface-alt)]'

  return (
    <div className="group flex flex-col items-start">
      <div className="flex items-center gap-1 mb-1 pl-1">
        <span className="text-badge text-[var(--color-foreground-muted)]">
          {displayName}
        </span>
        {bookmarkBtn}
      </div>
      <div className={`rounded-[var(--radius-lg)] rounded-tl-[var(--radius-xs)] px-3 py-2 ${isAgent ? 'w-full' : 'max-w-[85%] sm:max-w-[75%] md:max-w-[70%]'} ${bubbleClass}`}>
        <MarkdownContent content={message.content} resolveUser={resolveUser} resolveRoom={resolveRoom} />
      </div>
      <div className="mt-1 pl-1 flex items-center gap-2">
        {pendingBadge}
        <span className="text-[11px] text-[var(--color-foreground-subtle)]">
          {formatTime(message.created_at)}
        </span>
      </div>
    </div>
  )
})
