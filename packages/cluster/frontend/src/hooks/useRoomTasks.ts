import { useState, useEffect, useCallback, useMemo, useRef } from 'react'
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
  source_message_id?: string | null
  source_thread_root_id?: string | null
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
  createFromMessage: (
    messageId: string,
    input: { title: string; assignee_participant_id?: string | null },
  ) => Promise<Task | null>
  claim: (id: string) => Promise<Task | null>
  requeue: (
    id: string,
    input: { reason: string; assignee_participant_id?: string | null },
  ) => Promise<Task | null>
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
  // The scope object distinguishes A → B → A, not just different room IDs.
  // Store it with each snapshot so old rows disappear in the first render.
  const scope = useMemo(() => ({ roomId, status, goalId, active: true, request: 0 }), [roomId, status, goalId])
  const currentScope = useRef(scope)
  currentScope.current = scope
  const [snapshot, setSnapshot] = useState<{ scope: typeof scope; tasks: Task[]; loading: boolean; error: string | null }>(
    () => ({ scope, tasks: [], loading: Boolean(roomId), error: null }),
  )
  const isCurrent = useCallback(() => scope.active && currentScope.current === scope, [scope])

  const refresh = useCallback(async () => {
    if (!roomId || !isCurrent()) return
    const request = ++scope.request
    const accepts = () => isCurrent() && scope.request === request
    const params = new URLSearchParams()
    if (status) params.set('status', status)
    if (goalId) params.set('goal_id', goalId)
    const qs = params.toString()
    setSnapshot(previous => ({ scope, tasks: previous.scope === scope ? previous.tasks : [], loading: true, error: null }))
    try {
      const resp = await apiFetch(`/api/v1/rooms/${roomId}/tasks${qs ? '?' + qs : ''}`)
      if (!resp.ok) throw new Error(`Fetch failed (HTTP ${resp.status})`)
      const tasks = await resp.json() as Task[]
      if (accepts()) setSnapshot({ scope, tasks, loading: false, error: null })
    } catch (error) {
      if (accepts()) setSnapshot({ scope, tasks: [], loading: false, error: error instanceof Error ? error.message : String(error) })
    }
  }, [roomId, status, goalId, scope, isCurrent])

  useEffect(() => {
    scope.active = true
    void refresh()
    return () => { scope.active = false }
  }, [scope, refresh])

  useEffect(() => {
    if (!roomId) return
    const handler = (event: Event) => {
      const detail = (event as CustomEvent).detail as { task?: { room_id?: string } } | undefined
      if (detail?.task && (!detail.task.room_id || detail.task.room_id === roomId)) void refresh()
    }
    window.addEventListener('anygarden:task:updated', handler)
    return () => window.removeEventListener('anygarden:task:updated', handler)
  }, [roomId, refresh])

  // An action may finish after its room has gone away. Neither the follow-up
  // query nor its error/return value may affect the newly selected room.
  const mutate = useCallback(async (path: string, init: RequestInit, label: string, returnsTask = false): Promise<Task | null> => {
    if (!roomId || !isCurrent()) return null
    try {
      const resp = await apiFetch(path, init)
      if (!isCurrent()) return null
      if (!resp.ok) throw new Error(`${label} failed (HTTP ${resp.status})`)
      const task = returnsTask ? await resp.json() as Task : null
      if (!isCurrent()) return null
      await refresh()
      return isCurrent() ? task : null
    } catch (error) {
      if (isCurrent()) {
        ++scope.request
        setSnapshot(previous => ({ scope, tasks: previous.scope === scope ? previous.tasks : [], loading: false, error: error instanceof Error ? error.message : String(error) }))
      }
      return null
    }
  }, [roomId, scope, isCurrent, refresh])

  const create = useCallback<UseRoomTasksValue['create']>(input => mutate(`/api/v1/rooms/${roomId}/tasks`, {
    method: 'POST', body: JSON.stringify({ title: input.title, assignee_participant_id: input.assignee_participant_id ?? null }),
  }, 'Create', true), [roomId, mutate])

  const createFromMessage = useCallback<UseRoomTasksValue['createFromMessage']>((messageId, input) => mutate(`/api/v1/rooms/${roomId}/messages/${messageId}/task`, {
    method: 'POST', body: JSON.stringify({ title: input.title, assignee_participant_id: input.assignee_participant_id ?? null }),
  }, 'Create from message', true), [roomId, mutate])

  const claim = useCallback<UseRoomTasksValue['claim']>(id => mutate(`/api/v1/tasks/${id}/claim`, { method: 'POST' }, 'Claim', true), [mutate])
  const requeue = useCallback<UseRoomTasksValue['requeue']>((id, input) => mutate(`/api/v1/tasks/${id}/requeue`, {
    method: 'POST', body: JSON.stringify({ reason: input.reason, assignee_participant_id: input.assignee_participant_id ?? null }),
  }, 'Requeue', true), [mutate])
  const update = useCallback<UseRoomTasksValue['update']>(async (id, patch) => {
    await mutate(`/api/v1/tasks/${id}`, { method: 'PUT', body: JSON.stringify(patch) }, 'Update')
  }, [mutate])
  const remove = useCallback<UseRoomTasksValue['remove']>(async id => {
    await mutate(`/api/v1/tasks/${id}`, { method: 'DELETE' }, 'Delete')
  }, [mutate])

  const current = snapshot.scope === scope && roomId ? snapshot : { tasks: [], loading: Boolean(roomId), error: null }
  return { tasks: current.tasks, loading: current.loading, error: current.error, refresh, create, createFromMessage, claim, requeue, update, remove }
}
