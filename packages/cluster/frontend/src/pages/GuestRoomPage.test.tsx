// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor, cleanup } from '@testing-library/react'
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
  default: () => <div data-testid="message-input" />,
}))

vi.mock('@/components/ParticipantListPopover', () => ({
  default: () => <div data-testid="participants" />,
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
})
