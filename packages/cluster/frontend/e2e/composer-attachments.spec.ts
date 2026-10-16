import { expect, test, type Page, type Route } from '@playwright/test'

/**
 * Composer attachments by drag-and-drop and paste (#872), in a real
 * browser so the native ``DataTransfer`` / ``ClipboardEvent`` paths run.
 *
 * Like ``chat-width.spec.ts`` this runs against Vite alone with REST and
 * WebSocket stubbed. The file upload stub accepts text and answers 415
 * for images, which is the server's policy until #873.
 */

const user = { id: 'e2e-user', email: 'e2e@example.com', is_admin: true }
const PROJECT_ID = 'proj-1'
const ROOM_ID = 'room-1'
const MY_PARTICIPANT = 'part-me'

async function fulfillJson(route: Route, status: number, body: unknown) {
  await route.fulfill({
    status,
    contentType: 'application/json',
    body: JSON.stringify(body),
  })
}

async function stubApi(page: Page, uploads: string[]) {
  await page.route('**/api/v1/**', async route => {
    const request = route.request()
    const { pathname } = new URL(request.url())

    if (pathname === '/api/v1/auth/dev-token') {
      return fulfillJson(route, 404, { detail: 'Dev login disabled' })
    }
    if (pathname === '/api/v1/auth/login') {
      return fulfillJson(route, 200, { token: 'e2e-token', user })
    }
    if (pathname === '/api/v1/auth/me') return fulfillJson(route, 200, user)
    if (pathname === '/api/v1/system/version') {
      return fulfillJson(route, 200, { version: '0.21.0' })
    }
    if (pathname === '/api/v1/projects') {
      return fulfillJson(route, 200, [{ id: PROJECT_ID, name: 'e2e-proj' }])
    }
    const room = {
      id: ROOM_ID,
      project_id: PROJECT_ID,
      name: 'e2e-room',
      is_dm: false,
      parent_room_id: null,
    }
    if (pathname === '/api/v1/rooms') return fulfillJson(route, 200, [room])
    if (pathname === `/api/v1/rooms/${ROOM_ID}`) {
      return fulfillJson(route, 200, {
        ...room,
        participants: [
          { id: MY_PARTICIPANT, display_name: 'e2e', kind: 'user', user_id: user.id },
        ],
      })
    }
    if (pathname === `/api/v1/rooms/${ROOM_ID}/files` && request.method() === 'POST') {
      const body = request.postDataBuffer()?.toString('latin1') ?? ''
      const filename = /filename="([^"]+)"/.exec(body)?.[1] ?? 'upload'
      const mime = /Content-Type: ([^\r\n]+)/.exec(body)?.[1] ?? ''
      uploads.push(filename)
      if (mime.startsWith('image/')) {
        return fulfillJson(route, 415, { detail: `mime not allowed: '${mime}'` })
      }
      return fulfillJson(route, 201, {
        id: `file-${uploads.length}`,
        room_id: ROOM_ID,
        filename,
        storage_name: filename,
        sha256: `sha-${uploads.length}`,
        size_bytes: 5,
        mime,
        uploaded_by: user.id,
        created_at: '2026-10-16T00:00:00Z',
      })
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

async function openRoom(page: Page) {
  await page.goto('/login')
  await page.locator('#login-email').fill(user.email)
  await page.locator('#login-password').fill('correct-password')
  await page.getByRole('button', { name: 'Sign In' }).click()
  await expect(page).toHaveURL(/\/$/)
  await page.goto(`/rooms/${ROOM_ID}`)
  await expect(page.getByTestId('composer-box')).toBeVisible()
}

/** A ``DataTransfer`` built in the page, holding the given files. */
function dataTransfer(page: Page, files: { name: string; type: string }[]) {
  return page.evaluateHandle(specs => {
    const transfer = new DataTransfer()
    for (const spec of specs) transfer.items.add(new File(['hello'], spec.name, { type: spec.type }))
    return transfer
  }, files)
}

let uploads: string[]

test.beforeEach(async ({ page }) => {
  uploads = []
  await page.addInitScript(() => {
    localStorage.setItem('anygarden_locale', 'en')
  })
  await stubWebSocket(page)
  await stubApi(page, uploads)
})

test.describe('composer attachments (#872)', () => {
  test('dropping files attaches each one', async ({ page }) => {
    await openRoom(page)
    const column = page.getByTestId('composer-column')
    const transfer = await dataTransfer(page, [
      { name: 'a.txt', type: 'text/plain' },
      { name: 'b.md', type: 'text/markdown' },
    ])

    await column.dispatchEvent('dragenter', { dataTransfer: transfer })
    await expect(page.getByText('Drop files to attach')).toBeVisible()
    await column.dispatchEvent('drop', { dataTransfer: transfer })

    await expect(page.getByText('Drop files to attach')).toBeHidden()
    await expect(page.getByRole('button', { name: 'Remove a.txt' })).toBeVisible()
    await expect(page.getByRole('button', { name: 'Remove b.md' })).toBeVisible()
    expect(uploads).toEqual(['a.txt', 'b.md'])

    await page.getByRole('button', { name: 'Send message' }).click()
    await expect(page.getByRole('button', { name: 'Remove a.txt' })).toBeHidden()
  })

  test('pasting a screenshot uploads it under a timestamped name', async ({ page }) => {
    await openRoom(page)
    const input = page.getByRole('textbox', { name: 'Message' })
    const transfer = await dataTransfer(page, [{ name: 'image.png', type: 'image/png' }])

    const prevented = await input.evaluate((el, clipboardData) => {
      const event = new ClipboardEvent('paste', { clipboardData, bubbles: true, cancelable: true })
      return !el.dispatchEvent(event)
    }, transfer)

    expect(prevented).toBe(true)
    await expect(page.getByRole('alert')).toHaveText(/^pasted-\d{8}-\d{6}\.png — file type not allowed$/)
    expect(uploads).toHaveLength(1)
    expect(uploads[0]).toMatch(/^pasted-\d{8}-\d{6}\.png$/)
    await expect(input).toHaveValue('')
  })
})
