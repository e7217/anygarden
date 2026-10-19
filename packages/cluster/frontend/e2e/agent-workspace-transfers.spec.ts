import { expect, test } from '@playwright/test'

// #914 — files above one machine frame and folder zips go over HTTP.
const TOKEN = 'b'.repeat(64)
const agent = {
  id: 'agent-big', name: 'data-agent', engine: 'codex-cli', desired_state: 'running', actual_state: 'running',
  restart_policy: 'restart_on_same_machine', placed_on_machine_id: 'm1', machine_online: true,
  placement: { machine_id: 'm1', machine_name: 'gpu-box', machine_hostname: 'gpu', is_local: false },
  agents_md: '', instructions_mode: 'disk', instructions_revision: 0,
}

test('a large drop streams to the machine and a folder downloads as zip [WS-07]', async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem('anygarden_token', 'transfer-test')
    localStorage.setItem('anygarden_locale', 'en')
  })
  const uploads: Array<{ path: string | null; size: number }> = []
  await page.route('**/api/v1/**', async route => {
    const url = new URL(route.request().url())
    const { pathname } = url
    const method = route.request().method()
    const folder = {
      status: 'ready', machine_id: 'm1', machine_name: 'gpu-box', agent_state: 'running',
      can_edit: true, can_manage_files: true, can_transfer: true,
      transfer_max_bytes: 20 * 1024 * 1024, archive_max_bytes: 200 * 1024 * 1024,
      snapshot: { status: 'ready', cwd: '/data/agents/agent-big/workspace', engine: 'codex-cli', permission_level: 'standard',
        reported_at: '2026-10-19T06:00:00Z', runtime_generation: 1, live: true, path: '',
        entries: [{ name: 'results', kind: 'directory', size: null }], next_cursor: null, text: null, preview_status: null, edit_token: TOKEN },
    }
    let body: unknown = []
    if (pathname === '/api/v1/auth/me') body = { id: 'admin', email: 'admin@example.test', is_admin: true }
    else if (pathname === '/api/v1/machines') body = [{ id: 'm1', name: 'gpu-box', hostname: 'gpu', status: 'online' }]
    else if (pathname === '/api/v1/machines/m1/engines') body = [{ engine: 'codex-cli', version: '0.154.0' }]
    else if (pathname === '/api/v1/machines/m1/agents') body = [{ ...agent, rooms: [] }]
    else if (pathname === '/api/v1/agents' && method === 'GET') body = [agent]
    else if (pathname === '/api/v1/agents/engines/available') body = [{ engine: 'codex-cli', machine_count: 1 }]
    else if (pathname === `/api/v1/agents/${agent.id}/workspace/upload-stream`) {
      uploads.push({ path: url.searchParams.get('path'), size: route.request().postDataBuffer()?.length ?? 0 })
      body = { status: 'ready' }
    } else if (pathname === `/api/v1/agents/${agent.id}/workspace/archive`) {
      return route.fulfill({ status: 200, contentType: 'application/zip', body: Buffer.from('PK\x05\x06' + '\0'.repeat(18)) })
    } else if (pathname === `/api/v1/agents/${agent.id}/workspace`) body = folder
    else if (pathname === '/api/v1/system/version') body = { version: 'test' }
    else if (pathname.endsWith('/models')) body = { engine: 'codex-cli', models: [], default_model: '', reasoning_levels: [] }
    else if (pathname === `/api/v1/agents/${agent.id}`) body = agent
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) }).catch(() => {})
  })
  await page.goto('/admin/machines')
  await page.getByRole('button', { name: 'Agent settings' }).first().click()
  await page.getByTestId('agent-menu-settings').click()
  await page.getByTestId('agent-settings-tab-workspace').click()
  const list = page.getByTestId('workspace-file-list')
  await expect(list).toContainText('results')

  const transfer = await page.evaluateHandle(() => {
    const data = new DataTransfer()
    data.items.add(new File([new Uint8Array(3 * 1024 * 1024)], 'dataset.bin'))
    return data
  })
  await list.dispatchEvent('drop', { dataTransfer: transfer })
  await expect(page.getByTestId('workspace-upload-results')).toContainText('dataset.binUploaded')
  expect(uploads).toEqual([{ path: 'dataset.bin', size: 3 * 1024 * 1024 }])

  const download = page.waitForEvent('download')
  await page.getByTestId('workspace-zip-results').click()
  expect((await download).suggestedFilename()).toBe('results.zip')
})
