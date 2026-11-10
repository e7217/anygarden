import { Component, useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import { AlertTriangle, Pencil, Plus, RefreshCcw, Trash2, X } from 'lucide-react'
import PageShell from '@/components/PageShell'
import { Button } from '@/components/ui/button'
import AgentLinksCanvas, { type LinkSelection } from '@/components/agent-links/AgentLinksCanvas'
import LinkEditorDialog, { type LinkEditorTarget } from '@/components/agent-links/LinkEditorDialog'
import { useFeedback } from '@/components/feedback/FeedbackProvider'
import { useAgentLinks } from '@/hooks/useAgentLinks'
import { useLocale } from '@/i18n/LocaleProvider'
import { findCycles, reachableFrom, type AgentLink, type LinkAgent } from '@/lib/agentLinks'

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
 * Relationship map of agent links (#1006): which agent may ask which,
 * and when. Any signed-in user may edit links. Drag between agents to
 * add one; select a link to edit or delete it; select an agent to
 * highlight everyone it could pull in by following links.
 */
export default function AgentLinksPage() {
  const { t, formatNumber } = useLocale()
  const { confirm, notify } = useFeedback()
  const { graph, loading, errorStatus, refresh, create, update, remove } = useAgentLinks()
  const [selection, setSelection] = useState<LinkSelection>(null)
  const [editor, setEditor] = useState<LinkEditorTarget | null>(null)

  const agents = useMemo(() => graph?.agents ?? [], [graph])
  const links = useMemo(() => graph?.links ?? [], [graph])
  const nameOf = useCallback(
    (id: string) => agents.find(agent => agent.id === id)?.name ?? id.slice(0, 8),
    [agents],
  )
  const cycles = useMemo(() => findCycles(links), [links])

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
      <div className="relative flex min-h-0 flex-1">
        <section className="relative min-h-0 min-w-0 flex-1" aria-label={t('agentLinks.canvasLabel')}>
          <CanvasBoundary
            fallback={(retry, message) => (
              <Notice tone="warning" title={t('topology.cannotDraw')} body={message} action={<Button onClick={retry}><RefreshCcw /> {t('common.retry')}</Button>} />
            )}
          >
            <AgentLinksCanvas
              agents={agents}
              links={links}
              selection={selection}
              onSelect={setSelection}
              onConnect={(fromId, toId) => setEditor({ kind: 'create', fromId, toId, lockFrom: true, lockTo: true })}
            />
          </CanvasBoundary>
          {links.length === 0 && (
            // Non-blocking: the agents stay draggable underneath.
            <div className="pointer-events-none absolute inset-x-3 top-3 z-10 flex justify-center">
              <div className="pointer-events-auto max-w-md rounded-[var(--radius-lg)] border border-[var(--color-border)] bg-[var(--color-surface-elevated)] p-4 shadow-card" data-testid="agent-links-empty">
                <h2 className="text-body font-semibold text-[var(--color-foreground)]">{t('agentLinks.emptyTitle')}</h2>
                <p className="mt-1 text-caption text-[var(--color-foreground-muted)]">{t('agentLinks.emptyBody')}</p>
              </div>
            </div>
          )}
        </section>
        {(selectedLink || selectedAgent) && (
          <DetailsPanel onClose={() => setSelection(null)}>
            {selectedLink && (
              <LinkDetails
                link={selectedLink}
                nameOf={nameOf}
                onEdit={() => setEditor({ kind: 'edit', link: selectedLink })}
                onDelete={() => void askDelete(selectedLink)}
              />
            )}
            {selectedAgent && (
              <AgentDetails
                agent={selectedAgent}
                links={links}
                nameOf={nameOf}
                onSelectLink={id => setSelection({ kind: 'link', id })}
              />
            )}
          </DetailsPanel>
        )}
      </div>
    )
  }

  return (
    <PageShell title={t('agentLinks.title')} scroll={false}>
      <div className="shrink-0 border-b border-[var(--color-border)] bg-[var(--color-surface)] px-4 py-3 md:px-6">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
          <h1 className="text-body font-bold tracking-tight text-[var(--color-foreground)] max-md:sr-only">{t('agentLinks.title')}</h1>
          {graph && (
            <span className="text-badge text-[var(--color-foreground-muted)]">
              {t('agentLinks.summary', { agents: formatNumber(agents.length), links: formatNumber(links.length) })}
            </span>
          )}
          <div className="ml-auto flex items-center gap-2">
            <Button variant="ghost" size="sm" onClick={() => void refresh()} disabled={loading} aria-label={t('common.refresh')} title={t('common.refresh')}>
              <RefreshCcw className={loading ? 'motion-safe:animate-spin' : undefined} />
              <span className="max-sm:sr-only">{t('common.refresh')}</span>
            </Button>
            <Button size="sm" onClick={() => setEditor({ kind: 'create' })} disabled={agents.length < 2}>
              <Plus /> {t('agentLinks.addLink')}
            </Button>
          </div>
        </div>
        <p className="mt-1 max-w-3xl text-caption text-[var(--color-foreground-muted)]">
          {t('agentLinks.description')} <span className="max-md:hidden">{t('agentLinks.dragHint')}</span>
        </p>
        {cycles.length > 0 && (
          <div role="status" data-testid="agent-links-cycles" className="mt-3 flex gap-2 rounded-[var(--radius-md)] border border-[var(--color-warning)] bg-[var(--color-warning-soft)] px-3 py-2">
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
      </div>
      {body}
      <LinkEditorDialog
        target={editor}
        onClose={() => setEditor(null)}
        agents={agents}
        links={links}
        onCreate={async input => {
          const link = await create(input)
          setSelection({ kind: 'link', id: link.id })
        }}
        onUpdate={update}
      />
    </PageShell>
  )
}

function DetailsPanel({ onClose, children }: { onClose: () => void; children: ReactNode }) {
  const { t } = useLocale()
  return (
    <aside
      aria-label={t('agentLinks.details')}
      className="absolute inset-x-0 bottom-0 z-20 max-h-[60%] overflow-y-auto border-t border-[var(--color-border)] bg-[var(--color-surface)] p-4 shadow-deep md:static md:max-h-none md:w-80 md:shrink-0 md:border-l md:border-t-0 md:shadow-none"
    >
      <div className="mb-2 flex justify-end">
        <Button variant="ghost" size="icon" onClick={onClose} aria-label={t('agentLinks.closeDetails')}>
          <X />
        </Button>
      </div>
      {children}
    </aside>
  )
}

function LinkDetails({ link, nameOf, onEdit, onDelete }: {
  link: AgentLink
  nameOf: (id: string) => string
  onEdit: () => void
  onDelete: () => void
}) {
  const { t } = useLocale()
  return (
    <div className="space-y-3" data-testid="agent-link-details">
      <h2 className="break-words text-body font-semibold text-[var(--color-foreground)]">
        {t('agentLinks.linkDetailsTitle', { from: nameOf(link.from_agent_id), to: nameOf(link.to_agent_id) })}
      </h2>
      <div>
        <p className="text-badge uppercase tracking-wider text-[var(--color-foreground-muted)]">{t('agentLinks.whenToAsk')}</p>
        <p className="mt-1 whitespace-pre-wrap break-words text-caption text-[var(--color-foreground)]">{link.when_to_ask}</p>
      </div>
      <div className="flex flex-wrap gap-2">
        <Button variant="outline" size="sm" onClick={onEdit}><Pencil /> {t('agentLinks.edit')}</Button>
        <Button variant="ghost" size="sm" onClick={onDelete} className="text-[var(--color-danger)]"><Trash2 /> {t('agentLinks.delete')}</Button>
      </div>
    </div>
  )
}

function AgentDetails({ agent, links, nameOf, onSelectLink }: {
  agent: LinkAgent
  links: AgentLink[]
  nameOf: (id: string) => string
  onSelectLink: (id: string) => void
}) {
  const { t } = useLocale()
  const reachable = [...reachableFrom(agent.id, links).agents].filter(id => id !== agent.id).map(nameOf).sort()
  const outgoing = links.filter(link => link.from_agent_id === agent.id)
  const incoming = links.filter(link => link.to_agent_id === agent.id)
  const linkRow = (link: AgentLink, otherId: string) => (
    <li key={link.id}>
      <button
        type="button"
        onClick={() => onSelectLink(link.id)}
        className="w-full rounded-[var(--radius-sm)] px-2 py-1.5 text-left hover:bg-[var(--color-surface-hover)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-brand-focus)]"
      >
        <span className="block text-caption font-medium text-[var(--color-foreground)]">{nameOf(otherId)}</span>
        <span className="block break-words text-badge text-[var(--color-foreground-muted)]">{link.when_to_ask}</span>
      </button>
    </li>
  )
  return (
    <div className="space-y-4" data-testid="agent-reach-details">
      <div>
        <h2 className="break-words text-body font-semibold text-[var(--color-foreground)]">{agent.name}</h2>
        {agent.description && <p className="mt-1 text-caption text-[var(--color-foreground-muted)]">{agent.description}</p>}
      </div>
      <div>
        <h3 className="text-caption font-semibold text-[var(--color-foreground)]">{t('agentLinks.reachTitle', { name: agent.name })}</h3>
        {reachable.length === 0 ? (
          <p className="mt-1 text-caption text-[var(--color-foreground-muted)]">{t('agentLinks.reachNone', { name: agent.name })}</p>
        ) : (
          <>
            <ul className="mt-1 flex flex-wrap gap-1.5" data-testid="agent-reach-list">
              {reachable.map(name => (
                <li key={name} className="rounded-[var(--radius-pill)] border border-[var(--color-brand-text)] px-2 py-0.5 text-badge text-[var(--color-brand-text)]">{name}</li>
              ))}
            </ul>
            <p className="mt-2 text-badge text-[var(--color-foreground-muted)]">{t('agentLinks.reachHint', { name: agent.name })}</p>
          </>
        )}
      </div>
      <div>
        <h3 className="text-badge uppercase tracking-wider text-[var(--color-foreground-muted)]">{t('agentLinks.outgoing')}</h3>
        {outgoing.length === 0
          ? <p className="mt-1 text-caption text-[var(--color-foreground-subtle)]">{t('agentLinks.none')}</p>
          : <ul className="mt-1 space-y-0.5">{outgoing.map(link => linkRow(link, link.to_agent_id))}</ul>}
      </div>
      <div>
        <h3 className="text-badge uppercase tracking-wider text-[var(--color-foreground-muted)]">{t('agentLinks.incoming')}</h3>
        {incoming.length === 0
          ? <p className="mt-1 text-caption text-[var(--color-foreground-subtle)]">{t('agentLinks.none')}</p>
          : <ul className="mt-1 space-y-0.5">{incoming.map(link => linkRow(link, link.from_agent_id))}</ul>}
      </div>
    </div>
  )
}

