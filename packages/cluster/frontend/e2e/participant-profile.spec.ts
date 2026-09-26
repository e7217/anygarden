import { expect, test, type WebSocketRoute } from '@playwright/test'

// Browser contract backed by the public room endpoint. Backend tests separately
// verify that normal members and room-bound guests receive this description.
for (const guest of [false, true]) {
  test(`${guest ? 'guest' : 'member'} sees agent roles on first load and roster changes`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width: guest ? 375 : 1440, height: 900 })
    await page.addInitScript(guest => {
      localStorage.setItem('anygarden_token', 'profile-test')
      localStorage.setItem('anygarden_locale', 'en')
      if (guest) {
        localStorage.setItem('anygarden_is_guest', '1')
        localStorage.setItem('anygarden_guest_room_id', 'profile-room')
        localStorage.setItem('anygarden_guest_display_name', 'Guest')
      }
    }, guest)
    let description: string | null = 'Reviews architecture and implementation'
    let unavailable = false
    const agent = () => ({ id: 'agent-pid', agent_id: 'agent-1', display_name: 'Reviewer', kind: 'agent', role: 'member', engine: 'codex-cli', online: true, description })
    const requests: string[] = []
    await page.route('**/api/v1/**', async route => {
      const path = new URL(route.request().url()).pathname
      requests.push(path)
      let body: unknown = []
      let status = 200
      if (path === '/api/v1/auth/me') body = { id: 'viewer', email: 'viewer@example.test', is_admin: false }
      if (path === '/api/v1/projects') body = [{ id: 'project', name: 'Product' }]
      if (path === '/api/v1/rooms') body = [{ id: 'profile-room', project_id: 'project', name: 'Review', is_dm: false }]
      if (path === '/api/v1/rooms/profile-room') {
        body = { id: 'profile-room', name: 'Review', participants: [agent(), { id: 'viewer-pid', kind: guest ? 'guest' : 'user', user_id: guest ? null : 'viewer', display_name: guest ? 'Guest' : 'Viewer' }] }
        if (unavailable) status = 503
      }
      if (path === '/api/v1/system/version') body = { version: 'test' }
      await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
    })
    let socket: WebSocketRoute | undefined
    await page.routeWebSocket('**/ws/rooms/**', ws => { socket = ws })
    await page.goto(guest ? '/g/profile-room' : '/rooms/profile-room')
    await expect.poll(() => Boolean(socket)).toBe(true)
    const toggle = page.getByTestId(guest ? 'guest-header-participants-toggle' : 'room-header-participants-toggle')
    await toggle.click()
    const profile = page.getByTestId('participant-description-agent-pid')
    await expect(profile).toHaveText(description!)
    await page.screenshot({ path: testInfo.outputPath('participant-profile.png') })
    description = 'Coordinates the release review'
    unavailable = true
    socket!.send(JSON.stringify({ type: 'room_settings_changed', room_id: 'profile-room', participants: [agent()] }))
    await expect(profile).toHaveText(description)
    if (guest) {
      await expect(page.getByRole('alert')).toBeVisible()
      await expect(page.locator('textarea')).toBeVisible()
    }
    unavailable = false
    description = null
    socket!.send(JSON.stringify({ type: 'room_settings_changed', room_id: 'profile-room', participants: [agent()] }))
    await expect(profile).toHaveCount(0)
    expect(requests.some(path => /^\/api\/v1\/agents(?:\/|$)/.test(path))).toBe(false)
  })
}
