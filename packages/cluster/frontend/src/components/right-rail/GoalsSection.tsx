import { useMemo, useState } from 'react'
import { Plus, Pause, Play, Trash2, Zap } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { useRoomGoals } from '@/hooks/useRoomGoals'
import GoalForm, {
  type GoalFormAgentOption,
} from '@/components/goal-form/GoalForm'
import type { Goal } from '@/lib/goals'
import type { Participant } from '@/pages/ChatPage'
import { useLocale } from '@/i18n/LocaleProvider'
import { useFeedback } from '@/components/feedback/FeedbackProvider'

interface GoalsSectionProps {
  roomId: string
  /** #312 — full agent participants (id + display_name + agent_id)
   *  so the form can render an explicit picker and rows can show
   *  the assignee name. Replaces the pre-#312 ``candidateAgentIds``
   *  prop which only carried ids and forced an ``useAgents()``
   *  round-trip just to label rows. */
  agentParticipants: Participant[]
}

function statusDot(status: Goal['status']): string {
  switch (status) {
    case 'active':
      return 'bg-[var(--color-status-online)]'
    case 'paused':
      return 'bg-[var(--color-foreground-subtle)]'
    case 'failed':
      return 'bg-[var(--color-destructive)]'
    default:
      return 'bg-[var(--color-foreground-subtle)]'
  }
}

/**
 * Compact goals panel for the right rail (#302). Lists goals whose
 * ``report_room_id`` is the active room. Inline create form opens
 * when the user clicks ``+``. Each row exposes pause/resume/run-now
 * affordances; full edit lives in the per-agent dialog.
 */
export default function GoalsSection({
  roomId,
  agentParticipants,
}: GoalsSectionProps) {
  const { t } = useLocale()
  const { confirm, notify } = useFeedback()
  const { goals, loading, error, refresh, remove, runNow, pause, resume } =
    useRoomGoals(roomId)
  const [showForm, setShowForm] = useState(false)

  const formatNextRun = (iso: string | null): string => {
    if (!iso) return '—'
    const delta = new Date(iso).getTime() - Date.now()
    if (delta < 0) return t('goals.overdue')
    const minutes = Math.round(delta / 60_000)
    if (minutes < 60) return t('goals.inMinutes', { count: minutes })
    const hours = Math.round(minutes / 60)
    if (hours < 24) return t('goals.inHours', { count: hours })
    return t('goals.inDays', { count: Math.round(hours / 24) })
  }
  const statusLabel = (status: Goal['status']) => {
    switch (status) {
      case 'active': return t('goals.statusActive')
      case 'paused': return t('goals.statusPaused')
      case 'completed': return t('goals.statusCompleted')
      case 'failed': return t('goals.statusFailed')
      case 'abandoned': return t('goals.statusAbandoned')
    }
  }

  // #312 — derive {id, name} options for the form. Map back to
  // ``Agent.id`` (not Participant.id) because Goal.assignee_agent_id
  // references the agent, and the API call from GoalForm needs the
  // agent id.
  const formAgents = useMemo<GoalFormAgentOption[]>(
    () =>
      agentParticipants
        .filter((p) => p.agent_id)
        .map((p) => ({ id: p.agent_id as string, name: p.display_name }))
        .sort((a, b) => a.name.localeCompare(b.name)),
    [agentParticipants],
  )
  const hasCandidates = formAgents.length > 0

  const runAction = async (action: () => Promise<unknown>) => {
    try { await action() }
    catch (error) { notify({ message: error instanceof Error ? error.message : t('common.error'), tone: 'error' }) }
  }

  // Map agent_id → display_name so the goal rows can show the
  // assignee name without an extra fetch. Agents that have left the
  // room since the goal was created keep the goal pointing at their
  // id; we fall back to the raw id slice in that case.
  const agentNameById = useMemo<Record<string, string>>(() => {
    const out: Record<string, string> = {}
    for (const opt of formAgents) out[opt.id] = opt.name
    return out
  }, [formAgents])

  return (
    <section className="flex min-w-0 flex-col">
      <header className="flex items-baseline justify-between px-3 py-2">
        <h3 className="text-sm font-semibold text-[var(--color-foreground)]">
          {t('chat.responsibilities')}
        </h3>
        <div className="flex items-center gap-2">
          <span className="text-xs text-[var(--color-foreground-subtle)]">
            {goals.length}
          </span>
          {hasCandidates && (
            <Button
              variant="ghost"
              size="icon"
              type="button"
              onClick={() => setShowForm((v) => !v)}
              aria-label={showForm ? t('goals.cancelGoal') : t('goals.addGoal')}
              className="text-[var(--color-foreground-muted)]"
            >
              <Plus
                className={`h-3.5 w-3.5 transition-transform ${showForm ? 'rotate-45' : ''}`}
              />
            </Button>
          )}
        </div>
      </header>

      {showForm && hasCandidates && (
        <div className="mx-1 mb-2 rounded-[var(--radius-sm)] border border-[var(--color-border)] bg-[var(--color-surface)]">
          <GoalForm
            roomAgents={formAgents}
            defaultReportRoomId={roomId}
            onCreated={async () => {
              setShowForm(false)
              await refresh()
            }}
            onCancel={() => setShowForm(false)}
          />
        </div>
      )}

      <div className="min-w-0 px-1">
        {error && <p className="px-3 py-2 text-xs text-[var(--color-danger)]" role="alert">{t('goals.loadFailed')} {error}</p>}
        {loading && <p className="px-3 py-2 text-xs text-[var(--color-foreground-muted)]">{t('common.loading')}</p>}
        {!loading && !error && goals.length === 0 && !showForm && (
          <div className="px-3 py-4 text-center text-[12px] text-[var(--color-foreground-subtle)]">
            {t('goals.empty')}
          </div>
        )}
        {goals.map((g) => (
          <div
            key={g.id}
            data-testid={`right-rail-goal-row-${g.id}`}
            className="group relative flex min-w-0 items-center gap-2 rounded-[var(--radius-sm)] px-2 pb-[calc(var(--control-icon-size)+.5rem)] pt-1.5 hover:bg-[var(--color-surface-hover)] md:pointer-fine:py-1.5"
          >
            <span
              className={`h-1.5 w-1.5 shrink-0 rounded-full ${statusDot(g.status)}`}
              title={statusLabel(g.status)}
            />
            <div className="min-w-0 flex-1">
              <p
                className="truncate text-sm text-[var(--color-foreground)]"
                title={g.title}
              >
                {g.title}
              </p>
              {/* #323 — split the meta into two lines so the narrow
                  rail can show both "who" and "how often / when /
                  failures" without truncating either. ``manual`` goals
                  drop the second line's "next" segment but keep the
                  trigger label so the row never has a dead second
                  line. ``#312`` ordering preserved: assignee first. */}
              <p
                className="truncate text-xs text-[var(--color-foreground-muted)]"
                data-testid={`right-rail-goal-assignee-${g.id}`}
                title={
                  agentNameById[g.assignee_agent_id] ??
                  `${t('goals.agent')} ${g.assignee_agent_id.slice(0, 6)}`
                }
              >
                {agentNameById[g.assignee_agent_id] ??
                  `${t('goals.agent')} ${g.assignee_agent_id.slice(0, 6)}`}
              </p>
              <p className="truncate text-xs text-[var(--color-foreground-subtle)]">
                {statusLabel(g.status)} ·{' '}
                {t(g.trigger_type === 'cron' ? 'goals.cron' : g.trigger_type === 'interval' ? 'goals.interval' : 'goals.manual')}
                {g.trigger_type !== 'manual' && (
                  <> · {t('goals.next', { time: formatNextRun(g.next_run_at) })}</>
                )}
                {g.consecutive_failures > 0 && (
                  <span className="ml-1 text-[var(--color-destructive)]">
                    · {t('goals.failCount', { count: g.consecutive_failures })}
                  </span>
                )}
              </p>
            </div>
            {/* #325 — actions absolute so the meta column extends to
                the row's inner right edge at rest, aligning with the
                section header's right-aligned counter/action button.
                #327 — opaque ``bg-surface-alt`` so meta text under the
                cluster doesn't bleed through on hover; the row's hover
                state paints the same surface, so the cluster blends
                seamlessly. ``shadow-sm`` gives a faint lift so the
                cluster reads as floating over the row, not glued. */}
            <div className="absolute bottom-1 right-1 flex items-center gap-0.5 rounded-[var(--radius-sm)] bg-[var(--color-surface-alt)] opacity-100 shadow-sm transition-opacity md:pointer-fine:bottom-auto md:pointer-fine:right-2 md:pointer-fine:top-1/2 md:pointer-fine:-translate-y-1/2 md:pointer-fine:opacity-0 md:pointer-fine:group-hover:opacity-100 md:pointer-fine:group-focus-within:opacity-100">
              <Button
                variant="ghost"
                size="icon"
                title={t('goals.runNow', { name: g.title })}
                aria-label={t('goals.runNow', { name: g.title })}
                onClick={() => void runAction(() => runNow(g.id))}
              >
                <Zap className="h-3 w-3" />
              </Button>
              {g.status === 'active' ? (
                <Button
                  variant="ghost"
                  size="icon"
                  title={t('goals.pause', { name: g.title })}
                  aria-label={t('goals.pause', { name: g.title })}
                  onClick={() => void runAction(() => pause(g.id))}
                  >
                  <Pause className="h-3 w-3" />
                </Button>
              ) : (
                <Button
                  variant="ghost"
                  size="icon"
                  title={t('goals.resume', { name: g.title })}
                  aria-label={t('goals.resume', { name: g.title })}
                  onClick={() => void runAction(() => resume(g.id))}
                  >
                  <Play className="h-3 w-3" />
                </Button>
              )}
              <Button
                variant="ghost"
                size="icon"
                title={t('goals.delete', { name: g.title })}
                aria-label={t('goals.delete', { name: g.title })}
                onClick={async () => {
                  if (await confirm({ title: t('goals.deleteTitle'), description: t('goals.deleteConfirm', { name: g.title }), confirmLabel: t('goals.deleteTitle'), destructive: true })) await runAction(() => remove(g.id))
                }}
                className="text-[var(--color-destructive)] hover:text-[var(--color-destructive)]"
              >
                <Trash2 className="h-3 w-3" />
              </Button>
            </div>
          </div>
        ))}
      </div>
    </section>
  )
}
