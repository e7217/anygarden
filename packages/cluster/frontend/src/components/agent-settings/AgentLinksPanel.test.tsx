// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import { MemoryRouter } from 'react-router-dom'
import AgentLinksPanel from './AgentLinksPanel'
import { apiFetch } from '@/lib/api'
vi.mock('@/lib/api', () => ({ apiFetch: vi.fn() }))
const fetchMock = vi.mocked(apiFetch)
const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status })
const agents = ['Coder', 'Security', 'QA'].map(name => ({ id: name.toLowerCase(), name, description: null, actual_state: 'idle' }))
const link = { id: 'l1', from_agent_id: 'coder', to_agent_id: 'security', created_at: '', updated_at: '' }
const incoming = { ...link, id: 'l2', from_agent_id: 'qa', to_agent_id: 'coder' }
function setup(postStatus = 201) {
  fetchMock.mockImplementation(async (_path, init) => {
    if (init?.method === 'POST') return json(postStatus === 201 ? { ...link, id: 'l3', to_agent_id: 'qa' } : { detail: 'duplicate' }, postStatus)
    if (init?.method === 'DELETE') return new Response(null, { status: 204 })
    return json({ agents, links: [link, incoming] })
  })
  render(<MemoryRouter><AgentLinksPanel agentId="coder" agentName="Coder" /></MemoryRouter>)
}
afterEach(() => { cleanup(); vi.resetAllMocks() })
it('[COL-14] adds links with only a target picker and prevents self and duplicate choices', async () => {
  setup()
  await screen.findByText('Security')
  expect(screen.queryByRole('button', { name: /edit/i })).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Add link' }))
  const dialog = within(await screen.findByRole('dialog'))
  const target = dialog.getByLabelText('Agent it can ask')
  expect(within(target).getByRole('option', { name: 'Security' })).toBeDisabled()
  expect(within(target).getByRole('option', { name: 'Coder' })).toBeDisabled()
  fireEvent.change(target, { target: { value: 'qa' } })
  expect(dialog.queryByRole('textbox')).not.toBeInTheDocument()
  fireEvent.click(dialog.getByRole('button', { name: 'Add link' }))
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  const post = fetchMock.mock.calls.find(([, init]) => init?.method === 'POST')!
  expect(JSON.parse(String(post[1]!.body))).toEqual({ from_agent_id: 'coder', to_agent_id: 'qa' })
})
it('keeps a failed creation open with an actionable duplicate message', async () => {
  setup(409)
  await screen.findByText('Security')
  fireEvent.click(screen.getByRole('button', { name: 'Add link' }))
  const dialog = within(await screen.findByRole('dialog'))
  fireEvent.change(dialog.getByLabelText('Agent it can ask'), { target: { value: 'qa' } })
  fireEvent.click(dialog.getByRole('button', { name: 'Add link' }))
  expect(await dialog.findByRole('alert')).toHaveTextContent('already linked')
})
it('[COL-14] deletes the outgoing link after confirmation', async () => {
  setup()
  vi.spyOn(window, 'confirm').mockReturnValue(true)
  fireEvent.click(await screen.findByRole('button', { name: 'Delete link to Security' }))
  await waitFor(() => expect(screen.queryByText('Security')).not.toBeInTheDocument())
  expect(fetchMock).toHaveBeenCalledWith('/api/v1/agent-links/l1', { method: 'DELETE' })
})
