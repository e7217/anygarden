// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import { MemoryRouter } from 'react-router-dom'
import type { ReactNode } from 'react'
import { apiFetch } from '@/lib/api'

vi.mock('@/lib/api', () => ({ apiFetch: vi.fn() }))
vi.mock('@/hooks/useAuth', () => ({ useAuth: () => ({ user: { id: 'u', is_admin: true } }) }))
const roomsMock = vi.hoisted(() => ({ rooms: {} as Record<string, { id: string; name: string; has_updates?: boolean }[]> }))
vi.mock('@/hooks/useRooms', () => ({ useRooms: () => ({ createAgentDM: vi.fn(), rooms: roomsMock.rooms, agentDMs: [] }) }))
// The page frame and the xyflow canvas need a real layout engine; the
// stand-ins expose the canvas callbacks as plain buttons.
vi.mock('@/components/PageShell', () => ({
  default: ({ children }: { children: ReactNode }) => <main>{children}</main>,
}))
vi.mock('@/components/agent-links/AgentLinksCanvas', () => ({
  default: ({ agents, onSelect, onConnect, activity, activeEdges }: {
    agents: { id: string; name: string }[]
    onSelect: (selection: { kind: 'agent'; id: string } | null) => void
    onConnect: (from: string, to: string) => void
    activity?: Map<string, { state: string; roomIds: string[] }>
    activeEdges?: { from_agent_id: string; to_agent_id: string }[]
  }) => (
    <div data-testid="canvas">
      {agents.map(agent => (
        <button key={agent.id} type="button" onClick={() => onSelect({ kind: 'agent', id: agent.id })}>
          select {agent.name}
        </button>
      ))}
      <p data-testid="canvas-activity">
        {agents.map(agent => `${agent.id}:${activity?.get(agent.id)?.state ?? '-'}`).join(' ')}
      </p>
      <p data-testid="canvas-edges">{(activeEdges ?? []).map(e => `${e.from_agent_id}>${e.to_agent_id}`).join(' ')}</p>
      <button type="button" onClick={() => onConnect('qa', 'coder')}>connect QA to Coder</button>
    </div>
  ),
}))

import AgentLinksPage from './AgentLinksPage'

const fetchMock = vi.mocked(apiFetch)
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status })
const agents = [
  { id: 'coder', name: 'Coder', description: 'Writes code', actual_state: 'running' },
  { id: 'sec', name: 'Security', description: null, actual_state: 'running' },
  { id: 'qa', name: 'QA', description: null, actual_state: 'idle' },
  { id: 'docs', name: 'Docs', description: null, actual_state: 'idle' },
]
function link(id: string, from: string, to: string) {
  return { id, from_agent_id: from, to_agent_id: to, created_at: '', updated_at: '' }
}

function renderPage() {
  return render(<MemoryRouter><AgentLinksPage /></MemoryRouter>)
}

afterEach(() => { cleanup(); vi.resetAllMocks(); roomsMock.rooms = {} })

/** Answers each endpoint the overview reads with a fresh response. */
function serve({ graph, turns = [], edges = [], inbox = [] }: {
  graph: unknown
  turns?: { room_id: string; agent_ids: string[] }[]
  edges?: { chain_id: string; room_id: string; from_agent_id: string; to_agent_id: string }[]
  inbox?: unknown[]
}) {
  fetchMock.mockImplementation(async path => {
    const url = String(path)
    if (url.startsWith('/api/v1/turns/active')) return json(turns)
    if (url.startsWith('/api/v1/peer-chains/active')) return json({ edges })
    if (url.startsWith('/api/v1/inbox')) return json({ items: inbox, projects: [] })
    if (url.startsWith('/api/v1/agent-links')) return json(graph)
    return json({}, 404)
  })
}

describe('AgentLinksPage', () => {
  it('explains links when there are none', async () => {
    fetchMock.mockResolvedValue(json({ agents, links: [] }))
    renderPage()
    expect(await screen.findByTestId('agent-links-empty')).toHaveTextContent('A link lets one agent ask another')
    expect(screen.queryByTestId('agent-links-cycles')).not.toBeInTheDocument()
  })

  it('warns about a loop without blocking the map', async () => {
    fetchMock.mockResolvedValue(json({
      agents,
      links: [link('l1', 'coder', 'sec'), link('l2', 'sec', 'qa'), link('l3', 'qa', 'coder'), link('l4', 'coder', 'docs')],
    }))
    renderPage()
    const warning = await screen.findByTestId('agent-links-cycles')
    expect(warning).toHaveTextContent('Coder → Security → QA → Coder')
    expect(screen.getByTestId('canvas')).toBeInTheDocument()
  })

  it('lists every agent reachable from the selected one', async () => {
    fetchMock.mockResolvedValue(json({
      agents,
      links: [link('l1', 'coder', 'sec'), link('l2', 'sec', 'qa'), link('l3', 'docs', 'coder')],
    }))
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: 'select Coder' }))
    const reach = within(screen.getByTestId('agent-reach-list'))
    expect(reach.getByText('Security')).toBeInTheDocument()
    expect(reach.getByText('QA')).toBeInTheDocument()
    expect(reach.queryByText('Docs')).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'select QA' }))
    expect(screen.getByText('QA has no links, so it answers on its own.')).toBeInTheDocument()
  })

  it('[COL-14] saves a dragged link immediately without a condition dialog', async () => {
    const created = link('l9', 'qa', 'coder')
    fetchMock.mockImplementation(async (_path, init) => init?.method === 'POST' ? json(created, 201) : json({ agents, links: [] }))
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: 'connect QA to Coder' }))
    expect(await screen.findByTestId('agent-link-details')).toHaveTextContent('QA → Coder')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    const post = fetchMock.mock.calls.find(([, init]) => init?.method === 'POST')!
    expect(JSON.parse(String(post[1]!.body))).toEqual({ from_agent_id: 'qa', to_agent_id: 'coder' })
  })

  it('keeps failed drops out of the graph and reports the error', async () => {
    const alert = vi.spyOn(window, 'alert').mockImplementation(() => {})
    fetchMock.mockImplementation(async (_path, init) => init?.method === 'POST' ? json({}, 503) : json({ agents, links: [] }))
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: 'connect QA to Coder' }))
    await waitFor(() => expect(alert).toHaveBeenCalledWith('Could not save the link (503).'))
    expect(screen.queryByTestId('agent-link-details')).not.toBeInTheDocument()
    expect(screen.getByTestId('agent-links-empty')).toBeInTheDocument()
  })

  it('shows a failed load with retry', async () => {
    fetchMock.mockResolvedValue(json({}, 500))
    renderPage()
    expect(await screen.findByText('Could not load agent links (500).')).toBeInTheDocument()
    fetchMock.mockResolvedValue(json({ agents, links: [] }))
    fireEvent.click(screen.getByRole('button', { name: /try again/i }))
    expect(await screen.findByTestId('canvas')).toBeInTheDocument()
  })

  it('[COL-19] marks who is answering and who cannot, and the running questions', async () => {
    roomsMock.rooms = { p: [{ id: 'r1', name: 'release' }, { id: 'r2', name: 'design', has_updates: true }] }
    serve({
      graph: { agents: [...agents.slice(0, 3), { ...agents[3], actual_state: 'running', unavailable_code: 'crashed' }], links: [] },
      turns: [{ room_id: 'r1', agent_ids: ['coder', 'sec'] }],
      edges: [{ chain_id: 'c', room_id: 'r1', from_agent_id: 'coder', to_agent_id: 'sec' }],
    })
    renderPage()
    await waitFor(() => expect(screen.getByTestId('canvas-activity')).toHaveTextContent('coder:busy sec:busy qa:unavailable docs:unavailable'))
    expect(screen.getByTestId('canvas-edges')).toHaveTextContent('coder>sec')
    expect(screen.getByText('4 agents · 2 answering · 0 links')).toBeInTheDocument()

    const summary = within(screen.getByTestId('agent-overview-summary'))
    expect(summary.getByRole('link', { name: /#release/ })).toHaveAttribute('href', '/rooms/r1')
    expect(summary.getByText('Coder → Security')).toBeInTheDocument()
    expect(summary.getByRole('link', { name: '#design' })).toHaveAttribute('href', '/rooms/r2')
  })

  it('[COL-19] puts what needs the person first and links to the inbox', async () => {
    serve({
      graph: { agents, links: [] },
      inbox: [{ id: 'i1', needs_action: true, task_title: 'Approve the release', task_room_name: 'release', task_href: '/rooms/r1?task=t1' }],
    })
    renderPage()
    const summary = within(await screen.findByTestId('agent-overview-summary'))
    expect(summary.getByRole('link', { name: /Approve the release/ })).toHaveAttribute('href', '/rooms/r1?task=t1')
    expect(summary.getByRole('link', { name: 'Open inbox' })).toHaveAttribute('href', '/inbox')
  })

  it('[COL-19] says so when nothing is happening, and hides the summary while an agent is selected', async () => {
    serve({ graph: { agents, links: [] } })
    renderPage()
    expect(await screen.findByTestId('agent-overview-quiet')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'select Coder' }))
    expect(screen.queryByTestId('agent-overview-quiet')).not.toBeInTheDocument()
    expect(screen.getByTestId('agent-reach-details')).toBeInTheDocument()
  })
})
