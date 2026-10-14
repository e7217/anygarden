import { createContext, useContext } from 'react'

/**
 * Agent actions reachable from places other than the sidebar (e.g. a
 * chat message's sender avatar). The sidebar owns the DM list and the
 * agent settings dialog, so callers request an action over a window
 * event and the sidebar performs it — the same pattern the WS layer
 * uses for ``anygarden:rooms:*`` events.
 */
export type AgentAction = 'direct-message' | 'settings'

export const AGENT_ACTION_EVENT = 'anygarden:agent:action'

export interface AgentActionDetail {
  agentId: string
  action: AgentAction
}

export function requestAgentAction(agentId: string, action: AgentAction) {
  window.dispatchEvent(
    new CustomEvent<AgentActionDetail>(AGENT_ACTION_EVENT, { detail: { agentId, action } }),
  )
}

/** True where the viewer may use agent actions (admins on the chat
 *  page). Defaults to false so shared/guest views render plain senders. */
export const AgentActionsContext = createContext(false)

export function useAgentActionsEnabled() {
  return useContext(AgentActionsContext)
}
