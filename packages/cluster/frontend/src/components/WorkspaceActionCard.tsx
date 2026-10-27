/**
 * WorkspaceActionCard — an agent's workspace change waiting for a person
 * (#938). The agent's message carries ``metadata.workspace_action_request``;
 * the card reads the request, and the person who asked (or an admin, the
 * only approver for admin-only actions) approves or rejects it here.
 *
 * WorkspaceActionResultNote renders the server's report back to the agent
 * as one quiet line and tells any card for the same request to refresh.
 */
import { useCallback, useEffect, useState } from 'react'
import { CheckCircle2, CircleSlash, Clock, ShieldQuestion, XCircle } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { useLocale } from '@/i18n/LocaleProvider'
import type { MessageKey } from '@/i18n/messages'
import {
  decideWorkspaceAction,
  getWorkspaceAction,
  type WorkspaceAction,
  type WorkspaceActionRequestMeta,
  type WorkspaceActionResultMeta,
  type WorkspaceActionStatus,
} from '@/lib/workspaceActions'

const DECIDED_EVENT = 'anygarden:workspace-action:decided'

const STATUS: Record<WorkspaceActionStatus, { label: MessageKey; className: string; Icon: typeof Clock }> = {
  pending: { label: 'chat.workspaceAction.pending', className: 'text-[var(--color-warning)]', Icon: Clock },
  executing: { label: 'chat.workspaceAction.executing', className: 'text-[var(--color-foreground-muted)]', Icon: Clock },
  executed: { label: 'chat.workspaceAction.executed', className: 'text-[var(--color-success)]', Icon: CheckCircle2 },
  rejected: { label: 'chat.workspaceAction.rejected', className: 'text-[var(--color-foreground-muted)]', Icon: CircleSlash },
  expired: { label: 'chat.workspaceAction.expired', className: 'text-[var(--color-foreground-muted)]', Icon: CircleSlash },
  failed: { label: 'chat.workspaceAction.failed', className: 'text-[var(--color-danger)]', Icon: XCircle },
}

function StatusLabel({ status }: { status: WorkspaceActionStatus }) {
  const { t } = useLocale()
  const { label, className, Icon } = STATUS[status] ?? STATUS.pending
  return (
    <span className={`inline-flex items-center gap-1 text-badge font-medium ${className}`} data-testid="workspace-action-status">
      <Icon className="h-3.5 w-3.5" aria-hidden />
      {t(label)}
    </span>
  )
}

export default function WorkspaceActionCard({ meta, fallbackText }: { meta: WorkspaceActionRequestMeta; fallbackText: string }) {
  const { t } = useLocale()
  const [request, setRequest] = useState<WorkspaceAction | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    try {
      setRequest(await getWorkspaceAction(meta.request_id))
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }, [meta.request_id])

  useEffect(() => {
    void load()
    const onDecided = (e: Event) => {
      if ((e as CustomEvent<string>).detail === meta.request_id) void load()
    }
    window.addEventListener(DECIDED_EVENT, onDecided)
    return () => window.removeEventListener(DECIDED_EVENT, onDecided)
  }, [load, meta.request_id])

  const decide = async (decision: 'approve' | 'reject') => {
    setBusy(true)
    setError(null)
    try {
      setRequest(await decideWorkspaceAction(meta.request_id, decision))
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      void load()
    }
    setBusy(false)
  }

  return (
    <div
      data-testid="workspace-action-card"
      className="flex w-full max-w-xl flex-col gap-2 rounded-[var(--radius-md)] border border-[var(--color-border)] bg-[var(--color-surface)] p-3 text-sm shadow-whisper"
    >
      <div className="flex items-center justify-between gap-2">
        <span className="inline-flex items-center gap-1.5 text-caption font-semibold text-[var(--color-foreground)]">
          <ShieldQuestion className="h-4 w-4 text-[var(--color-brand-text)]" aria-hidden />
          {t('chat.workspaceAction.title')}
        </span>
        {request && <StatusLabel status={request.status} />}
      </div>
      <p className="whitespace-pre-wrap break-words text-[var(--color-foreground)]">
        {request?.summary ?? fallbackText}
      </p>
      {request && (
        // The summary is a preview; approval runs these exact values.
        <details open={request.status === 'pending'} className="text-caption">
          <summary className="cursor-pointer text-[var(--color-foreground-muted)]">{t('chat.workspaceAction.details')}</summary>
          <pre data-testid="workspace-action-params" className="mt-1 max-h-64 overflow-auto whitespace-pre-wrap break-words rounded-[var(--radius-sm)] bg-[var(--color-surface-alt)] p-2 text-[var(--color-foreground)]">
            {JSON.stringify(request.params, null, 2)}
          </pre>
        </details>
      )}
      {request?.status === 'pending' && request.admin_only && (
        <p className="text-caption text-[var(--color-foreground-muted)]">{t('chat.workspaceAction.adminOnly')}</p>
      )}
      {request?.error && <p className="text-caption text-[var(--color-danger)]">{request.error}</p>}
      {error && <p role="alert" className="text-caption text-[var(--color-danger)]">{error}</p>}
      {request?.status === 'pending' && request.can_decide && (
        <div className="flex gap-2">
          <Button type="button" size="sm" disabled={busy} onClick={() => void decide('approve')}>
            {t('chat.workspaceAction.approve')}
          </Button>
          <Button type="button" size="sm" variant="outline" disabled={busy} onClick={() => void decide('reject')}>
            {t('chat.workspaceAction.reject')}
          </Button>
        </div>
      )}
    </div>
  )
}

export function WorkspaceActionResultNote({ meta }: { meta: WorkspaceActionResultMeta }) {
  useEffect(() => {
    window.dispatchEvent(new CustomEvent(DECIDED_EVENT, { detail: meta.request_id }))
  }, [meta.request_id, meta.status])
  return (
    <div data-testid="workspace-action-result" className="inline-flex items-center gap-2 rounded-[var(--radius-md)] border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-1.5 shadow-whisper">
      <StatusLabel status={meta.status} />
    </div>
  )
}
