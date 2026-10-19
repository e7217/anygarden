import { expect, test, type Page } from '@playwright/test'

// #908 — instructions and work files share the Workspace tab; work files can
// be dropped in, downloaded, renamed and deleted.
const TOKEN = 'a'.repeat(64)
const agent = {
  id: 'agent-files', name: 'iac-agent', engine: 'codex-cli', desired_state: 'running', actual_state: 'running',
  restart_policy: 'restart_on_same_machine', placed_on_machine_id: 'm-home', machine_online: true,
  placement: { machine_id: 'm-home', machine_name: 'iac-agent-machine', machine_hostname: 'home', is_local: true },
  agents_md: 'Manage infrastructure.', instructions_mode: 'disk', instructions_revision: 2,
}

async function setup(page: Page, locale: string, theme: string, files: string[], uploads: string[]) {
  await page.addInitScript(({ locale, theme }) => {
    localStorage.setItem('anygarden_token', 'files-test')
    localStorage.setItem('anygarden_locale', locale)
    localStorage.setItem('anygarden-theme', theme)
  }, { locale, theme })
  await page.route('**/api/v1/**', async route => {
    const url = new URL(route.request().url())
    const { pathname } = url
    const method = route.request().method()
    const folder = () => ({
      status: 'ready', machine_id: 'm-home', machine_name: 'iac-agent-machine', agent_state: 'running',
      can_edit: true, can_manage_files: true,
      snapshot: { status: 'ready', cwd: '/home/sfa/.anygarden/agents/agent-files/workspace', engine: 'codex-cli', permission_level: 'trusted',
        reported_at: '2026-10-19T06:36:06Z', runtime_generation: 3, live: true, path: '',
        entries: [{ name: 'memory', kind: 'directory', size: null }, ...files.map(name => ({ name, kind: 'file', size: 2048 }))],
        next_cursor: null, text: null, preview_status: null, edit_token: TOKEN },
    })
    let body: unknown = []
    let contentType = 'application/json'
    if (pathname === '/api/v1/auth/me') body = { id: 'admin', email: 'admin@example.test', is_admin: true }
    else if (pathname === '/api/v1/machines') body = [{ id: 'm-home', name: 'iac-agent-machine', hostname: 'home', status: 'online' }]
    else if (pathname === '/api/v1/machines/m-home/engines') body = [{ engine: 'codex-cli', version: '0.154.0' }]
    else if (pathname === '/api/v1/machines/m-home/agents') body = [{ ...agent, rooms: [] }]
    else if (pathname === '/api/v1/agents' && method === 'GET') body = [agent]
    else if (pathname === '/api/v1/agents/engines/available') body = [{ engine: 'codex-cli', machine_count: 1 }]
    else if (pathname === `/api/v1/agents/${agent.id}/files`) body = [{ path: 'skills/deploy/SKILL.md', content: '# Deploy', updated_at: '2026-10-19T00:00:00Z' }]
    else if (pathname === `/api/v1/agents/${agent.id}/workspace/upload`) {
      uploads.push(JSON.parse(route.request().postData() ?? '{}').path)
      files.push(uploads[uploads.length - 1])
      body = folder()
    } else if (pathname === `/api/v1/agents/${agent.id}/workspace/download`) {
      return route.fulfill({ status: 200, contentType: 'text/markdown', body: '# Report', headers: { 'content-disposition': 'attachment; filename="report.md"' } })
    } else if (pathname === `/api/v1/agents/${agent.id}/workspace`) body = folder()
    else if (pathname === '/api/v1/system/version') body = { version: 'test' }
    else if (pathname.endsWith('/models')) body = { engine: 'codex-cli', models: [], default_model: '', reasoning_levels: [] }
    else if (pathname === `/api/v1/agents/${agent.id}`) body = agent
    await route.fulfill({ status: 200, contentType, body: JSON.stringify(body) }).catch(() => {})
  })
  await page.goto('/admin/machines')
  await page.getByRole('button', { name: locale === 'ko' ? '에이전트 설정' : 'Agent settings' }).first().click()
  await page.getByTestId('agent-menu-settings').click()
  await page.getByTestId('agent-settings-tab-workspace').click()
}

for (const [width, locale, theme] of [[375, 'ko', 'light'], [1440, 'en', 'dark']] as const) {
  test(`work files can be dropped in and downloaded at ${width}px [WS-02]`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 900 })
    const files = ['report.md']
    const uploads: string[] = []
    await setup(page, locale, theme, files, uploads)
    await expect(page.getByTestId('manifest-panel')).toBeVisible()
    const list = page.getByTestId('workspace-file-list')
    await expect(list).toContainText('report.md')

    const dataTransfer = await page.evaluateHandle(() => {
      const transfer = new DataTransfer()
      transfer.items.add(new File(['one'], 'notes.txt', { type: 'text/plain' }))
      transfer.items.add(new File(['two'], 'plan.md', { type: 'text/markdown' }))
      return transfer
    })
    await list.dispatchEvent('dragenter', { dataTransfer })
    await expect(page.getByTestId('workspace-drop-overlay')).toBeVisible()
    await page.screenshot({ path: testInfo.outputPath(`workspace-drop-${width}.png`) })
    await list.dispatchEvent('drop', { dataTransfer })
    await expect(page.getByTestId('workspace-upload-results')).toContainText('plan.md')
    expect(uploads).toEqual(['notes.txt', 'plan.md'])

    const download = page.waitForEvent('download')
    await page.getByTestId('workspace-download-report.md').click()
    expect((await download).suggestedFilename()).toBe('report.md')
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width)
    await page.getByTestId('workspace-file-list').scrollIntoViewIfNeeded()
    await page.screenshot({ path: testInfo.outputPath(`workspace-files-${width}.png`) })
  })
}
