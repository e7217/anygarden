import { expect, test, type Page, type Route } from '@playwright/test'

/**
 * #1006 — agent links and question chains, in a real browser.
 *
 * The link map is a React Flow canvas: component tests mock it away, so
 * this suite checks that the graph actually renders agents and arrows and
 * that the keyboard path (header "Add link") posts the link. The chain
 * half checks that a running chain shows its progress card under the
 * person's message and its call tree in the thread.
 *
 * REST is stubbed and ``window.WebSocket`` is a silent fake, like
 * ``threads.spec.ts``.
 */

const user = { id: 'e2e-user', email: 'e2e@example.com', is_admin: true }
const PROJECT_ID = 'proj-1'
const ROOM_ID = 'room-1'
const ROOT_ID = 'msg-root'

const agents = [
  { id: 'a-dev', name: 'Developer', description: null, actual_state: 'running' },
  { id: 'a-rev', name: 'Reviewer', description: null, actual_state: 'running' },
  { id: 'a-sec', name: 'Security', description: null, actual_state: 'idle' },
]

async function fulfillJson(route: Route, status: number, body: unknown) {
  await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
}

function chain() {
  return {
    id: 'chain-1',
    room_id: ROOM_ID,
    thread_root_id: ROOT_ID,
    origin_message_id: ROOT_ID,
    state: 'active',
    started_at: '2026-11-10T00:00:00Z',
    finished_at: null,
    max_depth: 2,
    agent_count: 3,
    call_count: 2,
    root: { participant_id: 'p-dev', agent_id: 'a-dev', name: 'Developer' },
    nodes: [
      {
        id: 't-rev', parent_id: null, depth: 1,
        caller_participant_id: 'p-dev', caller_name: 'Developer',
        target_participant_id: 'p-rev', target_agent_id: 'a-rev', target_name: 'Reviewer',
        question: 'Is the token check safe?', state: 'waiting', reason: null,
        question_message_id: 'msg-q1', reply_message_id: null,
        created_at: '2026-11-10T00:00:01Z', finished_at: null,
      },
      {
        id: 't-sec', parent_id: 't-rev', depth: 2,
        caller_participant_id: 'p-rev', caller_name: 'Reviewer',
        target_participant_id: 'p-sec', target_agent_id: 'a-sec', target_name: 'Security',
        question: 'Is this change in the audit log?', state: 'answering', reason: null,
        question_message_id: 'msg-q2', reply_message_id: null,
        created_at: '2026-11-10T00:00:02Z', finished_at: null,
      },
    ],
  }
}

function message(overrides: Record<string, unknown>) {
  return {
    room_id: ROOM_ID,
    parent_message_id: null,
    root_message_id: null,
    created_at: '2026-11-10T00:00:00Z',
    metadata: null,
    ...overrides,
  }
}

const HISTORY = [
  message({ id: ROOT_ID, participant_id: 'p-me', content: 'Please check the release', seq: 1 }),
  message({
    id: 'msg-q1', participant_id: 'p-dev', content: '<@user:p-rev> Is the token check safe?',
    parent_message_id: ROOT_ID, root_message_id: ROOT_ID, seq: 2,
    metadata: { kind: 'peer_query', peer_ask: { group_id: 'g1' } },
  }),
  message({
    id: 'msg-q2', participant_id: 'p-rev', content: '<@user:p-sec> Is this change in the audit log?',
    parent_message_id: ROOT_ID, root_message_id: ROOT_ID, seq: 3,
    metadata: { kind: 'peer_query', peer_ask: { group_id: 'g2' } },
  }),
]

async function stubApi(page: Page, posted: unknown[], dmPosts: string[] = [], isAdmin = true) {
  await page.route('**/api/v1/**', async route => {
    const request = route.request()
    const { pathname, searchParams } = new URL(request.url())

    if (pathname === '/api/v1/auth/dev-token') return fulfillJson(route, 404, { detail: 'off' })
    if (pathname === '/api/v1/auth/login') return fulfillJson(route, 200, { token: 'e2e-token', user: { ...user, is_admin: isAdmin } })
    if (pathname === '/api/v1/auth/me') return fulfillJson(route, 200, { ...user, is_admin: isAdmin })
    if (pathname === '/api/v1/system/version') return fulfillJson(route, 200, { version: '0.28.0' })
    if (pathname === '/api/v1/projects') return fulfillJson(route, 200, [{ id: PROJECT_ID, name: 'e2e-proj' }])
    if (pathname.match(/^\/api\/v1\/agents\/[^/]+\/dms$/) && request.method() === 'POST') {
      const agentId = pathname.split('/')[4]
      dmPosts.push(agentId)
      return fulfillJson(route, 201, { id: 'dm-new', name: 'New conversation', project_id: null, is_dm: true, representative_agent_id: agentId })
    }
    if (pathname === '/api/v1/rooms' && searchParams.get('is_dm') === 'true') {
      const target = searchParams.get('representative_agent_id')
      return fulfillJson(route, 200, target && target !== 'a-dev' ? [] : [
        { id: 'dm-dev-1', name: 'Release planning', project_id: null, is_dm: true, representative_agent_id: 'a-dev' },
        { id: 'dm-dev-2', name: 'Fix login', project_id: null, is_dm: true, representative_agent_id: 'a-dev' },
      ])
    }
    if (pathname === '/api/v1/rooms') {
      return fulfillJson(route, 200, [
        { id: ROOM_ID, project_id: PROJECT_ID, name: 'e2e-room', is_dm: false, parent_room_id: null },
      ])
    }
    if (pathname === '/api/v1/agent-links' && request.method() === 'POST') {
      const body = request.postDataJSON()
      posted.push(body)
      return fulfillJson(route, 201, {
        id: 'l-new', ...body, created_at: '2026-11-10T00:00:00Z', updated_at: '2026-11-10T00:00:00Z',
      })
    }
    if (pathname === '/api/v1/agent-links') {
      return fulfillJson(route, 200, {
        agents,
        links: [{
          id: 'l-1', from_agent_id: 'a-dev', to_agent_id: 'a-rev',
          created_at: '2026-11-10T00:00:00Z', updated_at: '2026-11-10T00:00:00Z',
        }],
      })
    }
    if (pathname === `/api/v1/rooms/${ROOM_ID}`) {
      return fulfillJson(route, 200, {
        id: ROOM_ID, project_id: PROJECT_ID, name: 'e2e-room', is_dm: false, parent_room_id: null,
        participants: [
          { id: 'p-me', display_name: 'e2e', kind: 'user', user_id: user.id },
          { id: 'p-dev', display_name: 'Developer', kind: 'agent', agent_id: 'a-dev' },
          { id: 'p-rev', display_name: 'Reviewer', kind: 'agent', agent_id: 'a-rev' },
          { id: 'p-sec', display_name: 'Security', kind: 'agent', agent_id: 'a-sec', joined_via: 'link' },
        ],
      })
    }
    if (pathname === `/api/v1/rooms/${ROOM_ID}/peer-chains`) {
      return fulfillJson(route, 200, { chains: [chain()] })
    }
    if (pathname === `/api/v1/rooms/${ROOM_ID}/messages`) return fulfillJson(route, 200, HISTORY)
    if (pathname.startsWith(`/api/v1/rooms/${ROOM_ID}/messages/`)) return fulfillJson(route, 200, HISTORY)
    return fulfillJson(route, 200, [])
  })
}

async function stubWebSocket(page: Page) {
  await page.addInitScript(() => {
    class FakeWebSocket {
      static readonly OPEN = 1
      readyState = 1
      onopen: ((e: unknown) => void) | null = null
      onclose: ((e: unknown) => void) | null = null
      onerror: ((e: unknown) => void) | null = null
      onmessage: ((e: unknown) => void) | null = null
      constructor() { setTimeout(() => this.onopen?.({}), 0) }
      send() {}
      close() { this.readyState = 3 }
      addEventListener() {}
      removeEventListener() {}
    }
    ;(window as unknown as { WebSocket: unknown }).WebSocket = FakeWebSocket
    localStorage.setItem('anygarden_locale', 'en')
  })
}

async function signIn(page: Page) {
  await page.goto('/login')
  await page.locator('#login-email').fill(user.email)
  await page.locator('#login-password').fill('correct-password')
  await page.getByRole('button', { name: 'Sign In' }).click()
  await expect(page).toHaveURL(/\/$/)
}

test.describe('agent links', () => {
  test('[COL-14] horizontal drag creates a link immediately with compact names and keyboard-selectable edges', async ({ page }) => {
    const posted: unknown[] = []
    await stubWebSocket(page)
    await stubApi(page, posted)
    await signIn(page)
    await page.goto('/agents/links')
    const source = page.getByTestId('rf__node-a-rev')
    const target = page.getByTestId('rf__node-a-sec')
    await expect(source).toBeVisible()
    await expect(page.getByTestId('rf__edge-l-1')).toHaveAttribute('aria-label', 'Developer asks Reviewer')
    const handle = source.locator('.agent-link-node__source')
    await expect(handle).toBeVisible()
    await expect.poll(async () => (await handle.boundingBox())?.width).toBeGreaterThan(0)
    const start = (await handle.boundingBox())!
    const end = (await target.boundingBox())!
    const before = (await source.boundingBox())!
    await page.mouse.move(start.x + start.width / 2, start.y + start.height / 2)
    await page.mouse.down()
    await page.mouse.move(end.x + end.width / 2, end.y + end.height / 2, { steps: 16 })
    await page.mouse.up()
    await expect.poll(() => posted).toEqual([{ from_agent_id: 'a-rev', to_agent_id: 'a-sec' }])
    await expect(page.getByRole('dialog')).toHaveCount(0)
    await expect.poll(async () => (await source.boundingBox())!.width).toBeGreaterThan(before.width)
    await expect(source.locator('.agent-link-node')).toHaveText('Reviewer+')
    const sourceHandle = (await handle.boundingBox())!
    const sourceBox = (await source.boundingBox())!
    expect(Math.abs(sourceHandle.x + sourceHandle.width / 2 - sourceBox.x - sourceBox.width)).toBeLessThan(2)
    await page.getByRole('button', { name: 'Close details' }).click()
    const edge = page.getByTestId('rf__edge-l-new')
    await edge.focus()
    await edge.press('Enter')
    await expect(page.getByTestId('agent-link-details')).toContainText('Reviewer → Security')
    await expect(page.getByText('When to ask', { exact: true })).toHaveCount(0)
  })

  test('[COL-18] opens the selected existing conversation and restores the map view on return', async ({ page }) => {
    await stubWebSocket(page)
    await stubApi(page, [])
    await signIn(page)
    await page.goto('/agents/links')
    await page.getByTestId('rf__node-a-dev').click()
    await expect(page.getByRole('link', { name: 'Release planning', exact: true })).toBeVisible()
    await expect(page.getByRole('link', { name: 'Fix login', exact: true })).toBeVisible()
    await page.locator('.react-flow__controls-zoomin').click()
    const viewport = page.locator('.react-flow__viewport')
    await expect.poll(async () => viewport.getAttribute('style')).toContain('scale(1.2)')
    const before = await viewport.getAttribute('style')
    await page.getByRole('link', { name: 'Release planning', exact: true }).click()
    await expect(page).toHaveURL(/\/rooms\/dm-dev-1$/)
    await page.goBack()
    await expect(page).toHaveURL(/\/agents\/links$/)
    await expect(viewport).toHaveAttribute('style', before!)
  })

  test('[COL-18] narrow-screen details keep the selected node visible', async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 900 })
    await stubWebSocket(page)
    await stubApi(page, [])
    await signIn(page)
    await page.goto('/agents/links')
    const node = page.getByTestId('rf__node-a-dev')
    await node.click()
    const details = page.getByRole('complementary', { name: 'Details', exact: true })
    await expect(details.getByRole('link', { name: 'Release planning', exact: true })).toBeVisible()
    await expect.poll(async () => {
      const n = await node.boundingBox(), d = await details.boundingBox()
      return !!n && !!d && n.y >= 0 && n.y + n.height <= d.y
    }).toBe(true)
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(375)
  })

  test('[COL-18] creates one new conversation for the selected agent', async ({ page }) => {
    const dmPosts: string[] = []
    await stubWebSocket(page)
    await stubApi(page, [], dmPosts)
    await signIn(page)
    await page.goto('/agents/links')
    await page.getByTestId('rf__node-a-dev').click()
    await page.getByRole('button', { name: 'New conversation', exact: true }).click()
    await expect(page).toHaveURL(/\/rooms\/dm-new$/)
    expect(dmPosts).toEqual(['a-dev'])
  })

  test('[COL-18] non-admins can open accessible history without a create button', async ({ page }) => {
    await stubWebSocket(page)
    await stubApi(page, [], [], false)
    await signIn(page)
    await page.goto('/agents/links')
    await page.getByTestId('rf__node-a-dev').click()
    await expect(page.getByRole('link', { name: 'Release planning', exact: true })).toBeVisible()
    await expect(page.getByRole('button', { name: 'New conversation', exact: true })).toHaveCount(0)
  })
})

test.describe('question chains', () => {
  test('[COL-17] a running chain shows its card and its call tree in the thread', async ({ page }) => {
    await stubWebSocket(page)
    await stubApi(page, [])
    await signIn(page)
    await page.goto(`/rooms/${ROOM_ID}`)
    await expect(page.getByText('Please check the release')).toBeVisible()

    const card = page.getByTestId('peer-chain-progress').or(
      page.getByText(/Developer.*(collecting|gathering)/i),
    ).first()
    await expect(card).toBeVisible()
    await page.getByRole('button', { name: /Open thread/i }).first().click()

    await expect(page.getByText('Reviewer → Security').first()).toBeVisible()
  })
})
