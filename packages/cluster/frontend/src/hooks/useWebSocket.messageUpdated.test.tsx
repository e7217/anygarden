// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, render, screen } from '@testing-library/react'
import { useWebSocket } from './useWebSocket'
import { MESSAGE_UPDATED_EVENT } from '@/lib/messageEdits'

class FakeWebSocket {
  onopen: ((event: Event) => void) | null = null
  onclose: ((event: CloseEvent) => void) | null = null
  onmessage: ((event: MessageEvent) => void) | null = null
  send = vi.fn()
  close = vi.fn()
  constructor(public url: string, public protocols?: string | string[]) { sockets.push(this) }
}
let sockets: FakeWebSocket[] = []

function Harness() {
  const { messages } = useWebSocket('room-1')
  return <div data-testid="messages">{JSON.stringify(messages.map(m => [m.seq, m.content, m.metadata ?? null]))}</div>
}
const shown = () => JSON.parse(screen.getByTestId('messages').textContent || '[]')

function frame(socket: FakeWebSocket, data: Record<string, unknown>) {
  act(() => socket.onmessage?.({ data: JSON.stringify(data) } as MessageEvent))
}
const base = { id: 'm1', room_id: 'room-1', participant_id: 'p1', seq: 1, created_at: '2026-10-12T00:00:00Z' }

beforeEach(() => {
  sockets = []
  localStorage.clear()
  localStorage.setItem('anygarden_token', 'token-one')
  globalThis.WebSocket = FakeWebSocket as unknown as typeof WebSocket
  globalThis.fetch = vi.fn().mockResolvedValue(
    new Response('[]', { status: 200, headers: { 'Content-Type': 'application/json' } }),
  ) as unknown as typeof fetch
})
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('useWebSocket message_updated', () => {
  it('[MSG-08] replaces an edited message from another tab in place', () => {
    render(<Harness />)
    frame(sockets[0], { type: 'message', ...base, content: 'before' })
    frame(sockets[0], { type: 'message_updated', ...base, content: 'after', metadata: { edited_at: 't' } })
    expect(shown()).toEqual([[1, 'after', { edited_at: 't' }]])
  })

  it('[MSG-08] ignores updates for another room', () => {
    render(<Harness />)
    frame(sockets[0], { type: 'message', ...base, content: 'before' })
    frame(sockets[0], { type: 'message_updated', ...base, room_id: 'room-2', content: 'after' })
    expect(shown()).toEqual([[1, 'before', null]])
  })

  it('[MSG-08] applies a local update dispatched by the editing tab', () => {
    render(<Harness />)
    frame(sockets[0], { type: 'message', ...base, content: 'before' })
    act(() => {
      window.dispatchEvent(new CustomEvent(MESSAGE_UPDATED_EVENT, {
        detail: { type: 'message', ...base, content: '', metadata: { deleted_at: 't' } },
      }))
    })
    expect(shown()).toEqual([[1, '', { deleted_at: 't' }]])
  })
})
