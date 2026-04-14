import { useState, useCallback, memo } from 'react'
import { Bookmark, BookmarkCheck } from 'lucide-react'
import type { ChatMessage } from '@/hooks/useWebSocket'
import type { Participant } from '@/pages/ChatPage'
import MarkdownContent from '@/components/MarkdownContent'
import { apiFetch } from '@/lib/api'
import { useRooms } from '@/hooks/useRooms'

interface MessageBubbleProps {
  message: ChatMessage
  participants: Record<string, Participant>
  isMine: boolean
}

export default memo(function MessageBubble({ message, participants, isMine }: MessageBubbleProps) {
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
      const d = new Date(iso)
      return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
    } catch {
      return ''
    }
  }

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
        <span className="text-[11px] text-[var(--color-foreground-subtle)] mt-1 pr-1">
          {formatTime(message.created_at)}
        </span>
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
      <span className="text-[11px] text-[var(--color-foreground-subtle)] mt-1 pl-1">
        {formatTime(message.created_at)}
      </span>
    </div>
  )
})
