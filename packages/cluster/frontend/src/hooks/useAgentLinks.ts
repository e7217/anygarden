import { useCallback, useEffect, useRef, useState } from 'react'
import {
  createAgentLink,
  deleteAgentLink,
  fetchAgentLinkGraph,
  type AgentLink,
  type AgentLinkGraph,
} from '@/lib/agentLinks'

interface State {
  graph: AgentLinkGraph | null
  loading: boolean
  /** HTTP status of the last failed load (0 for a network error). */
  errorStatus: number | null
}

/**
 * The agent-link graph plus its mutations (#1006). Writes update the
 * local copy from the server's response, so the map and the settings
 * list stay in step without a refetch; a failed write throws an
 * ``AgentLinkError`` for the caller to show next to the form.
 */
export function useAgentLinks() {
  const [state, setState] = useState<State>({ graph: null, loading: true, errorStatus: null })
  const controller = useRef<AbortController | null>(null)

  const refresh = useCallback(async () => {
    controller.current?.abort()
    const request = new AbortController()
    controller.current = request
    setState(previous => ({ ...previous, loading: true, errorStatus: null }))
    try {
      const graph = await fetchAgentLinkGraph(request.signal)
      if (!request.signal.aborted) setState({ graph, loading: false, errorStatus: null })
    } catch (error) {
      if (request.signal.aborted) return
      const status = typeof (error as { status?: unknown }).status === 'number' ? (error as { status: number }).status : 0
      setState(previous => ({ ...previous, loading: false, errorStatus: status }))
    }
  }, [])

  useEffect(() => {
    void refresh()
    return () => controller.current?.abort()
  }, [refresh])

  const replaceLinks = useCallback((update: (links: AgentLink[]) => AgentLink[]) => {
    setState(previous => previous.graph
      ? { ...previous, graph: { ...previous.graph, links: update(previous.graph.links) } }
      : previous)
  }, [])

  const pending = useRef(new Map<string, Promise<AgentLink>>())
  const create = useCallback((input: { from_agent_id: string; to_agent_id: string }) => {
    const key = `${input.from_agent_id}>${input.to_agent_id}`
    const running = pending.current.get(key)
    if (running) return running
    const request = createAgentLink(input).then(link => {
      replaceLinks(links => [...links.filter(item => item.id !== link.id), link])
      return link
    }).catch(async error => {
      if (error.status === 409 || error.status === 404) await refresh()
      throw error
    }).finally(() => { pending.current.delete(key) })
    pending.current.set(key, request)
    return request
  }, [replaceLinks, refresh])

  const remove = useCallback(async (id: string) => {
    await deleteAgentLink(id)
    replaceLinks(links => links.filter(item => item.id !== id))
  }, [replaceLinks])

  return { ...state, refresh, create, remove }
}
