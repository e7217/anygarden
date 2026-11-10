/**
 * AgentLinksPanel — "Agents this agent can ask" (#1006).
 *
 * The list form of the agent-link map for one agent's outgoing links:
 * the keyboard and narrow-screen path. Adding or editing opens the same
 * dialog the map uses.
 */
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Pencil, Plus, Trash2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import LinkEditorDialog, { type LinkEditorTarget } from '@/components/agent-links/LinkEditorDialog'
import { useFeedback } from '@/components/feedback/FeedbackProvider'
import { useAgentLinks } from '@/hooks/useAgentLinks'
import { useLocale } from '@/i18n/LocaleProvider'
import type { AgentLink } from '@/lib/agentLinks'

interface Props {
  agentId: string
  agentName: string
  /** Close the surrounding dialog before leaving for the map page. */
  onNavigateAway?: () => void
}

export default function AgentLinksPanel({ agentId, agentName, onNavigateAway }: Props) {
  const { t } = useLocale()
  const navigate = useNavigate()
  const { confirm, notify } = useFeedback()
  const { graph, loading, errorStatus, refresh, create, update, remove } = useAgentLinks()
  const [editor, setEditor] = useState<LinkEditorTarget | null>(null)

  const agents = graph?.agents ?? []
  const links = graph?.links ?? []
  const nameOf = (id: string) => agents.find(agent => agent.id === id)?.name ?? id.slice(0, 8)
  const outgoing = links
    .filter(link => link.from_agent_id === agentId)
    .sort((a, b) => nameOf(a.to_agent_id).localeCompare(nameOf(b.to_agent_id)))
  const canAdd = agents.some(agent => agent.id !== agentId)

  async function askDelete(link: AgentLink) {
    const to = nameOf(link.to_agent_id)
    const ok = await confirm({
      title: t('agentLinks.deleteTitle'),
      description: t('agentLinks.deleteDescription', { from: agentName, to }),
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
  }

  return (
    <div className="space-y-3" data-testid="agent-links-panel">
      <p className="text-caption text-[var(--color-foreground-muted)]">{t('agentLinks.settingsHint', { name: agentName })}</p>

      {loading && !graph && <p role="status" className="text-caption text-[var(--color-foreground-muted)]">{t('agentLinks.loading')}</p>}
      {errorStatus !== null && (
        <div className="flex flex-wrap items-center gap-3">
          <p role="alert" className="text-caption text-[var(--color-danger)]">{t('agentLinks.loadFailed', { status: errorStatus })}</p>
          <Button variant="outline" size="sm" onClick={() => void refresh()}>{t('common.retry')}</Button>
        </div>
      )}

      {graph && (outgoing.length === 0 ? (
        <p className="text-caption text-[var(--color-foreground-subtle)]">{t('agentLinks.settingsEmpty', { name: agentName })}</p>
      ) : (
        <ul className="space-y-2">
          {outgoing.map(link => {
            const to = nameOf(link.to_agent_id)
            return (
              <li key={link.id} className="flex items-start gap-2 rounded-[var(--radius-md)] border border-[var(--color-border)] bg-[var(--color-surface-alt)] px-3 py-2">
                <div className="min-w-0 flex-1">
                  <p className="truncate text-caption font-medium text-[var(--color-foreground)]">{to}</p>
                  <p className="whitespace-pre-wrap break-words text-caption text-[var(--color-foreground-muted)]">{link.when_to_ask}</p>
                </div>
                <Button variant="ghost" size="icon" onClick={() => setEditor({ kind: 'edit', link })} aria-label={t('agentLinks.editNamed', { to })} title={t('agentLinks.edit')}>
                  <Pencil />
                </Button>
                <Button variant="ghost" size="icon" onClick={() => void askDelete(link)} aria-label={t('agentLinks.deleteNamed', { to })} title={t('agentLinks.delete')}>
                  <Trash2 className="text-[var(--color-danger)]" />
                </Button>
              </li>
            )
          })}
        </ul>
      ))}

      {graph && (
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="outline" size="sm" disabled={!canAdd} onClick={() => setEditor({ kind: 'create', fromId: agentId, lockFrom: true })}>
            <Plus /> {t('agentLinks.addLink')}
          </Button>
          <Button variant="link" size="sm" onClick={() => { onNavigateAway?.(); navigate('/agents/links') }}>
            {t('agentLinks.openMap')}
          </Button>
          {!canAdd && <p className="w-full text-caption text-[var(--color-foreground-subtle)]">{t('agentLinks.noOtherAgents')}</p>}
        </div>
      )}

      <LinkEditorDialog
        target={editor}
        onClose={() => setEditor(null)}
        agents={agents}
        links={links}
        onCreate={create}
        onUpdate={update}
      />
    </div>
  )
}
