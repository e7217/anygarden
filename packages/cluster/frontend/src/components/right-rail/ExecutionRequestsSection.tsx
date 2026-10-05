import { useState } from 'react'
import { Link } from 'react-router-dom'
import { ChevronRight } from 'lucide-react'
import { Button } from '@/components/ui/button'
import MarkdownContent from '@/components/MarkdownContent'
import { DispositionNotice } from '@/components/ExecutionState'
import { useExecutionRequests, type ExecutionRequest } from '@/hooks/useExecutionRequests'
import { useLocale } from '@/i18n/LocaleProvider'

export function RequestCard({ request, answer, onNavigate, taskHref, compact = false, expanded = false }: { request: ExecutionRequest; answer: (id: string, value: string) => Promise<ExecutionRequest | null>; onNavigate?: () => void; taskHref?: string; compact?: boolean; expanded?: boolean }) {
  const { t, formatDate } = useLocale()
  const [value, setValue] = useState('')
  const [sending, setSending] = useState(false)
  const pending = request.status === 'pending' && request.is_current !== false
  const content = <>
    <Link to={taskHref ?? `/rooms/${request.task_room_id}`} onClick={onNavigate} className="flex min-h-11 items-center break-words text-xs text-[var(--color-brand-text)] underline md:min-h-8">{request.task_title}</Link>
    {request.assignee_display_name && <p className="mb-2 text-xs text-[var(--color-foreground-muted)]">{request.assignee_display_name}</p>}
    <MarkdownContent content={request.question} />
    {request.execution_source_message_id && <Link to={`/rooms/${request.operating_room_id}?message=${encodeURIComponent(request.execution_source_message_id)}`} onClick={onNavigate} className="mt-1 flex min-h-11 items-center text-xs text-[var(--color-brand-text)] underline md:min-h-8">{t('tasks.openOriginalRequest')}</Link>}
    {pending && request.can_answer ? <form className="mt-2 space-y-2" onSubmit={async event => {
      event.preventDefault()
      if (!value.trim() || sending) return
      setSending(true)
      try { if (await answer(request.id, value.trim())) setValue('') } finally { setSending(false) }
    }}>
      <label htmlFor={`execution-answer-${request.id}`} className="block text-xs font-medium">{t('executionRequests.answer')}</label>
      <textarea id={`execution-answer-${request.id}`} data-testid={`execution-request-answer-${request.id}`} value={value} onChange={event => setValue(event.target.value)} disabled={sending} rows={3} maxLength={100000} className="w-full min-w-0 resize-y rounded-[var(--radius-sm)] border border-[var(--color-border)] bg-[var(--color-surface)] px-2 py-2 text-base focus:outline-none focus:ring-2 focus:ring-[var(--color-brand-text)] md:text-sm" />
      <Button type="submit" size="sm" disabled={!value.trim() || sending} data-testid={`execution-request-submit-${request.id}`}>{sending ? t('common.loading') : t('executionRequests.sendAnswer')}</Button>
    </form> : request.answer?.trim() && <div className="mt-3 border-t border-[var(--color-border)] pt-2">
      <p className="mb-1 text-xs font-semibold">{t('executionRequests.answered')}</p>
      <MarkdownContent content={request.answer} />
      {request.answered_at && <time dateTime={request.answered_at} className="mt-1 block text-xs text-[var(--color-foreground-muted)]">{formatDate(new Date(request.answered_at), { dateStyle: 'medium', timeStyle: 'short' })}</time>}
    </div>}
  </>
  return <article data-testid={`execution-request-${request.id}`} className="min-w-0 rounded-[var(--radius-sm)] border border-[var(--color-border)] bg-[var(--color-surface)] p-2 text-sm [overflow-wrap:anywhere]">
    <DispositionNotice disposition={request.disposition} inputRevision={request.input_revision} operationAction={request.execution_operation_action} />
    {pending ? <><p className="mb-1 text-xs font-semibold text-[var(--color-warning)]">{t('executionRequests.waiting')}</p>{compact ? <details className="group/summary">
      <summary className="flex min-h-11 cursor-pointer list-none items-start gap-2 py-2 text-sm font-medium [&::-webkit-details-marker]:hidden"><ChevronRight aria-hidden="true" className="mt-0.5 h-4 w-4 shrink-0 group-open/summary:rotate-90" /><span className="min-w-0 flex-1"><span className="line-clamp-2">{request.task_title}</span></span></summary>
      <div className="pt-2">{content}</div>
    </details> : content}</> : <details open={expanded || undefined}><summary className="min-h-11 cursor-pointer content-center text-xs font-medium md:min-h-8">{request.status === 'answered' ? t('executionRequests.answered') : t('executionRequests.closed')}: {request.task_title}</summary>{content}</details>}
  </article>
}

export default function ExecutionRequestsSection({ roomId, onNavigate }: { roomId: string; onNavigate?: () => void }) {
  const { t } = useLocale()
  const { requests, error, answer } = useExecutionRequests(roomId)
  if (requests.length === 0 && !error) return null
  const pending = requests.filter(request => request.status === 'pending' && request.is_current !== false)
  const history = requests.filter(request => !pending.includes(request))
  return <section data-testid="execution-requests-section" className="min-w-0 space-y-2 border-b border-[var(--color-border)] px-3 py-2">
    <h3 className="text-sm font-semibold">{t('executionRequests.title')} · {pending.length}</h3>
    {error && <p role="alert" className="text-xs text-[var(--color-danger)]">{t('tasks.requestFailed', { status: error.match(/HTTP (\d+)/)?.[1] ?? '500' })}</p>}
    {pending.map(request => <RequestCard key={request.id} compact request={request} answer={answer} onNavigate={onNavigate} />)}
    {history.length > 0 && <details><summary className="min-h-11 cursor-pointer content-center text-sm text-[var(--color-foreground-muted)]">{t('contextRail.requestHistory')} ({history.length})</summary><div className="space-y-2">{history.map(request => <RequestCard key={request.id} request={request} answer={answer} onNavigate={onNavigate} />)}</div></details>}
  </section>
}
