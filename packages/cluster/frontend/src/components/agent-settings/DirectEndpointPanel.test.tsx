// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import { apiFetch } from '@/lib/api'
import DirectEndpointPanel from './DirectEndpointPanel'
vi.mock('@/lib/api', () => ({ apiFetch: vi.fn() }))
afterEach(() => { cleanup(); vi.resetAllMocks() })
const config = { provider: 'local', model: 'm', base_url: 'http://localhost:8000/v1', api_protocol: 'responses', credential_ref: null }
function setup() {
  const calls: Array<{url: string; method: string; body: unknown}> = []
  let credentials: Array<{id: string; label: string; revision: number}> = []
  vi.mocked(apiFetch).mockImplementation(async (url, init) => {
    const method = init?.method ?? 'GET'
    const body = init?.body ? JSON.parse(String(init.body)) : undefined
    calls.push({ url, method, body })
    if (method === 'POST') {
      credentials = [{ id: 'ref', label: body.label, revision: 1 }]
      return new Response(JSON.stringify(credentials[0]), { status: 201 })
    }
    if (method === 'PUT') return new Response(JSON.stringify({ ...config, ...body }))
    return new Response(JSON.stringify(url.endsWith('/credentials') ? credentials : config))
  })
  const saved = vi.fn().mockResolvedValue(undefined)
  render(<DirectEndpointPanel agentId="a" engine="codex-cli" onSaved={saved} />)
  return { calls, saved }
}
describe('direct endpoint editor', () => {
  it('saves model and connection fields atomically and excludes unsupported protocol', async () => {
    const { calls, saved } = setup()
    fireEvent.change(await screen.findByLabelText('Endpoint model'), { target: { value: 'custom-model' } })
    expect(calls.filter(c => c.method === 'PUT')).toHaveLength(0)
    expect(screen.queryByRole('option', { name: 'Chat Completions' })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Apply connection' }))
    await waitFor(() => expect(saved).toHaveBeenCalledOnce())
    expect(calls.find(c => c.method === 'PUT')?.body).toEqual({ ...config, model: 'custom-model' })
  })
  it('clears entered keys after write and displays only stored references', async () => {
    const { calls } = setup()
    const key = await screen.findByLabelText('New endpoint API key')
    expect(key).toHaveAttribute('type', 'password')
    fireEvent.change(key, { target: { value: 'fake-test-key' } })
    fireEvent.click(screen.getByRole('button', { name: 'Store new credential' }))
    await screen.findByRole('option', { name: /stored \(revision 1\)/ })
    expect(key).toHaveValue('')
    expect(calls.find(c => c.method === 'POST')?.body).toEqual({ label: 'Endpoint credential', value: 'fake-test-key' })
    expect(screen.queryByText('fake-test-key')).not.toBeInTheDocument()
  })
  it('shows server capability errors without claiming a successful save', async () => {
    const { saved } = setup()
    await screen.findByLabelText('Endpoint model')
    vi.mocked(apiFetch).mockResolvedValueOnce(new Response(JSON.stringify({detail: 'Connect a compatible machine first'}), {status: 409}))
    fireEvent.click(screen.getByRole('button', { name: 'Apply connection' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Connect a compatible machine first')
    expect(saved).not.toHaveBeenCalled()
  })
  it('resets an existing Codex direct connection to the CLI default', async () => {
    const { calls, saved } = setup()
    fireEvent.click(await screen.findByRole('button', { name: 'Disable direct connection' }))
    await waitFor(() => expect(saved).toHaveBeenCalledOnce())
    expect(calls.find(c => c.method === 'PUT')?.body).toEqual({ base_url: null, model: null })
  })
})

describe('direct endpoint status and model discovery (#685)', () => {
  function mockWith(saved: Record<string, string | null>, models = ['m', 'other-model']) {
    const calls: Array<{url: string; method: string; body: unknown}> = []
    vi.mocked(apiFetch).mockImplementation(async (url, init) => {
      const method = init?.method ?? 'GET'
      const body = init?.body ? JSON.parse(String(init.body)) : undefined
      calls.push({ url, method, body })
      if (url === '/api/v1/engine-endpoints/models') {
        return new Response(JSON.stringify({ models: models.map(id => ({ id, max_model_len: null })), reachable_from: 'server' }))
      }
      return new Response(JSON.stringify(url.endsWith('/credentials') ? [] : saved))
    })
    return calls
  }
  it('is rendered with a status line when not configured', async () => {
    mockWith({ provider: 'local', model: null, base_url: null, api_protocol: null, credential_ref: null })
    render(<DirectEndpointPanel agentId="a" engine="pi-cli" onSaved={vi.fn()} />)
    expect(await screen.findByText('Direct connection: not configured')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Disable direct connection' })).toBeNull()
  })
  it('shows the configured target and warns when the model is not served', async () => {
    const calls = mockWith(config)
    render(<DirectEndpointPanel agentId="a" engine="codex-cli" onSaved={vi.fn()} />)
    expect(await screen.findByText('Direct connection: http://localhost:8000/v1 · Responses')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Load models' }))
    expect(await screen.findByText(/2 models found/)).toBeInTheDocument()
    expect(calls.find(c => c.url === '/api/v1/engine-endpoints/models')?.body).toEqual({ base_url: 'http://localhost:8000/v1' })
    expect(screen.queryByText(/is not in the list served/)).not.toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('Endpoint model'), { target: { value: 'typo-model' } })
    expect(screen.getByText(/"typo-model" is not in the list served by this endpoint/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Apply connection' })).toBeEnabled()
  })
  it('probes with the selected stored credential by reference', async () => {
    const calls = mockWith({ ...config, credential_ref: 'ref-1' })
    render(<DirectEndpointPanel agentId="a" engine="pi-cli" onSaved={vi.fn()} />)
    fireEvent.click(await screen.findByRole('button', { name: 'Load models' }))
    await screen.findByText(/models found/)
    expect(calls.find(c => c.url === '/api/v1/engine-endpoints/models')?.body).toEqual({
      base_url: 'http://localhost:8000/v1', agent_id: 'a', credential_ref: 'ref-1',
    })
  })
})
