import { Component, useMemo, useState } from 'react'
import type { Edge, Node } from '@xyflow/react'
import { Menu, RefreshCcw, AlertTriangle } from 'lucide-react'
import Sidebar from '@/components/Sidebar'
import SidebarExpandButton from '@/components/SidebarExpandButton'
import { Button } from '@/components/ui/button'
import { useAuth } from '@/hooks/useAuth'
import TopologyCanvas from '@/components/topology/TopologyCanvas'
import FilterPanel, {
  DEFAULT_FILTER,
  type FilterState,
} from '@/components/topology/FilterPanel'
import DetailPanel from '@/components/topology/DetailPanel'
import { useGraphData } from '@/components/topology/useGraphData'
import { useGraphLayout } from '@/components/topology/useGraphLayout'
import {
  useTopologyLayoutOverrides,
  type Scope,
} from '@/components/topology/useTopologyLayoutOverrides'
import type { GraphEdge, GraphNode, NodeKind } from '@/components/topology/types'
import { TEXT_MUTED, TEXT_PRIMARY } from '@/components/topology/constants'

/**
 * Simple error boundary so a crash inside React Flow doesn't take the
 * whole SPA down. Matches the DESIGN.md card styling.
 */
interface ErrState {
  error: Error | null
}
class TopologyErrorBoundary extends Component<{ children: React.ReactNode }, ErrState> {
  state: ErrState = { error: null }
  static getDerivedStateFromError(error: Error): ErrState {
    return { error }
  }
  componentDidCatch(error: Error) {
    // eslint-disable-next-line no-console
    console.error('TopologyPage crashed:', error)
  }
  render() {
    if (this.state.error) {
      return (
        <EmptyState
          title="토폴로지를 그릴 수 없어요"
          message={this.state.error.message}
          action={
            <Button onClick={() => this.setState({ error: null })}>
              <RefreshCcw className="h-4 w-4" /> 다시 시도
            </Button>
          }
        />
      )
    }
    return this.props.children
  }
}

function EmptyState({
  title,
  message,
  action,
}: {
  title: string
  message?: string
  action?: React.ReactNode
}) {
  return (
    <div
      style={{
        flex: 1,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: 24,
        background: '#f6f5f4',
      }}
    >
      <div
        style={{
          maxWidth: 420,
          padding: 24,
          background: '#ffffff',
          border: '1px solid rgba(0,0,0,0.1)',
          borderRadius: 12,
          boxShadow: '0 1px 2px rgba(0,0,0,0.04)',
          textAlign: 'center',
          display: 'flex',
          flexDirection: 'column',
          gap: 12,
          alignItems: 'center',
        }}
      >
        <AlertTriangle
          size={28}
          strokeWidth={1.5}
          style={{ color: '#dd5b00' }}
          aria-hidden
        />
        <h2 style={{ fontSize: 17, fontWeight: 700, color: TEXT_PRIMARY, margin: 0 }}>
          {title}
        </h2>
        {message && (
          <p style={{ fontSize: 13, color: TEXT_MUTED, margin: 0, lineHeight: 1.5 }}>
            {message}
          </p>
        )}
        {action}
      </div>
    </div>
  )
}

function TopologySkeleton() {
  return (
    <div
      style={{
        flex: 1,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        background: '#f6f5f4',
      }}
    >
      <div style={{ color: TEXT_MUTED, fontSize: 13 }}>토폴로지를 불러오는 중...</div>
    </div>
  )
}

/**
 * Route-level container. v1 follows the AdminMachinesPage layout:
 * Sidebar (left) + mobile top bar + main canvas region with filter
 * rail + detail panel siblings.
 */
export default function TopologyPage() {
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [selected, setSelected] = useState<GraphNode | null>(null)
  const [filter, setFilter] = useState<FilterState>(DEFAULT_FILTER)
  const { user } = useAuth()

  // Poll every 5s so the Room "is_typing" pulse (#84) stays within the
  // server's ``Cache-Control: max-age=5`` envelope. ETag short-circuits
  // unchanged responses to 304 — this is a cheap heartbeat, not a full
  // refetch loop. Polling auto-pauses when the tab is hidden.
  const { data, loading, error, refresh } = useGraphData('auto', 5000)

  // Server-side graph → filtered client view.
  const { nodesIn, edgesIn, counts, knownEngines, knownStates } = useMemo(() => {
    const emptyCounts: Record<NodeKind, number> = {
      user: 0,
      machine: 0,
      agent: 0,
      room: 0,
      project: 0,
    }
    if (!data) {
      return {
        nodesIn: [] as GraphNode[],
        edgesIn: [] as GraphEdge[],
        counts: emptyCounts,
        knownEngines: [] as string[],
        knownStates: [] as string[],
      }
    }
    const counts = { ...emptyCounts }
    for (const n of data.nodes) counts[n.kind] = (counts[n.kind] ?? 0) + 1
    const engines = new Set<string>()
    const states = new Set<string>()
    for (const n of data.nodes) {
      if (n.kind === 'agent') {
        const d = n.data as Record<string, unknown>
        if (typeof d.engine === 'string' && d.engine) engines.add(d.engine)
        if (typeof d.actual_state === 'string' && d.actual_state) states.add(d.actual_state)
      }
    }
    return {
      nodesIn: data.nodes,
      edgesIn: data.edges,
      counts,
      knownEngines: [...engines].sort(),
      knownStates: [...states].sort(),
    }
  }, [data])

  const filteredNodes = useMemo(() => {
    const search = filter.search.trim().toLowerCase()
    return nodesIn.filter(n => {
      if (!filter.kinds[n.kind]) return false
      if (search && !n.label.toLowerCase().includes(search)) return false
      if (n.kind === 'agent') {
        const d = n.data as Record<string, unknown>
        if (filter.engines && filter.engines.length > 0) {
          const engine = typeof d.engine === 'string' ? d.engine : ''
          if (!filter.engines.includes(engine)) return false
        }
        if (filter.actualStates && filter.actualStates.length > 0) {
          const s = typeof d.actual_state === 'string' ? d.actual_state : ''
          if (!filter.actualStates.includes(s)) return false
        }
      }
      return true
    })
  }, [nodesIn, filter])

  const filteredEdges = useMemo(() => {
    const visibleIds = new Set(filteredNodes.map(n => n.id))
    return edgesIn.filter(e => visibleIds.has(e.source) && visibleIds.has(e.target))
  }, [edgesIn, filteredNodes])

  // Per-user, per-scope position overrides (#234). ``scope`` is the
  // server-resolved value from ``useGraphData('auto')``, so we can only
  // scope storage correctly after the first successful fetch. The hook
  // gracefully no-ops while either ``userId`` or ``scope`` is null.
  const scope: Scope | null =
    data?.scope === 'global' || data?.scope === 'personal' ? data.scope : null
  const { overrides, setPosition, reset, hasOverrides } =
    useTopologyLayoutOverrides(user?.id ?? null, scope)

  const layouted = useGraphLayout(filteredNodes, filteredEdges, overrides)

  // Translate a selected RF Node back to the original GraphNode shape
  // so the DetailPanel can speak kind-based branches.
  const handleSelect = (node: Node | null) => {
    if (!node) {
      setSelected(null)
      return
    }
    const original = nodesIn.find(n => n.id === node.id) ?? null
    setSelected(original)
  }

  return (
    <div className="flex h-screen overflow-hidden bg-[var(--color-background)]">
      <Sidebar
        selectedRoom={null}
        open={sidebarOpen}
        onClose={() => setSidebarOpen(false)}
      />
      <SidebarExpandButton />
      <main className="flex min-w-0 flex-1 flex-col overflow-hidden bg-[var(--color-background)]">
        {/* Mobile top bar */}
        <div className="flex h-14 shrink-0 items-center gap-2 border-b border-[var(--color-border)] bg-white px-4 md:hidden">
          <Button
            variant="ghost"
            size="icon"
            onClick={() => setSidebarOpen(true)}
            aria-label="Open sidebar"
          >
            <Menu className="h-5 w-5" />
          </Button>
          <span className="text-[15px] font-bold tracking-tight">Topology</span>
        </div>

        {/* Desktop header */}
        <div className="hidden h-14 shrink-0 items-center justify-between border-b border-[var(--color-border)] bg-white px-6 md:flex">
          <div className="flex items-baseline gap-3">
            <span className="text-[17px] font-bold tracking-tight text-[var(--color-foreground)]">
              Topology
            </span>
            {data && (
              <span className="text-xs text-[var(--color-foreground-muted)]">
                {data.scope} · {data.nodes.length} nodes · {data.edges.length} edges
              </span>
            )}
          </div>
          <Button variant="ghost" size="sm" onClick={refresh} disabled={loading}>
            <RefreshCcw className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} />
            Refresh
          </Button>
        </div>

        <div className="flex min-h-0 flex-1">
          {/* Left filter rail — hidden on mobile */}
          <div className="hidden md:block">
            <FilterPanel
              filter={filter}
              onChange={setFilter}
              counts={counts}
              knownEngines={knownEngines}
              knownStates={knownStates}
            />
          </div>

          <TopologyErrorBoundary>
            {loading && !data ? (
              <TopologySkeleton />
            ) : error ? (
              <EmptyState
                title="토폴로지를 불러오지 못했습니다"
                message={error.message}
                action={
                  <Button onClick={refresh}>
                    <RefreshCcw className="h-4 w-4" /> 다시 시도
                  </Button>
                }
              />
            ) : !data || data.nodes.length === 0 ? (
              <EmptyState
                title="아직 보여줄 게 없어요"
                message="머신을 등록하거나 룸에 참여하면 여기에 그래프가 그려져요."
              />
            ) : (
              <section className="flex min-w-0 flex-1">
                <div style={{ flex: 1, minWidth: 0 }}>
                  <TopologyCanvas
                    nodes={layouted.nodes as Node[]}
                    edges={layouted.edges as Edge[]}
                    onSelect={handleSelect}
                    selectedId={selected?.id ?? null}
                    onPositionChange={setPosition}
                    onResetLayout={reset}
                    hasOverrides={hasOverrides}
                  />
                </div>
                {selected && (
                  <DetailPanel
                    selected={selected}
                    onClose={() => setSelected(null)}
                    isAdmin={Boolean(user?.is_admin)}
                  />
                )}
              </section>
            )}
          </TopologyErrorBoundary>
        </div>
      </main>
    </div>
  )
}
