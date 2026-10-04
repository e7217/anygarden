// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, render, renderHook, screen } from '@testing-library/react'

import { useWebSocket, type ChatMessage } from './useWebSocket'

class FakeWebSocket {
  onopen: ((event: Event) => void) | null = null
  onclose: ((event: CloseEvent) => void) | null = null
  onmessage: ((event: MessageEvent) => void) | null = null
  send = vi.fn()
  close = vi.fn()

  constructor(
    public url: string,
    public protocols?: string | string[],
  ) {
    sockets.push(this)
  }
}

let sockets: FakeWebSocket[] = []

function Harness({ roomId = 'room-1' }: { roomId?: string | null }) {
  const { typingUsers, typingStages } = useWebSocket(roomId)
  return <div data-testid="typing">{JSON.stringify({ users: [...typingUsers], stages: typingStages })}</div>
}

function typingState() {
  return JSON.parse(screen.getByTestId('typing').textContent || '{}')
}

function receiveTyping(socket: FakeWebSocket, participantId: string, isTyping: boolean, stage?: string) {
  act(() => socket.onmessage?.({ data: JSON.stringify({
    type: 'typing', participant_id: participantId, is_typing: isTyping, stage,
  }) } as MessageEvent))
}

function closeEvent(code: number): CloseEvent {
  return { code, reason: '' } as CloseEvent
}

beforeEach(() => {
  vi.useFakeTimers()
  sockets = []
  localStorage.clear()
  localStorage.setItem('anygarden_token', 'token-one')
  globalThis.WebSocket = FakeWebSocket as unknown as typeof WebSocket
  globalThis.fetch = vi.fn().mockResolvedValue(
    new Response(JSON.stringify([]), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }),
  ) as unknown as typeof fetch
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.restoreAllMocks()
})

describe('useWebSocket reconnect guards', () => {
  it('clears a rejected auth token from the history fetch path', async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ detail: 'Invalid or expired token' }), {
        status: 401,
        headers: { 'Content-Type': 'application/json' },
      }),
    ) as unknown as typeof fetch

    render(<Harness />)

    await act(async () => {
      await Promise.resolve()
      await Promise.resolve()
    })

    expect(localStorage.getItem('anygarden_token')).toBeNull()

    act(() => {
      sockets[0].onclose?.(closeEvent(1006))
      vi.advanceTimersByTime(30_000)
    })

    expect(sockets).toHaveLength(1)
  })

  it('keeps a valid token on forbidden room history but stops reconnecting that room', async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ detail: 'Not a room member' }), {
        status: 403,
        headers: { 'Content-Type': 'application/json' },
      }),
    ) as unknown as typeof fetch

    render(<Harness />)

    await act(async () => {
      await Promise.resolve()
      await Promise.resolve()
    })

    expect(localStorage.getItem('anygarden_token')).toBe('token-one')

    act(() => {
      sockets[0].onclose?.(closeEvent(1006))
      vi.advanceTimersByTime(30_000)
    })

    expect(sockets).toHaveLength(1)
  })

  it('does not reconnect a stale socket after the auth token changes', () => {
    render(<Harness />)
    expect(sockets).toHaveLength(1)

    localStorage.setItem('anygarden_token', 'token-two')
    act(() => {
      sockets[0].onclose?.(closeEvent(1006))
      vi.advanceTimersByTime(30_000)
    })

    expect(sockets).toHaveLength(1)
  })

  it('does not auto-reconnect after being superseded (4040) by another connection', () => {
    render(<Harness />)

    act(() => {
      sockets[0].onclose?.(closeEvent(4040))
      vi.advanceTimersByTime(30_000)
    })

    expect(sockets).toHaveLength(1)
  })

  it('reconnects a superseded socket once when the tab becomes visible or focused', () => {
    render(<Harness />)
    act(() => {
      sockets[0].onclose?.(closeEvent(4040))
    })

    act(() => {
      document.dispatchEvent(new Event('visibilitychange'))
    })
    expect(sockets).toHaveLength(2)

    // Only a superseded socket is revived — focus on a live one is a no-op.
    act(() => {
      window.dispatchEvent(new Event('focus'))
    })
    expect(sockets).toHaveLength(2)

    act(() => {
      sockets[1].onclose?.(closeEvent(4040))
      window.dispatchEvent(new Event('focus'))
    })
    expect(sockets).toHaveLength(3)
  })

  it('stops listening for visibility after unmount', () => {
    const { unmount } = render(<Harness />)
    act(() => {
      sockets[0].onclose?.(closeEvent(4040))
    })
    unmount()
    act(() => {
      document.dispatchEvent(new Event('visibilitychange'))
    })

    expect(sockets).toHaveLength(1)
  })

  it('clears a pending reconnect timer on unmount', () => {
    const { unmount } = render(<Harness />)
    expect(sockets).toHaveLength(1)

    act(() => {
      sockets[0].onclose?.(closeEvent(1006))
    })
    unmount()
    act(() => {
      vi.advanceTimersByTime(30_000)
    })

    expect(sockets).toHaveLength(1)
  })
})

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>(done => { resolve = done })
  return { promise, resolve }
}
const historyResponse = (messages: ChatMessage[]) => new Response(JSON.stringify(messages), {
  headers: { 'Content-Type': 'application/json' },
})
const chatMessage = (room: string, seq: number, content = room): ChatMessage => ({
  type: 'message', id: `${room}-${seq}`, room_id: room, participant_id: 'participant',
  content, seq, created_at: '2026-09-30T09:00:00Z',
})

describe('useWebSocket room history isolation', () => {
  it('ignores delayed A history after A → B → A and preserves the current reconnect cursor', async () => {
    const oldA = deferred<Response>()
    const newA = deferred<Response>()
    vi.mocked(fetch)
      .mockReturnValueOnce(oldA.promise)
      .mockResolvedValueOnce(historyResponse([chatMessage('B', 4)]))
      .mockReturnValueOnce(newA.promise)
    const { result, rerender } = renderHook(({ room }) => useWebSocket(room), { initialProps: { room: 'A' } })
    rerender({ room: 'B' })
    await act(async () => { await Promise.resolve() })
    expect(result.current.messages.map(message => message.room_id)).toEqual(['B'])
    rerender({ room: 'A' })
    expect(result.current.messages).toEqual([])
    await act(async () => { oldA.resolve(historyResponse([chatMessage('A', 90, 'stale A')])) })
    expect(result.current.messages).toEqual([])
    await act(async () => { newA.resolve(historyResponse([chatMessage('A', 7, 'current A')])) })
    expect(result.current.messages.map(message => message.content)).toEqual(['current A'])
    act(() => { sockets[2].onclose?.(closeEvent(1006)); vi.advanceTimersByTime(1000) })
    expect(sockets[3].url).toContain('/ws/rooms/A?since_seq=7')
  })

  it('keeps live messages when the initial history body arrives later', async () => {
    const body = deferred<ChatMessage[]>()
    vi.mocked(fetch).mockResolvedValueOnce({ ok: true, status: 200, json: () => body.promise } as Response)
    const { result } = renderHook(() => useWebSocket('A'))
    await act(async () => { await Promise.resolve() })
    receiveFrame(sockets[0], { ...chatMessage('A', 8, 'live result') })
    await act(async () => { body.resolve([chatMessage('A', 6), chatMessage('A', 7)]) })
    expect(result.current.messages.map(message => message.seq)).toEqual([6, 7, 8])
    act(() => { sockets[0].onclose?.(closeEvent(1006)); vi.advanceTimersByTime(1000) })
    expect(sockets[1].url).toContain('since_seq=8')
  })

  it('deduplicates live and historical copies while retaining newer live contents', async () => {
    const history = deferred<Response>()
    vi.mocked(fetch).mockReturnValueOnce(history.promise)
    const { result } = renderHook(() => useWebSocket('A'))
    receiveFrame(sockets[0], { ...chatMessage('A', 3, 'latest live result') })
    await act(async () => { history.resolve(historyResponse([chatMessage('A', 2), chatMessage('A', 3, 'history copy')])) })
    expect(result.current.messages.map(message => message.content)).toEqual(['A', 'latest live result'])
  })

  it.each([401, 403])('ignores a delayed forbidden response from the room that was left (HTTP %s)', async status => {
    const oldHistory = deferred<Response>()
    vi.mocked(fetch).mockReturnValueOnce(oldHistory.promise).mockResolvedValueOnce(historyResponse([chatMessage('B', 1)]))
    const invalid = vi.fn()
    window.addEventListener('anygarden:auth:invalid', invalid)
    try {
      const { result, rerender } = renderHook(({ room }) => useWebSocket(room), { initialProps: { room: 'A' } })
      rerender({ room: 'B' })
      await act(async () => { oldHistory.resolve(new Response(null, { status })) })
      expect(localStorage.getItem('anygarden_token')).toBe('token-one')
      expect(invalid).not.toHaveBeenCalled()
      expect(result.current.messages.map(message => message.room_id)).toEqual(['B'])
      act(() => { sockets[1].onclose?.(closeEvent(1006)); vi.advanceTimersByTime(1000) })
      expect(sockets[2].url).toContain('/ws/rooms/B?since_seq=1')
    } finally { window.removeEventListener('anygarden:auth:invalid', invalid) }
  })

  it('does not apply old history after suspension or an authentication change', async () => {
    const history = deferred<Response>()
    vi.mocked(fetch).mockReturnValueOnce(history.promise)
    const { result, rerender } = renderHook(({ room }: { room: string | null }) => useWebSocket(room), { initialProps: { room: 'A' as string | null } })
    localStorage.setItem('anygarden_token', 'token-two')
    await act(async () => { history.resolve(historyResponse([chatMessage('A', 99)])) })
    expect(result.current.messages).toEqual([])
    rerender({ room: null })
    expect(result.current.connected).toBe(false)
    expect(result.current.messages).toEqual([])
  })

  it('ignores old socket messages after changing the authenticated session', () => {
    const { result } = renderHook(() => useWebSocket('A'))
    localStorage.setItem('anygarden_token', 'token-two')
    receiveFrame(sockets[0], { ...chatMessage('A', 99, 'old session') })
    expect(result.current.messages).toEqual([])
  })

  it('does not route stale handlers or sends into the newly selected room', () => {
    const { result, rerender } = renderHook(({ room }) => useWebSocket(room), { initialProps: { room: 'A' } })
    const oldMessage = sockets[0].onmessage
    const oldOpen = sockets[0].onopen
    const oldSend = result.current.send
    const oldTyping = result.current.sendTyping
    rerender({ room: 'B' })
    act(() => {
      oldOpen?.(new Event('open'))
      oldMessage?.({ data: JSON.stringify(chatMessage('A', 99)) } as MessageEvent)
      oldSend('old room request')
      oldTyping(true)
    })
    expect(result.current.connected).toBe(false)
    expect(result.current.messages).toEqual([])
    expect(sockets[1].send).not.toHaveBeenCalled()
    act(() => { result.current.send('current room request') })
    expect(sockets[1].send).toHaveBeenCalledWith(JSON.stringify({ type: 'send', content: 'current room request' }))
  })

  it('aborts history loading on unmount and ignores its late authentication failure', async () => {
    const history = deferred<Response>()
    vi.mocked(fetch).mockReturnValueOnce(history.promise)
    const { unmount } = renderHook(() => useWebSocket('A'))
    const signal = vi.mocked(fetch).mock.calls[0][1]?.signal
    unmount()
    expect(signal?.aborted).toBe(true)
    await act(async () => { history.resolve(new Response(null, { status: 401 })) })
    expect(localStorage.getItem('anygarden_token')).toBe('token-one')
  })
})

describe('useWebSocket typing stages', () => {
  it('tracks each participant and clears stages on stop, expiry, room change and close', () => {
    const view = render(<Harness />)
    receiveTyping(sockets[0], 'agent', true, 'using_tool')
    receiveTyping(sockets[0], 'human', true)
    expect(typingState()).toEqual({ users: ['agent', 'human'], stages: { agent: 'using_tool' } })

    receiveTyping(sockets[0], 'agent', true, 'unknown')
    expect(typingState().stages).toEqual({})
    receiveTyping(sockets[0], 'agent', true, 'writing')
    receiveTyping(sockets[0], 'human', false)
    expect(typingState()).toEqual({ users: ['agent'], stages: { agent: 'writing' } })

    act(() => vi.advanceTimersByTime(5000))
    expect(typingState()).toEqual({ users: [], stages: {} })

    receiveTyping(sockets[0], 'agent', true, 'preparing')
    view.rerender(<Harness roomId="room-2" />)
    expect(typingState()).toEqual({ users: [], stages: {} })
    receiveTyping(sockets[0], 'agent', true, 'using_tool')
    expect(typingState()).toEqual({ users: [], stages: {} })

    receiveTyping(sockets[1], 'agent', true, 'writing')
    act(() => sockets[1].onclose?.(closeEvent(1006)))
    expect(typingState()).toEqual({ users: [], stages: {} })
  })
})

function ProgressHarness() {
  const { typingStages, typingProgress } = useWebSocket('room-1')
  return <div data-testid="progress">{JSON.stringify({ stages: typingStages, progress: typingProgress })}</div>
}

function receiveFrame(socket: FakeWebSocket, frame: Record<string, unknown>) {
  act(() => socket.onmessage?.({ data: JSON.stringify(frame) } as MessageEvent))
}

describe('useWebSocket peer-wait progress (#762)', () => {
  it('keeps waiting_peers counts until the stage changes, stops or expires', () => {
    render(<ProgressHarness />)
    const state = () => JSON.parse(screen.getByTestId('progress').textContent || '{}')
    receiveFrame(sockets[0], {
      type: 'typing', participant_id: 'pm', is_typing: true, stage: 'waiting_peers',
      waiting_done: 0, waiting_total: 2, waiting_names: ['a', 'b'],
    })
    expect(state()).toEqual({ stages: { pm: 'waiting_peers' }, progress: { pm: { done: 0, total: 2 } } })

    receiveFrame(sockets[0], { type: 'typing', participant_id: 'pm', is_typing: true, stage: 'writing' })
    expect(state()).toEqual({ stages: { pm: 'writing' }, progress: {} })

    receiveFrame(sockets[0], {
      type: 'typing', participant_id: 'pm', is_typing: true, stage: 'waiting_peers',
      waiting_done: 1, waiting_total: 2,
    })
    receiveFrame(sockets[0], { type: 'typing', participant_id: 'pm', is_typing: false })
    expect(state()).toEqual({ stages: {}, progress: {} })

    receiveFrame(sockets[0], {
      type: 'typing', participant_id: 'pm', is_typing: true, stage: 'waiting_peers',
      waiting_done: 1, waiting_total: 2,
    })
    act(() => vi.advanceTimersByTime(5000))
    expect(state()).toEqual({ stages: {}, progress: {} })
  })
})
