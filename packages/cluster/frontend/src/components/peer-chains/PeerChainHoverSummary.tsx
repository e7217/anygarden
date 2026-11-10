import { useMemo } from 'react'
import PeerStateBadge from '@/components/peer-chains/PeerStateBadge'
import { useLocale } from '@/i18n/LocaleProvider'
import { buildChainTree, MAX_THREAD_INDENT, type ChainTreeNode, type PeerChain } from '@/lib/peerChains'

interface PeerChainHoverSummaryProps {
  chain: PeerChain
  id: string
  /** Mirrors the reply row's alignment so the summary opens under it. */
  alignEnd: boolean
}

/**
 * #1013 — who asked whom, at a glance, while hovering or focusing a
 * thread's reply row. Read-only and never focusable: the row itself is a
 * button, and filtering, the graph and per-branch work stay in the
 * thread panel's call tree. Pointer-fine only; on touch the row opens
 * the thread, which shows that tree.
 */
export default function PeerChainHoverSummary({ chain, id, alignEnd }: PeerChainHoverSummaryProps) {
  const { t } = useLocale()
  const tree = useMemo(() => buildChainTree(chain), [chain])

  const rows: { entry: ChainTreeNode; level: number }[] = []
  const stack = tree.roots.map(entry => ({ entry, level: 1 })).reverse()
  const seen = new Set<string>()
  while (stack.length > 0) {
    const item = stack.pop()!
    if (seen.has(item.entry.node.id)) continue
    seen.add(item.entry.node.id)
    rows.push(item)
    for (let i = item.entry.children.length - 1; i >= 0; i--) {
      stack.push({ entry: item.entry.children[i], level: item.level + 1 })
    }
  }

  return (
    <div
      id={id}
      role="tooltip"
      data-testid="peer-chain-hover"
      className={`
        absolute top-full z-20 mt-1 hidden w-80 max-w-[calc(100vw-2rem)] flex-col gap-1
        rounded-[var(--radius-md)] border border-[var(--color-border)] bg-[var(--color-surface)] p-3 shadow-deep
        md:pointer-fine:group-hover/chain:flex md:pointer-fine:group-focus-within/chain:flex
        ${alignEnd ? 'right-0' : 'left-0'}
      `}
    >
      <p className="text-caption font-medium text-[var(--color-foreground)]">{t('peerChains.hover.label')}</p>
      <ul className="flex flex-col gap-0.5">
        {rows.map(({ entry, level }) => (
          <li
            key={entry.node.id}
            data-node-id={entry.node.id}
            className="flex min-w-0 items-center justify-between gap-2"
            style={{ paddingInlineStart: `calc(var(--space-3) * ${Math.min(level, MAX_THREAD_INDENT) - 1})` }}
          >
            <span className="min-w-0 break-words text-caption text-[var(--color-foreground-muted)]">
              {`${entry.node.caller_name} → ${entry.node.target_name}`}
            </span>
            <PeerStateBadge state={entry.node.state} />
          </li>
        ))}
      </ul>
    </div>
  )
}
