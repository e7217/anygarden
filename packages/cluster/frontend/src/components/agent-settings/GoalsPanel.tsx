import { useState } from 'react'
import { Pause, Play, Trash2, Zap } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { useAgentGoals } from '@/hooks/useAgentGoals'
import GoalForm from '@/components/goal-form/GoalForm'
import type { Goal } from '@/lib/goals'
import { useLocale } from '@/i18n/LocaleProvider'
import { useFeedback } from '@/components/feedback/FeedbackProvider'

interface GoalsPanelProps {
  agentId: string | null
  /** #312 — display name surfaced from AgentSettingsDialog so the
   *  GoalForm picker can render a proper label. The dialog is
   *  always single-agent so the picker has exactly one option. */
  agentName?: string
}

function statusDot(status: Goal['status']): string {
  switch (status) {
    case 'active':
      return 'bg-[var(--color-brand)]'
    case 'paused':
      return 'bg-[var(--color-foreground-subtle)]'
    case 'failed':
      return 'bg-[var(--color-destructive)]'
    default:
      return 'bg-[var(--color-foreground-subtle)]'
  }
}

/**
 * AgentSettingsDialog Goals section (#302).
 *
 * Cross-room view of every responsibility this agent owns. Sits
 * above the Tasks section since "what is this agent committed to
 * over time" is a higher-level question than "what's open right
 * now". Inline create form opens via the ``+ Add goal`` button.
 */
export default function GoalsPanel({ agentId, agentName = '' }: GoalsPanelProps) {
  const { t } = useLocale()
  const { confirm: confirmAction } = useFeedback()
  const { goals, refresh, remove, runNow, pause, resume } =
    useAgentGoals(agentId)
  const [showForm, setShowForm] = useState(false)

  if (!agentId) {
    return (
      <p className="text-sm text-[var(--color-foreground-subtle)]">
        {t('admin.goals.selectAgent')}
      </p>
    )
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center justify-between">
        <p className="text-[12px] text-[var(--color-foreground-muted)]">
          {t('admin.goals.description')}
        </p>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => setShowForm((v) => !v)}
        >
          {showForm ? t('common.cancel') : t('admin.goals.add')}
        </Button>
      </div>

      {showForm && (
        <div className="rounded-[var(--radius-sm)] border border-[var(--color-border)] bg-[var(--color-surface-elevated)]">
          <GoalForm
            roomAgents={[
              { id: agentId, name: agentName || agentId.slice(0, 6) },
            ]}
            onCreated={async () => {
              setShowForm(false)
              await refresh()
            }}
            onCancel={() => setShowForm(false)}
          />
        </div>
      )}

      {goals.length === 0 && !showForm && (
        <p className="rounded-[var(--radius-sm)] border border-dashed border-[var(--color-border)] px-3 py-4 text-center text-[12px] text-[var(--color-foreground-subtle)]">
          {t('admin.goals.none')}
        </p>
      )}

      {goals.map((g) => (
        <div
          key={g.id}
          className="flex items-start gap-2 rounded-[var(--radius-sm)] border border-[var(--color-border)] bg-[var(--color-surface-elevated)] px-3 py-2"
        >
          <span
            className={`mt-1 h-1.5 w-1.5 shrink-0 rounded-full ${statusDot(g.status)}`}
            title={g.status === 'active' ? t('admin.goals.status.active') : g.status === 'paused' ? t('admin.goals.status.paused') : g.status === 'failed' ? t('admin.goals.status.failed') : g.status}
          />
          <div className="min-w-0 flex-1">
            <p className="truncate text-sm text-[var(--color-foreground)]">
              {g.title}
            </p>
            <p className="text-[11px] text-[var(--color-foreground-subtle)]">
              {g.trigger_type === 'cron' ? t('admin.goals.trigger.cron') : g.trigger_type === 'interval' ? t('admin.goals.trigger.interval') : g.trigger_type}
              {g.trigger_type === 'cron' && (
                <> · {(g.trigger_config as { cron?: string }).cron}</>
              )}
              {g.trigger_type === 'interval' && (
                <>
                  {' '}
                  · {t('admin.goals.intervalSeconds', { count: (g.trigger_config as { interval_seconds?: number }).interval_seconds ?? 0 })}
                </>
              )}
              {g.report_room_id && <> · {t('admin.goals.reportRoom', { id: g.report_room_id.slice(0, 8) })}</>}
              {g.consecutive_failures > 0 && (
                <span className="ml-1 text-[var(--color-destructive)]">
                  · {t('admin.goals.failureCount', { count: g.consecutive_failures })}
                </span>
              )}
            </p>
            <p className="mt-1 line-clamp-2 text-[11px] text-[var(--color-foreground-muted)]">
              {g.spec}
            </p>
          </div>
          <div className="flex items-center gap-0.5">
            <Button
              variant="ghost"
              size="icon"
              title={t('admin.goals.runNow')}
              onClick={() => runNow(g.id)}
              className="h-6 w-6"
            >
              <Zap className="h-3 w-3" />
            </Button>
            {g.status === 'active' ? (
              <Button
                variant="ghost"
                size="icon"
                title={t('admin.goals.pause')}
                onClick={() => pause(g.id)}
                className="h-6 w-6"
              >
                <Pause className="h-3 w-3" />
              </Button>
            ) : (
              <Button
                variant="ghost"
                size="icon"
                title={t('admin.goals.resume')}
                onClick={() => resume(g.id)}
                className="h-6 w-6"
              >
                <Play className="h-3 w-3" />
              </Button>
            )}
            <Button
              variant="ghost"
              size="icon"
              title={t('admin.goals.delete')}
              onClick={async () => {
                if (await confirmAction({ title: t('admin.goals.delete'), description: t('admin.goals.confirmDelete', { title: g.title }), destructive: true })) remove(g.id)
              }}
              className="h-6 w-6 text-[var(--color-destructive)]/70 hover:text-[var(--color-destructive)]"
            >
              <Trash2 className="h-3 w-3" />
            </Button>
          </div>
        </div>
      ))}
    </div>
  )
}
