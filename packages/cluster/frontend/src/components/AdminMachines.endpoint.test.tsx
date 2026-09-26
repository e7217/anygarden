// @vitest-environment jsdom
// #685 — direct endpoint + model discovery in the Create Agent dialog.
import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'

const mocks = vi.hoisted(() => ({
  createAgent: vi.fn().mockResolvedValue({ id: 'new-agent' }),
  fetchAgentDMs: vi.fn(),
  fetchEngineCatalog: vi.fn().mockResolvedValue({
    engine: 'pi-cli', default_model: '', reasoning_levels: [],
    models: [{ id: 'glm-5.3-flash', label: 'GLM 5.3 Flash (zai)', reasoning_levels: [] }],
  }),
  machines: [{ id: 'm1', name: 'Test machine', hostname: 'test', status: 'online' }],
  agents: [], availableEngines: [{ engine: 'pi-cli', machine_count: 1 }, { engine: 'codex-cli', machine_count: 1 }],
  calls: [] as Array<{ path: string; method: string; body: unknown }>,
}))
vi.mock('@/hooks/useMachines', () => ({ useMachines: () => ({ machines: mocks.machines }) }))
vi.mock('@/hooks/useAgents', () => ({ useAgents: () => ({
  createAgent: mocks.createAgent, fetchEngineCatalog: mocks.fetchEngineCatalog,
  agents: mocks.agents, availableEngines: mocks.availableEngines, pendingIds: new Set(),
}) }))
vi.mock('@/hooks/useRooms', () => ({ useRooms: () => ({ projects: [], rooms: {}, fetchAgentDMs: mocks.fetchAgentDMs }) }))
vi.mock('@/lib/api', () => ({ apiFetch: vi.fn(async (path: string, init?: RequestInit) => {
  const method = init?.method ?? 'GET'
  const body = init?.body ? JSON.parse(String(init.body)) : undefined
  mocks.calls.push({ path, method, body })
  const json = (value: unknown, status = 200) => ({ ok: status < 400, status, json: async () => value })
  if (path === '/api/v1/engine-endpoints/models') {
    return json({ models: [{ id: 'qwen3.8-27b-fp8', max_model_len: 32768 }, { id: 'llama-local', max_model_len: null }], reachable_from: 'server' })
  }
  if (path.endsWith('/endpoint/credentials') && method === 'POST') return json({ id: 'cred-1' }, 201)
  if (path.endsWith('/endpoint') && method === 'PUT') return json(body)
  return json(path.endsWith('/engines') ? [{ engine: 'pi-cli', version: '0.85.1' }, { engine: 'codex-cli', version: '0.1.0' }] : [])
}) }))
vi.mock('@/components/AgentSettingsDialog', () => ({ default: () => null }))
vi.mock('@/components/AgentSettingsMenu', () => ({ default: () => null }))
vi.mock('@/components/EntityAvatar', () => ({ EntityAvatar: () => null }))

import AdminMachines from './AdminMachines'
afterEach(() => { cleanup(); vi.clearAllMocks(); mocks.calls.length = 0 })

async function openPiDialog() {
  render(<AdminMachines />)
  fireEvent.click(await screen.findByRole('button', { name: 'New Agent' }))
  await waitFor(() => expect(document.querySelector('option[value="pi-cli"]')).not.toBeNull())
  fireEvent.change(screen.getByLabelText('Engine'), { target: { value: 'pi-cli' } })
  await screen.findByLabelText('Provider (required)')
  fireEvent.change(screen.getByPlaceholderText('Agent name'), { target: { value: 'Local qwen' } })
  fireEvent.change(screen.getByLabelText('Provider (required)'), { target: { value: 'qwen-llm' } })
  fireEvent.change(screen.getByLabelText('Connection type'), { target: { value: 'direct' } })
  fireEvent.change(screen.getByLabelText('Provider (required)'), { target: { value: 'qwen-llm' } })
}

it('creates a keyless Pi agent with its endpoint in one request after loading models', async () => {
  await openPiDialog()
  const submit = screen.getByRole('button', { name: 'Create Agent' })
  fireEvent.change(screen.getByLabelText('Base URL'), { target: { value: 'http://10.0.0.5:8000/v1' } })
  expect(screen.getByLabelText('API protocol')).toHaveValue('chat-completions')
  expect(submit).toBeDisabled() // model is required for a direct endpoint
  fireEvent.click(screen.getByRole('button', { name: 'Load models' }))
  expect(await screen.findByText(/2 models found · reachable from the AnyGarden server/)).toBeInTheDocument()
  expect(mocks.calls.find(c => c.path === '/api/v1/engine-endpoints/models')?.body).toEqual({ base_url: 'http://10.0.0.5:8000/v1' })
  expect(document.querySelector('#pi-models option[value="qwen3.8-27b-fp8"]')).not.toBeNull()
  fireEvent.change(screen.getByLabelText('Model (required)'), { target: { value: 'qwen3.8-27b-fp8' } })
  expect(submit).toBeEnabled()
  fireEvent.click(submit)
  await waitFor(() => expect(mocks.createAgent).toHaveBeenCalledWith({
    name: 'Local qwen', engine: 'pi-cli', provider: 'qwen-llm', model: 'qwen3.8-27b-fp8', rooms: [],
    endpoint: { base_url: 'http://10.0.0.5:8000/v1', api_protocol: 'chat-completions' },
  }))
  expect(mocks.calls.some(c => c.path.includes('/endpoint/credentials'))).toBe(false)
})

it('stores a new API key and binds it after creation', async () => {
  await openPiDialog()
  fireEvent.change(screen.getByLabelText('Base URL'), { target: { value: 'http://10.0.0.5:8000/v1' } })
  fireEvent.change(screen.getByLabelText('Authentication'), { target: { value: 'key' } })
  const key = screen.getByLabelText('API key')
  expect(key).toHaveAttribute('type', 'password')
  fireEvent.change(key, { target: { value: 'sk-local-123' } })
  fireEvent.click(screen.getByRole('button', { name: 'Load models' }))
  await screen.findByText(/2 models found/)
  expect(mocks.calls.find(c => c.path === '/api/v1/engine-endpoints/models')?.body).toEqual({ base_url: 'http://10.0.0.5:8000/v1', api_key: 'sk-local-123' })
  fireEvent.change(screen.getByLabelText('Model (required)'), { target: { value: 'llama-local' } })
  fireEvent.click(screen.getByRole('button', { name: 'Create Agent' }))
  await waitFor(() => expect(mocks.calls.some(c => c.method === 'PUT')).toBe(true))
  const createCall = mocks.createAgent.mock.calls[0][0]
  expect(JSON.stringify(createCall)).not.toContain('sk-local-123')
  expect(mocks.calls.find(c => c.method === 'POST' && c.path === '/api/v1/agents/new-agent/endpoint/credentials')?.body)
    .toEqual({ value: 'sk-local-123', label: 'Endpoint API key' })
  expect(mocks.calls.find(c => c.method === 'PUT')).toEqual({
    path: '/api/v1/agents/new-agent/endpoint', method: 'PUT',
    body: { provider: 'qwen-llm', model: 'llama-local', base_url: 'http://10.0.0.5:8000/v1', api_protocol: 'chat-completions', credential_ref: 'cred-1' },
  })
})

it('blocks creation for an invalid base URL', async () => {
  await openPiDialog()
  fireEvent.change(screen.getByLabelText('Base URL'), { target: { value: 'http://user:pw@10.0.0.5/v1' } })
  fireEvent.change(screen.getByLabelText('Model (required)'), { target: { value: 'm' } })
  expect(screen.getByRole('button', { name: 'Create Agent' })).toBeDisabled()
  expect(screen.getByRole('button', { name: 'Load models' })).toBeDisabled()
  expect(screen.getByText(/HTTP\(S\) URL without credentials/)).toBeInTheDocument()
})

it('keeps direct server fields out of the Codex creation path', async () => {
  render(<AdminMachines />)
  fireEvent.click(await screen.findByRole('button', { name: 'New Agent' }))
  fireEvent.change(screen.getByLabelText('Engine'), { target: { value: 'codex-cli' } })
  expect(screen.queryByLabelText('Connection type')).toBeNull()
  expect(screen.queryByLabelText('Base URL')).toBeNull()
  expect(screen.queryByLabelText('Provider (required)')).toBeNull()
  fireEvent.change(screen.getByPlaceholderText('Agent name'), { target: { value: 'Codex worker' } })
  fireEvent.click(screen.getByRole('button', { name: 'Create Agent' }))
  await waitFor(() => expect(mocks.createAgent).toHaveBeenCalled())
  expect(mocks.createAgent.mock.calls[0][0]).not.toHaveProperty('endpoint')
})

it('drops hidden Pi direct values when switching back to native', async () => {
  await openPiDialog()
  fireEvent.change(screen.getByLabelText('Base URL'), { target: { value: 'http://10.0.0.5:8000/v1' } })
  fireEvent.change(screen.getByLabelText('Model (required)'), { target: { value: 'old-direct-model' } })
  fireEvent.change(screen.getByLabelText('Connection type'), { target: { value: 'native' } })
  expect(screen.queryByLabelText('Base URL')).toBeNull()
  fireEvent.change(screen.getByLabelText('Provider (required)'), { target: { value: 'zai' } })
  fireEvent.change(screen.getByLabelText('Model (required)'), { target: { value: 'glm-5.3-flash' } })
  fireEvent.click(screen.getByRole('button', { name: 'Create Agent' }))
  await waitFor(() => expect(mocks.createAgent).toHaveBeenCalled())
  expect(mocks.createAgent.mock.calls[0][0]).toEqual({
    name: 'Local qwen', engine: 'pi-cli', provider: 'zai', model: 'glm-5.3-flash', rooms: [],
  })
})
