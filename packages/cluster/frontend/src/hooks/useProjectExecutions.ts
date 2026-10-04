import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { apiFetch } from '@/lib/api'
import type { RoomSharedFile } from '@/lib/roomFiles'
import type { ExecutionApproval } from '@/hooks/useExecutionApprovals'

export type ExecutionDisposition = 'current' | 'superseded' | 'cancelled' | 'cancelling'
export type ExecutionOperationAction = 'revise' | 'cancel' | 'deadline' | 'limit'
export interface ExecutionUsageTotals {
  invocation_count: number
  pending_invocations: number; measured_invocations: number; unknown_invocations: number; not_started_invocations: number
  known_input_tokens: number | null; known_output_tokens: number | null; known_cached_input_tokens: number | null; known_total_tokens: number | null
  input_tokens: number | null; output_tokens: number | null; cached_input_tokens: number | null; total_tokens: number | null
  known_cost_usd: string | null; cost_usd: string | null
  cost_measured_invocations: number; cost_unknown_invocations: number
}
export interface ExecutionUsageSummary extends ExecutionUsageTotals {
  version: 'native-invocation-v1'
  native_invocations_reserved: number
  unknown_reasons?: Record<string, number>
  coverage: {
    source_adoption_recorded: boolean; missing_permitted_attempts: number; expected_permitted_attempts: number
    accounting_counter_matches: boolean; native_reservation_complete: boolean
    status: 'complete' | 'partial_or_legacy_unknown'; unattributed_legacy_invocations_possible: boolean
  }
  revisions: (ExecutionUsageTotals & { input_revision: number })[]
  as_of: string | null
  provider_model_calls: null
  enforcement: {
    scope: 'managed_native_admission'; provider_request_cap_enforced: false
    token_limit_mode: 'observed_after_terminal'; cost_hard_cap_enforced: false
    initial_limit_declared_after_native_start: boolean
  }
}
export interface ExecutionLimits {
  max_native_invocations?: number; max_total_tokens?: number
  max_delegations?: number; max_depth?: number; max_repair_rounds?: number; deadline_at?: string
}
export interface ExecutionInputFile { file_id: string; room_id?: string; filename: string; sha256: string; size_bytes?: number }
export interface StopSummary {
  total: number; pending: number; confirmed: number; not_started: number
  already_finished: number; unknown: number; all_confirmed: boolean
}
export interface ExecutionOperation {
  id: string; operation_id: string; action: ExecutionOperationAction
  phase: 'awaiting_stop' | 'applied' | 'superseded'; reason: string
  previous_input_revision: number; input_revision: number
  requested_at: string; completed_at?: string | null
}
export interface ProjectExecution {
  id: string; operating_room_id: string; source_message_id: string | null
  objective: string; status: string; input_revision: number; state_revision: number
  completion_criteria: string[]; can_manage: boolean
  current_constraints?: string; current_input_files?: ExecutionInputFile[]
  latest_operation?: ExecutionOperation | null; stop_summary?: StopSummary | null
  usage_summary?: ExecutionUsageSummary | null; limits?: ExecutionLimits | null
  error?: string | null; created_at: string; finished_at?: string | null
}
export interface ExecutionDetail {
  execution: ProjectExecution
  input_revisions: { revision: number; objective: string; user_constraints: string; completion_criteria: string[]; input_files: ExecutionInputFile[]; reason?: string | null; created_at: string }[]
  tasks: { id: string; title: string; input_revision: number; status: string; disposition?: ExecutionDisposition }[]
  results: { id: string; task_id: string; input_revision: number; version: number; is_current?: boolean; disposition?: ExecutionDisposition }[]
  approvals?: ExecutionApproval[]
  effects?: ExecutionApproval[]
}
export interface RevisionInput {
  operation_id: string; expected_input_revision: number; expected_state_revision: number
  objective: string; constraints: string; completion_criteria: string[]
  input_files: { file_id: string; sha256: string }[]; reason: string; change_scope: 'all'
}
export interface CancelInput { operation_id: string; expected_input_revision: number; expected_state_revision: number; reason: string }

async function responseError(response: Response): Promise<string> {
  try {
    const body = await response.json() as { detail?: string | { detail?: string } }
    const detail = typeof body.detail === 'string' ? body.detail : body.detail?.detail
    if (detail) return `HTTP ${response.status}: ${detail}`
  } catch { /* Retain the HTTP status when no public detail is available. */ }
  return `HTTP ${response.status}`
}

export function useProjectExecutions(roomId: string, userId: string | null) {
  const scope = useMemo(() => ({ active: true, sequence: 0, controller: null as AbortController | null }), [roomId, userId])
  const currentScope = useRef(scope)
  currentScope.current = scope
  const isCurrent = useCallback(() => scope.active && currentScope.current === scope, [scope])
  const [snapshot, setSnapshot] = useState<{ scope: typeof scope; executions: ProjectExecution[]; error: string | null }>({ scope, executions: [], error: null })
  const refresh = useCallback(async () => {
    if (!userId || !isCurrent()) return
    const sequence = ++scope.sequence
    scope.controller?.abort()
    const controller = new AbortController()
    scope.controller = controller
    try {
      const response = await apiFetch(`/api/v1/rooms/${roomId}/executions`, { signal: controller.signal })
      if (!response.ok) {
        const error = await responseError(response)
        if ([401, 403, 404].includes(response.status) && isCurrent() && sequence === scope.sequence) {
          setSnapshot({ scope, executions: [], error })
          return
        }
        throw new Error(error)
      }
      const executions = await response.json() as ProjectExecution[]
      if (isCurrent() && sequence === scope.sequence) setSnapshot({ scope, executions, error: null })
    } catch (error) {
      if (isCurrent() && sequence === scope.sequence && !controller.signal.aborted) setSnapshot(previous => ({ scope, executions: previous.scope === scope ? previous.executions : [], error: error instanceof Error ? error.message : 'HTTP 500' }))
    }
  }, [roomId, userId, scope, isCurrent])
  useEffect(() => {
    scope.active = true
    void refresh()
    const onEvent = () => { if (document.visibilityState === 'visible') void refresh() }
    const interval = window.setInterval(onEvent, 15000)
    window.addEventListener('anygarden:execution:updated', onEvent)
    window.addEventListener('anygarden:task:updated', onEvent)
    window.addEventListener('focus', onEvent)
    window.addEventListener('online', onEvent)
    document.addEventListener('visibilitychange', onEvent)
    return () => {
      scope.active = false
      scope.controller?.abort()
      window.clearInterval(interval)
      window.removeEventListener('anygarden:execution:updated', onEvent)
      window.removeEventListener('anygarden:task:updated', onEvent)
      window.removeEventListener('focus', onEvent)
      window.removeEventListener('online', onEvent)
      document.removeEventListener('visibilitychange', onEvent)
    }
  }, [scope, refresh])
  const read = useCallback(async <T,>(path: string): Promise<T> => {
    const response = await apiFetch(path)
    if (!isCurrent()) throw new Error('Scope changed')
    if (!response.ok) {
      if ([401, 403].includes(response.status)) {
        ++scope.sequence
        scope.controller?.abort()
        setSnapshot({ scope, executions: [], error: null })
      }
      throw new Error(await responseError(response))
    }
    const data = await response.json() as T
    if (!isCurrent()) throw new Error('Scope changed')
    return data
  }, [scope, isCurrent])
  const detail = useCallback((id: string) => read<ExecutionDetail>(`/api/v1/executions/${id}`), [read])
  const files = useCallback(() => read<RoomSharedFile[]>(`/api/v1/rooms/${roomId}/files`), [roomId, read])
  const mutate = useCallback(async (id: string, action: 'input-revisions' | 'cancel', body: RevisionInput | CancelInput) => {
    if (!isCurrent()) return { detail: null, error: null }
    try {
      const response = await apiFetch(`/api/v1/executions/${id}/${action}`, { method: 'POST', body: JSON.stringify(body) })
      if (!isCurrent()) return { detail: null, error: null }
      if (!response.ok) {
        if ([401, 403].includes(response.status)) {
          ++scope.sequence
          scope.controller?.abort()
          setSnapshot(previous => ({ scope, error: null, executions: previous.scope === scope ? previous.executions.map(row => row.id === id ? { ...row, can_manage: false } : row) : [] }))
        }
        throw new Error(await responseError(response))
      }
      const data = await response.json() as ExecutionDetail
      if (!isCurrent() || data.execution.operating_room_id !== roomId) return { detail: null, error: null }
      ++scope.sequence
      scope.controller?.abort()
      setSnapshot(previous => ({ scope, error: null, executions: previous.scope === scope ? previous.executions.map(row => row.id === id ? data.execution : row) : [] }))
      window.dispatchEvent(new CustomEvent('anygarden:execution:updated', { detail: { execution_id: id, operating_room_id: roomId } }))
      await refresh()
      return isCurrent() ? { detail: data, error: null } : { detail: null, error: null }
    } catch (error) {
      return isCurrent() ? { detail: null, error: error instanceof Error ? error.message : 'HTTP 500' } : { detail: null, error: null }
    }
  }, [roomId, scope, isCurrent, refresh])
  const current = snapshot.scope === scope ? snapshot : { executions: [], error: null }
  return { ...current, refresh, detail, files, mutate }
}
