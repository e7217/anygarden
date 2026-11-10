import { memo, useMemo } from 'react'
import { Handle, Position, ReactFlow, ReactFlowProvider, type Edge, type Node, type NodeProps } from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import PeerStateBadge from '@/components/peer-chains/PeerStateBadge'
import { CHAIN_NODE_SIZE, layoutChainGraph, type ChainGraphNode } from '@/lib/peerChainGraph'
import type { ChainTree, PeerChain } from '@/lib/peerChains'

type ChainNodeData = ChainGraphNode & { selected: boolean }

const ChainNode = memo(function ChainNode({ data }: NodeProps) {
  const node = data as unknown as ChainNodeData
  return (
    <div
      style={{ width: CHAIN_NODE_SIZE.width, height: CHAIN_NODE_SIZE.height }}
      className={`
        flex flex-col justify-center gap-0.5 rounded-[var(--radius-md)] border px-3
        ${node.selected
          ? 'border-[var(--color-brand)] bg-[var(--color-surface-selected)]'
          : 'border-[var(--color-border-strong)] bg-[var(--color-surface-elevated)]'}
      `}
    >
      <Handle type="target" position={Position.Top} isConnectable={false} style={{ opacity: 0 }} />
      <span className="truncate text-caption font-medium text-[var(--color-foreground)]">{node.name}</span>
      <PeerStateBadge state={node.state} />
      <Handle type="source" position={Position.Bottom} isConnectable={false} style={{ opacity: 0 }} />
    </div>
  )
})

const nodeTypes = { chain: ChainNode }

interface PeerChainGraphProps {
  chain: PeerChain
  tree: ChainTree
  selectedNodeId: string | null
  onSelect: (nodeId: string | null) => void
}

/**
 * #1006 — the call tree as a graph. Read-only: nodes don't drag or
 * connect; clicking one filters the thread like a list row does.
 * Lazy-loaded by ``PeerChainTree`` so React Flow stays out of the chat
 * bundle until someone opens the graph.
 */
export default function PeerChainGraph({ chain, tree, selectedNodeId, onSelect }: PeerChainGraphProps) {
  const layout = useMemo(() => layoutChainGraph(chain, tree), [chain, tree])
  const nodes = useMemo<Node[]>(
    () => layout.nodes.map(node => ({
      id: node.id,
      type: 'chain',
      position: { x: node.x, y: node.y },
      data: { ...node, selected: node.nodeId !== null && node.nodeId === selectedNodeId },
      draggable: false,
      connectable: false,
      // Clicking a node is the filter action, announced by its row in the list view.
      ariaLabel: node.name,
    })),
    [layout, selectedNodeId],
  )
  const edges = useMemo<Edge[]>(
    () => layout.edges.map(edge => ({ ...edge, type: 'smoothstep', style: { stroke: 'var(--color-border-strong)' } })),
    [layout],
  )

  return (
    <div data-testid="peer-chain-graph" className="h-72 w-full overflow-hidden rounded-[var(--radius-md)] border border-[var(--color-border)] bg-[var(--color-surface-alt)]">
      <ReactFlowProvider>
        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={nodeTypes}
          fitView
          fitViewOptions={{ padding: 0.15, maxZoom: 1 }}
          minZoom={0.2}
          maxZoom={1.5}
          nodesDraggable={false}
          nodesConnectable={false}
          elementsSelectable={false}
          proOptions={{ hideAttribution: true }}
          onNodeClick={(_, node) => onSelect((node.data as unknown as ChainNodeData).nodeId)}
          onPaneClick={() => onSelect(null)}
        />
      </ReactFlowProvider>
    </div>
  )
}
