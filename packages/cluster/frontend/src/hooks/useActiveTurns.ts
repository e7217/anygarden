import { createContext, useContext, useEffect, useState } from 'react'
import { apiFetch } from '@/lib/api'

/** Poll interval while the tab is visible (#912). Other rooms may lag by
 *  up to this much; the open room refreshes at once via the event below. */
export const ACTIVE_TURNS_POLL_MS = 5_000

/** Dispatched when the open room's typing set changes, so the sidebar
 *  follows the room the user is looking at without waiting for a poll. */
export const ACTIVE_TURNS_INVALIDATE_EVENT = 'anygarden:turns:invalidate'

export interface ActiveTurns {
  roomIds: ReadonlySet<string>
  agentIds: ReadonlySet<string>
}

const EMPTY: ActiveTurns = { roomIds: new Set(), agentIds: new Set() }

/** Sidebar-wide snapshot, provided once so nested rows don't each poll. */
export const ActiveTurnsContext = createContext<ActiveTurns>(EMPTY)

export function useActiveTurnsContext(): ActiveTurns {
  return useContext(ActiveTurnsContext)
}

/**
 * MSG-09 — rooms (and their agents) where an agent is answering right now,
 * across every room the caller can see. ``enabled`` false (guests) skips
 * the endpoint entirely.
 */
export function useActiveTurns(enabled: boolean): ActiveTurns {
  const [state, setState] = useState<ActiveTurns>(EMPTY)

  useEffect(() => {
    if (!enabled) {
      setState(EMPTY)
      return
    }
    let active = true
    let controller: AbortController | null = null

    const refresh = async () => {
      controller?.abort()
      controller = new AbortController()
      try {
        const resp = await apiFetch('/api/v1/turns/active', { signal: controller.signal })
        if (!resp.ok) return
        const rows = await resp.json() as { room_id: string; agent_ids: string[] }[]
        if (!active || !Array.isArray(rows)) return
        setState({
          roomIds: new Set(rows.map(row => row.room_id)),
          agentIds: new Set(rows.flatMap(row => row.agent_ids)),
        })
      } catch {
        // Keep the last known state; the next poll corrects it.
      }
    }
    const refreshIfVisible = () => {
      if (document.visibilityState === 'visible') void refresh()
    }

    void refresh()
    const interval = window.setInterval(refreshIfVisible, ACTIVE_TURNS_POLL_MS)
    window.addEventListener(ACTIVE_TURNS_INVALIDATE_EVENT, refreshIfVisible)
    document.addEventListener('visibilitychange', refreshIfVisible)
    return () => {
      active = false
      controller?.abort()
      window.clearInterval(interval)
      window.removeEventListener(ACTIVE_TURNS_INVALIDATE_EVENT, refreshIfVisible)
      document.removeEventListener('visibilitychange', refreshIfVisible)
    }
  }, [enabled])

  return state
}
