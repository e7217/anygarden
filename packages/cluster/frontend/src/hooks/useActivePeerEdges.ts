import { useEffect, useState } from 'react'
import { ACTIVE_TURNS_POLL_MS } from '@/hooks/useActiveTurns'
import { fetchActivePeerEdges, type ActivePeerEdge } from '@/lib/agentLinks'

/**
 * COL-19 — agent-to-agent questions running now, polled like the
 * sidebar's answering marks (MSG-09) and only while the tab is visible.
 */
export function useActivePeerEdges(enabled: boolean): ActivePeerEdge[] {
  const [edges, setEdges] = useState<ActivePeerEdge[]>([])

  useEffect(() => {
    if (!enabled) {
      setEdges([])
      return
    }
    let active = true
    let controller: AbortController | null = null

    const refresh = async () => {
      controller?.abort()
      controller = new AbortController()
      try {
        const next = await fetchActivePeerEdges(controller.signal)
        if (active) setEdges(next)
      } catch {
        // Keep the last answer; the next poll retries.
      }
    }
    const refreshIfVisible = () => {
      if (document.visibilityState === 'visible') void refresh()
    }

    void refresh()
    const interval = window.setInterval(refreshIfVisible, ACTIVE_TURNS_POLL_MS)
    document.addEventListener('visibilitychange', refreshIfVisible)
    return () => {
      active = false
      controller?.abort()
      window.clearInterval(interval)
      document.removeEventListener('visibilitychange', refreshIfVisible)
    }
  }, [enabled])

  return edges
}
