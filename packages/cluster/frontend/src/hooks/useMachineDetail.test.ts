// @vitest-environment jsdom
import { act, cleanup, renderHook, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { useMachineDetail } from './useMachineDetail'
import { apiFetch } from '@/lib/api'

vi.mock('@/lib/api', () => ({ apiFetch: vi.fn() }))
const fetchMock = vi.mocked(apiFetch)

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>(done => { resolve = done })
  return { promise, resolve }
}

function response(data: unknown, ok = true) {
  return { ok, json: async () => data } as Response
}

function machineResponse(path: string, label: string) {
  if (path.endsWith('/engines')) return response([{ engine: label }])
  if (path.endsWith('/agents')) return response([{ id: label, name: label }])
  return response([{ id: label, event_type: 'online' }])
}

afterEach(() => { cleanup(); vi.resetAllMocks() })

it('hides the previous machine immediately and ignores its late response', async () => {
  const oldRequest = deferred<void>()
  fetchMock.mockImplementation(async path => {
    if (path.includes('/a/')) await oldRequest.promise
    return machineResponse(path, path.includes('/a/') ? 'a-data' : 'b-data')
  })
  const { result, rerender } = renderHook(({ id }) => useMachineDetail(id, ''), { initialProps: { id: 'a' } })
  rerender({ id: 'b' })
  expect(result.current.data).toBeNull()
  await waitFor(() => expect(result.current.data?.engines[0].engine).toBe('b-data'))
  await act(async () => { oldRequest.resolve(); await oldRequest.promise })
  expect(result.current.data?.agents[0].id).toBe('b-data')
  expect(result.current.data?.activity[0].id).toBe('b-data')
})

it('does not expose a loaded machine snapshot while another machine is loading', async () => {
  const nextRequest = deferred<void>()
  fetchMock.mockImplementation(async path => {
    if (path.includes('/b/')) await nextRequest.promise
    return machineResponse(path, path.includes('/a/') ? 'a-data' : 'b-data')
  })
  const { result, rerender } = renderHook(({ id }) => useMachineDetail(id, ''), { initialProps: { id: 'a' } })
  await waitFor(() => expect(result.current.status).toBe('loaded'))
  rerender({ id: 'b' })
  expect(result.current.status).toBe('loading')
  expect(result.current.data).toBeNull()
  await act(async () => { nextRequest.resolve() })
  await waitFor(() => expect(result.current.data?.agents[0].id).toBe('b-data'))
})

it('keeps a newer refresh when an older response finishes decoding later', async () => {
  const oldBody = deferred<unknown>()
  let requestCount = 0
  fetchMock.mockImplementation(async path => {
    const initial = requestCount++ < 3
    return initial
      ? { ok: true, json: () => oldBody.promise } as Response
      : machineResponse(path, 'fresh')
  })
  const { result } = renderHook(() => useMachineDetail('a', ''))
  await act(async () => { await result.current.refresh('a') })
  await act(async () => { oldBody.resolve([{ id: 'stale', engine: 'stale' }]) })
  expect(result.current.status).toBe('loaded')
  expect(result.current.data?.engines[0].engine).toBe('fresh')
})

it('surfaces partial HTTP failures and only publishes a complete retry snapshot', async () => {
  fetchMock.mockImplementation(async path => path.endsWith('/engines')
    ? response([], false) : machineResponse(path, 'partial'))
  const { result } = renderHook(() => useMachineDetail('a', ''))
  await waitFor(() => expect(result.current.status).toBe('error'))
  expect(result.current.data).toBeNull()
  fetchMock.mockImplementation(async path => machineResponse(path, 'recovered'))
  await act(async () => { await result.current.refresh() })
  expect(result.current.status).toBe('loaded')
  expect(result.current.data?.agents[0].id).toBe('recovered')
})

it('ignores delayed mutation refreshes for another machine or an unmounted view', async () => {
  fetchMock.mockImplementation(async path => machineResponse(path, 'current'))
  const { result, unmount } = renderHook(() => useMachineDetail('b', ''))
  await waitFor(() => expect(result.current.status).toBe('loaded'))
  const refresh = result.current.refresh
  const calls = fetchMock.mock.calls.length
  await act(async () => { await refresh('a') })
  expect(fetchMock).toHaveBeenCalledTimes(calls)
  unmount()
  await refresh('b')
  expect(fetchMock).toHaveBeenCalledTimes(calls)
})

it('does not fetch when the unplaced view has no machine selection', async () => {
  const { result } = renderHook(() => useMachineDetail(null, ''))
  expect(result.current.status).toBe('idle')
  await act(async () => { await result.current.refresh('__unplaced__') })
  expect(fetchMock).not.toHaveBeenCalled()
})
