import type { CSSProperties, ReactNode } from 'react'
import { CornerUpLeft, MessageCircleQuestion } from 'lucide-react'
import { useLocale } from '@/i18n/LocaleProvider'
import type { MessagePlacement } from '@/lib/peerChains'

/**
 * Indent for a placed thread message. Level 1 (asked by the root agent)
 * only gets the rule; each further level adds one ``--space-3`` step,
 * capped by ``MAX_THREAD_INDENT`` so a phone keeps a readable column —
 * deeper messages carry their path in the label instead.
 */
export function placementIndentStyle(placement: MessagePlacement): CSSProperties {
  return { marginInlineStart: `calc(var(--space-3) * ${Math.max(0, placement.indent - 1)})` }
}

/** #1006 — a thread message that is a question or answer between agents. */
export default function PeerMessagePath({ placement, children }: { placement: MessagePlacement; children: ReactNode }) {
  const { t } = useLocale()
  const Icon = placement.kind === 'question' ? MessageCircleQuestion : CornerUpLeft
  const fullPath = placement.fullPath.join(' › ')
  return (
    <div
      data-testid="peer-message"
      data-peer-kind={placement.kind}
      data-peer-indent={placement.indent}
      style={placementIndentStyle(placement)}
      className="min-w-0 border-s-2 border-[var(--color-border)] ps-3"
    >
      <p className="mb-1 flex min-w-0 items-center gap-1 text-badge text-[var(--color-foreground-muted)]" title={fullPath}>
        <Icon aria-hidden="true" className="h-3.5 w-3.5 shrink-0" />
        <span className="sr-only">
          {t(placement.kind === 'question' ? 'peerChains.message.question' : 'peerChains.message.reply', { path: fullPath })}
        </span>
        <span aria-hidden="true" className="min-w-0 truncate" data-testid="peer-message-label">{placement.label}</span>
      </p>
      {children}
    </div>
  )
}
