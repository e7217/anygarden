// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi, afterEach } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'

import { useAuth } from './useAuth'

function LoginHarness() {
  const { login } = useAuth()
  return (
    <button type="button" onClick={() => void login('admin@doorae.dev', 'pw')}>
      login
    </button>
  )
}

function MountHarness() {
  useAuth()
  return <div>mounted</div>
}

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

describe('useAuth storage cleanup', () => {
  it('clears guest/prelogin state when login succeeds', async () => {
    localStorage.setItem('doorae_token', 'old-guest')
    localStorage.setItem('doorae_token_prelogin', 'expired-user')
    localStorage.setItem('doorae_is_guest', '1')
    localStorage.setItem('doorae_guest_room_id', 'room-1')
    localStorage.setItem('doorae_guest_display_name', 'Guest')

    globalThis.fetch = vi.fn().mockImplementation((url: string) => {
      if (url.includes('/api/v1/auth/me')) {
        return Promise.resolve(jsonResponse({}, 403))
      }
      if (url.includes('/api/v1/auth/login')) {
        return Promise.resolve(
          jsonResponse({
            token: 'fresh-user',
            user: { id: 'u1', email: 'admin@doorae.dev', is_admin: true },
          }),
        )
      }
      return Promise.resolve(jsonResponse({}, 404))
    }) as unknown as typeof fetch

    render(<LoginHarness />)
    fireEvent.click(screen.getByRole('button', { name: 'login' }))

    await waitFor(() => {
      expect(localStorage.getItem('doorae_token')).toBe('fresh-user')
    })
    expect(localStorage.getItem('doorae_token_prelogin')).toBeNull()
    expect(localStorage.getItem('doorae_is_guest')).toBeNull()
    expect(localStorage.getItem('doorae_guest_room_id')).toBeNull()
    expect(localStorage.getItem('doorae_guest_display_name')).toBeNull()
  })

  it('clears all stale auth state when /auth/me rejects the saved token', async () => {
    localStorage.setItem('doorae_token', 'expired-user')
    localStorage.setItem('doorae_token_prelogin', 'older-user')
    localStorage.setItem('doorae_is_guest', '1')
    localStorage.setItem('doorae_guest_room_id', 'room-1')
    localStorage.setItem('doorae_guest_display_name', 'Guest')

    globalThis.fetch = vi.fn().mockImplementation((url: string) => {
      if (url.includes('/api/v1/auth/me')) {
        return Promise.resolve(jsonResponse({ detail: 'Invalid or expired token' }, 401))
      }
      return Promise.resolve(jsonResponse({}, 404))
    }) as unknown as typeof fetch

    render(<MountHarness />)

    await waitFor(() => {
      expect(localStorage.getItem('doorae_token')).toBeNull()
    })
    expect(localStorage.getItem('doorae_token_prelogin')).toBeNull()
    expect(localStorage.getItem('doorae_is_guest')).toBeNull()
    expect(localStorage.getItem('doorae_guest_room_id')).toBeNull()
    expect(localStorage.getItem('doorae_guest_display_name')).toBeNull()
  })
})
