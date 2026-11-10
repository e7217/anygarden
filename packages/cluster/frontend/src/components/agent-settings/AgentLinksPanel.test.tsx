// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import { MemoryRouter } from 'react-router-dom'
import AgentLinksPanel from './AgentLinksPanel'
import { apiFetch } from '@/lib/api'

vi.mock('@/lib/api', () => ({ apiFetch: vi.fn() }))
const fetchMock = vi.mocked(apiFetch)
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status })

const agents = [
  { id: 'coder', name: 'Coder', description: null, actual_state: 'running' },
  { id: 'sec', name: 'Security', description: null, actual_state: 'running' },
  { id: 'qa', name: 'QA', description: null, actual_state: 'idle' },
]
const link = {
  id: 'l1', from_agent_id: 'coder', to_agent_id: 'sec',
  when_to_ask: 'when auth or permissions are involved',
  created_at: '2026-11-10T00:00:00Z', updated_at: '2026-11-10T00:00:00Z',
}
// An incoming link must not show in the outgoing list.
const incoming = { ...link, id: 'l2', from_agent_id: 'qa', to_agent_id: 'coder', when_to_ask: 'when code changes' }

function serve(overrides: (path: string, init?: RequestInit) => Response | undefined = () => undefined) {
  fetchMock.mockImplementation(async (path, init) => {
    const custom = overrides(String(path), init)
    if (custom) return custom
    return json({ agents, links: [link, incoming] })
  })
}

function renderPanel() {
  return render(
    <MemoryRouter>
      <AgentLinksPanel agentId="coder" agentName="Coder" />
    </MemoryRouter>,
  )
}

afterEach(() => { cleanup(); vi.resetAllMocks() })

describe('AgentLinksPanel', () => {
  it('lists only the agent’s outgoing links with their sentence', async () => {
    serve()
    renderPanel()
    expect(await screen.findByText('when auth or permissions are involved')).toBeInTheDocument()
    expect(screen.getByText('Security')).toBeInTheDocument()
    expect(screen.queryByText('when code changes')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Edit link to Security' })).toBeInTheDocument()
  })

  it('[COL-14] adds a link to a chosen agent and offers already linked agents disabled', async () => {
    const created = { ...link, id: 'l3', to_agent_id: 'qa', when_to_ask: 'when tests are missing' }
    serve((path, init) => (init?.method === 'POST' ? json(created, 201) : undefined))
    renderPanel()
    await screen.findByText('when auth or permissions are involved')
    fireEvent.click(screen.getByRole('button', { name: 'Add link' }))

    const dialog = await screen.findByRole('dialog')
    const target = within(dialog).getByLabelText('Agent it can ask') as HTMLSelectElement
    expect(within(target).getByRole('option', { name: 'Security' })).toBeDisabled()
    expect(within(target).getByRole('option', { name: 'Coder' })).toBeDisabled()
    fireEvent.change(target, { target: { value: 'qa' } })
    fireEvent.change(within(dialog).getByLabelText('When to ask'), { target: { value: '  when tests are missing ' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Add link' }))

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    const post = fetchMock.mock.calls.find(([, init]) => init?.method === 'POST')!
    expect(post[0]).toBe('/api/v1/agent-links')
    expect(JSON.parse(String(post[1]!.body))).toEqual({ from_agent_id: 'coder', to_agent_id: 'qa', when_to_ask: 'when tests are missing' })
    expect(screen.getByText('when tests are missing')).toBeInTheDocument()
  })

  it('requires a sentence and keeps it within 300 characters', async () => {
    serve()
    renderPanel()
    await screen.findByText('when auth or permissions are involved')
    fireEvent.click(screen.getByRole('button', { name: 'Edit link to Security' }))
    const dialog = await screen.findByRole('dialog')
    const field = within(dialog).getByLabelText('When to ask')
    fireEvent.change(field, { target: { value: '   ' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }))
    expect(within(dialog).getByText('Say when this agent should ask.')).toBeInTheDocument()
    fireEvent.change(field, { target: { value: 'x'.repeat(301) } })
    expect(within(dialog).getByText('Keep it to 300 characters.')).toBeInTheDocument()
    expect(fetchMock.mock.calls.some(([, init]) => init?.method === 'PATCH')).toBe(false)
  })

  it('[COL-14] saves an edited sentence with PATCH', async () => {
    serve((path, init) => (init?.method === 'PATCH' ? json({ ...link, when_to_ask: 'for any login flow' }) : undefined))
    renderPanel()
    await screen.findByText('when auth or permissions are involved')
    fireEvent.click(screen.getByRole('button', { name: 'Edit link to Security' }))
    const dialog = await screen.findByRole('dialog')
    fireEvent.change(within(dialog).getByLabelText('When to ask'), { target: { value: 'for any login flow' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(await screen.findByText('for any login flow')).toBeInTheDocument()
    const patch = fetchMock.mock.calls.find(([, init]) => init?.method === 'PATCH')!
    expect(patch[0]).toBe('/api/v1/agent-links/l1')
    expect(JSON.parse(String(patch[1]!.body))).toEqual({ when_to_ask: 'for any login flow' })
  })

  it('explains a duplicate instead of closing the dialog', async () => {
    serve((path, init) => (init?.method === 'POST' ? json({ detail: 'Link already exists' }, 409) : undefined))
    renderPanel()
    await screen.findByText('when auth or permissions are involved')
    fireEvent.click(screen.getByRole('button', { name: 'Add link' }))
    const dialog = await screen.findByRole('dialog')
    fireEvent.change(within(dialog).getByLabelText('Agent it can ask'), { target: { value: 'qa' } })
    fireEvent.change(within(dialog).getByLabelText('When to ask'), { target: { value: 'when tests are missing' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Add link' }))
    expect(await within(dialog).findByRole('alert')).toHaveTextContent('This link already exists')
  })

  it('[COL-14] deletes a link after confirmation', async () => {
    serve((path, init) => (init?.method === 'DELETE' ? new Response(null, { status: 204 }) : undefined))
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)
    renderPanel()
    await screen.findByText('when auth or permissions are involved')
    fireEvent.click(screen.getByRole('button', { name: 'Delete link to Security' }))
    await waitFor(() => expect(screen.queryByText('when auth or permissions are involved')).not.toBeInTheDocument())
    expect(confirmSpy).toHaveBeenCalled()
    expect(fetchMock).toHaveBeenCalledWith('/api/v1/agent-links/l1', { method: 'DELETE' })
    expect(screen.getByText('Coder cannot ask any agent yet.')).toBeInTheDocument()
  })

  it('shows a failed load with retry', async () => {
    fetchMock.mockResolvedValue(json({}, 503))
    renderPanel()
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not load agent links (503).')
    serve()
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }))
    expect(await screen.findByText('when auth or permissions are involved')).toBeInTheDocument()
  })
})
