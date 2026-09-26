// @vitest-environment jsdom
import { act, cleanup, renderHook, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { apiFetch } from '@/lib/api'
import { ACTIVITY_POLL_MS, hasPendingActivity, useAgentActivity, type ActivityLog } from './useAgentActivity'

vi.mock('@/lib/api', () => ({ apiFetch: vi.fn() }))
const fetchMock = vi.mocked(apiFetch)
const time = '2026-09-26T12:00:00.000000+00:00'
const row = (id: string, patch: Partial<ActivityLog> = {}): ActivityLog => ({ id, timestamp: time, event_type: 'start_requested', request_id: null, details: null, ...patch })
const response = (rows: ActivityLog[], ok = true) => ({ ok, json: async () => rows }) as Response
function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>(done => { resolve = done })
  return { promise, resolve }
}
function server(getRows: () => ActivityLog[]) {
  fetchMock.mockImplementation(async path => {
    const query = new URL(path, 'http://local').searchParams
    const after = query.has('after_timestamp')
    const timestamp = query.get(after ? 'after_timestamp' : 'before_timestamp')
    const id = query.get(after ? 'after_id' : 'before_id')
    const compare = (a: ActivityLog, b: { timestamp: string; id: string }) => a.timestamp === b.timestamp ? a.id < b.id ? -1 : a.id > b.id ? 1 : 0 : a.timestamp < b.timestamp ? -1 : 1
    const rows = getRows().filter(row => !timestamp || (after ? compare(row, { timestamp, id: id! }) > 0 : compare(row, { timestamp, id: id! }) < 0))
    rows.sort((a, b) => compare(a, b) * (after ? 1 : -1))
    return response(rows.slice(0, Number(query.get('limit'))))
  })
}
afterEach(() => { cleanup(); vi.resetAllMocks(); vi.useRealTimers(); Object.defineProperty(document, 'visibilityState', { value: 'visible', configurable: true }) })

it('uses exclusive time and ID cursors to load tied timestamps without duplicates', async () => {
  server(() => Array.from({ length: 105 }, (_, index) => row(String(index).padStart(4, '0'))))
  const { result } = renderHook(() => useAgentActivity('a'))
  await waitFor(() => expect(result.current.logs).toHaveLength(50))
  expect(result.current.hasMore).toBe(true)
  await act(async () => { await result.current.loadMore(); await result.current.loadMore() })
  expect(result.current.logs).toHaveLength(105)
  expect(new Set(result.current.logs.map(row => row.id)).size).toBe(105)
  expect(result.current.logs[0].id).toBe('0104')
  expect(result.current.logs.at(-1)?.id).toBe('0000')
  expect(result.current.hasMore).toBe(false)
})

it('preserves loaded records on failure and retries the failed page', async () => {
  server(() => Array.from({ length: 55 }, (_, index) => row(String(index).padStart(4, '0'))))
  const { result } = renderHook(() => useAgentActivity('a'))
  await waitFor(() => expect(result.current.logs).toHaveLength(50))
  fetchMock.mockResolvedValueOnce(response([], false))
  await act(async () => { await result.current.loadMore() })
  expect(result.current.error).toBe('older')
  expect(result.current.logs).toHaveLength(50)
  await act(async () => { await result.current.retry() })
  expect(result.current.error).toBeNull()
  expect(result.current.logs).toHaveLength(55)
})

it('catches up a burst spanning pages and replays same-time lower IDs', async () => {
  let rows = [row('z')]
  server(() => rows)
  const { result } = renderHook(() => useAgentActivity('a'))
  await waitFor(() => expect(result.current.logs).toHaveLength(1))
  rows = [row('a'), ...rows, ...Array.from({ length: 450 }, (_, index) => row(String(index).padStart(4, '0'), { timestamp: '2026-09-26T12:00:01.000000+00:00' }))]
  await act(async () => { await result.current.refresh() })
  expect(result.current.logs).toHaveLength(452)
  expect(result.current.logs.some(row => row.id === 'a')).toBe(true)
  expect(fetchMock.mock.calls.filter(([path]) => path.includes('after_timestamp'))).toHaveLength(3)
})

it('bounds each refresh and resumes its continuation without losing a large burst', async () => {
  let rows = [row('z')]
  server(() => rows)
  const { result } = renderHook(() => useAgentActivity('a'))
  await waitFor(() => expect(result.current.logs).toHaveLength(1))
  rows = [...rows, ...Array.from({ length: 1100 }, (_, index) => row(String(index).padStart(4, '0')))]
  await act(async () => { await result.current.refresh() })
  expect(fetchMock.mock.calls.filter(([path]) => path.includes('after_timestamp'))).toHaveLength(5)
  expect(result.current.logs).toHaveLength(1001)
  await act(async () => { await result.current.refresh() })
  expect(result.current.logs).toHaveLength(1101)
  expect(new Set(result.current.logs.map(row => row.id)).size).toBe(1101)
})

it('does not move the history cursor when refreshed records are merged', async () => {
  let rows = Array.from({ length: 60 }, (_, index) => row(String(index).padStart(4, '0')))
  server(() => rows)
  const { result } = renderHook(() => useAgentActivity('a'))
  await waitFor(() => expect(result.current.logs).toHaveLength(50))
  rows = [...rows, row('new', { timestamp: '2026-09-26T12:00:01.000000+00:00' })]
  await act(async () => { await result.current.refresh(); await result.current.loadMore() })
  expect(result.current.logs).toHaveLength(61)
  expect(new Set(result.current.logs.map(row => row.id)).size).toBe(61)
  const olderPath = fetchMock.mock.calls.find(([path]) => path.includes('before_timestamp'))![0]
  expect(new URL(olderPath, 'http://local').searchParams.get('before_id')).toBe('0010')
})

it('discards delayed JSON across A to B to A and rejects callbacks for another agent', async () => {
  const oldBody = deferred<ActivityLog[]>()
  let calls = 0
  fetchMock.mockImplementation(async path => ++calls === 1 ? { ok: true, json: () => oldBody.promise } as Response : response([row(path.includes('/b/') ? 'b-current' : 'a-current')]))
  const { result, rerender } = renderHook(({ id }) => useAgentActivity(id), { initialProps: { id: 'a' } })
  const oldRefresh = result.current.refresh
  await act(async () => {})
  rerender({ id: 'b' })
  expect(result.current.logs).toHaveLength(0)
  await waitFor(() => expect(result.current.logs[0]?.id).toBe('b-current'))
  await act(async () => { await oldRefresh() })
  expect(fetchMock).toHaveBeenCalledTimes(2)
  rerender({ id: 'a' })
  await waitFor(() => expect(result.current.logs[0]?.id).toBe('a-current'))
  await act(async () => { oldBody.resolve([row('a-obsolete')]) })
  expect(result.current.logs[0].id).toBe('a-current')
})

it('polls pending work only while active and visible, stopping after a terminal event', async () => {
  vi.useFakeTimers()
  let rows = [row('started', { event_type: 'handler_started', request_id: 'request' })]
  server(() => rows)
  const { result, rerender } = renderHook(({ active }) => useAgentActivity('a', active), { initialProps: { active: false } })
  expect(fetchMock).not.toHaveBeenCalled()
  rerender({ active: true })
  await act(async () => {})
  expect(result.current.autoUpdating).toBe(true)
  Object.defineProperty(document, 'visibilityState', { value: 'hidden', configurable: true })
  act(() => document.dispatchEvent(new Event('visibilitychange')))
  await act(async () => { await vi.advanceTimersByTimeAsync(ACTIVITY_POLL_MS * 2) })
  expect(fetchMock).toHaveBeenCalledTimes(1)
  Object.defineProperty(document, 'visibilityState', { value: 'visible', configurable: true })
  act(() => document.dispatchEvent(new Event('visibilitychange')))
  rows = [...rows, row('finished', { timestamp: '2026-09-26T12:00:01.000000+00:00', event_type: 'handler_finished', request_id: 'request', details: { outcome: 'ok' } })]
  await act(async () => { await vi.advanceTimersByTimeAsync(ACTIVITY_POLL_MS) })
  expect(result.current.logs).toHaveLength(2)
  expect(result.current.autoUpdating).toBe(false)
  await act(async () => { await vi.advanceTimersByTimeAsync(ACTIVITY_POLL_MS * 2) })
  expect(fetchMock).toHaveBeenCalledTimes(2)
})

it('shows initial failures separately and rejects refresh after unmount', async () => {
  fetchMock.mockRejectedValueOnce(new Error('network down')).mockResolvedValueOnce(response([row('recovered')]))
  const { result, unmount } = renderHook(() => useAgentActivity('a'))
  await waitFor(() => expect(result.current.error).toBe('initial'))
  expect(result.current.loading).toBe(false)
  await act(async () => { await result.current.retry() })
  expect(result.current.logs[0].id).toBe('recovered')
  const refresh = result.current.refresh
  unmount()
  await refresh()
  expect(fetchMock).toHaveBeenCalledTimes(2)
})


it.each(['queued', 'retrying'])('keeps polling %s until the request actually finishes', async outcome => {
  vi.useFakeTimers()
  let rows = [row('deferred', { event_type: 'handler_finished', request_id: 'request', details: { outcome } })]
  server(() => rows)
  const { result } = renderHook(() => useAgentActivity('a'))
  await act(async () => {})
  expect(result.current.autoUpdating).toBe(true)
  await act(async () => { await vi.advanceTimersByTimeAsync(ACTIVITY_POLL_MS) })
  expect(fetchMock).toHaveBeenCalledTimes(2)
  expect(result.current.autoUpdating).toBe(true)
  rows = [...rows, row('terminal', { timestamp: '2026-09-26T12:00:01.000000+00:00', event_type: 'handler_finished', request_id: 'request', details: { outcome: 'retry_exhausted' } })]
  await act(async () => { await vi.advanceTimersByTimeAsync(ACTIVITY_POLL_MS) })
  expect(result.current.autoUpdating).toBe(false)
  await act(async () => { await vi.advanceTimersByTimeAsync(ACTIVITY_POLL_MS * 2) })
  expect(fetchMock).toHaveBeenCalledTimes(3)
})

it('recognizes only supported terminal outcomes and legacy completion', () => {
  const started = row('start', { event_type: 'handler_started', request_id: 'request' })
  for (const outcome of ['ok', 'failed', 'timeout', 'cancelled', 'rejected', 'retry_exhausted', undefined]) {
    expect(hasPendingActivity([started, row('end', { event_type: 'handler_finished', request_id: 'request', details: { outcome } })])).toBe(false)
  }
  expect(hasPendingActivity([started, row('unknown', { event_type: 'handler_finished', request_id: 'request', details: { outcome: 'future_nonterminal' } })])).toBe(true)
})
