// @vitest-environment jsdom
import { beforeEach, describe, expect, it } from 'vitest'

import {
  clearAuthSession,
  clearGuestSession,
  getAuthToken,
  setGuestToken,
  setRegisteredToken,
} from './authStorage'

beforeEach(() => {
  localStorage.clear()
})

describe('authStorage', () => {
  it('stores a registered-user token and clears stale guest state', () => {
    localStorage.setItem('doorae_token', 'old-guest')
    localStorage.setItem('doorae_token_prelogin', 'expired-user')
    localStorage.setItem('doorae_is_guest', '1')
    localStorage.setItem('doorae_guest_room_id', 'room-1')
    localStorage.setItem('doorae_guest_display_name', 'Guest')

    setRegisteredToken('fresh-user')

    expect(getAuthToken()).toBe('fresh-user')
    expect(localStorage.getItem('doorae_token_prelogin')).toBeNull()
    expect(localStorage.getItem('doorae_is_guest')).toBeNull()
    expect(localStorage.getItem('doorae_guest_room_id')).toBeNull()
    expect(localStorage.getItem('doorae_guest_display_name')).toBeNull()
  })

  it('stores a guest token and preserves the prior registered-user token once', () => {
    localStorage.setItem('doorae_token', 'current-user')

    setGuestToken({
      token: 'guest-one',
      roomId: 'room-1',
      displayName: 'Guest One',
    })

    expect(getAuthToken()).toBe('guest-one')
    expect(localStorage.getItem('doorae_token_prelogin')).toBe('current-user')
    expect(localStorage.getItem('doorae_is_guest')).toBe('1')
    expect(localStorage.getItem('doorae_guest_room_id')).toBe('room-1')
    expect(localStorage.getItem('doorae_guest_display_name')).toBe('Guest One')

    setGuestToken({
      token: 'guest-two',
      roomId: 'room-2',
      displayName: 'Guest Two',
    })

    expect(getAuthToken()).toBe('guest-two')
    expect(localStorage.getItem('doorae_token_prelogin')).toBe('current-user')
    expect(localStorage.getItem('doorae_guest_room_id')).toBe('room-2')
    expect(localStorage.getItem('doorae_guest_display_name')).toBe('Guest Two')
  })

  it('clears guest state without restoring the prelogin token', () => {
    localStorage.setItem('doorae_token', 'guest')
    localStorage.setItem('doorae_token_prelogin', 'expired-user')
    localStorage.setItem('doorae_is_guest', '1')
    localStorage.setItem('doorae_guest_room_id', 'room-1')
    localStorage.setItem('doorae_guest_display_name', 'Guest')

    clearGuestSession({ clearToken: true })

    expect(getAuthToken()).toBeNull()
    expect(localStorage.getItem('doorae_token_prelogin')).toBeNull()
    expect(localStorage.getItem('doorae_is_guest')).toBeNull()
    expect(localStorage.getItem('doorae_guest_room_id')).toBeNull()
    expect(localStorage.getItem('doorae_guest_display_name')).toBeNull()
  })

  it('clears the entire auth session', () => {
    localStorage.setItem('doorae_token', 'guest')
    localStorage.setItem('doorae_token_prelogin', 'expired-user')
    localStorage.setItem('doorae_is_guest', '1')
    localStorage.setItem('doorae_guest_room_id', 'room-1')
    localStorage.setItem('doorae_guest_display_name', 'Guest')

    clearAuthSession()

    expect(getAuthToken()).toBeNull()
    expect(localStorage.getItem('doorae_token_prelogin')).toBeNull()
    expect(localStorage.getItem('doorae_is_guest')).toBeNull()
    expect(localStorage.getItem('doorae_guest_room_id')).toBeNull()
    expect(localStorage.getItem('doorae_guest_display_name')).toBeNull()
  })
})
