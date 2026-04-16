import { useCallback, useMemo, useState } from 'react'
import {
  Background,
  Controls,
  MiniMap,
  ReactFlow,
  ReactFlowProvider,
  getIncomers,
  getOutgoers,
  type Edge,
  type Node,
  type NodeTypes,
  type EdgeTypes,
  type NodeMouseHandler,
  useReactFlow,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { useNavigate } from 'react-router-dom'
import { Map, Maximize2 } from 'lucide-react'
import { MachineNode } from './nodes/MachineNode'
import { AgentNode } from './nodes/AgentNode'
import { RoomNode } from './nodes/RoomNode'
import { UserNode } from './nodes/UserNode'
import { ProjectGroup } from './nodes/ProjectGroup'
import { RelationEdge } from './edges/RelationEdge'

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

const DIMMED_OPACITY = 0.18

interface CanvasProps {
  nodes: Node[]
  edges: Edge[]
  onSelect: (node: Node | null) => void
  selectedId: string | null
}

/**
 * React Flow canvas with hover-focus dimming, selection, and
 * navigation on double-click for Room nodes.
 *
 * Wrapped in ``ReactFlowProvider`` inside the default export so
 * ``useReactFlow`` hooks work for the ``Fit view`` button.
 */
function CanvasInner({ nodes, edges, onSelect, selectedId }: CanvasProps) {
  const navigate = useNavigate()
  const rf = useReactFlow()
  const [hoverId, setHoverId] = useState<string | null>(null)
  const [miniMapOn, setMiniMapOn] = useState(false)

  const { neighborNodes, neighborEdges } = useMemo(() => {
    if (!hoverId) return { neighborNodes: new Set<string>(), neighborEdges: new Set<string>() }
    const node = nodes.find(n => n.id === hoverId)
    if (!node) {
      return { neighborNodes: new Set<string>(), neighborEdges: new Set<string>() }
    }
    const incoming = getIncomers(node, nodes, edges)
    const outgoing = getOutgoers(node, nodes, edges)
    const n = new Set<string>([node.id, ...incoming.map(x => x.id), ...outgoing.map(x => x.id)])
    const e = new Set<string>(
      edges.filter(x => x.source === hoverId || x.target === hoverId).map(x => x.id),
    )
    return { neighborNodes: n, neighborEdges: e }
  }, [hoverId, nodes, edges])

  const displayNodes = useMemo<Node[]>(() => {
    if (!hoverId) return nodes
    return nodes.map(n => ({
      ...n,
      style: {
        ...(n.style ?? {}),
        opacity: neighborNodes.has(n.id) ? 1 : DIMMED_OPACITY,
      },
    }))
  }, [nodes, hoverId, neighborNodes])

  const displayEdges = useMemo<Edge[]>(() => {
    if (!hoverId) return edges
    return edges.map(e => ({
      ...e,
      style: {
        ...(e.style ?? {}),
        opacity: neighborEdges.has(e.id) ? 1 : DIMMED_OPACITY,
      },
    }))
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
  const onKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLDivElement>) => {
      if (e.key === 'Escape') {
        setHoverId(null)
        onSelect(null)
      }
    },
    [onSelect],
  )

  const withSelected = useMemo<Node[]>(
    () => displayNodes.map(n => ({ ...n, selected: n.id === selectedId })),
    [displayNodes, selectedId],
  )

  return (
    <div
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
        minZoom={0.3}
        maxZoom={2}
        onlyRenderVisibleElements
        proOptions={{ hideAttribution: true }}
        onNodeMouseEnter={onMouseEnter}
        onNodeMouseLeave={onMouseLeave}
        onNodeClick={onClick}
        onNodeDoubleClick={onDblClick}
        onPaneClick={onPaneClick}
        defaultEdgeOptions={{ type: 'relation' }}
      >
        <Background color="rgba(0,0,0,0.04)" gap={16} size={1} />
        <Controls
          showInteractive={false}
          style={{
            borderRadius: 8,
            border: '1px solid rgba(0,0,0,0.08)',
            background: '#ffffff',
            boxShadow: '0 1px 2px rgba(0,0,0,0.04)',
          }}
        />
        {miniMapOn && (
          <MiniMap
            pannable
            zoomable
            nodeStrokeColor="#0075de"
            maskColor="rgba(0,0,0,0.04)"
            style={{
              borderRadius: 8,
              border: '1px solid rgba(0,0,0,0.08)',
              background: '#ffffff',
            }}
          />
        )}
      </ReactFlow>

      {/* Top-right floating actions: toggle minimap + fit view */}
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
        <button
          type="button"
          aria-label="Toggle minimap"
          title="Toggle minimap"
          onClick={() => setMiniMapOn(v => !v)}
          style={{
            width: 32,
            height: 32,
            borderRadius: 8,
            border: '1px solid rgba(0,0,0,0.08)',
            background: miniMapOn ? '#f2f9ff' : '#ffffff',
            color: miniMapOn ? '#097fe8' : 'rgba(0,0,0,0.95)',
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
          aria-label="Fit view"
          title="Fit view"
          onClick={() => rf.fitView({ padding: 0.2, duration: 280 })}
          style={{
            width: 32,
            height: 32,
            borderRadius: 8,
            border: '1px solid rgba(0,0,0,0.08)',
            background: '#ffffff',
            color: 'rgba(0,0,0,0.95)',
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
