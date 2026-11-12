import { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import dagre from 'dagre'
import {
  BaseEdge, Controls, Background, Handle, MarkerType, NodeToolbar, Position, ReactFlow, ReactFlowProvider,
  applyNodeChanges, getBezierPath, useNodesInitialized, useNodesState,
  useReactFlow, useStore, useUpdateNodeInternals,
  type Connection, type Edge, type EdgeChange, type EdgeProps, type EdgeTypes, type FinalConnectionState,
  type Node, type NodeChange, type NodeProps, type NodeTypes,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import './agentLinks.css'
import { Clock3, LayoutGrid, Maximize2 } from 'lucide-react'
import type { MessageKey } from '@/i18n/messages'
import { Button } from '@/components/ui/button'
import { useLocale } from '@/i18n/LocaleProvider'
import { useAgentLinkView } from '@/hooks/useAgentLinkView'
import { EngineGlyph } from '@/components/EngineGlyph'
import { agentNodeBox, agentNodeDiameter, reachableFrom, topAlert, type ActivePeerEdge, type AgentActivity, type AgentAlertKind, type AgentAlerts, type AgentLink, type LinkAgent } from '@/lib/agentLinks'

export type LinkSelection = { kind: 'agent'; id: string } | { kind: 'link'; id: string } | null
interface Props {
  agents: LinkAgent[]
  links: AgentLink[]
  userId: string | null
  selection: LinkSelection
  onSelect: (selection: LinkSelection) => void
  onConnect: (fromId: string, toId: string) => void
  /** COL-19 — live state per agent id; agents missing here read as idle. */
  activity?: ReadonlyMap<string, AgentActivity>
  /** COL-19 — questions running now; matching links flow, others draw dashed. */
  activeEdges?: readonly ActivePeerEdge[]
  roomName?: (roomId: string) => string | null
  onOpenRoom?: (roomId: string) => void
  /** COL-20 — what needs the person, per agent id. */
  alerts?: ReadonlyMap<string, AgentAlerts>
  onOpenHref?: (href: string) => void
}
type Emphasis = 'reach' | 'none'
interface AgentNodeData extends Record<string, unknown> {
  name: string; engine: string | null; emphasis: Emphasis; diameter: number
  status: AgentActivity['state']; statusText: string | null
  room: { id: string; label: string } | null
  onOpenRoom?: (roomId: string) => void
  /** COL-20 — the one badge at the top right, the queued count at the bottom right, and the card's lines. */
  alert: AgentAlertKind | null
  queued: number
  alertLines: AlertLine[]
  onOpenHref?: (href: string) => void
}
interface AlertLine { key: string; kind: AgentAlertKind | 'queued'; text: string; roomId?: string | null; href?: string | null }
const ALERT_LABELS: Record<AgentAlertKind, MessageKey> = {
  issue: 'agentLinks.alert.issue', waiting: 'agentLinks.alert.waiting', unread: 'agentLinks.alert.unread',
}
const CARD_ROOMS = 3
const UNAVAILABLE_KEYS: Record<string, MessageKey> = {
  crashed: 'agentLinks.unavailable.crashed',
  crash_loop: 'agentLinks.unavailable.crashed',
  spawn_failed: 'agentLinks.unavailable.crashed',
  no_machine_for_engine: 'agentLinks.unavailable.noMachine',
  machine_removed: 'agentLinks.unavailable.noMachine',
  machine_busy: 'agentLinks.unavailable.noMachine',
  no_room: 'agentLinks.unavailable.noRoom',
}
const IDLE: AgentActivity = { state: 'idle', roomIds: [], unavailableCode: null }
function viewportDuration() { return window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ? 0 : 220 }
const LinkAgentNode = memo(function LinkAgentNode({ id, data, selected }: NodeProps<Node<AgentNodeData>>) {
  const updateInternals = useUpdateNodeInternals()
  // React Flow must remeasure handles as a link changes the circle's size.
  useEffect(() => { updateInternals(id) }, [id, data.diameter, updateInternals])
  const [hovered, setHovered] = useState(false)
  const tone = data.status === 'busy' ? ' is-busy' : data.status === 'unavailable' ? ' is-unavailable' : ''
  // #1031 — the circle carries the engine and the state; the card on hover or selection says the rest.
  return <div className={`agent-link-node${tone}${selected ? ' is-selected' : data.emphasis === 'reach' ? ' is-reachable' : ''}`}
    onMouseEnter={() => setHovered(true)} onMouseLeave={() => setHovered(false)}>
    {data.status === 'busy' && <span className="agent-link-node__aurora" aria-hidden="true" />}
    <span className="agent-link-node__icon" aria-hidden="true"><EngineGlyph engine={data.engine ?? undefined} size={Math.round(data.diameter / 2)} /></span>
    <span className="agent-link-node__name">{data.name}</span>
    {data.alert && <span className={`agent-link-alert is-${data.alert}`} aria-hidden="true">
      {data.alert === 'issue' ? '!' : data.alert === 'waiting' ? <Clock3 strokeWidth={3} /> : null}
    </span>}
    {data.queued > 0 && <span className="agent-link-queue" aria-hidden="true">{data.queued}</span>}
    <NodeToolbar isVisible={hovered || selected} position={Position.Top} offset={10}>
      <div className="agent-link-card nodrag nopan" data-testid={`agent-link-card-${id}`}>
        <span className="agent-link-card__name">{data.name}</span>
        {data.engine && <span className="agent-link-card__meta">{data.engine}</span>}
        {data.statusText && <span>
          {data.room && data.onOpenRoom
            ? <button type="button" className="agent-link-card__room" onClick={event => { event.stopPropagation(); data.onOpenRoom!(data.room!.id) }}>{data.statusText}</button>
            : data.statusText}
        </span>}
        {data.alertLines.map(line => {
          const open = line.href && data.onOpenHref ? () => data.onOpenHref!(line.href!)
            : line.roomId && data.onOpenRoom ? () => data.onOpenRoom!(line.roomId!) : null
          return <span key={line.key} className={`agent-link-card__alert is-${line.kind}`}>
            {open ? <button type="button" className="agent-link-card__room" onClick={event => { event.stopPropagation(); open() }}>{line.text}</button> : line.text}
          </span>
        })}
      </div>
    </NodeToolbar>
    <Handle type="target" position={Position.Left} isConnectableStart={false} />
    <Handle type="source" position={Position.Right} isConnectableEnd={false} />
  </div>
})
// A drop anywhere on a card links to it, not only within reach of its small port.
function nodeIdAt(event: MouseEvent | TouchEvent) {
  const point = 'changedTouches' in event ? event.changedTouches[0] : event
  if (!point) return null
  return document.elementFromPoint(point.clientX, point.clientY)?.closest('.react-flow__node')?.getAttribute('data-id') ?? null
}
const LinkEdge = memo(function LinkEdge({ id, sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, markerEnd, style }: EdgeProps) {
  // A stronger curve keeps the run into each card level instead of hooking at the end.
  const [bezier] = getBezierPath({ sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, curvature: 0.5 })
  // A return connection uses a separate lane, keeping both arrows readable.
  const y = Math.max(sourceY, targetY) + 64
  const path = sourceX > targetX
    ? `M ${sourceX} ${sourceY} C ${sourceX + 48} ${sourceY}, ${sourceX + 48} ${y}, ${sourceX} ${y} L ${targetX - 24} ${y} C ${targetX - 48} ${y}, ${targetX - 48} ${targetY}, ${targetX} ${targetY}`
    : bezier
  return <BaseEdge id={id} path={path} markerEnd={markerEnd} style={style} interactionWidth={28} />
})
// Reserve room for the top-right tools, the bottom-left zoom controls and the last row's names.
const fitViewOptions = { padding: { top: '64px', bottom: '64px', left: '64px', right: '24px' }, maxZoom: 1 } as const
const nodeTypes: NodeTypes = { agent: LinkAgentNode }
const edgeTypes: EdgeTypes = { link: LinkEdge }

function layoutPositions(agents: LinkAgent[], links: AgentLink[], counts: Map<string, number>) {
  const graph = new dagre.graphlib.Graph()
  graph.setGraph({ rankdir: 'LR', nodesep: 48, ranksep: 96, marginx: 32, marginy: 32 })
  graph.setDefaultEdgeLabel(() => ({}))
  // Lay out the circle with its name under it so names never overlap a neighbour.
  for (const a of agents) graph.setNode(a.id, agentNodeBox(counts.get(a.id) ?? 0))
  for (const l of links) if (graph.hasNode(l.from_agent_id) && graph.hasNode(l.to_agent_id)) graph.setEdge(l.from_agent_id, l.to_agent_id)
  dagre.layout(graph)
  return new Map(agents.map(a => {
    const n = counts.get(a.id) ?? 0, d = agentNodeDiameter(n), box = graph.node(a.id)
    return [a.id, { x: box.x - d / 2, y: box.y - agentNodeBox(n).height / 2 }]
  }))
}
type Translate = ReturnType<typeof useLocale>['t']
/** COL-20 — the card lists every alert, each opening its room or item. */
function alertLinesFor(alerts: AgentAlerts, room: (id: string) => string, t: Translate): AlertLine[] {
  const lines: AlertLine[] = []
  const more = (key: string, kind: AlertLine['kind'], shown: number, total: number) => {
    if (total > shown) lines.push({ key: `${key}:more`, kind, text: t('agentLinks.alert.more', { count: total - shown }) })
  }
  for (const task of alerts.failed_tasks) lines.push({ key: `task:${task.id}`, kind: 'issue', text: t('agentLinks.alert.failedTask', { title: task.title }), roomId: task.room_id })
  more('task', 'issue', alerts.failed_tasks.length, alerts.failed_tasks_count)
  for (const id of alerts.failed_rooms.slice(0, CARD_ROOMS)) lines.push({ key: `failed:${id}`, kind: 'issue', text: t('agentLinks.alert.failedRoom', { room: room(id) }), roomId: id })
  more('failed', 'issue', Math.min(CARD_ROOMS, alerts.failed_rooms.length), alerts.failed_rooms.length)
  for (const item of alerts.waiting) lines.push({ key: `wait:${item.id}`, kind: 'waiting', text: t('agentLinks.alert.waitingItem', { title: item.title }), roomId: item.room_id, href: item.href })
  more('wait', 'waiting', alerts.waiting.length, alerts.waiting_count)
  for (const id of alerts.unread_rooms.slice(0, CARD_ROOMS)) lines.push({ key: `unread:${id}`, kind: 'unread', text: t('agentLinks.alert.unreadRoom', { room: room(id) }), roomId: id })
  more('unread', 'unread', Math.min(CARD_ROOMS, alerts.unread_rooms.length), alerts.unread_rooms.length)
  if (alerts.queued_tasks > 0) lines.push({ key: 'queued', kind: 'queued', text: t('agentLinks.alert.queued', { count: alerts.queued_tasks }) })
  return lines
}
function CanvasInner({ agents, links, userId, selection, onSelect, onConnect, activity, activeEdges, roomName, onOpenRoom, alerts, onOpenHref }: Props) {
  const { t } = useLocale()
  const rf = useReactFlow()
  const { view, savePosition, saveViewport, reset } = useAgentLinkView(userId)
  const initialView = useRef(view)
  const initialized = useNodesInitialized()
  const restored = useRef(false)
  const canvasWidth = useStore(state => state.width)
  const canvasHeight = useStore(state => state.height)
  const previousSize = useRef({ width: 0, height: 0 })
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
        const now = activity?.get(a.id) ?? IDLE
        const existing = old.get(a.id), diameter = agentNodeDiameter(counts.get(a.id) ?? 0)
        const firstRoom = now.roomIds[0]
        const label = firstRoom ? `#${roomName?.(firstRoom) ?? t('agentLinks.someRoom')}` : ''
        const statusText = now.state === 'busy'
          ? (now.roomIds.length > 1 ? t('agentLinks.busyInMany', { room: label, count: now.roomIds.length - 1 }) : t('agentLinks.busyIn', { room: label }))
          : now.state === 'unavailable'
            ? t(UNAVAILABLE_KEYS[now.unavailableCode ?? ''] ?? 'agentLinks.unavailable.offline')
            : null
        // COL-20 — an agent that cannot answer shows its outline only, no badges.
        const mine = now.state === 'unavailable' ? undefined : alerts?.get(a.id)
        const alertLines = mine ? alertLinesFor(mine, id => `#${roomName?.(id) ?? t('agentLinks.someRoom')}`, t) : []
        return { ...existing, id: a.id, type: 'agent', position: existing?.position ?? initialView.current.positions[a.id] ?? positions.get(a.id)!,
          style: { width: diameter, height: diameter }, data: { name: a.name, engine: a.engine ?? null, diameter, emphasis: 'none' as Emphasis, status: now.state, statusText,
            room: firstRoom ? { id: firstRoom, label } : null, onOpenRoom,
            alert: topAlert(mine), queued: mine?.queued_tasks ?? 0, alertLines, onOpenHref } }
      })
    })
  }, [agents, counts, positions, setLocalNodes, activity, roomName, onOpenRoom, alerts, onOpenHref, t])
  useEffect(() => {
    if (!initialized || restored.current) return
    restored.current = true
    if (initialView.current.viewport) void rf.setViewport(initialView.current.viewport)
    else void rf.fitView(fitViewOptions)
  }, [initialized, rf])
  useEffect(() => {
    const previous = previousSize.current
    previousSize.current = { width: canvasWidth, height: canvasHeight }
    // Details shrink the map horizontally on desktop and vertically on mobile.
    // Initial dimensions must not overwrite a restored user viewport.
    if (previous.width > 0 && previous.height > 0 && restored.current
      && (previous.width !== canvasWidth || previous.height !== canvasHeight)) {
      void rf.fitView(fitViewOptions)
    }
  }, [canvasWidth, canvasHeight, rf])
  const reach = useMemo(() => selection?.kind === 'agent' ? reachableFrom(selection.id, links) : null, [selection, links])
  const names = useMemo(() => new Map(agents.map(a => [a.id, a.name])), [agents])
  const nodes = useMemo(() => localNodes.map(node => {
    const emphasis: Emphasis = reach?.agents.has(node.id) ? 'reach' : 'none'
    return { ...node, selected: selection?.kind === 'agent' && selection.id === node.id,
      // The state moved off the circle into its card, so the accessible name keeps it.
      ariaLabel: [t('agentLinks.agentNode', { name: node.data.name }), node.data.statusText,
        node.data.alert && t(ALERT_LABELS[node.data.alert]),
        node.data.queued > 0 && t('agentLinks.alert.queued', { count: node.data.queued })].filter(Boolean).join(', '), data: { ...node.data, emphasis } }
  }), [localNodes, selection, reach, t])
  const edges = useMemo<Edge[]>(() => {
    const running = new Set((activeEdges ?? []).map(e => `${e.from_agent_id}>${e.to_agent_id}`))
    const ids = new Set(agents.map(a => a.id))
    // The built-in symbol occupies 8 of its 20 viewBox units: 16 gives a 6.4px chevron.
    const arrow = (color: string) => ({ type: MarkerType.Arrow, color, width: 16, height: 16, markerUnits: 'userSpaceOnUse' as const, strokeWidth: 1.5 })
    const linked = links.map(l => {
      const emphasized = selection?.kind === 'link' && selection.id === l.id || !!reach?.links.has(l.id)
      const live = running.delete(`${l.from_agent_id}>${l.to_agent_id}`)
      const color = emphasized || live ? 'var(--color-brand-text)' : 'var(--color-foreground-subtle)'
      // COL-19 — only a question running now flows; a resting link is a still line with an arrow.
      return { id: l.id, source: l.from_agent_id, target: l.to_agent_id, type: 'link', animated: live,
        selected: selection?.kind === 'link' && selection.id === l.id, focusable: true,
        ariaLabel: t(live ? 'agentLinks.linkLabelActive' : 'agentLinks.linkLabel', { from: names.get(l.from_agent_id) ?? '?', to: names.get(l.to_agent_id) ?? '?' }),
        className: [emphasized && 'is-emphasized', live && 'is-active'].filter(Boolean).join(' ') || undefined,
        markerEnd: arrow(color), style: { stroke: color } }
    })
    // A question between room members needs no link; draw it dashed while it runs.
    const unlinked = [...running].map(key => key.split('>')).filter(([from, to]) => ids.has(from) && ids.has(to)).map(([from, to]) => ({
      id: `activity:${from}>${to}`, source: from, target: to, type: 'link', animated: true,
      selectable: false, focusable: false, className: 'is-activity',
      markerEnd: arrow('var(--color-brand-text)'), style: { stroke: 'var(--color-brand-text)', strokeDasharray: '3 4' } }))
    return [...linked, ...unlinked]
  }, [agents, links, activeEdges, selection, reach, names, t])
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
  const canLink = useCallback((from: string, to: string) => from !== to && !links.some(l => l.from_agent_id === from && l.to_agent_id === to), [links])
  const isValidConnection = useCallback((connection: Connection | Edge) => canLink(connection.source, connection.target), [canLink])
  const onConnectEnd = useCallback((event: MouseEvent | TouchEvent, state: FinalConnectionState) => {
    // A valid drop on a port already went through onConnect.
    if (state.isValid || !state.fromNode) return
    const to = nodeIdAt(event)
    if (to && canLink(state.fromNode.id, to)) onConnect(state.fromNode.id, to)
  }, [canLink, onConnect])
  const rearrange = useCallback(() => {
    reset()
    setLocalNodes(nodes => nodes.map(node => ({ ...node, position: positions.get(node.id) ?? node.position })))
    for (const [id, pos] of positions) savePosition(id, pos)
    requestAnimationFrame(() => { void rf.fitView({ ...fitViewOptions, duration: viewportDuration() }) })
  }, [reset, positions, savePosition, rf, setLocalNodes])
  return <div className="agent-links-root relative h-full w-full" role="application" aria-label={t('agentLinks.canvasLabel')} onKeyDown={e => { if (e.key === 'Escape') select(null) }}>
    <ReactFlow nodes={nodes} edges={edges} nodeTypes={nodeTypes} edgeTypes={edgeTypes}
      onNodesChange={onNodesChange} onEdgesChange={onEdgesChange}
      onConnect={c => { if (c.source && c.target) onConnect(c.source, c.target) }} onConnectEnd={onConnectEnd} isValidConnection={isValidConnection}
      onNodeDragStop={(_, node) => savePosition(node.id, node.position)}
      onMoveEnd={(_, viewport) => { if (restored.current) saveViewport(viewport) }}
      onPaneClick={() => select(null)} deleteKeyCode={null} connectionRadius={32}
      connectionLineStyle={{ stroke: 'var(--color-brand-text)', strokeWidth: 1.5 }}
      minZoom={0.3} maxZoom={2} proOptions={{ hideAttribution: true }}>
      <Background color="var(--color-border-subtle)" gap={16} size={1} />
      <Controls showInteractive={false} showFitView={false} />
    </ReactFlow>
    <div className="absolute right-3 top-3 z-10 flex gap-2">
      <Button variant="outline" size="sm" onClick={rearrange} title={t('agentLinks.rearrangeHint')}><LayoutGrid />{t('agentLinks.rearrange')}</Button>
      <Button variant="outline" size="icon" onClick={() => void rf.fitView({ ...fitViewOptions, duration: viewportDuration() })} aria-label={t('agentLinks.fitView')} title={t('agentLinks.fitView')}><Maximize2 /></Button>
    </div>
  </div>
}
export default function AgentLinksCanvas(props: Props) {
  return <ReactFlowProvider><CanvasInner {...props} /></ReactFlowProvider>
}
