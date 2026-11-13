import { expect, test, type Page, type Route } from '@playwright/test'

/**
 * While a room's history is still on its way, the chat shows a loading
 * placeholder rather than the "No messages yet" prompt. Runs against Vite
 * alone with REST and WebSocket stubbed; the messages request is held open
 * until the test releases it.
 */

const user = { id: 'e2e-user', email: 'e2e@example.com', is_admin: true }
const PROJECT_ID = 'proj-1'
const ROOM_ID = 'room-1'
const MY_PARTICIPANT = 'part-me'

const room = { id: ROOM_ID, project_id: PROJECT_ID, name: 'e2e-room', is_dm: false, parent_room_id: null }

async function fulfillJson(route: Route, status: number, body: unknown) {
  await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
}

async function stubApi(page: Page, history: Promise<unknown[]>) {
  await page.route('**/api/v1/**', async route => {
    const { pathname } = new URL(route.request().url())
    if (pathname === '/api/v1/auth/dev-token') return fulfillJson(route, 404, { detail: 'Dev login disabled' })
    if (pathname === '/api/v1/auth/login') return fulfillJson(route, 200, { token: 'e2e-token', user })
    if (pathname === '/api/v1/auth/me') return fulfillJson(route, 200, user)
    if (pathname === '/api/v1/system/version') return fulfillJson(route, 200, { version: '0.18.0' })
    if (pathname === '/api/v1/projects') return fulfillJson(route, 200, [{ id: PROJECT_ID, name: 'e2e-proj' }])
    if (pathname === '/api/v1/rooms') return fulfillJson(route, 200, [room])
    if (pathname === `/api/v1/rooms/${ROOM_ID}`) {
      return fulfillJson(route, 200, {
        ...room,
        participants: [{ id: MY_PARTICIPANT, display_name: 'e2e', kind: 'user', user_id: user.id }],
      })
    }
    if (pathname === `/api/v1/rooms/${ROOM_ID}/messages`) {
      const rows = await history
      return fulfillJson(route, 200, rows).catch(() => {})
    }
    return fulfillJson(route, 200, [])
  })
}

async function stubWebSocket(page: Page) {
  await page.addInitScript(() => {
    class FakeWebSocket {
      static readonly OPEN = 1
      readyState = 1
      onopen: ((e: unknown) => void) | null = null
      onclose: ((e: unknown) => void) | null = null
      onerror: ((e: unknown) => void) | null = null
      onmessage: ((e: unknown) => void) | null = null
      constructor() {
        setTimeout(() => this.onopen?.({}), 0)
      }
      send() {}
      close() {
        this.readyState = 3
      }
      addEventListener() {}
      removeEventListener() {}
    }
    ;(window as unknown as { WebSocket: unknown }).WebSocket = FakeWebSocket
  })
}

async function signInAndOpenRoom(page: Page) {
  await page.goto('/login')
  await page.locator('#login-email').fill(user.email)
  await page.locator('#login-password').fill('correct-password')
  await page.getByRole('button', { name: 'Sign In' }).click()
  await expect(page).toHaveURL(/\/$/)
  await page.goto(`/rooms/${ROOM_ID}`)
}

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('anygarden_locale', 'en'))
  await stubWebSocket(page)
})

test('shows a loading placeholder until the history arrives', async ({ page }) => {
  let release!: (rows: unknown[]) => void
  await stubApi(page, new Promise(resolve => { release = resolve }))
  await signInAndOpenRoom(page)

  const loading = page.getByTestId('chat-history-loading')
  await expect(loading).toBeVisible()
  await expect(loading).toHaveText('Loading messages…')
  await expect(page.getByText('No messages yet')).toHaveCount(0)

  release([{
    id: 'msg-1', room_id: ROOM_ID, participant_id: MY_PARTICIPANT, content: 'first message',
    parent_message_id: null, root_message_id: null, seq: 1, created_at: '2026-11-13T00:00:01Z', metadata: null,
  }])
  await expect(page.getByText('first message')).toBeVisible()
  await expect(loading).toHaveCount(0)
})

test('falls back to the empty-room prompt when the history is empty', async ({ page }) => {
  await stubApi(page, Promise.resolve([]))
  await signInAndOpenRoom(page)
  await expect(page.getByText('No messages yet')).toBeVisible()
  await expect(page.getByTestId('chat-history-loading')).toHaveCount(0)
})
