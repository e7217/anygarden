import { useState, useEffect, useCallback, useMemo, useRef } from 'react'
import { apiFetch } from '@/lib/api'
import type { ExecutionDisposition, ExecutionOperationAction } from '@/hooks/useProjectExecutions'

export interface TaskDependencyResult {
  task_id: string
  room_id?: string
  title: string
  result_markdown: string | null
  result_sha256: string | null
  finished_at?: string | null
  result_id?: string
  result_version?: number
}

export interface TaskScheduleContext {
  goal_id: string
  scheduled_for: string | null
  timezone: string
  overlap_policy: 'wait'
  trigger_source: string
}

export interface TaskBlockerSummary {
  task_id: string
  title: string
  room_id: string
  room_name: string
  status: string
}

// Mirrors TaskOut. Optional execution fields retain compatibility with
// servers and manual tasks that do not supply execution evidence.
export interface TaskRecovery {
  state: 'none' | 'running' | 'retry_wait' | 'retrying' | 'failed' | 'action_required' | 'completed' | 'cancelled' | 'historical'
  reason_code?: string | null
  next_action?: 'none' | 'wait_for_retry' | 'check_agent' | 'review_failure' | 'answer_question' | 'review_approval' | 'await_safe_stop' | 'fix_configuration_and_retry' | 'restore_authentication_and_retry' | 'retry_task' | 'review_task_details'
  can_retry?: boolean
  request_id?: string | null
  active_attempt?: number | null
  attempt_count?: number
  completed_attempt_count?: number
  retry_count?: number
  max_retries?: number
  next_retry_at?: string | null
  attempts?: {
    ordinal: number
    state: string
    outcome: string | null
    reason_code?: string | null
    started_at?: string | null
    finished_at?: string | null
  }[]
}

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
  spec?: string | null
  result_markdown?: string | null
  error?: string | null
  recovery?: TaskRecovery | null
  dependency_results?: TaskDependencyResult[] | null
  schedule_context?: TaskScheduleContext | null
  is_silent?: boolean
  execution_id?: string | null
  parent_task_id?: string | null
  parent_task_title?: string | null
  input_revision?: number | null
  delegation_depth?: number
  role?: string | null
  result_version?: number
  room_name?: string | null
  assignee_display_name?: string | null
  execution_operating_room_id?: string | null
  execution_source_message_id?: string | null
  execution_objective?: string | null
  execution_status?: string | null
  execution_input_revision?: number | null
  execution_operation_action?: ExecutionOperationAction | null
  execution_error?: string | null
  is_current?: boolean
  disposition?: ExecutionDisposition
  blocked_by?: TaskBlockerSummary[]
}

export interface UseRoomTasksOptions {
  /** Status filter — passed through as ``?status=`` query param. */
  status?: string | null
  /** Goal history includes silent runs that are hidden from the work queue. */
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
      const rows = await resp.json() as Task[]
      const tasks = goalId ? rows : rows.filter(task => !task.is_silent)
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
      const detail = (event as CustomEvent).detail as { task?: { room_id?: string; execution_operating_room_id?: string } } | undefined
      if (detail?.task && (!detail.task.room_id || detail.task.room_id === roomId || detail.task.execution_operating_room_id === roomId)) void refresh()
    }
    window.addEventListener('anygarden:task:updated', handler)
    const executionHandler = (event: Event) => {
      const detail = (event as CustomEvent).detail as { operating_room_id?: string } | undefined
      if (detail?.operating_room_id === roomId) void refresh()
    }
    window.addEventListener('anygarden:execution:updated', executionHandler)
    return () => {
      window.removeEventListener('anygarden:task:updated', handler)
      window.removeEventListener('anygarden:execution:updated', executionHandler)
    }
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
