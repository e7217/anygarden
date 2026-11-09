import { expect, test, type Page, type Route } from '@playwright/test'

/**
 * Sidebar project order (#995), exercised in a real browser.
 *
 * jsdom cannot drive dnd-kit's pointer and keyboard sensors, so the
 * drag handle lives here: dragging or keyboard-moving a project
 * reorders the list and saves the caller's full order. Runs against
 * Vite alone with REST and WebSocket stubbed.
 */

const user = { id: 'e2e-user', email: 'e2e@example.com', is_admin: true }
const PROJECTS = [
  { id: 'p0', name: 'Alpha', can_delete: true },
  { id: 'p1', name: 'Bravo', can_delete: true },
  { id: 'p2', name: 'Charlie', can_delete: true },
]

async function fulfillJson(route: Route, status: number, body: unknown) {
  await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
}

/** Stubs the API and records every saved project order. */
async function stubApi(page: Page): Promise<string[][]> {
  const saved: string[][] = []
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
      return fulfillJson(route, 200, { version: '0.23.0' })
    }
    if (pathname === '/api/v1/projects/order' && request.method() === 'PUT') {
      const { project_ids } = request.postDataJSON() as { project_ids: string[] }
      saved.push(project_ids)
      return fulfillJson(route, 200, { project_ids })
    }
    if (pathname === '/api/v1/projects') return fulfillJson(route, 200, PROJECTS)
    return fulfillJson(route, 200, [])
  })
  return saved
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
  await expect(projectNames(page)).toHaveText(['Alpha', 'Bravo', 'Charlie'])
}

const projectNames = (page: Page) =>
  page.getByTestId('sidebar-projects-section').locator('[aria-expanded] > span.truncate')
const handle = (page: Page, name: string) =>
  page.getByRole('button', { name: `Reorder ${name}` })

test.describe('sidebar project order', () => {
  test.beforeEach(async ({ page }) => {
    await page.addInitScript(() => localStorage.setItem('anygarden_locale', 'en'))
    await stubWebSocket(page)
    await page.setViewportSize({ width: 1280, height: 800 })
  })

  test('[ROOM-14] dragging a project by its handle saves the new order', async ({ page }) => {
    const saved = await stubApi(page)
    await signIn(page)

    await page.getByTestId('sidebar-project-drag-p0').hover()
    const from = await handle(page, 'Alpha').boundingBox()
    const to = await handle(page, 'Charlie').boundingBox()
    expect(from).not.toBeNull()
    expect(to).not.toBeNull()
    await page.mouse.move(from!.x + from!.width / 2, from!.y + from!.height / 2)
    await page.mouse.down()
    await page.mouse.move(from!.x + from!.width / 2, to!.y + to!.height / 2, { steps: 8 })
    await page.mouse.move(from!.x + from!.width / 2, to!.y + to!.height, { steps: 4 })
    await page.mouse.up()

    await expect(projectNames(page)).toHaveText(['Bravo', 'Charlie', 'Alpha'])
    expect(saved).toEqual([['p1', 'p2', 'p0']])
  })

  test('[ROOM-14] a project moves with the keyboard from its handle', async ({ page }) => {
    const saved = await stubApi(page)
    await signIn(page)

    // Wait for each step to land: dnd-kit measures the list after the
    // drag starts and after each move, and ignores keys sent sooner.
    const charlie = handle(page, 'Charlie')
    await charlie.focus()
    await page.keyboard.press('Space')
    await expect(charlie).toHaveAttribute('aria-pressed', 'true')
    await page.keyboard.press('ArrowUp')
    // The handle sits in the header row inside the moving project block.
    // Drop only after its slide finishes: dnd-kit re-measures the list
    // mid-transition and can drop back in place.
    const bravo = handle(page, 'Bravo').locator('xpath=../..')
    await expect(bravo).toHaveAttribute('style', /translate/)
    await expect.poll(() => bravo.evaluate(el => el.getAnimations().length)).toBe(0)
    await page.keyboard.press('Space')

    await expect(projectNames(page)).toHaveText(['Alpha', 'Charlie', 'Bravo'])
    expect(saved).toEqual([['p0', 'p2', 'p1']])
  })
})
