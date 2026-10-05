import { useState } from 'react'
import { Link } from 'react-router-dom'
import { ChevronRight } from 'lucide-react'
import { Button } from '@/components/ui/button'
import AuthenticatedArtifactLink from '@/components/AuthenticatedArtifactLink'
import { DispositionNotice, TransmissionNotice } from '@/components/ExecutionState'
import { useExecutionApprovals, type ApprovalDecision, type ExecutionApproval } from '@/hooks/useExecutionApprovals'
import { useLocale } from '@/i18n/LocaleProvider'

type Decide = ReturnType<typeof useExecutionApprovals>['decide']

export function ApprovalCard({ approval, decide, onNavigate, taskHref, sourceTaskHref, compact = false, expanded = false }: { approval: ExecutionApproval; decide: Decide; onNavigate?: () => void; taskHref?: string; sourceTaskHref?: string; compact?: boolean; expanded?: boolean }) {
  const { t, formatDate } = useLocale()
  const [sending, setSending] = useState<ApprovalDecision | null>(null)
  const [error, setError] = useState<string | null>(null)
  const pending = approval.status === 'pending' && approval.is_current === true
  const status = approval.status === 'pending' && !pending ? t('executionApprovals.inactive') : t(`executionApprovals.status.${approval.status}`)
  const action = approval.action_kind === 'submission' ? t('executionApprovals.submitArtifact') : approval.action_kind === 'deployment' ? t('executionApprovals.deployArtifact') : approval.action_kind
  const timestamp = approval.finished_at ?? approval.executed_at ?? approval.decided_at ?? approval.created_at
  async function submit(decision: ApprovalDecision) {
    if (sending || !approval.can_decide || !pending) return
    setSending(decision)
    setError(null)
    try { const result = await decide(approval.id, decision); setError(result.error) } finally { setSending(null) }
  }
  const content = <>
    <Link to={taskHref ?? `/rooms/${approval.task_room_id}`} onClick={onNavigate} className="flex min-h-11 items-center text-sm text-[var(--color-brand-text)] underline">{approval.task_title}</Link>
    {(approval.task_room_name || approval.assignee_display_name) && <p className="mb-2 text-sm text-[var(--color-foreground-muted)]">{[approval.task_room_name, approval.assignee_display_name].filter(Boolean).join(' · ')}</p>}
    <dl className="space-y-2 text-sm">
      <div><dt className="font-semibold">{t('executionApprovals.target')}</dt><dd>{approval.target_label} ({approval.target_alias})</dd><dd className="break-all">{approval.target_url}</dd></div>
      <div><dt className="font-semibold">{t('executionApprovals.action')}</dt><dd>{action}</dd></div>
      <div><dt className="font-semibold">{t('executionApprovals.effect')}</dt><dd className="whitespace-pre-wrap">{approval.summary}</dd></div>
      <div><dt className="font-semibold">{t('executionApprovals.file')}</dt><dd>{approval.artifact_accessible === false ? <>{approval.artifact_filename}<p className="text-[var(--color-foreground-muted)]">{t('inbox.fileUnavailable')}</p></> : <AuthenticatedArtifactLink href={approval.artifact_url} className="inline-flex min-h-11 items-center text-[var(--color-brand-text)] underline">{approval.artifact_filename}</AuthenticatedArtifactLink>}</dd></div>
      <div><dt className="font-semibold">{t('executionApprovals.version')}</dt><dd>{t('executionApprovals.versionValue', { input: approval.input_revision, result: approval.source_result_version })}</dd>{approval.source_task_title && approval.source_task_room_id && <dd><Link to={sourceTaskHref ?? `/rooms/${approval.source_task_room_id}`} onClick={onNavigate} className="flex min-h-11 items-center text-[var(--color-brand-text)] underline">{approval.source_task_title}{approval.source_task_room_name && ` · ${approval.source_task_room_name}`}</Link></dd>}</div>
    </dl>
    <details className="mt-2 text-xs text-[var(--color-foreground-muted)]"><summary className="min-h-11 cursor-pointer content-center">{t('executionApprovals.hashes')}</summary><p className="break-all">{t('executionApprovals.fileHash')}: {approval.artifact_sha256}</p><p className="break-all">{t('executionApprovals.resultHash')}: {approval.source_result_sha256}</p></details>
    {approval.source_message_id && <Link to={`/rooms/${approval.operating_room_id}?message=${encodeURIComponent(approval.source_message_id)}`} onClick={onNavigate} className="flex min-h-11 items-center text-sm text-[var(--color-brand-text)] underline">{t('tasks.openOriginalRequest')}</Link>}
    {approval.decision && <p className="mt-2 text-sm">{t('executionApprovals.decision')}: {approval.decision === 'approve' ? t('executionApprovals.approve') : t('executionApprovals.reject')}{approval.decided_at && <> · <time dateTime={approval.decided_at}>{formatDate(new Date(approval.decided_at), { dateStyle: 'medium', timeStyle: 'short' })}</time></>}</p>}
    {approval.receipt?.http_status != null && <p className="mt-2 text-sm">{t('executionApprovals.response', { status: approval.receipt.http_status })}</p>}
    {pending && approval.can_decide ? <div className="mt-2 flex flex-wrap gap-2">
      <Button type="button" className="min-h-11 flex-1" disabled={!!sending} onClick={() => { void submit('approve') }} data-testid={`execution-approval-approve-${approval.id}`}>{sending === 'approve' ? t('common.loading') : t('executionApprovals.approve')}</Button>
      <Button type="button" variant="outline" className="min-h-11 flex-1" disabled={!!sending} onClick={() => { void submit('reject') }} data-testid={`execution-approval-reject-${approval.id}`}>{sending === 'reject' ? t('common.loading') : t('executionApprovals.reject')}</Button>
    </div> : pending && <p className="mt-2 text-sm text-[var(--color-foreground-muted)]">{t('executionApprovals.readOnly')}</p>}
    {approval.status === 'pending' && !pending && <p className="mt-2 text-sm text-[var(--color-warning)]">{t('executionApprovals.stale')}</p>}
    {(error || approval.error) && <p role="alert" className="mt-2 whitespace-pre-wrap text-sm text-[var(--color-danger)]">{error || approval.error}</p>}
    <time dateTime={timestamp} className="mt-2 block text-xs text-[var(--color-foreground-muted)]">{formatDate(new Date(timestamp), { dateStyle: 'medium', timeStyle: 'short' })}</time>
  </>
  return <article data-testid={`execution-approval-${approval.id}`} className="min-w-0 rounded-[var(--radius-sm)] border border-[var(--color-border)] bg-[var(--color-surface)] p-2 text-sm [overflow-wrap:anywhere]">
    <DispositionNotice disposition={approval.disposition} inputRevision={approval.input_revision} operationAction={approval.execution_operation_action} />
    <TransmissionNotice approval={approval} />
    {pending ? <><p className="mb-1 font-semibold text-[var(--color-warning)]">{status}</p>{compact ? <details className="group/summary">
      <summary className="flex min-h-11 cursor-pointer list-none items-start gap-2 py-2 text-sm font-medium [&::-webkit-details-marker]:hidden"><ChevronRight aria-hidden="true" className="mt-0.5 h-4 w-4 shrink-0 group-open/summary:rotate-90" /><span className="min-w-0 flex-1"><span className="line-clamp-2">{approval.task_title}</span><span className="block text-sm font-normal text-[var(--color-foreground-muted)]">{approval.target_label || approval.target_alias} · {approval.artifact_filename}</span></span></summary>
      <div className="pt-2">{content}</div>
    </details> : content}</> : <details open={expanded || undefined}><summary className="min-h-11 cursor-pointer content-center font-medium">{status}: {approval.artifact_filename}</summary>{content}</details>}
  </article>
}

export default function ExecutionApprovalsSection({ roomId, onNavigate }: { roomId: string; onNavigate?: () => void }) {
  const { t } = useLocale()
  const { approvals, error, decide } = useExecutionApprovals(roomId)
  if (approvals.length === 0 && !error) return null
  const pending = approvals.filter(approval => approval.status === 'pending' && approval.is_current === true)
  const history = approvals.filter(approval => !pending.includes(approval))
  return <section data-testid="execution-approvals-section" className="min-w-0 space-y-2 border-b border-[var(--color-border)] px-3 py-2">
    <h3 className="text-sm font-semibold">{t('executionApprovals.title')} · {pending.length}</h3>
    {error && <p role="alert" className="text-sm text-[var(--color-danger)]">{error}</p>}
    {pending.map(approval => <ApprovalCard key={`${roomId}:${approval.id}`} compact approval={approval} decide={decide} onNavigate={onNavigate} />)}
    {history.length > 0 && <details><summary className="min-h-11 cursor-pointer content-center text-sm text-[var(--color-foreground-muted)]">{t('contextRail.approvalHistory')} ({history.length})</summary><div className="space-y-2">{history.map(approval => <ApprovalCard key={`${roomId}:${approval.id}`} approval={approval} decide={decide} onNavigate={onNavigate} />)}</div></details>}
  </section>
}
