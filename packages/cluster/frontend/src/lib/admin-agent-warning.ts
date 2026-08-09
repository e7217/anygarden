import type { Agent } from '@/hooks/useAgents'

type AgentWarningState = Pick<
  Agent,
  'actual_state' | 'last_crash_reason' | 'unavailable_reason'
>

/**
 * Whether the legacy crash string should be shown as a warning.
 *
 * Structured unavailability is authoritative for pending placement/spawn
 * failures. The raw crash string can survive a later start/stop transition,
 * so showing it outside the crashed state raises stale admin alarms.
 */
export function shouldShowFallbackCrashWarning(
  agent: AgentWarningState,
): boolean {
  return (
    agent.actual_state === 'crashed' &&
    Boolean(agent.last_crash_reason) &&
    !agent.unavailable_reason
  )
}
