// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, render, screen, waitFor, cleanup, fireEvent } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'

import GuestRoomPage from './GuestRoomPage'

vi.mock('@/hooks/useWebSocket', () => ({
  useWebSocket: () => ({
    messages: [],
    connected: false,
    typingUsers: new Set<string>(),
    send: vi.fn(),
    sendTyping: vi.fn(),
  }),
}))

vi.mock('@/components/ChatArea', () => ({
  default: () => <div data-testid="chat-area" />,
}))

vi.mock('@/components/MessageInput', () => ({
  default: ({ mentionUsers }: { mentionUsers: { description?: string }[] }) => <div data-testid="message-input">{mentionUsers.map(p => p.description).join('|')}</div>,
}))

vi.mock('@/components/ParticipantListPopover', () => ({
  default: ({ participants }: { participants: Record<string, { description?: string }> }) => <div data-testid="participants">{Object.values(participants).map(p => p.description).join('|')}</div>,
}))

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

beforeEach(() => {
  localStorage.clear()
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe('GuestRoomPage auth cleanup', () => {
  it('does not restore a stale prelogin token after guest auth is rejected', async () => {
    localStorage.setItem('anygarden_token', 'guest-token')
    localStorage.setItem('anygarden_token_prelogin', 'expired-user-token')
    localStorage.setItem('anygarden_is_guest', '1')
    localStorage.setItem('anygarden_guest_room_id', 'room-1')
    localStorage.setItem('anygarden_guest_display_name', 'Guest')

    globalThis.fetch = vi.fn().mockResolvedValue(
      jsonResponse({ detail: 'Invalid or expired token' }, 401),
    ) as unknown as typeof fetch

    render(
      <MemoryRouter initialEntries={['/g/room-1']}>
        <Routes>
          <Route path="/g/:roomId" element={<GuestRoomPage />} />
          <Route path="/login" element={<div>login page</div>} />
        </Routes>
      </MemoryRouter>,
    )

    await screen.findByText('login page')

    await waitFor(() => {
      expect(localStorage.getItem('anygarden_token')).toBeNull()
    })
    expect(localStorage.getItem('anygarden_token_prelogin')).toBeNull()
    expect(localStorage.getItem('anygarden_is_guest')).toBeNull()
    expect(localStorage.getItem('anygarden_guest_room_id')).toBeNull()
    expect(localStorage.getItem('anygarden_guest_display_name')).toBeNull()
  })

  it('keeps the conversation and updated descriptions when roster hydration fails, then retries', async () => {
    localStorage.setItem('anygarden_token', 'guest-token')
    localStorage.setItem('anygarden_is_guest', '1')
    localStorage.setItem('anygarden_guest_room_id', 'room-1')
    localStorage.setItem('anygarden_guest_display_name', 'Guest')
    const agent = { id: 'agent-pid', kind: 'agent', display_name: 'Writer', description: 'Original role' }
    const fetch = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(jsonResponse({ name: 'Team room', participants: [agent] }))
      .mockResolvedValueOnce(jsonResponse({}, 503))
      .mockResolvedValueOnce(jsonResponse({ name: 'Team room', participants: [{ ...agent, description: 'Current role' }] }))
    render(<MemoryRouter initialEntries={['/g/room-1']}><Routes>
      <Route path="/g/:roomId" element={<GuestRoomPage />} />
    </Routes></MemoryRouter>)
    await waitFor(() => expect(screen.getByTestId('message-input').textContent).toBe('Original role'))
    expect(screen.getByTestId('participants').textContent).toBe('Original role')
    act(() => window.dispatchEvent(new CustomEvent('anygarden:rooms:settings-changed', {
      detail: { room_id: 'room-1', participants: [{ ...agent, description: 'Current role' }] },
    })))
    await screen.findByRole('alert')
    expect(screen.getByTestId('chat-area')).toBeTruthy()
    expect(screen.getByTestId('message-input').textContent).toBe('Current role')
    expect(screen.getByTestId('participants').textContent).toBe('Current role')
    fireEvent.click(screen.getByRole('alert').querySelector('button')!)
    await waitFor(() => expect(screen.queryByRole('alert')).toBeNull())
    expect(fetch).toHaveBeenCalledTimes(3)
    expect(fetch.mock.calls.every(([url]) => url === '/api/v1/rooms/room-1')).toBe(true)
  })
})
