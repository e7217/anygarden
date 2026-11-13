import { Component, useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import { AlertTriangle, ArrowLeftRight, ChevronRight, Network, Plus, RefreshCcw, Trash2, X } from 'lucide-react'
import PageShell from '@/components/PageShell'
import { Button } from '@/components/ui/button'
import AgentLinksCanvas, { type LinkSelection } from '@/components/agent-links/AgentLinksCanvas'
import LinkCreateDialog, { linkErrorMessage, type LinkCreateTarget } from '@/components/agent-links/LinkCreateDialog'
import { useFeedback } from '@/components/feedback/FeedbackProvider'
import { useNavigate } from 'react-router-dom'
import AgentConversationsPanel from '@/components/agent-links/AgentConversationsPanel'
import AgentOverviewSummary from '@/components/agent-links/AgentOverviewSummary'
import { useAuth } from '@/hooks/useAuth'
import { useAgentLinks } from '@/hooks/useAgentLinks'
import { useActiveTurns } from '@/hooks/useActiveTurns'
import { useAgentAlerts } from '@/hooks/useAgentAlerts'
import { useActivePeerEdges } from '@/hooks/useActivePeerEdges'
import { useProjectInbox } from '@/hooks/useProjectInbox'
import { useRooms } from '@/hooks/useRooms'
import { useLocale } from '@/i18n/LocaleProvider'
import { agentActivity, findCycles, reachableFrom, type AgentLink, type LinkAgent } from '@/lib/agentLinks'

/** Keeps a React Flow crash from blanking the whole SPA (as on Topology). */
class CanvasBoundary extends Component<{ fallback: (retry: () => void, message: string) => ReactNode; children: ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null }
  static getDerivedStateFromError(error: Error) { return { error } }
  render() {
    if (this.state.error) return this.props.fallback(() => this.setState({ error: null }), this.state.error.message)
    return this.props.children
  }
}

function Notice({ title, body, action, tone = 'neutral' }: { title: string; body?: string; action?: ReactNode; tone?: 'neutral' | 'warning' }) {
  return (
    <div className="flex flex-1 items-center justify-center bg-[var(--color-surface-alt)] p-6">
      <div className="flex max-w-md flex-col items-center gap-3 rounded-[var(--radius-lg)] border border-[var(--color-border)] bg-[var(--color-surface-elevated)] p-6 text-center shadow-card">
        {tone === 'warning' && <AlertTriangle className="h-6 w-6 text-[var(--color-warning)]" aria-hidden="true" />}
        <h2 className="text-body font-semibold text-[var(--color-foreground)]">{title}</h2>
        {body && <p className="text-caption text-[var(--color-foreground-muted)]">{body}</p>}
        {action}
      </div>
    </div>
  )
}

/**
 * Overview (#1023, COL-19) on top of the agent-link map (#1006): which agent
 * may ask which, who is answering where right now, and which questions are
 * running. Any signed-in user may edit links. Drag between agents to add
 * one; select a link to delete it; select an agent to highlight everyone it
 * could pull in by following links. With nothing selected the side panel
 * sums up what needs the person.
 */
export default function AgentLinksPage() {
  const { t } = useLocale()
  const { confirm, notify } = useFeedback()
  const { graph, loading, errorStatus, refresh, create, remove } = useAgentLinks()
  const { user } = useAuth()
  const [saving, setSaving] = useState(0)
  const [selection, setSelection] = useState<LinkSelection>(null)
  const [editor, setEditor] = useState<LinkCreateTarget | null>(null)
  const navigate = useNavigate()
  const activeTurns = useActiveTurns(true)
  const activeEdges = useActivePeerEdges(true)
  const alerts = useAgentAlerts(true)
  const inbox = useProjectInbox(user?.id ?? null)
  const { rooms: roomsByProject, agentDMs } = useRooms()

  const agents = useMemo(() => graph?.agents ?? [], [graph])
  const links = useMemo(() => graph?.links ?? [], [graph])
  const nameOf = useCallback(
    (id: string) => agents.find(agent => agent.id === id)?.name ?? id.slice(0, 8),
    [agents],
  )
  const cycles = useMemo(() => findCycles(links), [links])
  const roomList = useMemo(
    () => [...Object.values(roomsByProject ?? {}).flat(), ...(agentDMs ?? [])],
    [roomsByProject, agentDMs],
  )
  const roomName = useCallback((id: string) => roomList.find(room => room.id === id)?.name ?? null, [roomList])
  const activity = useMemo(
    () => new Map(agents.map(agent => [agent.id, agentActivity(agent, activeTurns.roomsByAgent.get(agent.id) ?? [])])),
    [agents, activeTurns],
  )
  const running = useMemo(() => [...activeTurns.roomIds].map(id => ({
    room: { id, name: roomName(id) ?? t('agentLinks.someRoom') },
    agentIds: agents.filter(agent => activity.get(agent.id)?.roomIds.includes(id)).map(agent => agent.id),
  })), [activeTurns, agents, activity, roomName, t])
  const needsYou = useMemo(() => (inbox.items ?? []).filter(item => item.needs_action).map(item => ({
    id: item.id, title: item.task_title, roomName: item.task_room_name, href: item.task_href,
  })), [inbox.items])
  const roomsWithNews = useMemo(
    () => roomList.filter(room => room.has_updates && !activeTurns.roomIds.has(room.id)).map(room => ({ id: room.id, name: room.name })),
    [roomList, activeTurns],
  )
  const openRoom = useCallback((id: string) => navigate(`/rooms/${id}`), [navigate])
  const openHref = useCallback((href: string) => navigate(href), [navigate])

  // Drop a selection whose agent or link disappeared (deleted, refetched).
  useEffect(() => {
    if (!selection) return
    const exists = selection.kind === 'agent'
      ? agents.some(agent => agent.id === selection.id)
      : links.some(link => link.id === selection.id)
    if (!exists) setSelection(null)
  }, [selection, agents, links])

  const askDelete = useCallback(async (link: AgentLink) => {
    const names = { from: nameOf(link.from_agent_id), to: nameOf(link.to_agent_id) }
    const ok = await confirm({
      title: t('agentLinks.deleteTitle'),
      description: t('agentLinks.deleteDescription', names),
      confirmLabel: t('agentLinks.delete'),
      destructive: true,
    })
    if (!ok) return
    try {
      await remove(link.id)
    } catch (error) {
      const status = typeof (error as { status?: unknown }).status === 'number' ? (error as { status: number }).status : 0
      notify({ message: t('agentLinks.deleteFailed', { status }), tone: 'error' })
    }
  }, [confirm, notify, nameOf, remove, t])

  const connect = useCallback(async (fromId: string, toId: string) => {
    setSaving(n => n + 1)
    try {
      const link = await create({ from_agent_id: fromId, to_agent_id: toId })
      setSelection({ kind: 'link', id: link.id })
    } catch (error) { notify({ message: linkErrorMessage(t, error), tone: 'error' }) }
    finally { setSaving(n => n - 1) }
  }, [create, notify, t])

  // Picking an agent from the details panel also brings it into view on the map.
  const [focusAgent, setFocusAgent] = useState<{ id: string; seq: number } | null>(null)
  const openAgent = useCallback((id: string) => {
    setSelection({ kind: 'agent', id })
    setFocusAgent(previous => ({ id, seq: (previous?.seq ?? 0) + 1 }))
  }, [])

  const selectedLink = selection?.kind === 'link' ? links.find(link => link.id === selection.id) ?? null : null
  const selectedAgent = selection?.kind === 'agent' ? agents.find(agent => agent.id === selection.id) ?? null : null

  let body: ReactNode
  if (loading && !graph) {
    body = <div role="status" className="flex flex-1 items-center justify-center bg-[var(--color-surface-alt)] text-caption text-[var(--color-foreground-muted)]">{t('agentLinks.loading')}</div>
  } else if (errorStatus !== null && !graph) {
    body = (
      <Notice
        tone="warning"
        title={t('agentLinks.loadFailed', { status: errorStatus })}
        action={<Button onClick={() => void refresh()}><RefreshCcw /> {t('common.retry')}</Button>}
      />
    )
  } else if (agents.length === 0) {
    body = <Notice title={t('agentLinks.noAgentsTitle')} body={t('agentLinks.noAgentsBody')} />
  } else {
    body = (
      <div className="relative flex min-h-0 flex-1 flex-col lg:flex-row">
        <section className="relative min-h-0 min-w-0 flex-1" aria-label={t('agentLinks.canvasLabel')}>
          <CanvasBoundary
            fallback={(retry, message) => (
              <Notice tone="warning" title={t('topology.cannotDraw')} body={message} action={<Button onClick={retry}><RefreshCcw /> {t('common.retry')}</Button>} />
            )}
          >
            <AgentLinksCanvas
              key={user?.id ?? 'anonymous'}
              agents={agents}
              links={links}
              userId={user?.id ?? null}
              selection={selection}
              onSelect={setSelection}
              onConnect={(fromId, toId) => void connect(fromId, toId)}
              activity={activity}
              activeEdges={activeEdges}
              roomName={roomName}
              onOpenRoom={openRoom}
              alerts={alerts}
              onOpenHref={openHref}
              focusAgent={focusAgent}
              leadingActions={
                <Button variant="outline" size="icon" onClick={() => void refresh()} disabled={loading} aria-label={t('common.refresh')} title={t('common.refresh')}>
                  <RefreshCcw className={loading ? 'motion-safe:animate-spin' : undefined} />
                </Button>
              }
              actions={
                <Button size="sm" onClick={() => setEditor({})} disabled={agents.length < 2} title={t('agentLinks.dragHint')}>
                  <Plus /> {t('agentLinks.addLink')}
                </Button>
              }
            />
          </CanvasBoundary>
          {(links.length === 0 || cycles.length > 0 || saving > 0) && (
            // Non-blocking: the agents stay draggable underneath. Starts below the toolbar.
            <div className="pointer-events-none absolute inset-x-3 top-14 z-10 flex flex-col items-center gap-2">
              {saving > 0 && <p role="status" className="rounded-[var(--radius-pill)] bg-[var(--color-surface-elevated)] px-3 py-1 text-caption text-[var(--color-foreground-muted)] shadow-whisper">{t('agentLinks.saving')}</p>}
              {cycles.length > 0 && (
                <div role="status" data-testid="agent-links-cycles" className="pointer-events-auto flex max-w-md gap-2 rounded-[var(--radius-md)] border border-[var(--color-warning)] bg-[var(--color-warning-soft)] px-3 py-2 shadow-card">
                  <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-[var(--color-warning)]" aria-hidden="true" />
                  <div className="min-w-0 text-caption text-[var(--color-foreground)]">
                    <p className="font-semibold">{t('agentLinks.cycleTitle')}</p>
                    <ul className="mt-1 space-y-0.5">
                      {cycles.map(cycle => (
                        <li key={cycle.join('>')} className="break-words">
                          {[...cycle, cycle[0]].map(nameOf).join(' → ')}
                        </li>
                      ))}
                    </ul>
                    <p className="mt-1 text-[var(--color-foreground-muted)]">{t('agentLinks.cycleBody')}</p>
                  </div>
                </div>
              )}
              {links.length === 0 && (
                <div className="pointer-events-auto max-w-md rounded-[var(--radius-lg)] border border-[var(--color-border)] bg-[var(--color-surface-elevated)] p-4 shadow-card" data-testid="agent-links-empty">
                  <h2 className="text-body font-semibold text-[var(--color-foreground)]">{t('agentLinks.emptyTitle')}</h2>
                  <p className="mt-1 text-caption text-[var(--color-foreground-muted)]">{t('agentLinks.emptyBody')}</p>
                </div>
              )}
            </div>
          )}
        </section>
        {!selectedLink && !selectedAgent && (
          <DetailsPanel label={t('agentLinks.overview')}>
            <AgentOverviewSummary needsYou={needsYou} running={running} activeEdges={activeEdges} roomsWithNews={roomsWithNews} nameOf={nameOf} />
          </DetailsPanel>
        )}
        {(selectedLink || selectedAgent) && (
          <DetailsPanel label={t('agentLinks.details')} onClose={() => setSelection(null)}>
            {selectedLink && (
              <LinkDetails
                link={selectedLink}
                nameOf={nameOf}
                onReverse={links.some(l => l.from_agent_id === selectedLink.to_agent_id && l.to_agent_id === selectedLink.from_agent_id) ? undefined : () => void connect(selectedLink.to_agent_id, selectedLink.from_agent_id)}
                onDelete={() => void askDelete(selectedLink)}
              />
            )}
            {selectedAgent && (
              <AgentDetails
                agent={selectedAgent}
                user={user}
                onAdd={agents.some(a => a.id !== selectedAgent.id && !links.some(l => l.from_agent_id === selectedAgent.id && l.to_agent_id === a.id)) ? () => setEditor({ fromId: selectedAgent.id, lockFrom: true }) : undefined}
                links={links}
                nameOf={nameOf}
                onOpenAgent={openAgent}
                onDeleteLink={link => void askDelete(link)}
              />
            )}
          </DetailsPanel>
        )}
      </div>
    )
  }

  return (
    <PageShell title={t('agentLinks.title')} scroll={false}>
      <h1 className="sr-only">{t('agentLinks.title')}</h1>
      {body}
      <LinkCreateDialog
        target={editor}
        onClose={() => setEditor(null)}
        agents={agents}
        links={links}
        onCreate={async input => {
          const link = await create(input)
          setSelection({ kind: 'link', id: link.id })
        }}
      />
    </PageShell>
  )
}

function DetailsPanel({ label, onClose, children }: { label: string; onClose?: () => void; children: ReactNode }) {
  const { t } = useLocale()
  return (
    <aside
      aria-label={label}
      className="relative z-20 max-h-[55%] shrink-0 overflow-y-auto border-t border-[var(--color-border)] bg-[var(--color-surface)] p-4 shadow-deep lg:static lg:max-h-none lg:w-80 lg:shrink-0 lg:border-l lg:border-t-0 lg:shadow-none"
    >
      {onClose ? (
        <div className="mb-2 flex justify-end">
          <Button variant="ghost" size="icon" onClick={onClose} aria-label={t('agentLinks.closeDetails')}>
            <X />
          </Button>
        </div>
      ) : (
        <h2 className="mb-3 text-body font-semibold text-[var(--color-foreground)]">{label}</h2>
      )}
      {children}
    </aside>
  )
}

function LinkDetails({ link, nameOf, onReverse, onDelete }: {
  link: AgentLink
  nameOf: (id: string) => string
  onReverse?: () => void
  onDelete: () => void
}) {
  const { t } = useLocale()
  return <div className="space-y-4" data-testid="agent-link-details">
    <h2 className="break-words text-body font-semibold">{t('agentLinks.linkDetailsTitle', { from: nameOf(link.from_agent_id), to: nameOf(link.to_agent_id) })}</h2>
    <p className="text-caption text-[var(--color-foreground-muted)]">{t('agentLinks.description')}</p>
    <div className="flex flex-wrap gap-2">
      {onReverse && <Button variant="outline" size="sm" onClick={onReverse}><ArrowLeftRight />{t('agentLinks.reverseLink')}</Button>}
      <Button variant="ghost" size="sm" onClick={onDelete} className="text-[var(--color-danger)]"><Trash2 />{t('agentLinks.delete')}</Button>
    </div>
  </div>
}

function AgentDetails({ agent, user, onAdd, links, nameOf, onOpenAgent, onDeleteLink }: {
  user: { id: string; is_admin: boolean } | null
  onAdd?: () => void
  agent: LinkAgent
  links: AgentLink[]
  nameOf: (id: string) => string
  onOpenAgent: (id: string) => void
  onDeleteLink: (link: AgentLink) => void
}) {
  const { t, formatNumber } = useLocale()
  const reachable = [...reachableFrom(agent.id, links).agents].filter(id => id !== agent.id)
  const outgoing = links.filter(link => link.from_agent_id === agent.id)
  const incoming = links.filter(link => link.to_agent_id === agent.id)
  // #1035 — a name opens that agent; the row's own button removes the link.
  const linkRow = (link: AgentLink, otherId: string, deleteLabel: string) => (
    <li key={link.id} className="flex items-center gap-1">
      <button
        type="button"
        onClick={() => onOpenAgent(otherId)}
        className="flex min-h-[var(--control-height)] min-w-0 flex-1 items-center gap-2 rounded-[var(--radius-sm)] px-2 text-left text-caption font-medium text-[var(--color-foreground)] hover:bg-[var(--color-surface-hover)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-brand-focus)]"
      >
        <span className="min-w-0 flex-1 truncate">{nameOf(otherId)}</span>
        <ChevronRight className="size-4 shrink-0 text-[var(--color-foreground-muted)]" aria-hidden="true" />
      </button>
      <Button variant="ghost" size="icon" onClick={() => onDeleteLink(link)} aria-label={deleteLabel} title={deleteLabel}>
        <X />
      </Button>
    </li>
  )
  return (
    <div className="space-y-4" data-testid="agent-reach-details">
      <div>
        <h2 className="break-words text-body font-semibold text-[var(--color-foreground)]">{agent.name}</h2>
        {agent.description && <p className="mt-1 text-caption text-[var(--color-foreground-muted)]">{agent.description}</p>}
      </div>
      <AgentConversationsPanel key={`${user?.id ?? ''}:${agent.id}`} agentId={agent.id} user={user} />
      {onAdd && <Button variant="outline" size="sm" onClick={onAdd}><Plus />{t('agentLinks.addLink')}</Button>}
      <p className="flex items-start gap-2 text-caption text-[var(--color-foreground-muted)]">
        <Network className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
        <span>{reachable.length === 0
          ? t('agentLinks.reachNone', { name: agent.name })
          : t('agentLinks.reachSummary', { count: formatNumber(reachable.length) })}</span>
      </p>
      <div>
        <h3 className="text-badge uppercase tracking-wider text-[var(--color-foreground-muted)]">{t('agentLinks.outgoing')}</h3>
        {outgoing.length === 0
          ? <p className="mt-1 text-caption text-[var(--color-foreground-subtle)]">{t('agentLinks.none')}</p>
          : <ul className="mt-1 space-y-0.5">{outgoing.map(link => linkRow(link, link.to_agent_id, t('agentLinks.deleteNamed', { to: nameOf(link.to_agent_id) })))}</ul>}
      </div>
      <div>
        <h3 className="text-badge uppercase tracking-wider text-[var(--color-foreground-muted)]">{t('agentLinks.incoming')}</h3>
        {incoming.length === 0
          ? <p className="mt-1 text-caption text-[var(--color-foreground-subtle)]">{t('agentLinks.none')}</p>
          : <ul className="mt-1 space-y-0.5">{incoming.map(link => linkRow(link, link.from_agent_id, t('agentLinks.deleteIncoming', { from: nameOf(link.from_agent_id) })))}</ul>}
      </div>
    </div>
  )
}

