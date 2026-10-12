import { expect, test, type Page } from '@playwright/test'

const user = { id: 'e2e-admin', email: 'admin@example.com', is_admin: true }

interface Policy {
  id: string
  scope_type: string
  scope_id: string | null
  token_ceiling: number
  warn_percent: number
  window_kind: string
  hard_stop_enabled: boolean
  is_active: boolean
  created_at: string
  updated_at: string
}

function makePolicy(overrides: Partial<Policy>): Policy {
  return {
    id: 'p-1', scope_type: 'global', scope_id: null, token_ceiling: 1000, warn_percent: 80,
    window_kind: 'rolling_24h', hard_stop_enabled: false, is_active: true,
    created_at: '2026-10-01T00:00:00Z', updated_at: '2026-10-01T00:00:00Z',
    ...overrides,
  }
}

/** Stateful stub of the budgets REST contract plus the shell's own calls. */
async function stubApi(page: Page) {
  const state = {
    policies: [makePolicy({ id: 'p-agent', scope_type: 'agent', scope_id: 'a1', hard_stop_enabled: true })],
    paused: [{ agent_id: 'a1', name: 'Writer' }] as { agent_id: string; name: string }[],
    posted: [] as unknown[],
    resumed: [] as string[],
  }
  await page.route('**/api/v1/**', async route => {
    const request = route.request()
    const { pathname } = new URL(request.url())
    const method = request.method()
    let status = 200
    let body: unknown = []

    if (pathname === '/api/v1/auth/dev-token') status = 404
    else if (pathname === '/api/v1/auth/login') body = { token: 'e2e-token', user }
    else if (pathname === '/api/v1/auth/me') body = user
    else if (pathname === '/api/v1/system/version') body = { version: '0.18.0' }
    else if (pathname === '/api/v1/system/updates') body = []
    else if (pathname === '/api/v1/agents') body = [{ id: 'a1', name: 'Writer' }, { id: 'a2', name: 'Reviewer' }]
    else if (pathname === '/api/v1/budgets' && method === 'POST') {
      const payload = request.postDataJSON() as Partial<Policy>
      state.posted.push(payload)
      const created = makePolicy({ ...payload, id: `p-${state.policies.length + 1}` })
      state.policies.push(created)
      status = 201
      body = created
    } else if (pathname === '/api/v1/budgets') body = state.policies
    else if (pathname === '/api/v1/budgets/overview') {
      body = {
        usage: state.policies.map(p => ({ policy_id: p.id, observed_tokens: p.id === 'p-agent' ? 1200 : 0, window_start: '2026-10-11T00:00:00Z' })),
        incidents: [],
        paused_agents: state.paused,
      }
    } else if (/^\/api\/v1\/budgets\/agents\/[^/]+\/resume$/.test(pathname) && method === 'POST') {
      const agentId = pathname.split('/')[5]
      state.resumed.push(agentId)
      state.paused = state.paused.filter(a => a.agent_id !== agentId)
      body = { agent_id: agentId, incidents_resolved: 1 }
    } else if (pathname !== '/api/v1/projects' && pathname !== '/api/v1/rooms') {
      status = 404
    }

    await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
  })
  return state
}

async function signIn(page: Page) {
  const state = await stubApi(page)
  await page.goto('/login')
  await page.locator('#login-email').fill(user.email)
  await page.locator('#login-password').fill('correct-password')
  await page.getByRole('button', { name: 'Sign In' }).click()
  await expect(page).toHaveURL(/\/$/)
  return state
}

test('[USE-07] admin manages budget policies and resumes a paused agent', async ({ page }) => {
  await page.setViewportSize({ width: 1024, height: 900 })
  const state = await signIn(page)

  await page.getByRole('button', { name: /^Admin settings/ }).click()
  await page.getByRole('button', { name: 'Budgets' }).click()
  await expect(page).toHaveURL(/\/admin\/budgets$/)
  await expect(page.getByRole('heading', { level: 1, name: 'Budgets' })).toBeVisible()

  const agentRow = page.getByTestId('budget-policy-p-agent')
  await expect(agentRow).toContainText('Agent · Writer')
  await expect(agentRow).toContainText('Over ceiling')
  await expect(agentRow).toContainText('Hard stop on')

  // Create a workspace-wide policy on the typical path.
  await page.getByRole('button', { name: 'New policy' }).first().click()
  const dialog = page.getByRole('dialog')
  await expect(dialog).toBeVisible()
  await dialog.getByLabel('Token ceiling').fill('250000')
  await dialog.getByRole('button', { name: 'Create policy' }).click()
  await expect(dialog).not.toBeVisible()
  expect(state.posted).toEqual([{
    scope_type: 'global', scope_id: null, token_ceiling: 250000,
    warn_percent: 80, window_kind: 'rolling_24h', hard_stop_enabled: false,
  }])
  await expect(page.getByTestId('budget-policy-p-2')).toContainText('Whole workspace')
  await expect(page.getByTestId('budget-policy-p-2')).toContainText('Record only')

  // Resume the budget-paused agent through the in-app confirmation.
  await page.getByRole('button', { name: 'Resume Writer' }).click()
  await page.getByRole('dialog').getByRole('button', { name: 'Resume', exact: true }).click()
  await expect(page.getByTestId('budget-paused-a1')).toHaveCount(0)
  expect(state.resumed).toEqual(['a1'])
})
