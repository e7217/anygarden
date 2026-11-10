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

async function stubApi(page: Page, posted: unknown[]) {
  await page.route('**/api/v1/**', async route => {
    const request = route.request()
    const { pathname } = new URL(request.url())

    if (pathname === '/api/v1/auth/dev-token') return fulfillJson(route, 404, { detail: 'off' })
    if (pathname === '/api/v1/auth/login') return fulfillJson(route, 200, { token: 'e2e-token', user })
    if (pathname === '/api/v1/auth/me') return fulfillJson(route, 200, user)
    if (pathname === '/api/v1/system/version') return fulfillJson(route, 200, { version: '0.28.0' })
    if (pathname === '/api/v1/projects') return fulfillJson(route, 200, [{ id: PROJECT_ID, name: 'e2e-proj' }])
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
          when_to_ask: 'before opening a pull request',
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
  test('[COL-14] the map shows agents and arrows, and Add link posts a link', async ({ page }) => {
    const posted: unknown[] = []
    await stubWebSocket(page)
    await stubApi(page, posted)
    await signIn(page)
    await page.goto('/agents/links')

    const canvas = page.getByRole('region', { name: 'Agent link map' }).or(
      page.getByLabel('Agent link map'),
    ).first()
    await expect(canvas).toBeVisible()
    await expect(page.getByText('Developer').first()).toBeVisible()
    await expect(page.getByText('Security').first()).toBeVisible()
    await expect(
      page.getByRole('button', { name: /Developer asks Reviewer: before opening a pull request/ }),
    ).toBeVisible()

    await page.getByRole('button', { name: 'Add link' }).first().click()
    const dialog = page.getByRole('dialog')
    await dialog.getByLabel('Agent that asks').selectOption({ label: 'Reviewer' })
    await dialog.getByLabel('Agent it can ask').selectOption({ label: 'Security' })
    await dialog.getByLabel('When to ask').fill('when sign-in or permissions are involved')
    await dialog.getByRole('button', { name: 'Add link' }).click()

    await expect.poll(() => posted).toEqual([{
      from_agent_id: 'a-rev',
      to_agent_id: 'a-sec',
      when_to_ask: 'when sign-in or permissions are involved',
    }])
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
