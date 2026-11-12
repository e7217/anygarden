import { lazy, Suspense, useMemo, useState } from 'react'
import { ChevronDown, GitFork, List, Network } from 'lucide-react'
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

/** Branches indent this many levels; deeper rows line up with the last. */
const MAX_TREE_INDENT = 6

export function rowLabel(entry: ChainTreeNode): string {
  return `${entry.node.caller_name} → ${entry.node.target_name}`
}

/**
 * #1024 — what a row shows. The asker is the row above in almost every
 * case, so only the answerer is named; a row whose asker differs (its
 * parent question is missing from a partial payload) keeps the arrow.
 */
export function shortRowLabel(entry: ChainTreeNode, parentName: string): string {
  return entry.node.caller_name === parentName ? entry.node.target_name : rowLabel(entry)
}

/** Empty stand-in for the expand button, so row highlights share one width. */
function ToggleSlot() {
  return <span aria-hidden="true" data-toggle-slot className="size-[var(--control-icon-size)] shrink-0" />
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
  // #1029 — the expand button trails its row so it never pushes labels
  // right. Every row, the root included, reserves that column when any
  // row can fold, and none does otherwise, so highlights stay one width.
  const hasToggles = useMemo(() => [...tree.byId.values()].some(entry => entry.children.length > 0), [tree])
  const [toggled, setToggled] = useState<Map<string, boolean>>(() => new Map())
  const [view, setView] = useState<'list' | 'graph'>('list')

  const isExpanded = (id: string) => toggled.get(id) ?? defaults.has(id)
  const toggle = (id: string) => {
    setToggled(previous => new Map(previous).set(id, !isExpanded(id)))
  }

  // Each open branch hangs under a guide line drawn below its parent's
  // label text, one indent step in, up to MAX_TREE_INDENT.
  const renderBranch = (entries: ChainTreeNode[], level: number, parentName: string) => (
    <ul
      data-testid="peer-chain-branch"
      className={`flex flex-col ${level <= MAX_TREE_INDENT ? 'ms-2 border-s border-[var(--color-border)] ps-2' : ''}`}
    >
      {entries.map(entry => renderRow(entry, level, parentName))}
    </ul>
  )

  const renderRow = (entry: ChainTreeNode, level: number, parentName: string) => {
    const { node } = entry
    const hasChildren = entry.children.length > 0
    const expanded = hasChildren && isExpanded(node.id)
    const selected = selectedNodeId === node.id
    const label = rowLabel(entry)
    return (
      <li key={node.id} data-node-id={node.id}>
        <div className="flex min-w-0 items-center gap-1">
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
            <span data-testid="peer-chain-row-label" className="min-w-0 truncate text-caption text-[var(--color-foreground)]">
              {shortRowLabel(entry, parentName)}
            </span>
            <PeerStateBadge state={node.state} title={node.reason} />
          </button>
          {hasChildren ? (
            <Button
              type="button"
              variant="ghost"
              size="icon"
              data-toggle-slot
              aria-expanded={expanded}
              aria-label={t(expanded ? 'peerChains.tree.collapse' : 'peerChains.tree.expand', { name: node.target_name })}
              onClick={() => toggle(node.id)}
              className="text-[var(--color-foreground-muted)]"
            >
              <ChevronDown aria-hidden="true" className={`transition-transform ${expanded ? 'rotate-180' : ''}`} />
            </Button>
          ) : hasToggles && <ToggleSlot />}
        </div>
        {expanded && renderBranch(entry.children, level + 1, node.target_name)}
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
            <div className="flex min-w-0 items-center gap-1">
              <button
                type="button"
                aria-pressed={selectedNodeId === null}
                aria-label={`${t('peerChains.tree.showAll')}: ${chain.root.name} · ${t(`peerChains.chainState.${chain.state}`)}`}
                data-testid="peer-chain-root-row"
                onClick={() => onSelect(null)}
                className={`
                  flex min-h-[var(--control-sm-height)] min-w-0 flex-1 flex-wrap items-center gap-x-2 gap-y-0.5 rounded-[var(--radius-sm)] px-2 py-1 text-left
                  focus-visible:outline-2 focus-visible:outline-[var(--color-brand)]
                  ${selectedNodeId === null ? 'bg-[var(--color-surface-selected)]' : 'hover:bg-[var(--color-surface-hover)]'}
                `}
              >
                <span className="min-w-0 truncate text-caption font-medium text-[var(--color-foreground)]">{chain.root.name}</span>
                <PeerStateBadge state={`chain:${chain.state}`} />
              </button>
              {hasToggles && <ToggleSlot />}
            </div>
            {renderBranch(tree.roots, 1, chain.root.name)}
          </li>
        </ul>
      )}
    </section>
  )
}
