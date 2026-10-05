import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { apiFetch } from '@/lib/api'
import type { ExecutionDisposition, ExecutionOperationAction } from '@/hooks/useProjectExecutions'

export interface ExecutionRequest {
  id: string
  execution_id: string
  execution_operation_action?: ExecutionOperationAction | null
  operating_room_id: string
  task_id: string
  task_room_id: string
  task_title: string
  assignee_display_name?: string | null
  execution_source_message_id?: string | null
  question: string
  status: string
  can_answer?: boolean
  is_current?: boolean
  disposition?: ExecutionDisposition
  input_revision: number
  answer?: string | null
  question_message_id?: string | null
  answer_message_id?: string | null
  created_at: string
  answered_at?: string | null
}

export function useExecutionRequests(roomId: string | null) {
  const scope = useMemo(() => ({ roomId, active: true, request: 0 }), [roomId])
  const currentScope = useRef(scope)
  currentScope.current = scope
  const isCurrent = useCallback(() => scope.active && currentScope.current === scope, [scope])
  const [snapshot, setSnapshot] = useState<{ scope: typeof scope; requests: ExecutionRequest[]; error: string | null }>({ scope, requests: [], error: null })
  const refresh = useCallback(async () => {
    if (!roomId || !isCurrent()) return
    const sequence = ++scope.request
    try {
      const response = await apiFetch(`/api/v1/rooms/${roomId}/execution-requests`)
      if (!response.ok) throw new Error(`HTTP ${response.status}`)
      const requests = await response.json() as ExecutionRequest[]
      if (isCurrent() && sequence === scope.request) setSnapshot({ scope, requests, error: null })
    } catch (error) {
      if (isCurrent() && sequence === scope.request) setSnapshot(previous => ({ scope, requests: previous.scope === scope ? previous.requests : [], error: error instanceof Error ? error.message : 'HTTP 500' }))
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
  const answer = useCallback(async (id: string, value: string): Promise<ExecutionRequest | null> => {
    if (!isCurrent()) return null
    try {
      const response = await apiFetch(`/api/v1/execution-requests/${id}/answer`, { method: 'POST', body: JSON.stringify({ answer: value }) })
      if (!isCurrent()) return null
      if (!response.ok) throw new Error(`HTTP ${response.status}`)
      const answered = await response.json() as ExecutionRequest
      if (!isCurrent()) return null
      await refresh()
      return isCurrent() ? answered : null
    } catch (error) {
      if (isCurrent()) {
        ++scope.request
        setSnapshot(previous => ({ scope, requests: previous.scope === scope ? previous.requests : [], error: error instanceof Error ? error.message : 'HTTP 500' }))
      }
      return null
    }
  }, [scope, isCurrent, refresh])
  const current = snapshot.scope === scope ? snapshot : { requests: [], error: null }
  return { requests: current.requests, error: current.error, refresh, answer }
}
