import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Background,
  Controls,
  MiniMap,
  ReactFlow,
  ReactFlowProvider,
  getIncomers,
  getOutgoers,
  useNodesState,
  type Edge,
  type Node,
  type NodeTypes,
  type EdgeTypes,
  type NodeMouseHandler,
  type OnNodeDrag,
  useReactFlow,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import './topology.css'
import { useNavigate } from 'react-router-dom'
import { Map, Maximize2, RotateCcw } from 'lucide-react'
import { MachineNode } from './nodes/MachineNode'
import { AgentNode } from './nodes/AgentNode'
import { RoomNode } from './nodes/RoomNode'
import { UserNode } from './nodes/UserNode'
import { ProjectGroup } from './nodes/ProjectGroup'
import { RelationEdge } from './edges/RelationEdge'
import { useLocale } from '@/i18n/LocaleProvider'

const nodeTypes: NodeTypes = {
  machine: MachineNode,
  agent: AgentNode,
  room: RoomNode,
  user: UserNode,
  project: ProjectGroup,
}

const edgeTypes: EdgeTypes = {
  relation: RelationEdge,
}

interface CanvasProps {
  nodes: Node[]
  edges: Edge[]
  onSelect: (node: Node | null) => void
  selectedId: string | null
  /** Persist a node's new position after the user drops it (#234). */
  onPositionChange?: (nodeId: string, pos: { x: number; y: number }) => void
  /** Clear all position overrides (#234). Only called by the Reset button. */
  onResetLayout?: () => void
  /** Drives the Reset button's enabled state (#234). */
  hasOverrides?: boolean
}

/**
 * React Flow canvas with hover-focus dimming, selection, and
 * navigation on double-click for Room nodes.
 *
 * Wrapped in ``ReactFlowProvider`` inside the default export so
 * ``useReactFlow`` hooks work for the ``Fit view`` button.
 *
 * Drag handling (#234): ``useNodesState`` keeps a local copy of the
 * node list so React Flow's drag changes can take effect immediately.
 * Incoming ``props.nodes`` (post dagre + override merge) are resynced
 * via ``useEffect`` — but never mid-drag, which would tear the node
 * out from under the user's cursor when a 5s poll lands.
 */
function CanvasInner({
  nodes,
  edges,
  onSelect,
  selectedId,
  onPositionChange,
  onResetLayout,
  hasOverrides,
}: CanvasProps) {
  const { t } = useLocale()
  const navigate = useNavigate()
  const rf = useReactFlow()
  const [hoverId, setHoverId] = useState<string | null>(null)
  const [miniMapOn, setMiniMapOn] = useState(false)
  const [compact, setCompact] = useState(() => window.innerWidth < 768)

  useEffect(() => {
    const media = window.matchMedia('(max-width: 767px)')
    const onChange = () => setCompact(media.matches)
    media.addEventListener('change', onChange)
    return () => media.removeEventListener('change', onChange)
  }, [])

  const initialFit = useMemo(() => ({ padding: 0.2, minZoom: compact ? 0.65 : 0.3 }), [compact])

  useEffect(() => {
    requestAnimationFrame(() => void rf.fitView(initialFit))
  }, [compact, rf, initialFit])

  // Local React-Flow-friendly node state. ``useNodesState`` hands us
  // the ``applyNodeChanges`` plumbing for free, which we want so
  // position/selection/dimension changes land in local state the way
  // React Flow expects.
  const [localNodes, setLocalNodes, onLocalNodesChange] = useNodesState<Node>(nodes)

  // Guard the resync effect: if we overwrite localNodes while the user
  // is actively dragging, their cursor-attached node snaps back to the
  // props-supplied dagre position and the drag dies.
  const isDraggingRef = useRef(false)

  useEffect(() => {
    if (isDraggingRef.current) return
    setLocalNodes(nodes)
  }, [nodes, setLocalNodes])

  const { neighborNodes, neighborEdges } = useMemo(() => {
    if (!hoverId) return { neighborNodes: new Set<string>(), neighborEdges: new Set<string>() }
    const node = localNodes.find(n => n.id === hoverId)
    if (!node) {
      return { neighborNodes: new Set<string>(), neighborEdges: new Set<string>() }
    }
    const incoming = getIncomers(node, localNodes, edges)
    const outgoing = getOutgoers(node, localNodes, edges)
    const n = new Set<string>([node.id, ...incoming.map(x => x.id), ...outgoing.map(x => x.id)])
    const e = new Set<string>(
      edges.filter(x => x.source === hoverId || x.target === hoverId).map(x => x.id),
    )
    return { neighborNodes: n, neighborEdges: e }
  }, [hoverId, localNodes, edges])

  // Dim via CSS class + preserve object identity for unchanged items.
  // React Flow treats Node/Edge objects by reference internally; keeping
  // the same reference when nothing changed skips per-item diff work and
  // avoids re-rendering memoised node components on every hover move.
  const displayNodes = useMemo<Node[]>(() => {
    return localNodes.map(n => {
      const nextClass =
        hoverId !== null && !neighborNodes.has(n.id) ? 'is-dimmed' : undefined
      if (n.className === nextClass) return n
      return { ...n, className: nextClass }
    })
  }, [localNodes, hoverId, neighborNodes])

  const displayEdges = useMemo<Edge[]>(() => {
    return edges.map(e => {
      const nextClass =
        hoverId !== null && !neighborEdges.has(e.id) ? 'is-dimmed' : undefined
      if (e.className === nextClass) return e
      return { ...e, className: nextClass }
    })
  }, [edges, hoverId, neighborEdges])

  const onMouseEnter: NodeMouseHandler = useCallback((_e, node) => setHoverId(node.id), [])
  const onMouseLeave: NodeMouseHandler = useCallback(() => setHoverId(null), [])
  const onClick: NodeMouseHandler = useCallback(
    (_e, node) => {
      onSelect(node)
    },
    [onSelect],
  )
  const onDblClick: NodeMouseHandler = useCallback(
    (_e, node) => {
      if (node.type === 'room') {
        const rawId = node.id.startsWith('r_') ? node.id.slice(2) : node.id
        navigate(`/rooms/${rawId}`)
      }
    },
    [navigate],
  )
  const onPaneClick = useCallback(() => onSelect(null), [onSelect])

  const onNodeDragStart: OnNodeDrag = useCallback(() => {
    isDraggingRef.current = true
  }, [])

  const onNodeDragStop: OnNodeDrag = useCallback(
    (_e, node) => {
      isDraggingRef.current = false
      onPositionChange?.(node.id, { x: node.position.x, y: node.position.y })
    },
    [onPositionChange],
  )

  const onResetClick = useCallback(() => {
    if (!onResetLayout) return
    onResetLayout()
    // Let React flush the reset, then refit so the user immediately
    // sees the restored dagre layout.
    requestAnimationFrame(() => rf.fitView({ ...initialFit, duration: 280 }))
  }, [onResetLayout, rf, initialFit])

  const onKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLDivElement>) => {
      if (e.key === 'Escape') {
        setHoverId(null)
        onSelect(null)
      }
    },
    [onSelect],
  )

  // Only replace the nodes whose selected flag actually flipped so the
  // rest keep their references from `displayNodes` and skip re-render.
  const withSelected = useMemo<Node[]>(
    () =>
      displayNodes.map(n => {
        const nextSelected = n.id === selectedId
        if ((n.selected ?? false) === nextSelected) return n
        return { ...n, selected: nextSelected }
      }),
    [displayNodes, selectedId],
  )

  return (
    <div
      className="topology-root"
      style={{ width: '100%', height: '100%', position: 'relative' }}
      role="application"
      onKeyDown={onKeyDown}
      tabIndex={-1}
    >
      <ReactFlow
        nodes={withSelected}
        edges={displayEdges}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        fitView
        fitViewOptions={initialFit}
        minZoom={0.3}
        maxZoom={2}
        onlyRenderVisibleElements
        proOptions={{ hideAttribution: true }}
        onNodesChange={onLocalNodesChange}
        onNodeDragStart={onNodeDragStart}
        onNodeDragStop={onNodeDragStop}
        onNodeMouseEnter={onMouseEnter}
        onNodeMouseLeave={onMouseLeave}
        onNodeClick={onClick}
        onNodeDoubleClick={onDblClick}
        onPaneClick={onPaneClick}
        defaultEdgeOptions={{ type: 'relation' }}
      >
        <Background color="var(--color-border-subtle)" gap={16} size={1} />
        <Controls
          showInteractive={false}
          style={{
            borderRadius: 8,
            border: '1px solid var(--color-border)',
            background: 'var(--color-surface-elevated)',
            boxShadow: '0 1px 2px rgba(0,0,0,0.04)',
          }}
        />
        {miniMapOn && (
          <MiniMap
            pannable
            zoomable
            nodeStrokeColor="var(--color-brand-text)"
            maskColor="var(--color-topology-mask)"
            style={{
              borderRadius: 8,
              border: '1px solid var(--color-border)',
              background: 'var(--color-surface-elevated)',
            }}
          />
        )}
      </ReactFlow>

      {!miniMapOn && (
        <div className="pointer-events-none absolute bottom-3 left-1/2 z-10 -translate-x-1/2 rounded-full border border-[var(--color-border)] bg-[var(--color-surface-elevated)]/90 px-3 py-1.5 text-center text-xs text-[var(--color-foreground-muted)] shadow-card md:hidden">
          {t('topology.panHint')}
        </div>
      )}

      {/* Top-right floating actions: reset layout + toggle minimap + fit view */}
      <div
        style={{
          position: 'absolute',
          top: 12,
          right: 12,
          display: 'flex',
          gap: 6,
          zIndex: 5,
        }}
      >
        {onResetLayout && (
          <button
            type="button"
            aria-label={t('topology.resetLayout')}
            title={
              hasOverrides
                ? t('topology.resetLayoutAvailable')
                : t('topology.noPositions')
            }
            onClick={onResetClick}
            disabled={!hasOverrides}
            style={{
              width: 44,
              height: 44,
              borderRadius: 8,
              border: '1px solid var(--color-border)',
              background: 'var(--color-surface-elevated)',
              color: hasOverrides ? 'var(--color-foreground)' : 'var(--color-foreground-subtle)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              boxShadow: '0 1px 2px rgba(0,0,0,0.04)',
              cursor: hasOverrides ? 'pointer' : 'default',
            }}
          >
            <RotateCcw size={16} strokeWidth={1.75} />
          </button>
        )}
        <button
          type="button"
          aria-label={t('topology.toggleMinimap')}
          title={t('topology.toggleMinimap')}
          onClick={() => setMiniMapOn(v => !v)}
          style={{
            width: 44,
            height: 44,
            borderRadius: 8,
            border: '1px solid var(--color-border)',
            background: miniMapOn ? 'var(--color-brand-tint-bg)' : 'var(--color-surface-elevated)',
            color: miniMapOn ? 'var(--color-brand-tint-text)' : 'var(--color-foreground)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            boxShadow: '0 1px 2px rgba(0,0,0,0.04)',
            cursor: 'pointer',
          }}
        >
          <Map size={16} strokeWidth={1.75} />
        </button>
        <button
          type="button"
          aria-label={t('topology.fitView')}
          title={t('topology.fitView')}
          onClick={() => rf.fitView({ padding: 0.2, duration: 280 })}
          style={{
            width: 44,
            height: 44,
            borderRadius: 8,
            border: '1px solid var(--color-border)',
            background: 'var(--color-surface-elevated)',
            color: 'var(--color-foreground)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            boxShadow: '0 1px 2px rgba(0,0,0,0.04)',
            cursor: 'pointer',
          }}
        >
          <Maximize2 size={16} strokeWidth={1.75} />
        </button>
      </div>
    </div>
  )
}

export default function TopologyCanvas(props: CanvasProps) {
  return (
    <ReactFlowProvider>
      <CanvasInner {...props} />
    </ReactFlowProvider>
  )
}
