import type { ChatMessage } from '@/hooks/useWebSocket'

/**
 * Server-persisted plumbing that is kept for the audit trail but is not
 * chat content: auto-route protocol echoes (#313) and the result message
 * the ask_peer fan-in wakes a caller with (#762) — users read the caller's
 * final answer instead.
 */
const HIDDEN_SYSTEM_ORIGINS = new Set(['auto_route_request', 'auto_route_response', 'peer_ask_results'])

export function isHiddenSystemMessage(msg: ChatMessage): boolean {
  const origin = (msg.metadata as Record<string, unknown> | undefined)?.system_origin
  return typeof origin === 'string' && HIDDEN_SYSTEM_ORIGINS.has(origin)
}
