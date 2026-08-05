import { expect, test, type Page, type Route } from '@playwright/test'

const user = {
  id: 'e2e-user',
  email: 'e2e@example.com',
  is_admin: true,
}

async function fulfillJson(route: Route, status: number, body: unknown) {
  await route.fulfill({
    status,
    contentType: 'application/json',
    body: JSON.stringify(body),
  })
}

/**
 * The first browser scenario verifies the SPA's public authentication
 * contract without requiring a Python server, a database, or an LLM.  Keep
 * route fixtures local to the test so future flows can model their own API
 * state explicitly rather than sharing mutable backend state.
 */
async function stubApi(page: Page, loginStatus = 200) {
  await page.route('**/api/v1/**', async route => {
    const request = route.request()
    const { pathname } = new URL(request.url())

    if (pathname === '/api/v1/auth/dev-token') {
      return fulfillJson(route, 404, { detail: 'Dev login disabled' })
    }

    if (pathname === '/api/v1/auth/login') {
      if (loginStatus !== 200) {
        return fulfillJson(route, loginStatus, {
          detail: 'Invalid email or password',
        })
      }
      return fulfillJson(route, 200, { token: 'e2e-token', user })
    }

    if (pathname === '/api/v1/auth/me') {
      return fulfillJson(route, 200, user)
    }

    // The post-login empty workspace needs only these list endpoints.  The
    // same test still runs the real router, providers, and sidebar code.
    if (pathname === '/api/v1/projects' || pathname === '/api/v1/rooms') {
      return fulfillJson(route, 200, [])
    }

    return fulfillJson(route, 404, { detail: `Unstubbed E2E route: ${pathname}` })
  })
}

test.describe('authentication browser smoke', () => {
  test('signs in, persists the session, and opens the empty workspace', async ({ page }) => {
    await stubApi(page)
    await page.goto('/login')

    await page.locator('#login-email').fill(user.email)
    await page.locator('#login-password').fill('correct-password')
    await page.getByRole('button', { name: 'Sign In' }).click()

    await expect(page).toHaveURL(/\/$/)
    await expect(page.getByRole('heading', { name: 'Welcome to Anygarden' })).toBeVisible()
    await expect
      .poll(() => page.evaluate(() => localStorage.getItem('anygarden_token')))
      .toBe('e2e-token')
  })

  test('keeps the visitor on login and surfaces an authentication error', async ({ page }) => {
    await stubApi(page, 401)
    await page.goto('/login')

    await page.locator('#login-email').fill(user.email)
    await page.locator('#login-password').fill('wrong-password')
    await page.getByRole('button', { name: 'Sign In' }).click()

    await expect(page).toHaveURL(/\/login$/)
    await expect(page.getByText('Invalid email or password')).toBeVisible()
  })
})
