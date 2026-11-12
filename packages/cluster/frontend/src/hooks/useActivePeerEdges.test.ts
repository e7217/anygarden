// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, renderHook } from '@testing-library/react'
import { ACTIVE_TURNS_POLL_MS } from './useActiveTurns'
import { useActivePeerEdges } from './useActivePeerEdges'

const mocks = vi.hoisted(() => ({ apiFetch: vi.fn() }))
vi.mock('@/lib/api', () => ({ apiFetch: mocks.apiFetch }))

const edge = { chain_id: 'c', room_id: 'r', from_agent_id: 'a', to_agent_id: 'b' }
const reply = (edges: unknown[]) => new Response(JSON.stringify({ edges }), { status: 200 })

async function flush() {
  await act(async () => { for (let i = 0; i < 5; i++) await Promise.resolve() })
}

beforeEach(() => {
  vi.useFakeTimers()
  mocks.apiFetch.mockReset()
  Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'visible' })
})
afterEach(() => { cleanup(); vi.useRealTimers() })

describe('useActivePeerEdges', () => {
  it('polls the running questions and clears finished ones [COL-19]', async () => {
    mocks.apiFetch.mockResolvedValueOnce(reply([edge]))
    const { result } = renderHook(() => useActivePeerEdges(true))
    await flush()
    expect(result.current).toEqual([edge])

    mocks.apiFetch.mockResolvedValue(reply([]))
    await act(async () => { vi.advanceTimersByTime(ACTIVE_TURNS_POLL_MS) })
    await flush()
    expect(result.current).toEqual([])
  })

  it('does not call the endpoint when disabled [COL-19]', async () => {
    renderHook(() => useActivePeerEdges(false))
    await flush()
    expect(mocks.apiFetch).not.toHaveBeenCalled()
  })
})
