import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { apiFetch } from '@/lib/api'
import type { ExecutionRequest } from '@/hooks/useExecutionRequests'
import type { ApprovalDecision, ExecutionApproval } from '@/hooks/useExecutionApprovals'
import type { ExecutionDisposition, ExecutionOperationAction } from '@/hooks/useProjectExecutions'
import type { TaskRecovery } from '@/hooks/useRoomTasks'

export interface InboxItem {
  id: string
  type: 'task' | 'question' | 'approval'
  project_id: string
  project_name: string
  execution_id: string
  execution_operation_action?: ExecutionOperationAction | null
  execution_error?: string | null
  operating_room_id: string
  operating_room_name: string
  task_id: string
  task_title: string
  task_room_id: string
  task_room_name: string
  source_message_id: string | null
  task_href: string
  source_href: string | null
  status: string
  needs_action: boolean
  current_action: 'none' | 'read' | 'answer' | 'decide' | 'retry'
  created_at: string
  updated_at: string
  latest_event_id?: string | null
  latest_event_type?: string | null
  input_revision?: number
  is_current?: boolean
  disposition?: ExecutionDisposition
  question?: ExecutionRequest
  approval?: ExecutionApproval
  task?: {
    id: string
    title: string
    status: string
    error: string | null
    recovery?: TaskRecovery | null
    result_version: number
    result_markdown: string | null
    result_sha256: string | null
    assignee_display_name: string | null
    artifacts: { id: string; filename: string; sha256: string; url: string }[]
    input_revision?: number
    is_current?: boolean
    disposition?: ExecutionDisposition
  }
}
interface InboxSnapshot { items: InboxItem[]; projects: { id: string; name: string }[] }

async function responseError(response: Response): Promise<string> {
  try {
    const body = await response.json() as { detail?: string | { detail?: string } }
    const detail = typeof body.detail === 'string' ? body.detail : body.detail?.detail
    if (detail) return `HTTP ${response.status}: ${detail}`
  } catch { /* Fall back to the status when a response has no public detail. */ }
  return `HTTP ${response.status}`
}

export function useProjectInbox(userId: string | null) {
  const scope = useMemo(() => ({ active: true, sequence: 0, controller: null as AbortController | null }), [userId])
  const currentScope = useRef(scope)
  currentScope.current = scope
  const isCurrent = useCallback(() => scope.active && currentScope.current === scope, [scope])
  const [snapshot, setSnapshot] = useState<{ scope: typeof scope; data: InboxSnapshot; loading: boolean; error: string | null }>({ scope, data: { items: [], projects: [] }, loading: true, error: null })
  const refresh = useCallback(async () => {
    if (!userId || !isCurrent()) return
    const sequence = ++scope.sequence
    scope.controller?.abort()
    const controller = new AbortController()
    scope.controller = controller
    try {
      const response = await apiFetch('/api/v1/inbox', { signal: controller.signal })
      if ((response.status === 401 || response.status === 403) && isCurrent() && sequence === scope.sequence) {
        setSnapshot({ scope, data: { items: [], projects: [] }, loading: false, error: await responseError(response) })
        return
      }
      if (!response.ok) throw new Error(await responseError(response))
      const data = await response.json() as InboxSnapshot
      if (!isCurrent() || sequence !== scope.sequence) return
      // Repeated transport deliveries replace the same source identity.
      data.items = [...new Map(data.items.map(item => [item.id, item])).values()]
      setSnapshot({ scope, data, loading: false, error: null })
    } catch (error) {
      if (!isCurrent() || sequence !== scope.sequence || controller.signal.aborted) return
      setSnapshot(previous => ({ scope, data: previous.scope === scope ? previous.data : { items: [], projects: [] }, loading: false, error: error instanceof Error ? error.message : 'HTTP 500' }))
    }
  }, [userId, scope, isCurrent])
  useEffect(() => {
    scope.active = true
    void refresh()
    const onFocus = () => { if (document.visibilityState === 'visible') void refresh() }
    const interval = window.setInterval(onFocus, 15000)
    window.addEventListener('focus', onFocus)
    window.addEventListener('online', onFocus)
    window.addEventListener('anygarden:task:updated', onFocus)
    window.addEventListener('anygarden:execution:updated', onFocus)
    document.addEventListener('visibilitychange', onFocus)
    return () => {
      scope.active = false
      scope.controller?.abort()
      window.clearInterval(interval)
      window.removeEventListener('focus', onFocus)
      window.removeEventListener('online', onFocus)
      window.removeEventListener('anygarden:task:updated', onFocus)
      window.removeEventListener('anygarden:execution:updated', onFocus)
      document.removeEventListener('visibilitychange', onFocus)
    }
  }, [scope, refresh])
  const mutate = useCallback(async <T extends ExecutionRequest | ExecutionApproval>(kind: 'question' | 'approval', id: string, url: string, body: object): Promise<{ record: T | null; error: string | null }> => {
    if (!isCurrent()) return { record: null, error: null }
    try {
      const response = await apiFetch(url, { method: 'POST', body: JSON.stringify(body) })
      if (!isCurrent()) return { record: null, error: null }
      if (!response.ok) throw new Error(await responseError(response))
      const record = await response.json() as T
      if (!isCurrent() || record.id !== id) return { record: null, error: null }
      ++scope.sequence
      scope.controller?.abort()
      setSnapshot(previous => ({ scope, loading: false, error: null, data: previous.scope === scope ? {
        ...previous.data, items: previous.data.items.map(item => item.id === `${kind}:${id}` ? {
          ...item, [kind]: record, status: record.status, needs_action: false, current_action: 'read',
        } : item),
      } : { items: [], projects: [] } }))
      await refresh()
      return isCurrent() ? { record, error: null } : { record: null, error: null }
    } catch (error) {
      const message = error instanceof Error ? error.message : 'HTTP 500'
      if (isCurrent()) setSnapshot(previous => ({ ...previous, error: message }))
      return isCurrent() ? { record: null, error: message } : { record: null, error: null }
    }
  }, [scope, isCurrent, refresh])
  const answer = useCallback(async (id: string, answer: string) => {
    const result = await mutate<ExecutionRequest>('question', id, `/api/v1/execution-requests/${id}/answer`, { answer })
    return result.record
  }, [mutate])
  const decide = useCallback(async (id: string, decision: ApprovalDecision) => {
    const result = await mutate<ExecutionApproval>('approval', id, `/api/v1/execution-approvals/${id}/decision`, { decision })
    return { approval: result.record, error: result.error }
  }, [mutate])
  const current = snapshot.scope === scope ? snapshot : { data: { items: [], projects: [] }, loading: true, error: null }
  return { ...current.data, loading: current.loading, error: current.error, refresh, answer, decide }
}
