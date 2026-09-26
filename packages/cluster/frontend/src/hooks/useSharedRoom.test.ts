// @vitest-environment jsdom
import { act, cleanup, renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '@/lib/federationApi'
import { useSharedRoom } from './useSharedRoom'

vi.mock('@/lib/federationApi', async importOriginal => ({
  ...await importOriginal<typeof api>(),
  getSharedRoomSnapshot: vi.fn(), sendSharedMessage: vi.fn(),
  delegateSharedMessage: vi.fn(), cancelSharedDelegation: vi.fn(), retrySubmission: vi.fn(),
}))

const ref = { authority_node_id: 'authority', channel_id: 'A' }
function snapshot(channel = 'A', seq = 1): api.SharedRoomSnapshot {
  return {
    authority_node_id: 'authority', channel_id: channel, applied_seq: seq,
    participants: [], permissions: { can_send: true, can_delegate: true },
    targets: [], delegations: [], submissions: [],
    messages: [{ message_id: `${channel}-${seq}`, authority_node_id: 'authority', channel_id: channel, actor: { node_id: 'authority', kind: 'human', principal_id: 'u' }, seq, thread_root_id: null, confirmed: true, text: `${channel} message ${seq}` }],
    cursor: { oldest_seq: seq, newest_seq: seq, has_more_before: seq > 1 },
  }
}
function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason: unknown) => void
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}
beforeEach(() => vi.resetAllMocks())
afterEach(() => { cleanup(); vi.useRealTimers() })

describe('useSharedRoom', () => {
  it('loads latest messages without an oldest-window cursor and resumes durable submissions', async () => {
    const view = snapshot('A', 120)
    view.submissions = [{ request_id: 'pending', kind: 'task.request', state: 'unconfirmed', receipt: null, error_code: null, delegation_id: 'd', source_message_id: 'A-120', executor: null, can_retry: true }]
    vi.mocked(api.getSharedRoomSnapshot).mockResolvedValue(view)
    const { result } = renderHook(() => useSharedRoom(ref))
    await waitFor(() => expect(result.current.data?.submissions).toHaveLength(1))
    expect(api.getSharedRoomSnapshot).toHaveBeenCalledWith(ref, { signal: expect.any(AbortSignal) })
  })

  it('hides A immediately on A→B→A and ignores delayed old snapshots', async () => {
    const first = deferred<api.SharedRoomSnapshot>()
    const second = deferred<api.SharedRoomSnapshot>()
    vi.mocked(api.getSharedRoomSnapshot).mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise).mockResolvedValueOnce(snapshot('A', 3))
    const { result, rerender } = renderHook(({ channel }) => useSharedRoom({ ...ref, channel_id: channel }), { initialProps: { channel: 'A' } })
    rerender({ channel: 'B' })
    expect(result.current.data).toBeNull()
    rerender({ channel: 'A' })
    await waitFor(() => expect(result.current.data?.messages[0].seq).toBe(3))
    await act(async () => { first.resolve(snapshot('A', 1)); second.resolve(snapshot('B', 2)) })
    expect(result.current.data?.messages.map(m => m.text)).toEqual(['A message 3'])
  })

  it('keeps confirmed content on a connection error but clears it after access is revoked', async () => {
    vi.mocked(api.getSharedRoomSnapshot).mockResolvedValueOnce(snapshot()).mockRejectedValueOnce(new Error('offline')).mockRejectedValueOnce(new api.FederationApiError('SCOPE_DENIED', 403))
    const { result } = renderHook(() => useSharedRoom(ref))
    await waitFor(() => expect(result.current.data).not.toBeNull())
    await act(() => result.current.refresh())
    expect(result.current.data?.messages).toHaveLength(1)
    expect(result.current.error).toBe('CONNECTION_FAILED')
    await act(() => result.current.refresh())
    expect(result.current.data).toBeNull()
    expect(result.current.error).toBe('SCOPE_DENIED')
  })

  it('merges older pages and subsequent refreshes without losing history', async () => {
    vi.mocked(api.getSharedRoomSnapshot).mockResolvedValueOnce(snapshot('A', 120)).mockResolvedValueOnce(snapshot('A', 1)).mockResolvedValueOnce(snapshot('A', 121))
    const { result } = renderHook(() => useSharedRoom(ref))
    await waitFor(() => expect(result.current.data).not.toBeNull())
    await act(() => result.current.loadOlder())
    expect(api.getSharedRoomSnapshot).toHaveBeenLastCalledWith(ref, { before_seq: 120, signal: expect.any(AbortSignal) })
    await act(() => result.current.refresh())
    expect(result.current.data?.messages.map(m => m.seq)).toEqual([1, 120, 121])
    expect(result.current.data?.cursor.has_more_before).toBe(false)
  })

  it('polls new messages for ordinary members and suspends while hidden', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    vi.mocked(api.getSharedRoomSnapshot).mockResolvedValueOnce(snapshot('A', 120)).mockResolvedValue(snapshot('A', 121))
    const { result } = renderHook(() => useSharedRoom(ref))
    await waitFor(() => expect(result.current.data).not.toBeNull())
    await act(async () => { await vi.advanceTimersByTimeAsync(5000) })
    expect(api.getSharedRoomSnapshot).toHaveBeenLastCalledWith(ref, { after_seq: 120, signal: expect.any(AbortSignal) })
    const visibility = vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('hidden')
    await act(async () => { await vi.advanceTimersByTimeAsync(10000) })
    expect(api.getSharedRoomSnapshot).toHaveBeenCalledTimes(2)
    visibility.mockRestore()
  })

  it('does not refresh or report successful completion from an old-room mutation', async () => {
    vi.mocked(api.getSharedRoomSnapshot).mockImplementation(async r => snapshot(r.channel_id))
    const mutation = deferred<api.SharedCommandResult>()
    vi.mocked(api.delegateSharedMessage).mockReturnValue(mutation.promise)
    const { result, rerender } = renderHook(({ channel }) => useSharedRoom({ ...ref, channel_id: channel }), { initialProps: { channel: 'A' } })
    await waitFor(() => expect(result.current.data).not.toBeNull())
    let action!: Promise<boolean>
    act(() => { action = result.current.delegate({ request_id: 'same', source_message_id: 'A-1', executor: { node_id: 'remote', agent_id: 'agent' } }) })
    rerender({ channel: 'B' })
    await waitFor(() => expect(result.current.data?.channel_id).toBe('B'))
    await act(async () => mutation.resolve({ request_id: 'same', state: 'unconfirmed', receipt: null, error_code: null }))
    expect(await action).toBe(false)
    expect(api.getSharedRoomSnapshot).toHaveBeenCalledTimes(2)
    expect(result.current.busy).toBe(false)
  })

  it('retries a durable submission using the exact original request id', async () => {
    vi.mocked(api.getSharedRoomSnapshot).mockResolvedValue(snapshot())
    vi.mocked(api.retrySubmission).mockResolvedValue({ request_id: 'original', state: 'unconfirmed', receipt: null, error_code: null })
    const { result } = renderHook(() => useSharedRoom(ref))
    await waitFor(() => expect(result.current.data).not.toBeNull())
    await act(() => result.current.retry('original'))
    expect(api.retrySubmission).toHaveBeenCalledWith('authority', 'A', 'original')
    expect(api.delegateSharedMessage).not.toHaveBeenCalled()
  })

  it('reports revision conflicts and refreshes without automatically resubmitting', async () => {
    vi.mocked(api.getSharedRoomSnapshot).mockResolvedValue(snapshot())
    vi.mocked(api.cancelSharedDelegation).mockRejectedValue(new api.FederationApiError('REVISION_CONFLICT', 409))
    const { result } = renderHook(() => useSharedRoom(ref))
    await waitFor(() => expect(result.current.data).not.toBeNull())
    await act(() => result.current.cancel('d', { request_id: 'stop', expected_revision: 2 }))
    expect(result.current.actionError).toBe('REVISION_CONFLICT')
    expect(api.cancelSharedDelegation).toHaveBeenCalledTimes(1)
    expect(api.getSharedRoomSnapshot).toHaveBeenCalledTimes(2)
  })
})
