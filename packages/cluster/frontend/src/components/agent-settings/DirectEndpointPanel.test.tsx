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
})
