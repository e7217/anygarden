import { useState, useCallback, memo, useMemo } from 'react'
import { BellOff, Bookmark, BookmarkCheck, CornerDownRight, Paperclip, Pencil, Trash2 } from 'lucide-react'
import type { ChatMessage } from '@/hooks/useWebSocket'
import type { Participant } from '@/pages/ChatPage'
import MarkdownContent from '@/components/MarkdownContent'
import RoomQueryResultCard from '@/components/RoomQueryResultCard'
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
import { parseTaskAssignment, stripTaskMentionPrefix } from '@/lib/taskAssignment'
import TaskAssignmentCard from '@/components/TaskAssignmentCard'
import WorkspaceActionCard, { WorkspaceActionResultNote } from '@/components/WorkspaceActionCard'
import { parseWorkspaceActionRequest, parseWorkspaceActionResult } from '@/lib/workspaceActions'
import { parseDraftFailed, parseDraftRequest } from '@/lib/drafts'
import { DraftFailedNote, DraftRequestNote } from '@/components/drafts/DraftParts'
import AgentSenderMenu from '@/components/AgentSenderMenu'
import { useAgentActionsEnabled } from '@/lib/agentActions'
import { formatMessageTimestamp } from '@/lib/datetime'
import { useLocale } from '@/i18n/LocaleProvider'
import {
  buildFileReferenceCandidates,
  extractSharedFileReferencesFromMetadata,
} from '@/lib/fileReferences'
import type { RoomSharedFile } from '@/lib/roomFiles'
import { extractUndeliveredCalls } from '@/lib/undeliveredCalls'
import MessageAttachments from '@/components/MessageAttachments'
import ExecutionTrace from '@/components/ExecutionTrace'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import { useFeedback } from '@/components/feedback/FeedbackProvider'
import {
  canChangeMessage,
  deleteMessage,
  editMessage,
  isDeletedMessage,
  isEditedMessage,
} from '@/lib/messageEdits'

interface MessageBubbleProps {
  message: ChatMessage
  participants: Record<string, Participant>
  isMine: boolean
  /** Issue #94 — ``query_id``s of currently in-flight room_query
   * questions. When a question bubble's ``query_id`` is in this set
   * we render a ``BrailleSpinner`` + "응답 대기 중" badge so the user
   * can tie the banner chip back to the originating message. */
  pendingQueryIds?: Set<string>
  roomFiles?: RoomSharedFile[]
}

function MessageReferences({
  metadata,
  align = 'start',
}: {
  metadata?: Record<string, unknown>
  align?: 'start' | 'end'
}) {
  const refs = extractSharedFileReferencesFromMetadata(metadata)
  if (refs.length === 0) return null
  return (
    <div className={`mt-2 flex flex-wrap gap-1.5 ${align === 'end' ? 'justify-end' : ''}`}>
      {refs.map(r => (
        <span
          key={r.id}
          className="inline-flex max-w-[180px] items-center gap-1.5 rounded-full border border-[var(--color-border)] bg-[var(--color-surface)] px-2 py-0.5 text-xs text-[var(--color-foreground-muted)] sm:max-w-[240px] md:max-w-[320px]"
          title={r.name}
        >
          {r.origin === 'inline' ? (
            <span className="shrink-0 font-medium text-[var(--color-foreground-subtle)]">$</span>
          ) : (
            <Paperclip className="h-3 w-3 shrink-0 text-[var(--color-foreground-subtle)]" />
          )}
          <span className="truncate">{r.origin === 'inline' ? `$ ${r.name}` : r.name}</span>
        </span>
      ))}
    </div>
  )
}

// #743 — peer calls the server did not deliver. The sentence that made
// the call stays in the bubble; this chip says who was not called.
function UndeliveredCalls({
  metadata,
  resolveUser,
}: {
  metadata?: Record<string, unknown>
  resolveUser: (id: string) => string | undefined
}) {
  const { t } = useLocale()
  const groups = extractUndeliveredCalls(metadata)
  if (groups.length === 0) return null
  return (
    <div className="mt-2 flex flex-wrap gap-1.5">
      {groups.map(g => {
        const names = g.participantIds
          .map(id => resolveUser(id) ?? t('chat.unknownParticipant'))
          .join(', ')
        const label = g.reason === 'already_answering'
          ? t('chat.undeliveredCallAlreadyAnswering', { names })
          : t('chat.undeliveredCallLimitReached', { names })
        return (
          <span
            key={g.reason}
            data-testid="undelivered-call"
            title={t('chat.undeliveredCallTitle')}
            className="inline-flex max-w-full items-center gap-1.5 rounded-full border border-[var(--color-border)] bg-[var(--color-surface-alt)] px-2 py-0.5 text-xs text-[var(--color-foreground-muted)]"
          >
            <BellOff className="h-3 w-3 shrink-0 text-[var(--color-foreground-subtle)]" aria-hidden="true" />
            <span className="min-w-0 break-words">{label}</span>
          </span>
        )
      })}
    </div>
  )
}

export default memo(function MessageBubble({
  message,
  participants,
  isMine,
  pendingQueryIds,
  roomFiles = [],
}: MessageBubbleProps) {
  const [saved, setSaved] = useState(false)
  const { locale, t } = useLocale()
  const { rooms } = useRooms()
  const agentActionsEnabled = useAgentActionsEnabled()
  const fileReferenceCandidates = useMemo(
    () => buildFileReferenceCandidates(message.metadata, roomFiles),
    [message.metadata, roomFiles],
  )

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

  // MSG-08 — author edit / soft delete. The server is the authority; these
  // flags only decide which actions to offer and how to render the row.
  const { confirm, notify } = useFeedback()
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState('')
  const [changeBusy, setChangeBusy] = useState(false)
  const isDeleted = isDeletedMessage(message)
  const isEdited = isEditedMessage(message)
  const canChange = canChangeMessage(message, { isMine, isAgent })

  // #512 — today's messages show the time only; older messages are
  // prefixed with their date. Formatting lives in ``lib/datetime`` so
  // it is unit-tested independently of this component.
  const formatTime = (iso: string) => formatMessageTimestamp(iso, new Date(), locale)

  // ---------- Issue #55: room_query result / forward variants ----------
  // Result variant delegates entirely to RoomQueryResultCard. The
  // card ignores the body (which still carries the legacy
  // ``[취합 결과] ...`` text so ``should_respond`` keeps working)
  // and renders from metadata. We pre-compute the participant
  // names / room name here so the card stays presentational.
  const resultMeta = useMemo(() => parseResult(message), [message])
  const forwardMeta = useMemo(() => parseForward(message), [message])
  const questionMeta = useMemo(() => parseQuestion(message), [message])
  // #266 — synthetic task-assignment messages render as a compact
  // card. Checked first because it is the cheapest predicate and the
  // synthetic payload should never accidentally fall through to the
  // legacy text bubble.
  const taskAssignmentMeta = useMemo(
    () => parseTaskAssignment(message),
    [message],
  )
  // #938 — an agent's workspace change waiting for approval, and the
  // server's report of how it ended.
  const workspaceRequestMeta = useMemo(() => parseWorkspaceActionRequest(message.metadata), [message])
  const workspaceResultMeta = useMemo(() => parseWorkspaceActionResult(message.metadata), [message])
  // #947/#948 — a person's draft request, and the notice when no draft came.
  const draftRequestMeta = useMemo(() => parseDraftRequest(message.metadata), [message])
  const draftFailedMeta = useMemo(() => parseDraftFailed(message.metadata), [message])
  const isPendingQuestion = !!(
    questionMeta && pendingQueryIds?.has(questionMeta.query_id)
  )
  const pendingBadge = isPendingQuestion ? (
    <span
      data-testid="question-pending-badge"
      className="inline-flex items-center gap-1 text-badge font-normal text-[var(--color-foreground-subtle)]"
    >
      <BrailleSpinner />
      <span>{t('chat.awaitingResponse')}</span>
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

  const actionBtnClass = "inline-flex size-[var(--control-icon-size)] shrink-0 items-center justify-center rounded opacity-100 transition-opacity hover:bg-[var(--color-surface-hover)] focus-visible:opacity-100 focus-visible:outline-2 focus-visible:outline-[var(--color-brand-focus)] md:pointer-fine:opacity-0 md:group-hover:opacity-100 md:group-focus-within:opacity-100"

  const bookmarkBtn = isDeleted ? null : (
    <button
      onClick={toggleSave}
      className={actionBtnClass}
      title={saved ? t('chat.removeBookmark') : t('chat.bookmark')}
      aria-label={saved ? t('chat.removeBookmark') : t('chat.bookmark')}
    >
      {saved
        ? <BookmarkCheck className="h-3.5 w-3.5 text-[var(--color-brand-text)]" />
        : <Bookmark className="h-3.5 w-3.5 text-[var(--color-foreground-subtle)]" />}
    </button>
  )

  const startEdit = () => {
    setDraft(message.content)
    setEditing(true)
  }
  const cancelEdit = () => {
    setEditing(false)
    setDraft('')
  }
  const saveEdit = async () => {
    if (changeBusy || !draft.trim()) return
    if (draft === message.content) {
      cancelEdit()
      return
    }
    setChangeBusy(true)
    try {
      await editMessage(message.room_id, message.id, draft)
      cancelEdit()
    } catch (error) {
      notify({ message: error instanceof Error ? error.message : t('common.error'), tone: 'error' })
    } finally {
      setChangeBusy(false)
    }
  }
  const removeMessage = async () => {
    if (changeBusy) return
    const accepted = await confirm({
      title: t('chat.deleteMessageTitle'),
      description: t('chat.deleteMessageDescription'),
      confirmLabel: t('chat.deleteMessageConfirm'),
      destructive: true,
    })
    if (!accepted) return
    setChangeBusy(true)
    try {
      await deleteMessage(message.room_id, message.id)
    } catch (error) {
      notify({ message: error instanceof Error ? error.message : t('common.error'), tone: 'error' })
    } finally {
      setChangeBusy(false)
    }
  }

  const changeActions = canChange && !editing ? (
    <>
      <button
        type="button"
        onClick={startEdit}
        disabled={changeBusy}
        className={actionBtnClass}
        title={t('chat.editMessage')}
        aria-label={t('chat.editMessage')}
      >
        <Pencil className="h-3.5 w-3.5 text-[var(--color-foreground-subtle)]" aria-hidden="true" />
      </button>
      <button
        type="button"
        onClick={removeMessage}
        disabled={changeBusy}
        className={actionBtnClass}
        title={t('chat.deleteMessage')}
        aria-label={t('chat.deleteMessage')}
      >
        <Trash2 className="h-3.5 w-3.5 text-[var(--color-foreground-subtle)]" aria-hidden="true" />
      </button>
    </>
  ) : null

  const editor = (
    <div className="flex w-full flex-col gap-2">
      <Textarea
        aria-label={t('chat.editMessage')}
        value={draft}
        autoFocus
        disabled={changeBusy}
        onChange={event => setDraft(event.target.value)}
        onKeyDown={event => {
          if (event.key === 'Escape') {
            event.preventDefault()
            event.stopPropagation()
            cancelEdit()
          } else if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
            event.preventDefault()
            void saveEdit()
          }
        }}
        className="min-h-20"
      />
      <div className="flex items-center justify-end gap-2">
        <span className="mr-auto hidden text-caption text-[var(--color-foreground-subtle)] md:inline">
          {t('chat.editMessageHint')}
        </span>
        <Button type="button" size="sm" variant="outline" onClick={cancelEdit} disabled={changeBusy}>
          {t('common.cancel')}
        </Button>
        <Button type="button" size="sm" onClick={() => void saveEdit()} disabled={changeBusy || !draft.trim()}>
          {t('common.save')}
        </Button>
      </div>
    </div>
  )

  const deletedPlaceholder = (
    <p className="text-caption italic text-[var(--color-foreground-muted)]">
      {t('chat.messageDeleted')}
    </p>
  )

  const editedMarker = isEdited ? (
    <span
      className="text-badge font-normal text-[var(--color-foreground-subtle)]"
      title={t('chat.messageEditedTitle', { time: formatTime(String(message.metadata?.edited_at)) })}
    >
      {t('chat.messageEdited')}
    </span>
  ) : null

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

  const senderName = (
    <span className="text-badge text-[var(--color-foreground-muted)]">
      {displayName}
    </span>
  )
  // An agent's avatar + name opens its menu (direct message, settings)
  // for viewers allowed to act on agents.
  const sender = isAgent && participant?.agent_id && agentActionsEnabled ? (
    <AgentSenderMenu agentId={participant.agent_id} name={displayName}>
      {avatar}
      {senderName}
    </AgentSenderMenu>
  ) : (
    <>
      {avatar}
      {senderName}
    </>
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
          title={title || t('chat.untitledTask')}
          assignee={assignee}
        />
        <span className="text-badge font-normal text-[var(--color-foreground-subtle)] mt-1 pl-1">
          {formatTime(message.created_at)}
        </span>
      </div>
    )
  }

  if (workspaceRequestMeta) {
    return (
      <div className="group flex flex-col items-start">
        <div className="flex items-center gap-1.5 mb-1 pl-1">{sender}</div>
        <WorkspaceActionCard meta={workspaceRequestMeta} fallbackText={message.content} participants={participants} />
        <span className="text-badge font-normal text-[var(--color-foreground-subtle)] mt-1 pl-1">
          {formatTime(message.created_at)}
        </span>
      </div>
    )
  }

  // The request's body is the agent's brief; people see what they asked.
  if (draftRequestMeta || draftFailedMeta) {
    return (
      <div className="group flex flex-col items-start">
        {draftRequestMeta
          ? <DraftRequestNote meta={draftRequestMeta} participants={participants} />
          : <DraftFailedNote meta={draftFailedMeta!} participants={participants} />}
        <span className="text-badge font-normal text-[var(--color-foreground-subtle)] mt-1 pl-1">
          {formatTime(message.created_at)}
        </span>
      </div>
    )
  }

  // The report's body is written for the agent; people see the outcome.
  if (workspaceResultMeta) {
    return (
      <div className="group flex flex-col items-start">
        <WorkspaceActionResultNote meta={workspaceResultMeta} />
        <span className="text-badge font-normal text-[var(--color-foreground-subtle)] mt-1 pl-1">
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
          {sender}
          {bookmarkBtn}
        </div>
        <div className="w-full">
          <RoomQueryResultCard
            result={resultMeta}
            participantNames={participantNamesMap}
            targetRoomName={targetRoomName}
          />
        </div>
        <span className="text-badge font-normal text-[var(--color-foreground-subtle)] mt-1 pl-1">
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
          {sender}
          {bookmarkBtn}
        </div>
        <div
          className="relative w-full rounded-[var(--radius-lg)] rounded-tl-[var(--radius-xs)] border border-[var(--color-border)] bg-[var(--color-surface)] pl-4 pr-3 py-2"
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
            fileReferenceCandidates={fileReferenceCandidates}
          />
        </div>
        <span className="text-badge font-normal text-[var(--color-foreground-subtle)] mt-1 pl-1">
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
          {changeActions}
          {bookmarkBtn}
          <span className="text-badge text-[var(--color-foreground-muted)]">{t('chat.me')}</span>
          {avatar}
        </div>
        <div
          data-message-body="bubble"
          className={`max-w-[85%] rounded-[var(--radius-lg)] rounded-tr-[var(--radius-xs)] px-3 py-2 sm:max-w-[75%] md:max-w-[70%] ${
            isDeleted
              ? 'border border-dashed border-[var(--color-border)] bg-transparent'
              : 'bg-[var(--color-brand-tint-bg)]'
          } ${editing ? 'w-full' : ''}`}
        >
          {isDeleted ? deletedPlaceholder : editing ? editor : (
            <>
              <MarkdownContent
                content={message.content}
                resolveUser={resolveUser}
                resolveRoom={resolveRoom}
                fileReferenceCandidates={fileReferenceCandidates}
              />
              <MessageReferences metadata={message.metadata} align="end" />
              <MessageAttachments metadata={message.metadata} align="end" />
            </>
          )}
        </div>
        <div className="mt-1 pr-1 flex items-center gap-2 justify-end">
          {pendingBadge}
          {editedMarker}
          <span className="text-badge font-normal text-[var(--color-foreground-subtle)]">
            {formatTime(message.created_at)}
          </span>
        </div>
      </div>
    )
  }

  // 다른 참여자 = 왼쪽
  // #856 — agent replies are results (tables, code, attachments) rather
  // than chat lines, so they drop the bubble and fill the conversation
  // column as prose under their sender row. A box sized to its content
  // left short and long replies at different widths and a long one
  // capped at 80% with an empty strip beside it. #329's reason for the
  // cap (code running edge to edge on phones) is covered by the
  // column's own px-6 gutter. People, orphaned and deleted messages
  // keep their bubble so human conversation still reads as chat.
  const isAgentProse = isAgent && !isOrphan && !isDeleted
  const bubbleClass = isOrphan
    ? 'bg-[var(--color-surface)] border border-dashed border-[var(--color-border)] opacity-80'
    : 'bg-[var(--color-surface-alt)]'
  const bodyClass = isAgentProse
    ? 'w-full min-w-0 px-1'
    : `max-w-[85%] rounded-[var(--radius-lg)] rounded-tl-[var(--radius-xs)] px-3 py-2 sm:max-w-[75%] md:max-w-[70%] ${
      isDeleted ? 'border border-dashed border-[var(--color-border)] bg-transparent' : bubbleClass
    }`

  return (
    <div className={`group flex flex-col ${isAgentProse ? 'items-stretch' : 'items-start'}`}>
      <div className="flex items-center gap-1.5 mb-1 pl-1">
        {sender}
        {bookmarkBtn}
      </div>
      <div data-message-body={isAgentProse ? 'prose' : 'bubble'} className={bodyClass}>
        {isDeleted ? deletedPlaceholder : (
          <>
            <MarkdownContent
              content={message.content}
              resolveUser={resolveUser}
              resolveRoom={resolveRoom}
              fileReferenceCandidates={fileReferenceCandidates}
            />
            <MessageReferences metadata={message.metadata} />
            <MessageAttachments metadata={message.metadata} />
            <UndeliveredCalls metadata={message.metadata} resolveUser={resolveUser} />
            {isAgent && <ExecutionTrace metadata={message.metadata} />}
          </>
        )}
      </div>
      <div className="mt-1 pl-1 flex items-center gap-2">
        {pendingBadge}
        {editedMarker}
        <span className="text-badge font-normal text-[var(--color-foreground-subtle)]">
          {formatTime(message.created_at)}
        </span>
      </div>
    </div>
  )
})
