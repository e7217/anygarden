import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { ScrollArea } from '@/components/ui/scroll-area'
import MessageBubble from '@/components/MessageBubble'
import RoomQueryBanner from '@/components/RoomQueryBanner'
import BrailleSpinner from '@/components/BrailleSpinner'
import { MessageSquare } from 'lucide-react'
import type { ChatMessage } from '@/hooks/useWebSocket'
import type { Participant } from '@/pages/ChatPage'
import { useRooms } from '@/hooks/useRooms'
import {
  buildPendingQueries,
  seedTerminalDismissals,
} from '@/lib/pending-queries'
import { useRoomFiles } from '@/hooks/useRoomFiles'
import ThreadReplyAffordance from '@/components/ThreadReplyAffordance'
import { canHostThread, type ThreadIndex } from '@/lib/threads'
import { stageLabel, type AgentStage, type PeerProgress } from '@/lib/typingStage'
import { isHiddenSystemMessage } from '@/lib/systemMessages'
import { useLocale } from '@/i18n/LocaleProvider'

interface ChatAreaProps {
  messages: ChatMessage[]
  focusedMessageId?: string | null
  participants: Record<string, Participant>
  myParticipantId: string | null
  typingUsers?: Set<string>
  typingStages?: Record<string, AgentStage>
  typingProgress?: Record<string, PeerProgress>
  /** Grouped view of ``messages`` — replies are rendered in the
   *  thread panel, not inline in this timeline. Computed once by
   *  ``ChatPage`` so the panel and the timeline agree.
   *
   *  Optional on purpose: the guest room (§11.5) is a single flat
   *  surface with no thread panel to open, so it omits this and keeps
   *  the pre-thread behaviour of rendering every message inline.
   *  Grouping there would hide replies with no way to reach them. */
  threadIndex?: ThreadIndex
  /** Root id of the thread currently open — in the side panel or
   *  expanded inline, depending on the display mode. */
  activeThreadRootId?: string | null
  onOpenThread?: (rootMessageId: string) => void
  /** Slot rendered under a root while its thread is the active one.
   *  Supplied only in inline mode; panel mode leaves it undefined and
   *  renders the thread as a sibling column instead. Kept as a render
   *  prop so ChatArea doesn't need the composer's dependencies. */
  renderInlineThread?: (root: ChatMessage) => ReactNode
}

export default function ChatArea({
  messages,
  focusedMessageId,
  participants,
  myParticipantId,
  typingUsers,
  typingStages = {},
  typingProgress = {},
  threadIndex,
  activeThreadRootId,
  onOpenThread,
  renderInlineThread,
}: ChatAreaProps) {
  const { t } = useLocale()
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
  const timeline = threadIndex ? threadIndex.roots : messages

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
  const { files: roomFiles } = useRoomFiles(currentRoomId || null)

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

  // Surfacing query_ids of currently-pending questions so
  // ``MessageBubble`` can render a "응답 대기 중" badge next to the
  // originating question (#94).
  const pendingQueryIds = useMemo(
    () =>
      new Set(
        pendingQueries.filter(q => q.status === 'pending').map(q => q.query_id),
      ),
    [pendingQueries],
  )

  // Seed dismissedIds on room (re-)entry with the terminal chips
  // already present in history. Without this, switching into an old
  // room re-surfaces every timeout/completed/solo chip the user has
  // already seen (#94). ``seededRoomRef`` fires the seed exactly once
  // per room: first we clear so an empty-history render is safe, and
  // the first non-empty ``messages`` snapshot supplies the seed. Later
  // results arriving while the user stays in the room are NOT seeded
  // — they still render as fresh chips.
  const seededRoomRef = useRef<string | null>(null)
  useEffect(() => {
    if (seededRoomRef.current === currentRoomId) return
    if (messages.length === 0) {
      setDismissedIds(new Set())
      return
    }
    setDismissedIds(seedTerminalDismissals(messages, currentRoomId))
    seededRoomRef.current = currentRoomId
  }, [currentRoomId, messages])

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
    .map(pid => {
      const name = participants[pid]?.display_name ?? pid.slice(0, 8)
      const stage = typingStages[pid]
      if (!stage) return name
      return `${name} · ${stageLabel(t, stage, typingProgress[pid])}`
    })

  useEffect(() => {
    if (focusedMessageId) return
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, typingNames.length, focusedMessageId])

  if (messages.length === 0) {
    return (
      <div className="mx-auto flex w-full max-w-3xl flex-1 flex-col items-center justify-center bg-[var(--color-surface)] px-6 py-4 text-center">
        <MessageSquare className="mb-4 h-12 w-12 text-[var(--color-foreground-subtle)] opacity-60" />
        <p className="text-lead text-[var(--color-foreground)]">{t('chat.noMessages')}</p>
        <p className="text-caption text-[var(--color-foreground-muted)] mt-1">{t('chat.startConversation')}</p>
      </div>
    )
  }

  return (
    <div className="flex flex-1 flex-col bg-[var(--color-surface)] min-h-0">
      <RoomQueryBanner
        queries={pendingQueries}
        onDismiss={handleDismiss}
        onScrollTo={handleScrollTo}
      />
      <ScrollArea className="flex-1 bg-[var(--color-surface)]" ref={scrollRootRef}>
        <div className="mx-auto flex w-full max-w-3xl flex-col gap-5 px-6 py-4">
          {/* With a thread index, only top-level messages appear here —
              replies live in the panel. ``roots`` preserves stream order
              and keeps orphaned replies (root outside the loaded
              window) visible rather than dropping them. Without one
              (guest room) every message renders inline as before. */}
          {timeline.map((msg, i) => {
            // #313 / #762 — auto-route echoes and ask_peer result messages
            // are internal plumbing, not chat content. The cluster persists
            // them so the audit trail is complete; we just hide them.
            if (isHiddenSystemMessage(msg)) return null
            return (
              <div key={msg.seq || i} data-message-id={msg.id} className="group/message relative">
                <MessageBubble
                  message={msg}
                  participants={participants}
                  isMine={msg.participant_id === myParticipantId}
                  pendingQueryIds={pendingQueryIds}
                  roomFiles={roomFiles}
                />
                {/* An orphaned reply renders here so it isn't lost, but it
                    cannot host a thread — the server rejects a thread rooted
                    at a reply, so an affordance would be a button that always
                    fails. Its own thread is reachable once its root loads. */}
                {threadIndex && onOpenThread && canHostThread(threadIndex, msg.id) && (
                  <ThreadReplyAffordance
                    root={msg}
                    index={threadIndex}
                    participants={participants}
                    isMine={msg.participant_id === myParticipantId}
                    active={activeThreadRootId === msg.id}
                    onOpen={onOpenThread}
                  />
                )}
                {renderInlineThread
                  && activeThreadRootId === msg.id
                  && (!threadIndex || canHostThread(threadIndex, msg.id))
                  && renderInlineThread(msg)}
              </div>
            )
          })}
          {typingNames.length > 0 && (
            <div className="flex flex-col items-start">
              <span className="text-badge text-[var(--color-foreground-muted)] mb-1 pl-1">
                {typingNames.join(', ')}
              </span>
              <div className="max-w-[85%] rounded-[var(--radius-lg)] rounded-tl-[var(--radius-xs)] bg-[var(--color-surface)] border border-[var(--color-border)] px-4 py-2.5 sm:max-w-[75%] md:max-w-[70%]">
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
