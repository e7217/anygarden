import { lazy, Suspense, useMemo, useState } from 'react'
import { ChevronRight, GitFork, List, Network } from 'lucide-react'
import { Button } from '@/components/ui/button'
import PeerStateBadge from '@/components/peer-chains/PeerStateBadge'
import PeerChainStopButton from '@/components/peer-chains/PeerChainStopButton'
import { useLocale } from '@/i18n/LocaleProvider'
import {
  buildChainTree,
  defaultExpandedIds,
  type ChainTreeNode,
  type PeerChain,
} from '@/lib/peerChains'

const PeerChainGraph = lazy(() => import('@/components/peer-chains/PeerChainGraph'))

/** Tree rows indent this many levels; deeper rows line up with the last. */
const MAX_TREE_INDENT = 6

export function rowLabel(entry: ChainTreeNode): string {
  return `${entry.node.caller_name} → ${entry.node.target_name}`
}

interface PeerChainTreeProps {
  chain: PeerChain
  /** Node whose branch the thread is filtered to; null shows everything. */
  selectedNodeId: string | null
  onSelect: (nodeId: string | null) => void
  /** Stops the chain; omitted when the viewer may not stop agents. */
  onCancel?: (chainId: string) => Promise<boolean>
}

/**
 * #1006 — who asked whom, at the top of the thread panel.
 *
 * The root row is the agent that started asking; each row below is one
 * question, nested under the question whose answerer asked it. Branches
 * with work still running open by default and finished ones stay folded,
 * so the active path is what a reader sees first. A user's own
 * expand/collapse choice sticks across polls.
 */
export default function PeerChainTree({ chain, selectedNodeId, onSelect, onCancel }: PeerChainTreeProps) {
  const { t } = useLocale()
  const tree = useMemo(() => buildChainTree(chain), [chain])
  const defaults = useMemo(() => defaultExpandedIds(tree), [tree])
  const [toggled, setToggled] = useState<Map<string, boolean>>(() => new Map())
  const [view, setView] = useState<'list' | 'graph'>('list')

  const isExpanded = (id: string) => toggled.get(id) ?? defaults.has(id)
  const toggle = (id: string) => {
    setToggled(previous => new Map(previous).set(id, !isExpanded(id)))
  }

  const renderRow = (entry: ChainTreeNode, level: number) => {
    const { node } = entry
    const hasChildren = entry.children.length > 0
    const expanded = hasChildren && isExpanded(node.id)
    const selected = selectedNodeId === node.id
    const label = rowLabel(entry)
    return (
      <li key={node.id} data-node-id={node.id}>
        <div
          className="flex min-w-0 items-center gap-1"
          style={{ paddingInlineStart: `calc(var(--space-4) * ${Math.min(level, MAX_TREE_INDENT)})` }}
        >
          {hasChildren ? (
            <Button
              type="button"
              variant="ghost"
              size="icon"
              aria-expanded={expanded}
              aria-label={t(expanded ? 'peerChains.tree.collapse' : 'peerChains.tree.expand', { name: node.target_name })}
              onClick={() => toggle(node.id)}
              className="text-[var(--color-foreground-muted)]"
            >
              <ChevronRight aria-hidden="true" className={`transition-transform ${expanded ? 'rotate-90' : ''}`} />
            </Button>
          ) : (
            <span aria-hidden="true" className="size-[var(--control-icon-size)] shrink-0" />
          )}
          <button
            type="button"
            aria-pressed={selected}
            aria-label={`${t('peerChains.tree.filterBranch', { label })} · ${t(`peerChains.state.${node.state}`)}`}
            title={node.question}
            onClick={() => onSelect(selected ? null : node.id)}
            className={`
              flex min-h-[var(--control-sm-height)] min-w-0 flex-1 flex-wrap items-center gap-x-2 gap-y-0.5 rounded-[var(--radius-sm)] px-2 py-1 text-left
              focus-visible:outline-2 focus-visible:outline-[var(--color-brand)]
              ${selected ? 'bg-[var(--color-surface-selected)]' : 'hover:bg-[var(--color-surface-hover)]'}
            `}
          >
            <span className="min-w-0 truncate text-caption text-[var(--color-foreground)]">{label}</span>
            <PeerStateBadge state={node.state} title={node.reason} />
          </button>
        </div>
        {expanded && (
          <ul className="flex flex-col">
            {entry.children.map(child => renderRow(child, level + 1))}
          </ul>
        )}
      </li>
    )
  }

  return (
    <section
      data-testid="peer-chain-tree"
      aria-label={t('peerChains.tree.title')}
      className="flex flex-col gap-2 rounded-[var(--radius-md)] border border-[var(--color-border)] bg-[var(--color-surface-alt)] p-3"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2">
          <GitFork aria-hidden="true" className="h-4 w-4 shrink-0 text-[var(--color-foreground-muted)]" />
          <h3 className="text-caption font-semibold text-[var(--color-foreground)]">{t('peerChains.tree.title')}</h3>
          <span className="text-badge text-[var(--color-foreground-muted)]">
            {t('peerChains.tree.summary', { agents: chain.agent_count, calls: chain.call_count, depth: chain.max_depth })}
          </span>
        </div>
        <div className="flex items-center gap-1">
          <div role="group" aria-label={t('peerChains.tree.viewLabel')} className="flex items-center">
            <Button
              type="button"
              variant="ghost"
              size="icon"
              aria-pressed={view === 'list'}
              aria-label={t('peerChains.tree.viewList')}
              title={t('peerChains.tree.viewList')}
              onClick={() => setView('list')}
              className={view === 'list' ? 'bg-[var(--color-surface-selected)]' : 'text-[var(--color-foreground-muted)]'}
            >
              <List aria-hidden="true" />
            </Button>
            <Button
              type="button"
              variant="ghost"
              size="icon"
              aria-pressed={view === 'graph'}
              aria-label={t('peerChains.tree.viewGraph')}
              title={t('peerChains.tree.viewGraph')}
              onClick={() => setView('graph')}
              className={view === 'graph' ? 'bg-[var(--color-surface-selected)]' : 'text-[var(--color-foreground-muted)]'}
            >
              <Network aria-hidden="true" />
            </Button>
          </div>
          {chain.state === 'active' && onCancel && (
            <PeerChainStopButton chain={chain} onCancel={onCancel} />
          )}
        </div>
      </div>

      {view === 'graph' ? (
        <Suspense fallback={<p className="text-caption text-[var(--color-foreground-muted)]">{t('peerChains.tree.graphLoading')}</p>}>
          <PeerChainGraph chain={chain} tree={tree} selectedNodeId={selectedNodeId} onSelect={onSelect} />
        </Suspense>
      ) : (
        <ul className="flex flex-col" data-testid="peer-chain-rows">
          <li>
            <button
              type="button"
              aria-pressed={selectedNodeId === null}
              aria-label={`${t('peerChains.tree.showAll')}: ${chain.root.name} · ${t(`peerChains.chainState.${chain.state}`)}`}
              data-testid="peer-chain-root-row"
              onClick={() => onSelect(null)}
              className={`
                flex min-h-[var(--control-sm-height)] w-full min-w-0 flex-wrap items-center gap-x-2 gap-y-0.5 rounded-[var(--radius-sm)] px-2 py-1 text-left
                focus-visible:outline-2 focus-visible:outline-[var(--color-brand)]
                ${selectedNodeId === null ? 'bg-[var(--color-surface-selected)]' : 'hover:bg-[var(--color-surface-hover)]'}
              `}
            >
              <span className="min-w-0 truncate text-caption font-medium text-[var(--color-foreground)]">{chain.root.name}</span>
              <PeerStateBadge state={`chain:${chain.state}`} />
            </button>
            <ul className="flex flex-col">
              {tree.roots.map(entry => renderRow(entry, 1))}
            </ul>
          </li>
        </ul>
      )}
    </section>
  )
}
