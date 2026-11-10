import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { MessageSquarePlus } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { useAgentConversations } from '@/hooks/useAgentConversations'
import { useRooms } from '@/hooks/useRooms'
import { useLocale } from '@/i18n/LocaleProvider'

interface Props { agentId: string; user: { id: string; is_admin: boolean } | null }
export default function AgentConversationsPanel({ agentId, user }: Props) {
  const { t } = useLocale()
  const navigate = useNavigate()
  const { createAgentDM } = useRooms()
  const { rooms, loading, errorStatus, refresh } = useAgentConversations(agentId, user?.id ?? null)
  const [creating, setCreating] = useState(false)
  const [failed, setFailed] = useState(false)
  const busy = useRef(false)
  const mounted = useRef(true)
  useEffect(() => { mounted.current = true; return () => { mounted.current = false } }, [])
  async function create() {
    if (!user?.is_admin || busy.current) return
    busy.current = true
    setCreating(true)
    setFailed(false)
    try {
      const room = await createAgentDM(agentId)
      if (mounted.current) navigate(`/rooms/${room.id}`)
    } catch {
      if (mounted.current) { setFailed(true); void refresh() }
    } finally {
      busy.current = false
      if (mounted.current) setCreating(false)
    }
  }
  return <section className="space-y-3" aria-label={t('agentLinks.conversations')}>
    {user?.is_admin && <Button onClick={() => void create()} disabled={creating} className="w-full"><MessageSquarePlus />{creating ? t('agentLinks.conversationCreating') : t('agentLinks.newConversation')}</Button>}
    {failed && <p role="alert" className="text-caption text-[var(--color-danger)]">{t('agentLinks.conversationCreateFailed')}</p>}
    <h3 className="text-caption font-semibold">{t('agentLinks.conversations')}</h3>
    {loading ? <p role="status" className="text-caption text-[var(--color-foreground-muted)]">{t('agentLinks.conversationLoading')}</p>
      : errorStatus !== null ? <div className="space-y-2"><p role="alert" className="text-caption text-[var(--color-danger)]">{t('agentLinks.conversationLoadFailed')}</p><Button variant="outline" size="sm" onClick={() => void refresh()}>{t('common.retry')}</Button></div>
        : rooms.length === 0 ? <p className="text-caption text-[var(--color-foreground-muted)]">{t('agentLinks.conversationEmpty')}</p>
          : <ul className="divide-y divide-[var(--color-border)]">{rooms.map(room => <li key={room.id}><Link className="block break-words rounded-[var(--radius-sm)] px-1 py-3 text-caption text-[var(--color-link)] hover:bg-[var(--color-surface-hover)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--color-brand-focus)]" to={`/rooms/${room.id}`}>{room.name}</Link></li>)}</ul>}
  </section>
}
