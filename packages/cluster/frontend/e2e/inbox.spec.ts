import { expect, test } from '@playwright/test'

const question = {
  id: 'question:q', type: 'question', project_id: 'p', project_name: 'Project', execution_id: 'run',
  operating_room_id: 'room', operating_room_name: 'Operations', task_id: 'task', task_title: 'Choose display name',
  task_room_id: 'room', task_room_name: 'Research', source_message_id: null, task_href: '/rooms/room', source_href: null,
  status: 'pending', needs_action: true, current_action: 'answer', created_at: '2026-10-01T00:00:00Z', updated_at: '2026-10-01T01:00:00Z',
  question: { id: 'q', execution_id: 'run', operating_room_id: 'room', task_id: 'task', task_room_id: 'room',
    task_title: 'Choose display name', question: 'What name should appear in the document?', status: 'pending',
    can_answer: true, is_current: true, input_revision: 1, created_at: '2026-10-01T00:00:00Z' },
}
for (const width of [375, 1440]) {
  test(`inbox disclosure preserves drafts and links at ${width}px [INB-05]`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 })
    await page.addInitScript(() => {
      localStorage.setItem('anygarden_token', 'test-token')
      localStorage.setItem('anygarden_locale', 'en')
    })
    await page.route('**/api/v1/**', async route => {
      const path = new URL(route.request().url()).pathname
      let body: unknown = []
      if (path === '/api/v1/auth/me') body = { id: 'user', email: 'review@example.test', is_admin: true }
      if (path === '/api/v1/inbox') body = { items: [question], projects: [{ id: 'p', name: 'Project' }] }
      if (path === '/api/v1/projects') body = [{ id: 'p', name: 'Project' }]
      if (path === '/api/v1/system/version') body = { version: 'test' }
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) })
    })
    await page.goto('/inbox')
    const item = page.getByTestId('inbox-item-question:q')
    const summary = item.locator(':scope > summary')
    const input = page.getByTestId('execution-request-answer-q')
    await expect(item).toBeVisible()
    await expect(input).toBeHidden()
    await summary.focus()
    await page.keyboard.press('Enter')
    await expect(input).toBeVisible()
    await input.fill('Anygarden')
    await summary.click()
    await expect(input).toBeHidden()
    const refresh = page.waitForResponse(response => response.url().endsWith('/api/v1/inbox'))
    await page.getByRole('button', { name: 'Refresh', exact: true }).click()
    await refresh
    await summary.click()
    await expect(input).toHaveValue('Anygarden')
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width)
    await page.goto('/inbox?item=question%3Aq')
    await expect(item).toHaveAttribute('open', '')
    await expect(input).toBeVisible()
    // The answer card links back to the originating request.
    await item.getByRole('link', { name: 'Choose display name' }).click()
    await expect(page).toHaveURL(/\/rooms\/room$/)
  })
}

test('pending questions survive a reload and a new sign-in [INB-04]', async ({ page }) => {
  await page.addInitScript(() => {
    if (!localStorage.getItem('anygarden_token')) localStorage.setItem('anygarden_token', 'first-session')
    localStorage.setItem('anygarden_locale', 'en')
  })
  const inboxTokens: string[] = []
  await page.route('**/api/v1/**', async route => {
    const path = new URL(route.request().url()).pathname
    let body: unknown = []
    if (path === '/api/v1/auth/me') body = { id: 'user', email: 'review@example.test', is_admin: true }
    if (path === '/api/v1/inbox') {
      inboxTokens.push(route.request().headers()['authorization'] ?? '')
      body = { items: [question], projects: [{ id: 'p', name: 'Project' }] }
    }
    if (path === '/api/v1/projects') body = [{ id: 'p', name: 'Project' }]
    if (path === '/api/v1/system/version') body = { version: 'test' }
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) })
  })
  const item = page.getByTestId('inbox-item-question:q')
  const attention = page.getByRole('region', { name: 'Needs attention' })
  await page.goto('/inbox')
  await expect(attention.getByTestId('inbox-item-question:q')).toBeVisible()

  await page.reload()
  await expect(attention.getByTestId('inbox-item-question:q')).toBeVisible()

  // Signing in again replaces the session token; the open question is still waiting.
  await page.evaluate(() => localStorage.setItem('anygarden_token', 'second-session'))
  await page.reload()
  await expect(attention.getByTestId('inbox-item-question:q')).toBeVisible()
  await expect(item).toContainText('Choose display name')
  expect(inboxTokens.at(-1)).toContain('second-session')
})
