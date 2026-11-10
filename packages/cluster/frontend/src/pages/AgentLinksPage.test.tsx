// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import { MemoryRouter } from 'react-router-dom'
import type { ReactNode } from 'react'
import { apiFetch } from '@/lib/api'

vi.mock('@/lib/api', () => ({ apiFetch: vi.fn() }))
// The page frame and the xyflow canvas need a real layout engine; the
// stand-ins expose the canvas callbacks as plain buttons.
vi.mock('@/components/PageShell', () => ({
  default: ({ children }: { children: ReactNode }) => <main>{children}</main>,
}))
vi.mock('@/components/agent-links/AgentLinksCanvas', () => ({
  default: ({ agents, onSelect, onConnect }: {
    agents: { id: string; name: string }[]
    onSelect: (selection: { kind: 'agent'; id: string } | null) => void
    onConnect: (from: string, to: string) => void
  }) => (
    <div data-testid="canvas">
      {agents.map(agent => (
        <button key={agent.id} type="button" onClick={() => onSelect({ kind: 'agent', id: agent.id })}>
          select {agent.name}
        </button>
      ))}
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
function link(id: string, from: string, to: string, when = `${from} asks ${to}`) {
  return { id, from_agent_id: from, to_agent_id: to, when_to_ask: when, created_at: '', updated_at: '' }
}

function renderPage() {
  return render(<MemoryRouter><AgentLinksPage /></MemoryRouter>)
}

afterEach(() => { cleanup(); vi.resetAllMocks() })

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

  it('[COL-14] creates a dragged link after asking when to ask', async () => {
    const created = link('l9', 'qa', 'coder', 'when a test fails')
    fetchMock.mockImplementation(async (_path, init) => (
      init?.method === 'POST' ? json(created, 201) : json({ agents, links: [] })
    ))
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: 'connect QA to Coder' }))
    const dialog = await screen.findByRole('dialog')
    expect(dialog).toHaveTextContent('QA will be able to ask Coder')
    // Both ends come from the drag, so no agent pickers are shown.
    expect(within(dialog).queryByLabelText('Agent that asks')).not.toBeInTheDocument()
    fireEvent.change(within(dialog).getByLabelText('When to ask'), { target: { value: 'when a test fails' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Add link' }))

    // The new link is selected so its details show at once.
    expect(await screen.findByTestId('agent-link-details')).toHaveTextContent('when a test fails')
    const post = fetchMock.mock.calls.find(([, init]) => init?.method === 'POST')!
    expect(JSON.parse(String(post[1]!.body))).toEqual({ from_agent_id: 'qa', to_agent_id: 'coder', when_to_ask: 'when a test fails' })
    await waitFor(() => expect(screen.queryByTestId('agent-links-empty')).not.toBeInTheDocument())
  })

  it('shows a failed load with retry', async () => {
    fetchMock.mockResolvedValue(json({}, 500))
    renderPage()
    expect(await screen.findByText('Could not load agent links (500).')).toBeInTheDocument()
    fetchMock.mockResolvedValue(json({ agents, links: [] }))
    fireEvent.click(screen.getByRole('button', { name: /try again/i }))
    expect(await screen.findByTestId('canvas')).toBeInTheDocument()
  })
})
