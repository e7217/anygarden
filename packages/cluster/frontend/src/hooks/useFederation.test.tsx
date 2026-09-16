// @vitest-environment jsdom
// Behavior tests for useFederation (#593 / task #33): node capability
// derivation, snapshot polling on a selected channel, roster ordering,
// participant removal fence, and durable submission tracking for task
// commands.
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { cleanup, renderHook, waitFor, act } from '@testing-library/react'
import { useFederation } from './useFederation'

vi.mock('@/hooks/useAuth', () => ({
  useAuth: vi.fn(() => ({ user: { id: 'user-1', is_admin: true }, loading: false })),
}))

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

const emptyNodes = (url: string): Response => {
  if (url.includes('/node/invites')) return jsonResponse([])
  if (url.includes('/node/peers')) return jsonResponse([])
  throw new Error('unexpected ' + url)
}

function mockFetch(handler: (url: string, init?: RequestInit) => Response) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(
    (input, init) => Promise.resolve(handler(String(input), init)),
  )
}

beforeEach(() => {
  vi.restoreAllMocks()
  localStorage.clear()
  vi.useFakeTimers({ shouldAdvanceTime: true })
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.restoreAllMocks()
})

describe('useFederation', () => {
  it('derives ready capability when the admin API answers', async () => {
    mockFetch(emptyNodes)
    const { result } = renderHook(() => useFederation())
    await waitFor(() => expect(result.current.nodesCapability).toBe('ready'))
    expect(result.current.invites).toEqual([])
    expect(result.current.peers).toEqual([])
  })

  it('derives disabled capability from 404 (admin router unmounted)', async () => {
    mockFetch(() => new Response('Not Found', { status: 404 }))
    const { result } = renderHook(() => useFederation())
    await waitFor(() => expect(result.current.nodesCapability).toBe('disabled'))
  })

  it('derives disabled capability from SHARING_DISABLED 503', async () => {
    mockFetch((url) =>
      url.includes('/node/')
        ? jsonResponse({ code: 'SHARING_DISABLED' }, 503)
        : emptyNodes(url),
    )
    const { result } = renderHook(() => useFederation())
    await waitFor(() => expect(result.current.nodesCapability).toBe('disabled'))
  })

  it('loads and orders the roster for a selected channel', async () => {
    const snapshot = {
      authority_node_id: 'auth-1',
      channel_id: 'chan-1',
      applied_seq: 9,
      messages: [],
      participants: [
        { principal: { node_id: 'b', kind: 'agent', principal_id: 'p2' }, active: true, role: 'member', revision: 2 },
        { principal: { node_id: 'a', kind: 'human', principal_id: 'p1' }, active: false, role: 'observer', revision: 4 },
      ],
    }
    const fetchMock = mockFetch((url) =>
      url.includes('/shared-channels/auth-1/chan-1')
        ? jsonResponse(snapshot)
        : emptyNodes(url),
    )
    const { result } = renderHook(() => useFederation())
    await waitFor(() => expect(result.current.nodesCapability).toBe('ready'))
    act(() => {
      result.current.setChannelRef({ authority: 'auth-1', channel: 'chan-1' })
    })
    await waitFor(() => expect(result.current.snapshot?.applied_seq).toBe(9))
    // Sorted by node then principal, tombstones retained.
    expect(result.current.roster.map((p) => p.principal.node_id)).toEqual(['a', 'b'])
    expect(fetchMock.mock.calls.some(([u]) => String(u).includes('after_seq=0&limit=100'))).toBe(true)
  })

  it('keeps channel selection across remounts via localStorage', async () => {
    mockFetch(emptyNodes)
    const first = renderHook(() => useFederation())
    await waitFor(() => expect(first.result.current.nodesCapability).toBe('ready'))
    act(() => {
      first.result.current.setChannelRef({ authority: 'auth-1', channel: 'chan-1' })
    })
    first.unmount()
    const second = renderHook(() => useFederation())
    expect(second.result.current.channelRef).toEqual({ authority: 'auth-1', channel: 'chan-1' })
  })

  it('removes a participant with the current revision as the fence', async () => {
    const snapshot = {
      authority_node_id: 'auth-1',
      channel_id: 'chan-1',
      applied_seq: 5,
      messages: [],
      participants: [
        { principal: { node_id: 'b', kind: 'agent' as const, principal_id: 'p2' }, active: true, role: 'member', revision: 3 },
      ],
    }
    let removed = false
    const fetchMock = mockFetch((url, init) => {
      if (url.includes('/chan-1/participants') && init?.method === 'POST') {
        removed = true
        expect(JSON.parse(String(init.body)).expected_revision).toBe(3)
        return jsonResponse({})
      }
      if (url.includes('/shared-channels/auth-1/chan-1')) {
        return jsonResponse(
          removed
            ? { ...snapshot, participants: [{ ...snapshot.participants[0], active: false, revision: 4 }] }
            : snapshot,
        )
      }
      return emptyNodes(url)
    })
    const { result } = renderHook(() => useFederation())
    await waitFor(() => expect(result.current.nodesCapability).toBe('ready'))
    act(() => {
      result.current.setChannelRef({ authority: 'auth-1', channel: 'chan-1' })
    })
    await waitFor(() => expect(result.current.roster).toHaveLength(1))
    await act(async () => {
      await result.current.changeParticipant(snapshot.participants[0].principal, false, 'member', 3)
    })
    await waitFor(() => expect(result.current.roster[0].active).toBe(false))
    expect(removed).toBe(true)
  })

  it('queues a task request and tracks its unconfirmed submission', async () => {
    const snapshot = {
      authority_node_id: 'auth-1',
      channel_id: 'chan-1',
      applied_seq: 2,
      messages: [
        {
          message_id: 'm1',
          authority_node_id: 'auth-1',
          channel_id: 'chan-1',
          actor: { node_id: 'auth-1', kind: 'human', principal_id: 'u9' },
          seq: 1,
          thread_root_id: null,
          confirmed: true,
          text: 'prepare the release notes',
        },
      ],
      participants: [],
    }
    const fetchMock = mockFetch((url, init) => {
      if (url.endsWith('/commands')) {
        const body = JSON.parse(String(init?.body))
        expect(body.kind).toBe('task.request')
        expect(body.sender_node_id).toBe('node-self')
        expect(body.grant_epoch).toBe(2)
        expect(body.actor).toEqual({ node_id: 'node-self', kind: 'human', principal_id: 'user-1' })
        return jsonResponse({ request_id: body.request_id, state: 'unconfirmed', receipt: null, error_code: null })
      }
      if (url.includes('/shared-channels/auth-1/chan-1')) return jsonResponse(snapshot)
      return emptyNodes(url)
    })
    const { result } = renderHook(() => useFederation())
    await waitFor(() => expect(result.current.nodesCapability).toBe('ready'))
    act(() => {
      result.current.setChannelRef({
        authority: 'auth-1',
        channel: 'chan-1',
        senderNodeId: 'node-self',
        grantEpoch: 2,
      })
    })
    await waitFor(() => expect(result.current.snapshot).not.toBeNull())
    await act(async () => {
      await result.current.requestTask({
        delegationId: 'd1',
        taskId: 't1',
        sourceMessageId: 'm1',
        executorNodeId: 'node-b',
        executorAgentId: 'agent-b',
      })
    })
    expect(result.current.submissions).toHaveLength(1)
    expect(result.current.submissions[0].state).toBe('unconfirmed')
    expect(result.current.submissions[0].kind).toBe('task.request')
    expect(result.current.actorPrincipal).toEqual({
      node_id: 'node-self',
      kind: 'human',
      principal_id: 'user-1',
    })
  })

  it('does not poll or act for non-admin sessions', async () => {
    const { useAuth } = await import('@/hooks/useAuth')
    vi.mocked(useAuth).mockReturnValue({ user: { id: 'user-1', is_admin: false }, loading: false } as never)
    const fetchMock = mockFetch(emptyNodes)
    const { result } = renderHook(() => useFederation())
    act(() => {
      vi.advanceTimersByTime(15_000)
    })
    expect(result.current.isAdmin).toBe(false)
    expect(fetchMock).not.toHaveBeenCalled()
  })
})
