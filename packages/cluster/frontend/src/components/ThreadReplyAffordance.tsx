import { MessageSquareReply } from 'lucide-react'
import BrailleSpinner from '@/components/BrailleSpinner'
import TurnCancelControl from '@/components/TurnCancelControl'
import { EntityAvatar, type EntityKind } from '@/components/EntityAvatar'
import type { ChatMessage } from '@/hooks/useWebSocket'
import type { Participant } from '@/pages/ChatPage'
import { formatMessageTimestamp } from '@/lib/datetime'
import { useLocale } from '@/i18n/LocaleProvider'
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
  /** ENG-09 — agents answering inside this thread right now. */
  workingNames?: string[]
  /** Open turns in this thread; one stop control cancels them all. */
  stopTurnIds?: string[]
  onStop?: (requestIds: string[]) => Promise<boolean>
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
  workingNames = [],
  stopTurnIds = [],
  onStop,
}: ThreadReplyAffordanceProps) {
  const { locale, t } = useLocale()
  const count = replyCount(index, root.id)
  const align = isMine ? 'justify-end' : 'justify-start'
  const working = workingNames.length > 0

  // A thread with work under way advertises itself even before its
  // first reply lands, so the reader can see and stop it from here.
  if (count === 0 && !working) {
    return (
      <div className={`mt-1 flex ${align} md:pointer-fine:absolute md:pointer-fine:top-0 md:pointer-fine:mt-0 ${isMine ? 'md:pointer-fine:left-0' : 'md:pointer-fine:right-0'}`}>
        <button
          type="button"
          onClick={() => onOpen(root.id)}
          data-thread-trigger={root.id}
          aria-label={t('chat.replyInThread')}
          className={`
            flex min-h-[var(--control-sm-height)] items-center gap-1 rounded-[var(--radius-sm)] px-2
            text-badge text-[var(--color-foreground-subtle)] md:pointer-fine:bg-[var(--color-surface)]
            transition-opacity hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-foreground-muted)]
            focus-visible:opacity-100 focus-visible:outline-2 focus-visible:outline-[var(--color-brand)]
            ${active ? 'opacity-100' : 'opacity-100 md:pointer-fine:opacity-0 md:group-hover/message:opacity-100'}
          `}
        >
          <MessageSquareReply className="h-3 w-3" />
          {t('chat.replyInThread')}
        </button>
      </div>
    )
  }

  const memberIds = threadParticipantIds(index, root)
  const shown = memberIds.slice(0, MAX_AVATARS)
  const overflow = memberIds.length - shown.length
  const last = lastReplyAt(index, root.id)

  return (
    <div className={`mt-1.5 flex flex-wrap items-center gap-1 ${align}`}>
      <button
        type="button"
        onClick={() => onOpen(root.id)}
        data-thread-trigger={root.id}
        aria-label={t(count === 1 ? 'chat.openThreadReply' : 'chat.openThreadReplies', { count })}
        aria-expanded={active}
        className={`
          flex min-h-[var(--control-sm-height)] max-w-full items-center gap-2 rounded-[var(--radius-md)] border px-2 py-1
          text-badge transition-colors
          focus-visible:outline-2 focus-visible:outline-[var(--color-brand)]
          ${active
            ? 'border-[var(--color-brand)] bg-[var(--color-brand-tint-bg)] text-[var(--color-brand-tint-text)]'
            : 'border-[var(--color-border)] bg-[var(--color-surface)] text-[var(--color-foreground-muted)] hover:border-[var(--color-border-strong)] hover:bg-[var(--color-surface-alt)]'}
        `}
      >
        <span className="flex -space-x-1.5">
          {shown.map(pid => {
            const p = participants[pid]
            return (
              <span key={pid} className="ring-2 ring-[var(--color-surface)] rounded-full">
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
        {count > 0 && (
          <span className="font-medium text-[var(--color-brand-text)]">
            {t(count === 1 ? 'chat.replyCountOne' : 'chat.replyCount', { count })}
          </span>
        )}
        {overflow > 0 && (
          <span className="text-[var(--color-foreground-subtle)]">
            +{overflow}
          </span>
        )}
        {working ? (
          <span className="flex min-w-0 items-center gap-1.5 text-[var(--color-foreground-muted)]">
            <BrailleSpinner />
            <span className="truncate">{t('chat.threadWorking', { names: workingNames.join(', ') })}</span>
          </span>
        ) : last && (
          <span className="truncate text-[var(--color-foreground-subtle)]">
            {formatMessageTimestamp(last, new Date(), locale)}
          </span>
        )}
      </button>
      {working && onStop && (
        <TurnCancelControl
          requestIds={stopTurnIds}
          label={t('chat.stopThreadLabel')}
          text={t('chat.stopThread')}
          onCancel={onStop}
        />
      )}
    </div>
  )
}
