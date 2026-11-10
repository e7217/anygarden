// @vitest-environment jsdom
import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { apiFetch } from '@/lib/api'
import { useAgentLinks } from './useAgentLinks'
vi.mock('@/lib/api', () => ({ apiFetch: vi.fn() }))
afterEach(() => vi.resetAllMocks())
it('[COL-14] coalesces repeated drops into one creation request', async () => {
  let finish!: (response: Response) => void
  vi.mocked(apiFetch).mockImplementation(async (_path, init) => init?.method === 'POST'
    ? new Promise(resolve => { finish = resolve })
    : new Response(JSON.stringify({ agents: [], links: [] }), { status: 200 }))
  const { result } = renderHook(useAgentLinks)
  await waitFor(() => expect(result.current.graph).not.toBeNull())
  const input = { from_agent_id: 'a', to_agent_id: 'b' }
  const first = result.current.create(input)
  const second = result.current.create(input)
  expect(first).toBe(second)
  await act(async () => {
    finish(new Response(JSON.stringify({ id: 'l', ...input }), { status: 201 }))
    await first
  })
  expect(vi.mocked(apiFetch).mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(1)
  expect(result.current.graph?.links.map(l => l.id)).toEqual(['l'])
})
