import { useState } from 'react'
import { Pause, Play, Trash2, Zap } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { useAgentGoals } from '@/hooks/useAgentGoals'
import GoalForm from '@/components/goal-form/GoalForm'
import type { Goal } from '@/lib/goals'

interface GoalsPanelProps {
  agentId: string | null
}

function statusDot(status: Goal['status']): string {
  switch (status) {
    case 'active':
      return 'bg-[var(--color-brand)]'
    case 'paused':
      return 'bg-[var(--color-foreground-subtle)]'
    case 'failed':
      return 'bg-red-500'
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
export default function GoalsPanel({ agentId }: GoalsPanelProps) {
  const { goals, refresh, remove, runNow, pause, resume } =
    useAgentGoals(agentId)
  const [showForm, setShowForm] = useState(false)

  if (!agentId) {
    return (
      <p className="text-sm text-[var(--color-foreground-subtle)]">
        Select an agent to view its responsibilities.
      </p>
    )
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center justify-between">
        <p className="text-[12px] text-[var(--color-foreground-muted)]">
          이 에이전트가 자율적으로 수행하는 책임 (room 무관, cross-room)
        </p>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => setShowForm((v) => !v)}
        >
          {showForm ? '취소' : '+ Add goal'}
        </Button>
      </div>

      {showForm && (
        <div className="rounded-[var(--radius-sm)] border border-[var(--color-border)] bg-white">
          <GoalForm
            agentId={agentId}
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
          이 에이전트가 책임지는 일이 없어요.
        </p>
      )}

      {goals.map((g) => (
        <div
          key={g.id}
          className="flex items-start gap-2 rounded-[var(--radius-sm)] border border-[var(--color-border)] bg-white px-3 py-2"
        >
          <span
            className={`mt-1 h-1.5 w-1.5 shrink-0 rounded-full ${statusDot(g.status)}`}
            title={g.status}
          />
          <div className="min-w-0 flex-1">
            <p className="truncate text-sm text-[var(--color-foreground)]">
              {g.title}
            </p>
            <p className="text-[11px] text-[var(--color-foreground-subtle)]">
              {g.trigger_type}
              {g.trigger_type === 'cron' && (
                <> · {(g.trigger_config as { cron?: string }).cron}</>
              )}
              {g.trigger_type === 'interval' && (
                <>
                  {' '}
                  · {(g.trigger_config as { interval_seconds?: number }).interval_seconds}s
                </>
              )}
              {g.report_room_id && <> · → room {g.report_room_id.slice(0, 8)}…</>}
              {g.consecutive_failures > 0 && (
                <span className="ml-1 text-red-500">
                  · {g.consecutive_failures} fail
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
              title="Run now"
              onClick={() => runNow(g.id)}
              className="h-6 w-6"
            >
              <Zap className="h-3 w-3" />
            </Button>
            {g.status === 'active' ? (
              <Button
                variant="ghost"
                size="icon"
                title="Pause"
                onClick={() => pause(g.id)}
                className="h-6 w-6"
              >
                <Pause className="h-3 w-3" />
              </Button>
            ) : (
              <Button
                variant="ghost"
                size="icon"
                title="Resume"
                onClick={() => resume(g.id)}
                className="h-6 w-6"
              >
                <Play className="h-3 w-3" />
              </Button>
            )}
            <Button
              variant="ghost"
              size="icon"
              title="Delete"
              onClick={() => {
                if (confirm(`Delete goal "${g.title}"?`)) remove(g.id)
              }}
              className="h-6 w-6 text-red-400 hover:text-red-600"
            >
              <Trash2 className="h-3 w-3" />
            </Button>
          </div>
        </div>
      ))}
    </div>
  )
}
