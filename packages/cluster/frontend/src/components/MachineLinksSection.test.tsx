// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'

const mocks = vi.hoisted(() => ({
  apiFetch: vi.fn(),
  confirm: vi.fn(async () => true),
  notify: vi.fn(),
}))
vi.mock('@/lib/api', () => ({ apiFetch: mocks.apiFetch }))
vi.mock('@/components/feedback/FeedbackProvider', () => ({ useFeedback: () => ({ confirm: mocks.confirm, notify: mocks.notify }) }))

import MachineLinksSection from './MachineLinksSection'

const link = {
  machine_id: 'm1',
  name: 'Y remote',
  server: 'http://x.test:8000',
  status: 'auth_failed',
  error: 'server rejected WebSocket connection: HTTP 403',
  running_agents: 0,
}

function respond(status: number, body: unknown) {
  return { ok: status >= 200 && status < 300, status, json: async () => body }
}

beforeEach(() => { mocks.apiFetch.mockReset(); mocks.confirm.mockClear(); mocks.notify.mockClear() })
afterEach(() => cleanup())

describe('serve another server (#848)', () => {
  it('stays hidden on a server that is not an integrated node', async () => {
    mocks.apiFetch.mockResolvedValue(respond(404, { detail: { code: 'MACHINE_LINKS_UNAVAILABLE' } }))
    const { container } = render(<MachineLinksSection />)
    await waitFor(() => expect(mocks.apiFetch).toHaveBeenCalled())
    expect(container).toBeEmptyDOMElement()
  })

  it('shows each link with a text status and explains a rejected token', async () => {
    mocks.apiFetch.mockResolvedValue(respond(200, [link]))
    render(<MachineLinksSection />)
    expect(await screen.findByText('Y remote')).toBeInTheDocument()
    expect(screen.getByText('Token rejected')).toBeInTheDocument()
    expect(screen.getByText(/Issue a new token there/)).toBeInTheDocument()
  })

  it('adds a link from a pasted code and reports a code from this node', async () => {
    mocks.apiFetch.mockImplementation(async (url: string, init?: RequestInit) => {
      if (init?.method === 'POST') {
        const code = JSON.parse(String(init.body)).code
        if (code === 'agmachine1.self') return respond(409, { detail: { code: 'MACHINE_LINK_SELF', message: 'own' } })
        return respond(201, { ...link, status: 'connecting', error: null })
      }
      return respond(200, [])
    })
    render(<MachineLinksSection />)
    fireEvent.click(await screen.findByRole('button', { name: 'Add' }))
    const field = screen.getByLabelText('Connection code')
    fireEvent.change(field, { target: { value: 'agmachine1.self' } })
    fireEvent.click(screen.getByRole('button', { name: 'Connect' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('own machine')

    fireEvent.change(field, { target: { value: '  agmachine1.good \n' } })
    fireEvent.click(screen.getByRole('button', { name: 'Connect' }))
    expect(await screen.findByText('Y remote')).toBeInTheDocument()
    const post = mocks.apiFetch.mock.calls.filter(([, init]) => init?.method === 'POST').at(-1)!
    expect(JSON.parse(String(post[1].body))).toEqual({ code: 'agmachine1.good' })
    expect(mocks.notify).toHaveBeenCalledWith(expect.objectContaining({ tone: 'success' }))
  })

  it('disconnects only after confirmation', async () => {
    mocks.apiFetch.mockImplementation(async (_url: string, init?: RequestInit) =>
      init?.method === 'DELETE' ? respond(204, null) : respond(200, [link]))
    render(<MachineLinksSection />)
    fireEvent.click(await screen.findByRole('button', { name: 'Disconnect Y remote' }))
    await waitFor(() => expect(screen.queryByText('Y remote')).not.toBeInTheDocument())
    expect(mocks.confirm).toHaveBeenCalledWith(expect.objectContaining({ destructive: true }))
    expect(mocks.apiFetch).toHaveBeenCalledWith('/api/v1/machine-links/m1', { method: 'DELETE' })
  })
})
