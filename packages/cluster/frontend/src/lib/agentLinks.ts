/**
 * Agent links (#1006) — global, one-directional "A may ask B" edges.
 *
 * Agents use their peers' public descriptions to decide when to ask.
 * The REST surface is
 * ``/api/v1/agent-links``; any signed-in user may edit it for now.
 *
 * The graph helpers below are pure so the relationship map's
 * reachability highlight and cycle warning can be unit-tested without
 * a canvas.
 */
import { apiFetch } from '@/lib/api'
import { deriveAgentOnline } from '@/lib/agent-liveness'

export interface AgentLink {
  id: string
  from_agent_id: string
  to_agent_id: string
  created_at: string
  updated_at: string
}

export interface LinkAgent {
  id: string
  name: string
  description: string | null
  actual_state: string
  /** #1023 — why it cannot answer, worded by the UI; null when fine. */
  unavailable_code?: string | null
  /** #1031 — drawn as the engine's logo. */
  engine?: string | null
}

export interface AgentLinkGraph {
  agents: LinkAgent[]
  links: AgentLink[]
}

/** A failed request with the HTTP status and the server's ``detail``. */
export class AgentLinkError extends Error {
  status: number
  detail: string | null
  constructor(status: number, detail: string | null) {
    super(detail ?? String(status))
    this.status = status
    this.detail = detail
  }
}

async function failure(response: Response): Promise<AgentLinkError> {
  const body = await response.json().catch(() => null) as { detail?: unknown } | null
  // FastAPI 422 bodies carry a list of validation errors, not a string.
  const detail = typeof body?.detail === 'string' ? body.detail : null
  return new AgentLinkError(response.status, detail)
}

export async function fetchAgentLinkGraph(signal?: AbortSignal): Promise<AgentLinkGraph> {
  const response = await apiFetch('/api/v1/agent-links', { signal })
  if (!response.ok) throw await failure(response)
  const body = await response.json() as Partial<AgentLinkGraph> | null
  return {
    agents: Array.isArray(body?.agents) ? body.agents : [],
    links: Array.isArray(body?.links) ? body.links : [],
  }
}

export async function createAgentLink(input: {
  from_agent_id: string
  to_agent_id: string
}): Promise<AgentLink> {
  const response = await apiFetch('/api/v1/agent-links', {
    method: 'POST',
    body: JSON.stringify(input),
  })
  if (!response.ok) throw await failure(response)
  return response.json()
}

export async function deleteAgentLink(id: string): Promise<void> {
  const response = await apiFetch(`/api/v1/agent-links/${id}`, { method: 'DELETE' })
  // Already gone counts as deleted.
  if (!response.ok && response.status !== 404) throw await failure(response)
}

/**
 * Agents (and the links between them) that could become involved when
 * someone talks to ``startId``: everything reachable by following links
 * forward. The start agent itself is included.
 */
export function reachableFrom(
  startId: string,
  links: readonly AgentLink[],
): { agents: Set<string>; links: Set<string> } {
  const outgoing = new Map<string, AgentLink[]>()
  for (const link of links) {
    const list = outgoing.get(link.from_agent_id)
    if (list) list.push(link)
    else outgoing.set(link.from_agent_id, [link])
  }
  const agents = new Set<string>([startId])
  const used = new Set<string>()
  const queue = [startId]
  while (queue.length > 0) {
    const current = queue.shift()!
    for (const link of outgoing.get(current) ?? []) {
      used.add(link.id)
      if (!agents.has(link.to_agent_id)) {
        agents.add(link.to_agent_id)
        queue.push(link.to_agent_id)
      }
    }
  }
  return { agents, links: used }
}

/**
 * Directed cycles in the link graph, one entry per strongly connected
 * component with more than one agent (self-links are refused by the
 * server). Each entry is an agent-id path that starts and returns to
 * its first agent, e.g. ``[A, B, C]`` for A→B→C→A. A component with
 * several interleaved loops is reported once, through one of them, so
 * the warning stays short.
 *
 * Tarjan's algorithm keeps this linear in agents + links.
 */
export function findCycles(links: readonly AgentLink[]): string[][] {
  const outgoing = new Map<string, string[]>()
  const nodes = new Set<string>()
  for (const link of links) {
    nodes.add(link.from_agent_id)
    nodes.add(link.to_agent_id)
    const list = outgoing.get(link.from_agent_id)
    if (list) list.push(link.to_agent_id)
    else outgoing.set(link.from_agent_id, [link.to_agent_id])
  }

  let counter = 0
  const index = new Map<string, number>()
  const low = new Map<string, number>()
  const onStack = new Set<string>()
  const stack: string[] = []
  const components: string[][] = []

  // Iterative DFS so a long chain cannot overflow the call stack.
  for (const root of [...nodes].sort()) {
    if (index.has(root)) continue
    const work: { node: string; next: number }[] = [{ node: root, next: 0 }]
    index.set(root, counter); low.set(root, counter); counter += 1
    stack.push(root); onStack.add(root)
    while (work.length > 0) {
      const frame = work[work.length - 1]
      const targets = outgoing.get(frame.node) ?? []
      if (frame.next < targets.length) {
        const target = targets[frame.next++]
        if (!index.has(target)) {
          index.set(target, counter); low.set(target, counter); counter += 1
          stack.push(target); onStack.add(target)
          work.push({ node: target, next: 0 })
        } else if (onStack.has(target)) {
          low.set(frame.node, Math.min(low.get(frame.node)!, index.get(target)!))
        }
        continue
      }
      work.pop()
      if (work.length > 0) {
        const parent = work[work.length - 1].node
        low.set(parent, Math.min(low.get(parent)!, low.get(frame.node)!))
      }
      if (low.get(frame.node) === index.get(frame.node)) {
        const component: string[] = []
        let member: string
        do {
          member = stack.pop()!
          onStack.delete(member)
          component.push(member)
        } while (member !== frame.node)
        if (component.length > 1) components.push(component)
      }
    }
  }

  return components.map(component => cycleThrough(component, outgoing))
}

/** One concrete loop inside a strongly connected component. */
function cycleThrough(component: string[], outgoing: Map<string, string[]>): string[] {
  const members = new Set(component)
  const start = [...component].sort()[0]
  // BFS back to ``start`` inside the component gives the shortest loop.
  const previous = new Map<string, string>()
  const queue = [start]
  const seen = new Set([start])
  while (queue.length > 0) {
    const current = queue.shift()!
    for (const target of outgoing.get(current) ?? []) {
      if (!members.has(target)) continue
      if (target === start) {
        const path = [current]
        while (path[0] !== start) path.unshift(previous.get(path[0])!)
        return path
      }
      if (!seen.has(target)) {
        seen.add(target)
        previous.set(target, current)
        queue.push(target)
      }
    }
  }
  return [start]
}

/** #1031 — a circle that grows 5% per distinct outgoing link, with no cap. */
export function agentNodeDiameter(outgoingCount: number) {
  return Math.round(40 * (1 + 0.05 * Math.max(0, outgoingCount)))
}

/** The name sits under the circle: at most this wide and two lines tall. */
export const AGENT_NAME_WIDTH = 124
export const AGENT_NAME_HEIGHT = 40

/** Space the layout reserves for a circle and the name under it. */
export function agentNodeBox(outgoingCount: number) {
  const d = agentNodeDiameter(outgoingCount)
  return { width: Math.max(d, AGENT_NAME_WIDTH), height: d + AGENT_NAME_HEIGHT }
}

/** #1023 — an agent-to-agent question running now in a room the person can open. */
export interface ActivePeerEdge {
  chain_id: string
  room_id: string
  from_agent_id: string
  to_agent_id: string
}

export async function fetchActivePeerEdges(signal?: AbortSignal): Promise<ActivePeerEdge[]> {
  const response = await apiFetch('/api/v1/peer-chains/active', { signal })
  if (!response.ok) throw await failure(response)
  const body = await response.json() as { edges?: unknown } | null
  return Array.isArray(body?.edges) ? body.edges as ActivePeerEdge[] : []
}

export interface AgentActivity {
  state: 'busy' | 'idle' | 'unavailable'
  /** Rooms it is answering in; empty unless busy. */
  roomIds: string[]
  unavailableCode: string | null
}

/**
 * COL-19 — what the overview shows on an agent's card. An agent that cannot
 * answer reads as such even while a stale turn lingers in one of its rooms.
 */
export function agentActivity(agent: LinkAgent, answeringIn: readonly string[]): AgentActivity {
  const code = agent.unavailable_code ?? null
  if (code || !deriveAgentOnline(agent.actual_state)) {
    return { state: 'unavailable', roomIds: [], unavailableCode: code }
  }
  if (answeringIn.length > 0) return { state: 'busy', roomIds: [...answeringIn], unavailableCode: null }
  return { state: 'idle', roomIds: [], unavailableCode: null }
}
