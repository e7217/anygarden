import { LoaderCircle, MessagesSquare } from 'lucide-react'
import { Button } from '@/components/ui/button'
import PeerChainStopButton from '@/components/peer-chains/PeerChainStopButton'
import { useLocale } from '@/i18n/LocaleProvider'
import { currentActivity, type ChainActivity, type PeerChain } from '@/lib/peerChains'
import type { MessageKey } from '@/i18n/messages'

type Translate = (key: MessageKey, values?: Record<string, string | number>) => string

export function activityText(t: Translate, activity: ChainActivity): string {
  if (activity.kind === 'finishing') return t('peerChains.activity.finishing', { name: activity.name })
  if (activity.others > 0) {
    return t(activity.kind === 'answering' ? 'peerChains.activity.answeringMore' : 'peerChains.activity.pendingMore', {
      name: activity.name,
      count: activity.others,
    })
  }
  return t(activity.kind === 'answering' ? 'peerChains.activity.answering' : 'peerChains.activity.pending', {
    name: activity.name,
  })
}

interface PeerChainProgressCardProps {
  chain: PeerChain
  /** Opens the thread holding the chain's questions. */
  onOpenThread?: (rootMessageId: string) => void
  /** Stops the chain; omitted when the viewer may not stop agents. */
  onCancel?: (chainId: string) => Promise<boolean>
}

/**
 * #1006 — one line under the message that started a running question
 * chain: who is gathering answers, how wide and deep it went, and who is
 * working right now. The questions themselves live in the thread; the
 * card disappears once the chain finishes.
 */
export default function PeerChainProgressCard({ chain, onOpenThread, onCancel }: PeerChainProgressCardProps) {
  const { t } = useLocale()
  if (chain.state !== 'active') return null
  const threadRootId = chain.thread_root_id
  const parts = [
    t(chain.agent_count === 1 ? 'peerChains.progress.agentsOne' : 'peerChains.progress.agents', { count: chain.agent_count }),
    t('peerChains.progress.depth', { depth: chain.max_depth }),
    t('peerChains.progress.now', { activity: activityText(t, currentActivity(chain)) }),
  ]

  return (
    <div
      role="group"
      aria-label={t('peerChains.progress.label')}
      data-testid="peer-chain-progress"
      data-chain-id={chain.id}
      className="mt-2 flex w-full flex-wrap items-center gap-x-3 gap-y-1 rounded-[var(--radius-md)] border border-[var(--color-border)] bg-[var(--color-surface-alt)] px-3 py-1.5"
    >
      <p className="flex min-w-0 flex-1 basis-60 items-start gap-2 text-caption text-[var(--color-foreground-muted)]">
        {/* A busy signal, so it keeps turning (slowly) under reduced motion. */}
        <LoaderCircle aria-hidden="true" className="mt-0.5 h-4 w-4 shrink-0 animate-spin" />
        <span className="min-w-0">
          <span className="font-medium text-[var(--color-foreground)]">
            {t('peerChains.progress.collecting', { name: chain.root.name })}
          </span>
          {parts.map(part => <span key={part}>{` · ${part}`}</span>)}
        </span>
      </p>
      <div className="flex shrink-0 flex-wrap items-center gap-1">
        {threadRootId && onOpenThread && (
          <Button type="button" variant="outline" size="sm" onClick={() => onOpenThread(threadRootId)} className="gap-1.5">
            <MessagesSquare aria-hidden="true" />
            <span className="text-caption">{t('peerChains.openThread')}</span>
          </Button>
        )}
        {onCancel && <PeerChainStopButton chain={chain} onCancel={onCancel} />}
      </div>
    </div>
  )
}
