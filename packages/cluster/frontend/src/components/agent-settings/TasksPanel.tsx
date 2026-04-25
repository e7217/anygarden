/**
 * TasksPanel — agent-scoped task aggregation (#266 Step 9).
 *
 * Shows every task currently assigned to the agent across every room
 * it participates in. Powered by ``GET /api/v1/agents/{id}/tasks``,
 * which is admin-only in Phase 1 (plan §3.2 결정 3).
 *
 * Status grouping mirrors the room TaskPanel ordering (Todo → In
 * Progress → Done → Blocked) so the two views feel like one feature.
 * Each row carries a room-name chip; clicking it navigates to the
 * originating room — the chat thread is where the human stakeholders
 * usually want to land.
 *
 * Realtime: subscribes to ``doorae:task:updated`` window events
 * dispatched by ``useWebSocket``. We refetch the whole list on any
 * relevant event — incremental merging would shave ~10 LOC of UI
 * code without changing the user-facing latency.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { CheckCircle2, Circle, Clock, AlertTriangle } from 'lucide-react'
import { apiFetch } from '@/lib/api'

interface AgentTask {
  id: string
  room_id: string
  room_name: string
  title: string
  status: string
  assignee_participant_id: string | null
  created_by: string | null
  created_at: string
}

const STATUS_ORDER = ['todo', 'in_progress', 'done', 'blocked'] as const
const STATUS_LABEL: Record<string, string> = {
  todo: 'Todo',
  in_progress: 'In Progress',
  done: 'Done',
  blocked: 'Blocked',
}
const STATUS_ICON: Record<string, typeof Circle> = {
  todo: Circle,
  in_progress: Clock,
  done: CheckCircle2,
  blocked: AlertTriangle,
}

export default function TasksPanel({ agentId }: { agentId: string | null }) {
  const [tasks, setTasks] = useState<AgentTask[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const navigate = useNavigate()

  const fetchTasks = useCallback(async () => {
    if (!agentId) return
    const resp = await apiFetch(`/api/v1/agents/${agentId}/tasks`)
    if (resp.status === 403) {
      setError('admin_only')
      setTasks([])
      return
    }
    if (!resp.ok) {
      setError('error')
      setTasks([])
      return
    }
    setError(null)
    setTasks(await resp.json())
  }, [agentId])

  useEffect(() => { fetchTasks() }, [fetchTasks])

  // #266 — subscribe to the WS task fanout so the panel stays live
  // without polling. We refetch any time a task event mentions our
  // agent or includes a task whose ``agent_id`` matches.
  useEffect(() => {
    if (!agentId) return
    const handler = (e: Event) => {
      const detail = (e as CustomEvent).detail as
        | { task?: { agent_id?: string | null } }
        | undefined
      if (!detail?.task) return
      // Only refetch when the event is *plausibly* relevant. The
      // backend includes ``agent_id`` on the payload (resolved from
      // the assignee participant) so we can scope cheaply.
      if (detail.task.agent_id && detail.task.agent_id !== agentId) return
      fetchTasks()
    }
    window.addEventListener('doorae:task:updated', handler)
    return () => window.removeEventListener('doorae:task:updated', handler)
  }, [agentId, fetchTasks])

  const grouped = useMemo(() => {
    const m = new Map<string, AgentTask[]>()
    for (const s of STATUS_ORDER) m.set(s, [])
    for (const t of tasks ?? []) {
      const bucket = m.get(t.status) ?? m.get('todo')!
      bucket.push(t)
    }
    return m
  }, [tasks])

  if (!agentId) return null

  if (error === 'admin_only') {
    return (
      <p className="text-sm text-[var(--color-foreground-muted)]">
        Admin-only view. (Tasks aggregation across rooms requires admin
        access.)
      </p>
    )
  }

  if (tasks === null) {
    return (
      <p className="text-sm text-[var(--color-foreground-subtle)]">
        Loading tasks…
      </p>
    )
  }

  if (tasks.length === 0) {
    return (
      <p className="text-sm text-[var(--color-foreground-muted)]">
        No tasks assigned.
      </p>
    )
  }

  return (
    <div className="space-y-4">
      {STATUS_ORDER.map(status => {
        const rows = grouped.get(status) ?? []
        if (rows.length === 0) return null
        const Icon = STATUS_ICON[status]
        return (
          <section key={status}>
            <h4 className="mb-1.5 flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-[0.06em] text-[var(--color-foreground-muted)]">
              <Icon className="h-3 w-3" />
              {STATUS_LABEL[status]}
              <span className="text-[var(--color-foreground-subtle)]">
                ({rows.length})
              </span>
            </h4>
            <ul className="space-y-1">
              {rows.map(t => (
                <li
                  key={t.id}
                  data-testid={`agent-task-row-${t.id}`}
                  className="flex items-center gap-2 rounded-[var(--radius-sm)] px-2 py-1.5 hover:bg-[var(--color-surface-alt)]"
                >
                  <span className="flex-1 truncate text-sm text-[var(--color-foreground)]">
                    {t.title}
                  </span>
                  <button
                    type="button"
                    onClick={() => navigate(`/rooms/${t.room_id}`)}
                    className="inline-flex max-w-[12rem] items-center rounded-full border border-[rgba(0,0,0,0.1)] bg-white px-2 py-0.5 text-[11px] text-[var(--color-foreground-muted)] hover:border-[var(--color-brand)] hover:text-[var(--color-brand)] transition-colors"
                    title={`Open ${t.room_name}`}
                  >
                    <span className="truncate">{t.room_name}</span>
                  </button>
                </li>
              ))}
            </ul>
          </section>
        )
      })}
    </div>
  )
}
