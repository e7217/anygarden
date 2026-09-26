import { expect, test, type Page, type Route } from '@playwright/test'

const user = { id: 'e2e-admin', email: 'admin@example.com', is_admin: true }

async function stubApi(page: Page) {
  await page.route('**/api/v1/**', async route => {
    const { pathname } = new URL(route.request().url())
    let status = 200
    let body: unknown = []

    if (pathname === '/api/v1/auth/dev-token') status = 404
    else if (pathname === '/api/v1/auth/login') body = { token: 'e2e-token', user }
    else if (pathname === '/api/v1/auth/me') body = user
    else if (pathname === '/api/v1/system/version') body = { version: '0.18.0' }
    else if (pathname === '/api/v1/system/updates') {
      body = [{ package: 'anygarden', update_available: true }]
    } else if (pathname !== '/api/v1/projects' && pathname !== '/api/v1/rooms' && pathname !== '/api/v1/agents') {
      status = 404
    }

    await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
  })
}

async function signIn(page: Page) {
  await stubApi(page)
  await page.goto('/login')
  await page.locator('#login-email').fill(user.email)
  await page.locator('#login-password').fill('correct-password')
  await page.getByRole('button', { name: 'Sign In' }).click()
  await expect(page).toHaveURL(/\/$/)
}

for (const { name, width, height, mobile } of [
  { name: '320px mobile drawer', width: 320, height: 320, mobile: true },
  { name: 'short desktop sidebar', width: 1024, height: 360, mobile: false },
]) {
  test(`${name} keeps every admin link reachable inside the viewport`, async ({ page }) => {
    await page.setViewportSize({ width, height })
    await signIn(page)

    if (mobile) await page.getByRole('button', { name: 'Open sidebar' }).click()

    const trigger = page.getByRole('button', { name: 'Admin settings, update available' })
    await expect(trigger).toBeVisible()
    if (mobile) await trigger.click()
    else await trigger.press('Enter')

    const menu = page.getByRole('group', { name: 'Admin navigation' })
    await expect(menu).toBeVisible()
    const box = await menu.boundingBox()
    expect(box).not.toBeNull()
    expect(box!.y).toBeGreaterThanOrEqual(0)
    expect(box!.y + box!.height).toBeLessThanOrEqual(height)
    expect(await menu.evaluate(element => element.scrollHeight > element.clientHeight)).toBe(true)

    if (!mobile) {
      await expect(page.getByRole('button', { name: 'Machines' })).toBeFocused()
      await page.keyboard.press('Tab')
      await expect(page.getByRole('button', { name: 'System' })).toBeFocused()
      await page.keyboard.press('Shift+Tab')
      await expect(page.getByRole('button', { name: 'Machines' })).toBeFocused()
      await page.keyboard.press('Escape')
      await expect(trigger).toBeFocused()
      await trigger.press('Enter')
    }

    const topology = page.getByRole('button', { name: 'Topology, experimental feature' })
    if (mobile) await topology.click()
    else await topology.press('Enter')
    await expect(page).toHaveURL(/\/topology$/)
    await expect(menu).not.toBeVisible()
    if (mobile) await expect(page.getByTestId('sidebar-root')).toHaveClass(/-translate-x-full/)
  })
}
