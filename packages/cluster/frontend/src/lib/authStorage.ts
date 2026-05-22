export const AUTH_TOKEN_KEY = 'anygarden_token'
export const PRELOGIN_TOKEN_KEY = 'anygarden_token_prelogin'
export const GUEST_FLAG_KEY = 'anygarden_is_guest'
export const GUEST_ROOM_KEY = 'anygarden_guest_room_id'
export const GUEST_DISPLAY_NAME_KEY = 'anygarden_guest_display_name'

const GUEST_SESSION_KEYS = [
  GUEST_FLAG_KEY,
  GUEST_ROOM_KEY,
  GUEST_DISPLAY_NAME_KEY,
] as const

export interface GuestTokenInput {
  token: string
  roomId: string
  displayName: string
  preservePrior?: boolean
}

export function getAuthToken(): string | null {
  return localStorage.getItem(AUTH_TOKEN_KEY)
}

export function isGuestSession(): boolean {
  return localStorage.getItem(GUEST_FLAG_KEY) === '1'
}

export function setRegisteredToken(token: string): void {
  clearGuestSession({ clearToken: false })
  localStorage.setItem(AUTH_TOKEN_KEY, token)
}

export function setGuestToken({
  token,
  roomId,
  displayName,
  preservePrior = true,
}: GuestTokenInput): void {
  const prior = getAuthToken()
  if (preservePrior && prior && !isGuestSession()) {
    localStorage.setItem(PRELOGIN_TOKEN_KEY, prior)
  }

  localStorage.setItem(AUTH_TOKEN_KEY, token)
  localStorage.setItem(GUEST_FLAG_KEY, '1')
  localStorage.setItem(GUEST_ROOM_KEY, roomId)
  localStorage.setItem(GUEST_DISPLAY_NAME_KEY, displayName)
}

export function clearGuestSession({ clearToken = true } = {}): void {
  for (const key of GUEST_SESSION_KEYS) {
    localStorage.removeItem(key)
  }
  localStorage.removeItem(PRELOGIN_TOKEN_KEY)
  if (clearToken) {
    localStorage.removeItem(AUTH_TOKEN_KEY)
  }
}

export function clearAuthSession(): void {
  clearGuestSession({ clearToken: true })
}
