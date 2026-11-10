// @vitest-environment jsdom
import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { apiFetch } from '@/lib/api'
import { useAgentConversations } from './useAgentConversations'
vi.mock('@/lib/api', () => ({ apiFetch: vi.fn() }))
afterEach(() => vi.resetAllMocks())
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status })
it('ignores a previous agent response even if it completes after a new selection', async () => {
  let finish!: (value: Response) => void
  vi.mocked(apiFetch).mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
    .mockResolvedValueOnce(json([{ id: 'b-room', name: 'B conversation' }]))
  const { result, rerender } = renderHook(({ id }) => useAgentConversations(id, 'u'), { initialProps: { id: 'a' } })
  rerender({ id: 'b' })
  await waitFor(() => expect(result.current.rooms[0]?.id).toBe('b-room'))
  await act(async () => { finish(json([{ id: 'a-room', name: 'A conversation' }])) })
  expect(result.current.rooms.map(r => r.id)).toEqual(['b-room'])
})
it('distinguishes failed requests from empty history and permits retry', async () => {
  vi.mocked(apiFetch).mockResolvedValueOnce(json({}, 503)).mockResolvedValueOnce(json([]))
  const { result } = renderHook(() => useAgentConversations('a', 'u'))
  await waitFor(() => expect(result.current.errorStatus).toBe(503))
  await act(async () => { await result.current.refresh() })
  expect(result.current.errorStatus).toBeNull()
  expect(result.current.rooms).toEqual([])
  expect(result.current.loading).toBe(false)
})
