import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
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
import { parseHandoff, isHandoffStatusMessage } from '@/lib/handoff'
import { parseServerDate } from '@/lib/datetime'
import { useRoomFiles } from '@/hooks/useRoomFiles'

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

  // Issue #238 — compute two pieces of per-message state in a single
  // O(n) sweep: the ``handoffResolvedAt`` map (handoff_message_id →
  // target-reply timestamp) and the ``hiddenIds`` set (orchestrator
  // "마이크 넘겼습니다 🎤" style status chatter that should be
  // suppressed in the UI). Both are derived from the message stream
  // alone so no new subscriptions or server changes are needed.
  const { handoffResolvedMap, hiddenMessageIds } = useMemo(() => {
    const resolved = new Map<string, string>()
    const hidden = new Set<string>()
    // Window within which a status-announcement following a handoff
    // is considered part of the same protocol turn (milliseconds).
    const STATUS_WINDOW_MS = 10_000
    for (let i = 0; i < messages.length; i++) {
      const msg = messages[i]
      const handoff = parseHandoff(msg)
      if (handoff) {
        // Forward-scan for the first subsequent message authored by
        // the target participant. That message's timestamp is the
        // "resolved" moment — ``HandoffMessageCard`` stops breathing.
        for (let j = i + 1; j < messages.length; j++) {
          if (
            messages[j].participant_id === handoff.targetParticipantId
          ) {
            resolved.set(msg.id, messages[j].created_at)
            break
          }
        }
      }
      // Hide detection — a later message is hidden when:
      //   1. Its content matches the orchestrator status patterns,
      //   2. There is a preceding handoff from the same sender,
      //   3. The handoff is less than STATUS_WINDOW_MS old.
      // We walk backwards to find the most recent handoff; if it
      // matches the sender/window/pattern we suppress this row.
      if (i > 0 && isHandoffStatusMessage(msg.content)) {
        for (let k = i - 1; k >= 0; k--) {
          const prior = messages[k]
          const priorHandoff = parseHandoff(prior)
          if (!priorHandoff) continue
          if (prior.participant_id !== msg.participant_id) break
          try {
            const delta =
              parseServerDate(msg.created_at).getTime()
              - parseServerDate(prior.created_at).getTime()
            if (delta >= 0 && delta <= STATUS_WINDOW_MS) {
              hidden.add(msg.id)
            }
          } catch {
            // Bad timestamp → don't hide (fail-open is safer than
            // losing a user-visible row).
          }
          break
        }
      }
    }
    return { handoffResolvedMap: resolved, hiddenMessageIds: hidden }
  }, [messages])

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
    .map(pid => participants[pid]?.display_name ?? pid.slice(0, 8))

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, typingNames.length])

  if (messages.length === 0) {
    return (
      <div className="mx-auto flex w-full max-w-3xl flex-1 flex-col items-center justify-center bg-white px-6 py-4 text-center">
        <MessageSquare className="mb-4 h-12 w-12 text-[var(--color-foreground-subtle)] opacity-60" />
        <p className="text-lead text-[var(--color-foreground)]">No messages yet</p>
        <p className="text-caption text-[var(--color-foreground-muted)] mt-1">Start the conversation by sending a message below.</p>
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
          {messages.map((msg, i) => {
            // #238 — drop orchestrator status chatter that the UI
            // already expresses via HandoffMessageCard's breathing
            // border. Returning null here (not just an empty bubble)
            // keeps the gap from spacing-y vestigial.
            if (hiddenMessageIds.has(msg.id)) return null
            // #313 — auto-route protocol echoes (request/response
            // synthetic messages) are internal plumbing, not chat
            // content. The cluster persists them so the audit
            // trail is complete; we just hide them from the
            // user-facing thread.
            const sysOrigin = (msg.metadata as Record<string, unknown> | undefined)
              ?.system_origin
            if (
              sysOrigin === 'auto_route_request' ||
              sysOrigin === 'auto_route_response'
            ) {
              return null
            }
            return (
              <div key={msg.seq || i} data-message-id={msg.id}>
                <MessageBubble
                  message={msg}
                  participants={participants}
                  isMine={msg.participant_id === myParticipantId}
                  pendingQueryIds={pendingQueryIds}
                  handoffResolvedAt={handoffResolvedMap.get(msg.id) ?? null}
                  roomFiles={roomFiles}
                />
              </div>
            )
          })}
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
