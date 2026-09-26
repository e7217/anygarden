// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, render, screen } from '@testing-library/react'

import { useWebSocket } from './useWebSocket'

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
