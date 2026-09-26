/**
 * TasksPanel — agent-scoped task aggregation (#266 Step 9, #320 collapse).
 *
 * Shows every task currently assigned to the agent across every room
 * it participates in. Powered by ``GET /api/v1/agents/{id}/tasks``,
 * which is admin-only in Phase 1 (plan §3.2 결정 3).
 *
 * Status grouping (#320): five status buckets, partitioned into Active
 * vs Terminal by meaning rather than by lifecycle stage. ``blocked`` is
 * Active because the router (``routing/router.py:165``) still treats it
 * as in-flight work — a stuck task that the user/agent should still
 * see, not buried with completed work. ``failed`` is Terminal because
 * the goals sweeper sets it as a final state. Active sections render
 * open by default; Terminal sections render closed and offer a
 * per-item trash plus a header "Clear all" backed by
 * ``DELETE /api/v1/agents/{id}/tasks?status=<done|failed>``.
 *
 * Realtime: subscribes to ``anygarden:task:updated`` window events
 * dispatched by ``useWebSocket``. We refetch the whole list on any
 * relevant event — incremental merging would shave ~10 LOC of UI
 * code without changing the user-facing latency.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  CheckCircle2,
  Circle,
  Clock,
  AlertTriangle,
  XCircle,
  ChevronRight,
  Trash2,
} from 'lucide-react'
import { apiFetch } from '@/lib/api'
import { useLocale } from '@/i18n/LocaleProvider'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'

export interface AgentTask {
  id: string
  room_id: string
  room_name: string
  title: string
  status: string
  assignee_participant_id: string | null
  created_by: string | null
  created_at: string
  source_message_id?: string | null
  source_thread_root_id?: string | null
}

// ── Status taxonomy (#320) ───────────────────────────────────────────
//
// Active states stay visible by default because the user/agent may
// still need to act on them. Terminal states are reference-only and
// collapse out of the way to fight scroll bloat. ``blocked`` belongs
// in Active per ``routing/router.py:165`` even though it represents
// stalled work — hiding it would make stuck tasks invisible.
type Status = 'todo' | 'in_progress' | 'blocked' | 'done' | 'failed'

export const STATUS_ORDER: readonly Status[] = [
  'todo',
  'in_progress',
  'blocked',
  'done',
  'failed',
] as const

const ACTIVE_STATUSES: readonly Status[] = ['todo', 'in_progress', 'blocked']
const TERMINAL_STATUSES: readonly Status[] = ['done', 'failed']

const STATUS_ICON: Record<Status, typeof Circle> = {
  todo: Circle,
  in_progress: Clock,
  blocked: AlertTriangle,
  done: CheckCircle2,
  failed: XCircle,
}

const SLICE_LIMIT = 20

function isTerminal(s: Status): boolean {
  return TERMINAL_STATUSES.includes(s)
}

/**
 * Group tasks into known status buckets. Unknown statuses are dropped
 * (with a console warning) rather than silently absorbed into ``todo``
 * — the previous fallback caused #320's display bug where sweeper-set
 * ``failed`` tasks rendered under "Todo".
 *
 * Exported for unit tests; the component just spreads it into a Map.
 */
export function groupTasksByStatus(
  tasks: readonly AgentTask[],
): Record<Status, AgentTask[]> {
  const out: Record<Status, AgentTask[]> = {
    todo: [],
    in_progress: [],
    blocked: [],
    done: [],
    failed: [],
  }
  const known = new Set<Status>(STATUS_ORDER)
  for (const t of tasks) {
    if (known.has(t.status as Status)) {
      out[t.status as Status].push(t)
    } else {
      // Unknown status — log once per render so a new sweeper-side
      // status doesn't disappear on us silently. Adding a new bucket
      // is a code change, not a runtime accident.
      // eslint-disable-next-line no-console
      console.warn(`[TasksPanel] unknown task status: ${t.status}`)
    }
  }
  return out
}

export default function TasksPanel({ agentId }: { agentId: string | null }) {
  const { t } = useLocale()
  const statusLabels: Record<Status, string> = {
    todo: t('admin.tasks.status.todo'),
    in_progress: t('admin.tasks.status.inProgress'),
    blocked: t('admin.tasks.status.blocked'),
    done: t('admin.tasks.status.done'),
    failed: t('admin.tasks.status.failed'),
  }
  const [tasks, setTasks] = useState<AgentTask[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [showAll, setShowAll] = useState<Partial<Record<Status, boolean>>>({})
  const [confirmClear, setConfirmClear] = useState<Status | null>(null)
  const [busy, setBusy] = useState(false)
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

  useEffect(() => {
    fetchTasks()
  }, [fetchTasks])

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
      if (detail.task.agent_id && detail.task.agent_id !== agentId) return
      fetchTasks()
    }
    window.addEventListener('anygarden:task:updated', handler)
    return () => window.removeEventListener('anygarden:task:updated', handler)
  }, [agentId, fetchTasks])

  const grouped = useMemo(() => groupTasksByStatus(tasks ?? []), [tasks])

  const handleDelete = useCallback(
    async (taskId: string) => {
      const resp = await apiFetch(`/api/v1/tasks/${taskId}`, { method: 'DELETE' })
      if (!resp.ok) {
        // eslint-disable-next-line no-console
        console.error(`[TasksPanel] delete ${taskId} failed: ${resp.status}`)
        return
      }
      // The WS fanout in delete_task should refresh us, but refetch
      // explicitly so the row disappears even if the listener missed
      // the event (e.g. tab just regained focus).
      await fetchTasks()
    },
    [fetchTasks],
  )

  const handleClearAll = useCallback(
    async (status: Status) => {
      if (!agentId) return
      setBusy(true)
      try {
        const resp = await apiFetch(
          `/api/v1/agents/${agentId}/tasks?status=${status}`,
          { method: 'DELETE' },
        )
        if (!resp.ok) {
          // eslint-disable-next-line no-console
          console.error(`[TasksPanel] clear ${status} failed: ${resp.status}`)
          return
        }
        await fetchTasks()
      } finally {
        setBusy(false)
        setConfirmClear(null)
      }
    },
    [agentId, fetchTasks],
  )

  if (!agentId) return null

  if (error === 'admin_only') {
    return (
      <p className="text-sm text-[var(--color-foreground-muted)]">
        {t('admin.tasks.adminOnly')}
      </p>
    )
  }

  if (tasks === null) {
    return (
      <p className="text-sm text-[var(--color-foreground-subtle)]">
        {t('admin.tasks.loading')}
      </p>
    )
  }

  if (tasks.length === 0) {
    return (
      <p className="text-sm text-[var(--color-foreground-muted)]">
        {t('admin.tasks.none')}
      </p>
    )
  }

  const confirmCount = confirmClear ? grouped[confirmClear].length : 0

  return (
    <div className="space-y-2">
      {STATUS_ORDER.map(status => {
        const rows = grouped[status]
        const total = rows.length
        const Icon = STATUS_ICON[status]
        const terminal = isTerminal(status)
        const expanded = showAll[status] ?? false
        // ASC by created_at on the wire; "latest 20" means the tail.
        const visible = expanded || total <= SLICE_LIMIT
          ? rows
          : rows.slice(total - SLICE_LIMIT)
        const hidden = total - visible.length

        return (
          <details
            key={status}
            data-testid={`tasks-section-${status}`}
            className="group"
            open={ACTIVE_STATUSES.includes(status)}
          >
            <summary className="flex items-center gap-1.5 cursor-pointer list-none select-none py-1.5">
              <ChevronRight
                className="h-3 w-3 shrink-0 text-[var(--color-foreground-subtle)] transition-transform group-open:rotate-90"
                aria-hidden="true"
              />
              <Icon className="h-3 w-3 text-[var(--color-foreground-muted)]" />
              <span className="text-[11px] font-medium uppercase tracking-[0.06em] text-[var(--color-foreground-muted)]">
                {statusLabels[status]}
              </span>
              <span className="text-[11px] text-[var(--color-foreground-subtle)]">
                ({total})
              </span>
              {terminal && total > 0 ? (
                <button
                  type="button"
                  onClick={e => {
                    e.preventDefault()
                    e.stopPropagation()
                    setConfirmClear(status)
                  }}
                  className="ml-auto text-[11px] text-[var(--color-foreground-muted)] hover:text-[var(--color-foreground)] transition-colors"
                  data-testid={`tasks-clear-all-${status}`}
                >
                  {t('admin.tasks.clearAll')}
                </button>
              ) : null}
            </summary>
            {total === 0 ? null : (
              <div className="mt-1 max-h-80 overflow-y-auto pr-1">
                <ul className="space-y-1">
                  {visible.map(task => (
                    <li
                      key={task.id}
                      data-testid={`agent-task-row-${task.id}`}
                      className="group/row flex items-center gap-2 rounded-[var(--radius-sm)] px-2 py-1.5 hover:bg-[var(--color-surface-alt)]"
                    >
                      <span className="flex-1 truncate text-sm text-[var(--color-foreground)]">
                        {task.title}
                      </span>
                      <button
                        type="button"
                        onClick={() => navigate(`/rooms/${task.room_id}`)}
                        className="inline-flex max-w-[12rem] items-center rounded-full border border-[var(--color-border)] bg-[var(--color-surface-elevated)] px-2 py-0.5 text-[11px] text-[var(--color-foreground-muted)] hover:border-[var(--color-brand)] hover:text-[var(--color-link)] transition-colors"
                        title={t('admin.tasks.openRoom', { name: task.room_name })}
                      >
                        <span className="truncate">{task.room_name}</span>
                      </button>
                      {terminal && !task.source_message_id ? (
                        <button
                          type="button"
                          onClick={() => handleDelete(task.id)}
                          className="opacity-0 group-hover/row:opacity-100 transition-opacity text-[var(--color-foreground-subtle)] hover:text-[var(--color-foreground)]"
                          aria-label={t('admin.tasks.delete', { title: task.title })}
                          data-testid={`agent-task-delete-${task.id}`}
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                        </button>
                      ) : null}
                    </li>
                  ))}
                </ul>
                {hidden > 0 ? (
                  <button
                    type="button"
                    onClick={() =>
                      setShowAll(prev => ({ ...prev, [status]: true }))
                    }
                    className="mt-1.5 w-full rounded-[var(--radius-sm)] py-1 text-[11px] text-[var(--color-foreground-muted)] hover:bg-[var(--color-surface-alt)] hover:text-[var(--color-foreground)] transition-colors"
                    data-testid={`tasks-show-all-${status}`}
                  >
                    {t('admin.tasks.showAll', { count: total })}
                  </button>
                ) : null}
              </div>
            )}
          </details>
        )
      })}

      <Dialog
        open={confirmClear !== null}
        onOpenChange={open => {
          if (!open) setConfirmClear(null)
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t('admin.tasks.clearTitle', { status: confirmClear ? statusLabels[confirmClear] : '' })}</DialogTitle>
            <DialogDescription>
              {t(confirmCount === 1 ? 'admin.tasks.clearDescriptionOne' : 'admin.tasks.clearDescription', {
                count: confirmCount,
                status: confirmClear ? statusLabels[confirmClear].toLowerCase() : '',
              })}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <button
              type="button"
              onClick={() => setConfirmClear(null)}
              disabled={busy}
              className="rounded-[var(--radius-sm)] border border-[var(--color-border)] bg-[var(--color-surface-elevated)] px-3 py-1.5 text-sm text-[var(--color-foreground)] hover:bg-[var(--color-surface-hover)] transition-colors"
            >
              {t('common.cancel')}
            </button>
            <button
              type="button"
              onClick={() => confirmClear && handleClearAll(confirmClear)}
              disabled={busy}
              className="rounded-[var(--radius-sm)] bg-[var(--color-brand)] px-3 py-1.5 text-sm text-[var(--color-on-brand)] hover:bg-[var(--color-brand-hover)] transition-colors disabled:opacity-50"
              data-testid="tasks-clear-all-confirm"
            >
              {busy ? t('admin.tasks.clearing') : t('admin.tasks.clear')}
            </button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
