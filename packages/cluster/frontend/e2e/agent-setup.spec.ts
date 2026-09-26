import { expect, test, type Page } from '@playwright/test'

const machine = { id: 'm-setup', name: 'Development machine', hostname: 'dev-host', status: 'online' }
const user = { id: 'setup-admin', email: 'setup@example.com', is_admin: true }

async function setup(page: Page, locale: string, theme: string) {
  await page.addInitScript(({ locale, theme }) => {
    localStorage.setItem('anygarden_token', 'setup-token')
    localStorage.setItem('anygarden_locale', locale)
    localStorage.setItem('anygarden-theme', theme)
  }, { locale, theme })
  await page.route('**/api/v1/**', async route => {
    const { pathname } = new URL(route.request().url())
    let body: unknown = []
    let status = 200
    if (pathname === '/api/v1/auth/me') body = user
    else if (pathname === '/api/v1/machines') body = [machine]
    else if (pathname === `/api/v1/machines/${machine.id}/engines`) body = [{ engine: 'codex-cli', version: '0.154.0' }]
    else if (pathname === '/api/v1/agents/engines/available') body = [{ engine: 'codex-cli', machine_count: 1 }]
    else if (pathname === '/api/v1/agents/engines/codex-cli/models') body = { engine: 'codex-cli', default_model: '', models: [], reasoning_levels: [] }
    else if (pathname === '/api/v1/projects') body = [{ id: 'p-setup', name: 'Product' }]
    else if (pathname === '/api/v1/rooms') body = [{ id: 'r-setup', project_id: 'p-setup', name: 'Planning', is_dm: false }]
    else if (pathname === '/api/v1/system/version') body = { version: '0.19.0' }
    else if (pathname === '/api/v1/agents' && route.request().method() === 'POST') {
      // A rejected request leaves the draft visible and lets the test inspect
      // the exact contract without claiming an engine process was started.
      status = 409
      body = { detail: 'Machine disconnected. Reconnect and try again.' }
    }
    await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
  })
  await page.goto('/admin/machines')
  await page.getByRole('button', { name: locale === 'ko' ? '새 에이전트' : 'New Agent', exact: true }).click()
}

for (const [width, locale, theme] of [[375, 'en', 'light'], [768, 'ko', 'dark'], [1024, 'en', 'dark'], [1440, 'ko', 'light']] as const) {
  test(`agent setup remains usable at ${width}px in ${locale}/${theme}`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 900 })
    await setup(page, locale, theme)
    const dialog = page.getByRole('dialog')
    await expect(dialog).toBeVisible()
    const name = page.locator('#create-agent-name')
    await name.fill('Planning assistant')
    await page.locator('#create-agent-description').fill('Coordinates the product launch')
    const nameBox = await name.boundingBox()
    const engineBox = await page.locator('#create-agent-engine').boundingBox()
    expect(nameBox!.height).toBe(engineBox!.height)
    expect(nameBox!.height).toBe(width < 768 ? 44 : 36)
    await page.locator('#create-agent-permission').selectOption('restricted')
    await dialog.getByRole('checkbox', { name: /Planning/ }).check()
    const create = dialog.getByRole('button', { name: locale === 'ko' ? '에이전트 만들기' : 'Create Agent', exact: true })
    const bounds = await create.boundingBox()
    expect(bounds!.y + bounds!.height).toBeLessThanOrEqual(900)
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width)
    await expect.poll(() => dialog.evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true)
    await page.screenshot({ path: testInfo.outputPath('agent-setup.png') })
    const requestPromise = page.waitForRequest(request => request.method() === 'POST' && request.url().endsWith('/api/v1/agents'))
    await create.click()
    expect((await requestPromise).postDataJSON()).toMatchObject({
      name: 'Planning assistant', machine_id: machine.id, engine: 'codex-cli',
      description: 'Coordinates the product launch', permission_level: 'restricted', rooms: ['r-setup'],
    })
    await expect(dialog.getByRole('alert')).toHaveText('Machine disconnected. Reconnect and try again.')
    await expect(name).toHaveValue('Planning assistant')
    await expect(create).toBeEnabled()
  })
}
