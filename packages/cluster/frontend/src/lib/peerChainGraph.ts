import dagre from 'dagre'
import type { ChainTree, PeerChain, PeerChainState, PeerNodeState } from '@/lib/peerChains'

/** Box dagre lays out; the node component renders at this size. */
export const CHAIN_NODE_SIZE = { width: 168, height: 52 }

export interface ChainGraphNode {
  id: string
  /** Tree node id, or null for the root agent. */
  nodeId: string | null
  name: string
  state: PeerNodeState | `chain:${PeerChainState}`
  x: number
  y: number
}

export interface ChainGraphEdge {
  id: string
  source: string
  target: string
}

export function rootGraphId(chain: Pick<PeerChain, 'id'>): string {
  return `root:${chain.id}`
}

/**
 * #1006 — top-down positions for a chain's call graph: the root agent on
 * top, each question one rank below the question that caused it. Kept
 * apart from ``peerChains.ts`` so dagre only loads with the graph view.
 */
export function layoutChainGraph(chain: PeerChain, tree: ChainTree): { nodes: ChainGraphNode[]; edges: ChainGraphEdge[] } {
  const g = new dagre.graphlib.Graph()
  g.setGraph({ rankdir: 'TB', nodesep: 24, ranksep: 56, marginx: 16, marginy: 16 })
  g.setDefaultEdgeLabel(() => ({}))

  const rootId = rootGraphId(chain)
  g.setNode(rootId, { ...CHAIN_NODE_SIZE })
  const edges: ChainGraphEdge[] = []
  for (const entry of tree.byId.values()) {
    g.setNode(entry.node.id, { ...CHAIN_NODE_SIZE })
  }
  const link = (source: string, target: string) => {
    g.setEdge(source, target)
    edges.push({ id: `${source}->${target}`, source, target })
  }
  for (const entry of tree.roots) link(rootId, entry.node.id)
  for (const entry of tree.byId.values()) {
    for (const child of entry.children) link(entry.node.id, child.node.id)
  }

  dagre.layout(g)

  const at = (id: string) => {
    const pos = g.node(id)
    return {
      x: pos ? pos.x - CHAIN_NODE_SIZE.width / 2 : 0,
      y: pos ? pos.y - CHAIN_NODE_SIZE.height / 2 : 0,
    }
  }
  const nodes: ChainGraphNode[] = [
    { id: rootId, nodeId: null, name: chain.root.name, state: `chain:${chain.state}`, ...at(rootId) },
    ...Array.from(tree.byId.values()).map(entry => ({
      id: entry.node.id,
      nodeId: entry.node.id,
      name: entry.node.target_name,
      state: entry.node.state,
      ...at(entry.node.id),
    })),
  ]
  return { nodes, edges }
}
