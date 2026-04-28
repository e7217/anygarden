import { useMemo, useState } from 'react'
import { Plus, Pause, Play, Trash2, Zap } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { useRoomGoals } from '@/hooks/useRoomGoals'
import GoalForm, {
  type GoalFormAgentOption,
} from '@/components/goal-form/GoalForm'
import type { Goal } from '@/lib/goals'
import type { Participant } from '@/pages/ChatPage'

interface GoalsSectionProps {
  roomId: string
  /** #312 — full agent participants (id + display_name + agent_id)
   *  so the form can render an explicit picker and rows can show
   *  the assignee name. Replaces the pre-#312 ``candidateAgentIds``
   *  prop which only carried ids and forced an ``useAgents()``
   *  round-trip just to label rows. */
  agentParticipants: Participant[]
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
  agentParticipants,
}: GoalsSectionProps) {
  const { goals, refresh, remove, runNow, pause, resume } =
    useRoomGoals(roomId)
  const [showForm, setShowForm] = useState(false)

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
    <section className="flex flex-col">
      <header className="flex items-baseline justify-between px-3 py-2">
        <h3 className="text-[11px] font-semibold uppercase tracking-wider text-[var(--color-foreground-subtle)]">
          Responsibilities
        </h3>
        <div className="flex items-center gap-2">
          <span className="text-[11px] text-[var(--color-foreground-subtle)]">
            {goals.length}
          </span>
          {hasCandidates && (
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

      {showForm && hasCandidates && (
        <div className="mx-1 mb-2 rounded-[var(--radius-sm)] border border-[var(--color-border)] bg-white">
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
                {/* #312 — show assignee first so the user sees "who"
                    before "how often". Falls back to a short id slice
                    when the assignee agent has left the room (rare;
                    the row goes stale rather than disappearing). */}
                <span
                  className="text-[var(--color-foreground-muted)]"
                  data-testid={`right-rail-goal-assignee-${g.id}`}
                >
                  {agentNameById[g.assignee_agent_id] ??
                    `agent ${g.assignee_agent_id.slice(0, 6)}`}
                </span>
                {' · '}
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
