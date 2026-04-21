import { useMemo } from 'react'
import dagre from 'dagre'
import type { Edge as RFEdge, Node as RFNode } from '@xyflow/react'
import type { GraphEdge, GraphNode, NodeKind } from './types'
import type { Overrides } from './useTopologyLayoutOverrides'

// Canvas size per node kind. Dagre uses these to compute layout boxes
// before React Flow renders the DOM. The exact rendered sizes can
// differ (and drift as DESIGN.md evolves) — these are "good enough"
// bounding boxes, not pixel-perfect measurements. See DESIGN.md §5
// (8px base unit) for spacing rationale.
const NODE_SIZE: Record<NodeKind, { width: number; height: number }> = {
  user: { width: 56, height: 56 },
  machine: { width: 136, height: 56 },
  agent: { width: 140, height: 44 },
  room: { width: 160, height: 32 },
  project: { width: 320, height: 160 },
}

// Hierarchical rank so dagre produces a predictable top-down layout.
// Lower rank number = higher row on the canvas.
const RANK_BY_KIND: Record<NodeKind, number> = {
  user: 0,
  machine: 1,
  agent: 2,
  room: 3,
  project: 99, // special: rendered as a container, no dagre ranking
}

interface Layouted {
  nodes: RFNode[]
  edges: RFEdge[]
}

/**
 * Wrap dagre as a memoized, pure function.
 *
 * ``useMemo`` is keyed on a lightweight digest of the inputs so the
 * dagre run — which is O(V + E) but allocates heavily — only happens
 * when the server delivers a genuinely new graph. Filter toggles or
 * hover state must NOT invalidate this cache; they operate on the
 * already-positioned output further down the render tree.
 *
 * ``overrides`` (#234) is a per-node position map that is overlaid on
 * the dagre result in a separate memo keyed on its reference. This
 * way dragging a node doesn't invalidate the expensive dagre pass —
 * only the cheap ``.map`` that applies the overrides reruns.
 */
export function useGraphLayout(
  graphNodes: GraphNode[] | undefined,
  graphEdges: GraphEdge[] | undefined,
  overrides?: Overrides,
): Layouted {
  // Stable hash of inputs. ``id``s are the only values the layout
  // depends on — labels, data.status etc. can churn without moving
  // a single node. Keep the key cheap.
  const digest = useMemo(() => {
    if (!graphNodes || !graphEdges) return ''
    const nids = graphNodes
      .map(n => `${n.kind}:${n.id}`)
      .sort()
      .join('|')
    const eids = graphEdges
      .map(e => `${e.kind}:${e.source}->${e.target}`)
      .sort()
      .join('|')
    return `${nids}#${eids}`
  }, [graphNodes, graphEdges])

  const dagreLayouted = useMemo<Layouted>(() => {
    if (!graphNodes || !graphEdges || graphNodes.length === 0) {
      return { nodes: [], edges: [] }
    }

    const g = new dagre.graphlib.Graph()
    g.setGraph({
      rankdir: 'TB',
      nodesep: 48,
      ranksep: 96,
      ranker: 'tight-tree',
      marginx: 32,
      marginy: 32,
    })
    g.setDefaultEdgeLabel(() => ({}))

    // Only rank "primary" kinds. Project nodes are rendered as groups
    // (React Flow parent nodes) and should not participate in dagre
    // layout.
    const rankable = graphNodes.filter(n => n.kind !== 'project')
    for (const n of rankable) {
      const { width, height } = NODE_SIZE[n.kind]
      g.setNode(n.id, {
        width,
        height,
        rank: RANK_BY_KIND[n.kind],
      })
    }

    for (const e of graphEdges) {
      if (g.hasNode(e.source) && g.hasNode(e.target)) {
        g.setEdge(e.source, e.target)
      }
    }

    dagre.layout(g)

    const rfNodes: RFNode[] = graphNodes.map(n => {
      if (n.kind === 'project') {
        // Project groups float off to the side as optional overlays
        // in v1. Position them at origin and let the caller move them
        // if needed — we deliberately don't cluster rooms inside
        // project containers yet (that is v2 scope).
        return {
          id: n.id,
          type: 'project',
          position: { x: 0, y: 0 },
          data: { kind: n.kind, label: n.label, ...n.data },
          hidden: true, // hidden by default in v1
        }
      }
      const pos = g.node(n.id)
      return {
        id: n.id,
        type: n.kind,
        position: {
          x: pos ? pos.x - NODE_SIZE[n.kind].width / 2 : 0,
          y: pos ? pos.y - NODE_SIZE[n.kind].height / 2 : 0,
        },
        data: { kind: n.kind, label: n.label, ...n.data },
      }
    })

    const rfEdges: RFEdge[] = graphEdges.map(e => ({
      id: e.id,
      source: e.source,
      target: e.target,
      type: 'relation',
      data: { kind: e.kind, ...(e.data ?? {}) },
    }))

    return { nodes: rfNodes, edges: rfEdges }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [digest])

  // Overlay user-edited positions on top of dagre. Skipping the overlay
  // entirely when ``overrides`` is missing/empty keeps reference identity
  // of ``rfNodes`` stable (no unnecessary allocations for users who
  // haven't dragged anything).
  return useMemo<Layouted>(() => {
    if (!overrides || Object.keys(overrides).length === 0) {
      return dagreLayouted
    }
    const applied: RFNode[] = dagreLayouted.nodes.map(n => {
      const o = overrides[n.id]
      if (!o) return n
      return { ...n, position: { x: o.x, y: o.y } }
    })
    return { nodes: applied, edges: dagreLayouted.edges }
  }, [dagreLayouted, overrides])
}
