/**
 * Agent liveness derivation (#71).
 *
 * Pure functions that map an agent's ``actual_state`` (as reported
 * by the agent-daemon via ``/api/v1/agents``) to the two signals
 * the UI actually needs:
 *
 *   - ``deriveAgentOnline`` — boolean for ``<PresenceDot online>``;
 *     drives the dot color (online = sage green, offline = gray).
 *   - ``agentStatusLabel`` — human-readable label for tooltips/text.
 *
 * The only non-obvious input is ``machineOffline``: when the agent's
 * hosting machine has lost its WebSocket the daemon cannot report,
 * so the DB value is stale by definition. We surface the
 * uncertainty as a derived "unreachable" rather than pretend the
 * agent is still running on a box we can no longer reach.
 */

/**
 * States in which the agent is considered alive.
 *
 * ``starting`` is included because, from the user's perspective,
 * the agent has been dispatched and is booting — the inbox is
 * expected to drain soon. Keeping it in the "alive" family avoids
 * a jarring gray→green transition a few seconds after clicking
 * "Start".
 */
export const ALIVE_AGENT_STATES: ReadonlySet<string> = new Set([
  'running',
  'starting',
])

export interface AgentLivenessOptions {
  /** True when the agent's hosting machine has no live WS
   *  session. Forces ``deriveAgentOnline`` to false regardless of
   *  the (now-stale) actual_state in the DB. */
  machineOffline?: boolean
}

export function deriveAgentOnline(
  actualState: string | undefined,
  options?: AgentLivenessOptions,
): boolean {
  if (options?.machineOffline) return false
  if (!actualState) return false
  return ALIVE_AGENT_STATES.has(actualState)
}

export function agentStatusLabel(
  actualState: string | undefined,
  options?: AgentLivenessOptions,
): string {
  if (options?.machineOffline) return 'unreachable'
  return actualState ?? 'unknown'
}
