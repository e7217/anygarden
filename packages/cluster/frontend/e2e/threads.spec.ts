import { expect, test, type Page, type Route } from '@playwright/test'

/**
 * The thread composer contract, exercised in a real browser.
 *
 * Unit and component tests can show which controls render; they cannot
 * show what actually goes out on the wire. The rule this suite pins down
 * is narrow and easy to regress: a reply composer must address the
 * *top-level* root, and the room composer must not address a thread at
 * all. A mistake either way is invisible in the UI and only surfaces as
 * a server rejection or a reply landing in the wrong place.
 *
 * Like ``auth.spec.ts`` this runs against Vite alone. REST is stubbed,
 * and ``window.WebSocket`` is replaced with a recorder so the frames the
 * app sends can be read back exactly.
 */

const user = { id: 'e2e-user', email: 'e2e@example.com', is_admin: true }
const PROJECT_ID = 'proj-1'
const ROOM_ID = 'room-1'
const ROOT_ID = 'msg-root'
const MY_PARTICIPANT = 'part-me'

interface SentFrame {
  type: string
  content?: string
  thread_root_id?: string
}

declare global {
  interface Window {
    __wsSent: SentFrame[]
    __wsPush: (frame: unknown) => void
  }
}

async function fulfillJson(route: Route, status: number, body: unknown) {
  await route.fulfill({
    status,
    contentType: 'application/json',
    body: JSON.stringify(body),
  })
}

function message(overrides: Record<string, unknown> = {}) {
  return {
    id: ROOT_ID,
    room_id: ROOM_ID,
    participant_id: MY_PARTICIPANT,
    content: 'root message',
    parent_message_id: null,
    root_message_id: null,
    seq: 1,
    created_at: '2026-08-09T00:00:00Z',
    metadata: null,
    ...overrides,
  }
}

/** A room holding one root and one reply, so a thread already exists. */
const HISTORY = [
  message(),
  message({
    id: 'msg-reply',
    content: 'first reply',
    parent_message_id: ROOT_ID,
    root_message_id: ROOT_ID,
    seq: 2,
  }),
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
    if (pathname === '/api/v1/rooms') {
      return fulfillJson(route, 200, [
        {
          id: ROOM_ID,
          project_id: PROJECT_ID,
          name: 'e2e-room',
          is_dm: false,
          parent_room_id: null,
        },
      ])
    }
    if (pathname === `/api/v1/rooms/${ROOM_ID}`) {
      return fulfillJson(route, 200, {
        id: ROOM_ID,
        project_id: PROJECT_ID,
        name: 'e2e-room',
        is_dm: false,
        parent_room_id: null,
        participants: [
          {
            id: MY_PARTICIPANT,
            display_name: 'e2e',
            kind: 'user',
            user_id: user.id,
          },
        ],
      })
    }
    if (pathname === `/api/v1/rooms/${ROOM_ID}/messages`) {
      return fulfillJson(route, 200, HISTORY)
    }

    // Everything else the room shell asks for is empty for this suite.
    return fulfillJson(route, 200, [])
  })
}

/**
 * Replace ``window.WebSocket`` before any app code runs. The fake opens
 * immediately, records every frame the app sends, and exposes a push so a
 * test can deliver server messages.
 */
async function stubWebSocket(page: Page) {
  await page.addInitScript(() => {
    window.__wsSent = []
    const listeners = new Map<string, Set<(e: unknown) => void>>()

    class FakeWebSocket {
      static readonly OPEN = 1
      readyState = 1
      onopen: ((e: unknown) => void) | null = null
      onclose: ((e: unknown) => void) | null = null
      onerror: ((e: unknown) => void) | null = null
      onmessage: ((e: unknown) => void) | null = null

      constructor(_url: string, _protocols?: string | string[]) {
        setTimeout(() => this.onopen?.({}), 0)
      }
      send(data: string) {
        try {
          window.__wsSent.push(JSON.parse(data))
        } catch {
          // Non-JSON frames aren't part of this contract.
        }
      }
      close() {
        this.readyState = 3
      }
      addEventListener(type: string, fn: (e: unknown) => void) {
        if (!listeners.has(type)) listeners.set(type, new Set())
        listeners.get(type)!.add(fn)
      }
      removeEventListener(type: string, fn: (e: unknown) => void) {
        listeners.get(type)?.delete(fn)
      }
    }

    window.__wsPush = (frame: unknown) => {
      const ev = { data: JSON.stringify(frame) }
      for (const fn of listeners.get('message') ?? []) fn(ev)
    }
    ;(window as unknown as { WebSocket: unknown }).WebSocket = FakeWebSocket
  })
}

async function signIn(page: Page) {
  await page.goto('/login')
  await page.locator('#login-email').fill(user.email)
  await page.locator('#login-password').fill('correct-password')
  await page.getByRole('button', { name: 'Sign In' }).click()
  await expect(page).toHaveURL(/\/$/)
}

async function openRoom(page: Page) {
  await signIn(page)
  await page.goto(`/rooms/${ROOM_ID}`)
  await expect(page.getByText('root message')).toBeVisible()
}

/** Frames the app sent, newest last, ignoring typing pings. */
async function sentSends(page: Page): Promise<SentFrame[]> {
  return page.evaluate(() =>
    window.__wsSent.filter(f => f.type === 'send'),
  )
}

const threadTrigger = (page: Page) =>
  page.locator(`[data-thread-trigger="${ROOT_ID}"]`)

test.describe('thread composer contract', () => {
  test.beforeEach(async ({ page }) => {
    await stubWebSocket(page)
    await stubApi(page)
  })

  test('the room composer addresses no thread', async ({ page }) => {
    await openRoom(page)

    await page
      .getByPlaceholder('Type a message... (@ to mention, # for rooms)')
      .fill('a top-level message')
    await page.getByRole('button', { name: 'Send message' }).click()

    await expect.poll(async () => (await sentSends(page)).length).toBe(1)
    const [frame] = await sentSends(page)
    expect(frame.content).toBe('a top-level message')
    expect(frame.thread_root_id).toBeUndefined()
  })

  test('the panel composer addresses the top-level root', async ({ page }) => {
    await openRoom(page)
    await threadTrigger(page).click()

    const panel = page.getByTestId('thread-panel-root')
    await expect(panel).toBeVisible()
    await panel.getByPlaceholder('Reply to thread…').fill('panel reply')
    await panel.getByRole('button', { name: 'Send message' }).click()

    await expect.poll(async () => (await sentSends(page)).length).toBe(1)
    const [frame] = await sentSends(page)
    expect(frame.content).toBe('panel reply')
    expect(frame.thread_root_id).toBe(ROOT_ID)
  })

  test('the inline composer addresses the same root', async ({ page }) => {
    await openRoom(page)
    await page.getByTestId('thread-mode-toggle').click()
    await threadTrigger(page).click()

    const inline = page.getByTestId('thread-inline-root')
    await expect(inline).toBeVisible()
    await inline.getByPlaceholder('Reply to thread…').fill('inline reply')
    await inline.getByRole('button', { name: 'Send message' }).click()

    await expect.poll(async () => (await sentSends(page)).length).toBe(1)
    const [frame] = await sentSends(page)
    expect(frame.content).toBe('inline reply')
    expect(frame.thread_root_id).toBe(ROOT_ID)
  })

  test('opening focuses the reply composer', async ({ page }) => {
    await openRoom(page)
    await threadTrigger(page).click()

    const composer = page
      .getByTestId('thread-panel-root')
      .getByPlaceholder('Reply to thread…')
    await expect(composer).toBeFocused()
  })

  test('Escape closes the thread and returns focus to its trigger', async ({
    page,
  }) => {
    await openRoom(page)
    await threadTrigger(page).click()
    await expect(page.getByTestId('thread-panel-root')).toBeVisible()

    await page.keyboard.press('Escape')

    await expect(page.getByTestId('thread-panel-root')).toHaveCount(0)
    await expect(threadTrigger(page)).toBeFocused()
  })

  test('the close button also returns focus to the trigger', async ({ page }) => {
    await openRoom(page)
    await threadTrigger(page).click()

    await page
      .getByTestId('thread-panel-root')
      .getByRole('button', { name: 'Close thread' })
      .click()

    await expect(page.getByTestId('thread-panel-root')).toHaveCount(0)
    await expect(threadTrigger(page)).toBeFocused()
  })

  test('an unsent reply survives switching layouts', async ({ page }) => {
    // Comparing the two layouts is the toggle's whole purpose, so losing
    // the message mid-comparison is the worst moment to drop it.
    await openRoom(page)
    await threadTrigger(page).click()
    await page
      .getByTestId('thread-panel-root')
      .getByPlaceholder('Reply to thread…')
      .fill('half-written reply')

    await page.getByTestId('thread-mode-toggle').click()

    const inline = page.getByTestId('thread-inline-root')
    await expect(inline).toBeVisible()
    await expect(inline.getByPlaceholder('Reply to thread…')).toHaveValue(
      'half-written reply',
    )
  })

  test('re-clicking the open trigger closes it and discards the draft', async ({
    page,
  }) => {
    // The affordance doubles as a close control. That is still an
    // explicit close, so it must behave like Escape rather than merely
    // hiding the panel and leaving the draft to resurface.
    await openRoom(page)
    await threadTrigger(page).click()
    await page
      .getByTestId('thread-panel-root')
      .getByPlaceholder('Reply to thread…')
      .fill('toggled away')

    await threadTrigger(page).click()
    await expect(page.getByTestId('thread-panel-root')).toHaveCount(0)
    await expect(threadTrigger(page)).toBeFocused()

    await threadTrigger(page).click()
    await expect(
      page.getByTestId('thread-panel-root').getByPlaceholder('Reply to thread…'),
    ).toHaveValue('')
  })

  test('the room title stays readable with the panel open at 1024px', async ({
    page,
  }) => {
    // The control cluster opposite the title is shrink-0, so the title
    // group must grow-then-truncate. Without that it collapsed to ~1.5px
    // once the thread panel narrowed the column.
    await page.setViewportSize({ width: 1024, height: 800 })
    await openRoom(page)
    await threadTrigger(page).click()
    await expect(page.getByTestId('thread-panel-root')).toBeVisible()

    const title = page.getByRole('heading', { name: 'e2e-room', level: 2 })
    await expect(title).toBeVisible()
    const box = await title.boundingBox()
    expect(box).not.toBeNull()
    expect(box!.width).toBeGreaterThan(60)

    // Auxiliary controls yield first; the title never overlaps them.
    const controls = page.getByRole('button', { name: 'Room settings' })
    const controlsBox = await controls.boundingBox()
    expect(controlsBox).not.toBeNull()
    expect(box!.x + box!.width).toBeLessThanOrEqual(controlsBox!.x + 1)
  })

  test('the mobile backdrop closes the thread, restores focus, and drops the draft', async ({
    page,
  }) => {
    // Below md the panel is an overlay with a scrim. Dismissing by scrim
    // is an explicit close and must behave like Escape.
    await page.setViewportSize({ width: 640, height: 800 })
    await openRoom(page)
    await threadTrigger(page).click()
    await page
      .getByTestId('thread-panel-root')
      .getByPlaceholder('Reply to thread…')
      .fill('dismissed by scrim')

    const backdrop = page.getByTestId('thread-panel-backdrop')
    await expect(backdrop).toBeVisible()
    // The scrim spans the viewport and the panel sits on top of its right
    // edge, so click near the left where the scrim is actually exposed.
    await backdrop.click({ position: { x: 10, y: 10 } })

    await expect(page.getByTestId('thread-panel-root')).toHaveCount(0)
    await expect(threadTrigger(page)).toBeFocused()

    await threadTrigger(page).click()
    await expect(
      page.getByTestId('thread-panel-root').getByPlaceholder('Reply to thread…'),
    ).toHaveValue('')
  })

  test('closing a thread discards its draft', async ({ page }) => {
    // The counterpart to the rule above: an explicitly closed thread is a
    // discarded one, so reopening starts clean.
    await openRoom(page)
    await threadTrigger(page).click()
    await page
      .getByTestId('thread-panel-root')
      .getByPlaceholder('Reply to thread…')
      .fill('abandoned reply')

    await page.keyboard.press('Escape')
    await threadTrigger(page).click()

    await expect(
      page.getByTestId('thread-panel-root').getByPlaceholder('Reply to thread…'),
    ).toHaveValue('')
  })
})
