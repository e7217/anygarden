import { memo, useCallback, useEffect, useMemo, useRef } from 'react'
import dagre from 'dagre'
import {
  BaseEdge, Controls, Background, Handle, MarkerType, Position, ReactFlow, ReactFlowProvider,
  applyNodeChanges, getBezierPath, useConnection, useNodesInitialized, useNodesState,
  useReactFlow, useStore, useUpdateNodeInternals,
  type Connection, type Edge, type EdgeChange, type EdgeProps, type EdgeTypes,
  type Node, type NodeChange, type NodeProps, type NodeTypes,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import './agentLinks.css'
import { LayoutGrid, Maximize2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { useLocale } from '@/i18n/LocaleProvider'
import { useAgentLinkView } from '@/hooks/useAgentLinkView'
import { agentNodeSize, reachableFrom, type AgentLink, type LinkAgent } from '@/lib/agentLinks'

export type LinkSelection = { kind: 'agent'; id: string } | { kind: 'link'; id: string } | null
interface Props {
  agents: LinkAgent[]
  links: AgentLink[]
  userId: string | null
  selection: LinkSelection
  onSelect: (selection: LinkSelection) => void
  onConnect: (fromId: string, toId: string) => void
}
type Emphasis = 'reach' | 'dim' | 'none'
interface AgentNodeData extends Record<string, unknown> {
  name: string; emphasis: Emphasis; width: number; height: number
}
function viewportDuration() { return window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ? 0 : 220 }
const LinkAgentNode = memo(function LinkAgentNode({ id, data, selected }: NodeProps<Node<AgentNodeData>>) {
  const connecting = useConnection(connection => connection.inProgress)
  const updateInternals = useUpdateNodeInternals()
  // React Flow must remeasure handles as a link changes the source's size.
  useEffect(() => { updateInternals(id) }, [id, data.width, data.height, updateInternals])
  return <div className={`agent-link-node${selected ? ' is-selected' : data.emphasis === 'reach' ? ' is-reachable' : ''}`} title={data.name}>
    <span className="agent-link-node__name">{data.name}</span>
    <Handle type="target" position={Position.Left} isConnectableStart={false} className={`agent-link-node__target${connecting ? ' is-active' : ''}`} />
    <Handle type="source" position={Position.Right} className="agent-link-node__source"><span aria-hidden="true">+</span></Handle>
  </div>
})
const LinkEdge = memo(function LinkEdge({ id, sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, markerEnd, style }: EdgeProps) {
  const [bezier] = getBezierPath({ sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition })
  // A return connection uses a separate lane, keeping both arrows readable.
  const y = Math.max(sourceY, targetY) + 64
  const path = sourceX > targetX
    ? `M ${sourceX} ${sourceY} C ${sourceX + 48} ${sourceY}, ${sourceX + 48} ${y}, ${sourceX} ${y} L ${targetX - 24} ${y} C ${targetX - 48} ${y}, ${targetX - 48} ${targetY}, ${targetX} ${targetY}`
    : bezier
  return <BaseEdge id={id} path={path} markerEnd={markerEnd} style={style} interactionWidth={28} />
})
const nodeTypes: NodeTypes = { agent: LinkAgentNode }
const edgeTypes: EdgeTypes = { link: LinkEdge }

function layoutPositions(agents: LinkAgent[], links: AgentLink[], counts: Map<string, number>) {
  const graph = new dagre.graphlib.Graph()
  graph.setGraph({ rankdir: 'LR', nodesep: 48, ranksep: 96, marginx: 32, marginy: 32 })
  graph.setDefaultEdgeLabel(() => ({}))
  for (const a of agents) graph.setNode(a.id, agentNodeSize(counts.get(a.id) ?? 0))
  for (const l of links) if (graph.hasNode(l.from_agent_id) && graph.hasNode(l.to_agent_id)) graph.setEdge(l.from_agent_id, l.to_agent_id)
  dagre.layout(graph)
  return new Map(agents.map(a => {
    const size = agentNodeSize(counts.get(a.id) ?? 0), box = graph.node(a.id)
    return [a.id, { x: box.x - size.width / 2, y: box.y - size.height / 2 }]
  }))
}
function CanvasInner({ agents, links, userId, selection, onSelect, onConnect }: Props) {
  const { t } = useLocale()
  const rf = useReactFlow()
  const { view, savePosition, saveViewport, reset } = useAgentLinkView(userId)
  const initialView = useRef(view)
  const initialized = useNodesInitialized()
  const restored = useRef(false)
  const canvasHeight = useStore(state => state.height)
  const previousHeight = useRef(0)
  const selectionRef = useRef(selection)
  selectionRef.current = selection
  const select = useCallback((next: LinkSelection) => { selectionRef.current = next; onSelect(next) }, [onSelect])
  const counts = useMemo(() => {
    const ids = new Set(agents.map(a => a.id)), targets = new Map<string, Set<string>>()
    for (const l of links) if (ids.has(l.from_agent_id) && ids.has(l.to_agent_id)) {
      const set = targets.get(l.from_agent_id) ?? new Set<string>()
      set.add(l.to_agent_id); targets.set(l.from_agent_id, set)
    }
    return new Map([...targets].map(([id, set]) => [id, set.size]))
  }, [agents, links])
  const positions = useMemo(() => layoutPositions(agents, links, counts), [agents, links, counts])
  const [localNodes, setLocalNodes] = useNodesState<Node<AgentNodeData>>([])
  useEffect(() => {
    setLocalNodes(previous => {
      const old = new Map(previous.map(node => [node.id, node]))
      return agents.map(a => {
        const existing = old.get(a.id), size = agentNodeSize(counts.get(a.id) ?? 0)
        return { ...existing, id: a.id, type: 'agent', position: existing?.position ?? initialView.current.positions[a.id] ?? positions.get(a.id)!,
          style: size, data: { name: a.name, ...size, emphasis: 'none' as Emphasis } }
      })
    })
  }, [agents, counts, positions, setLocalNodes])
  useEffect(() => {
    if (!initialized || restored.current) return
    restored.current = true
    if (initialView.current.viewport) void rf.setViewport(initialView.current.viewport)
    else void rf.fitView({ padding: 0.25, maxZoom: 1 })
  }, [initialized, rf])
  useEffect(() => {
    const previous = previousHeight.current
    previousHeight.current = canvasHeight
    // A narrow-screen details panel resizes the map instead of hiding its nodes.
    // Initial height must not overwrite a restored user viewport.
    if (previous > 0 && previous !== canvasHeight && restored.current && window.matchMedia('(max-width: 1023px)').matches) {
      void rf.fitView({ padding: 0.25, maxZoom: 1 })
    }
  }, [canvasHeight, rf])
  const reach = useMemo(() => selection?.kind === 'agent' ? reachableFrom(selection.id, links) : null, [selection, links])
  const names = useMemo(() => new Map(agents.map(a => [a.id, a.name])), [agents])
  const nodes = useMemo(() => localNodes.map(node => {
    const emphasis: Emphasis = !reach ? 'none' : reach.agents.has(node.id) ? 'reach' : 'dim'
    return { ...node, selected: selection?.kind === 'agent' && selection.id === node.id,
      className: emphasis === 'dim' ? 'is-dimmed' : undefined,
      ariaLabel: t('agentLinks.agentNode', { name: node.data.name }), data: { ...node.data, emphasis } }
  }), [localNodes, selection, reach, t])
  const edges = useMemo<Edge[]>(() => links.map(l => {
    const emphasized = selection?.kind === 'link' && selection.id === l.id || !!reach?.links.has(l.id)
    const color = emphasized ? 'var(--color-brand-text)' : 'var(--color-foreground-subtle)'
    return { id: l.id, source: l.from_agent_id, target: l.to_agent_id, type: 'link',
      selected: selection?.kind === 'link' && selection.id === l.id, focusable: true,
      ariaLabel: t('agentLinks.linkLabel', { from: names.get(l.from_agent_id) ?? '?', to: names.get(l.to_agent_id) ?? '?' }),
      className: reach && !reach.links.has(l.id) ? 'is-dimmed' : undefined,
      markerEnd: { type: MarkerType.ArrowClosed, color, width: 18, height: 18 }, style: { stroke: color, strokeWidth: emphasized ? 2 : 1.5 } }
  }), [links, selection, reach, names, t])
  const onNodesChange = useCallback((changes: NodeChange<Node<AgentNodeData>>[]) => {
    const rest: NodeChange<Node<AgentNodeData>>[] = []
    for (const change of changes) {
      if (change.type !== 'select') rest.push(change)
      else if (change.selected) select({ kind: 'agent', id: change.id })
      else if (selectionRef.current?.kind === 'agent' && selectionRef.current.id === change.id) select(null)
    }
    if (rest.length) setLocalNodes(nodes => applyNodeChanges(rest, nodes))
  }, [select, setLocalNodes])
  const onEdgesChange = useCallback((changes: EdgeChange[]) => {
    for (const change of changes) if (change.type === 'select') {
      if (change.selected) select({ kind: 'link', id: change.id })
      else if (selectionRef.current?.kind === 'link' && selectionRef.current.id === change.id) select(null)
    }
  }, [select])
  const isValidConnection = useCallback((connection: Connection | Edge) => connection.source !== connection.target && !links.some(l => l.from_agent_id === connection.source && l.to_agent_id === connection.target), [links])
  const rearrange = useCallback(() => {
    reset()
    setLocalNodes(nodes => nodes.map(node => ({ ...node, position: positions.get(node.id) ?? node.position })))
    for (const [id, pos] of positions) savePosition(id, pos)
    requestAnimationFrame(() => { void rf.fitView({ padding: 0.25, maxZoom: 1, duration: viewportDuration() }) })
  }, [reset, positions, savePosition, rf, setLocalNodes])
  return <div className="agent-links-root relative h-full w-full" role="application" aria-label={t('agentLinks.canvasLabel')} onKeyDown={e => { if (e.key === 'Escape') select(null) }}>
    <ReactFlow nodes={nodes} edges={edges} nodeTypes={nodeTypes} edgeTypes={edgeTypes}
      onNodesChange={onNodesChange} onEdgesChange={onEdgesChange}
      onConnect={c => { if (c.source && c.target) onConnect(c.source, c.target) }} isValidConnection={isValidConnection}
      onNodeDragStop={(_, node) => savePosition(node.id, node.position)}
      onMoveEnd={(_, viewport) => { if (restored.current) saveViewport(viewport) }}
      onPaneClick={() => select(null)} deleteKeyCode={null} connectionRadius={32}
      minZoom={0.3} maxZoom={2} proOptions={{ hideAttribution: true }}>
      <Background color="var(--color-border-subtle)" gap={16} size={1} />
      <Controls showInteractive={false} showFitView={false} />
    </ReactFlow>
    <div className="absolute right-3 top-3 z-10 flex gap-2">
      <Button variant="outline" size="icon" onClick={rearrange} aria-label={t('agentLinks.rearrange')} title={t('agentLinks.rearrange')}><LayoutGrid /></Button>
      <Button variant="outline" size="icon" onClick={() => void rf.fitView({ padding: 0.25, maxZoom: 1, duration: viewportDuration() })} aria-label={t('agentLinks.fitView')} title={t('agentLinks.fitView')}><Maximize2 /></Button>
    </div>
  </div>
}
export default function AgentLinksCanvas(props: Props) {
  return <ReactFlowProvider><CanvasInner {...props} /></ReactFlowProvider>
}
