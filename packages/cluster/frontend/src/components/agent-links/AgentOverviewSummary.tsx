import { Link } from 'react-router-dom'
import { useLocale } from '@/i18n/LocaleProvider'
import type { ActivePeerEdge } from '@/lib/agentLinks'

export interface SummaryRoom { id: string; name: string }
export interface SummaryTask { id: string; title: string; roomName: string; href: string }

interface Props {
  /** Inbox items waiting on the person, newest first. */
  needsYou: SummaryTask[]
  /** Rooms with an agent answering now, and who. */
  running: { room: SummaryRoom; agentIds: string[] }[]
  activeEdges: readonly ActivePeerEdge[]
  roomsWithNews: SummaryRoom[]
  nameOf: (agentId: string) => string
}

const LIMIT_TASKS = 3
const LIMIT_ROOMS = 5

/**
 * COL-19 — the overview's side panel while nothing is selected: what needs
 * the person, what is running, and where there is news. Empty sections fold.
 */
export default function AgentOverviewSummary({ needsYou, running, activeEdges, roomsWithNews, nameOf }: Props) {
  const { t } = useLocale()
  if (needsYou.length === 0 && running.length === 0 && roomsWithNews.length === 0) {
    return <p className="text-caption text-[var(--color-foreground-muted)]" data-testid="agent-overview-quiet">{t('agentLinks.quiet')}</p>
  }
  const rowClass = 'block rounded-[var(--radius-sm)] px-2 py-1.5 hover:bg-[var(--color-surface-hover)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-brand-focus)]'
  return (
    <div className="space-y-5" data-testid="agent-overview-summary">
      {needsYou.length > 0 && (
        <section aria-labelledby="overview-needs-you">
          <div className="mb-1 flex items-baseline justify-between gap-2">
            <h3 id="overview-needs-you" className="text-caption font-semibold text-[var(--color-foreground)]">{t('agentLinks.needsYou')}</h3>
            <Link to="/inbox" className="text-caption text-[var(--color-brand-text)] hover:underline">{t('agentLinks.openInbox')}</Link>
          </div>
          <ul className="space-y-1">
            {needsYou.slice(0, LIMIT_TASKS).map(task => (
              <li key={task.id}>
                <Link to={task.href} className={`${rowClass} border border-[var(--color-warning)] bg-[var(--color-warning-soft)]`}>
                  <span className="block break-words text-caption font-medium text-[var(--color-foreground)]">{task.title}</span>
                  <span className="block text-badge text-[var(--color-foreground-muted)]">#{task.roomName}</span>
                </Link>
              </li>
            ))}
          </ul>
        </section>
      )}
      {running.length > 0 && (
        <section aria-labelledby="overview-running">
          <h3 id="overview-running" className="mb-1 text-caption font-semibold text-[var(--color-foreground)]">{t('agentLinks.runningNow')}</h3>
          <ul className="space-y-1">
            {running.map(({ room, agentIds }) => {
              const asks = activeEdges.filter(edge => edge.room_id === room.id)
              return (
                <li key={room.id}>
                  <Link to={`/rooms/${room.id}`} className={rowClass}>
                    <span className="block break-words text-caption font-medium text-[var(--color-foreground)]">#{room.name}</span>
                    <span className="block break-words text-badge text-[var(--color-foreground-muted)]">
                      {asks.length > 0
                        ? asks.map(edge => t('agentLinks.asking', { from: nameOf(edge.from_agent_id), to: nameOf(edge.to_agent_id) })).join(' · ')
                        : agentIds.map(nameOf).join(', ')}
                    </span>
                  </Link>
                </li>
              )
            })}
          </ul>
        </section>
      )}
      {roomsWithNews.length > 0 && (
        <section aria-labelledby="overview-news">
          <h3 id="overview-news" className="mb-1 text-caption font-semibold text-[var(--color-foreground)]">{t('agentLinks.roomsWithNews')}</h3>
          <ul className="space-y-0.5">
            {roomsWithNews.slice(0, LIMIT_ROOMS).map(room => (
              <li key={room.id}>
                <Link to={`/rooms/${room.id}`} className={`${rowClass} break-words text-caption text-[var(--color-foreground)]`}>#{room.name}</Link>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  )
}
