import { useEffect, useRef } from 'react'
import { X, MessagesSquare } from 'lucide-react'
import { ScrollArea } from '@/components/ui/scroll-area'
import MessageBubble from '@/components/MessageBubble'
import MessageInput from '@/components/MessageInput'
import type { ChatMessage } from '@/hooks/useWebSocket'
import type { Participant } from '@/pages/ChatPage'
import type { MentionOption } from '@/components/MentionPopover'
import { useRoomFiles } from '@/hooks/useRoomFiles'

interface ThreadPanelProps {
  /** The top-level message the thread hangs off. */
  root: ChatMessage
  /** Replies to ``root``, already ordered by seq. */
  replies: ChatMessage[]
  participants: Record<string, Participant>
  myParticipantId: string | null
  roomId: string
  connected: boolean
  mentionUsers?: MentionOption[]
  mentionRooms?: MentionOption[]
  /** Posts a reply. The caller supplies the thread root id. */
  onSend: (content: string, metadata?: Record<string, unknown>) => void
  onTyping: (isTyping: boolean) => void
  onClose: () => void
}

/**
 * Side panel for a single message thread.
 *
 * Rendered as a flex sibling of the main chat column, mirroring
 * ``RightContextRail``'s drawer-on-mobile / column-on-desktop chrome
 * so both side surfaces read as one system. Unlike the rail this
 * panel is conditionally mounted — a closed thread has no residual
 * width to collapse.
 *
 * The panel holds no message state of its own: replies arrive through
 * the room's existing WebSocket stream and are handed down already
 * grouped (see ``lib/threads.ts``), so a reply posted here appears
 * through the same path as any other message.
 */
export default function ThreadPanel({
  root,
  replies,
  participants,
  myParticipantId,
  roomId,
  connected,
  mentionUsers,
  mentionRooms,
  onSend,
  onTyping,
  onClose,
}: ThreadPanelProps) {
  const bottomRef = useRef<HTMLDivElement>(null)
  const { files: roomFiles } = useRoomFiles(roomId)

  // ESC closes the panel, matching the rail's drawer behaviour.
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [onClose])

  // Follow new replies. Keyed on count rather than the array so a
  // re-render with identical contents doesn't yank the viewport.
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [replies.length])

  return (
    <>
      {/* Mobile backdrop — same chrome as the context rail's. */}
      <button
        type="button"
        aria-label="Close thread"
        className="fixed inset-0 z-30 bg-black/25 backdrop-blur-[1px] md:hidden"
        onClick={onClose}
      />

      <aside
        data-testid="thread-panel-root"
        aria-label="Thread"
        // Width staging mirrors ``RightContextRail`` (#329) because the
        // two occupy the same slot — a thread must not resize the chat
        // column relative to the rail it replaced.
        className="
          fixed inset-y-0 right-0 z-40 flex h-full min-w-0 w-full flex-col
          border-l border-[var(--color-border)] bg-white shadow-deep
          sm:w-96
          md:static md:z-auto md:w-72 md:shadow-none lg:w-80 xl:w-96
        "
      >
        <div className="flex h-12 shrink-0 items-center justify-between border-b border-[var(--color-border)] px-3">
          <div className="flex min-w-0 items-center gap-2">
            <MessagesSquare className="h-4 w-4 shrink-0 text-[var(--color-foreground-muted)]" />
            <h2 className="text-[12px] font-semibold uppercase tracking-wider text-[var(--color-foreground-muted)]">
              Thread
            </h2>
            <span className="text-badge text-[var(--color-foreground-subtle)]">
              {replies.length === 0
                ? 'No replies yet'
                : `${replies.length} ${replies.length === 1 ? 'reply' : 'replies'}`}
            </span>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-[var(--radius-sm)] p-1 text-[var(--color-foreground-muted)] hover:bg-black/5"
            aria-label="Close thread"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <ScrollArea className="min-w-0 flex-1">
          <div className="flex flex-col gap-4 px-4 py-4">
            <div data-message-id={root.id}>
              <MessageBubble
                message={root}
                participants={participants}
                isMine={root.participant_id === myParticipantId}
                roomFiles={roomFiles}
              />
            </div>

            <div className="flex items-center gap-3" aria-hidden="true">
              <span className="h-px flex-1 bg-[var(--color-border)]" />
              <span className="text-badge text-[var(--color-foreground-subtle)]">
                {replies.length === 0
                  ? 'Start of thread'
                  : `${replies.length} ${replies.length === 1 ? 'reply' : 'replies'}`}
              </span>
              <span className="h-px flex-1 bg-[var(--color-border)]" />
            </div>

            {replies.map(msg => (
              <div key={msg.id} data-message-id={msg.id}>
                <MessageBubble
                  message={msg}
                  participants={participants}
                  isMine={msg.participant_id === myParticipantId}
                  roomFiles={roomFiles}
                />
              </div>
            ))}
            <div ref={bottomRef} />
          </div>
        </ScrollArea>

        <div className="shrink-0 border-t border-[var(--color-border)]">
          <MessageInput
            onSend={onSend}
            onTyping={onTyping}
            disabled={!connected}
            mentionUsers={mentionUsers}
            mentionRooms={mentionRooms}
            roomId={roomId}
            placeholder="Reply to thread…"
          />
        </div>
      </aside>
    </>
  )
}
