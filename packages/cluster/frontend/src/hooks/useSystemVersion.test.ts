// @vitest-environment jsdom
// Contract tests for the version / update-status hooks (#546).
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { cleanup, renderHook, waitFor, act } from '@testing-library/react'
import { useSystemVersion, useUpdateStatus, type PackageUpdate } from './useSystemVersion'

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

const UPDATE: PackageUpdate = {
  package: 'anygarden',
  current: '0.15.0',
  latest: '0.16.0',
  update_available: true,
  checked_at: '2026-07-23T12:00:00Z',
  error: null,
}

beforeEach(() => vi.restoreAllMocks())
afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe('useSystemVersion', () => {
  it('exposes the server version fetched on mount', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse({ version: '0.15.0' }))
    const { result } = renderHook(() => useSystemVersion())
    await waitFor(() => expect(result.current).toBe('0.15.0'))
  })

  it('stays null on failure (best-effort, never throws)', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(null, { status: 500 }))
    const { result } = renderHook(() => useSystemVersion())
    await act(async () => {
      await Promise.resolve()
    })
    expect(result.current).toBeNull()
  })
})

describe('useUpdateStatus', () => {
  it('loads cached updates on mount when enabled', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse([UPDATE]))
    const { result } = renderHook(() => useUpdateStatus(true))
    await waitFor(() => expect(result.current.updates.length).toBe(1))
    expect(result.current.updates[0].update_available).toBe(true)
  })

  it('does not fetch when disabled (non-admin)', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch')
    renderHook(() => useUpdateStatus(false))
    await act(async () => {
      await Promise.resolve()
    })
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('refresh posts to check-updates and stores the result', async () => {
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(jsonResponse([])) // initial GET (empty cache)
      .mockResolvedValueOnce(jsonResponse([UPDATE])) // POST check-updates

    const { result } = renderHook(() => useUpdateStatus(true))
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1))

    await act(async () => {
      await result.current.refresh()
    })
    expect(result.current.updates[0].latest).toBe('0.16.0')
    expect(fetchMock).toHaveBeenLastCalledWith(
      '/api/v1/system/check-updates',
      expect.objectContaining({ method: 'POST' }),
    )
  })
})
