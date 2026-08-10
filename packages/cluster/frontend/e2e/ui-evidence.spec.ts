import { expect, test, type Page, type Route } from '@playwright/test'

const roomId = 'room-ui-evidence'
const projectId = 'project-ui-evidence'
const user = {
  id: 'user-ui-evidence',
  email: 'evidence@example.com',
  is_admin: true,
}

const participants = [
  {
    id: 'participant-user-evidence',
    user_id: user.id,
    display_name: 'Evidence User',
    kind: 'user',
    role: 'owner',
    online: true,
    last_seen_at: null,
  },
  {
    id: 'participant-agent-evidence',
    agent_id: 'agent-ui-evidence',
    display_name: 'Evidence Agent',
    kind: 'agent',
    role: 'member',
    online: true,
    last_seen_at: null,
    engine: 'codex-cli',
  },
]

type AttachmentMode = 'read' | 'write'

async function fulfillJson(route: Route, body: unknown, status = 200) {
  await route.fulfill({
    status,
    contentType: 'application/json',
    body: JSON.stringify(body),
  })
}

/**
 * The browser renders the production React routes and controls.  Only the
 * local API data plane is deterministic; this test never creates an image or
 * calls a provider, machine, or external workspace.
 */
async function stubRoomApi(
  page: Page,
  { attachmentMode }: { attachmentMode?: AttachmentMode } = {},
) {
  const room = {
    id: roomId,
    name: 'UI evidence room',
    project_id: projectId,
    is_dm: false,
    representative_agent_id: 'agent-ui-evidence',
    workspace_attachments: attachmentMode
      ? [
          {
            id: 'attachment-ui-evidence',
            workspace_id: 'workspace-ui-evidence',
            label: 'Approved project workspace',
            agent_id: 'agent-ui-evidence',
            mode: attachmentMode,
            epoch: 1,
            expires_at: '2026-08-08T00:00:00Z',
          },
        ]
      : [],
  }
  let tasks: Array<Record<string, unknown>> = []

  await page.route('**/api/v1/**', async route => {
    const request = route.request()
    const { pathname, searchParams } = new URL(request.url())

    if (pathname === '/api/v1/auth/me') return fulfillJson(route, user)
    if (pathname === '/api/v1/system/version') {
      return fulfillJson(route, { version: '0.18.0' })
    }
    if (pathname === '/api/v1/projects') {
      return fulfillJson(route, [{ id: projectId, name: 'Evidence project' }])
    }
    if (pathname === '/api/v1/rooms') {
      if (searchParams.get('is_dm') === 'true') return fulfillJson(route, [])
      return fulfillJson(route, [room])
    }
    if (pathname === `/api/v1/rooms/${roomId}`) {
      return fulfillJson(route, { ...room, participants, allow_human_assignment: true })
    }
    if (pathname === `/api/v1/rooms/${roomId}/tasks`) {
      if (request.method() === 'GET') return fulfillJson(route, tasks)
      if (request.method() === 'POST') {
        const body = request.postDataJSON() as {
          title: string
          assignee_participant_id: string | null
        }
        const task = {
          id: 'task-ui-evidence',
          room_id: roomId,
          title: body.title,
          status: 'todo',
          assignee_participant_id: body.assignee_participant_id,
          created_by: participants[0].id,
          created_at: '2026-08-07T00:00:00Z',
          source_message_id: null,
          source_thread_root_id: null,
        }
        tasks = [task]
        return fulfillJson(route, task)
      }
    }
    if (pathname === '/api/v1/tasks/task-ui-evidence/claim' && request.method() === 'POST') {
      tasks = tasks.map(task => ({
        ...task,
        status: 'in_progress',
        assignee_participant_id: participants[0].id,
      }))
      return fulfillJson(route, tasks[0])
    }
    if (pathname === `/api/v1/rooms/${roomId}/files`) return fulfillJson(route, [])
    if (pathname.startsWith('/api/v1/goals')) return fulfillJson(route, [])

    return fulfillJson(route, { detail: 'Unstubbed evidence route' }, 404)
  })
}

async function prepareRoom(page: Page, attachmentMode?: AttachmentMode) {
  await page.addInitScript(() => {
    localStorage.setItem('anygarden_token', 'ui-evidence-token')
    localStorage.setItem('anygarden_right_sidebar_collapsed', 'false')

    // Preserve Vite's own HMR socket, but keep the room transport local and
    // deterministic. The app's real WebSocket hook still transitions through
    // its normal open state; no network service receives a connection.
    const NativeWebSocket = window.WebSocket
    class LocalRoomSocket {
      static readonly CONNECTING = 0
      static readonly OPEN = 1
      static readonly CLOSING = 2
      static readonly CLOSED = 3
      readyState = LocalRoomSocket.CONNECTING
      onopen: ((event: Event) => void) | null = null
      onclose: ((event: CloseEvent) => void) | null = null
      onerror: ((event: Event) => void) | null = null
      onmessage: ((event: MessageEvent) => void) | null = null

      constructor() {
        queueMicrotask(() => {
          this.readyState = LocalRoomSocket.OPEN
          this.onopen?.(new Event('open'))
        })
      }

      send() {}

      close() {
        this.readyState = LocalRoomSocket.CLOSED
        this.onclose?.(new CloseEvent('close'))
      }
    }

    window.WebSocket = new Proxy(NativeWebSocket, {
      construct(target, args) {
        if (String(args[0]).includes('/ws/rooms/')) return new LocalRoomSocket()
        return Reflect.construct(target, args)
      },
    }) as typeof WebSocket
  })
  await stubRoomApi(page, { attachmentMode })
  await page.goto(`/rooms/${roomId}`)
  await expect(page.getByRole('heading', { name: 'UI evidence room' })).toBeVisible()
}

test.describe('actual browser UI evidence', () => {
  test('captures Phase 3 task creation and claim in the context rail', async ({ page }, testInfo) => {
    await prepareRoom(page)

    const rail = page.getByTestId('right-rail-root')
    await expect(rail).toBeVisible()
    await rail.getByPlaceholder('Add a task…').fill('Review workspace evidence')
    await rail.getByRole('button', { name: 'Create task' }).click()
    await expect(rail.getByTestId('right-rail-task-row-task-ui-evidence')).toBeVisible()

    await rail
      .getByRole('button', { name: 'Cycle status (current: Todo)' })
      .click()
    await expect(rail.getByText('In Progress', { exact: true })).toBeVisible()
    await expect(rail.getByText('Review workspace evidence', { exact: true })).toBeVisible()

    await page.screenshot({
      path: testInfo.outputPath('phase3-task-create-claim.png'),
      fullPage: true,
      animations: 'disabled',
    })
  })

  test('captures Phase 5 active workspace-attachment warning', async ({ page }, testInfo) => {
    await prepareRoom(page, 'write')

    const banner = page.getByRole('status', {
      name: 'External workspace attachment active',
    })
    await expect(banner).toContainText('External workspace attached')
    await expect(banner).toContainText('Approved project workspace')
    await expect(banner).toContainText('scoped write')

    await page.screenshot({
      path: testInfo.outputPath('phase5-workspace-attachment-warning.png'),
      fullPage: true,
      animations: 'disabled',
    })
  })
})
