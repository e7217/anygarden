import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ScrollArea } from '@/components/ui/scroll-area'
import MessageBubble from '@/components/MessageBubble'
import RoomQueryBanner from '@/components/RoomQueryBanner'
import { MessageSquare } from 'lucide-react'
import type { ChatMessage } from '@/hooks/useWebSocket'
import type { Participant } from '@/pages/ChatPage'
import { useRooms } from '@/hooks/useRooms'
import { buildPendingQueries } from '@/lib/pending-queries'

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
  // Radix ScrollArea forwards the outer ref to its Root element;
  // the actual scrolling viewport is a descendant with
  // ``data-radix-scroll-area-viewport``. IntersectionObserver
  // needs the viewport as root to correctly detect visibility.
  const scrollRootRef = useRef<HTMLDivElement>(null)
  const getViewport = useCallback((): HTMLElement | null => {
    const root = scrollRootRef.current
    if (!root) return null
    return root.querySelector('[data-radix-scroll-area-viewport]') as HTMLElement | null
  }, [])
  const [dismissedIds, setDismissedIds] = useState<Set<string>>(new Set())
  const { rooms, agentDMs } = useRooms()

  // Room-name resolver — checks regular project rooms first, then
  // agent DMs. Mirrors the lookup ``ChatPage.currentRoom`` does.
  const resolveRoomName = useCallback(
    (id: string): string | undefined => {
      for (const projectRooms of Object.values(rooms)) {
        const found = projectRooms.find(r => r.id === id)
        if (found) return found.name
      }
      const dm = agentDMs.find(r => r.id === id)
      return dm?.name
    },
    [rooms, agentDMs],
  )

  // Derive the current room id from the latest message. This is a
  // pragmatic choice: ChatArea doesn't accept ``roomId`` as a
  // prop, but every message carries ``room_id`` and the
  // useWebSocket hook resets ``messages`` on room switch. Safer
  // than threading a new prop through every call site.
  const currentRoomId = messages.length > 0 ? messages[messages.length - 1].room_id : ''

  // ``new Date()`` is produced inside the factory so a fresh now is
  // read every time ``messages``/``currentRoomId``/``dismissedIds``/
  // ``resolveRoomName`` change. We deliberately do NOT list
  // ``Date.now()`` or ``new Date()`` as a dep — that would create a
  // fresh reference on every render and make this useMemo useless
  // (or, worse, loop if something else depended on its output). TTL
  // accuracy relies on re-renders from new messages, typing events,
  // presence updates, etc., which happen often enough in practice.
  const pendingQueries = useMemo(
    () =>
      buildPendingQueries(
        messages,
        currentRoomId,
        dismissedIds,
        resolveRoomName,
        new Date(),
      ),
    [messages, currentRoomId, dismissedIds, resolveRoomName],
  )

  // Reset dismissed set when we switch rooms so the new room's
  // banner starts clean (dismissing in room A should not suppress
  // an unrelated timeout in room B).
  useEffect(() => {
    setDismissedIds(new Set())
  }, [currentRoomId])

  // Auto-dismiss completed chips once their result bubble scrolls
  // into view. Timeout / solo chips are NOT auto-dismissed — the
  // user needs to see partial answers consciously.
  useEffect(() => {
    const viewport = getViewport()
    if (!viewport) return
    const completedIds = pendingQueries
      .filter(q => q.status === 'completed' && q.result_message_id)
      .map(q => ({ query_id: q.query_id, msg_id: q.result_message_id! }))
    if (completedIds.length === 0) return

    const observer = new IntersectionObserver(
      entries => {
        for (const entry of entries) {
          if (!entry.isIntersecting) continue
          const msgId = (entry.target as HTMLElement).dataset.messageId
          const match = completedIds.find(c => c.msg_id === msgId)
          if (match) {
            setDismissedIds(prev => {
              if (prev.has(match.query_id)) return prev
              const next = new Set(prev)
              next.add(match.query_id)
              return next
            })
          }
        }
      },
      { root: viewport, threshold: 0.4 },
    )
    for (const { msg_id } of completedIds) {
      const el = viewport.querySelector(
        `[data-message-id="${msg_id}"]`,
      )
      if (el) observer.observe(el)
    }
    return () => observer.disconnect()
  }, [pendingQueries, getViewport])

  const handleDismiss = useCallback((queryId: string) => {
    setDismissedIds(prev => {
      const next = new Set(prev)
      next.add(queryId)
      return next
    })
  }, [])

  const handleScrollTo = useCallback(
    (queryId: string) => {
      const q = pendingQueries.find(p => p.query_id === queryId)
      if (!q?.result_message_id) return
      const viewport = getViewport()
      if (!viewport) return
      const el = viewport.querySelector(
        `[data-message-id="${q.result_message_id}"]`,
      )
      if (el) el.scrollIntoView({ behavior: 'smooth', block: 'center' })
    },
    [pendingQueries, getViewport],
  )

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
    <div className="flex flex-1 flex-col bg-white min-h-0">
      <RoomQueryBanner
        queries={pendingQueries}
        onDismiss={handleDismiss}
        onScrollTo={handleScrollTo}
      />
      <ScrollArea className="flex-1 bg-white" ref={scrollRootRef}>
        <div className="mx-auto flex w-full max-w-3xl flex-col gap-5 px-6 py-4">
          {messages.map((msg, i) => (
            <div key={msg.seq || i} data-message-id={msg.id}>
              <MessageBubble
                message={msg}
                participants={participants}
                isMine={msg.participant_id === myParticipantId}
              />
            </div>
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
    </div>
  )
}
