import { useState, useCallback, memo, useMemo } from 'react'
import { Bookmark, BookmarkCheck, CornerDownRight, Paperclip } from 'lucide-react'
import type { ChatMessage } from '@/hooks/useWebSocket'
import type { Participant } from '@/pages/ChatPage'
import MarkdownContent from '@/components/MarkdownContent'
import RoomQueryResultCard from '@/components/RoomQueryResultCard'
import HandoffMessageCard from '@/components/HandoffMessageCard'
import BrailleSpinner from '@/components/BrailleSpinner'
import { EntityAvatar, type AvatarKind, type EntityKind } from '@/components/EntityAvatar'
import { apiFetch } from '@/lib/api'
import { useRooms } from '@/hooks/useRooms'
import {
  parseForward,
  parseQuestion,
  parseResult,
  stripRoomQueryPrefix,
} from '@/lib/room-query'
import { parseHandoff, stripHandoffToTrailer } from '@/lib/handoff'
import { parseTaskAssignment, stripTaskMentionPrefix } from '@/lib/taskAssignment'
import TaskAssignmentCard from '@/components/TaskAssignmentCard'
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
  /** Issue #238 — when this message is an accepted orchestrator
   * handoff, the ``created_at`` of the target participant's first
   * subsequent reply (or ``null`` if the target has not yet replied).
   * Drives the breathing-border state on ``HandoffMessageCard``.
   * Ignored for non-handoff messages. ``ChatArea`` computes this by
   * forward-scanning the message stream. */
  handoffResolvedAt?: string | null
}

/** #246 — render file attachments announced via
 * ``metadata.references``. Only ``shared_file`` references are
 * supported for now; future reference kinds can fan out from the
 * same metadata key. */
interface SharedFileReference {
  type: 'shared_file'
  id: string
  name: string
}

function MessageReferences({
  metadata,
}: {
  metadata?: Record<string, unknown>
}) {
  if (!metadata) return null
  const raw = metadata.references
  if (!Array.isArray(raw)) return null
  const refs = raw.filter(
    (r): r is SharedFileReference =>
      typeof r === 'object' && r !== null && (r as { type?: unknown }).type === 'shared_file'
      && typeof (r as { id?: unknown }).id === 'string'
      && typeof (r as { name?: unknown }).name === 'string',
  )
  if (refs.length === 0) return null
  return (
    <div className="mt-2 flex flex-wrap gap-1.5">
      {refs.map(r => (
        <span
          key={r.id}
          className="inline-flex max-w-[180px] items-center gap-1.5 rounded-full border border-[rgba(0,0,0,0.1)] bg-white px-2 py-0.5 text-xs text-[var(--color-foreground-muted)] sm:max-w-[240px] md:max-w-[320px]"
          title={r.name}
        >
          <Paperclip className="h-3 w-3 shrink-0 text-[var(--color-foreground-subtle)]" />
          <span className="truncate">{r.name}</span>
        </span>
      ))}
    </div>
  )
}


export default memo(function MessageBubble({
  message,
  participants,
  isMine,
  pendingQueryIds,
  handoffResolvedAt,
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
  // Issue #238 — handoff is checked BEFORE the room_query variants so
  // an accepted ``[HANDOFF]`` always renders as the breathing card.
  const handoffMeta = useMemo(() => parseHandoff(message), [message])
  // #266 — synthetic task-assignment messages render as a compact
  // card. Checked first because it is the cheapest predicate and the
  // synthetic payload should never accidentally fall through to the
  // legacy text bubble.
  const taskAssignmentMeta = useMemo(
    () => parseTaskAssignment(message),
    [message],
  )
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

  // Avatar kind derivation — participants can be agents, anonymous
  // guests, or regular users. Orphan rows (participant FK cleared
  // to NULL) render a neutral "user" fallback so the avatar still
  // anchors the row visually even when we don't know who spoke.
  const avatarKind: EntityKind = isOrphan
    ? 'user'
    : isAgent
      ? 'agent'
      : participant?.is_anonymous
        ? 'guest'
        : 'user'
  // Seed the tone hash on the most stable id we have. Orphans get
  // a per-message fallback so two orphan rows don't visually collide.
  const avatarId =
    participant?.id ?? message.participant_id ?? `orphan-${message.id}`
  const avatar = (
    <EntityAvatar
      id={avatarId}
      name={displayName}
      kind={avatarKind}
      engine={participant?.engine}
      size="sm"
      // Issue #101 — agent participants carry ``avatar_*`` through
      // ParticipantOut. Users/guests always pass null (kind='user'
      // ignores these fields inside EntityAvatar anyway, but being
      // explicit keeps the call-site easy to audit).
      avatarKind={
        isAgent
          ? ((participant?.avatar_kind as AvatarKind | null | undefined) ?? null)
          : null
      }
      avatarValue={isAgent ? (participant?.avatar_value ?? null) : null}
      data-testid="message-avatar"
    />
  )

  // Task-assignment variant (#266): synthetic message that announces a
  // task (re)assignment. Compact card, no avatar block — the row is
  // server-originated info, not a participant statement, so we suppress
  // the usual sender chrome to keep the channel readable.
  if (taskAssignmentMeta) {
    const assignee = participants[taskAssignmentMeta.assignee_pid]
    const title = stripTaskMentionPrefix(message.content)
    return (
      <div className="group flex flex-col items-start">
        <TaskAssignmentCard
          meta={taskAssignmentMeta}
          title={title || '(untitled task)'}
          assignee={assignee}
        />
        <span className="text-[11px] text-[var(--color-foreground-subtle)] mt-1 pl-1">
          {formatTime(message.created_at)}
        </span>
      </div>
    )
  }

  // Handoff variant (#238): accepted orchestrator handoff renders as
  // a full-width breathing-border card. We still surface the sender's
  // avatar, display name and timestamp — those are the contextual
  // cues admins need to tie the handoff back to its author. The card
  // body itself hides the raw ``[HANDOFF] <@user:...>`` protocol.
  if (handoffMeta) {
    const targetParticipant = participants[handoffMeta.targetParticipantId]
    const targetName =
      targetParticipant?.display_name
        ?? handoffMeta.targetParticipantId.slice(-6)
    return (
      <div className="group flex flex-col items-start">
        <div className="flex items-center gap-1.5 mb-1 pl-1">
          {avatar}
          <span className="text-badge text-[var(--color-foreground-muted)]">
            {displayName}
          </span>
          {bookmarkBtn}
        </div>
        <div className="w-full">
          <HandoffMessageCard
            handoff={handoffMeta}
            targetName={targetName}
            createdAt={message.created_at}
            resolvedAt={handoffResolvedAt ?? null}
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

  // Result variant: full-width structured card. We still expose
  // the bookmark button in the corner so users can save the
  // aggregated result like any other message.
  if (resultMeta) {
    const targetRoomName = resolveRoom(resultMeta.target_room_id)?.name
    return (
      <div className="group flex flex-col items-start">
        <div className="flex items-center gap-1.5 mb-1 pl-1">
          {avatar}
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
    // #155 — prefer the server-supplied snapshot name, which the target
    // room's local ``participants`` map never contains. Fall through to
    // the legacy ``resolveUser`` path for same-room forwards and pre-
    // #155 servers, then to the last-6-hex of the UUID as a last
    // resort. ``||`` (not ``??``) so a selector that ever returns an
    // empty string still drops into the legacy chain.
    const srcUserLabel =
      forwardMeta.source_participant_name ||
      (forwardMeta.source_participant_id
        ? (resolveUser(forwardMeta.source_participant_id) ??
          forwardMeta.source_participant_id.slice(-6))
        : null)
    const stripped = stripRoomQueryPrefix(message.content)
    return (
      <div className="group flex flex-col items-start">
        <div className="flex items-center gap-1.5 mb-1 pl-1">
          {avatar}
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
        <div className="flex items-center gap-1.5 mb-1 pr-1">
          {bookmarkBtn}
          <span className="text-badge text-[var(--color-foreground-muted)]">나</span>
          {avatar}
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

  // Issue #238 — strip any trailing ``handoff_to: ...`` directive the
  // worker may have appended to its reply. Render-time only; the wire
  // body stays intact so server-side consumers are unaffected.
  const renderedContent = isAgent
    ? stripHandoffToTrailer(message.content)
    : message.content
  return (
    <div className="group flex flex-col items-start">
      <div className="flex items-center gap-1.5 mb-1 pl-1">
        {avatar}
        <span className="text-badge text-[var(--color-foreground-muted)]">
          {displayName}
        </span>
        {bookmarkBtn}
      </div>
      {/* #329 Phase 2 — agent replies were ``w-full`` so on narrow
          viewports their markdown/code blocks ran edge-to-edge with
          no breathing room. Stage them like the user bubble but a
          step wider (info-container role): full width below sm,
          progressively tighter at sm/md/lg. Non-agent (orphan/guest)
          uses the tighter user-side ladder. */}
      <div className={`rounded-[var(--radius-lg)] rounded-tl-[var(--radius-xs)] px-3 py-2 ${isAgent ? 'max-w-full sm:max-w-[90%] md:max-w-[85%] lg:max-w-[80%]' : 'max-w-[85%] sm:max-w-[75%] md:max-w-[70%]'} ${bubbleClass}`}>
        <MarkdownContent content={renderedContent} resolveUser={resolveUser} resolveRoom={resolveRoom} />
        <MessageReferences metadata={message.metadata} />
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
