import { useEffect, useState } from 'react'
import { fetchAgentAlerts, type AgentAlerts } from '@/lib/agentLinks'

/** Slower than the answering marks: the alerts take a few more queries. */
export const AGENT_ALERTS_POLL_MS = 15_000

const EMPTY: ReadonlyMap<string, AgentAlerts> = new Map()

/**
 * COL-20 (#1032) — what needs the person, per agent id, polled only while
 * the tab is visible. Agents without anything are missing from the map.
 */
export function useAgentAlerts(enabled: boolean): ReadonlyMap<string, AgentAlerts> {
  const [alerts, setAlerts] = useState<ReadonlyMap<string, AgentAlerts>>(EMPTY)

  useEffect(() => {
    if (!enabled) {
      setAlerts(EMPTY)
      return
    }
    let active = true
    let controller: AbortController | null = null

    const refresh = async () => {
      controller?.abort()
      controller = new AbortController()
      try {
        const rows = await fetchAgentAlerts(controller.signal)
        if (active) setAlerts(new Map(rows.map(row => [row.agent_id, row])))
      } catch {
        // Keep the last answer; the next poll retries.
      }
    }
    const refreshIfVisible = () => {
      if (document.visibilityState === 'visible') void refresh()
    }

    void refresh()
    const interval = window.setInterval(refreshIfVisible, AGENT_ALERTS_POLL_MS)
    document.addEventListener('visibilitychange', refreshIfVisible)
    return () => {
      active = false
      controller?.abort()
      window.clearInterval(interval)
      document.removeEventListener('visibilitychange', refreshIfVisible)
    }
  }, [enabled])

  return alerts
}
