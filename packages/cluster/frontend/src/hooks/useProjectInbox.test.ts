// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, renderHook } from '@testing-library/react'
import { useProjectInbox, type InboxItem } from './useProjectInbox'

const mocks = vi.hoisted(() => ({ apiFetch: vi.fn() }))
vi.mock('@/lib/api', () => ({ apiFetch: mocks.apiFetch }))

function snapshot(items: Partial<InboxItem>[]): Response {
  return new Response(JSON.stringify({ items, projects: [] }), {
    status: 200, headers: { 'Content-Type': 'application/json' },
  })
}

async function flush() {
  await act(async () => { for (let i = 0; i < 5; i++) await Promise.resolve() })
}

beforeEach(() => {
  vi.useFakeTimers()
  mocks.apiFetch.mockReset()
})
afterEach(() => { cleanup(); vi.useRealTimers() })

describe('useProjectInbox live updates', () => {
  it('shows a new item within 15 seconds without a manual refresh [INB-06]', async () => {
    mocks.apiFetch.mockResolvedValueOnce(snapshot([]))
    const { result } = renderHook(() => useProjectInbox('user'))
    await flush()
    expect(result.current.items).toEqual([])

    mocks.apiFetch.mockResolvedValue(snapshot([{ id: 'question:q', type: 'question' }]))
    await act(async () => { vi.advanceTimersByTime(14_999) })
    await flush()
    expect(result.current.items).toEqual([])
    await act(async () => { vi.advanceTimersByTime(1) })
    await flush()
    expect(result.current.items.map(item => item.id)).toEqual(['question:q'])
  })

  it('refreshes immediately when a task update event arrives [INB-06]', async () => {
    mocks.apiFetch.mockResolvedValueOnce(snapshot([]))
    const { result } = renderHook(() => useProjectInbox('user'))
    await flush()
    mocks.apiFetch.mockResolvedValue(snapshot([{ id: 'task:t', type: 'task' }]))
    await act(async () => { window.dispatchEvent(new Event('anygarden:task:updated')) })
    await flush()
    expect(result.current.items.map(item => item.id)).toEqual(['task:t'])
  })
})
