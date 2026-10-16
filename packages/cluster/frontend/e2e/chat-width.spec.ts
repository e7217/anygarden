import { expect, test, type Locator, type Page, type Route } from '@playwright/test'

/**
 * Chat column geometry (#856), exercised in a real browser.
 *
 * jsdom has no layout, so the two contracts live here: the conversation
 * column widens in steps with the space the side panels leave
 * (768 → 832 → 896px, measured on ``<main>`` through a container query),
 * and agent replies fill that column as unboxed prose while the user's
 * own messages stay right-aligned bubbles.
 *
 * Like ``right-rail.spec.ts`` this runs against Vite alone with REST and
 * WebSocket stubbed.
 */

const user = { id: 'e2e-user', email: 'e2e@example.com', is_admin: true }
const PROJECT_ID = 'proj-1'
const ROOM_ID = 'room-1'
const MY_PARTICIPANT = 'part-me'
const AGENT_PARTICIPANT = 'part-agent'

const LONG_REPLY = [
  'Leaf certificates renew automatically only while the node root stays fixed.',
  '',
  '| Case | Behaviour | What the user sees |',
  '| --- | --- | --- |',
  '| Leaf expires in 7 days | Renews | Nothing |',
  '| Leaf expired | Federation off | Expired badge |',
].join('\n')

function message(id: string, seq: number, participant: string, content: string) {
  return {
    id,
    room_id: ROOM_ID,
    participant_id: participant,
    content,
    parent_message_id: null,
    root_message_id: null,
    seq,
    created_at: `2026-10-16T00:00:0${seq}Z`,
    metadata: null,
  }
}

const HISTORY = [
  message('msg-me', 1, MY_PARTICIPANT, 'root message'),
  message('msg-long', 2, AGENT_PARTICIPANT, LONG_REPLY),
  message('msg-short', 3, AGENT_PARTICIPANT, 'short agent reply'),
]

async function fulfillJson(route: Route, status: number, body: unknown) {
  await route.fulfill({
    status,
    contentType: 'application/json',
    body: JSON.stringify(body),
  })
}

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
            id: AGENT_PARTICIPANT,
            display_name: 'planner',
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
  await expect(page.getByText('short agent reply')).toBeVisible()
}

async function box(locator: Locator) {
  const b = await locator.boundingBox()
  expect(b).not.toBeNull()
  return b!
}

const column = (page: Page) => page.getByTestId('chat-column')
const composer = (page: Page) => page.getByTestId('composer-column')
const columnWidth = async (page: Page) => Math.round((await box(column(page))).width)
const prose = (page: Page, id: string) =>
  page.locator(`[data-message-id="${id}"] [data-message-body]`)

async function collapseLeft(page: Page) {
  await page.getByTestId('sidebar-collapse').click()
  await expect(page.getByTestId('sidebar-collapsed-rail')).toBeVisible()
}

async function collapseRight(page: Page) {
  await page.getByTestId('right-rail-toggle').click()
  await expect(page.getByTestId('right-rail-root')).toHaveAttribute('aria-hidden', 'true')
}

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem('anygarden_locale', 'en')
  })
  await stubWebSocket(page)
  await stubApi(page)
})

test.describe('chat column width (#856)', () => {
  test('widens in steps as the side panels collapse at 1440px', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 })
    await openRoom(page)
    await expect.poll(() => columnWidth(page)).toBe(768)

    await collapseLeft(page)
    await expect.poll(() => columnWidth(page)).toBe(832)

    await collapseRight(page)
    await expect.poll(() => columnWidth(page)).toBe(896)

    await page.getByTestId('sidebar-expand').click()
    await expect.poll(() => columnWidth(page)).toBe(832)
  })

  test('message list and composer share the same edges', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 })
    await openRoom(page)
    for (const step of [collapseLeft, collapseRight]) {
      await step(page)
      await expect.poll(async () => {
        const list = await box(column(page))
        const input = await box(composer(page))
        return Math.abs(list.x - input.x) <= 1 && Math.abs(list.width - input.width) <= 1
      }).toBe(true)
    }
  })

  for (const width of [375, 1024]) {
    test(`keeps the column inside the viewport at ${width}px`, async ({ page }) => {
      await page.setViewportSize({ width, height: 800 })
      await openRoom(page)
      const overflow = await page.evaluate(
        () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
      )
      expect(overflow).toBeLessThanOrEqual(0)
      const b = await box(column(page))
      expect(b.x + b.width).toBeLessThanOrEqual(width + 1)
    })
  }
})

test.describe('agent replies (#856)', () => {
  test('fill the column as unboxed prose regardless of length', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 })
    await openRoom(page)

    const long = await box(prose(page, 'msg-long'))
    const short = await box(prose(page, 'msg-short'))
    expect(Math.abs(long.width - short.width)).toBeLessThanOrEqual(1)
    // Column padding is 24px a side; prose sits flush with it.
    expect(long.width).toBeGreaterThanOrEqual((await columnWidth(page)) - 48 - 1)

    const style = await prose(page, 'msg-short').evaluate(el => {
      const s = getComputedStyle(el)
      return { bg: s.backgroundColor, border: s.borderTopWidth }
    })
    expect(style.bg).toBe('rgba(0, 0, 0, 0)')
    expect(style.border).toBe('0px')
  })

  test('the user message stays a right-aligned bubble', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 })
    await openRoom(page)
    const col = await box(column(page))
    const mine = await box(prose(page, 'msg-me'))
    expect(mine.width).toBeLessThan(col.width * 0.75)
    expect(Math.abs(mine.x + mine.width - (col.x + col.width - 24))).toBeLessThanOrEqual(2)
  })
})
