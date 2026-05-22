import { useState, useEffect, useCallback } from 'react'
import { apiFetch } from '@/lib/api'

// Task shape mirrors `TaskOut` in packages/cluster/anygarden/api/v1/tasks.py.
// Phase 1 of #302 keeps the schema unchanged; the goal-derived columns
// (#302 Phase 2 — goal_id, triggered_by, spec, started_at, finished_at,
// agent_session_id, tokens_used, result_markdown, error, is_interesting)
// land alongside the migration. Forward-compat optional fields are
// declared here so upgrade-path UIs read them safely against an older
// server.
export interface Task {
  id: string
  room_id: string
  title: string
  status: string
  assignee_participant_id: string | null
  created_by?: string | null
  created_at: string
  // Optional — populated only when the server has run the #302
  // migration. Older servers omit them; consumers must guard accordingly.
  goal_id?: string | null
  triggered_by?: string | null
}

export interface UseRoomTasksOptions {
  /** Status filter — passed through as ``?status=`` query param. */
  status?: string | null
  /** Goal id filter (#302 Phase 2). Currently passes through to the
   *  server which ignores it pre-migration; once the migration lands,
   *  the server filters by ``tasks.goal_id``. */
  goalId?: string | null
}

export interface UseRoomTasksValue {
  tasks: Task[]
  loading: boolean
  error: string | null
  refresh: () => Promise<void>
  create: (input: {
    title: string
    assignee_participant_id?: string | null
  }) => Promise<Task | null>
  update: (
    id: string,
    patch: Partial<Pick<Task, 'title' | 'status' | 'assignee_participant_id'>>,
  ) => Promise<void>
  remove: (id: string) => Promise<void>
}

/**
 * Subscribe to a room's tasks (#266 / #302).
 *
 * Owns: REST fetch + ``anygarden:task:updated`` WS subscription + CRUD
 * actions. UI components consume the returned state and never touch
 * ``apiFetch`` directly — this guarantees that Tasks rendered in the
 * legacy panel and the new right-rail section stay byte-identical and
 * react to the same WS events.
 *
 * Pass ``roomId === null`` to suspend fetching (useful when the host
 * page hasn't picked a room yet).
 */
export function useRoomTasks(
  roomId: string | null,
  opts: UseRoomTasksOptions = {},
): UseRoomTasksValue {
  const { status, goalId } = opts
  const [tasks, setTasks] = useState<Task[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    if (!roomId) {
      setTasks([])
      return
    }
    const params = new URLSearchParams()
    if (status) params.set('status', status)
    if (goalId) params.set('goal_id', goalId)
    const qs = params.toString()
    setLoading(true)
    setError(null)
    try {
      const resp = await apiFetch(
        `/api/v1/rooms/${roomId}/tasks${qs ? '?' + qs : ''}`,
      )
      if (resp.ok) {
        setTasks(await resp.json())
      } else {
        setTasks([])
        setError(`Fetch failed (HTTP ${resp.status})`)
      }
    } catch (e) {
      setTasks([])
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }, [roomId, status, goalId])

  useEffect(() => {
    refresh()
  }, [refresh])

  // #266 — refetch when the server pushes a ``task.updated`` frame.
  // Listening on ``window`` keeps this hook independent of where the
  // WS is mounted in the React tree (ChatPage owns the connection).
  useEffect(() => {
    if (!roomId) return
    const handler = (e: Event) => {
      const detail = (e as CustomEvent).detail as
        | { task?: { room_id?: string } }
        | undefined
      if (!detail?.task) return
      // Ignore events for other rooms — the hook only mirrors the
      // currently selected room's task list.
      if (detail.task.room_id && detail.task.room_id !== roomId) return
      refresh()
    }
    window.addEventListener('anygarden:task:updated', handler)
    return () => window.removeEventListener('anygarden:task:updated', handler)
  }, [roomId, refresh])

  const create = useCallback<UseRoomTasksValue['create']>(
    async (input) => {
      if (!roomId) return null
      const resp = await apiFetch(`/api/v1/rooms/${roomId}/tasks`, {
        method: 'POST',
        body: JSON.stringify({
          title: input.title,
          assignee_participant_id: input.assignee_participant_id ?? null,
        }),
      })
      if (!resp.ok) {
        setError(`Create failed (HTTP ${resp.status})`)
        return null
      }
      const created = (await resp.json()) as Task
      await refresh()
      return created
    },
    [roomId, refresh],
  )

  const update = useCallback<UseRoomTasksValue['update']>(
    async (id, patch) => {
      const resp = await apiFetch(`/api/v1/tasks/${id}`, {
        method: 'PUT',
        body: JSON.stringify(patch),
      })
      if (!resp.ok) {
        setError(`Update failed (HTTP ${resp.status})`)
        return
      }
      await refresh()
    },
    [refresh],
  )

  const remove = useCallback<UseRoomTasksValue['remove']>(
    async (id) => {
      const resp = await apiFetch(`/api/v1/tasks/${id}`, { method: 'DELETE' })
      if (!resp.ok && resp.status !== 204) {
        setError(`Delete failed (HTTP ${resp.status})`)
        return
      }
      await refresh()
    },
    [refresh],
  )

  return { tasks, loading, error, refresh, create, update, remove }
}
