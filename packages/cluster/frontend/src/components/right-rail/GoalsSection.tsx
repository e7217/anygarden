import { useState } from 'react'
import { Plus, Pause, Play, Trash2, Zap } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { useRoomGoals } from '@/hooks/useRoomGoals'
import GoalForm from '@/components/goal-form/GoalForm'
import type { Goal } from '@/lib/goals'

interface GoalsSectionProps {
  roomId: string
  /** Set of agent ids that can be picked when creating a goal from
   *  the right rail. The first one is used as the default — the
   *  rail is room-scoped so the user is implicitly creating a
   *  responsibility for an agent already in the room. */
  candidateAgentIds: string[]
}

function formatNextRun(iso: string | null): string {
  if (!iso) return '—'
  const target = new Date(iso).getTime()
  const delta = target - Date.now()
  if (delta < 0) return 'overdue'
  const m = Math.round(delta / 60_000)
  if (m < 60) return `in ${m}m`
  const h = Math.round(m / 60)
  if (h < 24) return `in ${h}h`
  const d = Math.round(h / 24)
  return `in ${d}d`
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
 * Compact goals panel for the right rail (#302). Lists goals whose
 * ``report_room_id`` is the active room. Inline create form opens
 * when the user clicks ``+``. Each row exposes pause/resume/run-now
 * affordances; full edit lives in the per-agent dialog.
 */
export default function GoalsSection({
  roomId,
  candidateAgentIds,
}: GoalsSectionProps) {
  const { goals, refresh, remove, runNow, pause, resume } =
    useRoomGoals(roomId)
  const [showForm, setShowForm] = useState(false)
  const defaultAgent = candidateAgentIds[0] ?? null

  return (
    <section className="flex flex-col">
      <header className="flex items-baseline justify-between px-3 py-2">
        <h3 className="text-[11px] font-semibold uppercase tracking-wider text-[var(--color-foreground-subtle)]">
          Responsibilities
        </h3>
        <div className="flex items-center gap-2">
          <span className="text-[11px] text-[var(--color-foreground-subtle)]">
            {goals.length}
          </span>
          {defaultAgent && (
            <button
              type="button"
              onClick={() => setShowForm((v) => !v)}
              aria-label={showForm ? 'Cancel new goal' : 'Add a goal'}
              className="rounded-[var(--radius-sm)] p-0.5 text-[var(--color-foreground-muted)] hover:bg-black/5"
            >
              <Plus
                className={`h-3.5 w-3.5 transition-transform ${showForm ? 'rotate-45' : ''}`}
              />
            </button>
          )}
        </div>
      </header>

      {showForm && defaultAgent && (
        <div className="mx-1 mb-2 rounded-[var(--radius-sm)] border border-[var(--color-border)] bg-white">
          <GoalForm
            agentId={defaultAgent}
            defaultReportRoomId={roomId}
            onCreated={async () => {
              setShowForm(false)
              await refresh()
            }}
            onCancel={() => setShowForm(false)}
          />
        </div>
      )}

      <div className="px-1">
        {goals.length === 0 && !showForm && (
          <div className="px-3 py-4 text-center text-[12px] text-[var(--color-foreground-subtle)]">
            No goals yet
          </div>
        )}
        {goals.map((g) => (
          <div
            key={g.id}
            data-testid={`right-rail-goal-row-${g.id}`}
            className="group flex items-center gap-2 rounded-[var(--radius-sm)] px-2 py-1.5 hover:bg-[var(--color-surface-alt)]"
          >
            <span
              className={`h-1.5 w-1.5 shrink-0 rounded-full ${statusDot(g.status)}`}
              title={g.status}
            />
            <div className="min-w-0 flex-1">
              <p
                className="truncate text-[13px] text-[var(--color-foreground)]"
                title={g.title}
              >
                {g.title}
              </p>
              <p className="text-[10px] text-[var(--color-foreground-subtle)]">
                {g.trigger_type}
                {g.trigger_type !== 'manual' && (
                  <> · next {formatNextRun(g.next_run_at)}</>
                )}
                {g.consecutive_failures > 0 && (
                  <span className="ml-1 text-red-500">
                    · {g.consecutive_failures} fail
                  </span>
                )}
              </p>
            </div>
            <div className="flex items-center gap-0.5 opacity-0 group-hover:opacity-100 transition-opacity">
              <Button
                variant="ghost"
                size="icon"
                title="Run now"
                aria-label={`Run ${g.title} now`}
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
                  aria-label={`Pause ${g.title}`}
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
                  aria-label={`Resume ${g.title}`}
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
                aria-label={`Delete ${g.title}`}
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
    </section>
  )
}
