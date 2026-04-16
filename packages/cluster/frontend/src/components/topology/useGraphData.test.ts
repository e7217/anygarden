// @vitest-environment jsdom
import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest'
import { renderHook, act, cleanup } from '@testing-library/react'

import { useGraphData } from './useGraphData'

/**
 * Polling lifecycle smoke tests (#84 Codex review follow-up).
 *
 * These assertions codify the contract the topology page relies on:
 * - exactly one fetch on mount (no double-dispatch under strict mode
 *   when React 18 / 19 replays the effect),
 * - interval fires at ``pollInterval`` cadence and is suspended while
 *   ``document.visibilityState === 'hidden'``,
 * - visible-on-return triggers an immediate refresh before the next
 *   interval tick (Blocker 2), and
 * - slow responses don't livelock into a cascade of aborted requests
 *   (Blocker 1 — loadingRef gate).
 *
 * jsdom's ``document.visibilityState`` is a read-only getter backed by
 * a private slot, so we swap the property descriptor rather than
 * assigning directly.
 */

type FetchShape = (
  input: RequestInfo | URL,
  init?: RequestInit,
) => Promise<Response>

type FetchMock = ReturnType<typeof vi.fn> & FetchShape

const okResponse = (etag = 'W/"1"'): Response => {
  return new Response(
    JSON.stringify({
      generated_at: '2026-04-17T00:00:00Z',
      scope: 'personal',
      nodes: [],
      edges: [],
    }),
    {
      status: 200,
      headers: { 'Content-Type': 'application/json', ETag: etag },
    },
  )
}

/**
 * Wait for any pending microtasks in-between timer advances. We don't
 * want to await fetches directly (the hook's internal promise chain is
 * private) — ``act(Promise.resolve())`` flushes every microtask round
 * until React settles, which is what ``renderHook`` needs for state
 * transitions (``setLoading(false)`` finally callback, in particular)
 * to take effect before the next assertion.
 */
const flushMicrotasks = async () => {
  await act(async () => {
    await Promise.resolve()
    await Promise.resolve()
  })
}

const setVisibility = (state: 'visible' | 'hidden') => {
  Object.defineProperty(document, 'visibilityState', {
    configurable: true,
    get: () => state,
  })
}

describe('useGraphData polling lifecycle', () => {
  let fetchMock: FetchMock

  beforeEach(() => {
    vi.useFakeTimers()
    setVisibility('visible')
    localStorage.clear()
    fetchMock = vi.fn(async (): Promise<Response> => okResponse()) as FetchMock
    vi.stubGlobal('fetch', fetchMock)
  })

  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
    vi.useRealTimers()
    setVisibility('visible')
  })

  it('fires exactly one fetch on mount and one more after pollInterval elapses', async () => {
    const { unmount } = renderHook(() => useGraphData('auto', 1000))
    await flushMicrotasks()
    expect(fetchMock).toHaveBeenCalledTimes(1)

    await act(async () => {
      vi.advanceTimersByTime(1000)
    })
    await flushMicrotasks()
    expect(fetchMock).toHaveBeenCalledTimes(2)

    unmount()
  })

  it('does not poll when pollInterval is undefined', async () => {
    const { unmount } = renderHook(() => useGraphData('auto'))
    await flushMicrotasks()
    expect(fetchMock).toHaveBeenCalledTimes(1)

    await act(async () => {
      vi.advanceTimersByTime(10_000)
    })
    await flushMicrotasks()
    // Only the mount fetch — no background polling.
    expect(fetchMock).toHaveBeenCalledTimes(1)

    unmount()
  })

  it('does not poll when pollInterval is <= 0', async () => {
    const { unmount } = renderHook(() => useGraphData('auto', 0))
    await flushMicrotasks()
    expect(fetchMock).toHaveBeenCalledTimes(1)

    await act(async () => {
      vi.advanceTimersByTime(10_000)
    })
    await flushMicrotasks()
    expect(fetchMock).toHaveBeenCalledTimes(1)

    unmount()
  })

  it('skips interval ticks while the tab is hidden', async () => {
    const { unmount } = renderHook(() => useGraphData('auto', 1000))
    await flushMicrotasks()
    expect(fetchMock).toHaveBeenCalledTimes(1)

    // Flip to hidden and advance several intervals — no new fetches.
    setVisibility('hidden')
    document.dispatchEvent(new Event('visibilitychange'))
    await act(async () => {
      vi.advanceTimersByTime(5000)
    })
    await flushMicrotasks()
    expect(fetchMock).toHaveBeenCalledTimes(1)

    unmount()
  })

  it('refreshes immediately on hidden→visible transition (Blocker 2)', async () => {
    const { unmount } = renderHook(() => useGraphData('auto', 1000))
    await flushMicrotasks()
    expect(fetchMock).toHaveBeenCalledTimes(1)

    // Hide the tab.
    setVisibility('hidden')
    document.dispatchEvent(new Event('visibilitychange'))

    // Come back visible — hook must fetch without waiting pollInterval.
    setVisibility('visible')
    document.dispatchEvent(new Event('visibilitychange'))
    await flushMicrotasks()
    expect(fetchMock).toHaveBeenCalledTimes(2)

    unmount()
  })

  it('detaches interval and visibilitychange listener on unmount', async () => {
    const removeSpy = vi.spyOn(document, 'removeEventListener')
    // ``clearInterval`` on globalThis is typed as a non-method property
    // in lib.dom; spy via window where the overload is a real method.
    const clearSpy = vi.spyOn(window, 'clearInterval')

    const { unmount } = renderHook(() => useGraphData('auto', 1000))
    await flushMicrotasks()

    unmount()
    expect(clearSpy).toHaveBeenCalled()
    // Listener teardown: at least one removeEventListener call targeted
    // the visibilitychange event (other listeners may also detach — we
    // only care that ours did).
    const sawVisibility = removeSpy.mock.calls.some(
      call => call[0] === 'visibilitychange',
    )
    expect(sawVisibility).toBe(true)

    removeSpy.mockRestore()
    clearSpy.mockRestore()
  })

  it('does not abort the in-flight request when a tick fires mid-fetch (Blocker 1 livelock guard)', async () => {
    // Simulate a slow backend: each fetch waits 500ms before resolving,
    // and pollInterval is 100ms. Without the loadingRef gate, every
    // tick would bump ``trigger`` and the main effect would abort the
    // previous AbortController before the response arrived, producing
    // an ever-growing string of aborts that never settle.
    // Track every ``abort()`` call across every controller the hook
    // spins up. Wrapping the prototype method is simpler (and survives
    // strict type-checking) than spying per-instance.
    let abortCalls = 0
    const originalCtor = globalThis.AbortController
    const originalAbort = originalCtor.prototype.abort
    originalCtor.prototype.abort = function patchedAbort(
      this: AbortController,
      reason?: unknown,
    ) {
      abortCalls += 1
      return originalAbort.call(this, reason)
    }

    fetchMock.mockImplementation(
      async (_input: RequestInfo | URL, init?: RequestInit) => {
        return new Promise<Response>((resolve, reject) => {
          const signal = init?.signal
          const timeout = setTimeout(() => resolve(okResponse()), 500)
          if (signal) {
            signal.addEventListener('abort', () => {
              clearTimeout(timeout)
              reject(
                Object.assign(new Error('aborted'), { name: 'AbortError' }),
              )
            })
          }
        })
      },
    )

    const { unmount } = renderHook(() => useGraphData('auto', 100))
    // Mount fetch kicks off — let the promise register its abort
    // listener before we start advancing timers.
    await flushMicrotasks()

    // Advance 400ms (4 interval ticks) while the initial fetch is
    // still in flight. The loadingRef gate must suppress refresh()
    // calls, so no new AbortController is spun up and no existing
    // abort() is called.
    await act(async () => {
      vi.advanceTimersByTime(400)
    })
    await flushMicrotasks()

    // Exactly one fetch kicked off despite 4 interval ticks firing.
    expect(fetchMock).toHaveBeenCalledTimes(1)

    // Zero aborts across the whole window — the interval ticks skipped
    // as designed rather than cancelling the in-flight request.
    expect(abortCalls).toBe(0)

    // Let the slow fetch settle, then confirm the next tick does fire.
    await act(async () => {
      vi.advanceTimersByTime(200)
    })
    await flushMicrotasks()
    // One more tick past the 500ms settle line should now dispatch.
    await act(async () => {
      vi.advanceTimersByTime(100)
    })
    await flushMicrotasks()
    expect(fetchMock.mock.calls.length).toBeGreaterThanOrEqual(2)

    unmount()
    // Restore the prototype method so other tests see a clean
    // AbortController. (afterEach runs regardless, but teardown here
    // keeps the prototype patch scoped to this test.)
    originalCtor.prototype.abort = originalAbort
  })
})
