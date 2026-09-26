// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import RoomsPanel from './RoomsPanel'
import { apiFetch } from '@/lib/api'
vi.mock('@/components/EngineGlyph', () => ({ EngineGlyph: () => null }))
vi.mock('@/lib/api', () => ({ apiFetch: vi.fn() }))
const fetch = vi.mocked(apiFetch)
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status })
const rooms = [{ id: 'room-A', name: 'Alpha room', project_id: 'p' }, { id: 'room-B', name: 'Beta room', project_id: 'p' }]
function inventory() {
  fetch.mockImplementation(async path => {
    if (path === '/api/v1/projects') return json([{ id: 'p' }])
    if (String(path).startsWith('/api/v1/rooms?')) return json(rooms)
    return json([{ room_id: 'room-A', room_name: 'Alpha room' }])
  })
}
afterEach(() => { cleanup(); vi.resetAllMocks() })
describe('RoomsPanel recovery', () => {
  it('shows a failed query and retry instead of claiming no membership', async () => {
    fetch.mockResolvedValue(json({}, 503))
    render(<RoomsPanel agentId="agent-A" />)
    expect(await screen.findByRole('alert')).toHaveTextContent(/could not/i)
    expect(screen.queryByText('No rooms assigned')).not.toBeInTheDocument()
    inventory()
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }))
    expect(await screen.findByText('Alpha room')).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })
  it.each(['add', 'remove'])('preserves membership and reports a failed %s', async operation => {
    inventory()
    const onChange = vi.fn()
    render(<RoomsPanel agentId="agent-A" onChange={onChange} />)
    await screen.findByText('Alpha room')
    fetch.mockImplementation(async (_path, init) => init?.method ? json({ detail: 'Permission denied' }, 403) : json([]))
    fireEvent.click(screen.getByRole('button', { name: operation === 'add' ? /add room: Beta room/i : /remove room: Alpha room/i }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Permission denied')
    expect(screen.getByRole('button', { name: /remove room: Alpha room/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /add room: Beta room/i })).toBeInTheDocument()
    expect(onChange).not.toHaveBeenCalled()
  })
  it('keeps a successful write visible when inventory refresh fails', async () => {
    inventory()
    const onChange = vi.fn()
    render(<RoomsPanel agentId="agent-A" onChange={onChange} />)
    await screen.findByText('Alpha room')
    fetch.mockImplementation(async (_path, init) => init?.method ? new Response(null, { status: 204 }) : json({}, 503))
    fireEvent.click(screen.getByRole('button', { name: /add room: Beta room/i }))
    expect(await screen.findByRole('alert')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /remove room: Beta room/i })).toBeInTheDocument()
    expect(onChange).toHaveBeenCalledOnce()
  })
  it('ignores mutation completion after switching agents', async () => {
    inventory()
    const onChange = vi.fn()
    const { rerender } = render(<RoomsPanel agentId="agent-A" onChange={onChange} />)
    await screen.findByText('Alpha room')
    let resolve!: (response: Response) => void
    fetch.mockImplementation(async (path, init) => {
      if (init?.method) return new Promise<Response>(done => { resolve = done })
      if (path === '/api/v1/projects') return json([])
      return json([{ room_id: 'room-B', room_name: 'Beta room' }])
    })
    fireEvent.click(screen.getByRole('button', { name: /add room: Beta room/i }))
    rerender(<RoomsPanel agentId="agent-B" onChange={onChange} />)
    await waitFor(() => expect(screen.getByRole('button', { name: /remove room: Beta room/i })).toBeInTheDocument())
    const count = fetch.mock.calls.length
    await act(async () => resolve(new Response(null, { status: 204 })))
    expect(onChange).not.toHaveBeenCalled()
    expect(fetch).toHaveBeenCalledTimes(count)
    expect(screen.queryByText('Alpha room')).not.toBeInTheDocument()
  })
})
