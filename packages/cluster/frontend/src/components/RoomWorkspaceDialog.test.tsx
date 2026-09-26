// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'

const mocks = vi.hoisted(() => ({
  calls: [] as Array<{ path: string; method: string }>,
  admin: false,
  archived: false,
  role: 'owner',
  mutation: null as null | Promise<unknown>,
  confirm: vi.fn().mockResolvedValue(true),
}))
vi.mock('@/components/feedback/FeedbackProvider', () => ({ useFeedback: () => ({ confirm: mocks.confirm }) }))
vi.mock('@/lib/api', () => ({ apiFetch: vi.fn(async (path: string, init?: RequestInit) => {
  const method = init?.method ?? 'GET'
  mocks.calls.push({ path, method })
  const response = (data: unknown, status = 200) => ({ ok: status < 400, status, json: async () => data })
  if (method !== 'GET') {
    if (mocks.mutation) await mocks.mutation
    return response({})
  }
  if (path === '/api/v1/auth/me') return response({ id: 'user', is_admin: mocks.admin })
  if (/\/rooms\/[^/]+$/.test(path)) return response({ archived_at: mocks.archived ? '2026-01-01' : null, participants: [{ user_id: 'user', role: mocks.role }, { agent_id: 'agent', role: 'member', display_name: 'Helper' }] })
  if (path.endsWith('/workspace-attachments')) return response([{ id: 'attachment', agent_id: 'agent', workspace_label: path.includes('room-2') ? 'Other repository' : 'Repository', state: 'requested', mode: 'read', expires_at: '2099-01-01T00:00:00Z', room_approved_by_user_id: null, global_approved_by_user_id: null }])
  throw new Error(`Unexpected request ${path}`)
}) }))

import RoomWorkspaceDialog from './RoomWorkspaceDialog'
const props = { open: true, onOpenChange: vi.fn(), roomId: 'room-1', roomName: 'Engineering' }
beforeEach(() => { mocks.calls = []; mocks.admin = false; mocks.archived = false; mocks.role = 'owner'; mocks.mutation = null })
afterEach(() => { cleanup(); vi.clearAllMocks() })

it('allows a non-global room owner to approve with room APIs alone', async () => {
  render(<RoomWorkspaceDialog {...props} />)
  fireEvent.click(await screen.findByRole('button', { name: 'Approve as room administrator' }))
  await waitFor(() => expect(mocks.calls).toContainEqual({ path: '/api/v1/rooms/room-1/workspace-attachments/attachment/approve-room', method: 'POST' }))
  expect(screen.queryByRole('button', { name: 'Approve as system administrator' })).not.toBeInTheDocument()
  expect(mocks.calls.some(call => call.path.startsWith('/api/v1/agents/'))).toBe(false)
})

it('does not fetch while closed and clears room state on a room change', async () => {
  const view = render(<RoomWorkspaceDialog {...props} open={false} />)
  expect(mocks.calls).toHaveLength(0)
  view.rerender(<RoomWorkspaceDialog {...props} />)
  await screen.findByText('Repository')
  view.rerender(<RoomWorkspaceDialog {...props} roomId="room-2" roomName="Other" />)
  expect(screen.queryByText('Repository')).not.toBeInTheDocument()
  expect(await screen.findByText('Other repository')).toBeInTheDocument()
})

it('keeps an archived room read-only even for system administrators', async () => {
  mocks.admin = true; mocks.archived = true
  render(<RoomWorkspaceDialog {...props} />)
  await screen.findByText('Repository')
  expect(screen.queryByRole('button', { name: /Approve as/ })).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Revoke connection' })).not.toBeInTheDocument()
})

it('does not apply a late mutation failure after closing and reopening the same room', async () => {
  let rejectMutation!: (reason: Error) => void
  mocks.mutation = new Promise((_, reject) => { rejectMutation = reject })
  const view = render(<RoomWorkspaceDialog {...props} />)
  fireEvent.click(await screen.findByRole('button', { name: 'Approve as room administrator' }))
  await waitFor(() => expect(mocks.calls.some(call => call.method === 'POST')).toBe(true))
  view.rerender(<RoomWorkspaceDialog {...props} open={false} />)
  view.rerender(<RoomWorkspaceDialog {...props} />)
  await screen.findByText('Repository')
  await act(async () => { rejectMutation(new Error('Old request failed')) })
  expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Approve as room administrator' })).toBeEnabled()
})

it('requires confirmation and preserves API errors when revocation fails', async () => {
  mocks.mutation = Promise.resolve().then(() => { throw new Error('Connection unavailable') })
  // Keep the promise rejection attached until the mutation starts.
  void mocks.mutation.catch(() => undefined)
  render(<RoomWorkspaceDialog {...props} />)
  fireEvent.click(await screen.findByRole('button', { name: 'Revoke connection' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('Connection unavailable')
  expect(mocks.confirm).toHaveBeenCalledWith(expect.objectContaining({ destructive: true }))
})
