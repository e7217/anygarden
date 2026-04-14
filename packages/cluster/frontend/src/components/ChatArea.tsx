import { useEffect, useRef, useState } from 'react'
import { ScrollArea } from '@/components/ui/scroll-area'
import MessageBubble from '@/components/MessageBubble'
import { MessageSquare } from 'lucide-react'
import type { ChatMessage } from '@/hooks/useWebSocket'
import type { Participant } from '@/pages/ChatPage'

const BRAILLE_FRAMES = ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏']

function BrailleSpinner() {
  const [frame, setFrame] = useState(0)
  useEffect(() => {
    const id = setInterval(() => setFrame(f => (f + 1) % BRAILLE_FRAMES.length), 80)
    return () => clearInterval(id)
  }, [])
  return (
    <span className="text-sm text-[var(--color-foreground-muted)]">
      {BRAILLE_FRAMES[frame]}
    </span>
  )
}

interface ChatAreaProps {
  messages: ChatMessage[]
  participants: Record<string, Participant>
  myParticipantId: string | null
  typingUsers?: Set<string>
}

export default function ChatArea({ messages, participants, myParticipantId, typingUsers }: ChatAreaProps) {
  const bottomRef = useRef<HTMLDivElement>(null)

  const typingNames = Array.from(typingUsers ?? [])
    .filter(pid => pid !== myParticipantId)
    .map(pid => participants[pid]?.display_name ?? pid.slice(0, 8))

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, typingNames.length])

  if (messages.length === 0) {
    return (
      <div className="mx-auto flex w-full max-w-3xl flex-1 flex-col items-center justify-center bg-white px-6 py-4 text-center">
        <MessageSquare className="mb-4 h-12 w-12 text-[var(--color-foreground-subtle)] opacity-60" />
        <p className="text-body-lg text-[var(--color-foreground)]">No messages yet</p>
        <p className="text-caption mt-1">Start the conversation by sending a message below.</p>
      </div>
    )
  }

  return (
    <ScrollArea className="flex-1 bg-white">
      <div className="mx-auto flex w-full max-w-3xl flex-col gap-5 px-6 py-4">
        {messages.map((msg, i) => (
          <MessageBubble
            key={msg.seq || i}
            message={msg}
            participants={participants}
            isMine={msg.participant_id === myParticipantId}
          />
        ))}
        {typingNames.length > 0 && (
          <div className="flex flex-col items-start">
            <span className="text-badge text-[var(--color-foreground-muted)] mb-1 pl-1">
              {typingNames.join(', ')}
            </span>
            <div className="max-w-[85%] rounded-[var(--radius-lg)] rounded-tl-[var(--radius-xs)] bg-white border border-[var(--color-border)] px-4 py-2.5 sm:max-w-[75%] md:max-w-[70%]">
              <BrailleSpinner />
            </div>
          </div>
        )}
        <div ref={bottomRef} />
      </div>
    </ScrollArea>
  )
}
