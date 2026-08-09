import { ChevronUp } from 'lucide-react'
import MessageBubble from '@/components/MessageBubble'
import MessageInput from '@/components/MessageInput'
import type { ChatMessage } from '@/hooks/useWebSocket'
import type { Participant } from '@/pages/ChatPage'
import type { MentionOption } from '@/components/MentionPopover'
import { useRoomFiles } from '@/hooks/useRoomFiles'

interface ThreadInlineProps {
  root: ChatMessage
  replies: ChatMessage[]
  participants: Record<string, Participant>
  myParticipantId: string | null
  roomId: string
  connected: boolean
  mentionUsers?: MentionOption[]
  mentionRooms?: MentionOption[]
  onSend: (content: string, metadata?: Record<string, unknown>) => void
  onTyping: (isTyping: boolean) => void
  onCollapse: () => void
}

/**
 * A thread expanded in place, underneath its root message.
 *
 * Trades the panel's stable viewport for keeping the context rail and
 * the surrounding conversation visible. The nesting is carried by an
 * indent and a left rule rather than a box, so an expanded thread
 * reads as part of the timeline instead of a widget dropped into it.
 *
 * Replies arrive through the room's WebSocket stream exactly as they
 * do for the panel; this component owns no message state.
 */
export default function ThreadInline({
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
  onCollapse,
}: ThreadInlineProps) {
  const { files: roomFiles } = useRoomFiles(roomId)

  return (
    <div
      data-testid="thread-inline-root"
      // The tint is the brand's, not the neutral section shade the
      // context rail uses — a thread belongs to the message above it,
      // it isn't chrome. Same hue as the reply affordance's active
      // state, so opening one continues a colour already on screen.
      // Left corners stay square so the rule reads as the thread's
      // spine rather than a floating card edge.
      className="mt-2 ml-8 rounded-r-[var(--radius-md)] border-l-2 border-[var(--color-brand)] bg-[var(--color-brand-tint-bg)] px-4 py-2.5"
    >
      <div className="flex items-center justify-between pb-1.5">
        <span className="text-badge font-medium text-[var(--color-brand-tint-text)]">
          {replies.length === 0
            ? 'No replies yet'
            : `${replies.length} ${replies.length === 1 ? 'reply' : 'replies'}`}
        </span>
        <button
          type="button"
          onClick={onCollapse}
          aria-label="Collapse thread"
          className="flex items-center gap-1 rounded-[var(--radius-sm)] px-1.5 py-0.5 text-badge text-[var(--color-brand-tint-text)] hover:bg-black/5"
        >
          <ChevronUp className="h-3 w-3" />
          Collapse
        </button>
      </div>

      <div className="flex flex-col gap-3 py-1">
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
      </div>

      {/* The nested composer is the reason for the left rule: with two
          inputs on screen the indent is what tells you which one you
          are typing into. */}
      <div className="pt-1">
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
    </div>
  )
}
