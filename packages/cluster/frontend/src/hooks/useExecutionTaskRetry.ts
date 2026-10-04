import { useEffect, useMemo, useRef, useState } from 'react'
import { apiFetch } from '@/lib/api'
import { getAuthToken } from '@/lib/authStorage'
import { useLocale } from '@/i18n/LocaleProvider'
import type { Task, TaskRecovery } from '@/hooks/useRoomTasks'

export function useExecutionTaskRetry(taskId: string, inputRevision: number | null | undefined, recovery?: TaskRecovery | null) {
  const { t } = useLocale()
  const authToken = getAuthToken()
  const requestId = recovery?.request_id
  const attempt = recovery?.active_attempt
  const scope = useMemo(() => ({ active: true, taskId, authToken, inputRevision, requestId, attempt }), [taskId, authToken, inputRevision, requestId, attempt])
  const currentScope = useRef(scope)
  currentScope.current = scope
  const operation = useRef<{ scope: typeof scope; id: string } | null>(null)
  const busy = useRef(false)
  const [snapshot, setSnapshot] = useState<{ scope: typeof scope; sending: boolean; accepted: boolean; permissionDenied: boolean; error: string | null }>({ scope, sending: false, accepted: false, permissionDenied: false, error: null })
  const current = snapshot.scope === scope ? snapshot : { sending: false, accepted: false, permissionDenied: false, error: null }
  const canRetry = !current.permissionDenied && recovery?.can_retry === true && ['failed', 'action_required'].includes(recovery.state) && !!authToken && !!requestId && inputRevision != null && !!attempt
  useEffect(() => { scope.active = true; return () => { scope.active = false } }, [scope])

  async function submit() {
    if (!canRetry || busy.current || current.accepted || !scope.active || currentScope.current !== scope || getAuthToken() !== scope.authToken) return
    busy.current = true
    if (operation.current?.scope !== scope) operation.current = { scope, id: crypto.randomUUID() }
    setSnapshot({ scope, sending: true, accepted: false, permissionDenied: false, error: null })
    const isCurrent = () => scope.active && currentScope.current === scope && getAuthToken() === scope.authToken
    let permissionDenied = false
    try {
      const response = await apiFetch(`/api/v1/execution-tasks/${taskId}/retry`, { method: 'POST', body: JSON.stringify({ operation_id: operation.current.id, expected_input_revision: inputRevision, expected_request_id: requestId, expected_attempt: attempt }) })
      if (!isCurrent()) return
      if (!response.ok) {
        permissionDenied = response.status === 401 || response.status === 403
        let detail: string | undefined
        try {
          const body = await response.json() as { detail?: string | { detail?: string; message?: string }; message?: string }
          detail = typeof body.detail === 'string' ? body.detail : body.detail?.detail ?? body.detail?.message ?? body.message
        } catch { /* Preserve the HTTP status if there is no public reason. */ }
        throw new Error(`HTTP ${response.status}${detail ? `: ${detail}` : ''}`)
      }
      const task = await response.json() as Task
      if (!isCurrent()) return
      if (task.id !== taskId) throw new Error(t('taskRecovery.retryFailed'))
      setSnapshot({ scope, sending: false, accepted: true, permissionDenied: false, error: null })
      window.dispatchEvent(new CustomEvent('anygarden:task:updated', { detail: { task } }))
    } catch (error) {
      if (isCurrent()) setSnapshot({ scope, sending: false, accepted: false, permissionDenied, error: error instanceof Error ? error.message : t('taskRecovery.retryFailed') })
    } finally {
      busy.current = false
    }
  }
  return { canRetry, sending: current.sending, accepted: current.accepted, error: current.error, submit }
}
