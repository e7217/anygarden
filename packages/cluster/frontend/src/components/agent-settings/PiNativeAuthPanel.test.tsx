// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'

const mocks = vi.hoisted(() => ({ apiFetch: vi.fn() }))
vi.mock('@/lib/api', () => ({ apiFetch: mocks.apiFetch }))
import PiNativeAuthPanel from './PiNativeAuthPanel'

afterEach(() => { cleanup(); mocks.apiFetch.mockReset() })

it('stores a native Pi key without displaying it again', async () => {
  mocks.apiFetch.mockImplementation((_path: string, options?: { method?: string; body?: string }) => {
    if (options?.method === 'PUT') {
      expect(options.body).toBe(JSON.stringify({ value: 'private-test-key' }))
      return Promise.resolve({ ok: true, json: async () => ({ configured: true, provider: 'zai', revision: 1 }) })
    }
    return Promise.resolve({ ok: true, json: async () => ({ configured: false, provider: null, revision: null }) })
  })
  const saved = vi.fn().mockResolvedValue(undefined)
  render(<PiNativeAuthPanel agentId="agent-1" provider="zai" onSaved={saved} />)
  expect(await screen.findByText('No agent-specific Pi API key is stored.')).toBeInTheDocument()
  fireEvent.change(screen.getByLabelText('Pi provider API key'), { target: { value: 'private-test-key' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save key' }))
  expect(await screen.findByText('zai API key stored (revision 1)')).toBeInTheDocument()
  expect(screen.getByLabelText('Pi provider API key')).toHaveValue('')
  expect(document.body.textContent).not.toContain('private-test-key')
  await waitFor(() => expect(saved).toHaveBeenCalledOnce())
})
