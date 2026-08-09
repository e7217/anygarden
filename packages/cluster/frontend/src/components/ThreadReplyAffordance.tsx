import { MessageSquareReply } from 'lucide-react'
import { EntityAvatar, type EntityKind } from '@/components/EntityAvatar'
import type { ChatMessage } from '@/hooks/useWebSocket'
import type { Participant } from '@/pages/ChatPage'
import { formatMessageTimestamp } from '@/lib/datetime'
import { replyCount, lastReplyAt, threadParticipantIds, type ThreadIndex } from '@/lib/threads'

/** Avatars shown before the count collapses into "+N". */
const MAX_AVATARS = 3

interface ThreadReplyAffordanceProps {
  root: ChatMessage
  index: ThreadIndex
  participants: Record<string, Participant>
  /** Mirrors the bubble's alignment so the control sits under it. */
  isMine: boolean
  /** True when this thread is the one open in the side panel. */
  active: boolean
  onOpen: (rootMessageId: string) => void
}

/**
 * The control that opens a message's thread.
 *
 * Two states, because they answer different questions. A thread with
 * replies advertises itself — who is in it and when it last moved, so
 * the reader can decide whether to open it without doing so. A thread
 * with none is just an offer, and stays quiet until hover so an empty
 * room doesn't read as a wall of buttons.
 */
export default function ThreadReplyAffordance({
  root,
  index,
  participants,
  isMine,
  active,
  onOpen,
}: ThreadReplyAffordanceProps) {
  const count = replyCount(index, root.id)
  const align = isMine ? 'justify-end' : 'justify-start'

  if (count === 0) {
    return (
      <div className={`mt-1 flex ${align}`}>
        <button
          type="button"
          onClick={() => onOpen(root.id)}
          aria-label="Reply in thread"
          className={`
            flex items-center gap-1 rounded-[var(--radius-sm)] px-1.5 py-0.5
            text-badge text-[var(--color-foreground-subtle)]
            transition-opacity hover:bg-black/5 hover:text-[var(--color-foreground-muted)]
            focus-visible:opacity-100 focus-visible:outline-2 focus-visible:outline-[var(--color-brand)]
            ${active ? 'opacity-100' : 'opacity-0 group-hover/message:opacity-100'}
          `}
        >
          <MessageSquareReply className="h-3 w-3" />
          Reply in thread
        </button>
      </div>
    )
  }

  const memberIds = threadParticipantIds(index, root)
  const shown = memberIds.slice(0, MAX_AVATARS)
  const overflow = memberIds.length - shown.length
  const last = lastReplyAt(index, root.id)

  return (
    <div className={`mt-1.5 flex ${align}`}>
      <button
        type="button"
        onClick={() => onOpen(root.id)}
        aria-label={`Open thread, ${count} ${count === 1 ? 'reply' : 'replies'}`}
        aria-expanded={active}
        className={`
          flex max-w-full items-center gap-2 rounded-[var(--radius-md)] border px-2 py-1
          text-badge transition-colors
          focus-visible:outline-2 focus-visible:outline-[var(--color-brand)]
          ${active
            ? 'border-[var(--color-brand)] bg-[var(--color-brand-tint-bg)] text-[var(--color-brand-tint-text)]'
            : 'border-[var(--color-border)] bg-white text-[var(--color-foreground-muted)] hover:border-[var(--color-border-strong)] hover:bg-[var(--color-surface-alt)]'}
        `}
      >
        <span className="flex -space-x-1.5">
          {shown.map(pid => {
            const p = participants[pid]
            return (
              <span key={pid} className="ring-2 ring-white rounded-full">
                <EntityAvatar
                  id={pid}
                  name={p?.display_name ?? pid.slice(0, 8)}
                  kind={(p?.kind as EntityKind) ?? 'user'}
                  size="xs"
                />
              </span>
            )
          })}
        </span>
        <span className="font-medium text-[var(--color-brand)]">
          {count} {count === 1 ? 'reply' : 'replies'}
        </span>
        {overflow > 0 && (
          <span className="text-[var(--color-foreground-subtle)]">
            +{overflow}
          </span>
        )}
        {last && (
          <span className="truncate text-[var(--color-foreground-subtle)]">
            {formatMessageTimestamp(last)}
          </span>
        )}
      </button>
    </div>
  )
}
