import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  cancelSharedDelegation, delegateSharedMessage, FederationApiError,
  getSharedRoomSnapshot, retrySubmission, sendSharedMessage,
  type SharedChannelRef, type SharedCommandResult, type SharedRoomSnapshot,
} from '@/lib/federationApi'

type LoadMode = 'refresh' | 'poll' | 'older'

/** Shared rooms use the authenticated channel API only, never local room WS. */
export function useSharedRoom(ref: SharedChannelRef) {
  const { authority_node_id, channel_id } = ref
  const scope = useMemo(() => ({
    ref: { authority_node_id, channel_id }, active: true, serial: 0,
    data: null as SharedRoomSnapshot | null,
    controller: null as AbortController | null,
    mutating: false,
  }), [authority_node_id, channel_id])
  const current = useRef(scope)
  current.current = scope
  const [view, setView] = useState({ scope, data: scope.data, loading: true, error: null as string | null })
  const [action, setAction] = useState({ scope, busy: false, error: null as string | null })
  const valid = useCallback(() => scope.active && current.current === scope, [scope])

  const load = useCallback(async (mode: LoadMode = 'refresh') => {
    if (!valid()) return
    // A timer must not cancel a user's older-page request or manual refresh.
    if (mode === 'poll' && scope.controller) return
    scope.controller?.abort()
    const controller = new AbortController()
    scope.controller = controller
    const serial = ++scope.serial
    const previous = scope.data
    const accepts = () => valid() && scope.serial === serial && !controller.signal.aborted
    setView({ scope, data: previous, loading: true, error: null })
    try {
      const incoming = await getSharedRoomSnapshot(scope.ref, {
        signal: controller.signal,
        ...(mode !== 'older' && previous?.cursor.newest_seq != null
          ? { after_seq: previous.cursor.newest_seq } : {}),
        ...(mode === 'older' && previous?.cursor.oldest_seq != null
          ? { before_seq: previous.cursor.oldest_seq } : {}),
      })
      if (!accepts()) return
      // Older servers can expose the old diagnostic snapshot. Fail closed
      // rather than enabling commands without the product permission view.
      if (!incoming.permissions || !incoming.cursor || !Array.isArray(incoming.targets)
        || !Array.isArray(incoming.delegations) || !Array.isArray(incoming.submissions)) {
        throw new FederationApiError('VERSION_UNSUPPORTED', 426)
      }
      const messages = new Map(previous?.messages.map(message => [message.message_id, message]))
      incoming.messages.forEach(message => messages.set(message.message_id, message))
      const sorted = [...messages.values()].sort((a, b) => a.seq - b.seq)
      const cursor = {
        oldest_seq: sorted[0]?.seq ?? incoming.cursor.oldest_seq,
        newest_seq: sorted.at(-1)?.seq ?? incoming.cursor.newest_seq,
        has_more_before: mode === 'older' || !previous
          ? incoming.cursor.has_more_before : previous.cursor.has_more_before,
      }
      scope.data = { ...incoming, messages: sorted, cursor }
      setView({ scope, data: scope.data, loading: false, error: null })
    } catch (error) {
      if (!accepts()) return
      const code = error instanceof FederationApiError ? error.code : 'CONNECTION_FAILED'
      // Revocation must remove previously visible shared content immediately.
      if (error instanceof FederationApiError && [401, 403, 404].includes(error.status)) scope.data = null
      setView({ scope, data: scope.data, loading: false, error: code })
    } finally {
      if (scope.serial === serial) scope.controller = null
    }
  }, [scope, valid])

  useEffect(() => {
    scope.active = true
    void load()
    const tick = () => { if (document.visibilityState === 'visible') void load('poll') }
    const timer = window.setInterval(tick, 5000)
    document.addEventListener('visibilitychange', tick)
    return () => {
      scope.active = false
      scope.controller?.abort()
      window.clearInterval(timer)
      document.removeEventListener('visibilitychange', tick)
    }
  }, [scope, load])

  const execute = useCallback(async (operation: () => Promise<SharedCommandResult>): Promise<boolean> => {
    if (!valid() || scope.mutating) return false
    scope.mutating = true
    setAction({ scope, busy: true, error: null })
    try {
      const result = await operation()
      if (!valid()) return false
      if (result.state === 'failed') throw new FederationApiError(result.error_code ?? 'REQUEST_FAILED', 409)
      await load()
      return valid()
    } catch (error) {
      if (!valid()) return false
      setAction({ scope, busy: false, error: error instanceof FederationApiError ? error.code : 'CONNECTION_FAILED' })
      // Refresh permission/revision changes, without replaying the command.
      if (error instanceof FederationApiError && [403, 409].includes(error.status)) await load()
      return false
    } finally {
      scope.mutating = false
      if (valid()) setAction(previous => ({ ...previous, scope, busy: false }))
    }
  }, [scope, valid, load])

  return {
    data: view.scope === scope ? view.data : null,
    loading: view.scope !== scope || view.loading,
    error: view.scope === scope ? view.error : null,
    busy: action.scope === scope && action.busy,
    actionError: action.scope === scope ? action.error : null,
    clearActionError: () => setAction({ scope, busy: scope.mutating, error: null }),
    refresh: () => load(),
    loadOlder: () => load('older'),
    sendMessage: (input: Parameters<typeof sendSharedMessage>[1]) => execute(() => sendSharedMessage(scope.ref, input)),
    delegate: (input: Parameters<typeof delegateSharedMessage>[1]) => execute(() => delegateSharedMessage(scope.ref, input)),
    cancel: (id: string, input: Parameters<typeof cancelSharedDelegation>[2]) => execute(() => cancelSharedDelegation(scope.ref, id, input)),
    retry: (requestId: string) => execute(() => retrySubmission(authority_node_id, channel_id, requestId)),
  }
}

export type SharedRoomState = ReturnType<typeof useSharedRoom>
