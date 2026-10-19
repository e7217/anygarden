// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, renderHook } from '@testing-library/react'
import { ACTIVE_TURNS_INVALIDATE_EVENT, ACTIVE_TURNS_POLL_MS, useActiveTurns } from './useActiveTurns'

const mocks = vi.hoisted(() => ({ apiFetch: vi.fn() }))
vi.mock('@/lib/api', () => ({ apiFetch: mocks.apiFetch }))

function active(rows: { room_id: string; agent_ids: string[] }[]): Response {
  return new Response(JSON.stringify(rows), {
    status: 200, headers: { 'Content-Type': 'application/json' },
  })
}

async function flush() {
  await act(async () => { for (let i = 0; i < 5; i++) await Promise.resolve() })
}

function setVisibility(state: 'visible' | 'hidden') {
  Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => state })
}

beforeEach(() => {
  vi.useFakeTimers()
  mocks.apiFetch.mockReset()
  setVisibility('visible')
})
afterEach(() => { cleanup(); vi.useRealTimers() })

describe('useActiveTurns', () => {
  it('exposes the rooms and agents that are answering [MSG-09]', async () => {
    mocks.apiFetch.mockResolvedValue(active([
      { room_id: 'r1', agent_ids: ['a1'] },
      { room_id: 'r2', agent_ids: ['a1', 'a2'] },
    ]))
    const { result } = renderHook(() => useActiveTurns(true))
    await flush()
    expect(mocks.apiFetch).toHaveBeenCalledWith('/api/v1/turns/active', expect.anything())
    expect([...result.current.roomIds]).toEqual(['r1', 'r2'])
    expect([...result.current.agentIds].sort()).toEqual(['a1', 'a2'])
  })

  it('polls while visible and clears a finished answer [MSG-09]', async () => {
    mocks.apiFetch.mockResolvedValueOnce(active([{ room_id: 'r1', agent_ids: ['a1'] }]))
    const { result } = renderHook(() => useActiveTurns(true))
    await flush()
    expect(result.current.roomIds.has('r1')).toBe(true)

    mocks.apiFetch.mockResolvedValue(active([]))
    await act(async () => { vi.advanceTimersByTime(ACTIVE_TURNS_POLL_MS) })
    await flush()
    expect(result.current.roomIds.size).toBe(0)
    expect(result.current.agentIds.size).toBe(0)
  })

  it('skips polling while the tab is hidden [MSG-09]', async () => {
    mocks.apiFetch.mockResolvedValue(active([]))
    renderHook(() => useActiveTurns(true))
    await flush()
    const calls = mocks.apiFetch.mock.calls.length
    setVisibility('hidden')
    await act(async () => { vi.advanceTimersByTime(ACTIVE_TURNS_POLL_MS * 3) })
    await flush()
    expect(mocks.apiFetch.mock.calls.length).toBe(calls)
  })

  it('refetches immediately on the invalidate event [MSG-09]', async () => {
    mocks.apiFetch.mockResolvedValueOnce(active([]))
    const { result } = renderHook(() => useActiveTurns(true))
    await flush()
    mocks.apiFetch.mockResolvedValue(active([{ room_id: 'r9', agent_ids: ['a9'] }]))
    await act(async () => { window.dispatchEvent(new Event(ACTIVE_TURNS_INVALIDATE_EVENT)) })
    await flush()
    expect(result.current.roomIds.has('r9')).toBe(true)
  })

  it('does not fetch when disabled [MSG-09]', async () => {
    renderHook(() => useActiveTurns(false))
    await act(async () => { vi.advanceTimersByTime(ACTIVE_TURNS_POLL_MS * 2) })
    await flush()
    expect(mocks.apiFetch).not.toHaveBeenCalled()
  })

  it('keeps the last state when a poll fails [MSG-09]', async () => {
    mocks.apiFetch.mockResolvedValueOnce(active([{ room_id: 'r1', agent_ids: ['a1'] }]))
    const { result } = renderHook(() => useActiveTurns(true))
    await flush()
    mocks.apiFetch.mockRejectedValue(new Error('offline'))
    await act(async () => { vi.advanceTimersByTime(ACTIVE_TURNS_POLL_MS) })
    await flush()
    expect(result.current.roomIds.has('r1')).toBe(true)
  })
})
