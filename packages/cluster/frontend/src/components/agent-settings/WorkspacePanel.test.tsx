// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import { MemoryRouter } from 'react-router-dom'

const mocks = vi.hoisted(() => ({
  calls: [] as Array<{ path: string; method: string; body: unknown }>,
  viewer: { id: 'user-1', is_admin: true },
  userRole: 'owner',
  catalog: [] as Array<{ workspace_id: string; label: string; max_mode: string; expires_at: string }>,
  attachments: [] as Array<Record<string, unknown>>,
  failure: '',
  unsupported: false,
  optionsFailures: [] as string[],
  roomIds: ['room-1'],
  executionKind: 'remote',
  nodeDataDir: null as string | null,
  confirm: vi.fn().mockResolvedValue(true),
}))
vi.mock('@/components/feedback/FeedbackProvider', () => ({ useFeedback: () => ({ confirm: mocks.confirm }) }))
vi.mock('@/lib/api', () => ({ apiFetch: vi.fn(async (path: string, init?: RequestInit) => {
  const method = init?.method ?? 'GET'
  const body = init?.body ? JSON.parse(String(init.body)) : undefined
  mocks.calls.push({ path, method, body })
  const response = (value: unknown, status = 200) => ({ ok: status < 400, status, json: async () => value })
  if (mocks.failure && method !== 'GET') return response({ detail: mocks.failure }, 409)
  if (path === '/api/v1/auth/me') return response(mocks.viewer)
  if (path === '/api/v1/agents/agent-1') return response({ placed_on_machine_id: 'machine-1' })
  if (path === '/api/v1/agents/agent-1/rooms') return response(mocks.roomIds.map(room_id => ({ room_id, room_name: room_id === 'room-1' ? 'Engineering' : 'Other room' })))
  if (path.includes('/workspace-attachments/options?')) {
    if (mocks.optionsFailures.some(id => path.includes(`/rooms/${id}/`))) return response({ detail: 'Unavailable' }, 503)
    return response({ agent_id: 'agent-1', participant_id: 'agent-participant-1', machine_id: 'machine-1', machine_name: 'Worker', execution_kind: mocks.executionKind, node_data_dir: mocks.nodeDataDir, can_approve_room: true, can_approve_global: true, read: { supported: !mocks.unsupported, reason: mocks.unsupported ? 'workspace_root_or_audit_capability_missing' : null }, write: { supported: false, reason: 'workspace_write_adapter_unavailable' }, workspaces: mocks.catalog })
  }
  if (mocks.roomIds.some(id => path === `/api/v1/rooms/${id}`)) return response({ participants: [
    { id: 'agent-participant-1', agent_id: 'agent-1', role: 'member' },
    { id: 'user-participant-1', user_id: 'user-1', role: mocks.userRole },
  ] })
  if (path.endsWith('/workspace-attachments') && method === 'GET') return response(mocks.attachments)
  if (method === 'GET') throw new Error(`Unexpected GET ${path}`)
  return response({ id: 'attachment-1', ...body }, 201)
}) }))

import WorkspacePanel from './WorkspacePanel'

const attachment = {
  id: 'attachment-1', machine_id: 'machine-1', agent_id: 'agent-1', room_id: 'room-1', workspace_id: 'ws_project123',
  workspace_label: 'Project source', mode: 'read', state: 'requested', expires_at: '2099-01-01T00:00:00Z',
  room_approved_by_user_id: null, global_approved_by_user_id: null,
}
beforeEach(() => {
  mocks.unsupported = false; mocks.optionsFailures = []; mocks.roomIds = ['room-1']; mocks.executionKind = 'remote'; mocks.nodeDataDir = null
  mocks.calls.length = 0; mocks.viewer = { id: 'user-1', is_admin: true }; mocks.userRole = 'owner'; mocks.attachments = []; mocks.failure = ''
  mocks.catalog = [{ workspace_id: 'ws_project123', label: 'Project source', max_mode: 'read', expires_at: '2099-01-01T00:00:00Z' }]
})
afterEach(() => { cleanup(); vi.clearAllMocks() })
function show() { return render(<MemoryRouter><WorkspacePanel agentId="agent-1" onNavigateAway={vi.fn()} /></MemoryRouter>) }

it('requests only a catalog workspace with the real room participant and safe defaults', async () => {
  show()
  fireEvent.click(await screen.findByRole('button', { name: 'Connect external workspace' }))
  expect(screen.getByLabelText('Registered workspace')).toHaveValue('ws_project123')
  expect(screen.queryByRole('option', { name: 'Read and write' })).not.toBeInTheDocument()
  expect(screen.getByLabelText('Connection duration')).toHaveValue('3600')
  fireEvent.click(screen.getByRole('button', { name: 'Request connection' }))
  await waitFor(() => expect(mocks.calls).toContainEqual({
    path: '/api/v1/rooms/room-1/workspace-attachments', method: 'POST',
    body: { agent_id: 'agent-1', participant_id: 'agent-participant-1', workspace_id: 'ws_project123', mode: 'read', expires_in_seconds: 3600 },
  }))
})

it('does not offer write even when the catalog entry permits writes but the server does not', async () => {
  mocks.catalog.push({ workspace_id: 'ws_writable123', label: 'Writable source', max_mode: 'write', expires_at: '2099-01-01T00:00:00Z' })
  show()
  fireEvent.click(await screen.findByRole('button', { name: 'Connect external workspace' }))
  fireEvent.change(screen.getByLabelText('Registered workspace'), { target: { value: 'ws_writable123' } })
  expect(screen.queryByRole('option', { name: 'Read and write' })).not.toBeInTheDocument()
  fireEvent.change(screen.getByLabelText('Registered workspace'), { target: { value: 'ws_project123' } })
  expect(screen.getByLabelText('Access')).toHaveValue('read')
  expect(screen.queryByRole('option', { name: 'Read and write' })).not.toBeInTheDocument()
})

it('keeps global administrator approval separate from membership-based room approval', async () => {
  mocks.userRole = 'member'; mocks.attachments = [{ ...attachment }]
  show()
  expect(await screen.findByRole('button', { name: 'Approve as system administrator' })).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Approve as room administrator' })).not.toBeInTheDocument()
  expect(screen.queryByLabelText('Local consent proof')).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Approve as system administrator' }))
  await waitFor(() => expect(mocks.calls.some(call => call.method === 'POST' && call.path.endsWith('/approve-global'))).toBe(true))
})

it('requires both approvals and a well-formed one-time proof before local verification', async () => {
  mocks.attachments = [{ ...attachment, room_approved_by_user_id: 'room-owner', global_approved_by_user_id: 'admin' }]
  show()
  const proofInput = await screen.findByLabelText('Local consent proof')
  expect(proofInput).toHaveAttribute('type', 'password')
  const verify = screen.getByRole('button', { name: 'Verify local consent' })
  expect(verify).toBeDisabled()
  fireEvent.change(proofInput, { target: { value: 'not-a-proof' } })
  expect(verify).toBeDisabled()
  const proof = `wcp_${'a'.repeat(64)}`
  fireEvent.change(proofInput, { target: { value: proof } })
  fireEvent.click(verify)
  await waitFor(() => expect(mocks.calls).toContainEqual({ path: '/api/v1/rooms/room-1/workspace-attachments/attachment-1/verify', method: 'POST', body: { consent_proof: proof } }))
  expect(await screen.findByLabelText('Local consent proof')).toHaveValue('')
  expect(screen.getByText(/Local verification requested/)).toBeInTheDocument()
})

it('keeps a rejected workspace request visible and does not invent an active connection', async () => {
  mocks.failure = 'Workspace registration expired'
  show()
  fireEvent.click(await screen.findByRole('button', { name: 'Connect external workspace' }))
  fireEvent.click(screen.getByRole('button', { name: 'Request connection' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('Workspace registration expired')
  expect(await screen.findByRole('button', { name: 'Request connection' })).toBeInTheDocument()
})

it('revokes through the existing API after confirming the impact', async () => {
  mocks.attachments = [{ ...attachment, state: 'active' }]
  show()
  fireEvent.click(await screen.findByRole('button', { name: 'Revoke connection' }))
  await waitFor(() => expect(mocks.calls).toContainEqual({ path: '/api/v1/rooms/room-1/workspace-attachments/attachment-1', method: 'DELETE', body: undefined }))
  expect(mocks.confirm).toHaveBeenCalledWith(expect.objectContaining({ destructive: true }))
})

it('offers local registration guidance instead of accepting an arbitrary host path', async () => {
  mocks.catalog = []
  show()
  fireEvent.click(await screen.findByRole('button', { name: 'Connect external workspace' }))
  expect(screen.getByText(/No registered workspaces are available/)).toBeInTheDocument()
  expect(screen.getByText('Register a folder on the machine')).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Request connection' })).not.toBeInTheDocument()
  expect(screen.queryByRole('textbox')).not.toBeInTheDocument()
})


it('does not offer requests or verification when the shipped machine lacks supported execution', async () => {
  mocks.unsupported = true
  const view = show()
  expect(await screen.findByText(/This machine does not currently support external folder access/)).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Connect external workspace' })).not.toBeInTheDocument()
  view.unmount()
  mocks.attachments = [{ ...attachment, room_approved_by_user_id: 'owner', global_approved_by_user_id: 'admin' }]
  show()
  expect(await screen.findByText(/This machine does not currently support external folder access/)).toBeInTheDocument()
  expect(screen.queryByLabelText('Local consent proof')).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Revoke connection' })).toBeEnabled()
})

it('fails closed for a selected room whose support lookup failed and lets users choose another room', async () => {
  mocks.roomIds = ['room-1', 'room-2']; mocks.optionsFailures = ['room-2']
  show()
  fireEvent.click(await screen.findByRole('button', { name: 'Connect external workspace' }))
  fireEvent.change(screen.getByLabelText('Room'), { target: { value: 'room-2' } })
  expect(screen.getByRole('alert')).toHaveTextContent('Connection support could not be confirmed')
  expect(screen.queryByRole('button', { name: 'Request connection' })).not.toBeInTheDocument()
  fireEvent.change(screen.getByLabelText('Room'), { target: { value: 'room-1' } })
  expect(screen.getByRole('button', { name: 'Request connection' })).toBeEnabled()
  expect(mocks.calls.every(call => call.method === 'GET')).toBe(true)
})

it('uses the actual integrated node data directory in local consent commands', async () => {
  mocks.executionKind = 'integrated'; mocks.nodeDataDir = '/srv/custom node'
  mocks.attachments = [{ ...attachment, room_approved_by_user_id: 'owner', global_approved_by_user_id: 'admin' }]
  show()
  await screen.findByLabelText('Local consent proof')
  expect(screen.getByText(/anygarden-machine workspace --node-data-dir/)).toHaveTextContent("--node-data-dir '/srv/custom node' consent")
  expect(screen.queryByText(/--node-data-dir ~\/\.anygarden/)).not.toBeInTheDocument()
})
