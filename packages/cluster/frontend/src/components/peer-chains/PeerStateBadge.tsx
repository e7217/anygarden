import { Ban, CircleCheck, CircleX, Clock, Hourglass, LoaderCircle, TimerOff, type LucideIcon } from 'lucide-react'
import { useLocale } from '@/i18n/LocaleProvider'
import type { MessageKey } from '@/i18n/messages'
import type { PeerChainState, PeerNodeState } from '@/lib/peerChains'

type AnyState = PeerNodeState | `chain:${PeerChainState}`

interface StateLook {
  icon: LucideIcon
  /** Semantic token for icon and text; never the only signal. */
  tone: string
  label: MessageKey
  /** A busy signal — spins, slowed (not stopped) under reduced motion. */
  busy?: boolean
}

const MUTED = 'text-[var(--color-foreground-muted)]'

const LOOKS: Record<AnyState, StateLook> = {
  pending: { icon: Clock, tone: MUTED, label: 'peerChains.state.pending' },
  answering: { icon: LoaderCircle, tone: MUTED, label: 'peerChains.state.answering', busy: true },
  waiting: { icon: Hourglass, tone: MUTED, label: 'peerChains.state.waiting' },
  completed: { icon: CircleCheck, tone: 'text-[var(--color-success)]', label: 'peerChains.state.completed' },
  failed: { icon: CircleX, tone: 'text-[var(--color-danger)]', label: 'peerChains.state.failed' },
  cancelled: { icon: Ban, tone: MUTED, label: 'peerChains.state.cancelled' },
  timeout: { icon: TimerOff, tone: 'text-[var(--color-warning)]', label: 'peerChains.state.timeout' },
  'chain:active': { icon: LoaderCircle, tone: MUTED, label: 'peerChains.chainState.active', busy: true },
  'chain:done': { icon: CircleCheck, tone: 'text-[var(--color-success)]', label: 'peerChains.chainState.done' },
  'chain:cancelled': { icon: Ban, tone: MUTED, label: 'peerChains.chainState.cancelled' },
}

/** Icon plus text for a question's (or a whole chain's) state. */
export default function PeerStateBadge({ state, title }: { state: AnyState; title?: string | null }) {
  const { t } = useLocale()
  const look = LOOKS[state] ?? LOOKS.pending
  const Icon = look.icon
  return (
    <span
      className={`inline-flex shrink-0 items-center gap-1 text-badge ${look.tone}`}
      title={title ?? undefined}
      data-state={state}
    >
      <Icon aria-hidden="true" className={`h-3.5 w-3.5 shrink-0 ${look.busy ? 'animate-spin' : ''}`} />
      {t(look.label)}
    </span>
  )
}
