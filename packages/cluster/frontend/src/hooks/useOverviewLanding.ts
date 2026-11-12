import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { fetchAgentLinkGraph } from '@/lib/agentLinks'

export const OVERVIEW_PATH = '/agents/links'
/** With fewer agents the map has nothing to show; the welcome screen helps more. */
export const OVERVIEW_MIN_AGENTS = 2

/**
 * ROOM-16 — ``/`` with no room open sends a person with a team of agents
 * to the overview. ``checking`` holds the welcome screen back meanwhile so
 * it does not flash before the redirect; a failed check falls back to it.
 */
export function useOverviewLanding(active: boolean): 'checking' | 'welcome' {
  const navigate = useNavigate()
  const [state, setState] = useState<'checking' | 'welcome'>('checking')

  useEffect(() => {
    if (!active) return
    const controller = new AbortController()
    setState('checking')
    fetchAgentLinkGraph(controller.signal)
      .then(graph => {
        if (graph.agents.length >= OVERVIEW_MIN_AGENTS) navigate(OVERVIEW_PATH, { replace: true })
        else setState('welcome')
      })
      .catch(() => { if (!controller.signal.aborted) setState('welcome') })
    return () => controller.abort()
  }, [active, navigate])

  return active ? state : 'welcome'
}
