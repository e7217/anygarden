/**
 * InstructionHistoryDialog — #908. Lists the saved versions of an agent's
 * instructions (AGENTS.md and skills) and restores one.
 *
 * Agents may edit their own instructions, so each version shows who made
 * it. Restoring records a new version; nothing is lost.
 */
import { useEffect, useState } from 'react'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { useFeedback } from '@/components/feedback/FeedbackProvider'
import type { InstructionRevision } from '@/hooks/useAgents'
import { useLocale } from '@/i18n/LocaleProvider'

type LoadState = { kind: 'loading' } | { kind: 'error' } | { kind: 'ready'; revisions: InstructionRevision[] }

export default function InstructionHistoryDialog({
  open, onOpenChange, agentId, currentRevision, readOnly, fetchRevisions, restoreRevision,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  agentId: string
  currentRevision: number
  /** True while the agent's machine is offline; restoring needs it online. */
  readOnly: boolean
  fetchRevisions: (agentId: string) => Promise<InstructionRevision[]>
  restoreRevision: (agentId: string, revision: number) => Promise<unknown>
}) {
  const { t, formatDate } = useLocale()
  const { confirm, notify } = useFeedback()
  const [state, setState] = useState<LoadState>({ kind: 'loading' })
  const [selected, setSelected] = useState<number | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    if (!open) return
    let active = true
    setState({ kind: 'loading' }); setError('')
    fetchRevisions(agentId)
      .then(revisions => {
        if (!active) return
        setState({ kind: 'ready', revisions })
        setSelected(revisions[0]?.revision ?? null)
      })
      .catch(() => { if (active) setState({ kind: 'error' }) })
    return () => { active = false }
  }, [open, agentId, fetchRevisions])

  const revisions = state.kind === 'ready' ? state.revisions : []
  const chosen = revisions.find(revision => revision.revision === selected) ?? null

  const restore = async (revision: number) => {
    const ok = await confirm({
      title: t('admin.instructionHistory.confirmTitle', { revision }),
      description: t('admin.instructionHistory.confirmDescription', { revision }),
      confirmLabel: t('admin.instructionHistory.restore'),
    })
    if (!ok) return
    setBusy(true); setError('')
    try {
      await restoreRevision(agentId, revision)
      notify({ message: t('admin.instructionHistory.restored', { revision }), tone: 'success' })
      onOpenChange(false)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : t('admin.instructionHistory.restoreFailed'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={next => { if (!busy) onOpenChange(next) }}>
      <DialogContent className="max-w-3xl">
        <DialogHeader>
          <DialogTitle>{t('admin.instructionHistory.title')}</DialogTitle>
          <DialogDescription>{t('admin.instructionHistory.description')}</DialogDescription>
        </DialogHeader>
        {state.kind === 'loading' && <p className="text-sm text-[var(--color-foreground-muted)]">{t('admin.instructionHistory.loading')}</p>}
        {state.kind === 'error' && <p role="alert" className="text-sm text-[var(--color-danger)]">{t('admin.instructionHistory.loadFailed')}</p>}
        {state.kind === 'ready' && revisions.length === 0 && (
          <p className="text-sm text-[var(--color-foreground-muted)]" data-testid="instruction-history-empty">{t('admin.instructionHistory.empty')}</p>
        )}
        {revisions.length > 0 && (
          <div className="grid min-w-0 grid-cols-1 gap-3 sm:grid-cols-[minmax(0,14rem)_minmax(0,1fr)]">
            <ul className="max-h-72 min-w-0 overflow-y-auto rounded-[var(--radius-md)] border border-[var(--color-border)]" data-testid="instruction-history-list">
              {revisions.map(revision => (
                <li key={revision.revision}>
                  <button
                    type="button"
                    onClick={() => setSelected(revision.revision)}
                    aria-current={revision.revision === selected}
                    className={`flex w-full min-h-[var(--control-height)] flex-col items-start gap-0.5 px-3 py-2 text-left text-sm hover:bg-[var(--color-surface-hover)] ${
                      revision.revision === selected ? 'bg-[var(--color-surface-selected)]' : ''
                    }`}
                  >
                    <span className="flex flex-wrap items-center gap-1.5">
                      <span className="font-medium text-[var(--color-foreground)]">{t('admin.instructionHistory.revision', { revision: revision.revision })}</span>
                      <Badge variant="outline" data-testid={`instruction-source-${revision.revision}`}>{t(`admin.instructionHistory.source.${revision.source}`)}</Badge>
                      {revision.revision === currentRevision && <Badge variant="outline">{t('admin.instructionHistory.current')}</Badge>}
                    </span>
                    <span className="text-caption text-[var(--color-foreground-muted)]">
                      {formatDate(new Date(revision.created_at), { dateStyle: 'medium', timeStyle: 'short' })}
                      {revision.restored_from != null && ` · ${t('admin.instructionHistory.restoredFrom', { revision: revision.restored_from })}`}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
            {chosen && (
              <div className="min-w-0 space-y-2" data-testid="instruction-history-preview">
                <pre className="max-h-56 overflow-auto whitespace-pre-wrap break-words rounded-[var(--radius-md)] border border-[var(--color-border)] bg-[var(--color-surface-alt)] p-3 font-mono text-xs text-[var(--color-foreground)]">
                  {chosen.agents_md || t('admin.instructionHistory.noInstructions')}
                </pre>
                <p className="text-caption text-[var(--color-foreground-muted)]">
                  {t('admin.instructionHistory.skills', { count: Object.keys(chosen.skills).length })}
                  {Object.keys(chosen.skills).length > 0 && `: ${Object.keys(chosen.skills).join(', ')}`}
                </p>
                {chosen.revision !== currentRevision && (
                  <Button
                    variant="outline"
                    onClick={() => void restore(chosen.revision)}
                    disabled={busy || readOnly}
                    data-testid="instruction-history-restore"
                  >
                    {busy ? t('admin.instructionHistory.restoring') : t('admin.instructionHistory.restore')}
                  </Button>
                )}
              </div>
            )}
          </div>
        )}
        {error && <p role="alert" className="text-sm text-[var(--color-danger)]">{error}</p>}
      </DialogContent>
    </Dialog>
  )
}
