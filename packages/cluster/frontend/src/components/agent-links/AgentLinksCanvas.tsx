import React, { useCallback, useEffect, useMemo, useRef } from 'react'
import dagre from 'dagre'
import {
  BaseEdge,
  Controls,
  Background,
  EdgeLabelRenderer,
  Handle,
  MarkerType,
  Position,
  ReactFlow,
  ReactFlowProvider,
  applyNodeChanges,
  getBezierPath,
  useConnection,
  useNodesState,
  useReactFlow,
  type Connection,
  type Edge,
  type EdgeChange,
  type EdgeProps,
  type EdgeTypes,
  type Node,
  type NodeChange,
  type NodeProps,
  type NodeTypes,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import './agentLinks.css'
import { LayoutGrid, Maximize2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { useLocale } from '@/i18n/LocaleProvider'
import { agentStateColor } from '@/components/topology/constants'
import { reachableFrom, type AgentLink, type LinkAgent } from '@/lib/agentLinks'

export type LinkSelection = { kind: 'agent'; id: string } | { kind: 'link'; id: string } | null

interface Props {
  agents: LinkAgent[]
  links: AgentLink[]
  selection: LinkSelection
  onSelect: (selection: LinkSelection) => void
  /** A connection was dragged from ``fromId`` onto ``toId``. */
  onConnect: (fromId: string, toId: string) => void
}

const NODE_WIDTH = 176
const NODE_HEIGHT = 48

type Emphasis = 'reach' | 'dim' | 'none'

/** Viewport animations are decorative; skip them under reduced motion. */
function viewportDuration(): number {
  return window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ? 0 : 280
}

interface AgentNodeData extends Record<string, unknown> {
  name: string
  state: string
  emphasis: Emphasis
}

interface LinkEdgeData extends Record<string, unknown> {
  whenToAsk: string
  label: string
  emphasis: Emphasis
  onSelect: (id: string) => void
}

/** Agent card with a drag source under it; the whole card accepts drops. */
function LinkAgentNodeInner({ data, selected }: NodeProps<Node<AgentNodeData>>) {
  const connecting = useConnection(connection => connection.inProgress)
  const classes = ['agent-link-node']
  if (selected) classes.push('is-selected')
  else if (data.emphasis === 'reach') classes.push('is-reachable')
  return (
    <div className={classes.join(' ')} title={data.name}>
      <span className="agent-link-node__dot" style={{ background: agentStateColor(data.state) }} aria-hidden="true" />
      <span className="agent-link-node__name">{data.name}</span>
      {/* Always mounted: React Flow ends every arrow on this handle. It
          only grows into a whole-card drop target while a link is dragged. */}
      <Handle
        type="target"
        position={Position.Top}
        isConnectableStart={false}
        className={connecting ? 'agent-link-node__target is-active' : 'agent-link-node__target'}
      />
      <Handle type="source" position={Position.Bottom} className="agent-link-node__source" />
    </div>
  )
}
const LinkAgentNode = React.memo(LinkAgentNodeInner)

/** Directed link with its ``when_to_ask`` as a focusable label. */
function LinkEdgeInner({
  id, sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, data, markerEnd, selected, style,
}: EdgeProps<Edge<LinkEdgeData>>) {
  const [path, labelX, labelY] = getBezierPath({ sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition })
  const classes = ['agent-link-label', 'nodrag', 'nopan']
  if (selected) classes.push('is-selected')
  else if (data?.emphasis === 'reach') classes.push('is-reachable')
  else if (data?.emphasis === 'dim') classes.push('is-dimmed')
  return (
    <>
      <BaseEdge id={id} path={path} markerEnd={markerEnd} style={style} interactionWidth={20} />
      <EdgeLabelRenderer>
        <button
          type="button"
          className={classes.join(' ')}
          style={{ transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)` }}
          title={data?.whenToAsk}
          aria-label={data?.label}
          aria-pressed={selected ? true : false}
          onClick={() => data?.onSelect(id)}
        >
          {data?.whenToAsk}
        </button>
      </EdgeLabelRenderer>
    </>
  )
}
const LinkEdge = React.memo(LinkEdgeInner)

const nodeTypes: NodeTypes = { agent: LinkAgentNode }
const edgeTypes: EdgeTypes = { link: LinkEdge }

/** Top-down dagre positions keyed by agent id. */
function layoutPositions(agents: LinkAgent[], links: AgentLink[]): Map<string, { x: number; y: number }> {
  const g = new dagre.graphlib.Graph()
  g.setGraph({ rankdir: 'TB', nodesep: 64, ranksep: 112, marginx: 32, marginy: 32 })
  g.setDefaultEdgeLabel(() => ({}))
  for (const agent of agents) g.setNode(agent.id, { width: NODE_WIDTH, height: NODE_HEIGHT })
  for (const link of links) {
    if (g.hasNode(link.from_agent_id) && g.hasNode(link.to_agent_id)) g.setEdge(link.from_agent_id, link.to_agent_id)
  }
  dagre.layout(g)
  const positions = new Map<string, { x: number; y: number }>()
  for (const agent of agents) {
    const box = g.node(agent.id)
    positions.set(agent.id, box ? { x: box.x - NODE_WIDTH / 2, y: box.y - NODE_HEIGHT / 2 } : { x: 0, y: 0 })
  }
  return positions
}

/**
 * Relationship map canvas (#1006). Selection is owned by the page:
 * React Flow's ``select`` changes (mouse or Enter on a focused node)
 * are turned into ``onSelect`` calls and the ``selected`` flags are
 * re-derived from the ``selection`` prop. Node positions stay local so
 * a drag or a new link never snaps the user's arrangement back; only
 * new agents take their dagre slot until "Rearrange" is pressed.
 */
function CanvasInner({ agents, links, selection, onSelect, onConnect }: Props) {
  const { t } = useLocale()
  const rf = useReactFlow()
  const selectionRef = useRef(selection)
  selectionRef.current = selection
  const select = useCallback((next: LinkSelection) => {
    selectionRef.current = next
    onSelect(next)
  }, [onSelect])

  const digest = useMemo(
    () => `${agents.map(a => a.id).sort().join('|')}#${links.map(l => `${l.from_agent_id}>${l.to_agent_id}`).sort().join('|')}`,
    [agents, links],
  )
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const positions = useMemo(() => layoutPositions(agents, links), [digest])

  const [localNodes, setLocalNodes] = useNodesState<Node<AgentNodeData>>([])
  useEffect(() => {
    setLocalNodes(previous => {
      const byId = new Map(previous.map(node => [node.id, node]))
      return agents.map(agent => {
        const old = byId.get(agent.id)
        const data = { name: agent.name, state: agent.actual_state, emphasis: 'none' as Emphasis }
        return old
          ? { ...old, data }
          : { id: agent.id, type: 'agent', position: positions.get(agent.id) ?? { x: 0, y: 0 }, data }
      })
    })
  }, [agents, positions, setLocalNodes])

  const reach = useMemo(
    () => (selection?.kind === 'agent' ? reachableFrom(selection.id, links) : null),
    [selection, links],
  )
  const namesById = useMemo(() => new Map(agents.map(agent => [agent.id, agent.name])), [agents])
  const outCount = useMemo(() => {
    const counts = new Map<string, number>()
    for (const link of links) counts.set(link.from_agent_id, (counts.get(link.from_agent_id) ?? 0) + 1)
    return counts
  }, [links])

  const displayNodes = useMemo<Node<AgentNodeData>[]>(() => localNodes.map(node => {
    const emphasis: Emphasis = !reach ? 'none' : reach.agents.has(node.id) ? 'reach' : 'dim'
    return {
      ...node,
      selected: selection?.kind === 'agent' && selection.id === node.id,
      className: emphasis === 'dim' ? 'is-dimmed' : undefined,
      ariaLabel: t('agentLinks.agentNode', { name: node.data.name, count: outCount.get(node.id) ?? 0 }),
      data: node.data.emphasis === emphasis ? node.data : { ...node.data, emphasis },
    }
  }), [localNodes, reach, selection, outCount, t])

  const selectLink = useCallback((id: string) => select({ kind: 'link', id }), [select])
  const edges = useMemo<Edge<LinkEdgeData>[]>(() => links.map(link => {
    const emphasis: Emphasis = !reach ? 'none' : reach.links.has(link.id) ? 'reach' : 'dim'
    const isSelected = selection?.kind === 'link' && selection.id === link.id
    const strong = isSelected || emphasis === 'reach'
    const color = strong ? 'var(--color-brand-text)' : 'var(--color-foreground-subtle)'
    return {
      id: link.id,
      source: link.from_agent_id,
      target: link.to_agent_id,
      type: 'link',
      selected: isSelected,
      // The label button is the keyboard stop for a link; skip the path.
      focusable: false,
      className: emphasis === 'dim' ? 'is-dimmed' : undefined,
      markerEnd: { type: MarkerType.ArrowClosed, color, width: 18, height: 18 },
      style: { stroke: color, strokeWidth: strong ? 2 : 1.5 },
      data: {
        whenToAsk: link.when_to_ask,
        label: t('agentLinks.linkLabel', {
          from: namesById.get(link.from_agent_id) ?? '?',
          to: namesById.get(link.to_agent_id) ?? '?',
          when: link.when_to_ask,
        }),
        emphasis,
        onSelect: selectLink,
      },
    }
  }), [links, reach, selection, namesById, selectLink, t])

  const onNodesChange = useCallback((changes: NodeChange<Node<AgentNodeData>>[]) => {
    const rest: NodeChange<Node<AgentNodeData>>[] = []
    for (const change of changes) {
      if (change.type !== 'select') {
        rest.push(change)
        continue
      }
      const current = selectionRef.current
      if (change.selected) select({ kind: 'agent', id: change.id })
      else if (current?.kind === 'agent' && current.id === change.id) select(null)
    }
    if (rest.length > 0) setLocalNodes(nodes => applyNodeChanges(rest, nodes))
  }, [select, setLocalNodes])

  const onEdgesChange = useCallback((changes: EdgeChange<Edge<LinkEdgeData>>[]) => {
    for (const change of changes) {
      if (change.type !== 'select') continue
      const current = selectionRef.current
      if (change.selected) select({ kind: 'link', id: change.id })
      else if (current?.kind === 'link' && current.id === change.id) select(null)
    }
  }, [select])

  const isValidConnection = useCallback((connection: Connection | Edge) => (
    connection.source !== connection.target
    && !links.some(link => link.from_agent_id === connection.source && link.to_agent_id === connection.target)
  ), [links])

  const handleConnect = useCallback((connection: Connection) => {
    if (connection.source && connection.target) onConnect(connection.source, connection.target)
  }, [onConnect])

  const rearrange = useCallback(() => {
    setLocalNodes(nodes => nodes.map(node => ({ ...node, position: positions.get(node.id) ?? node.position })))
    requestAnimationFrame(() => void rf.fitView({ padding: 0.2, duration: viewportDuration() }))
  }, [positions, rf, setLocalNodes])

  return (
    <div
      className="agent-links-root relative h-full w-full"
      role="application"
      aria-label={t('agentLinks.canvasLabel')}
      onKeyDown={event => { if (event.key === 'Escape') select(null) }}
    >
      <ReactFlow
        nodes={displayNodes}
        edges={edges}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onConnect={handleConnect}
        isValidConnection={isValidConnection}
        onPaneClick={() => select(null)}
        deleteKeyCode={null}
        connectionRadius={32}
        fitView
        fitViewOptions={{ padding: 0.2 }}
        minZoom={0.3}
        maxZoom={2}
        proOptions={{ hideAttribution: true }}
      >
        <Background color="var(--color-border-subtle)" gap={16} size={1} />
        <Controls showInteractive={false} showFitView={false} />
      </ReactFlow>
      <div className="absolute right-3 top-3 z-10 flex gap-2">
        <Button variant="outline" size="icon" onClick={rearrange} aria-label={t('agentLinks.rearrange')} title={t('agentLinks.rearrange')}>
          <LayoutGrid />
        </Button>
        <Button
          variant="outline"
          size="icon"
          onClick={() => void rf.fitView({ padding: 0.2, duration: viewportDuration() })}
          aria-label={t('agentLinks.fitView')}
          title={t('agentLinks.fitView')}
        >
          <Maximize2 />
        </Button>
      </div>
    </div>
  )
}

export default function AgentLinksCanvas(props: Props) {
  return (
    <ReactFlowProvider>
      <CanvasInner {...props} />
    </ReactFlowProvider>
  )
}
