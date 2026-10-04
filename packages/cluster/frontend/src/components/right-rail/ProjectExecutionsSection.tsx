import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { DispositionNotice, ExecutionUsageHistory, ExecutionUsageState, executionLimitReasonKey, TransmissionNotice } from '@/components/ExecutionState'
import { useAuth } from '@/hooks/useAuth'
import { useProjectExecutions, type ExecutionDetail, type ProjectExecution, type StopSummary } from '@/hooks/useProjectExecutions'
import type { RoomSharedFile } from '@/lib/roomFiles'
import { useLocale } from '@/i18n/LocaleProvider'
import type { MessageKey } from '@/i18n/messages'

type ExecutionApi = ReturnType<typeof useProjectExecutions>
const ACTIVE = new Set(['planning', 'running', 'waiting_children'])
const STATUS: Record<string, MessageKey> = Object.fromEntries(['planning', 'running', 'waiting_children', 'revising', 'cancelling', 'cancelled', 'blocked', 'completed', 'failed', 'superseded'].map(status => [status, `executions.status.${status}`])) as Record<string, MessageKey>
const fields = 'w-full min-w-0 rounded-[var(--radius-sm)] border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-base focus:outline-none focus:ring-2 focus:ring-[var(--color-brand-text)]'

function StopState({ summary }: { summary?: StopSummary | null }) {
  const { t } = useLocale()
  if (!summary?.total) return null
  return <div data-testid="execution-stop-summary" className="space-y-1 text-sm">
    {summary.pending > 0 && <p>{t('executions.stop.pending', { count: summary.pending })}</p>}
    {summary.confirmed > 0 && <p>{t('executions.stop.confirmed', { count: summary.confirmed })}</p>}
    {summary.not_started > 0 && <p>{t('executions.stop.notStarted', { count: summary.not_started })}</p>}
    {summary.already_finished > 0 && <p>{t('executions.stop.finished', { count: summary.already_finished })}</p>}
    {summary.unknown > 0 && <p className="text-[var(--color-warning)]">{t('executions.stop.unknown', { count: summary.unknown })}</p>}
    {summary.all_confirmed && <p>{t('executions.stop.allConfirmed')}</p>}
  </div>
}

function ExecutionHistory({ detail }: { detail: ExecutionDetail }) {
  const { t, formatDate } = useLocale()
  const current = detail.execution.input_revision
  const effects = detail.effects ?? detail.approvals ?? []
  return <div className="space-y-3 pt-2 text-sm [overflow-wrap:anywhere]">
    <ExecutionUsageHistory summary={detail.execution.usage_summary} executionId={detail.execution.id} />
    {detail.input_revisions.map(input => <details key={input.revision} data-testid={`execution-input-history-${detail.execution.id}-${input.revision}`}>
      <summary className="min-h-11 cursor-pointer content-center font-medium">{t(input.revision === current ? 'executions.inputVersion' : 'executions.previousInput', { version: input.revision })}</summary>
      <div className="space-y-2 py-2"><p>{input.objective}</p>{input.reason && <p>{t('executions.reason')}: {input.reason}</p>}<p className="whitespace-pre-wrap">{input.user_constraints}</p>{input.completion_criteria.length > 0 && <ul className="list-inside list-disc">{input.completion_criteria.map((criterion, index) => <li key={index}>{criterion}</li>)}</ul>}{input.input_files.map(file => <p key={file.file_id}>{file.filename}<span className="block break-all text-xs text-[var(--color-foreground-muted)]">SHA-256: {file.sha256}</span></p>)}</div>
    </details>)}
    {detail.results.length > 0 && <div className="space-y-2"><p className="font-semibold">{t('executions.resultHistory')}</p>{detail.results.map(result => <div key={result.id} className="space-y-1 border-l-2 border-[var(--color-border)] pl-2"><Link to={`/inbox?item=${encodeURIComponent(`task:${result.task_id}`)}`} className="inline-flex min-h-11 items-center text-[var(--color-brand-text)] underline">{detail.tasks.find(task => task.id === result.task_id)?.title ?? t('tasks.details')}</Link><p>{t('executions.inputVersion', { version: result.input_revision })} · {t('tasks.resultVersion', { version: result.version })}</p><DispositionNotice disposition={result.disposition} inputRevision={result.input_revision} /></div>)}</div>}
    {effects.length > 0 && <div className="space-y-2"><p className="font-semibold">{t('executions.transmissions')}</p>{effects.map(approval => <div key={approval.id} className="space-y-1 border-l-2 border-[var(--color-border)] pl-2" data-testid={`execution-transmission-${approval.id}`}><Link to={`/inbox?item=${encodeURIComponent(`approval:${approval.id}`)}`} className="inline-flex min-h-11 items-center text-[var(--color-brand-text)] underline">{approval.target_label || approval.target_alias} · {approval.artifact_filename}</Link><p>{t('executions.inputVersion', { version: approval.input_revision })} · {t('tasks.resultVersion', { version: approval.source_result_version })}</p><TransmissionNotice approval={approval} />{approval.receipt?.http_status != null && <p>{t('executionApprovals.response', { status: approval.receipt.http_status })}</p>}{(approval.finished_at || approval.executed_at) && <time dateTime={(approval.finished_at || approval.executed_at)!}>{formatDate(new Date((approval.finished_at || approval.executed_at)!), { dateStyle: 'medium', timeStyle: 'short' })}</time>}</div>)}</div>}
  </div>
}

function ExecutionCard({ execution, api, onNavigate }: { execution: ProjectExecution; api: ExecutionApi; onNavigate?: () => void }) {
  const { t } = useLocale()
  const [mode, setMode] = useState<'revise' | 'cancel' | null>(null)
  const [initial, setInitial] = useState<ProjectExecution | null>(null)
  const [objective, setObjective] = useState('')
  const [constraints, setConstraints] = useState('')
  const [criteria, setCriteria] = useState('')
  const [reason, setReason] = useState('')
  const [files, setFiles] = useState<RoomSharedFile[]>([])
  const [selected, setSelected] = useState<string[]>([])
  const [loadingFiles, setLoadingFiles] = useState(false)
  const [filesReady, setFilesReady] = useState(false)
  const [sending, setSending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [detail, setDetail] = useState<ExecutionDetail | null>(null)
  const [historyOpen, setHistoryOpen] = useState(false)
  const mounted = useRef(true)
  const loadSequence = useRef(0)
  const inFlight = useRef(false)
  const operation = useRef<{ fingerprint: string; id: string } | null>(null)
  const deadline = execution.latest_operation?.action === 'deadline'
  const limit = execution.latest_operation?.action === 'limit'
  const deadlineStatus: MessageKey | null = deadline
    ? execution.stop_summary?.unknown ? 'executions.deadline.unknown'
      : execution.latest_operation?.phase === 'applied' && execution.status === 'failed' ? 'executions.deadline.expired'
      : 'executions.deadline.waitingStop'
    : null
  const limitStatus: MessageKey | null = limit
    ? execution.stop_summary?.unknown ? 'executions.limit.unknown'
      : execution.latest_operation?.phase === 'applied' && execution.status === 'failed' ? 'executions.limit.stopped'
      : 'executions.limit.waitingStop'
    : null
  const canManage = !deadline && !limit && execution.can_manage === true && ACTIVE.has(execution.status) && !(execution.stop_summary?.unknown || execution.stop_summary?.pending)
  const changed = initial != null && (initial.input_revision !== execution.input_revision || initial.state_revision !== execution.state_revision)
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; ++loadSequence.current } }, [])
  // Losing permission also removes a previously opened draft from the DOM.
  useEffect(() => { if (!execution.can_manage) { setMode(null); ++loadSequence.current } }, [execution.can_manage])
  useEffect(() => {
    if (!historyOpen) return
    let active = true
    void api.detail(execution.id).then(value => { if (active) setDetail(value) }).catch(cause => { if (active) setError(cause instanceof Error ? cause.message : 'HTTP 500') })
    return () => { active = false }
  }, [api.detail, execution.id, execution.input_revision, execution.state_revision, execution.usage_summary?.as_of, historyOpen])
  async function open(next: 'revise' | 'cancel') {
    const sequence = ++loadSequence.current
    setInitial(execution)
    setObjective(execution.objective)
    setConstraints(execution.current_constraints ?? '')
    setCriteria(execution.completion_criteria.join('\n'))
    setReason('')
    setError(null)
    setSelected([])
    setFiles([])
    operation.current = null
    setMode(next)
    setLoadingFiles(true)
    setFilesReady(false)
    try {
      const [rows, latestDetail] = await Promise.all([next === 'revise' ? api.files() : Promise.resolve([]), api.detail(execution.id)])
      if (!mounted.current || loadSequence.current !== sequence) return
      setDetail(latestDetail)
      setFiles(rows)
      setFilesReady(true)
      setSelected(rows.filter(file => execution.current_input_files?.some(previous => previous.file_id === file.id && previous.sha256 === file.sha256)).map(file => file.id))
    } catch (cause) {
      if (mounted.current && loadSequence.current === sequence) setError(cause instanceof Error ? cause.message : 'HTTP 500')
    } finally { if (mounted.current && loadSequence.current === sequence) setLoadingFiles(false) }
  }
  async function submit() {
    if (!initial || !mode || inFlight.current || !canManage || changed || loadingFiles || !filesReady || !reason.trim()) return
    if (mode === 'revise' && !objective.trim()) return
    const body = {
      expected_input_revision: initial.input_revision, expected_state_revision: initial.state_revision, reason: reason.trim(),
      ...(mode === 'revise' ? { objective: objective.trim(), constraints: constraints.trim(), completion_criteria: criteria.split('\n').map(value => value.trim()).filter(Boolean), input_files: files.filter(file => selected.includes(file.id)).map(file => ({ file_id: file.id, sha256: file.sha256 })), change_scope: 'all' as const } : {}),
    }
    const fingerprint = JSON.stringify({ mode, ...body })
    if (operation.current?.fingerprint !== fingerprint) operation.current = { fingerprint, id: crypto.randomUUID() }
    inFlight.current = true
    setSending(true)
    setError(null)
    const result = await api.mutate(execution.id, mode === 'revise' ? 'input-revisions' : 'cancel', { ...body, operation_id: operation.current.id })
    inFlight.current = false
    if (!mounted.current) return
    setSending(false)
    if (result.detail) { setDetail(result.detail); setMode(null); ++loadSequence.current }
    else setError(result.error)
  }
  const previousFilesChanged = !loadingFiles && !!initial?.current_input_files?.some(previous => !files.some(file => file.id === previous.file_id && file.sha256 === previous.sha256))
  return <article data-testid={`project-execution-${execution.id}`} className="min-w-0 space-y-2 rounded-[var(--radius-sm)] border border-[var(--color-border)] bg-[var(--color-surface)] p-3 text-sm [overflow-wrap:anywhere]">
    <p className="line-clamp-2 font-semibold" title={execution.objective}>{execution.objective}</p>
    <p data-testid={`execution-status-${execution.id}`} data-execution-action={execution.latest_operation?.action} className="text-[var(--color-foreground-muted)]">{deadlineStatus ? t(deadlineStatus) : limitStatus ? t(limitStatus) : STATUS[execution.status] ? t(STATUS[execution.status]) : execution.status} · {t('executions.inputVersion', { version: execution.input_revision })}</p>
    <details onToggle={event => setHistoryOpen(event.currentTarget.open)}>
      <summary className="min-h-11 cursor-pointer content-center text-[var(--color-brand-text)]">{t('contextRail.details')}</summary>
      <div className="min-w-0 space-y-3 pt-2">
        <p className="whitespace-pre-wrap">{execution.objective}</p>
    {execution.source_message_id && <Link to={`/rooms/${execution.operating_room_id}?message=${encodeURIComponent(execution.source_message_id)}`} onClick={onNavigate} className="inline-flex min-h-11 items-center text-[var(--color-brand-text)] underline">{t('tasks.openOriginalRequest')}</Link>}
    {!deadline && !limit && execution.latest_operation?.phase === 'awaiting_stop' && !execution.stop_summary?.unknown && <p className="text-[var(--color-warning)]">{t('executions.waitingStop')}</p>}
    <StopState summary={execution.stop_summary} />
    {execution.error && <p className="whitespace-pre-wrap text-[var(--color-warning)]">{execution.error === 'EXECUTION_DEADLINE_REACHED' ? t('executions.deadline.reason') : limit || ['EXECUTION_NATIVE_INVOCATION_LIMIT', 'EXECUTION_USAGE_LIMIT_REACHED', 'EXECUTION_USAGE_ACCOUNTING_INCOMPLETE'].includes(execution.error) ? t(executionLimitReasonKey(execution.error)) : execution.error}</p>}
    <ExecutionUsageState summary={execution.usage_summary} limits={execution.limits} executionId={execution.id} />
    {canManage && <div className="flex flex-wrap gap-2"><Button type="button" variant="outline" className="min-h-11 flex-1" data-testid={`execution-revise-${execution.id}`} onClick={() => { void open('revise') }}>{t('executions.change')}</Button><Button type="button" variant="outline" className="min-h-11 flex-1 text-[var(--color-danger)]" data-testid={`execution-cancel-${execution.id}`} onClick={() => { void open('cancel') }}>{t('executions.cancel')}</Button></div>}
    <p className="font-medium">{t('executions.details')}</p>{detail && <ExecutionHistory detail={detail} />}
    {execution.latest_operation && <p className="text-[var(--color-foreground-muted)]">{t('executions.effect.unobserved')}</p>}
      </div>
    </details>
    {error && !mode && <p role="alert" className="text-[var(--color-danger)]">{error}</p>}
    <Dialog open={mode !== null} onOpenChange={value => { if (!value && !sending) { setMode(null); ++loadSequence.current } }}>
      <DialogContent data-testid={`execution-dialog-${execution.id}`} className="max-w-2xl">
        <DialogHeader><DialogTitle>{t(mode === 'revise' ? 'executions.change' : 'executions.cancel')}</DialogTitle><DialogDescription>{t(mode === 'revise' ? 'executions.changeImpact' : 'executions.cancelImpact')}</DialogDescription></DialogHeader>
        <form className="min-w-0 space-y-4" onSubmit={event => { event.preventDefault(); void submit() }}>
          <p className="text-sm">{initial?.objective} · {t('executions.inputVersion', { version: initial?.input_revision ?? execution.input_revision })}</p>
          {mode === 'cancel' && loadingFiles && <p role="status">{t('common.loading')}</p>}
          {(detail?.effects ?? detail?.approvals ?? []).some(approval => ['executing', 'succeeded', 'failed', 'unknown'].includes(approval.status)) && <div className="space-y-2 border-l-2 border-[var(--color-warning)] pl-3 text-sm"><p className="font-semibold">{t('executions.transmissions')}</p>{(detail?.effects ?? detail?.approvals ?? []).filter(approval => ['executing', 'succeeded', 'failed', 'unknown'].includes(approval.status)).map(approval => <div key={approval.id} className="space-y-1"><p>{approval.target_label || approval.target_alias} · {approval.artifact_filename} · {t('tasks.resultVersion', { version: approval.source_result_version })}</p><TransmissionNotice approval={approval} /></div>)}</div>}
          {mode === 'revise' && <>
            <label className="block space-y-1 text-sm"><span>{t('executions.objective')}</span><textarea data-testid="execution-objective" rows={2} required value={objective} onChange={event => setObjective(event.target.value)} disabled={sending} className={fields} /></label>
            <label className="block space-y-1 text-sm"><span>{t('executions.constraints')}</span><textarea data-testid="execution-constraints" rows={4} value={constraints} onChange={event => setConstraints(event.target.value)} disabled={sending} className={fields} /></label>
            <label className="block space-y-1 text-sm"><span>{t('executions.criteria')}</span><textarea data-testid="execution-criteria" rows={3} value={criteria} onChange={event => setCriteria(event.target.value)} disabled={sending} className={fields} /></label>
            <fieldset className="min-w-0 space-y-2"><legend className="text-sm font-semibold">{t('executions.files')}</legend>{loadingFiles ? <p role="status">{t('executions.loadingFiles')}</p> : <>{previousFilesChanged && <p className="text-sm text-[var(--color-warning)]">{t('executions.changedFiles')}</p>}{files.length === 0 && <p className="text-sm text-[var(--color-foreground-muted)]">{t('executions.noFiles')}</p>}{files.map(file => <label key={file.id} className="flex min-h-11 items-start gap-3 text-sm"><input data-testid={`execution-input-file-${file.id}`} type="checkbox" checked={selected.includes(file.id)} disabled={sending} onChange={event => setSelected(previous => event.target.checked ? [...previous, file.id] : previous.filter(id => id !== file.id))} className="mt-1 size-5 shrink-0" /><span className="min-w-0 break-words">{file.filename}<span className="block text-xs text-[var(--color-foreground-muted)]">SHA-256: {file.sha256.slice(0, 12)}…</span></span></label>)}</>}</fieldset>
          </>}
          <label className="block space-y-1 text-sm"><span>{t('executions.reason')}</span><textarea data-testid="execution-reason" rows={2} required value={reason} onChange={event => setReason(event.target.value)} disabled={sending} className={fields} /></label>
          {changed && <p role="alert" className="text-sm text-[var(--color-warning)]">{t('executions.currentChanged')}</p>}
          {error && <p role="alert" className="break-words text-sm text-[var(--color-danger)]">{error}</p>}
          <DialogFooter><Button type="button" variant="outline" className="min-h-11" disabled={sending} onClick={() => { setMode(null); ++loadSequence.current }}>{t('executions.close')}</Button><Button type="submit" className="min-h-11" data-testid="execution-operation-submit" disabled={sending || loadingFiles || !filesReady || changed || !canManage || !reason.trim() || (mode === 'revise' && !objective.trim())}>{sending ? t('common.loading') : t(mode === 'revise' ? 'executions.apply' : 'executions.cancelSubmit')}</Button></DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  </article>
}

export default function ProjectExecutionsSection({ roomId, onNavigate }: { roomId: string; onNavigate?: () => void }) {
  const { user } = useAuth()
  const { t } = useLocale()
  const api = useProjectExecutions(roomId, user?.id ?? null)
  if (api.executions.length === 0 && !api.error) return null
  const active = api.executions.filter(execution => !['completed', 'failed', 'cancelled'].includes(execution.status))
  const history = api.executions.filter(execution => ['completed', 'failed', 'cancelled'].includes(execution.status))
  return <section data-testid="project-executions-section" className="min-w-0 space-y-3 border-b border-[var(--color-border)] px-3 py-3">
    <h3 className="text-sm font-semibold">{t('executions.title')}</h3>
    {api.error && <p role="alert" className="break-words text-sm text-[var(--color-danger)]">{api.error}</p>}
    {active.slice(0, 3).map(execution => <ExecutionCard key={`${roomId}:${user?.id}:${execution.id}`} execution={execution} api={api} onNavigate={onNavigate} />)}
    {active.length > 3 && <details><summary className="min-h-11 cursor-pointer content-center text-sm text-[var(--color-brand-text)]">{t('contextRail.more', { count: active.length })}</summary><div className="space-y-3 pt-2">{active.slice(3).map(execution => <ExecutionCard key={`${roomId}:${user?.id}:${execution.id}`} execution={execution} api={api} onNavigate={onNavigate} />)}</div></details>}
    {history.length > 0 && <details><summary className="min-h-11 cursor-pointer content-center text-sm">{t('executions.history')} ({history.length})</summary><div className="space-y-3 pt-2">{history.map(execution => <ExecutionCard key={`${roomId}:${user?.id}:${execution.id}`} execution={execution} api={api} onNavigate={onNavigate} />)}</div></details>}
  </section>
}
