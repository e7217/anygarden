import { expect, test } from '@playwright/test'

// #927 — an integrated node installs the release, restarts in place, and the
// page follows it through the restart by the version it comes back on.
test('[NODE-18] updates this node from "This server" and follows it through the restart', async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem('anygarden_token', 'node-update-test')
    localStorage.setItem('anygarden_locale', 'en')
  })
  let phase: 'before' | 'installing' | 'down' | 'back' = 'before'
  let polls = 0
  const nodeUpdate = (status: string, from_version: string) => ({
    supported: true, reason: null, status, from_version, target_version: status === 'idle' ? null : '0.22.0', error: null,
  })
  await page.route('**/api/v1/**', async route => {
    const { pathname } = new URL(route.request().url())
    const method = route.request().method()
    let body: unknown = []
    if (pathname === '/api/v1/auth/me') body = { id: 'admin', email: 'admin@example.test', is_admin: true }
    else if (pathname === '/api/v1/system/version') body = { version: '0.21.0' }
    else if (pathname === '/api/v1/system/updates') {
      body = [{ package: 'anygarden', current: '0.21.0', latest: '0.22.0', update_available: true, checked_at: '2026-10-01T00:00:00Z', error: null }]
    } else if (pathname === '/api/v1/system/node-update' && method === 'POST') {
      expect(route.request().postDataJSON()).toEqual({ target_version: '0.22.0' })
      phase = 'installing'
      await route.fulfill({ status: 202, contentType: 'application/json', body: JSON.stringify(nodeUpdate('installing', '0.21.0')) })
      return
    } else if (pathname === '/api/v1/system/node-update') {
      if (phase === 'installing') {
        polls += 1
        // One answer while restarting, then the node is unreachable.
        if (polls >= 2) phase = 'down'
        body = nodeUpdate('restarting', '0.21.0')
      } else if (phase === 'down') {
        polls += 1
        if (polls >= 4) phase = 'back'
        await route.abort('connectionrefused')
        return
      } else {
        body = nodeUpdate('idle', phase === 'back' ? '0.22.0' : '0.21.0')
      }
    }
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify(body) }).catch(() => {})
  })

  await page.goto('/admin/machines')
  await page.getByRole('button', { name: /This server/ }).click()
  const panel = page.getByRole('region', { name: 'This server' })
  // The manual command is replaced by the in-place update.
  await expect(panel.getByText('pip install -U anygarden')).toHaveCount(0)
  await panel.getByRole('button', { name: 'Update to 0.22.0 and restart' }).click()
  const dialog = page.getByRole('dialog')
  await expect(dialog).toContainText('Install anygarden 0.22.0 and restart this node?')
  await dialog.getByRole('button', { name: 'Update and restart' }).click()

  await expect(panel.getByRole('status')).toContainText('Installing 0.22.0')
  await expect(panel.getByRole('status')).toContainText('Restarting the node', { timeout: 10_000 })
  await expect(panel.getByRole('status')).toContainText('Updated to 0.22.0', { timeout: 15_000 })
  await expect(panel.getByRole('button', { name: 'Reload' })).toBeVisible()
  await expect(panel.getByRole('button', { name: /and restart/ })).toHaveCount(0)
})
