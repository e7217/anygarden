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

/** The room notice a ``/clear`` leaves (#986): who cleared which agents. */
export interface SessionResetNotice {
  actorName: string
  agentNames: string[]
}

export function parseSessionReset(msg: ChatMessage): SessionResetNotice | null {
  const metadata = msg.metadata as Record<string, unknown> | undefined
  if (metadata?.system_origin !== 'session_reset') return null
  const meta = metadata.session_reset as { actor_name?: unknown; agents?: unknown } | undefined
  const agents = Array.isArray(meta?.agents) ? meta.agents : []
  return {
    actorName: typeof meta?.actor_name === 'string' ? meta.actor_name : '',
    agentNames: agents
      .map(a => (a as { name?: unknown } | null)?.name)
      .filter((name): name is string => typeof name === 'string'),
  }
}
