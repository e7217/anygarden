import { useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { ChevronRight, RefreshCw } from 'lucide-react'
import PageShell from '@/components/PageShell'
import MarkdownContent from '@/components/MarkdownContent'
import AuthenticatedArtifactLink from '@/components/AuthenticatedArtifactLink'
import { DispositionNotice } from '@/components/ExecutionState'
import TaskRecoveryState, { hasTaskRecovery } from '@/components/TaskRecoveryState'
import { RequestCard } from '@/components/right-rail/ExecutionRequestsSection'
import { ApprovalCard } from '@/components/right-rail/ExecutionApprovalsSection'
import { Button } from '@/components/ui/button'
import { Select } from '@/components/ui/select'
import { useAuth } from '@/hooks/useAuth'
import { useProjectInbox, type InboxItem } from '@/hooks/useProjectInbox'
import { useLocale } from '@/i18n/LocaleProvider'
import { taskPresentation } from '@/lib/taskPresentation'

function TaskRecord({ item }: { item: InboxItem }) {
  const { t, formatDate } = useLocale()
  const task = item.task!
  return <div data-testid={`inbox-task-details-${task.id}`} className="min-w-0 [overflow-wrap:anywhere]">
    <div className="space-y-3 pt-2 text-sm">
      <DispositionNotice disposition={task.disposition ?? item.disposition} inputRevision={task.input_revision ?? item.input_revision} operationAction={item.execution_operation_action} />
      {task.input_revision != null && <p>{t('executions.inputVersion', { version: task.input_revision })}</p>}
      <p className="text-[var(--color-foreground-muted)]">{[item.task_room_name, task.assignee_display_name].filter(Boolean).join(' · ')}</p>
      {task.error && !(hasTaskRecovery(task.recovery) && task.recovery?.reason_code) && <p className="whitespace-pre-wrap text-[var(--color-warning)]">{t('tasks.reason')}: {task.error}</p>}
      {task.result_markdown && <div><p className="mb-2 font-semibold">{t('tasks.resultVersion', { version: task.result_version })}</p><MarkdownContent content={task.result_markdown} /></div>}
      {task.artifacts.length > 0 && <div className="space-y-2"><p className="font-semibold">{t('inbox.artifacts')}</p>{task.artifacts.map(file => <div key={file.id}><AuthenticatedArtifactLink href={file.url} className="inline-flex min-h-11 items-center text-[var(--color-brand-text)] underline">{file.filename}</AuthenticatedArtifactLink><p className="break-all text-xs text-[var(--color-foreground-muted)]">SHA-256: {file.sha256}</p></div>)}</div>}
      {item.source_href && <Link to={item.source_href} className="inline-flex min-h-11 items-center text-[var(--color-brand-text)] underline">{t('tasks.openOriginalRequest')}</Link>}
      <Link to={`/rooms/${item.operating_room_id}`} className="ml-3 inline-flex min-h-11 items-center text-[var(--color-brand-text)] underline">{t('inbox.openOperatingRoom')}</Link>
      <p className="break-all text-xs text-[var(--color-foreground-muted)]">{t('tasks.sourceTask')}: {task.id}</p>
      <time dateTime={item.updated_at} className="block text-xs text-[var(--color-foreground-muted)]">{formatDate(new Date(item.updated_at), { dateStyle: 'medium', timeStyle: 'short' })}</time>
    </div>
  </div>
}

function itemPresentation(item: InboxItem) {
  if (item.type === 'task') return taskPresentation({
    status: item.task?.status ?? item.status,
    disposition: item.task?.disposition ?? item.disposition,
    is_current: item.task?.is_current ?? item.is_current,
    execution_operation_action: item.execution_operation_action,
  })
  const disposition = item.type === 'question' ? item.question?.disposition ?? item.disposition : item.approval?.disposition ?? item.disposition
  const current = item.type === 'question' ? item.question?.is_current ?? item.is_current : item.approval?.is_current ?? item.is_current
  if (disposition === 'superseded' || current === false && !['cancelled', 'cancelling'].includes(disposition ?? '')) return { status: 'superseded', label: 'executions.status.superseded' as const, history: true }
  if (disposition === 'cancelled' || disposition === 'cancelling') return taskPresentation({ status: item.status, disposition, execution_operation_action: item.execution_operation_action })
  if (item.type === 'approval' && item.approval) return { status: item.approval.status, label: `executionApprovals.status.${item.approval.status}` as const, history: item.approval.status !== 'pending' && item.approval.status !== 'executing' }
  const status = item.question?.status ?? item.status
  return { status, label: status === 'pending' ? 'executionRequests.waiting' as const : status === 'answered' ? 'executionRequests.answered' as const : 'executionRequests.closed' as const, history: status !== 'pending' }
}

function requiresAttention(item: InboxItem) {
  const presentation = itemPresentation(item)
  return item.needs_action && !['superseded', 'cancelled'].includes(presentation.status)
}

function InboxRecord({ item, focused, inbox }: { item: InboxItem; focused: boolean; inbox: ReturnType<typeof useProjectInbox> }) {
  const { t, formatDate } = useLocale()
  const [expanded, setExpanded] = useState(focused)
  useEffect(() => { if (focused) setExpanded(true) }, [focused])
  const presentation = itemPresentation(item)
  const needsAttention = requiresAttention(item)
  const status = needsAttention
    ? item.type === 'task' ? `${presentation.label ? t(presentation.label) : presentation.status} · ${t('inbox.needsAttention')}` : item.current_action === 'read' ? t('inbox.awaitingMember') : t('inbox.needsAction')
    : presentation.label ? t(presentation.label) : presentation.status
  const title = item.task?.title ?? item.task_title
  return <details
    id={`inbox-${item.id}`} data-testid={`inbox-item-${item.id}`} data-inbox-type={item.type}
    data-project-id={item.project_id} data-focused={focused || undefined} open={expanded}
    onToggle={event => setExpanded(event.currentTarget.open)}
    className={`group/item min-w-0 border-b border-[var(--color-border)] [overflow-wrap:anywhere] ${focused ? 'rounded-[var(--radius-sm)] ring-2 ring-[var(--color-brand-text)]' : ''}`}
  >
    <summary className="flex min-h-11 cursor-pointer list-none items-start gap-3 rounded-[var(--radius-sm)] px-3 py-4 hover:bg-[var(--color-surface-hover)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-brand-text)] [&::-webkit-details-marker]:hidden">
      <ChevronRight aria-hidden="true" className="mt-1 h-4 w-4 shrink-0 text-[var(--color-foreground-muted)] group-open/item:rotate-90" />
      <span className="min-w-0 flex-1 space-y-1">
        <span className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm text-[var(--color-foreground-muted)]"><span className="font-medium text-[var(--color-foreground)]">{item.project_name}</span><span>{item.task_room_name}</span><span>· {t(`inbox.type.${item.type}`)}</span></span>
        <span className="line-clamp-2 text-base font-medium">{title}</span>
        <span className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 text-sm">
          <span className={needsAttention ? 'font-medium text-[var(--color-warning)]' : 'text-[var(--color-foreground-muted)]'}>{status}</span>
          <time dateTime={item.updated_at} className="text-[var(--color-foreground-muted)]">{formatDate(new Date(item.updated_at), { dateStyle: 'short', timeStyle: 'short' })}</time>
        </span>
      </span>
    </summary>
    <div className="min-w-0 space-y-3 px-3 pb-4 sm:pl-10" data-testid={`inbox-content-${item.id}`}>
      <h3 className="break-words text-base font-semibold">{title}</h3>
      {item.type === 'task' && item.task && <><TaskRecoveryState recovery={item.task.recovery} taskId={item.task_id} inputRevision={item.task.input_revision ?? item.input_revision} /><TaskRecord item={item} /></>}
      {item.type === 'question' && item.question && <RequestCard expanded request={item.question} answer={inbox.answer} taskHref={item.task_href} />}
      {item.type === 'approval' && item.approval && <ApprovalCard expanded approval={item.approval} decide={inbox.decide} taskHref={item.task_href} sourceTaskHref={`/inbox?item=${encodeURIComponent(`task:${item.approval.source_task_id}`)}`} />}
    </div>
  </details>
}

export default function InboxPage() {
  const { t } = useLocale()
  const { user } = useAuth()
  const inbox = useProjectInbox(user?.id ?? null)
  const [params] = useSearchParams()
  const focused = params.get('item')
  const [project, setProject] = useState('')
  const [type, setType] = useState('')
  const selected = inbox.items.find(item => item.id === focused)
  const visible = inbox.items.filter(item => item.id === focused || ((!project || item.project_id === project) && (!type || item.type === type)))
  useEffect(() => {
    if (!focused || !selected) return
    const frame = requestAnimationFrame(() => document.getElementById(`inbox-${focused}`)?.scrollIntoView({ block: 'center' }))
    return () => cancelAnimationFrame(frame)
  }, [focused, selected?.id])
  const ordered = [...visible].sort((a, b) => Date.parse(b.updated_at) - Date.parse(a.updated_at))
  const attention = ordered.filter(requiresAttention)
  const current = ordered.filter(item => !attention.includes(item) && !itemPresentation(item).history && itemPresentation(item).status !== 'failed')
  const history = ordered.filter(item => !attention.includes(item) && !current.includes(item))
  function renderGroup(items: InboxItem[], label: 'inbox.attention' | 'inbox.current' | 'inbox.history') {
    if (items.length === 0) return null
    return <section className="min-w-0 space-y-2" aria-label={t(label)}>
      <h2 className="flex items-center gap-2 text-lg font-semibold">{t(label)}<span className="text-sm font-normal tabular-nums text-[var(--color-foreground-muted)]">{items.length}</span></h2>
      <div className="min-w-0 border-t border-[var(--color-border)]">{items.map(item => <InboxRecord key={item.id} item={item} focused={item.id === focused} inbox={inbox} />)}</div>
    </section>
  }
  return <PageShell title={t('inbox.title')}><div className="mx-auto w-full max-w-4xl space-y-6 px-4 py-6 md:px-8 md:py-8" data-testid="project-inbox">
    <header className="space-y-2"><div className="flex items-center justify-between gap-3"><h1 className="text-2xl font-semibold">{t('inbox.title')}</h1><Button type="button" variant="outline" className="min-h-11" onClick={() => { void inbox.refresh() }} aria-label={t('inbox.refresh')}><RefreshCw className="mr-2 h-4 w-4" />{t('inbox.refresh')}</Button></div><p className="text-sm text-[var(--color-foreground-muted)]">{t('inbox.description')}</p></header>
    <div className="flex flex-col gap-3 sm:flex-row"><div className="flex min-w-0 flex-1 flex-col gap-1 text-sm"><label htmlFor="inbox-project">{t('inbox.project')}</label><Select id="inbox-project" className="min-h-11 w-full min-w-0" value={project} onChange={event => setProject(event.target.value)}><option value="">{t('inbox.allProjects')}</option>{inbox.projects.map(value => <option key={value.id} value={value.id}>{value.name}</option>)}</Select></div><div className="flex min-w-0 flex-1 flex-col gap-1 text-sm"><label htmlFor="inbox-type">{t('inbox.type')}</label><Select id="inbox-type" className="min-h-11 w-full min-w-0" value={type} onChange={event => setType(event.target.value)}><option value="">{t('inbox.allTypes')}</option>{(['question', 'approval', 'task'] as const).map(value => <option key={value} value={value}>{t(`inbox.type.${value}`)}</option>)}</Select></div></div>
    {inbox.error && <p role="alert" className="break-words text-sm text-[var(--color-danger)]">{inbox.error}</p>}
    {inbox.loading && <p role="status">{t('common.loading')}</p>}
    {!inbox.loading && focused && !selected && <p role="status" className="text-sm text-[var(--color-foreground-muted)]">{t('inbox.unavailable')}</p>}
    {!inbox.loading && !inbox.error && visible.length === 0 && <p className="py-8 text-center text-[var(--color-foreground-muted)]">{t('inbox.empty')}</p>}
    {renderGroup(attention, 'inbox.attention')}
    {renderGroup(current, 'inbox.current')}
    {renderGroup(history, 'inbox.history')}
  </div></PageShell>
}
