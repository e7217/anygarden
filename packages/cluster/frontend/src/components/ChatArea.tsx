import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ScrollArea } from '@/components/ui/scroll-area'
import MessageBubble from '@/components/MessageBubble'
import RoomQueryBanner, { type PendingQuery } from '@/components/RoomQueryBanner'
import { MessageSquare } from 'lucide-react'
import type { ChatMessage } from '@/hooks/useWebSocket'
import type { Participant } from '@/pages/ChatPage'
import { useRooms } from '@/hooks/useRooms'
import { parseQuestion, parseResult } from '@/lib/room-query'

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

/** Per-query aggregation derived from the message stream. Pure
 * function — the banner state rebuilds from ``messages`` on every
 * render, which is O(N) but bounded by the 100-message history
 * window the server ships. This also gives us automatic
 * reconnect-restore for free: if the user reloads the page, the
 * history fetch seeds the same view.
 *
 * ``dismissed`` ids come from the banner state so user-acknowledged
 * timeouts / solos stay hidden. */
function buildPendingQueries(
  messages: ChatMessage[],
  currentRoomId: string,
  dismissedIds: Set<string>,
  roomNameLookup: (id: string) => string | undefined,
): PendingQuery[] {
  // question side seeds a pending entry; a later result upgrades
  // status/counts and records the result_message_id so the
  // completed chip knows where to scroll.
  const byQuery = new Map<string, PendingQuery>()
  for (const msg of messages) {
    // Only show chips in the *source* room — the room where the
    // user asked the question. The target room's forward bubble
    // is not a banner concern.
    if (msg.room_id !== currentRoomId) continue

    const q = parseQuestion(msg)
    if (q) {
      const existing = byQuery.get(q.query_id)
      if (!existing) {
        byQuery.set(q.query_id, {
          query_id: q.query_id,
          target_room_id: q.target_room_id,
          target_room_name:
            roomNameLookup(q.target_room_id) ?? `${q.target_room_id.slice(-6)}`,
          status: 'pending',
          responded: 0,
          expected: 0,
        })
      }
      continue
    }

    const r = parseResult(msg)
    if (r) {
      const existing = byQuery.get(r.query_id)
      const merged: PendingQuery = {
        query_id: r.query_id,
        target_room_id: r.target_room_id,
        target_room_name:
          existing?.target_room_name ??
          roomNameLookup(r.target_room_id) ??
          `${r.target_room_id.slice(-6)}`,
        status: r.status,
        responded: r.responded,
        expected: r.expected,
        result_message_id: msg.id,
      }
      byQuery.set(r.query_id, merged)
    }
  }
  // Drop dismissed entries so user-acknowledged chips stay gone
  // across re-renders.
  const out: PendingQuery[] = []
  for (const entry of byQuery.values()) {
    if (dismissedIds.has(entry.query_id)) continue
    out.push(entry)
  }
  return out
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

  const pendingQueries = useMemo(
    () => buildPendingQueries(messages, currentRoomId, dismissedIds, resolveRoomName),
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
