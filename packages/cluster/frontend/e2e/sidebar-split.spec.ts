import { expect, test, type Locator, type Page, type Route } from '@playwright/test'

/**
 * Sidebar projects/agents split (#928), exercised in a real browser.
 *
 * jsdom has no layout, so the geometry lives here: the two sections
 * start evenly split, the boundary follows a drag and survives a
 * reload, and collapsing a section hands its space to the other one.
 * Runs against Vite alone with REST and WebSocket stubbed.
 */

const user = { id: 'e2e-user', email: 'e2e@example.com', is_admin: true }

async function fulfillJson(route: Route, status: number, body: unknown) {
  await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
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
      return fulfillJson(route, 200, { version: '0.23.0' })
    }
    if (pathname === '/api/v1/projects') {
      return fulfillJson(route, 200, [{ id: 'proj-1', name: 'e2e-proj' }])
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

async function signIn(page: Page) {
  await page.goto('/login')
  await page.locator('#login-email').fill(user.email)
  await page.locator('#login-password').fill('correct-password')
  await page.getByRole('button', { name: 'Sign In' }).click()
  await expect(page).toHaveURL(/\/$/)
  await expect(agents(page)).toBeVisible()
}

async function height(locator: Locator): Promise<number> {
  const box = await locator.boundingBox()
  expect(box).not.toBeNull()
  return box!.height
}

const sections = (page: Page) => page.getByTestId('sidebar-sections')
const projects = (page: Page) => page.getByTestId('sidebar-projects-section')
const agents = (page: Page) => page.getByTestId('sidebar-agents-section')
const handle = (page: Page) =>
  page.getByRole('separator', { name: 'Resize projects and agents' })

/** Agents section's share of the split container, as a fraction. */
async function share(page: Page): Promise<number> {
  return (await height(agents(page))) / (await height(sections(page)))
}

test.describe('sidebar projects/agents split', () => {
  test.beforeEach(async ({ page }) => {
    await page.addInitScript(() => localStorage.setItem('anygarden_locale', 'en'))
    await stubWebSocket(page)
    await stubApi(page)
    await page.setViewportSize({ width: 1280, height: 800 })
  })

  test('[ROOM-12] starts evenly split and the drag survives a reload', async ({ page }) => {
    await signIn(page)
    expect(Math.abs((await height(projects(page))) - (await height(agents(page))))).toBeLessThanOrEqual(1)

    const box = await handle(page).boundingBox()
    expect(box).not.toBeNull()
    const x = box!.x + box!.width / 2
    const y = box!.y + box!.height / 2
    const total = await height(sections(page))
    await page.mouse.move(x, y)
    await page.mouse.down()
    await page.mouse.move(x, y - total * 0.1, { steps: 3 })
    await page.mouse.move(x, y - total * 0.2, { steps: 3 })
    await page.mouse.up()

    expect(await share(page)).toBeCloseTo(0.7, 1)
    await expect(handle(page)).toHaveAttribute('aria-valuenow', '70')

    await page.reload()
    await expect(agents(page)).toBeVisible()
    expect(await share(page)).toBeCloseTo(0.7, 1)

    await handle(page).dblclick()
    await expect.poll(() => share(page)).toBeCloseTo(0.5, 1)
  })

  test('[ROOM-12] collapsing a section gives the other its space and hides the handle', async ({ page }) => {
    await signIn(page)
    const total = await height(sections(page))

    await page.getByRole('button', { name: 'Agents' }).click()
    await expect(handle(page)).toHaveCount(0)
    expect(await height(projects(page))).toBeGreaterThan(total * 0.8)

    await page.getByRole('button', { name: 'Agents' }).click()
    await page.getByTestId('sidebar-projects-toggle').click()
    await expect(handle(page)).toHaveCount(0)
    expect(await height(agents(page))).toBeGreaterThan(total * 0.8)

    // The collapse survives a reload; reopening brings the split back.
    await page.reload()
    await expect(page.getByTestId('sidebar-projects-toggle')).toHaveAttribute('aria-expanded', 'false')
    await page.getByTestId('sidebar-projects-toggle').click()
    await expect(handle(page)).toBeVisible()
    expect(await share(page)).toBeCloseTo(0.5, 1)
  })

  test('the handle resizes from the keyboard', async ({ page }) => {
    await signIn(page)
    await handle(page).focus()
    await page.keyboard.press('ArrowUp')
    await page.keyboard.press('ArrowUp')
    await expect(handle(page)).toHaveAttribute('aria-valuenow', '60')
    expect(await share(page)).toBeCloseTo(0.6, 1)
  })
})
