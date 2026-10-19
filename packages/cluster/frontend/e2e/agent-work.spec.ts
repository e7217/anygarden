import { expect, test, type Page, type Route } from '@playwright/test'

/**
 * Agent work visibility (#880), in a real browser.
 *
 * While an agent works, its typing heartbeat names the running work and its
 * latest narration; once it answers, the reply carries a collapsed work
 * trace. Runs against Vite alone with REST and WebSocket stubbed, like
 * ``chat-width.spec.ts``; the test pushes server frames through the fake
 * socket.
 */

const user = { id: 'e2e-user', email: 'e2e@example.com', is_admin: true }
const PROJECT_ID = 'proj-1'
const ROOM_ID = 'room-1'
const ME = 'part-me'
const AGENT = 'part-agent'

const TRACE = {
  v: 1,
  steps: [
    { narration: 'There are 4 new commits on main. I will update both nodes the same way as before.', activities: { commands: 1 } },
    { narration: 'Both nodes are built. Restarting and checking the federation link.', activities: { commands: 4, file_changes: 2 } },
  ],
  totals: { commands: 5, file_changes: 2 },
  truncated: 0,
}

const TIMED_TRACE = { ...TRACE, steps: [TRACE.steps[1]], totals: { commands: 4 }, duration_ms: 754_000 }

const HISTORY = [
  { id: 'msg-me', room_id: ROOM_ID, participant_id: ME, content: '@planner update both nodes',
    parent_message_id: null, root_message_id: null, seq: 1, created_at: '2026-10-19T00:00:01Z', metadata: null },
  { id: 'msg-reply', room_id: ROOM_ID, participant_id: AGENT, content: 'Both nodes are on 7e54fa7 and the link is active.',
    parent_message_id: null, root_message_id: null, seq: 2, created_at: '2026-10-19T00:00:02Z',
    metadata: { execution_trace: TRACE } },
  // #893 — a newer agent's reply also says how long the work took.
  { id: 'msg-timed', room_id: ROOM_ID, participant_id: AGENT, content: 'Restarted both nodes.',
    parent_message_id: null, root_message_id: null, seq: 3, created_at: '2026-10-19T00:00:03Z',
    metadata: { execution_trace: TIMED_TRACE } },
]

async function fulfillJson(route: Route, status: number, body: unknown) {
  await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
}

async function stubApi(page: Page) {
  await page.route('**/api/v1/**', async route => {
    const { pathname } = new URL(route.request().url())
    if (pathname === '/api/v1/auth/dev-token') return fulfillJson(route, 404, { detail: 'Dev login disabled' })
    if (pathname === '/api/v1/auth/login') return fulfillJson(route, 200, { token: 'e2e-token', user })
    if (pathname === '/api/v1/auth/me') return fulfillJson(route, 200, user)
    if (pathname === '/api/v1/system/version') return fulfillJson(route, 200, { version: '0.21.0' })
    if (pathname === '/api/v1/projects') return fulfillJson(route, 200, [{ id: PROJECT_ID, name: 'e2e-proj' }])
    const room = { id: ROOM_ID, project_id: PROJECT_ID, name: 'e2e-room', is_dm: false, parent_room_id: null }
    if (pathname === '/api/v1/rooms') return fulfillJson(route, 200, [room])
    if (pathname === `/api/v1/rooms/${ROOM_ID}`) {
      return fulfillJson(route, 200, {
        ...room,
        participants: [
          { id: ME, display_name: 'e2e', kind: 'user', user_id: user.id },
          { id: AGENT, display_name: 'planner', kind: 'agent', agent_id: 'agent-1', online: true },
        ],
      })
    }
    if (pathname === `/api/v1/rooms/${ROOM_ID}/messages`) return fulfillJson(route, 200, HISTORY)
    return fulfillJson(route, 200, [])
  })
}

async function stubWebSocket(page: Page) {
  await page.addInitScript(() => {
    const sockets: { onmessage: ((e: unknown) => void) | null }[] = []
    class FakeWebSocket {
      static readonly OPEN = 1
      readyState = 1
      onopen: ((e: unknown) => void) | null = null
      onclose: ((e: unknown) => void) | null = null
      onerror: ((e: unknown) => void) | null = null
      onmessage: ((e: unknown) => void) | null = null
      constructor() {
        sockets.push(this)
        setTimeout(() => this.onopen?.({}), 0)
      }
      send() {}
      close() { this.readyState = 3 }
      addEventListener() {}
      removeEventListener() {}
    }
    const w = window as unknown as { WebSocket: unknown; __serverFrame: (frame: unknown) => void }
    w.WebSocket = FakeWebSocket
    w.__serverFrame = frame => {
      for (const socket of sockets) socket.onmessage?.({ data: JSON.stringify(frame) })
    }
  })
}

async function serverFrame(page: Page, frame: Record<string, unknown>) {
  await page.evaluate(f => (window as unknown as { __serverFrame: (x: unknown) => void }).__serverFrame(f), frame)
}

async function openRoom(page: Page) {
  await page.goto('/login')
  await page.locator('#login-email').fill(user.email)
  await page.locator('#login-password').fill('correct-password')
  await page.getByRole('button', { name: 'Sign In' }).click()
  await expect(page).toHaveURL(/\/$/)
  await page.goto(`/rooms/${ROOM_ID}`)
  await expect(page.locator('[data-message-id="msg-reply"]')).toContainText('Both nodes are on', { timeout: 15_000 })
}

for (const theme of ['light', 'dark'] as const) {
  test.describe(`agent work visibility (#880, ${theme})`, () => {
    test.beforeEach(async ({ page }) => {
      await page.addInitScript(t => {
        localStorage.setItem('anygarden_locale', 'en')
        localStorage.setItem('anygarden-theme', t)
      }, theme)
      await stubWebSocket(page)
      await stubApi(page)
    })

    for (const width of [375, 1440]) {
      test(`shows running work, narration and the reply's trace at ${width}px`, async ({ page }, info) => {
        await page.setViewportSize({ width, height: 900 })
        await openRoom(page)

        await serverFrame(page, {
          type: 'typing', room_id: ROOM_ID, participant_id: AGENT, is_typing: true, stage: 'using_tool',
          activity: 'command', activity_seq: 3,
          narration: 'Both nodes are built. Restarting and checking the federation link.',
        })
        await expect(page.getByText('planner · running a shell command (#3)…')).toBeVisible()
        const narration = page.getByTestId('typing-narration')
        await expect(narration).toHaveText('Both nodes are built. Restarting and checking the federation link.')
        // One line, never wider than the page.
        const box = await narration.boundingBox()
        expect(box!.x + box!.width).toBeLessThanOrEqual(width)
        await page.screenshot({ path: info.outputPath(`working-${theme}-${width}.png`) })

        const toggle = page.getByRole('button', { name: /Work · 5 shell commands · 2 files changed/ })
        await expect(toggle).toHaveAttribute('aria-expanded', 'false')
        await toggle.click()
        await expect(toggle).toHaveAttribute('aria-expanded', 'true')
        await expect(page.getByText('There are 4 new commits on main.', { exact: false })).toBeVisible()
        await expect(page.getByText('4 shell commands · 2 files changed', { exact: true })).toBeVisible()
        expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width)
        await page.screenshot({ path: info.outputPath(`trace-${theme}-${width}.png`), fullPage: true })
      })
    }
  })
}

test.describe('agent working time (#893)', () => {
  test.beforeEach(async ({ page }) => {
    await page.addInitScript(() => {
      localStorage.setItem('anygarden_locale', 'en')
      localStorage.setItem('anygarden-theme', 'light')
    })
    await stubWebSocket(page)
    await stubApi(page)
  })

  const typing = (extra: Record<string, unknown>) => ({
    type: 'typing', room_id: ROOM_ID, participant_id: AGENT, is_typing: true, stage: 'using_tool',
    activity: 'command', ...extra,
  })

  test('counts up from the server\'s working time and survives a reload', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 })
    await openRoom(page)
    const elapsed = page.getByTestId('typing-name').getByTestId('work-elapsed')

    await serverFrame(page, typing({ elapsed_ms: 151_000 }))
    await expect(page.getByTestId('typing-name')).toContainText('planner · running a shell command… · 2m 31s')
    // The timer runs between heartbeats.
    await expect(elapsed).toHaveText('2m 33s', { timeout: 5_000 })

    // A reload starts from the server's count, not from zero.
    await page.reload()
    await expect(page.locator('[data-message-id="msg-reply"]')).toContainText('Both nodes are on', { timeout: 15_000 })
    await serverFrame(page, typing({ elapsed_ms: 3_725_000 }))
    await expect(elapsed).toHaveText('1h 2m')

    // Stopping clears the indicator, and a frame without the field shows no time.
    await serverFrame(page, { type: 'typing', room_id: ROOM_ID, participant_id: AGENT, is_typing: false })
    await expect(page.getByTestId('typing-name')).toHaveCount(0)
    await serverFrame(page, typing({}))
    await expect(page.getByTestId('typing-name')).toHaveText('planner · running a shell command…')
    await expect(elapsed).toHaveCount(0)
  })

  test('keeps the time whole at phone width', async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 800 })
    await openRoom(page)
    await serverFrame(page, typing({
      elapsed_ms: 12_000,
      activity: 'anygarden_tool', narration: 'Checking the federation link on both nodes before restarting.',
    }))
    const elapsed = page.getByTestId('typing-name').getByTestId('work-elapsed')
    await expect(elapsed).toHaveText(/^1[2-4]s$/)
    const box = await elapsed.boundingBox()
    expect(box!.x + box!.width).toBeLessThanOrEqual(375)
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(375)
  })

  test('shows a reply\'s total time in its work header, and nothing for older replies', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 })
    await openRoom(page)
    await expect(page.locator('[data-message-id="msg-timed"]').getByRole('button', { name: 'Work · 12m 34s · 4 shell commands', exact: true }))
      .toBeVisible()
    await expect(page.locator('[data-message-id="msg-reply"]').getByRole('button', { name: 'Work · 5 shell commands · 2 files changed', exact: true }))
      .toBeVisible()
  })

  test('uses Korean units in Korean', async ({ page }) => {
    await page.addInitScript(() => localStorage.setItem('anygarden_locale', 'ko'))
    await page.setViewportSize({ width: 1440, height: 900 })
    await page.goto('/login')
    await page.locator('#login-email').fill(user.email)
    await page.locator('#login-password').fill('correct-password')
    await page.locator('form button[type="submit"]').first().click()
    await expect(page).toHaveURL(/\/$/)
    await page.goto(`/rooms/${ROOM_ID}`)
    await expect(page.locator('[data-message-id="msg-timed"]')).toContainText('작업 과정 · 12분 34초 · ', { timeout: 15_000 })
    await serverFrame(page, typing({ elapsed_ms: 151_000 }))
    await expect(page.getByTestId('typing-name').getByTestId('work-elapsed')).toHaveText(/^2분 3[1-3]초$/)
  })
})
