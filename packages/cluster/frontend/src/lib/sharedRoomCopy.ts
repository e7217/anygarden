import type { MessageKey } from '@/i18n/messages'

const states: Record<string, MessageKey> = {
  requested: 'federation.room.requested', accepted: 'federation.room.accepted',
  running: 'federation.room.running', completed: 'federation.room.completed',
  failed: 'federation.room.failed', rejected: 'federation.room.rejected',
  cancel_requested: 'federation.room.cancelRequested', cancelled: 'federation.room.cancelled',
  unknown: 'federation.room.unknown',
}

export function delegationStateCopy(state: string): MessageKey {
  return states[state] ?? 'federation.room.unknown'
}

export function sharedErrorCopy(code: string, loading = false): MessageKey {
  if (/VERSION_UNSUPPORTED|SHARING_DISABLED/.test(code)) return 'federation.room.unavailable'
  if (/REVISION_CONFLICT|STATE_CONFLICT|CLAIM_CONFLICT|ID_CONFLICT/.test(code)) return 'federation.room.stateChanged'
  if (/DENIED|REVOKED|FORBIDDEN|NOT_FOUND|HTTP_40[134]/.test(code)) {
    return loading ? 'federation.room.accessDenied' : 'federation.room.permissionChanged'
  }
  return loading ? 'federation.room.loadFailed' : 'federation.room.requestFailed'
}

export function targetUnavailableCopy(code: string | null): MessageKey {
  const reason = (code ?? '').toUpperCase()
  if (/OFFLINE|MACHINE_UNAVAILABLE/.test(reason)) return 'federation.room.targetOffline'
  if (reason === 'EXECUTION_UNAVAILABLE') return 'federation.room.targetConnectionPending'
  if (/NOT_RUNNING|AGENT_UNAVAILABLE|AGENT_MISSING/.test(reason)) return 'federation.room.targetNotRunning'
  if (/QUOTA|BUDGET/.test(reason)) return 'federation.room.targetQuota'
  if (/METADATA_UNAVAILABLE/.test(reason)) return 'federation.room.targetNamePending'
  if (/UNSUPPORTED_RUNTIME|ENGINE_UNSUPPORTED|UNSUPPORTED_ENGINE/.test(reason)) return 'federation.room.targetEngineUnsupported'
  if (/VERSION|UNSUPPORTED/.test(reason)) return 'federation.room.targetUnsupported'
  if (/DENIED|GRANT|PERMISSION/.test(reason)) return 'federation.room.targetPermission'
  return 'federation.room.targetUnavailable'
}

export function executionFailureCopy(code: string | null): MessageKey {
  if (/AUTH/.test(code ?? '')) return 'federation.room.failureAuth'
  if (code === 'TIMEOUT_STOPPED') return 'federation.room.failureTimeout'
  if (code === 'POLICY_DENIED') return 'federation.room.failurePolicy'
  if (/PROVIDER/.test(code ?? '')) return 'federation.room.failureProvider'
  if (code === 'UNSUPPORTED_RUNTIME') return 'federation.room.failureUnsupported'
  return 'federation.room.executionFailed'
}
