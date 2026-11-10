import { useEffect, useMemo, useRef, useState, type CSSProperties } from 'react'
import { X, MessagesSquare } from 'lucide-react'
import { ScrollArea } from '@/components/ui/scroll-area'
import MessageBubble from '@/components/MessageBubble'
import MessageInput from '@/components/MessageInput'
import type { ChatMessage } from '@/hooks/useWebSocket'
import type { Participant } from '@/pages/ChatPage'
import type { MentionOption } from '@/components/MentionPopover'
import { useRoomFiles } from '@/hooks/useRoomFiles'
import { threadDraftKey } from '@/lib/composerDrafts'
import { useLocale } from '@/i18n/LocaleProvider'
import { useRightSidebarLayout } from '@/hooks/useRightSidebarLayout'
import RailResizeHandle from '@/components/right-rail/RailResizeHandle'
import BrailleSpinner from '@/components/BrailleSpinner'
import TurnCancelControl from '@/components/TurnCancelControl'
import Elapsed from '@/components/Elapsed'
import { Button } from '@/components/ui/button'
import PeerChainTree, { rowLabel } from '@/components/peer-chains/PeerChainTree'
import PeerMessagePath from '@/components/peer-chains/PeerMessagePath'
import {
  branchMessageIds,
  buildChainTree,
  chainsForThread,
  messagePlacements,
  primaryChain,
  type PeerChain,
} from '@/lib/peerChains'

/** ENG-09 — an agent answering inside this thread. */
export interface ThreadWorker {
  participantId: string
  name: string
  /** Localized stage, e.g. "Writing a reply…". */
  stage?: string
  /** #880 — the latest sentence the agent wrote before more work. */
  narration?: string
  /** #893 — when it started working (browser clock, ms). */
  since?: number
  /** This agent's open turns in the thread. */
  requestIds: string[]
}

interface ThreadPanelProps {
  focusedMessageId?: string | null
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
  workers?: ThreadWorker[]
  /** Cancels turns; omitted when the viewer may not stop agents. */
  onCancelTurns?: (requestIds: string[]) => Promise<boolean>
  /** #1006 — the room's agent question chains; the ones rooted at this
   *  thread show a call tree above it and label their messages. */
  peerChains?: PeerChain[]
  /** Stops a chain; omitted when the viewer may not stop agents. */
  onCancelPeerChain?: (chainId: string) => Promise<boolean>
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
  focusedMessageId,
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
  workers = [],
  onCancelTurns,
  peerChains = [],
  onCancelPeerChain,
}: ThreadPanelProps) {
  const { t } = useLocale()
  const bottomRef = useRef<HTMLDivElement>(null)
  const panelRef = useRef<HTMLElement>(null)
  const { width } = useRightSidebarLayout()
  const { files: roomFiles } = useRoomFiles(roomId)

  // #1006 — question chains asked under this thread's root.
  const threadChains = useMemo(() => chainsForThread(peerChains, root.id), [peerChains, root.id])
  const chain = primaryChain(threadChains)
  const chainTree = useMemo(() => (chain ? buildChainTree(chain) : null), [chain])
  const placements = useMemo(() => messagePlacements(threadChains), [threadChains])
  // The branch filter belongs to one thread; switching threads drops it.
  const [branch, setBranch] = useState<{ rootId: string; nodeId: string } | null>(null)
  const branchEntry = branch?.rootId === root.id ? chainTree?.byId.get(branch.nodeId) ?? null : null
  const branchIds = useMemo(
    () => (branchEntry && chainTree ? branchMessageIds(chainTree, branchEntry.node.id) : null),
    [branchEntry, chainTree],
  )
  const shownReplies = branchIds ? replies.filter(msg => branchIds.has(msg.id)) : replies
  const selectBranch = (nodeId: string | null) => setBranch(nodeId ? { rootId: root.id, nodeId } : null)

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
    if (focusedMessageId) return
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [replies.length, workers.length, focusedMessageId])

  return (
    <>
      {/* Mobile backdrop — same chrome as the context rail's. */}
      <button
        type="button"
        aria-label={t('chat.closeThread')}
        data-testid="thread-panel-backdrop"
        className="fixed inset-0 z-30 bg-[var(--color-overlay)] backdrop-blur-[1px] lg:hidden"
        onClick={onClose}
      />

      <aside
        ref={panelRef}
        id="thread-panel"
        data-testid="thread-panel-root"
        aria-label={t('chat.thread')}
        // Width staging mirrors ``RightContextRail`` (#329) because the
        // two occupy the same slot — a thread must not resize the chat
        // column relative to the rail it replaced. #760 — so they share
        // the user-set ``--right-rail-w`` as well.
        style={{ '--right-rail-w': `${width}px` } as CSSProperties}
        className="
          fixed inset-y-0 right-0 z-40 flex h-full min-w-0 w-full flex-col
          border-l border-[var(--color-border)] bg-[var(--color-surface)] shadow-deep
          sm:w-96
          lg:relative lg:z-auto lg:w-[var(--right-rail-w)] lg:min-w-70 lg:shrink lg:shadow-none
        "
      >
        <RailResizeHandle panelRef={panelRef} controls="thread-panel" />
        {/* Header carries the same brand tint the inline layout uses,
            so both surfaces read as one feature rather than two. */}
        <div data-testid="thread-panel-header" className="flex h-12 lg:h-[var(--room-header-h,3.5rem)] shrink-0 items-center justify-between border-b border-[var(--color-border)] bg-[var(--color-brand-tint-bg)] px-3">
          <div className="flex min-w-0 items-center gap-2">
            <MessagesSquare className="h-4 w-4 shrink-0 text-[var(--color-brand-tint-text)]" />
            <h2 className="text-badge font-semibold uppercase tracking-wider text-[var(--color-brand-tint-text)]">
              {t('chat.thread')}
            </h2>
            <span className="text-badge text-[var(--color-brand-tint-text)] opacity-80">
              {replies.length === 0
                ? t('chat.noReplies')
                : t(replies.length === 1 ? 'chat.replyCountOne' : 'chat.replyCount', { count: replies.length })}
            </span>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="flex h-11 w-11 items-center justify-center rounded-[var(--radius-sm)] text-[var(--color-foreground-muted)] hover:bg-[var(--color-surface-hover)]"
            aria-label={t('chat.closeThread')}
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <ScrollArea className="min-w-0 flex-1">
          <div className="flex flex-col gap-4 px-6 py-4">
            {chain && (
              <PeerChainTree
                key={chain.id}
                chain={chain}
                selectedNodeId={branchEntry?.node.id ?? null}
                onSelect={selectBranch}
                onCancel={onCancelPeerChain}
              />
            )}
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
                  ? t('chat.startThread')
                  : t(replies.length === 1 ? 'chat.replyCountOne' : 'chat.replyCount', { count: replies.length })}
              </span>
              <span className="h-px flex-1 bg-[var(--color-border)]" />
            </div>

            {branchEntry && (
              <div
                data-testid="peer-branch-filter"
                className="flex flex-wrap items-center justify-between gap-2 rounded-[var(--radius-md)] bg-[var(--color-surface-selected)] px-3 py-1"
              >
                <p className="min-w-0 text-caption text-[var(--color-foreground)]">
                  {t('peerChains.tree.filtering', { label: rowLabel(branchEntry) })}
                </p>
                <Button type="button" variant="ghost" size="sm" onClick={() => selectBranch(null)}>
                  <span className="text-caption">{t('peerChains.tree.showAll')}</span>
                </Button>
              </div>
            )}
            {branchEntry && shownReplies.length === 0 && (
              <p className="text-caption text-[var(--color-foreground-muted)]">{t('peerChains.tree.branchEmpty')}</p>
            )}

            {shownReplies.map(msg => {
              const bubble = (
                <MessageBubble
                  message={msg}
                  participants={participants}
                  isMine={msg.participant_id === myParticipantId}
                  roomFiles={roomFiles}
                />
              )
              const placement = placements.get(msg.id)
              return (
                <div key={msg.id} data-message-id={msg.id}>
                  {placement ? <PeerMessagePath placement={placement}>{bubble}</PeerMessagePath> : bubble}
                </div>
              )
            })}
            {workers.length > 0 && (
              <ul className="flex flex-col gap-1" data-testid="thread-workers">
                {workers.map(worker => (
                  <li key={worker.participantId} className="flex min-h-[var(--control-icon-size)] items-center gap-2">
                    <BrailleSpinner />
                    <span
                      className="min-w-0 flex-1 truncate text-caption text-[var(--color-foreground-muted)]"
                      title={worker.narration}
                    >
                      {worker.stage ? `${worker.name} · ${worker.stage}` : worker.name}
                      {worker.narration && ` — ${worker.narration}`}
                    </span>
                    {worker.since !== undefined && (
                      <Elapsed since={worker.since} className="shrink-0 text-caption text-[var(--color-foreground-muted)]" />
                    )}
                    {onCancelTurns && (
                      <TurnCancelControl
                        requestIds={worker.requestIds}
                        label={t('chat.stopAgentTurn', { name: worker.name })}
                        onCancel={onCancelTurns}
                      />
                    )}
                  </li>
                ))}
              </ul>
            )}
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
            placeholder={t('chat.replyPlaceholder')}
            autoFocus
            draftKey={threadDraftKey(root.id)}
          />
        </div>
      </aside>
    </>
  )
}
