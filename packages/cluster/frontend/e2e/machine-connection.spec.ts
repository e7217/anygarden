import { expect, test } from '@playwright/test'

test('the connection guide for a new machine fits the dialog without horizontal scrolling', async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem('anygarden_token', 'machine-connection-test')
    localStorage.setItem('anygarden_locale', 'en')
  })
  await page.route('**/api/v1/**', async route => {
    const request = route.request()
    const { pathname } = new URL(request.url())
    let body: unknown = []
    if (pathname === '/api/v1/auth/me') body = { id: 'admin', email: 'admin@example.test', is_admin: true }
    else if (pathname === '/api/v1/machines' && request.method() === 'POST') {
      body = { id: '0419e4d5-07f1-4ce9-ba4b-adf2e2534b9d', name: 'iac-agent-machine', status: 'offline', machine_token: 'mch_tVpK0zxZKbUmOGLogGRNkdudmu-spZL8MEMCmuRk_3U' }
    } else if (pathname === '/api/v1/system/version') body = { version: 'test' }
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify(body) }).catch(() => {})
  })
  await page.setViewportSize({ width: 720, height: 900 })
  await page.goto('/admin/machines')
  await page.getByRole('button', { name: 'Register machine' }).first().click()
  await page.locator('#register-machine-name').fill('iac-agent-machine')
  await page.getByRole('button', { name: 'Register', exact: true }).click()

  const dialog = page.getByRole('dialog')
  await expect(dialog.getByText(/^agmachine1\./)).toBeVisible()
  const overflow = await dialog.evaluate(element => element.scrollWidth - element.clientWidth)
  expect(overflow).toBeLessThanOrEqual(0)
})
