// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import type { Agent } from '@/hooks/useAgents'

const mocks = vi.hoisted(() => ({
  endpoint: { provider: 'local', model: 'local-model', base_url: 'http://local/v1', api_protocol: 'chat-completions', credential_ref: null } as {
    provider: string | null; model: string | null; base_url: string | null; api_protocol: string | null; credential_ref: string | null
  },
  failLoad: false,
  failKey: false,
  switchPiToNative: vi.fn(),
  applyEndpoint: vi.fn(),
  apiFetch: vi.fn(),
}))
vi.mock('@/lib/engineEndpoints', () => ({
  getEndpoint: vi.fn(async () => {
    if (mocks.failLoad) throw new Error('load failed')
    return { ...mocks.endpoint }
  }),
  switchPiToNative: mocks.switchPiToNative,
  applyEndpoint: mocks.applyEndpoint,
}))
vi.mock('@/lib/api', () => ({ apiFetch: mocks.apiFetch }))
vi.mock('./DirectEndpointPanel', () => ({ default: () => <div aria-label="Direct model connection">Direct editor</div> }))
vi.mock('./PiNativeAuthPanel', () => ({ default: () => <div aria-label="Pi provider authentication">Pi key editor</div> }))

import ModelConnectionPanel from './ModelConnectionPanel'

const pi: Agent = { id: 'a1', name: 'Pi', engine: 'pi-cli', provider: 'local', model: 'local-model', desired_state: 'running', actual_state: 'running', restart_policy: 'always' }
const codex: Agent = { ...pi, engine: 'codex-cli', provider: null, model: 'gpt-5.3-codex' }
const catalog = { engine: 'codex-cli', default_model: 'gpt-5.3-codex', models: [{ id: 'gpt-5.3-codex', label: 'GPT 5.3 Codex', reasoning_levels: ['low', 'high'] }], reasoning_levels: ['low', 'high'] }
const updateAgent = vi.fn(async (_id: string, _patch: object) => pi)
const onConnectionChange = vi.fn()

function setup(agent: Agent) {
  render(<ModelConnectionPanel agent={agent} updateAgent={updateAgent} fetchEngineCatalog={vi.fn().mockResolvedValue(catalog)} onConnectionChange={onConnectionChange} />)
}

beforeEach(() => {
  mocks.endpoint = { provider: 'local', model: 'local-model', base_url: 'http://local/v1', api_protocol: 'chat-completions', credential_ref: null }
  mocks.failLoad = false; mocks.failKey = false
  mocks.apiFetch.mockImplementation(async (path: string, options?: RequestInit) => {
    if (path.endsWith('/pi-auth') && options?.method === 'PUT') return { ok: !mocks.failKey, json: async () => ({}) }
    return { ok: true, json: async () => ({ provider: null, configured: false }) }
  })
  mocks.switchPiToNative.mockImplementation(async (_id: string, provider: string, model: string) => {
    mocks.endpoint = { provider, model, base_url: null, api_protocol: null, credential_ref: null }
    return { ...mocks.endpoint }
  })
  mocks.applyEndpoint.mockImplementation(async (_id: string, config: typeof mocks.endpoint) => {
    mocks.endpoint = { ...config }
  })
})
afterEach(() => { cleanup(); vi.clearAllMocks() })

it('shows only the Codex model controls for a default Codex connection', async () => {
  mocks.endpoint = { provider: null, model: 'gpt-5.3-codex', base_url: null, api_protocol: null, credential_ref: null }
  setup(codex)
  expect(await screen.findByLabelText('Agent model')).toBeInTheDocument()
  expect(screen.queryByLabelText('Direct model connection')).toBeNull()
  fireEvent.change(screen.getByLabelText('Reasoning effort'), { target: { value: 'high' } })
  await waitFor(() => expect(updateAgent).toHaveBeenCalledWith('a1', { reasoning_effort: 'high', reasoning_effort_set: true }))
})

it('keeps an existing Codex direct connection manageable', async () => {
  setup(codex)
  expect(await screen.findByLabelText('Direct model connection')).toBeInTheDocument()
  expect(screen.queryByLabelText('Agent model')).toBeNull()
})

it('shows Pi native fields and authentication only in native mode', async () => {
  mocks.endpoint = { provider: 'zai', model: 'glm-5.3-flash', base_url: null, api_protocol: null, credential_ref: null }
  setup({ ...pi, provider: 'zai', model: 'glm-5.3-flash' })
  expect(await screen.findByLabelText('Pi provider authentication')).toBeInTheDocument()
  expect(screen.queryByLabelText('Direct model connection')).toBeNull()
  expect(screen.getByLabelText('Agent provider')).toHaveValue('zai')
})

it('switches Pi direct to native and saves the key through the write-only API', async () => {
  setup(pi)
  fireEvent.change(await screen.findByLabelText('Connection type'), { target: { value: 'native' } })
  fireEvent.change(screen.getByLabelText('Agent provider'), { target: { value: 'zai' } })
  fireEvent.change(screen.getByLabelText('Agent model'), { target: { value: 'glm-5.3-flash' } })
  fireEvent.change(screen.getByLabelText('Native provider API key'), { target: { value: 'secret-value' } })
  fireEvent.click(screen.getByRole('button', { name: 'Switch to Pi provider' }))
  await waitFor(() => expect(mocks.switchPiToNative).toHaveBeenCalledWith('a1', 'zai', 'glm-5.3-flash'))
  expect(mocks.apiFetch).toHaveBeenCalledWith('/api/v1/agents/a1/pi-auth', { method: 'PUT', body: JSON.stringify({ value: 'secret-value' }) })
  expect(JSON.stringify(mocks.switchPiToNative.mock.calls)).not.toContain('secret-value')
  expect(await screen.findByLabelText('Pi provider authentication')).toBeInTheDocument()
})

it('restores the prior direct connection when native key storage fails', async () => {
  mocks.failKey = true
  setup(pi)
  fireEvent.change(await screen.findByLabelText('Connection type'), { target: { value: 'native' } })
  fireEvent.change(screen.getByLabelText('Agent provider'), { target: { value: 'zai' } })
  fireEvent.change(screen.getByLabelText('Agent model'), { target: { value: 'glm-5.3-flash' } })
  fireEvent.change(screen.getByLabelText('Native provider API key'), { target: { value: 'secret-value' } })
  fireEvent.click(screen.getByRole('button', { name: 'Switch to Pi provider' }))
  await waitFor(() => expect(mocks.applyEndpoint).toHaveBeenCalledWith('a1', {
    provider: 'local', model: 'local-model', base_url: 'http://local/v1', api_protocol: 'chat-completions', credential_ref: null,
  }))
  expect(await screen.findByRole('alert')).toHaveTextContent('previous direct connection was restored')
})

it('does not treat an endpoint lookup failure as a native connection', async () => {
  mocks.failLoad = true
  setup(pi)
  expect(await screen.findByRole('alert')).toHaveTextContent('Unable to load model connection')
  expect(screen.queryByLabelText('Pi provider authentication')).toBeNull()
  expect(onConnectionChange).toHaveBeenCalledWith({ agentId: 'a1', status: 'error' })
})
