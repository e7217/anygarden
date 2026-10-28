import { expect, test, type Page, type Route } from '@playwright/test'

/**
 * Campaigns in the right rail (#930): start one from the form, see it in
 * the rail, pause it. Runs against Vite alone with REST and WebSocket
 * stubbed, like ``right-rail.spec.ts``.
 */

const user = { id: 'e2e-user', email: 'e2e@example.com', is_admin: false }
const PROJECT_ID = 'proj-1'
const ROOM_ID = 'room-1'

async function fulfillJson(route: Route, status: number, body: unknown) {
  await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
}

type Body = Record<string, unknown> & {
  metric: Record<string, unknown>
  evaluator: Record<string, unknown>
  budget: { max_total_tokens: number }
}

function campaignFrom(body: Body, status = 'active', revision = 1) {
  return {
    id: 'camp-1', room_id: ROOM_ID, owner_id: user.id, agent_id: 'agent-1',
    title: body.title, purpose: body.purpose, mode: body.mode,
    metric: { min_delta: 0, ...body.metric }, evaluator: body.evaluator,
    trusted_eval: body.trusted_eval ?? 'none',
    budget: { retro_reserve_ratio: 0.05, ...body.budget },
    usable_tokens: Math.floor(body.budget.max_total_tokens * 0.95),
    deadline_at: body.deadline_at ?? null, status, stop_reason: null, status_detail: null,
    cycles_run: 0, tokens_used: 0, next_cycle_at: null, cycles_since_improvement: 0,
    consecutive_eval_failures: 0, retro_due: null, state_revision: revision,
    created_at: '2026-10-26T00:00:00Z', updated_at: '2026-10-26T00:00:00Z',
    best: null, final: null, open_attempt: null,
  }
}

async function stubApi(page: Page, messages: unknown[] = []) {
  const state: { created: Body | null; status: string; revision: number; requests: Body[] } = {
    created: null, status: 'active', revision: 1, requests: [],
  }
  await page.route('**/api/v1/**', async route => {
    const request = route.request()
    const { pathname } = new URL(request.url())
    const method = request.method()

    if (pathname === '/api/v1/auth/dev-token') return fulfillJson(route, 404, { detail: 'Dev login disabled' })
    if (pathname === '/api/v1/auth/login') return fulfillJson(route, 200, { token: 'e2e-token', user })
    if (pathname === '/api/v1/auth/me') return fulfillJson(route, 200, user)
    if (pathname === '/api/v1/system/version') return fulfillJson(route, 200, { version: '0.23.0' })
    if (pathname === '/api/v1/projects') return fulfillJson(route, 200, [{ id: PROJECT_ID, name: 'e2e-proj' }])
    const room = {
      id: ROOM_ID, project_id: PROJECT_ID, name: 'e2e-room', is_dm: false,
      parent_room_id: null, representative_agent_id: 'agent-1',
    }
    if (pathname === '/api/v1/rooms') return fulfillJson(route, 200, [room])
    if (pathname === `/api/v1/rooms/${ROOM_ID}`) {
      return fulfillJson(route, 200, {
        ...room,
        participants: [
          { id: 'part-me', display_name: 'e2e', kind: 'user', user_id: user.id, role: 'owner' },
          { id: 'part-agent', display_name: 'Lead', kind: 'agent', agent_id: 'agent-1', online: true },
        ],
      })
    }
    if (pathname === `/api/v1/rooms/${ROOM_ID}/messages`) return fulfillJson(route, 200, messages)

    if (pathname === `/api/v1/rooms/${ROOM_ID}/campaigns`) {
      if (method === 'POST') {
        const body = request.postDataJSON() as Body
        state.requests.push(body)
        state.created = body
        return fulfillJson(route, 201, campaignFrom(body))
      }
      return fulfillJson(route, 200, state.created ? [campaignFrom(state.created, state.status, state.revision)] : [])
    }
    if (pathname === '/api/v1/campaigns/camp-1/attempts') {
      return fulfillJson(route, 200, { items: [], total: 0, next_offset: null })
    }
    if (pathname === '/api/v1/campaigns/camp-1/pause' && method === 'POST' && state.created) {
      state.status = 'paused'
      state.revision += 1
      return fulfillJson(route, 200, campaignFrom(state.created, state.status, state.revision))
    }
    return fulfillJson(route, 200, [])
  })
  return state
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
  })
}

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem('anygarden_locale', 'en')
    if (localStorage.getItem('anygarden_right_sidebar_collapsed') === null) {
      localStorage.setItem('anygarden_right_sidebar_collapsed', 'false')
    }
  })
  await stubWebSocket(page)
})

test('[CMP-01] start a campaign, see it in the rail and pause it', async ({ page }) => {
  const state = await stubApi(page)
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto('/login')
  await page.locator('#login-email').fill(user.email)
  await page.locator('#login-password').fill('correct-password')
  await page.getByRole('button', { name: 'Sign In' }).click()
  await expect(page).toHaveURL(/\/$/)
  await page.goto(`/rooms/${ROOM_ID}`)

  const section = page.getByTestId('campaign-section')
  await expect(section).toBeVisible()
  await expect(section.getByText(/No goals yet/)).toBeVisible()
  await section.getByRole('button', { name: 'New goal' }).click()

  const form = page.getByTestId('campaign-form')
  await expect(form).toBeVisible()
  // Optimize mode hides the target; target mode shows it again.
  await form.getByRole('radio', { name: /Improve until a deadline/ }).check()
  await expect(form.getByLabel('Target score', { exact: true })).toHaveCount(0)
  await form.getByRole('radio', { name: /Reach a target score/ }).check()

  await form.getByLabel('Title', { exact: true }).fill('Raise parser accuracy')
  await form.getByLabel(/Purpose/).fill('The release needs 90% accuracy')
  await form.getByLabel('Metric name', { exact: true }).fill('accuracy')
  await form.getByLabel('Target score', { exact: true }).fill('0.9')
  await form.getByLabel('Evaluator command', { exact: true }).fill('python eval.py')
  await form.getByRole('button', { name: 'Start goal' }).click()
  await expect(form).toBeHidden()

  expect(state.requests).toHaveLength(1)
  expect(state.requests[0]).toMatchObject({
    mode: 'target',
    metric: { name: 'accuracy', direction: 'maximize', target: 0.9 },
    evaluator: { command: 'python eval.py', cwd: '.', timeout_sec: 600, pin_paths: [], score_key: 'score' },
    budget: { max_total_tokens: 500000 },
    trusted_eval: 'none',
  })
  expect(state.requests[0]).not.toHaveProperty('deadline_at')

  const card = section.getByTestId('campaign-card-camp-1')
  await expect(card).toBeVisible()
  await expect(card.getByTestId('campaign-status')).toHaveText('Running')
  await expect(card.getByTestId('campaign-score')).toHaveText('accuracy · No score yet · target 0.9 or higher')
  await expect(card.getByTestId('campaign-budget')).toHaveText('0% of budget used')

  await card.getByRole('button', { name: 'Pause' }).click()
  await expect(card.getByTestId('campaign-status')).toHaveText('Paused')
  await expect(card.getByRole('button', { name: 'Resume' })).toBeVisible()
  await expect(card.getByRole('button', { name: 'Pause' })).toHaveCount(0)
})

test('[CMP-16] a goal that is blocked shows up in the room chat', async ({ page }) => {
  await stubApi(page, [{
    type: 'message', id: 'msg-goal', room_id: ROOM_ID, participant_id: null, seq: 1,
    created_at: '2026-10-28T02:46:49Z',
    content: 'Goal “Explore 20 ideas” is blocked. The evaluation failed 3 times in a row; check the evaluator.',
    metadata: { goal_status: {
      campaign_id: 'camp-1', title: 'Explore 20 ideas', status: 'blocked', previous: 'active',
      detail: 'The evaluation failed 3 times in a row; check the evaluator.',
    } },
  }])
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto('/login')
  await page.locator('#login-email').fill(user.email)
  await page.locator('#login-password').fill('correct-password')
  await page.getByRole('button', { name: 'Sign In' }).click()
  await expect(page).toHaveURL(/\/$/)
  await page.goto(`/rooms/${ROOM_ID}`)

  const card = page.getByTestId('goal-status-card')
  await expect(card).toBeVisible()
  await expect(card).toContainText('Goal')
  await expect(card).toContainText('Explore 20 ideas')
  await expect(card).toContainText('Blocked')
  await expect(card).toContainText('check the evaluator')
})

test('[CMP-17] [CMP-18] a goal waiting for confirmation asks its lead to report, with the files', async ({ page }) => {
  await stubApi(page, [{
    type: 'message', id: 'msg-goal-report', room_id: ROOM_ID, participant_id: null, seq: 1,
    created_at: '2026-10-28T02:46:49Z',
    content: '<@user:part-agent> Goal report request — Goal “Explore 20 ideas” reached its target. REQUIRED brief',
    metadata: {
      goal_status: {
        campaign_id: 'camp-1', title: 'Explore 20 ideas', status: 'awaiting_confirmation',
        previous: 'active', detail: null,
      },
      system_origin: 'goal_report',
      goal_report: { campaign_id: 'camp-1', reason: 'confirmation', lead_participant_id: 'part-agent' },
      mentions: [{ type: 'user', id: 'part-agent' }],
      references: [{
        type: 'artifact', id: 'art-1', room_id: ROOM_ID, name: 'ideas.md',
        mime: 'text/markdown', size_bytes: 2048, sha256: 'a'.repeat(64),
      }],
    },
  }])
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto('/login')
  await page.locator('#login-email').fill(user.email)
  await page.locator('#login-password').fill('correct-password')
  await page.getByRole('button', { name: 'Sign In' }).click()
  await expect(page).toHaveURL(/\/$/)
  await page.goto(`/rooms/${ROOM_ID}`)

  const card = page.getByTestId('goal-status-card')
  await expect(card).toBeVisible()
  await expect(card).toContainText('Needs your confirmation')
  await expect(card.getByTestId('goal-report-request')).toContainText('Asked Lead to report the result')
  await expect(card.getByTestId('message-attachment-file')).toContainText('ideas.md')
  await expect(page.getByText('REQUIRED brief')).toHaveCount(0)
})
