// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest'
import { render, screen, fireEvent, cleanup, waitFor } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import RoomEditDialog from './RoomEditDialog'

// ``apiFetch`` thin-wraps ``fetch`` (see lib/api.ts) — stubbing the
// global ``fetch`` mirrors what the dialog actually calls in the
// browser and keeps the mock surface as small as possible.
type FetchMock = ReturnType<typeof vi.fn>

interface InstallOptions {
  // Admin or regular user for the /auth/me response. #225 gates the
  // context-window toggle behind ``is_admin`` so every test must
  // declare which tier it simulates.
  isAdmin: boolean
  // Extra payload merged into the GET /api/v1/rooms/:id response so
  // individual tests can vary ``context_window_enabled`` etc.
  roomPayload?: Record<string, unknown>
}

function installFetch(
  options: InstallOptions,
  overrideResponder?: (url: string, init?: RequestInit) => Response | undefined,
): FetchMock {
  const { isAdmin, roomPayload } = options
  const mock = vi.fn().mockImplementation((url: string, init?: RequestInit) => {
    // Let per-test overrides win when they return a response; fall
    // through to the default routes otherwise.
    const override = overrideResponder?.(url, init)
    if (override) return Promise.resolve(override)

    if (typeof url === 'string' && url.includes('/api/v1/auth/me')) {
      return Promise.resolve(
        jsonResponse({ id: 'u1', email: 'u@x', is_admin: isAdmin }),
      )
    }
    if (init?.method === 'PATCH') return Promise.resolve(jsonResponse({}))
    if (
      typeof url === 'string' &&
      url.includes('/api/v1/rooms/') &&
      url.includes('/token-stats')
    ) {
      return Promise.resolve(jsonResponse({ window_1h: { per_agent: [] }, window_24h: { per_agent: [] } }))
    }
    // GET /api/v1/rooms/:id — return the current state.
    return Promise.resolve(
      jsonResponse({
        id: 'r1',
        name: 'general',
        description: 'ops',
        context_window_enabled: true,
        speaker_strategy: 'mentioned_only',
        orchestrator_agent_id: null,
        participants: [],
        ...roomPayload,
      }),
    )
  })
  globalThis.fetch = mock as unknown as typeof fetch
  return mock
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

beforeEach(() => {
  localStorage.clear()
  // Seed a token so ``useAuth`` skips the dev-token branch and goes
  // straight to /auth/me — the stub above decides admin vs member.
  localStorage.setItem('anygarden_token', 'test-token')
})

describe('RoomEditDialog – admin context-window toggle (#225)', () => {
  it('reflects the persisted context_window_enabled flag on open for an admin', async () => {
    installFetch({
      isAdmin: true,
      roomPayload: { context_window_enabled: true },
    })

    render(
      <RoomEditDialog
        roomId="r1"
        open={true}
        onOpenChange={() => {}}
      />,
    )

    const toggle = (await screen.findByTestId(
      'room-edit-context-window-toggle',
    )) as HTMLInputElement
    await waitFor(() => {
      expect(toggle).toBeChecked()
    })
  })

  it('sends context_window_enabled in the admin PATCH body when toggled off', async () => {
    const fetchMock = installFetch({
      isAdmin: true,
      roomPayload: { context_window_enabled: true },
    })

    const onSaved = vi.fn()
    const onOpenChange = vi.fn()

    render(
      <RoomEditDialog
        roomId="r1"
        open={true}
        onOpenChange={onOpenChange}
        onSaved={onSaved}
      />,
    )

    const toggle = (await screen.findByTestId(
      'room-edit-context-window-toggle',
    )) as HTMLInputElement

    await waitFor(() => {
      // Wait for initial load to settle so the default-true state is
      // seeded before we flip the box off.
      expect(toggle).toBeChecked()
    })

    fireEvent.click(toggle)
    fireEvent.click(screen.getByRole('button', { name: /save/i }))

    await waitFor(() => {
      const patchCall = fetchMock.mock.calls.find(
        ([, init]) => (init as RequestInit | undefined)?.method === 'PATCH',
      )
      expect(patchCall).toBeDefined()
      const body = JSON.parse((patchCall![1] as RequestInit).body as string)
      expect(body.context_window_enabled).toBe(false)
      // Admin payload also carries the dispatch-mode fields.
      expect(body.speaker_strategy).toBe('mentioned_only')
    })
  })

  it('hides the context-window toggle from non-admin members and omits it from the PATCH body', async () => {
    const fetchMock = installFetch({
      isAdmin: false,
      roomPayload: { context_window_enabled: true },
    })

    render(
      <RoomEditDialog
        roomId="r1"
        open={true}
        onOpenChange={() => {}}
      />,
    )

    // Wait for the initial GET to settle. The input exists before
    // data loads, but Save stays disabled until ``name`` is populated.
    const nameInput = await screen.findByLabelText(/name/i)
    await waitFor(() => {
      expect(nameInput).toHaveValue('general')
    })

    // Toggle must not be rendered for non-admins.
    expect(
      screen.queryByTestId('room-edit-context-window-toggle'),
    ).not.toBeInTheDocument()

    // Rename + save should still work (rename-only PATCH stays open).
    fireEvent.click(screen.getByRole('button', { name: /save/i }))

    await waitFor(() => {
      const patchCall = fetchMock.mock.calls.find(
        ([, init]) => (init as RequestInit | undefined)?.method === 'PATCH',
      )
      expect(patchCall).toBeDefined()
      const body = JSON.parse((patchCall![1] as RequestInit).body as string)
      // Non-admin payload never includes the admin-only fields.
      expect(body).not.toHaveProperty('context_window_enabled')
      expect(body).not.toHaveProperty('speaker_strategy')
      expect(body).not.toHaveProperty('orchestrator_agent_id')
    })
  })
})
