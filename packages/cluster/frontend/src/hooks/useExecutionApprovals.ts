import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { apiFetch } from '@/lib/api'
import type { ExecutionDisposition, ExecutionOperationAction } from '@/hooks/useProjectExecutions'

export type ApprovalStatus = 'pending' | 'approved' | 'rejected' | 'executing' | 'succeeded' | 'failed' | 'unknown'
export type ApprovalDecision = 'approve' | 'reject'

export interface ExecutionApproval {
  id: string
  execution_id: string
  execution_operation_action?: ExecutionOperationAction | null
  operating_room_id: string
  task_id: string
  task_room_id: string
  task_title: string
  task_room_name?: string | null
  assignee_display_name?: string | null
  source_message_id?: string | null
  input_revision: number
  source_task_id: string
  source_task_title?: string | null
  source_task_room_id?: string | null
  source_task_room_name?: string | null
  source_result_id: string
  source_result_version: number
  source_result_sha256: string
  artifact_id: string
  artifact_room_id: string
  artifact_url: string
  artifact_accessible?: boolean
  artifact_filename: string
  artifact_sha256: string
  action_kind: string
  target_alias: string
  target_label: string
  target_url: string
  summary: string
  status: ApprovalStatus
  decision?: ApprovalDecision | null
  can_decide?: boolean
  is_current?: boolean
  disposition?: ExecutionDisposition
  revoked?: boolean
  permit_revoked?: boolean
  error?: string | null
  receipt?: { http_status?: number } | null
  created_at: string
  decided_at?: string | null
  executed_at?: string | null
  finished_at?: string | null
}

async function responseError(response: Response): Promise<string> {
  try {
    const body = await response.json() as { detail?: string | { code?: string; detail?: string } }
    const detail = typeof body.detail === 'string' ? body.detail : body.detail?.detail
    if (detail) return `HTTP ${response.status}: ${detail}`
  } catch { /* Keep the HTTP status when the server has no JSON detail. */ }
  return `HTTP ${response.status}`
}

export function useExecutionApprovals(roomId: string | null) {
  const scope = useMemo(() => ({ roomId, active: true, request: 0 }), [roomId])
  const currentScope = useRef(scope)
  currentScope.current = scope
  const isCurrent = useCallback(() => scope.active && currentScope.current === scope, [scope])
  const [snapshot, setSnapshot] = useState<{ scope: typeof scope; approvals: ExecutionApproval[]; error: string | null }>({ scope, approvals: [], error: null })
  const refresh = useCallback(async () => {
    if (!roomId || !isCurrent()) return
    const sequence = ++scope.request
    try {
      const response = await apiFetch(`/api/v1/rooms/${roomId}/execution-approvals`)
      if (!response.ok) throw new Error(await responseError(response))
      const approvals = await response.json() as ExecutionApproval[]
      if (isCurrent() && sequence === scope.request) setSnapshot({ scope, approvals, error: null })
    } catch (error) {
      if (isCurrent() && sequence === scope.request) setSnapshot(previous => ({ scope, approvals: previous.scope === scope ? previous.approvals : [], error: error instanceof Error ? error.message : 'HTTP 500' }))
    }
  }, [roomId, scope, isCurrent])
  useEffect(() => {
    scope.active = true
    void refresh()
    const onFocus = () => { if (document.visibilityState === 'visible') void refresh() }
    const interval = window.setInterval(onFocus, 15000)
    window.addEventListener('focus', onFocus)
    window.addEventListener('online', onFocus)
    return () => {
      scope.active = false
      window.clearInterval(interval)
      window.removeEventListener('focus', onFocus)
      window.removeEventListener('online', onFocus)
    }
  }, [scope, refresh])
  useEffect(() => {
    if (!roomId) return
    const handler = (event: Event) => {
      const detail = (event as CustomEvent).detail as { task?: { room_id?: string; execution_operating_room_id?: string } } | undefined
      if (detail?.task && (detail.task.room_id === roomId || detail.task.execution_operating_room_id === roomId)) void refresh()
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
  const decide = useCallback(async (id: string, decision: ApprovalDecision): Promise<{ approval: ExecutionApproval | null; error: string | null }> => {
    if (!isCurrent()) return { approval: null, error: null }
    try {
      const response = await apiFetch(`/api/v1/execution-approvals/${id}/decision`, { method: 'POST', body: JSON.stringify({ decision }) })
      if (!isCurrent()) return { approval: null, error: null }
      if (!response.ok) throw new Error(await responseError(response))
      const approval = await response.json() as ExecutionApproval
      if (!isCurrent()) return { approval: null, error: null }
      if (approval.operating_room_id !== roomId) return { approval: null, error: null }
      ++scope.request
      setSnapshot(previous => ({ scope, approvals: previous.scope === scope ? previous.approvals.map(record => record.id === approval.id ? approval : record) : [], error: null }))
      await refresh()
      return isCurrent() ? { approval, error: null } : { approval: null, error: null }
    } catch (error) {
      return isCurrent() ? { approval: null, error: error instanceof Error ? error.message : 'HTTP 500' } : { approval: null, error: null }
    }
  }, [roomId, scope, isCurrent, refresh])
  const current = snapshot.scope === scope ? snapshot : { approvals: [], error: null }
  return { approvals: current.approvals, error: current.error, refresh, decide }
}
