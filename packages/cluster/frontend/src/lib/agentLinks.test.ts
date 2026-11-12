import { afterEach, describe, expect, it, vi } from 'vitest'
import { AgentLinkError, agentActivity, createAgentLink, deleteAgentLink, fetchActivePeerEdges, fetchAgentAlerts, fetchAgentLinkGraph, topAlert, findCycles, reachableFrom, agentNodeDiameter, agentNodeBox, type AgentLink, type LinkAgent } from './agentLinks'
import { isLinkParticipant, roomMembers, type Participant } from './participants'
import { apiFetch } from './api'

vi.mock('./api', () => ({ apiFetch: vi.fn() }))
const fetchMock = vi.mocked(apiFetch)
afterEach(() => vi.resetAllMocks())

function link(from: string, to: string): AgentLink {
  return { id: `${from}>${to}`, from_agent_id: from, to_agent_id: to, created_at: '', updated_at: '' }
}

describe('reachableFrom', () => {
  it('follows links forward only, transitively', () => {
    const links = [link('a', 'b'), link('b', 'c'), link('d', 'a'), link('x', 'y')]
    const { agents, links: used } = reachableFrom('a', links)
    expect([...agents].sort()).toEqual(['a', 'b', 'c'])
    expect([...used].sort()).toEqual(['a>b', 'b>c'])
  })

  it('terminates on cycles and includes every link inside the loop', () => {
    const links = [link('a', 'b'), link('b', 'c'), link('c', 'a')]
    const { agents, links: used } = reachableFrom('b', links)
    expect([...agents].sort()).toEqual(['a', 'b', 'c'])
    expect(used.size).toBe(3)
  })

  it('returns just the agent when it has no links', () => {
    expect([...reachableFrom('lonely', [link('a', 'b')]).agents]).toEqual(['lonely'])
  })
})

describe('findCycles', () => {
  it('finds nothing in a DAG', () => {
    expect(findCycles([link('a', 'b'), link('b', 'c'), link('a', 'c')])).toEqual([])
  })

  it('reports a three-agent loop once, as a path back to its start', () => {
    expect(findCycles([link('a', 'b'), link('b', 'c'), link('c', 'a')])).toEqual([['a', 'b', 'c']])
  })

  it('reports a two-way pair', () => {
    expect(findCycles([link('a', 'b'), link('b', 'a')])).toEqual([['a', 'b']])
  })

  it('reports separate loops separately and ignores links that only lead into them', () => {
    const cycles = findCycles([link('a', 'b'), link('b', 'a'), link('c', 'd'), link('d', 'e'), link('e', 'c'), link('z', 'a')])
    expect(cycles).toHaveLength(2)
    expect(cycles).toContainEqual(['a', 'b'])
    expect(cycles).toContainEqual(['c', 'd', 'e'])
  })

  it('reports interleaved loops in one component once, through the shortest loop', () => {
    const cycles = findCycles([link('a', 'b'), link('b', 'a'), link('b', 'c'), link('c', 'd'), link('d', 'a')])
    expect(cycles).toEqual([['a', 'b']])
  })

  it('handles a long chain without recursion limits', () => {
    const chain = Array.from({ length: 5000 }, (_, i) => link(`n${i}`, `n${i + 1}`))
    expect(findCycles(chain)).toEqual([])
    expect(findCycles([...chain, link('n5000', 'n0')])).toHaveLength(1)
  })
})

describe('agent link requests', () => {
  it('tolerates a body without agents or links', async () => {
    fetchMock.mockResolvedValue(new Response('[]', { status: 200 }))
    await expect(fetchAgentLinkGraph()).resolves.toEqual({ agents: [], links: [] })
  })

  it('carries status and string detail of a failed write', async () => {
    fetchMock.mockResolvedValue(new Response(JSON.stringify({ detail: 'duplicate link' }), { status: 409 }))
    const error = await createAgentLink({ from_agent_id: 'a', to_agent_id: 'b' }).catch(e => e)
    expect(error).toBeInstanceOf(AgentLinkError)
    expect(error.status).toBe(409)
    expect(error.detail).toBe('duplicate link')
  })

  it('drops a non-string 422 detail', async () => {
    fetchMock.mockResolvedValue(new Response(JSON.stringify({ detail: [{ msg: 'too long' }] }), { status: 422 }))
    const error = await createAgentLink({ from_agent_id: 'a', to_agent_id: 'b' }).catch(e => e)
    expect(error.status).toBe(422)
    expect(error.detail).toBeNull()
  })

  it('treats deleting an already deleted link as done', async () => {
    fetchMock.mockResolvedValue(new Response(null, { status: 404 }))
    await expect(deleteAgentLink('gone')).resolves.toBeUndefined()
  })
})

describe('room members', () => {
  it('drops participants that joined through an agent link', () => {
    const roster: Record<string, Participant> = {
      p1: { id: 'p1', display_name: 'Alice', kind: 'user' },
      p2: { id: 'p2', display_name: 'Coder', kind: 'agent', joined_via: null },
      p3: { id: 'p3', display_name: 'Security', kind: 'agent', joined_via: 'link' },
    }
    expect(Object.keys(roomMembers(roster))).toEqual(['p1', 'p2'])
    expect(isLinkParticipant(roster.p3)).toBe(true)
  })
})

it('grows the circle 5% per outgoing link without a cap', () => {
  expect(agentNodeDiameter(0)).toBe(40)
  expect(agentNodeDiameter(1)).toBe(42)
  expect(agentNodeDiameter(4)).toBe(48)
  expect(agentNodeDiameter(20)).toBe(80)
  expect(agentNodeDiameter(-1)).toBe(40)
})

it('lays out the circle with room for a two-line name under it', () => {
  expect(agentNodeBox(0)).toEqual({ width: 124, height: 40 + 40 })
  expect(agentNodeBox(60)).toEqual({ width: 160, height: 160 + 40 })
})

describe('agentActivity', () => {
  const agent = (over: Partial<LinkAgent> = {}): LinkAgent =>
    ({ id: 'a', name: 'A', description: null, actual_state: 'running', unavailable_code: null, ...over })

  it('is busy in the rooms where it is answering [COL-19]', () => {
    expect(agentActivity(agent(), ['r1', 'r2'])).toEqual({ state: 'busy', roomIds: ['r1', 'r2'], unavailableCode: null })
  })

  it('is idle when running with nothing to answer [COL-19]', () => {
    expect(agentActivity(agent(), [])).toEqual({ state: 'idle', roomIds: [], unavailableCode: null })
  })

  it('cannot answer when stopped or carrying an unavailable code [COL-19]', () => {
    expect(agentActivity(agent({ actual_state: 'stopped' }), []).state).toBe('unavailable')
    expect(agentActivity(agent({ unavailable_code: 'crashed' }), ['r1']))
      .toEqual({ state: 'unavailable', roomIds: [], unavailableCode: 'crashed' })
  })
})

describe('active peer edges', () => {
  it('reads the running questions [COL-19]', async () => {
    fetchMock.mockResolvedValue(new Response(JSON.stringify({
      edges: [{ chain_id: 'c', room_id: 'r', from_agent_id: 'a', to_agent_id: 'b' }],
    }), { status: 200 }))
    expect(await fetchActivePeerEdges()).toEqual([{ chain_id: 'c', room_id: 'r', from_agent_id: 'a', to_agent_id: 'b' }])
    expect(fetchMock).toHaveBeenCalledWith('/api/v1/peer-chains/active', expect.anything())
  })
})


describe('agent alerts [COL-20]', () => {
  const none = { agent_id: 'a', failed_tasks: [], failed_tasks_count: 0, failed_rooms: [], waiting: [], waiting_count: 0, unread_rooms: [], queued_tasks: 0 }

  it('shows one alert: a problem, then waiting, then unread', () => {
    expect(topAlert(undefined)).toBeNull()
    expect(topAlert({ ...none, queued_tasks: 3 })).toBeNull()
    expect(topAlert({ ...none, unread_rooms: ['r'] })).toBe('unread')
    expect(topAlert({ ...none, unread_rooms: ['r'], waiting_count: 1 })).toBe('waiting')
    expect(topAlert({ ...none, waiting_count: 1, failed_rooms: ['r'] })).toBe('issue')
    expect(topAlert({ ...none, failed_tasks_count: 1 })).toBe('issue')
  })

  it('reads the alerts endpoint', async () => {
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ agents: [none] }), { status: 200 }))
    expect(await fetchAgentAlerts()).toEqual([none])
    expect(fetchMock).toHaveBeenCalledWith('/api/v1/agent-links/alerts', expect.anything())
  })
})
