import { expect, test, type Page } from '@playwright/test'

const machines = [
  { id: 'a', name: 'Machine A', hostname: 'host-a', status: 'online' },
  { id: 'b', name: 'Machine B', hostname: 'host-b', status: 'online' },
]

async function setup(page: Page, options: { holdA?: Promise<void>; failB?: () => boolean }) {
  await page.addInitScript(() => {
    localStorage.setItem('anygarden_token', 'machine-detail-test')
    localStorage.setItem('anygarden_locale', 'en')
  })
  await page.route('**/api/v1/**', async route => {
    const { pathname } = new URL(route.request().url())
    let body: unknown = []
    let status = 200
    if (pathname === '/api/v1/auth/me') body = { id: 'admin', email: 'admin@example.test', is_admin: true }
    else if (pathname === '/api/v1/machines') body = machines
    else if (pathname === '/api/v1/agents/engines/available') body = [{ engine: 'codex-cli', machine_count: 1 }, { engine: 'pi-cli', machine_count: 1 }]
    else if (pathname.startsWith('/api/v1/machines/a/')) {
      await options.holdA
      if (pathname.endsWith('/engines')) body = [{ engine: 'pi-cli', version: '0.85.1' }]
      if (pathname.endsWith('/agents')) body = [{ id: 'agent-a', name: 'A assistant', engine: 'pi-cli', actual_state: 'running', desired_state: 'running', rooms: [] }]
    } else if (pathname.startsWith('/api/v1/machines/b/')) {
      if (pathname.endsWith('/engines')) {
        status = options.failB?.() ? 503 : 200
        body = [{ engine: 'codex-cli', version: '0.154.0' }]
      }
    } else if (pathname.endsWith('/models')) body = { engine: 'codex-cli', models: [], default_model: '', reasoning_levels: [] }
    else if (pathname === '/api/v1/system/version') body = { version: 'test' }
    await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) }).catch(() => {})
  })
  await page.goto('/admin/machines')
}

test('machine selection and creation never reuse another machine’s delayed details', async ({ page }) => {
  let release!: () => void
  const holdA = new Promise<void>(resolve => { release = resolve })
  const aStarted = page.waitForRequest(request => request.url().endsWith('/machines/a/engines'))
  await setup(page, { holdA })
  await aStarted
  await expect(page.getByRole('button', { name: 'New Agent', exact: true })).toBeDisabled()
  await page.getByRole('button', { name: /Machine B host-b/ }).click()
  await expect(page.getByRole('heading', { name: 'Machine B', exact: true })).toBeVisible()
  const create = page.getByRole('button', { name: 'New Agent', exact: true })
  await expect(create).toBeEnabled()
  release()
  await expect(page.getByText('A assistant', { exact: true })).toHaveCount(0)
  await create.click()
  await expect(page.locator('#create-agent-engine')).toHaveValue('codex-cli')
  await expect(page.locator('#create-agent-engine option[value="pi-cli"]')).toHaveCount(0)
})

test('failed machine details clear previous actions and offer a working retry', async ({ page }) => {
  let failB = true
  await setup(page, { failB: () => failB })
  await expect(page.getByText('A assistant', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: /Machine B host-b/ }).click()
  await expect(page.getByRole('alert')).toContainText('Could not load this machine')
  await expect(page.getByText('A assistant', { exact: true })).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'New Agent', exact: true })).toBeDisabled()
  await expect(page.getByRole('button', { name: 'Delete machine', exact: true })).toBeDisabled()
  failB = false
  await page.getByRole('button', { name: 'Try again', exact: true }).click()
  await expect(page.getByRole('button', { name: 'New Agent', exact: true })).toBeEnabled()
  await expect(page.getByRole('alert')).toHaveCount(0)
})
