import { expect, test, type Page } from '@playwright/test'

// #907 — the server never moves a placed agent; the admin moves it here.
const machines = [
  { id: 'm-home', name: 'iac-agent-machine', hostname: 'home', status: 'online' },
  { id: 'm-office', name: 'office-linux', hostname: 'office', status: 'online' },
]
const agent = {
  id: 'agent-iac', name: 'iac-agent', engine: 'codex-cli', desired_state: 'running', actual_state: 'running',
  restart_policy: 'restart_on_same_machine', placed_on_machine_id: 'm-home', machine_online: true,
  placement: { machine_id: 'm-home', machine_name: 'iac-agent-machine', machine_hostname: 'home', is_local: true },
  permission_level: 'trusted',
}

async function setup(page: Page, locale: string, theme: string, moves: Array<{ machine_id: string; move_files: boolean }>) {
  await page.addInitScript(({ locale, theme }) => {
    localStorage.setItem('anygarden_token', 'machine-change-test')
    localStorage.setItem('anygarden_locale', locale)
    localStorage.setItem('anygarden-theme', theme)
  }, { locale, theme })
  await page.route('**/api/v1/**', async route => {
    const { pathname } = new URL(route.request().url())
    const method = route.request().method()
    let body: unknown = []
    if (pathname === '/api/v1/auth/me') body = { id: 'admin', email: 'admin@example.test', is_admin: true }
    else if (pathname === '/api/v1/machines') body = machines
    else if (pathname.endsWith('/engines') && pathname.startsWith('/api/v1/machines/')) body = [{ engine: 'codex-cli', version: '0.154.0' }]
    else if (pathname === '/api/v1/machines/m-home/agents') body = [{ ...agent, rooms: [] }]
    else if (pathname === '/api/v1/agents' && method === 'GET') body = [agent]
    else if (pathname === '/api/v1/agents/engines/available') body = [{ engine: 'codex-cli', machine_count: 2 }]
    else if (pathname === `/api/v1/agents/${agent.id}/machine/move-preview`) {
      body = { can_move: true, reason: null, files: 42, bytes: 12 * 1024 * 1024, limit_bytes: 200 * 1024 * 1024 }
    } else if (pathname === `/api/v1/agents/${agent.id}/machine` && method === 'PUT') {
      moves.push(JSON.parse(route.request().postData() ?? '{}'))
      body = { ...agent, placed_on_machine_id: 'm-office', placement: { ...agent.placement, machine_id: 'm-office', machine_name: 'office-linux' } }
    } else if (pathname.endsWith('/models')) body = { engine: 'codex-cli', models: [], default_model: '', reasoning_levels: [] }
    else if (pathname === '/api/v1/system/version') body = { version: 'test' }
    else if (pathname === `/api/v1/agents/${agent.id}`) body = agent
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) }).catch(() => {})
  })
  await page.goto('/admin/machines')
  await page.getByRole('button', { name: locale === 'ko' ? '에이전트 설정' : 'Agent settings' }).first().click()
  await page.getByTestId('agent-menu-settings').click()
}

for (const [width, locale, theme] of [[375, 'ko', 'dark'], [1440, 'en', 'light']] as const) {
  test(`admin moves an agent to another machine at ${width}px [AGT-10]`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 900 })
    const moves: Array<{ machine_id: string; move_files: boolean }> = []
    await setup(page, locale, theme, moves)
    await expect(page.getByTestId('overview-machine')).toContainText('iac-agent-machine')
    await page.getByTestId('overview-change-machine').click()
    const warning = page.getByTestId('machine-change-warning')
    await expect(warning).toContainText('office-linux')
    await expect(warning.locator('li')).toHaveCount(3)
    // #915 — work files can follow the agent; checked by default.
    await expect(page.getByTestId('machine-change-move-files-checkbox')).toBeChecked()
    await expect(page.getByTestId('machine-change-move-files')).toContainText('42')
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width)
    await page.screenshot({ path: testInfo.outputPath(`machine-change-${width}.png`) })
    await page.getByTestId('machine-change-submit').click()
    await expect.poll(() => moves).toEqual([{ machine_id: 'm-office', move_files: true }])
  })
}
