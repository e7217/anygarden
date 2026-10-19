import { expect, test } from '@playwright/test'

// #888 — the former System page now lives on the Machines page as "This server".
test('the old System route lands on Machines, where "This server" shows the update command', async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem('anygarden_token', 'server-entry-test')
    localStorage.setItem('anygarden_locale', 'en')
  })
  await page.route('**/api/v1/**', async route => {
    const { pathname } = new URL(route.request().url())
    let body: unknown = []
    if (pathname === '/api/v1/auth/me') body = { id: 'admin', email: 'admin@example.test', is_admin: true }
    else if (pathname === '/api/v1/system/version') body = { version: '0.21.0' }
    else if (pathname === '/api/v1/system/updates') {
      body = [{ package: 'anygarden', current: '0.21.0', latest: '0.22.0', update_available: true, checked_at: '2026-10-01T00:00:00Z', error: null }]
    }
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify(body) }).catch(() => {})
  })

  await page.goto('/admin/system')
  await expect(page).toHaveURL(/\/admin\/machines$/)
  const entry = page.getByRole('button', { name: /This server/ })
  await expect(entry).toContainText('v0.21.0')
  await expect(entry).toContainText('update available')
  await entry.click()
  const panel = page.getByRole('region', { name: 'This server' })
  await expect(panel.getByText('pip install -U anygarden')).toBeVisible()
  await expect(panel.getByRole('button', { name: 'Check for updates' })).toBeVisible()
})
