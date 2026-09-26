import { expect, test, type Page } from '@playwright/test'

const authority = 'authority-id'
const channel = 'shared-channel'
const prefix = `/api/v1/shared-channels/${authority}/${channel}`
const rootMessage = {
  message_id: 'source-message', authority_node_id: authority, channel_id: channel,
  actor: { node_id: authority, principal_id: 'member', kind: 'human' }, actor_name: 'Mina',
  seq: 120, thread_root_id: null, confirmed: true, text: 'Review the release plan', created_at: '2026-09-26T10:00:00Z',
}
const target = { node_id: 'remote-server-id', agent_id: 'remote-agent-id', name: 'Reviewer', node_name: null, server_label: 'studio.example:8443', is_local: false, description: 'Reviews architecture and release plans', can_execute: true, unavailable_code: null }
const room = { id: 'shared-room', project_id: 'project', name: 'Shared releases', is_dm: false, shared_channel: { authority_node_id: authority, channel_id: channel } }

async function setup(page: Page, locale = 'en', theme = 'light') {
  await page.addInitScript(({ locale, theme }) => {
    localStorage.setItem('anygarden_token', 'shared-member-test')
    localStorage.setItem('anygarden_locale', locale)
    localStorage.setItem('anygarden-theme', theme)
  }, { locale, theme })
  const requests: { path: string; method: string; body?: Record<string, unknown> }[] = []
  const forbidden: string[] = []
  let state = 'requested'
  let revision = 1
  let requested = false
  let pendingId: string | null = null
  let fail = false
  let messages = [rootMessage]
  let hasMore = true
  await page.routeWebSocket('**/ws/rooms/**', ws => { forbidden.push(ws.url()); ws.close() })
  await page.route('**/api/v1/**', async route => {
    const request = route.request()
    const url = new URL(request.url())
    const path = url.pathname
    requests.push({ path, method: request.method(), ...(request.method() === 'POST' ? { body: request.postDataJSON() } : {}) })
    let body: unknown = []
    let status = 200
    if (path === '/api/v1/auth/me') body = { id: 'member', email: 'member@example.test', is_admin: false }
    else if (path === '/api/v1/system/version') body = { version: 'test' }
    else if (path === '/api/v1/projects') body = [{ id: 'project', name: 'Product' }]
    else if (path === '/api/v1/rooms') body = url.searchParams.get('is_dm') === 'true' ? [] : [room, { ...room, id: 'other-room', name: 'Other shared room', shared_channel: { authority_node_id: authority, channel_id: 'other-channel' } }]
    else if (path === '/api/v1/rooms/shared-room/read' || path === '/api/v1/rooms/other-room/read') body = { room_id: path.split('/')[4], last_read_message_seq: 150 }
    else if (/^\/api\/v1\/rooms\/(shared-room|other-room)(\/|$)/.test(path)) { forbidden.push(path); status = 409 }
    else if (path === prefix) {
      if (fail) { status = 503; body = { code: 'CONNECTION_FAILED' } }
      else {
        let pageMessages = messages
        if (url.searchParams.has('before_seq')) { pageMessages = [{ ...rootMessage, message_id: 'older-message', seq: 1, text: 'Earlier discussion' }]; hasMore = false }
        if (url.searchParams.has('after_seq')) pageMessages = messages.filter(message => message.seq > Number(url.searchParams.get('after_seq')))
        body = {
          authority_node_id: authority, channel_id: channel, applied_seq: 150, participants: [], messages: pageMessages,
          permissions: { can_send: true, can_delegate: true }, targets: [target, { ...target, agent_id: 'offline-agent', name: 'Offline reviewer', can_execute: false, unavailable_code: 'MACHINE_OFFLINE' }],
          delegations: requested ? [{ delegation_id: 'delegation-id', task_id: 'task-id', source_message_id: rootMessage.message_id, requester: rootMessage.actor, executor: { node_id: target.node_id, agent_id: target.agent_id }, revision, state, process_state: state === 'running' ? 'running' : 'not_started', task_status: 'todo', result_markdown: state === 'completed' ? '**Release reviewed.**' : null, error: null, can_cancel: ['requested', 'running'].includes(state) }] : [],
          submissions: pendingId ? [{ request_id: pendingId, kind: 'task.request', state: 'unconfirmed', receipt: null, error_code: null, source_message_id: rootMessage.message_id, delegation_id: 'delegation-id', executor: { node_id: target.node_id, agent_id: target.agent_id }, can_retry: true }] : [],
          cursor: { oldest_seq: pageMessages[0]?.seq ?? null, newest_seq: pageMessages.at(-1)?.seq ?? null, has_more_before: hasMore },
        }
      }
    } else if (path.endsWith('/other-channel')) body = { authority_node_id: authority, channel_id: 'other-channel', applied_seq: 1, participants: [], messages: [{ ...rootMessage, message_id: 'other-source', channel_id: 'other-channel', text: 'Other room conversation' }], permissions: { can_send: false, can_delegate: false }, targets: [], delegations: [], submissions: [], cursor: { oldest_seq: 120, newest_seq: 120, has_more_before: false } }
    else if (path === `${prefix}/delegations`) { pendingId = request.postDataJSON().request_id; body = { request_id: pendingId, state: 'unconfirmed', receipt: null, error_code: null } }
    else if (path.includes('/submissions/') && path.endsWith('/retry')) { const id = pendingId; pendingId = null; requested = true; body = { request_id: id, state: 'confirmed', receipt: null, error_code: null } }
    else if (path.endsWith('/cancel')) { state = 'cancel_requested'; revision += 1; body = { request_id: request.postDataJSON().request_id, state: 'confirmed', receipt: null, error_code: null } }
    else if (path === `${prefix}/messages`) {
      const input = request.postDataJSON()
      messages = [...messages, { ...rootMessage, message_id: `sent-${messages.length}`, text: input.text, seq: 120 + messages.length, thread_root_id: input.thread_root_id }]
      body = { request_id: input.request_id, state: 'confirmed', receipt: null, error_code: null }
    }
    await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
  })
  return { requests, forbidden, setState: (next: string) => { requested = true; pendingId = null; state = next; revision += 1 }, setFail: (next: boolean) => { fail = next } }
}

for (const width of [375, 1440]) {
  test(`shared room message delegation, recovery and cancellation at ${width}px`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 900 })
    const fixture = await setup(page, 'en', width === 1440 ? 'dark' : 'light')
    const errors: string[] = []
    page.on('pageerror', error => errors.push(error.message))
    await page.goto('/rooms/shared-room')
    await expect(page.getByText(rootMessage.text, { exact: true })).toBeVisible()
    expect(fixture.requests.find(item => item.path === prefix)?.method).toBe('GET')
    await page.getByRole('button', { name: 'Delegate to an agent' }).click()
    const dialog = page.getByRole('dialog')
    await expect(dialog.getByRole('radio', { name: /^Offline reviewer/ })).toBeDisabled()
    await expect(dialog.getByText('Connection: studio.example:8443', { exact: true }).first()).toBeVisible()
    await page.screenshot({ path: testInfo.outputPath('shared-target-picker.png') })
    await dialog.getByRole('radio', { name: /^Reviewer / }).check()
    await dialog.getByRole('button', { name: 'Delegate message', exact: true }).click()
    await expect(dialog).toBeHidden()
    await expect(page.locator('[data-shared-message="source-message"]')).toBeFocused()
    await expect(page.getByText('Waiting for delivery confirmation', { exact: true })).toBeVisible()
    const command = fixture.requests.find(item => item.path === `${prefix}/delegations`)!.body!
    expect(Object.keys(command).sort()).toEqual(['executor', 'request_id', 'source_message_id'])
    await page.reload()
    await expect(page.getByRole('button', { name: 'Retry delivery' })).toBeVisible()
    await page.getByRole('button', { name: 'Retry delivery' }).click()
    expect(fixture.requests.some(item => item.path.endsWith(`/submissions/${command.request_id}/retry`))).toBe(true)
    fixture.setState('running')
    await page.getByRole('button', { name: 'Refresh', exact: true }).click()
    await expect(page.getByText('Running', { exact: true })).toBeVisible()
    await page.getByRole('button', { name: 'Request stop', exact: true }).click()
    await expect(page.getByText('Stop requested', { exact: true })).toBeVisible()
    await expect(page.getByText('Stopped', { exact: true })).toHaveCount(0)
    fixture.setState('cancelled')
    await page.getByRole('button', { name: 'Refresh', exact: true }).click()
    await expect(page.getByText('Stopped', { exact: true })).toBeVisible()
    await page.getByRole('button', { name: 'Load earlier messages' }).click()
    await expect(page.getByText('Earlier discussion', { exact: true })).toBeVisible()
    fixture.setFail(true)
    await page.getByRole('button', { name: 'Refresh', exact: true }).click()
    await expect(page.getByRole('alert')).toContainText('Could not refresh')
    await expect(page.getByText(rootMessage.text, { exact: true })).toBeVisible()
    fixture.setFail(false)
    await page.getByRole('button', { name: 'Try again', exact: true }).click()
    await page.getByRole('textbox', { name: 'Message', exact: true }).fill('Thank you for the review')
    await page.getByRole('button', { name: 'Send message', exact: true }).click()
    await expect(page.getByText('Thank you for the review', { exact: true })).toBeVisible()
    await page.screenshot({ path: testInfo.outputPath('shared-room.png'), fullPage: true })
    expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false)
    if (width === 375) {
      const tooSmall = await page.locator('main button:visible').evaluateAll(buttons => buttons.filter(button => { const box = button.getBoundingClientRect(); return box.width < 44 || box.height < 44 }).map(button => button.textContent))
      expect(tooSmall).toEqual([])
    }
    expect(fixture.forbidden).toEqual([])
    expect(errors).toEqual([])
  })
}

test('shared room keyboard dialog, language and room navigation', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 768, height: 900 })
  const fixture = await setup(page, 'ko')
  await page.goto('/rooms/shared-room')
  const trigger = page.getByRole('button', { name: '에이전트에게 맡기기' })
  await trigger.focus()
  await page.keyboard.press('Enter')
  await expect(page.getByRole('dialog')).toBeVisible()
  for (let i = 0; i < 8; i++) {
    await page.keyboard.press('Tab')
    expect(await page.getByRole('dialog').evaluate(dialog => dialog.contains(document.activeElement))).toBe(true)
  }
  await page.keyboard.press('Escape')
  await expect(page.getByRole('dialog')).toBeHidden()
  await expect(trigger).toBeFocused()
  await page.getByRole('button', { name: 'Product', exact: true }).click()
  let release!: () => void
  let waiting = false
  const held = new Promise<void>(resolve => { release = resolve })
  await page.route(`**${prefix}?*`, async route => { waiting = true; await held; await route.fallback().catch(() => {}); waiting = false })
  await page.getByRole('button', { name: '새로고침', exact: true }).click()
  await expect.poll(() => waiting).toBe(true)
  await page.getByTestId('sidebar-room-other-room').click()
  await expect(page.getByText('Other room conversation')).toBeVisible()
  release()
  await expect.poll(() => waiting).toBe(false)
  await expect(page.getByText(rootMessage.text, { exact: true })).toHaveCount(0)
  await expect(page.getByRole('button', { name: '에이전트에게 맡기기' })).toHaveCount(0)
  await expect(page.getByRole('textbox')).toHaveCount(0)
  await page.screenshot({ path: testInfo.outputPath('shared-room-ko-readonly.png') })
  expect(fixture.forbidden).toEqual([])
})
