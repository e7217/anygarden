// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, renderHook } from '@testing-library/react'
import { AGENT_ALERTS_POLL_MS, useAgentAlerts } from './useAgentAlerts'

const mocks = vi.hoisted(() => ({ apiFetch: vi.fn() }))
vi.mock('@/lib/api', () => ({ apiFetch: mocks.apiFetch }))

const row = { agent_id: 'a', failed_tasks: [], failed_tasks_count: 0, failed_rooms: ['r'], waiting: [], waiting_count: 0, unread_rooms: [], queued_tasks: 0 }
const reply = (agents: unknown[]) => new Response(JSON.stringify({ agents }), { status: 200 })

async function flush() {
  await act(async () => { for (let i = 0; i < 5; i++) await Promise.resolve() })
}

beforeEach(() => {
  vi.useFakeTimers()
  mocks.apiFetch.mockReset()
  Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'visible' })
})
afterEach(() => { cleanup(); vi.useRealTimers() })

describe('useAgentAlerts', () => {
  it('polls the alerts and drops the ones that went away [COL-20]', async () => {
    mocks.apiFetch.mockResolvedValueOnce(reply([row]))
    const { result } = renderHook(() => useAgentAlerts(true))
    await flush()
    expect(result.current.get('a')).toEqual(row)

    mocks.apiFetch.mockResolvedValue(reply([]))
    await act(async () => { vi.advanceTimersByTime(AGENT_ALERTS_POLL_MS) })
    await flush()
    expect(result.current.size).toBe(0)
  })

  it('does not call the endpoint when disabled [COL-20]', async () => {
    renderHook(() => useAgentAlerts(false))
    await flush()
    expect(mocks.apiFetch).not.toHaveBeenCalled()
  })
})
