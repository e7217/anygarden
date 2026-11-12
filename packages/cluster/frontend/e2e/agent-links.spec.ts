import { expect, test, type Locator, type Page, type Route } from '@playwright/test'

/**
 * #1006 — agent links and question chains, in a real browser.
 *
 * The link map is a React Flow canvas: component tests mock it away, so
 * this suite checks that the graph actually renders agents and arrows and
 * that the keyboard path (header "Add link") posts the link. The chain
 * half checks that a running chain shows its progress on the person's
 * thread reply row (#1013) and its call tree in the thread.
 *
 * REST is stubbed and ``window.WebSocket`` is a silent fake, like
 * ``threads.spec.ts``.
 */

const user = { id: 'e2e-user', email: 'e2e@example.com', is_admin: true }
const PROJECT_ID = 'proj-1'
const ROOM_ID = 'room-1'
const ROOT_ID = 'msg-root'
const WELCOME = 'Welcome to Anygarden'

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

async function stubApi(page: Page, posted: unknown[], dmPosts: string[] = [], isAdmin = true, history = HISTORY) {
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
        id: posted.length === 1 ? 'l-new' : `l-new-${posted.length}`, ...body, created_at: '2026-11-10T00:00:00Z', updated_at: '2026-11-10T00:00:00Z',
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
    if (pathname === `/api/v1/rooms/${ROOM_ID}/messages`) return fulfillJson(route, 200, history)
    if (pathname.startsWith(`/api/v1/rooms/${ROOM_ID}/messages/`)) return fulfillJson(route, 200, history)
    return fulfillJson(route, 200, [])
  })
}

async function stubWebSocket(page: Page, locale = 'en') {
  await page.addInitScript(locale => {
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
    localStorage.setItem('anygarden_locale', locale)
  }, locale)
}

async function signIn(page: Page, locale = 'en') {
  await page.goto('/login')
  await page.locator('#login-email').fill(user.email)
  await page.locator('#login-password').fill('correct-password')
  await page.getByRole('button', { name: locale === 'ko' ? '로그인' : 'Sign in', exact: true }).click()
  // ROOM-16 — with two or more agents, home is the overview.
  await expect(page).toHaveURL(/\/(agents\/links)?$/)
}

async function dragLink(page: Page, source: Locator, target: Locator, targetRight = false) {
  const handle = source.locator('.react-flow__handle.source')
  await expect(handle).toHaveCSS('opacity', '1')
  const start = (await handle.boundingBox())!
  const end = (await target.boundingBox())!
  await page.mouse.move(start.x + start.width / 2, start.y + start.height / 2)
  await page.mouse.down()
  await page.mouse.move(end.x + (targetRight ? end.width - 4 : end.width / 2), end.y + end.height / 2, { steps: 16 })
  await page.mouse.up()
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
    const handle = source.locator('.react-flow__handle.source')
    await expect(handle).toHaveCSS('opacity', '1')
    await expect(target.locator('.react-flow__handle.target')).toHaveCSS('opacity', '1')
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
    await expect(source.locator('.agent-link-node')).toHaveText('Reviewer')
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

  test('[COL-14] ports stay visible through hover, keyboard focus and selection without moving edge anchors', async ({ page }) => {
    await stubWebSocket(page)
    await stubApi(page, [])
    await signIn(page)
    await page.goto('/agents/links')
    const node = page.getByTestId('rf__node-a-dev')
    const other = page.getByTestId('rf__node-a-sec')
    const handle = node.locator('.react-flow__handle.source')
    const edge = page.getByTestId('rf__edge-l-1').locator('.react-flow__edge-path')
    await expect(edge).toHaveAttribute('d', /M/)
    const path = await edge.getAttribute('d')
    await expect(handle).toHaveCSS('opacity', '1')
    await expect(handle).toHaveCSS('pointer-events', 'all')
    // The port sits on the card border and the edge leaves from its outer side.
    const box = (await node.boundingBox())!
    const [, sx, sy] = path!.match(/^M\s*([\d.-]+)[ ,]+([\d.-]+)/)!.map(Number)
    const viewport = await page.locator('.react-flow__viewport').evaluate(el => new DOMMatrix(getComputedStyle(el).transform))
    const pane = (await page.locator('.react-flow').boundingBox())!
    const port = (await handle.boundingBox())!
    expect(Math.abs(viewport.a * sx + viewport.e + pane.x - (port.x + port.width))).toBeLessThan(1.5)
    expect(Math.abs(viewport.d * sy + viewport.f + pane.y - (port.y + port.height / 2))).toBeLessThan(1.5)
    expect(Math.abs(port.x + port.width / 2 - box.x - box.width)).toBeLessThan(1.5)
    await node.hover()
    await expect(edge).toHaveAttribute('d', path!)
    await page.mouse.move(0, 0)
    await node.focus()
    await expect(handle).toHaveCSS('opacity', '1')
    await expect(edge).toHaveAttribute('d', path!)
    await node.press('Enter')
    await page.getByRole('button', { name: 'Close details' }).focus()
    await expect(handle).toHaveCSS('opacity', '1')
    await expect(other).toHaveCSS('opacity', '1')
    await expect(node.locator('.agent-link-node')).toHaveText('Developer')
    // Starting a connection leaves existing endpoint geometry intact.
    const start = (await handle.boundingBox())!
    await page.mouse.move(start.x + start.width / 2, start.y + start.height / 2)
    await page.mouse.down()
    await other.hover()
    await expect(other.locator('.react-flow__handle.target')).toHaveCSS('opacity', '1')
    await expect(handle).toHaveCSS('opacity', '1')
    await expect(edge).toHaveAttribute('d', path!)
    await page.mouse.move(0, 0)
    await page.mouse.up()
    await expect(page.locator('.react-flow__connection')).toHaveCount(0)
    // The name region still moves the node instead of creating a link.
    const before = (await node.boundingBox())!
    await page.mouse.move(before.x + 12, before.y + before.height / 2)
    await page.mouse.down()
    await page.mouse.move(before.x + 42, before.y + before.height / 2 + 25, { steps: 8 })
    await page.mouse.up()
    await expect.poll(async () => (await node.boundingBox())!.y).toBeGreaterThan(before.y + 15)
  })

  test('[COL-14] rejects self and duplicate drops and leaves no edge after a failed creation', async ({ page }) => {
    const posted: unknown[] = []
    await stubWebSocket(page)
    await stubApi(page, posted)
    await signIn(page)
    await page.goto('/agents/links')
    const developer = page.getByTestId('rf__node-a-dev')
    const reviewer = page.getByTestId('rf__node-a-rev')
    await dragLink(page, developer, developer)
    await dragLink(page, developer, reviewer)
    expect(posted).toEqual([])
    await expect(page.locator('.react-flow__edge')).toHaveCount(1)
    await page.route('**/api/v1/agent-links', async route => {
      if (route.request().method() !== 'POST') return route.fallback()
      posted.push(route.request().postDataJSON())
      await fulfillJson(route, 500, { detail: 'Could not save the relationship' })
    })
    await dragLink(page, reviewer, page.getByTestId('rf__node-a-sec'), true)
    await expect.poll(() => posted.length).toBe(1)
    await expect(page.getByText('Could not save the relationship')).toBeVisible()
    await expect(page.locator('.react-flow__edge')).toHaveCount(1)
    await expect(page.locator('.react-flow__connection')).toHaveCount(0)
  })

  test('[COL-14] dropping exactly on the target port saves the link once', async ({ page }) => {
    const posted: unknown[] = []
    await stubWebSocket(page)
    await stubApi(page, posted)
    await signIn(page)
    await page.goto('/agents/links')
    const start = (await page.getByTestId('rf__node-a-rev').locator('.react-flow__handle.source').boundingBox())!
    const end = (await page.getByTestId('rf__node-a-sec').locator('.react-flow__handle.target').boundingBox())!
    await page.mouse.move(start.x + start.width / 2, start.y + start.height / 2)
    await page.mouse.down()
    await page.mouse.move(end.x + end.width / 2, end.y + end.height / 2, { steps: 16 })
    await page.mouse.up()
    await expect.poll(() => posted).toEqual([{ from_agent_id: 'a-rev', to_agent_id: 'a-sec' }])
    await page.waitForTimeout(300)
    expect(posted).toHaveLength(1)
  })

  test('[COL-14] a resting link is a still line with an arrow', async ({ page }) => {
    await stubWebSocket(page)
    await stubApi(page, [])
    await signIn(page)
    await page.goto('/agents/links')
    const edge = page.getByTestId('rf__edge-l-1')
    const path = edge.locator('.react-flow__edge-path')
    await expect(edge).not.toHaveClass(/animated/)
    await expect(path).toHaveCSS('animation-name', 'none')
    await expect(path).toHaveCSS('marker-end', /url\(/)
  })

  test('[COL-19] a running question flows, with a solid arrow under reduced motion', async ({ page }) => {
    await stubWebSocket(page)
    await stubApi(page, [])
    await page.route('**/api/v1/peer-chains/active', route => fulfillJson(route, 200, {
      edges: [
        { chain_id: 'c1', room_id: ROOM_ID, from_agent_id: 'a-dev', to_agent_id: 'a-rev' },
        { chain_id: 'c1', room_id: ROOM_ID, from_agent_id: 'a-rev', to_agent_id: 'a-dev' },
      ],
    }))
    await signIn(page)
    await page.goto('/agents/links')
    const edge = page.getByTestId('rf__edge-l-1')
    const path = edge.locator('.react-flow__edge-path')
    await expect(edge).toHaveClass(/animated/)
    await expect(path).toHaveCSS('animation-name', 'dashdraw')
    await expect(path).toHaveCSS('marker-end', 'none')
    // A question between room members needs no link; it draws while it runs.
    await expect(page.getByTestId('rf__edge-activity:a-rev>a-dev')).toBeVisible()
    await page.emulateMedia({ reducedMotion: 'reduce' })
    await expect(path).toHaveCSS('animation-name', 'none')
    await expect(path).toHaveCSS('stroke-dasharray', 'none')
    await expect(path).toHaveCSS('marker-end', /url\(/)
  })

  test('[ROOM-16] home opens the overview and its cards show who is answering', async ({ page }) => {
    await stubWebSocket(page)
    await stubApi(page, [])
    await page.route('**/api/v1/turns/active', route => fulfillJson(route, 200, [{ room_id: ROOM_ID, agent_ids: ['a-dev'] }]))
    await signIn(page)
    await expect(page).toHaveURL(/\/agents\/links$/)
    await expect(page.getByTestId('sidebar-agent-links')).toHaveAttribute('aria-current', 'page')
    const dev = page.getByTestId('rf__node-a-dev')
    await expect(dev.locator('.agent-link-node')).toHaveClass(/is-busy/)
    await expect(dev).toContainText('Answering in')
    await expect(page.getByTestId('rf__node-a-sec')).toContainText('Offline')
    await expect(page.getByRole('complementary', { name: 'What is happening' })).toContainText('Running now')
  })

  test('[ROOM-16] home keeps the welcome screen with fewer than two agents', async ({ page }) => {
    await stubWebSocket(page)
    await stubApi(page, [])
    await page.route('**/api/v1/agent-links', route => fulfillJson(route, 200, { agents: agents.slice(0, 1), links: [] }))
    await signIn(page)
    await expect(page.getByRole('heading', { name: WELCOME })).toBeVisible()
    await expect(page).toHaveURL(/\/$/)
  })

  test('[COL-14] keyboard picker, reverse and confirmed deletion remain available', async ({ page }) => {
    const posted: unknown[] = []
    await stubWebSocket(page)
    await stubApi(page, posted)
    await page.route('**/api/v1/agent-links/l-new-2', route => route.fulfill({ status: 204 }))
    await signIn(page)
    await page.goto('/agents/links')
    const add = page.getByRole('button', { name: 'Add link', exact: true })
    await add.focus()
    await add.press('Enter')
    const dialog = page.getByRole('dialog')
    const from = dialog.getByLabel('Agent that asks', { exact: true })
    const to = dialog.getByLabel('Agent it can ask', { exact: true })
    await from.focus()
    await from.press('r')
    await from.press('Tab')
    await expect(to).toBeFocused()
    await to.press('s')
    await to.press('Tab')
    await page.keyboard.press('Tab')
    await expect(dialog.getByRole('button', { name: 'Add link', exact: true })).toBeFocused()
    await page.keyboard.press('Enter')
    await expect.poll(() => posted).toEqual([{ from_agent_id: 'a-rev', to_agent_id: 'a-sec' }])
    await expect(dialog).toHaveCount(0)
    await page.getByRole('button', { name: 'Close details' }).click()
    const original = page.getByTestId('rf__edge-l-1')
    await original.focus()
    await original.press('Enter')
    await page.getByRole('button', { name: 'Link the other direction' }).click()
    await expect.poll(() => posted.length).toBe(2)
    expect(posted[1]).toEqual({ from_agent_id: 'a-rev', to_agent_id: 'a-dev' })
    await page.getByTestId('agent-link-details').getByRole('button', { name: 'Delete', exact: true }).click()
    await page.getByRole('dialog', { name: 'Delete this link?' }).getByRole('button', { name: 'Delete', exact: true }).click()
    await expect(page.getByTestId('rf__edge-l-new-2')).toHaveCount(0)
    await original.focus()
    await original.press('Enter')
    await expect(page.getByTestId('agent-link-details')).toContainText('Developer → Reviewer')
  })

  test('[COL-14] touch keeps a usable port and the picker creates a link', async ({ browser }) => {
    const context = await browser.newContext({ hasTouch: true, viewport: { width: 375, height: 900 } })
    try {
      const page = await context.newPage()
      const posted: unknown[] = []
      await stubWebSocket(page)
      await stubApi(page, posted)
      await signIn(page)
      await page.goto('/agents/links')
      const node = page.getByTestId('rf__node-a-rev')
      const handle = node.locator('.react-flow__handle.source')
      await expect(handle).toHaveCSS('opacity', '1')
      await node.tap()
      const targetSize = await handle.evaluate(element => {
        const hit = getComputedStyle(element, '::after')
        return [parseFloat(hit.width), parseFloat(hit.height)]
      })
      expect(Math.min(...targetSize)).toBeGreaterThanOrEqual(44)
      const add = page.getByTestId('agent-reach-details').getByRole('button', { name: 'Add link', exact: true })
      await add.tap()
      const dialog = page.getByRole('dialog')
      await expect(dialog.getByLabel('Agent that asks', { exact: true })).toHaveValue('a-rev')
      await dialog.getByLabel('Agent it can ask', { exact: true }).selectOption('a-sec')
      await dialog.getByRole('button', { name: 'Add link', exact: true }).tap()
      await expect.poll(() => posted).toEqual([{ from_agent_id: 'a-rev', to_agent_id: 'a-sec' }])
      await expect(page.getByTestId('rf__edge-l-new')).toBeAttached()
    } finally { await context.close() }
  })

  for (const theme of ['light', 'dark']) {
    for (const locale of ['en', 'ko']) {
      test(`[COL-14] slim cards fit reference widths in ${theme}/${locale}`, async ({ page }, testInfo) => {
        await page.setViewportSize({ width: 1440, height: 1000 })
        await stubWebSocket(page, locale)
        await stubApi(page, [])
        await signIn(page, locale)
        await page.evaluate(({ theme, locale }) => {
          localStorage.setItem('anygarden-theme', theme)
          localStorage.setItem('anygarden_locale', locale)
        }, { theme, locale })
        const graphAgents = [...agents,
          { id: 'a-design', name: '아주 긴 에이전트 이름 · Product design', description: null, actual_state: 'idle' },
          { id: 'a-docs', name: 'Documentation', description: null, actual_state: 'idle' },
        ]
        const pairs = graphAgents.slice(1).map(agent => ['a-dev', agent.id])
        pairs.push(['a-rev', 'a-dev'])
        await page.route('**/api/v1/agent-links', route => fulfillJson(route, 200, {
          agents: graphAgents,
          links: pairs.map(([from_agent_id, to_agent_id], i) => ({
            id: `visual-${i}`, from_agent_id, to_agent_id,
            created_at: '2026-11-10T00:00:00Z', updated_at: '2026-11-10T00:00:00Z',
          })),
        }))
        async function capture(name: string) {
          const path = testInfo.outputPath(`${name}.png`)
          await page.screenshot({ path })
          await testInfo.attach(name, { path, contentType: 'image/png' })
        }
        for (const width of [375, 768, 1024, 1440]) {
          await page.setViewportSize({ width, height: 1000 })
          await page.evaluate(() => localStorage.removeItem('anygarden_agent_links_view_v1_e2e-user'))
          await page.goto('/agents/links')
          const hub = page.getByTestId('rf__node-a-dev')
          await expect(hub).toBeVisible()
          await expect(page.locator('.react-flow__edge')).toHaveCount(5)
          await expect(page.getByTestId('rf__node-a-design').locator('.agent-link-node')).toHaveAttribute('title', graphAgents[3].name)
          expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width)
          await capture(`${theme}-${locale}-${width}`)
          await hub.click()
          const details = page.getByRole('complementary', { name: locale === 'en' ? 'Details' : '상세', exact: true })
          await expect(details).toBeVisible()
          await expect(page.getByTestId('rf__node-a-sec')).toHaveCSS('opacity', '1')
          await expect.poll(async () => {
            const card = await hub.boundingBox(), panel = await details.boundingBox()
            return !!card && !!panel && card.x >= 0 && card.y >= 0 && card.x + card.width <= width
              && (width >= 1024 ? card.x + card.width <= panel.x : card.y + card.height <= panel.y)
          }).toBe(true)
          // Auto-fit must leave cards clear of the canvas's floating controls.
          const canvas = (await page.locator('.agent-links-root').boundingBox())!
          const controls = await page.locator('.agent-links-root button').all()
          for (const card of await page.locator('.react-flow__node').all()) {
            const box = (await card.boundingBox())!
            expect(box.x).toBeGreaterThanOrEqual(canvas.x)
            expect(box.x + box.width).toBeLessThanOrEqual(canvas.x + canvas.width)
            expect(box.y).toBeGreaterThanOrEqual(canvas.y)
            expect(box.y + box.height).toBeLessThanOrEqual(canvas.y + canvas.height)
            for (const control of controls) {
              const button = (await control.boundingBox())!
              expect(box.x >= button.x + button.width || box.x + box.width <= button.x
                || box.y >= button.y + button.height || box.y + box.height <= button.y).toBe(true)
            }
          }
          await capture(`${theme}-${locale}-${width}-selected`)
        }
      })
    }
  }

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
  test('[COL-17] a running chain shows on the thread reply row and its call tree in the thread', async ({ page }) => {
    await stubWebSocket(page)
    await stubApi(page, [])
    await signIn(page)
    await page.goto(`/rooms/${ROOM_ID}`)
    await expect(page.getByText('Please check the release')).toBeVisible()

    // #1013 — the thread reply row carries the progress; no separate card.
    await expect(page.getByTestId('thread-working')).toHaveText('Security answering')
    await expect(page.getByTestId('peer-chain-progress')).toHaveCount(0)
    await expect(page.getByRole('button', { name: /^Stop/ })).toHaveCount(1)
    const row = page.locator(`[data-thread-trigger="${ROOT_ID}"]`)
    await row.hover()
    await expect(page.getByTestId('peer-chain-hover')).toContainText('Reviewer → Security')
    await row.click()

    await expect(page.getByText('Reviewer → Security').first()).toBeVisible()
  })

  test('a thread with long questions opens scrolled to its end, without shifting the panel', async ({ page }) => {
    // #1013 — following the newest reply used scrollIntoView, whose default
    // block "start" could not be met by the viewport alone, so the browser
    // scrolled the overflow-hidden ScrollArea root too: the top slid under
    // the header and the bottom went blank.
    const long = 'The release notes, the audit log and the token check all need a look. '.repeat(40)
    const history = HISTORY.map(msg => (msg.id === ROOT_ID ? msg : { ...msg, content: `${msg.content} ${long}` }))
    await page.setViewportSize({ width: 1280, height: 900 })
    await stubWebSocket(page)
    await stubApi(page, [], [], true, history)
    await signIn(page)
    await page.goto(`/rooms/${ROOM_ID}`)
    await page.locator(`[data-thread-trigger="${ROOT_ID}"]`).first().click()

    const viewport = page.getByTestId('thread-panel-root').locator('[data-radix-scroll-area-viewport]')
    await expect.poll(() => viewport.evaluate(el => el.scrollHeight - el.clientHeight - el.scrollTop)).toBeLessThan(2)
    expect(await viewport.evaluate(el => (el.parentElement as HTMLElement).scrollTop)).toBe(0)
  })
})
