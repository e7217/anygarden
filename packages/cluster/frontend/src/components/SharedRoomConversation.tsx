import { useEffect, useRef, useState } from 'react'
import { Link2, Menu, RefreshCw, X } from 'lucide-react'
import Sidebar from '@/components/Sidebar'
import SidebarExpandButton from '@/components/SidebarExpandButton'
import MarkdownContent from '@/components/MarkdownContent'
import DelegateMessageDialog from '@/components/DelegateMessageDialog'
import DelegationCard from '@/components/DelegationCard'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import { useSharedRoom } from '@/hooks/useSharedRoom'
import { useRooms, type Room } from '@/hooks/useRooms'
import { useLocale } from '@/i18n/LocaleProvider'
import { uuid, type FederationMessage, type SharedChannelRef, type SharedSubmission } from '@/lib/federationApi'
import { sharedErrorCopy } from '@/lib/sharedRoomCopy'

/** No local message/threads/files/presence hooks belong on this channel view. */
export default function SharedRoomConversation({ currentRoom, channel }: {
  currentRoom: Room; channel: SharedChannelRef
}) {
  const { t, formatDate } = useLocale()
  const room = useSharedRoom(channel)
  const { markRoomRead } = useRooms()
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [text, setText] = useState('')
  const [reply, setReply] = useState<FederationMessage | null>(null)
  const [requestId, setRequestId] = useState(uuid)
  const textarea = useRef<HTMLTextAreaElement>(null)
  const scroller = useRef<HTMLDivElement>(null)
  const followLatest = useRef(true)
  const data = room.data
  const writable = Boolean(data && !room.error)
  // An authority receipt can arrive before the follower projects that event.
  // Keep the delivery visible until the shared cursor catches up.
  const pending = data?.submissions.filter(item => item.state !== 'confirmed' || (item.receipt && item.receipt.seq > data.applied_seq)) ?? []
  const loadedIds = new Set(data?.messages.map(message => message.message_id))
  const targetFor = (executor: { node_id: string; agent_id: string }) => data?.targets.find(target => target.node_id === executor.node_id && target.agent_id === executor.agent_id)
  const newestSeq = data?.cursor.newest_seq
  useEffect(() => {
    if (followLatest.current && scroller.current) scroller.current.scrollTop = scroller.current.scrollHeight
  }, [newestSeq])
  useEffect(() => {
    if (newestSeq == null || room.error) return
    const timer = window.setTimeout(() => void markRoomRead(currentRoom.id), 1500)
    return () => window.clearTimeout(timer)
  }, [currentRoom.id, newestSeq, room.error, markRoomRead])

  async function send() {
    if (!text.trim() || !data?.permissions.can_send || !writable || room.busy) return
    if (await room.sendMessage({ request_id: requestId, text: text.trim(), thread_root_id: reply?.message_id ?? null })) {
      setText('')
      setReply(null)
      setRequestId(uuid())
      textarea.current?.focus()
      if (scroller.current) scroller.current.scrollTop = scroller.current.scrollHeight
    }
  }

  function setReplyTarget(message: FederationMessage | null) {
    setReply(message)
    setRequestId(uuid())
    textarea.current?.focus()
  }

  function renderSubmission(submission: SharedSubmission) {
    return (
      <section key={submission.request_id} className="mt-3 space-y-2 rounded-[var(--radius-md)] border border-dashed p-3 text-sm">
        <p className="font-medium">{t(submission.kind === 'message.send' ? 'federation.room.pendingMessage' : submission.kind === 'task.cancel' ? 'federation.room.pendingCancel' : 'federation.room.pendingDelegation')}</p>
        {submission.text && <p className="whitespace-pre-wrap break-words">{submission.text}</p>}
        {submission.executor && <p>{targetFor(submission.executor)?.name || t('federation.room.unknownAgent')}</p>}
        <p role="status" className="text-[var(--color-foreground-muted)]">{t(submission.state === 'failed' ? 'federation.room.deliveryFailed' : submission.state === 'confirmed' ? 'federation.room.confirmedSyncing' : 'federation.room.pending')}</p>
        {submission.error_code && <p className="text-[var(--color-danger)]">{t(sharedErrorCopy(submission.error_code))}</p>}
        {submission.can_retry && (
          <div className="space-y-1">
            <Button variant="outline" size="sm" disabled={room.busy || !writable} onClick={() => void room.retry(submission.request_id)}>{t('federation.room.resend')}</Button>
            <p className="text-caption text-[var(--color-foreground-muted)]">{t('federation.room.resendNote')}</p>
          </div>
        )}
      </section>
    )
  }

  return (
    <div className="flex h-dvh overflow-hidden bg-[var(--color-background)]">
      <Sidebar selectedRoom={currentRoom.id} open={sidebarOpen} onClose={() => setSidebarOpen(false)} />
      <SidebarExpandButton />
      <main className="flex min-w-0 flex-1 flex-col">
        <header className="flex min-h-14 shrink-0 items-center gap-2 border-b bg-[var(--color-surface)] px-3 sm:px-4">
          <Button variant="ghost" size="icon" className="md:hidden" aria-label={t('workspace.openSidebar')} onClick={() => setSidebarOpen(true)}><Menu className="size-5" /></Button>
          <Link2 className="hidden size-4 shrink-0 text-[var(--color-foreground-muted)] sm:block" aria-hidden="true" />
          <h1 className="min-w-0 flex-1 truncate text-lead">{currentRoom.name}</h1>
          <Badge variant="outline" className="shrink-0">{t('federation.sharedChannel')}</Badge>
          <Button variant="ghost" size="icon" aria-label={t('common.refresh')} disabled={room.loading} onClick={() => void room.refresh()}><RefreshCw className="size-4" /></Button>
        </header>
        {room.error && (
          <div role="alert" className="flex shrink-0 flex-wrap items-center justify-between gap-2 border-b bg-[var(--color-surface-alt)] px-4 py-3 text-sm">
            <span>{t(sharedErrorCopy(room.error, true))}</span>
            <Button size="sm" variant="outline" disabled={room.loading} onClick={() => void room.refresh()}>{t('federation.room.retry')}</Button>
          </div>
        )}
        <div ref={scroller} className="min-h-0 flex-1 overflow-y-auto" aria-label={t('federation.sharedChannel')}
          onScroll={event => { const element = event.currentTarget; followLatest.current = element.scrollHeight - element.scrollTop - element.clientHeight < 80 }}>
          <div className="mx-auto max-w-3xl space-y-4 px-3 py-4 sm:px-6">
            {!data && room.loading && <p role="status" className="text-sm text-[var(--color-foreground-muted)]">{t('federation.room.loading')}</p>}
            {data?.cursor.has_more_before && <Button variant="ghost" size="sm" disabled={room.loading} onClick={() => void room.loadOlder()}>{t('federation.room.older')}</Button>}
            {data && data.messages.length === 0 && <p className="py-8 text-center text-sm text-[var(--color-foreground-muted)]">{t('federation.room.empty')}</p>}
            {data?.messages.map(message => {
              const delegations = data.delegations.filter(item => item.source_message_id === message.message_id)
              const requestPending = pending.some(item => item.kind === 'task.request' && item.source_message_id === message.message_id && item.state !== 'failed')
              const root = message.thread_root_id ? data.messages.find(item => item.message_id === message.thread_root_id) : null
              const date = message.created_at ? new Date(message.created_at) : null
              return (
                <article key={message.message_id} id={`shared-message-${message.message_id}`} tabIndex={-1} data-shared-message={message.message_id} className={`min-w-0 break-words outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-brand-focus)] ${message.thread_root_id ? 'ml-3 border-l-2 pl-3 sm:ml-6' : ''}`}>
                  <div className="mb-1 flex flex-wrap items-baseline gap-x-2 gap-y-1 text-sm">
                    <span className="font-medium">{message.actor_name || t('federation.room.unknownParticipant')}</span>
                    {date && !Number.isNaN(date.getTime()) && <time dateTime={message.created_at!} className="text-caption text-[var(--color-foreground-muted)]">{formatDate(date, { dateStyle: 'short', timeStyle: 'short' })}</time>}
                    {!message.confirmed && <Badge variant="outline">{t('federation.room.pending')}</Badge>}
                  </div>
                  {root && <blockquote className="mb-2 line-clamp-2 border-l-2 pl-2 text-caption text-[var(--color-foreground-muted)]">{root.text}</blockquote>}
                  <div className="min-w-0 overflow-hidden text-sm"><MarkdownContent content={message.text} /></div>
                  {message.confirmed && message.thread_root_id === null && (
                    <div className="mt-2 flex flex-wrap gap-2">
                      {data.permissions.can_delegate && <DelegateMessageDialog message={message} room={room} disabled={!writable || room.busy || requestPending || delegations.length > 0} />}
                      {data.permissions.can_send && <Button size="sm" variant="ghost" disabled={!writable || room.busy} onClick={() => setReplyTarget(message)}>{t('federation.room.reply')}</Button>}
                    </div>
                  )}
                  {delegations.map(delegation => <DelegationCard key={delegation.delegation_id} delegation={delegation} target={targetFor(delegation.executor)} room={room} />)}
                  {pending.filter(item => item.kind === 'task.request' && item.source_message_id === message.message_id).map(renderSubmission)}
                </article>
              )
            })}
            {data?.delegations.filter(item => !loadedIds.has(item.source_message_id)).map(delegation => (
              <DelegationCard key={delegation.delegation_id} delegation={delegation} target={targetFor(delegation.executor)} room={room} />
            ))}
            {pending.filter(item => item.kind !== 'task.request' || !loadedIds.has(item.source_message_id ?? '')).map(renderSubmission)}
          </div>
        </div>
        <div className="shrink-0 border-t bg-[var(--color-surface)] px-3 py-3 sm:px-6">
          <div className="mx-auto max-w-3xl space-y-2">
            {room.actionError && <p role="alert" className="text-sm text-[var(--color-danger)]">{t(sharedErrorCopy(room.actionError))}</p>}
            {data?.permissions.can_send ? (
              <form onSubmit={event => { event.preventDefault(); void send() }} className="space-y-2">
                {reply && <div className="flex items-center justify-between gap-2 text-sm"><span className="truncate">{t('federation.room.replying', { name: reply.actor_name || t('federation.room.unknownParticipant') })}</span><Button type="button" size="icon" variant="ghost" aria-label={t('federation.room.cancel')} onClick={() => setReplyTarget(null)}><X className="size-4" /></Button></div>}
                <label className="sr-only" htmlFor="shared-message-input">{t('federation.room.message')}</label>
                <Textarea ref={textarea} id="shared-message-input" value={text} maxLength={16384} disabled={!writable || room.busy} placeholder={t('federation.room.placeholder')}
                  className="min-h-20 max-h-40" onChange={event => { setText(event.target.value); setRequestId(uuid()) }}
                  onKeyDown={event => { if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); void send() } }} />
                <div className="flex justify-end"><Button type="submit" disabled={!text.trim() || !writable || room.busy}>{t('federation.room.send')}</Button></div>
              </form>
            ) : data && <p className="text-sm text-[var(--color-foreground-muted)]">{t('federation.room.readOnly')}</p>}
          </div>
        </div>
      </main>
    </div>
  )
}
