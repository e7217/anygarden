import { expect, test, type Locator, type Page, type Route } from '@playwright/test'

/**
 * Right-hand slot geometry (#760), exercised in a real browser.
 *
 * jsdom has no layout, so two contracts can only be checked here: the
 * header of whatever occupies the right slot (context rail or thread
 * panel) ends on the same line as the room header, whether that header
 * fits on one row or wraps onto two; and the slot's width follows the
 * resize handle and survives a reload.
 *
 * Like ``threads.spec.ts`` this runs against Vite alone with REST and
 * WebSocket stubbed.
 */

const user = { id: 'e2e-user', email: 'e2e@example.com', is_admin: true }
const PROJECT_ID = 'proj-1'
const ROOM_ID = 'room-1'
const ROOT_ID = 'msg-root'
const MY_PARTICIPANT = 'part-me'

async function fulfillJson(route: Route, status: number, body: unknown) {
  await route.fulfill({
    status,
    contentType: 'application/json',
    body: JSON.stringify(body),
  })
}

const HISTORY = [
  {
    id: ROOT_ID,
    room_id: ROOM_ID,
    participant_id: MY_PARTICIPANT,
    content: 'root message',
    parent_message_id: null,
    root_message_id: null,
    seq: 1,
    created_at: '2026-08-09T00:00:00Z',
    metadata: null,
  },
  {
    id: 'msg-reply',
    room_id: ROOM_ID,
    participant_id: MY_PARTICIPANT,
    content: 'first reply',
    parent_message_id: ROOT_ID,
    root_message_id: ROOT_ID,
    seq: 2,
    created_at: '2026-08-09T00:00:01Z',
    metadata: null,
  },
]

async function stubApi(page: Page) {
  await page.route('**/api/v1/**', async route => {
    const { pathname } = new URL(route.request().url())

    if (pathname === '/api/v1/auth/dev-token') {
      return fulfillJson(route, 404, { detail: 'Dev login disabled' })
    }
    if (pathname === '/api/v1/auth/login') {
      return fulfillJson(route, 200, { token: 'e2e-token', user })
    }
    if (pathname === '/api/v1/auth/me') return fulfillJson(route, 200, user)
    if (pathname === '/api/v1/system/version') {
      return fulfillJson(route, 200, { version: '0.18.0' })
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
          {
            id: 'part-agent',
            display_name: 'Long representative agent name',
            kind: 'agent',
            agent_id: 'agent-1',
            online: false,
          },
        ],
      })
    }
    if (pathname === `/api/v1/rooms/${ROOM_ID}/messages`) {
      return fulfillJson(route, 200, HISTORY)
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
  await expect(page.getByText('root message')).toBeVisible()
}

async function bottom(locator: Locator): Promise<number> {
  const box = await locator.boundingBox()
  expect(box).not.toBeNull()
  return box!.y + box!.height
}

async function width(locator: Locator): Promise<number> {
  const box = await locator.boundingBox()
  expect(box).not.toBeNull()
  return box!.width
}

const roomHeader = (page: Page) => page.getByTestId('room-header')
const rail = (page: Page) => page.getByTestId('right-rail-root')
const railHeader = (page: Page) => page.getByTestId('right-rail-header')
const handle = (page: Page) =>
  rail(page).getByRole('separator', { name: 'Resize context panel' })

test.describe('right slot geometry', () => {
  test.beforeEach(async ({ page }) => {
    await page.addInitScript(() => {
      localStorage.setItem('anygarden_locale', 'en')
      // Pin the rail open regardless of the viewport default.
      if (localStorage.getItem('anygarden_right_sidebar_collapsed') === null) {
        localStorage.setItem('anygarden_right_sidebar_collapsed', 'false')
      }
    })
    await stubWebSocket(page)
    await stubApi(page)
  })

  for (const viewport of [1024, 1280, 1440, 1920]) {
    test(`rail header ends on the room header's line at ${viewport}px`, async ({ page }) => {
      await page.setViewportSize({ width: viewport, height: 800 })
      await openRoom(page)
      await expect(railHeader(page)).toBeVisible()
      await expect
        .poll(async () =>
          Math.abs((await bottom(railHeader(page))) - (await bottom(roomHeader(page)))),
        )
        .toBeLessThanOrEqual(0.5)
    })
  }

  test('the line holds when the room header wraps onto two rows', async ({ page }) => {
    // 1280px with both sidebars open leaves the chat column under the
    // header's 54rem single-row threshold.
    await page.setViewportSize({ width: 1280, height: 800 })
    await openRoom(page)
    const headerBox = await roomHeader(page).boundingBox()
    expect(headerBox!.height).toBeGreaterThan(60)
    expect(Math.abs((await bottom(railHeader(page))) - headerBox!.y - headerBox!.height))
      .toBeLessThanOrEqual(0.5)
  })

  test('the thread panel header also ends on the room header line', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 800 })
    await openRoom(page)
    await page.locator(`[data-thread-trigger="${ROOT_ID}"]`).click()
    const header = page.getByTestId('thread-panel-header')
    await expect(header).toBeVisible()
    await expect
      .poll(async () => Math.abs((await bottom(header)) - (await bottom(roomHeader(page)))))
      .toBeLessThanOrEqual(0.5)
  })

  test('dragging the handle resizes the rail and the width survives a reload', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 800 })
    await openRoom(page)
    expect(await width(rail(page))).toBe(320)

    const box = await handle(page).boundingBox()
    expect(box).not.toBeNull()
    const x = box!.x + box!.width / 2
    const y = box!.y + box!.height / 2
    await page.mouse.move(x, y)
    await page.mouse.down()
    await page.mouse.move(x - 60, y, { steps: 3 })
    await page.mouse.move(x - 120, y, { steps: 3 })
    await page.mouse.up()

    expect(await width(rail(page))).toBe(440)
    // The header line still holds once the chat column narrows.
    expect(Math.abs((await bottom(railHeader(page))) - (await bottom(roomHeader(page)))))
      .toBeLessThanOrEqual(0.5)

    await page.reload()
    await expect(railHeader(page)).toBeVisible()
    expect(await width(rail(page))).toBe(440)
  })

  test('the thread panel takes the rail width', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 800 })
    await page.addInitScript(() => localStorage.setItem('anygarden_right_sidebar_width', '400'))
    await openRoom(page)
    expect(await width(rail(page))).toBe(400)
    await page.locator(`[data-thread-trigger="${ROOT_ID}"]`).click()
    await expect.poll(() => width(page.getByTestId('thread-panel-root'))).toBe(400)
  })

  test('the handle resizes from the keyboard and resets on double click', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 800 })
    await openRoom(page)
    await handle(page).focus()
    await page.keyboard.press('ArrowLeft')
    // Outside a drag the rail keeps its open/close transition, so poll.
    await expect.poll(() => width(rail(page))).toBe(336)
    await expect(handle(page)).toHaveAttribute('aria-valuenow', '336')
    await handle(page).dblclick()
    await expect.poll(() => width(rail(page))).toBe(320)
  })

  test('the handle is absent below the desktop breakpoint', async ({ page }) => {
    await page.setViewportSize({ width: 800, height: 800 })
    await openRoom(page)
    await page.getByTestId('right-rail-toggle').click()
    await expect(railHeader(page)).toBeVisible()
    await expect(handle(page)).toBeHidden()
  })
})
