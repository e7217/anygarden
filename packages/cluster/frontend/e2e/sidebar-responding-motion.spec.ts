import { expect, test, type Page } from '@playwright/test'

// MSG-09 (#921) — the responding indicators are a busy signal, not
// decoration: under reduced motion they keep a slow in-place fade
// instead of freezing.

const user = { id: 'e2e-admin', email: 'admin@example.com', is_admin: true }
const agent = {
  id: 'a1',
  name: 'agent01',
  engine: 'codex-cli',
  desired_state: 'running',
  actual_state: 'running',
  restart_policy: 'never',
  machine_online: true,
}
const dm = { id: 'dm-a1', name: 'DM: agent01', project_id: null, is_dm: true, representative_agent_id: 'a1' }

async function stubApi(page: Page) {
  await page.route('**/api/v1/**', async route => {
    const url = new URL(route.request().url())
    const { pathname } = url
    let status = 200
    let body: unknown = []

    if (pathname === '/api/v1/auth/dev-token') status = 404
    else if (pathname === '/api/v1/auth/login') body = { token: 'e2e-token', user }
    else if (pathname === '/api/v1/auth/me') body = user
    else if (pathname === '/api/v1/system/version') body = { version: '0.18.0' }
    else if (pathname === '/api/v1/agents') body = [agent]
    else if (pathname === '/api/v1/rooms' && url.searchParams.get('is_dm') === 'true') body = [dm]
    else if (pathname === '/api/v1/turns/active') body = [{ room_id: dm.id, agent_ids: [agent.id] }]
    else if (pathname !== '/api/v1/projects' && pathname !== '/api/v1/rooms') status = 404

    await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
  })
}

async function signIn(page: Page) {
  await stubApi(page)
  await page.goto('/login')
  await page.locator('#login-email').fill(user.email)
  await page.locator('#login-password').fill('correct-password')
  await page.getByRole('button', { name: 'Sign In' }).click()
  await expect(page).toHaveURL(/\/$/)
}

const animationOf = (page: Page, selector: string) =>
  page.locator(selector).evaluateAll(els =>
    els.map(el => {
      const style = getComputedStyle(el)
      return { name: style.animationName, delay: style.animationDelay }
    }),
  )

test('reduced motion keeps the responding indicators breathing in place', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await signIn(page)
  await expect(page.getByRole('status', { name: 'Responding', exact: true })).toBeVisible()

  const dots = await animationOf(page, '.responding-dot')
  expect(dots).toHaveLength(3)
  for (const dot of dots) expect(dot).toEqual({ name: 'responding-breathe', delay: '0s' })

  const rings = await animationOf(page, '.presence-working')
  expect(rings.length).toBeGreaterThan(0)
  for (const ring of rings) expect(ring.name).toBe('presence-working-breathe')
})

test('default motion keeps the stepping dots and the growing ring', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'no-preference' })
  await signIn(page)
  await expect(page.getByRole('status', { name: 'Responding', exact: true })).toBeVisible()

  const dots = await animationOf(page, '.responding-dot')
  expect(dots.map(dot => dot.name)).toEqual(['responding-dot', 'responding-dot', 'responding-dot'])
  expect(dots.map(dot => dot.delay)).toEqual(['0s', '0.16s', '0.32s'])

  const rings = await animationOf(page, '.presence-working')
  expect(rings.length).toBeGreaterThan(0)
  for (const ring of rings) expect(ring.name).toBe('presence-working')
})
